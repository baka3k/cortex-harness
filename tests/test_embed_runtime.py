"""Tests for tools/common/embed_runtime.py (shared embedder + query-vector cache)."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
for entry in (str(CODE_TINY), str(CODE_TINY / "mcp")):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

from tools.common import embed_runtime  # noqa: E402


class _StubModel:
    """Encoder stub that counts calls and records which texts it saw."""

    def __init__(self) -> None:
        self.encode_calls: list[list[str]] = []

    def encode(self, texts, device=None):  # noqa: ANN001
        self.encode_calls.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]


class _FlakyEncodeModel:
    """Raises a configurable accelerator error once, then succeeds."""

    def __init__(self, error_message: str) -> None:
        self.error_message = error_message
        self.encode_calls: list[list[str]] = []

    def encode(self, texts, device=None):  # noqa: ANN001
        self.encode_calls.append(list(texts))
        if len(self.encode_calls) == 1:
            raise RuntimeError(self.error_message)
        return [[1.0, 0.0] for _ in texts]


class _LoadCounters:
    """Counts tokenizer+model loads; models refuse accelerator devices once."""

    def __init__(self) -> None:
        self.loads = 0
        self.encode_calls = 0
        self.fail_message: str | None = None

    def tokenizer(self, model_name, trust_remote_code=False):  # noqa: ANN001
        return object()

    def model(self, model_name, trust_remote_code=False):  # noqa: ANN001
        self.loads += 1
        counters = self

        class _Model:
            def to(self, device):  # noqa: ANN001
                if str(device) != "cpu" and counters.fail_message:
                    raise RuntimeError(counters.fail_message)

            def eval(self):  # noqa: ANN001
                return self

            def encode(self, texts, device=None):  # noqa: ANN001
                counters.encode_calls += len(texts)
                return [[1.0, 2.0] for _ in texts]

        return _Model()

    def install(self):
        tokenizer_cls = type("FakeAutoTokenizer", (), {})
        tokenizer_cls.from_pretrained = staticmethod(self.tokenizer)
        model_cls = type("FakeAutoModel", (), {})
        model_cls.from_pretrained = staticmethod(self.model)
        return [
            patch("transformers.AutoTokenizer", tokenizer_cls),
            patch("transformers.AutoModel", model_cls),
        ]


class EmbedQueryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        embed_runtime.reset_caches()

    def tearDown(self) -> None:
        embed_runtime.reset_caches()

    def test_repeat_query_hits_cache_without_model_call(self):
        model = _StubModel()
        with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
            first = embed_runtime.embed_query("same query", "model-a")
            second = embed_runtime.embed_query("same query", "model-a")
        self.assertEqual(len(model.encode_calls), 1)
        self.assertEqual(first, second)

    def test_cache_hit_returns_copy(self):
        model = _StubModel()
        with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
            first = embed_runtime.embed_query("mutate me", "model-a")
            first.append(999.0)
            second = embed_runtime.embed_query("mutate me", "model-a")
        self.assertNotIn(999.0, second)
        self.assertEqual(len(second), 2)

    def test_distinct_queries_miss_cache(self):
        model = _StubModel()
        with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
            embed_runtime.embed_query("query one", "model-a")
            embed_runtime.embed_query("query two", "model-a")
        self.assertEqual(len(model.encode_calls), 2)

    def test_lru_eviction_bound(self):
        model = _StubModel()
        with patch.dict(os.environ, {"MCP_QUERY_EMBED_CACHE": "2"}):
            with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
                for text in ("q1", "q2", "q3"):
                    embed_runtime.embed_query(text, "model-a")
                self.assertEqual(len(model.encode_calls), 3)
                embed_runtime.embed_query("q1", "model-a")  # evicted → miss
        self.assertEqual(len(model.encode_calls), 4)
        self.assertEqual(len(embed_runtime._QUERY_VECTOR_CACHE), 2)

    def test_cache_disabled_via_env(self):
        model = _StubModel()
        with patch.dict(os.environ, {"MCP_QUERY_EMBED_CACHE": "0"}):
            with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
                embed_runtime.embed_query("no cache", "model-a")
                embed_runtime.embed_query("no cache", "model-a")
        self.assertEqual(len(model.encode_calls), 2)
        self.assertEqual(len(embed_runtime._QUERY_VECTOR_CACHE), 0)


class AcceleratorFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        embed_runtime.reset_caches()

    def tearDown(self) -> None:
        embed_runtime.reset_caches()

    def _retry_scenario(self, error_message: str) -> list[float]:
        model = _FlakyEncodeModel(error_message)
        with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
            return embed_runtime.embed_query("retry query", "model-a")

    def test_cuda_style_error_retries_on_cpu(self):
        vector = self._retry_scenario(
            "CUDA error: no kernel image is available for execution on the device"
        )
        self.assertEqual(vector, [1.0, 0.0])

    def test_metal_style_error_retries_on_cpu(self):
        vector = self._retry_scenario("MPS backend: The operation failed on Metal")
        self.assertEqual(vector, [1.0, 0.0])

    def test_non_accelerator_error_propagates(self):
        model = _FlakyEncodeModel("value error shape mismatch")
        with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
            with self.assertRaises(RuntimeError):
                embed_runtime.embed_query("bad", "model-a")

    def test_fallback_disabled_via_env(self):
        model = _FlakyEncodeModel("CUDA error: invalid device function")
        with patch.dict(os.environ, {"EMBED_FALLBACK_TO_CPU": "0"}):
            with patch.object(embed_runtime, "get_embedder", return_value=(None, model, "cpu")):
                with self.assertRaises(RuntimeError):
                    embed_runtime.embed_query("strict", "model-a")

    def test_model_load_failure_on_mps_falls_back_to_cpu(self):
        counters = _LoadCounters()
        counters.fail_message = "MPS backend: operation failed on Metal device"
        patches = counters.install()
        with patch("torch.backends.mps.is_available", return_value=True), patches[0], patches[1]:
            tokenizer, model, device = embed_runtime.get_embedder("model-load", "mps")
        self.assertEqual(str(device), "cpu")
        self.assertEqual(counters.loads, 1)

    def test_model_load_failure_on_cuda_falls_back_to_cpu(self):
        counters = _LoadCounters()
        counters.fail_message = "CUDA error: no kernel image is available"
        patches = counters.install()
        with patch("torch.cuda.is_available", return_value=True), patches[0], patches[1]:
            tokenizer, model, device = embed_runtime.get_embedder("model-load-cuda", "cuda")
        self.assertEqual(str(device), "cpu")


class SharedRuntimeDedupeTests(unittest.TestCase):
    """Scope decision #6: importlib-aliased backends must share one runtime."""

    @classmethod
    def setUpClass(cls) -> None:
        def _load_alias(module_name: str, relative: str):
            spec = importlib.util.spec_from_file_location(module_name, CODE_TINY / relative)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            return module

        cls.counters = _LoadCounters()
        cls._loader_patches = cls.counters.install()
        for item in cls._loader_patches:
            item.start()
        import fastmcp_server  # noqa: F401

        cls.fast = fastmcp_server
        # Load a second copy of cplus_mcp under an alias, the way
        # unified_mcp._load_module does.
        cls.alias = _load_alias("cplus_backend_alias_embed_test", "mcp/cplus/cplus_mcp.py")

    @classmethod
    def tearDownClass(cls) -> None:
        for item in cls._loader_patches:
            item.stop()

    def setUp(self) -> None:
        embed_runtime.reset_caches()

    def tearDown(self) -> None:
        embed_runtime.reset_caches()

    def test_model_loads_once_across_real_and_alias_modules(self):
        loads_before = self.counters.loads
        encodes_before = self.counters.encode_calls
        first = self.alias._embed_query("shared text", "dedupe-model")
        second = self.fast._embed_query("shared text", "dedupe-model")
        self.assertEqual(first, second)
        # Same text: second call was a vector-cache hit (no new encode).
        self.assertEqual(self.counters.encode_calls, encodes_before + 1)
        # Different text, same model: still exactly one new model load.
        self.alias._embed_query("other text", "dedupe-model")
        self.assertEqual(self.counters.encode_calls, encodes_before + 2)
        self.assertEqual(self.counters.loads, loads_before + 1)

    def test_explore_service_fallback_finds_delegate(self):
        # explore_service falls back to `from cplus.cplus_mcp import _embed_query`;
        # the delegate must resolve lazily into the shared runtime.
        embedder = self.alias._embed_query
        vector = embedder("fallback text", "dedupe-model")
        self.assertEqual(vector, [1.0, 2.0])


class ModuleBoundaryGuardTests(unittest.TestCase):
    def test_existing_embedding_runtime_module_untouched(self):
        from tools.common.embedding_runtime import resolve_embedding_cache  # noqa: F401

        self.assertTrue(callable(resolve_embedding_cache))

    def test_backend_delegate_names_remain_patchable(self):
        import cplus.cplus_mcp as cplus_mcp

        # Tests patch module-level names (patch.object(module, "_embed_query"));
        # the delegates keep those names on the module itself.
        self.assertEqual(cplus_mcp._embed_query.__module__, "cplus.cplus_mcp")
        self.assertEqual(cplus_mcp._get_embedder.__module__, "cplus.cplus_mcp")
        self.assertEqual(cplus_mcp._resolve_embed_device.__module__, "cplus.cplus_mcp")


class AutoDetectDeviceTests(unittest.TestCase):
    """Phase 03: device auto-detect matrix (no real accelerators needed)."""

    def tearDown(self) -> None:
        os.environ.pop("EMBED_DEVICE", None)

    def _resolve(self, platform: str, mps: bool, cuda: bool, arg=None):
        def fake_mps_available():
            return mps

        def fake_cuda_available():
            return cuda

        with patch.object(embed_runtime.sys, "platform", platform), patch(
            "torch.backends.mps.is_available", fake_mps_available
        ), patch("torch.cuda.is_available", fake_cuda_available):
            return embed_runtime.resolve_device(arg)

    def test_matrix(self):
        cases = [
            # (platform, mps, cuda, argument, expected)
            ("darwin", True, False, None, "mps"),
            ("darwin", True, False, "auto", "mps"),
            ("darwin", False, False, None, "cpu"),
            ("darwin", False, True, None, "cpu"),  # darwin rule: mps else cpu
            ("win32", True, True, "auto", "cuda"),
            ("win32", False, True, None, "cuda"),
            ("win32", False, False, "auto", "cpu"),
            ("linux", False, False, None, "cpu"),
        ]
        for platform, mps, cuda, arg, expected in cases:
            with self.subTest(platform=platform, mps=mps, cuda=cuda, arg=arg):
                device = self._resolve(platform, mps, cuda, arg)
                self.assertEqual(str(device), expected)

    def test_explicit_cpu_never_upgraded(self):
        device = self._resolve("darwin", True, True, "cpu")
        self.assertEqual(str(device), "cpu")

    def test_explicit_mps_on_non_darwin_falls_back(self):
        device = self._resolve("win32", False, False, "mps")
        self.assertEqual(str(device), "cpu")

    def test_env_value_wins_over_auto_when_set(self):
        os.environ["EMBED_DEVICE"] = "cpu"
        device = self._resolve("darwin", True, True, None)
        self.assertEqual(str(device), "cpu")

    def test_torch_missing_returns_none_and_get_embedder_raises(self):
        with patch.object(embed_runtime, "torch", None):
            self.assertIsNone(embed_runtime.resolve_device("cpu"))
            with self.assertRaises(RuntimeError):
                embed_runtime.get_embedder("some-model", "cpu")


class PreloadGuardTests(unittest.TestCase):
    """Phase 03: a failing startup preload must not kill server boot."""

    def test_preload_failure_is_swallowed(self):
        import io
        import contextlib

        import fastmcp_server

        buffer = io.StringIO()
        with patch.object(embed_runtime, "get_embedder", side_effect=RuntimeError("MPS boom")), patch.object(
            fastmcp_server, "PRELOAD_EMBEDDER_ON_STARTUP", "1"
        ), patch.object(fastmcp_server, "DEFAULT_MODEL", "stub-model"), contextlib.redirect_stdout(buffer):
            fastmcp_server._preload_embedder_on_startup()  # must not raise
        output = buffer.getvalue()
        self.assertIn("startup preload failed", output)
        self.assertIn("MPS boom", output)

    def test_preload_disabled_short_circuits(self):
        import io
        import contextlib

        import fastmcp_server

        buffer = io.StringIO()
        with patch.object(fastmcp_server, "PRELOAD_EMBEDDER_ON_STARTUP", "0"), contextlib.redirect_stdout(buffer):
            fastmcp_server._preload_embedder_on_startup()
        self.assertIn("preload disabled", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

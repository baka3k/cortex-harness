"""Phase 01 — model policy + Qwen3 query-side encode parity (code + doc paths).

Covers the unified-embedding plan's query-side contract: every query path
(embed_query, explore_service fan-out, doc MCP query) encodes with the
Qwen3 ``query`` prompt + normalized vectors, jina/unknown keep legacy
behavior, and the CPU fallback for ST-policy models stays on the
SentenceTransformer backend (never AutoModel mean-pooling).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
DOC_TINY = ROOT / "doc-tiny"
for entry in (CODE_TINY, CODE_TINY / "mcp", DOC_TINY):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

from tools.common import embed_runtime  # noqa: E402

QWEN3 = "Qwen/Qwen3-Embedding-0.6B"
JINA = "jinaai/jina-embeddings-v3"


class _Array(list):
    """List that mimics a numpy row (``.tolist()``), like real ST output."""

    def tolist(self) -> list:
        return list(self)


class _RecordingST:
    """SentenceTransformer stub that records every encode call."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self._vectors = vectors or [[0.5, 0.25]]

    def encode(self, texts, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append((list(texts), dict(kwargs)))
        return [_Array(self._vectors[0]) for _ in texts]


class _FlakyAcceleratorST:
    """Raises an accelerator error on its first encode, then succeeds."""

    def __init__(self, error_message: str) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self._error = error_message

    def encode(self, texts, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append((list(texts), dict(kwargs)))
        if len(self.calls) == 1:
            raise RuntimeError(self._error)
        return [_Array([1.0, 0.0]) for _ in texts]


class ModelPolicyTableTests(unittest.TestCase):
    """AC3 — the policy table for all four input classes."""

    def test_policy_table(self):
        cases = [
            (QWEN3, {"trust_remote_code": False, "backend": "st",
                     "query_prompt": "query", "normalize": True}),
            (JINA, {"trust_remote_code": True, "backend": "automodel",
                    "query_prompt": None, "normalize": False}),
            # local snapshot whose basename names the family → qwen3 policy
            ("/models/qwen3-embedding-0.6b-snapshot",
             {"trust_remote_code": False, "backend": "st",
              "query_prompt": "query", "normalize": True}),
            # unrecognized name → ST default config, no prompt, no change
            ("some-unknown-model",
             {"trust_remote_code": False, "backend": "st",
              "query_prompt": None, "normalize": False}),
        ]
        for model_name, expected in cases:
            with self.subTest(model=model_name):
                self.assertEqual(embed_runtime.model_policy(model_name), expected)

    def test_jina_model_path_env_match_is_legacy_policy(self):
        local_jina = "/models/jina-v3-local"
        with patch.dict(os.environ, {"JINA_MODEL_PATH": local_jina}):
            policy = embed_runtime.model_policy(local_jina)
        self.assertEqual(policy["backend"], "automodel")
        self.assertTrue(policy["trust_remote_code"])

    def test_should_trust_remote_code_delegates_to_policy(self):
        self.assertFalse(embed_runtime._should_trust_remote_code(QWEN3))
        self.assertTrue(embed_runtime._should_trust_remote_code(JINA))


class EmbedQueryParityTests(unittest.TestCase):
    """AC1 + AC5 — embed_query for Qwen3 == ST encode(prompt_name, normalize)."""

    def setUp(self) -> None:
        embed_runtime.reset_caches()

    def tearDown(self) -> None:
        embed_runtime.reset_caches()

    def test_embed_query_qwen3_matches_st_query_encode(self):
        stub = _RecordingST(vectors=[[0.5, 0.25]])
        loader = patch.object(
            embed_runtime, "get_sentence_transformer", return_value=stub
        )
        with loader as load_mock, patch.object(
            embed_runtime, "_st_local_files_only", return_value=True
        ):
            vector = embed_runtime.embed_query("hello world", QWEN3)
        self.assertEqual(vector, [0.5, 0.25])
        # Loader got the policy's trust_remote_code and the local-files probe.
        self.assertFalse(load_mock.call_args.kwargs["trust_remote_code"])
        self.assertTrue(load_mock.call_args.kwargs["local_files_only"])
        # Query encode carries the ST config prompt key + normalization.
        self.assertEqual(stub.calls, [(["hello world"], {
            "prompt_name": "query",
            "normalize_embeddings": True,
        })])

    def test_embed_query_local_files_only_false_when_no_snapshot(self):
        stub = _RecordingST()
        with patch.object(
            embed_runtime, "get_sentence_transformer", return_value=stub
        ) as load_mock, patch.object(
            embed_runtime, "_st_local_files_only", return_value=False
        ):
            embed_runtime.embed_query("q", QWEN3)
        self.assertFalse(load_mock.call_args.kwargs["local_files_only"])

    def test_embed_query_jina_keeps_legacy_automodel_path(self):
        stub = _RecordingST(vectors=[[1.0, 2.0]])
        with patch.object(
            embed_runtime, "get_embedder", return_value=(None, stub, "cpu")
        ) as embedder_mock, patch.object(
            embed_runtime, "get_sentence_transformer"
        ) as st_mock:
            vector = embed_runtime.embed_query("legacy", JINA)
        self.assertEqual(vector, [1.0, 2.0])
        embedder_mock.assert_called_once()
        st_mock.assert_not_called()
        # No prompt/normalize kwargs for the legacy family (device is the
        # pre-existing legacy encode argument).
        self.assertEqual(stub.calls, [(["legacy"], {"device": "cpu"})])

    def test_embed_query_unknown_model_keeps_legacy_path(self):
        stub = _RecordingST(vectors=[[3.0, 4.0]])
        with patch.object(
            embed_runtime, "get_embedder", return_value=(None, stub, "cpu")
        ), patch.object(embed_runtime, "get_sentence_transformer") as st_mock:
            vector = embed_runtime.embed_query("legacy", "unknown-model-x")
        self.assertEqual(vector, [3.0, 4.0])
        st_mock.assert_not_called()
        self.assertEqual(stub.calls, [(["legacy"], {"device": "cpu"})])


class StCpuFallbackTests(unittest.TestCase):
    """AC4 — accelerator failure reloads ST on CPU; never mean-pools."""

    def setUp(self) -> None:
        embed_runtime.reset_caches()

    def tearDown(self) -> None:
        embed_runtime.reset_caches()

    def test_qwen3_fallback_reloads_st_on_cpu_with_same_policy(self):
        flaky = _FlakyAcceleratorST("MPS backend out of memory")
        good = _RecordingST(vectors=[[1.0, 0.0]])
        loaded_devices: list[str | None] = []

        def _loader(model_name, device=None, trust_remote_code=None, local_files_only=None):
            loaded_devices.append(device)
            return flaky if device != "cpu" else good

        stale = object()
        embed_runtime._SENTENCE_TRANSFORMER_CACHE[(QWEN3, "mps")] = stale
        try:
            with patch.object(
                embed_runtime, "get_sentence_transformer", side_effect=_loader
            ), patch.object(
                embed_runtime, "get_embedder"
            ) as automodel_mock, patch.object(
                embed_runtime, "_st_local_files_only", return_value=False
            ):
                vector = embed_runtime.embed_query("retry", QWEN3)
        finally:
            embed_runtime._SENTENCE_TRANSFORMER_CACHE.pop((QWEN3, "mps"), None)
        self.assertEqual(vector, [1.0, 0.0])
        # Two ST loads: accelerator device first, then CPU with the policy.
        self.assertEqual(loaded_devices[1], "cpu")
        self.assertEqual(len(good.calls), 1)
        self.assertEqual(good.calls[0][1]["prompt_name"], "query")
        self.assertEqual(good.calls[0][1]["normalize_embeddings"], True)
        # The AutoModel mean-pool path is never entered for ST-policy models.
        automodel_mock.assert_not_called()
        # The stale ST cache entry for this model was evicted.
        self.assertNotIn((QWEN3, "mps"), embed_runtime._SENTENCE_TRANSFORMER_CACHE)

    def test_qwen3_non_accelerator_error_propagates(self):
        stub = _FlakyAcceleratorST("shape mismatch on tensors")
        stub.calls.clear()
        real_encode = stub.encode

        def _always_raise(texts, **kwargs):
            raise RuntimeError("shape mismatch on tensors")

        stub.encode = _always_raise  # type: ignore[method-assign]
        with patch.object(
            embed_runtime, "get_sentence_transformer", return_value=stub
        ), patch.object(embed_runtime, "_st_local_files_only", return_value=False):
            with self.assertRaises(RuntimeError):
                embed_runtime.embed_query("bad", QWEN3)


class ExploreServiceQueryPolicyTests(unittest.TestCase):
    """AC2 — explore_service fan-out embedder follows the model policy."""

    def test_make_embedder_encodes_with_policy_kwargs(self):
        from services import explore_service as explore_module

        cases = [
            (QWEN3, {"prompt_name": "query", "normalize_embeddings": True}),
            (JINA, {}),
            ("unknown-model-x", {}),
        ]
        for model_name, expected_kwargs in cases:
            with self.subTest(model=model_name):
                stub = _RecordingST(vectors=[[0.1, 0.2]])
                with patch.object(
                    embed_runtime, "get_sentence_transformer", return_value=stub
                ), patch.object(embed_runtime, "resolve_device", return_value=None):
                    embedder = explore_module._make_embedder(model_name)
                    self.assertIsNotNone(embedder)
                    vector = embedder("orders")
                self.assertEqual(vector, [0.1, 0.2])
                self.assertEqual(stub.calls, [(["orders"], expected_kwargs)])


class DocQueryPolicyTests(unittest.TestCase):
    """AC2 — doc MCP query path follows the model policy (flat-tree helper)."""

    def test_st_query_encode_kwargs_matrix(self):
        from embedding_utils import st_query_encode_kwargs

        self.assertEqual(
            st_query_encode_kwargs(QWEN3),
            {"prompt_name": "query", "normalize_embeddings": True},
        )
        self.assertEqual(st_query_encode_kwargs(JINA), {})
        self.assertEqual(st_query_encode_kwargs(None), {})

    def test_mcp_graph_rag_query_encode_uses_policy(self):
        import mcp_graph_rag

        stub = _RecordingST(vectors=[[0.3, 0.4]])
        original_name = mcp_graph_rag._embedder_model_name
        try:
            with patch.object(mcp_graph_rag, "get_embedder", return_value=stub):
                for model_name, expected_kwargs in (
                    (QWEN3, {"prompt_name": "query", "normalize_embeddings": True}),
                    ("BAAI/bge-m3", {}),
                ):
                    with self.subTest(model=model_name):
                        mcp_graph_rag._embedder_model_name = model_name
                        vector = mcp_graph_rag._query_encode("find the schema")
                        self.assertEqual(vector, [0.3, 0.4])
                        self.assertEqual(stub.calls[-1], (["find the schema"], expected_kwargs))
        finally:
            mcp_graph_rag._embedder_model_name = original_name


if __name__ == "__main__":
    unittest.main()

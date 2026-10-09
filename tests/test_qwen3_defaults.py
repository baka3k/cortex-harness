"""Phase 04 — default sweep: every entry point resolves Qwen3 by default.

Covers the env chain (arg → local path → EMBEDDING_MODEL →
DOC_EMBEDDING_MODEL → default), doc device auto-detect, the dependency
floor fail-fast, and the constants-twin rule between trees.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
DOC_TINY = ROOT / "doc-tiny"
for entry in (CODE_TINY, DOC_TINY):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

QWEN3 = "Qwen/Qwen3-Embedding-0.6B"
JINA = "jinaai/jina-embeddings-v3"

# Entry points that must default to the unified model (one representative
# per pipeline: launcher, MCP chain, legacy analyzer, delegating analyzer,
# cobol, livingdoc, doc MCP, doc ingest, doc query CLI).
DEFAULT_MODULES = [
    ("cortex_harness.dev", None),  # checked via source constant below
    ("tools.common.model_defaults", "DEFAULT_CODE_EMBEDDING_MODEL"),
    ("embedding_utils", "DEFAULT_DOC_EMBEDDING_MODEL"),
]


class DefaultsConvergeTests(unittest.TestCase):
    """AC1 — constants converge; representative entry points use them."""

    def test_constants_twin_same_value(self):
        from tools.common.model_defaults import DEFAULT_CODE_EMBEDDING_MODEL
        import embedding_utils as doc_utils

        self.assertEqual(DEFAULT_CODE_EMBEDDING_MODEL, doc_utils.DEFAULT_DOC_EMBEDDING_MODEL)
        self.assertEqual(DEFAULT_CODE_EMBEDDING_MODEL, QWEN3)

    def test_source_defaults_reference_the_constant(self):
        # dev.py cannot be imported side-effect-free here; assert by source.
        dev_source = (ROOT / "cortex_harness" / "dev.py").read_text()
        self.assertIn("DEFAULT_CODE_EMBEDDING_MODEL", dev_source)
        self.assertNotIn('"jinaai/jina-embeddings-v3"', dev_source)
        self.assertNotIn('"BAAI/bge-m3"', dev_source)
        # The MCP chains resolve the constant as the final fallback.
        for mcp in (
            "code-tiny/mcp/fastmcp_server.py",
            "code-tiny/mcp/cplus/cplus_mcp.py",
            "code-tiny/mcp/android/android_mcp.py",
            "code-tiny/mcp/java/java_mcp.py",
        ):
            source = (ROOT / mcp).read_text()
            self.assertIn("or DEFAULT_CODE_EMBEDDING_MODEL", source, mcp)

    def test_doc_resolve_defaults_to_qwen3(self):
        import embedding_utils as doc_utils

        with patch.dict(os.environ, {}, clear=False):
            for var in ("EMBEDDING_MODEL", "EMBEDDING_MODEL_PATH", "DOC_EMBEDDING_MODEL"):
                os.environ.pop(var, None)
            model, local_only = doc_utils.resolve_embedding_model(None, doc_utils.DEFAULT_DOC_EMBEDDING_MODEL)
        self.assertEqual(model, QWEN3)
        self.assertFalse(local_only)


class DocEnvChainTests(unittest.TestCase):
    """AC2 — DOC_EMBEDDING_MODEL is consumed after EMBEDDING_MODEL."""

    def test_doc_embedding_model_env_wins_over_default(self):
        import embedding_utils as doc_utils

        env = {"DOC_EMBEDDING_MODEL": "org/doc-model"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("EMBEDDING_MODEL", None)
            os.environ.pop("EMBEDDING_MODEL_PATH", None)
            model, _ = doc_utils.resolve_embedding_model(None, QWEN3)
        self.assertEqual(model, "org/doc-model")

    def test_embedding_model_still_beats_doc_env(self):
        import embedding_utils as doc_utils

        env = {"EMBEDDING_MODEL": "org/master-model", "DOC_EMBEDDING_MODEL": "org/doc-model"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("EMBEDDING_MODEL_PATH", None)
            model, _ = doc_utils.resolve_embedding_model(None, QWEN3)
        self.assertEqual(model, "org/master-model")


class DocDeviceTests(unittest.TestCase):
    """AC3 — EMBEDDING_DEVICE respected; auto-detect otherwise."""

    def test_explicit_env_cpu_respected(self):
        import embedding_utils as doc_utils

        with patch.dict(os.environ, {"EMBEDDING_DEVICE": "cpu"}):
            self.assertEqual(doc_utils.resolve_embedding_device(None), "cpu")

    def test_arg_beats_env(self):
        import embedding_utils as doc_utils

        with patch.dict(os.environ, {"EMBEDDING_DEVICE": "cpu"}):
            self.assertEqual(doc_utils.resolve_embedding_device("cuda"), "cuda")

    def test_auto_detect_matrix(self):
        import embedding_utils as doc_utils

        fake_torch = importlib.util.module_from_spec(
            importlib.util.spec_from_loader("_fake_torch_dev", None)
        )
        fake_torch.backends = SimpleNamespace(
            mps=SimpleNamespace(is_available=lambda: True)
        )
        fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
        fake_torch.__spec__ = None
        fake_torch.__path__ = []  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"torch": fake_torch}), patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("EMBEDDING_DEVICE", None)
            with patch.object(doc_utils.sys, "platform", "darwin"):
                self.assertEqual(doc_utils.resolve_embedding_device(None), "mps")
            fake_torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False))
            fake_torch.cuda = SimpleNamespace(is_available=lambda: True)
            with patch.object(doc_utils.sys, "platform", "linux"):
                self.assertEqual(doc_utils.resolve_embedding_device(None), "cuda")
            fake_torch.cuda = SimpleNamespace(is_available=lambda: False)
            with patch.object(doc_utils.sys, "platform", "linux"):
                self.assertEqual(doc_utils.resolve_embedding_device(None), "cpu")

    def test_dev_py_doc_env_uses_embedding_device_key(self):
        source = (ROOT / "cortex_harness" / "dev.py").read_text()
        self.assertIn('result.setdefault("EMBEDDING_DEVICE"', source)


class SentenceTransformerFloorGateTests(unittest.TestCase):
    """Review fix — the floor check fires on EVERY load path (ingest included)."""

    def test_get_sentence_transformer_rejects_old_transformers_for_qwen3(self):
        from tools.common import embed_runtime

        old_transformers = importlib.util.module_from_spec(
            importlib.util.spec_from_loader("_old_transformers_st", None)
        )
        old_transformers.__version__ = "4.41.0"
        old_transformers.__spec__ = None
        embed_runtime.reset_caches()
        try:
            with patch.dict(sys.modules, {"transformers": old_transformers}):
                with self.assertRaises(RuntimeError) as ctx:
                    embed_runtime.get_sentence_transformer(QWEN3, device="cpu")
            self.assertIn("transformers >= 4.51", str(ctx.exception))
            # Legacy families are untouched by the gate.
            with patch.dict(sys.modules, {"transformers": old_transformers}), patch.dict(
                sys.modules, {"sentence_transformers": None}
            ):
                with self.assertRaises(ImportError):
                    embed_runtime.get_sentence_transformer(JINA, device="cpu")
        finally:
            embed_runtime.reset_caches()


class EffectiveModelIdentityTests(unittest.TestCase):
    """Review fix — marker stamps follow CODE_EMBEDDING_MODEL_PATH overrides."""

    def tearDown(self) -> None:
        os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)

    def test_path_override_wins(self):
        from tools.common.embed_runtime import effective_model_identity

        with patch.dict(os.environ, {"CODE_EMBEDDING_MODEL_PATH": "/models/local-qwen3"}):
            self.assertEqual(effective_model_identity(QWEN3), "/models/local-qwen3")
        os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)
        self.assertEqual(effective_model_identity(QWEN3), QWEN3)

    def test_primary_sync_stamps_effective_identity(self):
        import tools.common.primary_vector_sync as pvs
        from tools.common import embedding_marker

        class StampStore:
            def __init__(self):
                self.points = {}

            def collection_exists(self, name):
                return False

            def create_collection(self, name, vectors_config=None, **kwargs):
                pass

            def upsert(self, collection, points, wait=True):
                for point in points:
                    self.points[str(point["id"])] = point

            def create_payload_index(self, *a, **k):
                pass

            def retrieve(self, collection, ids, **k):
                return [self.points[i] for i in ids if i in self.points]

        store = StampStore()
        document = pvs.VectorDocument(
            id="d1", text="t",
            payload={"project_id": "p", "parser": "go", "root_scope": "r", "symbol_id": "s"},
        )
        with patch.dict(os.environ, {"CODE_EMBEDDING_MODEL_PATH": "/models/local-qwen3"}), patch.object(
            pvs.embed_runtime, "get_sentence_transformer",
            lambda *a, **k: type("M", (), {"encode": lambda self, texts, **kw: [[0.1] * 1024 for _ in texts]})(),
        ):
            pvs.sync_vector_documents(
                [document],
                url="local", collection="p", model_name=QWEN3, device="cpu",
                embed_batch_size=1, qdrant_batch_size=1,
                parser="go", project_id="p", root_scope="r", store=store,
            )
        stamped = embedding_marker.check(store, "p", use_cache=False)
        self.assertEqual(stamped["embedding_model"], "/models/local-qwen3")


class TransformesFloorTests(unittest.TestCase):
    """AC4 — old transformers fails fast with an actionable message."""

    def test_old_transformers_raises_clear_error_on_qwen3_load(self):
        from tools.common import embed_runtime

        old_transformers = importlib.util.module_from_spec(
            importlib.util.spec_from_loader("_old_transformers", None)
        )
        old_transformers.__version__ = "4.49.0"
        old_transformers.__spec__ = None
        embed_runtime.reset_caches()
        try:
            with patch.dict(sys.modules, {"transformers": old_transformers}):
                with self.assertRaises(RuntimeError) as ctx:
                    embed_runtime.embed_query("q", QWEN3)
            self.assertIn("transformers >= 4.51", str(ctx.exception))
            self.assertIn("pip install", str(ctx.exception))
        finally:
            embed_runtime.reset_caches()

    def test_current_transformers_passes_the_gate(self):
        import transformers

        from tools.common import embed_runtime

        major, minor = (int(p) for p in transformers.__version__.split(".")[:2])
        if (major, minor) >= (4, 51):
            # must not raise
            embed_runtime._check_qwen3_transformers_floor(QWEN3)


if __name__ == "__main__":
    unittest.main()

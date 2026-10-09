"""Phase 02 — ingest-side pooling unification (12 CodeEmbedder + cobol sync).

Every legacy analyzer CodeEmbedder now loads through the shared
SentenceTransformer runtime (model-config pooling — last-token for Qwen3)
and encodes normalized; the jina family keeps ``trust_remote_code`` via the
policy. Chunking/truncation behavior is unchanged.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import torch

from tools.common import embed_runtime, embedding_runtime  # noqa: E402
try:
    from huggingface_hub.errors import LocalEntryNotFoundError
except ImportError:  # pragma: no cover - hub always present in the dev venv
    class LocalEntryNotFoundError(Exception):
        pass

QWEN3 = "Qwen/Qwen3-Embedding-0.6B"
JINA = "jinaai/jina-embeddings-v3"

# The 12 legacy CodeEmbedder modules (vb_analyzer_base imports python's).
CODE_EMBEDDER_MODULES = [
    "tools.python.python_analyzer",
    "tools.js.js_analyzer",
    "tools.java.java_analyzer",
    "tools.cplus.cplus_analyzer",
    "tools.kotlin.kotlin_analyzer",
    "tools.php.php_analyzer",
    "tools.ts.ts_analyzer",
    "tools.sql.sql_analyzer",
    "tools.delphi.delphi_analyzer",
    "tools.csharp.csharp_analyzer",
    "tools.plsql.plsql_analyzer",
    "tools.android.android_kotlin_analyzer",
]


class _FakeST:
    """SentenceTransformer double: records constructor + encode calls."""

    last_instance: "_FakeST | None" = None

    def __init__(self, model_name, device=None, trust_remote_code=None,
                 local_files_only=None, cache_folder=None, **kwargs):  # noqa: ANN001
        self.model_name = model_name
        self.device = device
        self.trust_remote_code = trust_remote_code
        self.local_files_only = local_files_only
        self.cache_folder = cache_folder
        self.encode_calls: list[tuple[list[str], dict]] = []
        _FakeST.last_instance = self

    def get_sentence_embedding_dimension(self) -> int:
        return 1024

    def encode(self, texts, **kwargs):  # noqa: ANN001, ANN003
        self.encode_calls.append((list(texts), dict(kwargs)))
        rows = [[float(len(t)) / 100.0, 1.0] for t in texts]
        return torch.tensor(rows)


def _fake_loader(model_name, device=None, trust_remote_code=None,
                 local_files_only=None, cache_folder=None, **kwargs):  # noqa: ANN001
    return _FakeST(
        model_name,
        device=device,
        trust_remote_code=trust_remote_code,
        local_files_only=local_files_only,
        cache_folder=cache_folder,
        **kwargs,
    )


class CodeEmbedderStBackendTests(unittest.TestCase):
    """AC1 — every CodeEmbedder loads via the ST runtime, policy-driven."""

    def setUp(self) -> None:
        embed_runtime.reset_caches()
        os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)
        _FakeST.last_instance = None

    def tearDown(self) -> None:
        embed_runtime.reset_caches()
        os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)

    def test_code_embedders_load_via_sentence_transformer(self):
        import importlib

        for module_name in CODE_EMBEDDER_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                _FakeST.last_instance = None
                with patch.object(
                    embed_runtime, "get_sentence_transformer", _fake_loader
                ), patch.object(
                    embedding_runtime, "snapshot_download",
                    side_effect=LocalEntryNotFoundError("cache miss"),
                ):
                    embedder = module.CodeEmbedder(QWEN3, "cpu", 0, False)
                stub = _FakeST.last_instance
                self.assertIsNotNone(stub, module_name)
                # ST backend, no trust_remote_code for Qwen3, offline
                # probe result forwarded.
                self.assertEqual(stub.trust_remote_code, False)
                self.assertEqual(stub.local_files_only, False)
                # Vector size comes from ST, not a probe embed.
                self.assertEqual(embedder.vector_size, 1024)
                # Ingest encodes bare text, normalized.
                vectors = embedder.embed(["symbol text"], batch_size=2)
                self.assertEqual(len(vectors), 1)
                batch, kwargs = stub.encode_calls[-1]
                self.assertEqual(batch, ["symbol text"])
                self.assertTrue(kwargs.get("normalize_embeddings"))

    def test_jina_keeps_trust_remote_code_via_policy(self):
        import importlib

        module = importlib.import_module("tools.python.python_analyzer")
        _FakeST.last_instance = None
        with patch.object(
            embed_runtime, "get_sentence_transformer", _fake_loader
        ), patch.object(
            embedding_runtime, "snapshot_download",
            side_effect=LocalEntryNotFoundError("cache miss"),
        ):
            module.CodeEmbedder(JINA, "cpu", 0, False)
        self.assertTrue(_FakeST.last_instance.trust_remote_code)


class CodeEmbedderChunkBehaviorTests(unittest.TestCase):
    """AC3 — chunking/truncation/mean-pool-of-chunks behavior unchanged."""

    def setUp(self) -> None:
        embed_runtime.reset_caches()
        os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)
        _FakeST.last_instance = None

    def tearDown(self) -> None:
        embed_runtime.reset_caches()
        os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)

    def _embedder(self):
        from tools.python.python_analyzer import CodeEmbedder

        with patch.object(
            embed_runtime, "get_sentence_transformer", _fake_loader
        ), patch.object(
            embedding_runtime, "snapshot_download",
            side_effect=LocalEntryNotFoundError("cache miss"),
        ):
            return CodeEmbedder(QWEN3, "cpu", 10, True)

    def test_chunk_embed_splits_and_averages(self):
        embedder = self._embedder()
        stub = _FakeST.last_instance
        vectors = embedder.embed(["a" * 25], batch_size=4)
        # 25 chars / 10-char chunks → 3 chunks averaged into one vector.
        self.assertEqual(len(stub.encode_calls[-1][0]), 3)
        self.assertEqual(len(vectors), 1)

    def test_truncation_without_chunk_embed(self):
        embedder = self._embedder()
        stub = _FakeST.last_instance
        embedder.chunk_embed = False
        embedder.embed(["a" * 25], batch_size=4)
        batch, _ = stub.encode_calls[-1]
        self.assertEqual(batch, ["a" * 10])


class LocalModelPathOverrideTests(unittest.TestCase):
    def test_code_embedding_model_path_override_reaches_loader(self):
        import tempfile

        from tools.python import python_analyzer
        from tools.python.python_analyzer import CodeEmbedder

        with tempfile.TemporaryDirectory() as model_dir:
            os.environ["CODE_EMBEDDING_MODEL_PATH"] = model_dir
            try:
                with patch.object(
                    embed_runtime, "get_sentence_transformer", _fake_loader
                ), patch.object(
                    python_analyzer,
                    "resolve_embedding_cache",
                    return_value=(model_dir, True),
                ):
                    CodeEmbedder(QWEN3, "cpu", 0, False)
                self.assertEqual(_FakeST.last_instance.model_name, model_dir)
            finally:
                os.environ.pop("CODE_EMBEDDING_MODEL_PATH", None)


class PrimarySyncPolicyTests(unittest.TestCase):
    """primary_vector_sync + cobol use the shared policy, not inline heuristics."""

    def test_primary_sync_trust_remote_code_from_policy(self):
        import tools.common.primary_vector_sync as pvs

        calls = []

        def fake_factory(model_name, device=None, trust_remote_code=None):
            calls.append((model_name, trust_remote_code))
            return _FakeST(model_name, device=device)

        document = pvs.VectorDocument(
            id="doc-1",
            text="text",
            payload={
                "project_id": "p",
                "parser": "go",
                "root_scope": "r",
                "symbol_id": "s",
            },
        )
        store = type(
            "FakeStore",
            (),
            {
                "collection_exists": lambda self, c: False,
                "create_collection": lambda self, *a, **k: None,
                "create_payload_index": lambda self, *a, **k: None,
                "upsert": lambda self, *a, **k: None,
            },
        )()

        with patch.object(pvs.embed_runtime, "get_sentence_transformer", fake_factory):
            pvs.sync_vector_documents(
                [document],
                url="local-code-store",
                collection="project_x",
                model_name=JINA,
                device="cpu",
                embed_batch_size=1,
                qdrant_batch_size=1,
                parser="go",
                project_id="p",
                root_scope="r",
                store=store,
            )
        self.assertEqual(calls, [(JINA, True)])

    def test_cobol_sync_trust_remote_code_from_policy(self):
        from tools.cobol import qdrant as cobol_qdrant

        store = type(
            "FakeStore",
            (),
            {
                "collection_exists": lambda self, c: False,
                "create_collection": lambda self, *a, **k: None,
                "upsert": lambda self, *a, **k: None,
                "delete": lambda self, *a, **k: None,
            },
        )()

        def fake_docs(result, max_chars=800):
            return [{"id": "x", "text": "t", "payload": {"project_id": "p"}}]

        with patch.object(
            cobol_qdrant, "get_code_qdrant_store", return_value=store
        ), patch.object(
            cobol_qdrant, "semantic_documents", fake_docs
        ), patch.object(
            cobol_qdrant.embed_runtime, "get_sentence_transformer", _fake_loader
        ):
            cobol_qdrant.sync_qdrant(
                type("R", (), {"project_id": "p", "nodes": []})(),
                url="local-code-store",
                collection="cobol_x",
                model_name=JINA,
            )
        self.assertTrue(_FakeST.last_instance.trust_remote_code)


if __name__ == "__main__":
    unittest.main()

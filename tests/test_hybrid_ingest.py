"""Phase 02 — hybrid ingest: schema, chokepoints, marker, wiring.

Flag ON: every writer chokepoint records dense + named sparse vectors and
NEW collections declare the sparse config; the marker stamps ``hybrid``.
Flag OFF: byte-parallel with the legacy behavior. A pre-existing dense-only
collection is warned about (backfill pointer), never mutated.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
DOC_TINY = ROOT / "doc-tiny"
for entry in (CODE_TINY, DOC_TINY):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import embedding_marker, embed_runtime, local_qdrant  # noqa: E402

BGE = "BAAI/bge-m3"


class HybridEnv:
    """Context manager: EMBED_HYBRID on with a stub sparse encoder."""

    def __init__(self, indices=(4, 9), values=(0.5, 0.25)):
        self.indices = list(indices)
        self.values = list(values)

    def __enter__(self):
        os.environ["EMBED_HYBRID"] = "1"
        self.patchers = [
            patch.object(embed_runtime, "encode_sparse",
                         side_effect=self._fake_encode),
        ]
        for patcher in self.patchers:
            patcher.start()
        return self

    def _fake_encode(self, model_source, texts, device=None, **kwargs):
        return [(list(self.indices), list(self.values)) for _ in texts]

    def __exit__(self, *exc):
        os.environ.pop("EMBED_HYBRID", None)
        for patcher in self.patchers:
            patcher.stop()
        return False


class FakeWriterStore:
    """Store double recording create kwargs and upsert points."""

    def __init__(self, *, has_sparse=False):
        self.collections: set = set()
        self.create_kwargs: dict = {}
        self.points: dict = {}
        self.upserts: list = []
        self.dim = 1024
        self.has_sparse = has_sparse

    def collection_exists(self, name):
        return name in self.collections

    def get_collection_info(self, name):
        vectors = SimpleNamespace(size=self.dim)
        sparse = (
            SimpleNamespace(**{local_qdrant.HYBRID_SPARSE_VECTOR_NAME: object()})
            if self.has_sparse else None
        )
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(
            vectors=vectors, sparse_vectors=sparse,
        )))

    def create_collection(self, name, vectors_config=None, **kwargs):
        self.collections.add(name)
        self.create_kwargs = kwargs
        if vectors_config is not None:
            self.dim = int(vectors_config.size)
        self.has_sparse = local_qdrant.HYBRID_SPARSE_VECTOR_NAME in (
            kwargs.get("sparse_vectors_config") or {}
        )

    def upsert(self, collection, points, wait=True):
        for point in points:
            self.upserts.append(point)
            self.points[str(point["id"])] = point

    def retrieve(self, collection, ids, with_payload=True, with_vectors=False, **kwargs):
        return [
            {"id": pid, "payload": self.points[pid]["payload"]}
            for pid in ids if pid in self.points
        ]

    def count(self, collection_name, count_filter=None, exact=True):
        return SimpleNamespace(count=len(self.points))

    def create_payload_index(self, collection, field, wait=True):
        self.payload_indexes = getattr(self, "payload_indexes", [])
        self.payload_indexes.append((collection, field))


class EnsureCollectionSchemaTests(unittest.TestCase):
    """AC3a — collection schema per flag state."""

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        local_qdrant._HYBRID_BACKFILL_WARNED.clear()

    def test_new_collection_flag_on_declares_sparse(self):
        store = FakeWriterStore()
        with HybridEnv():
            local_qdrant.ensure_collection(store, "proj", 1024, embedding_model=BGE)
        self.assertIn("sparse", store.create_kwargs.get("sparse_vectors_config") or {})
        self.assertIsInstance(
            store.create_kwargs["sparse_vectors_config"]["sparse"],
            qmodels.SparseVectorParams,
        )
        meta = embedding_marker.check(store, "proj")
        self.assertTrue(meta["hybrid"])

    def test_new_collection_flag_off_has_no_sparse(self):
        store = FakeWriterStore()
        local_qdrant.ensure_collection(store, "proj", 1024, embedding_model=BGE)
        self.assertEqual(store.create_kwargs.get("sparse_vectors_config"), None)
        meta = embedding_marker.check(store, "proj")
        self.assertFalse(meta.get("hybrid", False))

    def test_existing_dense_only_flag_on_warns_without_mutation(self):
        """AC3b / red-team F5 — flag flipped mid-life: warn + point at the
        backfill script, never recreate or mutate."""
        store = FakeWriterStore(has_sparse=False)
        store.collections.add("proj")  # exists, dense-only
        with HybridEnv():
            with self.assertLogs("root", level="INFO") if False else _CapturePrint() as captured:
                local_qdrant.ensure_collection(store, "proj", 1024, embedding_model=BGE)
        joined = "\n".join(captured.lines)
        self.assertIn("backfill_hybrid_sparse.py", joined)
        self.assertEqual(len(store.upserts), 1)  # only the marker re-stamp
        self.assertFalse(store.has_sparse)

    def test_mess_collections_stay_exempt(self):
        """AC3d — hash-vector collections never participate."""
        store = FakeWriterStore()
        with HybridEnv():
            local_qdrant.ensure_collection(store, "proj_mess", 8, embedding_model=BGE)
        self.assertEqual(store.create_kwargs.get("sparse_vectors_config"), None)
        self.assertEqual(embedding_marker.check(store, "proj_mess"), None)


class _CapturePrint:
    def __init__(self):
        self.lines = []

    def __enter__(self):
        import contextlib
        import io

        self._buffer = io.StringIO()
        self._ctx = contextlib.redirect_stdout(self._buffer)
        self._ctx.__enter__()
        return self

    def __exit__(self, *exc):
        self.lines = self._buffer.getvalue().splitlines()
        self._ctx.__exit__(*exc)
        return False


class WriterUpsertHybridTests(unittest.TestCase):
    """AC3b — dual-vector upsert through the analyzer writer funnel."""

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        local_qdrant._HYBRID_BACKFILL_WARNED.clear()
        embed_runtime.reset_caches()

    def _writer(self, store):
        return local_qdrant.LocalQdrantWriter(
            None, "proj", 1024, embedding_model=BGE,
        ).__dict__ and None

    def test_analyzer_writer_upserts_dual_vector(self):
        store = FakeWriterStore(has_sparse=True)
        store.collections.add("proj")
        writer = local_qdrant.LocalQdrantWriter(
            None, "proj", 1024, embedding_model=BGE,
        )
        writer._store = store
        with HybridEnv():
            writer.upsert([{
                "id": "p1", "vector": [0.1] * 1024,
                "payload": {"code": "def run(): pass", "project_id": "proj"},
            }])
        point = store.points["p1"]
        self.assertIsInstance(point["vector"], dict)
        self.assertEqual(point["vector"][""], [0.1] * 1024)
        sparse = point["vector"]["sparse"]
        self.assertEqual(list(sparse.indices), [4, 9])
        self.assertEqual(list(sparse.values), [0.5, 0.25])

    def test_analyzer_writer_degrades_dense_only_on_legacy_collection(self):
        """A dense-only collection rejects sparse slots with a hard client
        error — the writer must probe capability and skip the sparse half."""
        store = FakeWriterStore(has_sparse=False)
        store.collections.add("proj")
        writer = local_qdrant.LocalQdrantWriter(
            None, "proj", 1024, embedding_model=BGE,
        )
        writer._store = store
        with HybridEnv():
            writer.upsert([{
                "id": "p1", "vector": [0.1] * 1024,
                "payload": {"code": "def run(): pass"},
            }])
        self.assertEqual(store.points["p1"]["vector"], [0.1] * 1024)

    def test_analyzer_writer_flag_off_keeps_list_vectors(self):
        store = FakeWriterStore()
        store.collections.add("proj")
        writer = local_qdrant.LocalQdrantWriter(
            None, "proj", 1024, embedding_model=BGE,
        )
        writer._store = store
        writer.upsert([{
            "id": "p1", "vector": [0.1] * 1024,
            "payload": {"code": "def run(): pass"},
        }])
        self.assertEqual(store.points["p1"]["vector"], [0.1] * 1024)

    def test_mess_writer_never_hybridizes(self):
        store = FakeWriterStore()
        store.collections.add("proj_mess")
        writer = local_qdrant.LocalQdrantWriter(
            None, "proj_mess", 8, embedding_model=BGE,
        )
        writer._store = store
        with HybridEnv():
            writer.upsert([{
                "id": "m1", "vector": [0.5] * 8, "payload": {"text": "hi"},
            }])
        self.assertEqual(store.points["m1"]["vector"], [0.5] * 8)

    def test_hybrid_document_text_priority(self):
        self.assertEqual(
            local_qdrant.hybrid_document_text(
                {"text": "a", "note": "b", "code": "c"}), "a")
        self.assertEqual(
            local_qdrant.hybrid_document_text({"note": "b", "code": "c"}), "b")
        self.assertEqual(
            local_qdrant.hybrid_document_text({"summary": "s", "comment": "k"}),
            "s\nk")
        self.assertEqual(
            local_qdrant.hybrid_document_text({"qualified_name": "mod.func"}),
            "mod.func")
        self.assertEqual(local_qdrant.hybrid_document_text(None), "")


class PrimarySyncHybridTests(unittest.TestCase):
    """sync_vector_documents records the sparse pair for the exact text."""

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)

    def test_sync_records_dual_vector_when_hybrid(self):
        from tools.common import primary_vector_sync

        store = FakeWriterStore(has_sparse=True)
        store.collections.add("proj")
        captured: dict = {}

        def fake_encode(model_source, texts, device=None, **kwargs):
            captured["texts"] = list(texts)
            captured["source"] = model_source
            return [( [4, 9], [0.5, 0.25]) for _ in texts]

        dense = [[0.2] * 1024]

        class _Model:
            def encode(self, texts, **kwargs):
                import numpy as np

                return np.array(dense * len(texts))

        with HybridEnv() as env:
            with patch.object(embed_runtime, "encode_sparse", fake_encode), \
                 patch.object(embed_runtime, "get_sentence_transformer",
                              return_value=_Model()):
                count = primary_vector_sync.sync_vector_documents(
                    [
                        primary_vector_sync.VectorDocument(
                            id="doc-1", text="incremental sync worker",
                            payload={"project_id": "proj", "parser": "python",
                                     "root_scope": "repo"},
                        )
                    ],
                    url="", collection="proj", model_name=BGE, device="cpu",
                    embed_batch_size=4, qdrant_batch_size=8, parser="python",
                    project_id="proj", root_scope="repo", store=store,
                )
        self.assertEqual(count, 1)
        self.assertEqual(captured["texts"], ["incremental sync worker"])
        point = store.points["doc-1"]
        self.assertEqual(point["vector"][""], [0.2] * 1024)
        self.assertEqual(list(point["vector"]["sparse"].indices), [4, 9])

    def test_first_sync_of_new_collection_writes_sparse(self):
        """Review fix: capability probes the LIVE collection — a first sync
        creates it WITH the sparse config and the points land dual-vector,
        not dense-only into a hybrid-declared collection."""
        from tools.common import primary_vector_sync

        store = FakeWriterStore()  # collection does NOT exist yet
        captured: dict = {}

        def fake_encode(model_source, texts, device=None, **kwargs):
            captured["called"] = True
            return [([4, 9], [0.5, 0.25]) for _ in texts]

        class _Model:
            def encode(self, texts, **kwargs):
                import numpy as np

                return np.array([[0.2] * 1024] * len(texts))

            def get_sentence_embedding_dimension(self):
                return 1024

        with HybridEnv():
            with patch.object(embed_runtime, "encode_sparse", fake_encode), \
                 patch.object(embed_runtime, "get_sentence_transformer",
                              return_value=_Model()):
                primary_vector_sync.sync_vector_documents(
                    [
                        primary_vector_sync.VectorDocument(
                            id="doc-1", text="incremental sync worker",
                            payload={"project_id": "proj", "parser": "python",
                                     "root_scope": "repo"},
                        )
                    ],
                    url="", collection="proj", model_name=BGE, device="cpu",
                    embed_batch_size=4, qdrant_batch_size=8, parser="python",
                    project_id="proj", root_scope="repo", store=store,
                )
        self.assertTrue(captured.get("called"))
        self.assertIn("sparse", store.create_kwargs.get("sparse_vectors_config") or {})
        point = store.points["doc-1"]
        self.assertEqual(list(point["vector"]["sparse"].indices), [4, 9])


class MarkerHybridTests(unittest.TestCase):
    """AC3c — sentinel stamps/exposes ``hybrid``; mismatch logic unchanged."""

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        embedding_marker.reset_sentinel_cache()

    def test_stamp_roundtrip_hybrid_flag(self):
        store = FakeWriterStore()
        embedding_marker.stamp(store, "c1", embedding_model=BGE,
                               vector_size=1024, hybrid=True)
        meta = embedding_marker.check(store, "c1")
        self.assertIs(meta["hybrid"], True)
        embedding_marker.stamp(store, "c2", embedding_model=BGE,
                               vector_size=1024, hybrid=False)
        meta = embedding_marker.check(store, "c2")
        self.assertIs(meta["hybrid"], False)

    def test_enforce_restamps_when_hybrid_flag_flips(self):
        store = FakeWriterStore()
        store.collections.add("proj")
        embedding_marker.stamp(store, "proj", embedding_model=BGE,
                               vector_size=1024, hybrid=False)
        embedding_marker.enforce(store, "proj", embedding_model=BGE,
                                 vector_size=1024, hybrid=True)
        meta = embedding_marker.check(store, "proj")
        self.assertIs(meta["hybrid"], True)

    def test_sentinel_upsert_keeps_plain_list_vector(self):
        store = FakeWriterStore()
        embedding_marker.stamp(store, "proj", embedding_model=BGE,
                               vector_size=1024, hybrid=True)
        sentinel = store.points[str(embedding_marker.meta_point_id("proj"))]
        self.assertIsInstance(sentinel["vector"], list)
        self.assertEqual(len(sentinel["vector"]), 1024)

    def test_mismatch_ignores_hybrid_flag(self):
        store = FakeWriterStore()
        embedding_marker.stamp(store, "proj", embedding_model=BGE,
                               vector_size=1024, hybrid=True)
        self.assertEqual(embedding_marker.mismatch(store, "proj", BGE), None)
        self.assertIsNotNone(embedding_marker.mismatch(store, "proj", "other/model"))


class DevEnvWiringTests(unittest.TestCase):
    """AC7 anchor — the flag default stays OFF and wiring passes it through."""

    def test_pass_through_from_shell_env(self):
        from cortex_harness import dev

        with patch.dict(os.environ, {"EMBED_HYBRID": "1"}):
            result: dict = {}
            dev._pass_through_embed_hybrid(result, {})
            self.assertEqual(result.get("EMBED_HYBRID"), "1")
        result: dict = {}
        dev._pass_through_embed_hybrid(result, {})
        self.assertNotIn("EMBED_HYBRID", result)

    def test_flag_off_by_default(self):
        os.environ.pop("EMBED_HYBRID", None)
        self.assertFalse(embed_runtime.hybrid_enabled(BGE))


if __name__ == "__main__":
    unittest.main()

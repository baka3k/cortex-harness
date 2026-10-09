"""Phase 03 — embedding-model sentinel: chokepoints, exclusion, reset tooling.

A model swap must never silently mix 1024-dim vectors: writers raise at the
chokepoints, the sentinel never surfaces as a search hit, mismatched
queries warn visibly, and the reset tools clear the sentinel (no runbook
deadlock).
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

from tools.common import embedding_marker, qdrant_query_support  # noqa: E402
from tools.common.embedding_marker import (  # noqa: E402
    EmbeddingModelMismatchError,
    meta_point_id,
)

QWEN3 = "Qwen/Qwen3-Embedding-0.6B"
JINA = "jinaai/jina-embeddings-v3"


def _meta_payload(model: str) -> dict:
    return {"_embed_meta": True, "embedding_model": model, "vector_size": 1024}


class FakeCodeStore:
    """QdrantStore double covering the marker/search surface."""

    def __init__(self) -> None:
        self.points: dict[str, dict] = {}
        self.created: list[str] = []
        self.dropped: list[str] = []
        self.upserts: list[dict] = []
        self.collections: set[str] = set()
        self.dim = 1024

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def get_collection_info(self, name: str) -> Any:  # noqa: ANN401
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=self.dim)))
        )

    def create_collection(self, name, vectors_config=None, **kwargs):  # noqa: ANN001
        self.collections.add(name)
        if vectors_config is not None:
            self.dim = int(vectors_config.size)
        self.created.append(name)

    def upsert(self, collection, points, wait=True):  # noqa: ANN001
        for point in points:
            self.upserts.append(point)
            self.points[str(point["id"])] = point

    def retrieve(self, collection, ids, with_payload=True, with_vectors=False, **kwargs):  # noqa: ANN001
        return [
            {"id": pid, "payload": self.points[pid]["payload"]}
            for pid in ids
            if pid in self.points
        ]

    def count(self, collection_name, count_filter=None, exact=True):  # noqa: ANN001
        return SimpleNamespace(count=len(self.points))

    def query_points(self, collection, query=None, limit=10, query_filter=None, **kwargs):  # noqa: ANN001
        return SimpleNamespace(points=[
            {"id": pid, "score": 0.9, "payload": p["payload"]}
            for pid, p in self.points.items()
            if not _filtered_out(p["payload"], query_filter)
        ][:limit])

    def delete_collection(self, name) -> None:  # noqa: ANN001
        self.dropped.append(name)
        self.collections.discard(name)
        self.points.clear()

    def delete(self, collection, filter_selector=None, wait=True, **kwargs):  # noqa: ANN001
        flt = getattr(filter_selector, "filter", filter_selector)
        must = list(getattr(flt, "must", None) or [])
        for pid, point in list(self.points.items()):
            payload = point["payload"]
            for cond in must:
                key = getattr(cond, "key", None)
                match = getattr(cond, "match", None)
                value = getattr(match, "value", None)
                if key and payload.get(key) == value:
                    self.points.pop(pid, None)
                    break


def _filtered_out(payload: dict, query_filter) -> bool:
    """Minimal evaluator: must_not on ``_embed_meta`` flag / project field."""
    if query_filter is None:
        return False
    must_not = getattr(query_filter, "must_not", None) or []
    for cond in must_not:
        key = getattr(cond, "key", None)
        match = getattr(cond, "match", None)
        value = getattr(match, "value", None)
        if key and payload.get(key) == value:
            return True
    return False


def _seed(store: FakeCodeStore, model: str, collection: str = "col") -> None:
    store.collections.add(collection)
    store.upsert(collection, [{"id": meta_point_id(collection), "vector": [0.0] * 1024,
                               "payload": _meta_payload(model)}])


def _seed_with_data(store: FakeCodeStore, model: str, collection: str = "col") -> None:
    """Sentinel + one real point: a genuine mismatch, not a post-reset state."""
    _seed(store, model, collection)
    store.upsert(collection, [{"id": "real-1", "vector": [0.0] * 1024,
                               "payload": {"symbol_id": "s"}}])


class ChokepointTests(unittest.TestCase):
    """AC1 — mismatch raises at the writer chokepoints; fresh collections stamp."""

    def setUp(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def tearDown(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def test_ensure_collection_raises_on_model_mismatch(self):
        from tools.common.local_qdrant import ensure_collection

        store = FakeCodeStore()
        _seed_with_data(store, JINA)
        with self.assertRaises(EmbeddingModelMismatchError) as ctx:
            ensure_collection(store, "col", 1024, embedding_model=QWEN3)
        self.assertIn("reset_code_collection.py", str(ctx.exception))

    def test_ensure_collection_stamps_fresh_and_legacy_collections(self):
        from tools.common.local_qdrant import ensure_collection

        # fresh collection → create + stamp
        fresh = FakeCodeStore()
        ensure_collection(fresh, "proj", 1024, embedding_model=QWEN3, project_id="proj")
        self.assertTrue(fresh.created)
        stamped = embedding_marker.check(fresh, "proj")
        self.assertEqual(stamped["embedding_model"], QWEN3)
        self.assertEqual(stamped["project_id"], "proj")

        # pre-marker legacy collection → backfill stamp, no raise
        legacy = FakeCodeStore()
        legacy.collections.add("proj")
        ensure_collection(legacy, "proj", 1024, embedding_model=QWEN3)
        self.assertEqual(embedding_marker.check(legacy, "proj")["embedding_model"], QWEN3)

    def test_mismatch_raises_via_primary_sync(self):
        import tools.common.primary_vector_sync as pvs

        seeded = FakeCodeStore()
        _seed_with_data(seeded, JINA, "p")
        document = pvs.VectorDocument(
            id="d1", text="t",
            payload={"project_id": "p", "parser": "go", "root_scope": "r", "symbol_id": "s"},
        )
        store_stub = type(
            "S", (),
            {
                "collection_exists": lambda self, c: True,
                "get_collection_info": lambda self, c: SimpleNamespace(
                    config=SimpleNamespace(params=SimpleNamespace(
                        vectors=SimpleNamespace(size=1024)))),
                "retrieve": lambda self, collection, ids, **k: seeded.retrieve(collection, ids, **k),
                "create_payload_index": lambda self, *a, **k: None,
                "upsert": lambda self, *a, **k: None,
            },
        )()
        with patch.object(pvs.embed_runtime, "get_sentence_transformer",
            lambda *a, **k: type("M", (), {"encode": lambda self, texts, **kw: [[0.1] * 1024 for _ in texts]})(),
        ):
            with self.assertRaises(EmbeddingModelMismatchError):
                pvs.sync_vector_documents(
                    [document],
                    url="local", collection="p", model_name=QWEN3, device="cpu",
                    embed_batch_size=1, qdrant_batch_size=1,
                    parser="go", project_id="p", root_scope="r", store=store_stub,
                )

    def test_doc_create_collection_guard_and_stamp(self):
        import graphrag_ingest_langextract as doc_ingest

        store = FakeCodeStore()
        _seed_with_data(store, JINA, "proj_doc")
        with self.assertRaises(ValueError) as ctx:
            doc_ingest.create_collection(
                store, "proj_doc", 1024, embedding_model=QWEN3, project_id="proj"
            )
        self.assertIn("0_reset_all.py", str(ctx.exception))

        fresh = FakeCodeStore()
        doc_ingest.create_collection(
            fresh, "proj_doc", 1024, embedding_model=QWEN3, project_id="proj"
        )
        stamped = embedding_marker.check(fresh, "proj_doc")
        self.assertEqual(stamped["embedding_model"], QWEN3)

        # same-dim legacy collection without a marker is size-guard-clean and
        # gets backfilled (dim matches → no raise)
        legacy = FakeCodeStore()
        legacy.collections.add("proj_doc")
        doc_ingest.create_collection(legacy, "proj_doc", 1024, embedding_model=QWEN3)
        self.assertEqual(embedding_marker.check(legacy, "proj_doc")["embedding_model"], QWEN3)


class MessageCollectionExemptionTests(unittest.TestCase):
    """AC2 — ``_mess`` hash-vector collections are never marker-checked."""

    def setUp(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def tearDown(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def test_mess_collections_skip_marker(self):
        from tools.common.local_qdrant import ensure_collection

        # Fresh-collection branch: create without stamping.
        store = FakeCodeStore()
        ensure_collection(store, "proj_mess", 1024, embedding_model=QWEN3)
        self.assertEqual(embedding_marker.check(store, "proj_mess"), None)

        # Existing-collection branch (the real AC2 trigger): a differently
        # stamped _mess collection is ignored entirely — no raise, no stamp.
        embedding_marker.reset_sentinel_cache()
        store = FakeCodeStore()
        _seed(store, JINA, "proj_mess")
        ensure_collection(store, "proj_mess", 1024, embedding_model=QWEN3)
        stamped = embedding_marker.check(store, "proj_mess")
        self.assertEqual(stamped["embedding_model"], JINA)  # untouched


class SearchExclusionTests(unittest.TestCase):
    """AC3 — sentinel never surfaces; mismatch warns via the shared merge."""

    def setUp(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def tearDown(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def test_sentinel_excluded_from_unscoped_and_scoped_queries(self):
        store = FakeCodeStore()
        _seed(store, QWEN3)
        store.upsert("col", [{"id": "real-1", "vector": [0.0] * 1024,
                              "payload": {"symbol_id": "s", "project_id": "p"}}])
        # unscoped (project filter None) — still carries the must_not clause
        hits = qdrant_query_support.search_collection(
            store, "col", [0.0] * 1024, None, 10, None
        )
        self.assertEqual([h["id"] for h in hits], ["real-1"])
        # scoped
        hits = qdrant_query_support.search_collection(
            store, "col", [0.0] * 1024, None, 10, "p"
        )
        self.assertEqual([h["id"] for h in hits], ["real-1"])

    def test_merge_collections_reports_mismatch_warning(self):
        store = FakeCodeStore()
        _seed(store, JINA)
        store.upsert("col", [{"id": "real-1", "vector": [0.0] * 1024,
                              "payload": {"symbol_id": "s"}}])
        hits, errors = qdrant_query_support.merge_collections(
            store, [("col", None)], [0.0] * 1024, 5, None,
            embedding_model=QWEN3,
        )
        self.assertEqual(len(hits), 1)
        mismatch_entries = [e for e in errors if "embedding_model_mismatch" in e]
        self.assertEqual(len(mismatch_entries), 1)
        self.assertIn(JINA, mismatch_entries[0]["embedding_model_mismatch"])
        self.assertIn(QWEN3, mismatch_entries[0]["embedding_model_mismatch"])

    def test_merge_collections_no_warning_when_model_matches(self):
        store = FakeCodeStore()
        _seed(store, QWEN3)
        _, errors = qdrant_query_support.merge_collections(
            store, [("col", None)], [0.0] * 1024, 5, None,
            embedding_model=QWEN3,
        )
        self.assertEqual([e for e in errors if "embedding_model_mismatch" in e], [])


class SentinelOnlyRestampTests(unittest.TestCase):
    """AC4 — a collection holding only the sentinel re-stamps (no deadlock)."""

    def setUp(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def tearDown(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def test_only_sentinel_left_restamps_new_model(self):
        from tools.common.local_qdrant import ensure_collection

        store = FakeCodeStore()
        _seed(store, JINA)  # post-reset: sentinel survived, data gone
        ensure_collection(store, "proj", 1024, embedding_model=QWEN3)
        stamped = embedding_marker.check(store, "proj")
        self.assertEqual(stamped["embedding_model"], QWEN3)

    def test_doc_reset_deletes_sentinel(self):
        import importlib

        reset_all = importlib.import_module("0_reset_all")
        client = FakeCodeStore()
        client.collections.add("proj_doc")
        client.upsert("proj_doc", [
            {"id": "point-1", "vector": [0.0],
             "payload": {"project_id_normalized": "proj", "text": "t"}},
            {"id": meta_point_id("proj_doc"), "vector": [0.0] * 1024,
             "payload": _meta_payload(QWEN3)},
        ])
        client.existing = True
        with patch.object(reset_all, "get_document_qdrant_store", return_value=client):
            reset_all.reset_qdrant("proj_doc", project_id="proj", dry_run=False)
        self.assertNotIn(meta_point_id("proj_doc"), client.points)
        self.assertNotIn("point-1", client.points)

    def test_doc_reset_zero_matches_still_clears_sentinel(self):
        """F2 corner: a reset matching 0 project points must still drop the
        stale sentinel, or the next ingest deadlocks against it."""
        import importlib

        reset_all = importlib.import_module("0_reset_all")
        client = FakeCodeStore()
        client.collections.add("proj_doc")
        # Only a stale sentinel, no project-scoped points.
        client.upsert("proj_doc", [
            {"id": meta_point_id("proj_doc"), "vector": [0.0] * 1024,
             "payload": _meta_payload(JINA)},
        ])
        with patch.object(reset_all, "get_document_qdrant_store", return_value=client):
            reset_all.reset_qdrant("proj_doc", project_id="proj", dry_run=False)
        self.assertNotIn(meta_point_id("proj_doc"), client.points)


class MarkerFailClosedTests(unittest.TestCase):
    """Review fix — a store error during the marker read must fail the writer."""

    def setUp(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def tearDown(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def test_transient_store_error_does_not_restamp(self):
        from tools.common.local_qdrant import ensure_collection

        store = FakeCodeStore()
        _seed_with_data(store, JINA)
        store.existing = True
        store.collections.add("col")

        def broken_retrieve(collection, ids, **kwargs):
            raise RuntimeError("remote timeout")

        store.retrieve = broken_retrieve
        with self.assertRaises(RuntimeError):
            ensure_collection(store, "col", 1024, embedding_model=QWEN3)
        # The stale jina marker was NOT laundered away.
        self.assertEqual(
            store.points[meta_point_id("col")]["payload"]["embedding_model"], JINA
        )

    def test_query_side_mismatch_degrades_soft_on_store_error(self):
        store = FakeCodeStore()
        store.retrieve = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        self.assertIsNone(embedding_marker.mismatch(store, "col", QWEN3))


class CacheTtlTests(unittest.TestCase):
    """Review fix — cross-process sentinel cache staleness is TTL-bounded."""

    def setUp(self) -> None:
        embedding_marker.reset_sentinel_cache()

    def tearDown(self) -> None:
        embedding_marker.reset_sentinel_cache()
        os.environ.pop("EMBEDDING_MARKER_CACHE_TTL", None)

    def test_expired_cache_entry_reprobes(self):
        store = FakeCodeStore()
        _seed(store, QWEN3)
        first = embedding_marker.check(store, "col")
        self.assertEqual(first["embedding_model"], QWEN3)
        # Another process resets + re-stamps with a different model.
        store.points[meta_point_id("col")]["payload"]["embedding_model"] = JINA
        # Fresh cache → stale read within TTL.
        self.assertEqual(embedding_marker.check(store, "col")["embedding_model"], QWEN3)
        with patch.dict(os.environ, {"EMBEDDING_MARKER_CACHE_TTL": "0"}):
            self.assertEqual(embedding_marker.check(store, "col")["embedding_model"], JINA)


class ResetCodeCollectionScriptTests(unittest.TestCase):
    """AC5 — the code reset drops the right targets, keeps ``_mess`` by default."""

    def _run(self, argv: list[str]) -> tuple[dict[str, list[str]], list[str]]:
        import importlib.util

        script_path = CODE_TINY / "scripts" / "reset_code_collection.py"
        spec = importlib.util.spec_from_file_location("reset_code_collection", script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        store = FakeCodeStore()
        store.collections.update({"proj", "proj_mess"})
        store.upsert("proj", [{"id": "x", "vector": [0.0], "payload": {}}])
        with patch.object(module, "get_code_qdrant_store", return_value=store), patch.object(
            sys, "argv", ["reset_code_collection.py", *argv]
        ):
            module.main()
        return store.points, store.dropped

    def test_force_drops_project_but_keeps_mess(self):
        remaining, dropped = self._run(["--project-id", "proj", "--force"])
        self.assertEqual(dropped, ["proj"])
        # The _mess sibling was never targeted.

    def test_include_messages_drops_mess_too(self):
        _, dropped = self._run(["--project-id", "proj", "--force", "--include-messages"])
        self.assertEqual(sorted(dropped), ["proj", "proj_mess"])

    def test_dry_run_deletes_nothing(self):
        remaining, dropped = self._run(["--project-id", "proj", "--dry-run"])
        self.assertEqual(dropped, [])
        self.assertIn("x", remaining)

    def test_force_required(self):
        with self.assertRaises(SystemExit):
            self._run(["--project-id", "proj"])


if __name__ == "__main__":
    unittest.main()

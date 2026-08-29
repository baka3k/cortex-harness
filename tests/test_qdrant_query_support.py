"""Tests for tools/common/qdrant_query_support.py + qdrant_layout_cache.py."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
for entry in (str(CODE_TINY), str(CODE_TINY / "mcp")):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import embed_runtime, qdrant_layout_cache, qdrant_query_support  # noqa: E402


class _RecordingStore:
    """Stub store capturing query/retrieve calls; serves canned points."""

    def __init__(self, points=None):
        self.points = points or []
        self.query_calls = []
        self.retrieve_calls = []

    def query_points(self, collection_name, **kwargs):
        self.query_calls.append((collection_name, kwargs))
        response = MagicMock()
        response.points = list(self.points)
        return response

    def retrieve(self, collection_name, ids, *, with_payload=True, with_vectors=False, **kwargs):
        self.retrieve_calls.append((collection_name, list(ids), with_payload))
        return [
            {"id": point_id, "payload": {"text": f"full text {point_id}", "summary": "s"}}
            for point_id in ids
        ]


def _point(point_id, score, payload, collection=None):
    point = {"id": point_id, "score": score, "payload": payload}
    if collection is not None:
        point["_collection"] = collection
    return point


class SearchCollectionTests(unittest.TestCase):
    def test_selector_excludes_text_and_hits_are_tagged(self):
        store = _RecordingStore()
        hits = qdrant_query_support.search_collection(
            store, "col_a", [0.1, 0.2], None, 5, project_id="proj-a"
        )
        selector = store.query_calls[0][1]["with_payload"]
        self.assertEqual(selector, qdrant_query_support.PAYLOAD_EXCLUDE_SELECTOR)
        self.assertEqual(selector.exclude, ["text"])
        self.assertFalse(store.query_calls[0][1]["with_vectors"])
        for hit in hits:
            self.assertEqual(hit["_collection"], "col_a")

    def test_merge_keeps_higher_score_with_provenance(self):
        # Same point id from two collections (uuid5 ids can collide);
        # the winner must keep its own _collection tag.
        hit_low = _point("dup-id", 0.4, {"summary": "a"}, collection="col_a")
        hit_high = _point("dup-id", 0.9, {"summary": "b"}, collection="col_b")
        merged = qdrant_query_support.merge_hits(
            [[hit_low], [hit_high]], top_k=5
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["_collection"], "col_b")
        self.assertEqual(merged[0]["score"], 0.9)

    def test_merge_cuts_to_top_k(self):
        hits = [[_point(f"id{i}", i / 10, {}, collection="c") for i in range(10)]]
        merged = qdrant_query_support.merge_hits(hits, top_k=3)
        self.assertEqual([item["id"] for item in merged], ["id9", "id8", "id7"])


class BackendContentContractTests(unittest.TestCase):
    """Fixture payload families through each backend's _select_content."""

    LEGACY = {
        "summary": "legacy summary",
        "comment": "legacy comment",
        "code": "def x(): ...",
        "name": "legacy_symbol",
        "text": "legacy text",
    }
    PRIMARY = {
        "symbol_id": "ps_1",
        "name": "ps_symbol",
        "qualified_name": "pkg::ps_symbol",
        "parser": "rust",
        "text": "P" * 2000,
    }
    KOTLIN = {
        "class_name": "BenchClass",
        "package_name": "com.bench",
        "summary": "kotlin summary",
        "text": "K" * 100,
    }

    def _backends(self):
        import fastmcp_server
        from android import android_mcp
        from cplus import cplus_mcp
        from java import java_mcp

        return [fastmcp_server, cplus_mcp, android_mcp, java_mcp]

    def test_legacy_payload_content_unchanged(self):
        for backend in self._backends():
            with self.subTest(backend=backend.__name__):
                payload = dict(self.LEGACY)
                self.assertEqual(backend._select_content(payload, "n", "auto"), "legacy summary")
                self.assertEqual(backend._select_content(payload, "n", "summary"), "legacy summary")
                self.assertEqual(backend._select_content(payload, "n", "comment"), "legacy comment")
                self.assertEqual(backend._select_content(payload, "n", "name"), "legacy_symbol")

    def test_primary_payload_falls_back_to_text_preview(self):
        for backend in self._backends():
            with self.subTest(backend=backend.__name__):
                payload = dict(self.PRIMARY)
                payload["text"] = "x" * 2000
                content = backend._select_content(payload, None, "auto")
                self.assertEqual(len(content), 401)  # 400 chars + ellipsis
                self.assertTrue(content.endswith("…"))
                # full text never leaks into content
                self.assertLess(len(content), 2000)

    def test_kotlin_payload_keeps_class_fields_and_summary(self):
        for backend in self._backends():
            with self.subTest(backend=backend.__name__):
                payload = dict(self.KOTLIN)
                content = backend._select_content(payload, None, "auto")
                self.assertEqual(content, "kotlin summary")
                # Exclude-based narrowing keeps class_name/package_name.
                self.assertIn("class_name", payload)
                self.assertIn("package_name", payload)

    def test_short_text_preview_has_no_ellipsis(self):
        payload = dict(self.PRIMARY)
        payload["text"] = "short text"
        content = self._backends()[0]._select_content(payload, None, "auto")
        self.assertEqual(content, "short text")


class LazyFetchTests(unittest.TestCase):
    def test_lazy_full_payload_groups_by_collection(self):
        store = _RecordingStore()
        hits = [
            _point("a1", 0.9, {}, collection="col_a"),
            _point("a2", 0.8, {}, collection="col_a"),
            _point("b1", 0.7, {}, collection="col_b"),
            _point("c1", 0.6, {"summary": "already has content"}, collection="col_c"),
        ]
        missing = [h for h in hits if qdrant_query_support.payload_needs_lazy_text(h)]
        self.assertEqual([h["id"] for h in missing], ["a1", "a2", "b1"])
        qdrant_query_support.lazy_full_payload(store, missing)
        # one retrieve per source collection, tagged hits only
        self.assertEqual(
            [call[0] for call in store.retrieve_calls],
            ["col_a", "col_b"],
        )
        self.assertTrue(all(call[2] for call in store.retrieve_calls))  # with_payload
        self.assertEqual(missing[0]["payload"]["text"], "full text a1")

    def test_lazy_fetch_missing_skips_name_mode(self):
        store_loader = MagicMock()
        results = {"results": [_point("a1", 0.9, {}, collection="col_a")]}
        qdrant_query_support.lazy_fetch_missing(results, "name", store_loader)
        store_loader.assert_not_called()

    def test_lazy_fetch_missing_uses_loader_only_when_needed(self):
        store = _RecordingStore()
        loader = MagicMock(return_value=store)
        results = {"results": [_point("a1", 0.9, {}, collection="col_a")]}
        qdrant_query_support.lazy_fetch_missing(results, "auto", loader)
        loader.assert_called_once()
        self.assertEqual(store.retrieve_calls[0][0], "col_a")


class LayoutCacheTests(unittest.TestCase):
    def setUp(self):
        qdrant_layout_cache.reset_cache()

    def tearDown(self):
        qdrant_layout_cache.reset_cache()

    @staticmethod
    def _loader(store):
        return lambda: store

    def _meta(self, store, url="url", col="col_a"):
        return qdrant_layout_cache.get_collection_meta(url, col, loader=lambda: store)

    def _store(self, sizes):
        store = MagicMock()
        info = MagicMock()
        info.config.params.vectors = sizes
        store.get_collection_info.return_value = info
        store.list_collection_names.return_value = ["col_a", "col_b"]
        return store

    def test_cold_then_cached_round_trips(self):
        store = self._store({"size": 1024})
        first = self._meta(store)
        second = self._meta(store)
        self.assertEqual(first, {"default": 1024})
        self.assertEqual(second, {"default": 1024})
        self.assertEqual(store.get_collection_info.call_count, 1)

    def test_named_vector_sizes_round_trip(self):
        store = self._store({"semantic": {"size": 1024}, "sparse": {"size": 8}})
        sizes = self._meta(store, col="named")
        self.assertEqual(sizes, {"semantic": 1024, "sparse": 8})

    def test_errors_are_never_cached(self):
        store = self._store({"size": 8})
        store.get_collection_info.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self._meta(store, col="col_err")
        store.get_collection_info.side_effect = None
        sizes = self._meta(store, col="col_err")
        self.assertEqual(sizes, {"default": 8})
        self.assertEqual(store.get_collection_info.call_count, 2)

    def test_ttl_expiry(self):
        store = self._store({"size": 8})
        real_monotonic = qdrant_layout_cache.time.monotonic
        clock = {"now": 1000.0}

        def fake_monotonic():
            return clock["now"]

        with patch.object(qdrant_layout_cache.time, "monotonic", fake_monotonic):
            self._meta(store)
            clock["now"] += 60.0
            self._meta(store)
            self.assertEqual(store.get_collection_info.call_count, 1)
            clock["now"] += 400.0  # past the 300s TTL
            self._meta(store)
            self.assertEqual(store.get_collection_info.call_count, 2)
        del real_monotonic

    def test_cache_disabled_via_env(self):
        store = self._store({"size": 8})
        with patch.dict(os.environ, {"MCP_COLLECTION_META_CACHE": "0"}):
            for _ in range(3):
                self._meta(store)
        self.assertEqual(store.get_collection_info.call_count, 3)

    def test_invalidate_forces_refetch(self):
        store = self._store({"size": 8})
        self._meta(store)
        self._meta(store, col="col_b")
        qdrant_layout_cache.invalidate("url")
        self._meta(store)
        self.assertEqual(store.get_collection_info.call_count, 3)

    def test_filter_collections_uses_cache_and_matches_behavior(self):
        store = self._store({"size": 8})
        selected, errors = qdrant_query_support.filter_collections_for_vector(
            store, ["col_a"], 8, "url"
        )
        self.assertEqual(selected, [("col_a", None)])
        self.assertEqual(errors, [])
        qdrant_query_support.filter_collections_for_vector(store, ["col_a"], 8, "url")
        self.assertEqual(store.get_collection_info.call_count, 1)  # cached

    def test_filter_reports_mismatch_like_legacy(self):
        store = self._store({"size": 1024})
        selected, errors = qdrant_query_support.filter_collections_for_vector(
            store, ["col_a"], 8, "url"
        )
        self.assertEqual(selected, [])
        self.assertIn("Vector size mismatch (expected 8, got 1024)", errors[0]["error"])


class LocalModeSelectorSmokeTests(unittest.TestCase):
    """Real qdrant-client local mode: the Exclude selector flows through."""

    def test_local_query_points_exclude_text(self):
        from cortex_harness.storage import LocalQdrantStore, QdrantStorageRole, resolve_storage

        with tempfile.TemporaryDirectory() as tmp:
            resolved = resolve_storage(Path(tmp), qdrant_code_path=str(Path(tmp) / "code.qdrant"))
            store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
            store.create_collection(
                "smoke_narrow",
                vectors_config=qmodels.VectorParams(size=4, distance=qmodels.Distance.COSINE),
            )
            store.upsert(
                "smoke_narrow",
                [{
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "smoke-1")),
                    "vector": [1.0, 0.0, 0.0, 0.0],
                    "payload": {"text": "T" * 5000, "summary": "keep me"},
                }],
                wait=True,
            )
            hits = qdrant_query_support.search_collection(store, "smoke_narrow", [1.0, 0, 0, 0], None, 3)
            self.assertEqual(len(hits), 1)
            self.assertNotIn("text", hits[0]["payload"])
            self.assertEqual(hits[0]["payload"]["summary"], "keep me")
            self.assertEqual(hits[0]["_collection"], "smoke_narrow")


class HnswEfEnvTests(unittest.TestCase):
    def test_unset_env_sends_no_search_params(self):
        store = _RecordingStore()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QDRANT_HNSW_EF", None)
            qdrant_query_support.search_collection(store, "c", [0.1], None, 1)
        self.assertNotIn("search_params", store.query_calls[0][1])

    def test_env_set_sends_hnsw_ef(self):
        store = _RecordingStore()
        with patch.dict(os.environ, {"QDRANT_HNSW_EF": "128"}):
            qdrant_query_support.search_collection(store, "c", [0.1], None, 1)
        params = store.query_calls[0][1]["search_params"]
        self.assertEqual(params.hnsw_ef, 128)


class ToolLevelOutputContractTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end through cplus tool_semantic_search: narrowed payload, preview content, lazy fetch."""

    def _store(self):
        from unittest.mock import MagicMock

        store = MagicMock()
        store.query_points.return_value.points = [
            {
                "id": "pt-1",
                "score": 0.9,
                "payload": {"symbol_id": "s1", "name": "nar_symbol", "parser": "rust"},
            }
        ]
        store.retrieve.return_value = [
            {
                "id": "pt-1",
                "payload": {
                    "symbol_id": "s1",
                    "name": "nar_symbol",
                    "parser": "rust",
                    "text": "T" * 2000,
                },
            }
        ]
        return store

    async def _search(self, store, **overrides):
        from cplus import cplus_mcp
        from unittest.mock import AsyncMock, patch

        tool = getattr(cplus_mcp.tool_semantic_search, "fn", cplus_mcp.tool_semantic_search)
        with patch.object(cplus_mcp, "get_code_qdrant_store", return_value=store), patch.object(
            cplus_mcp, "_embed_query", return_value=[0.1, 0.2]
        ), patch.object(
            cplus_mcp, "_resolve_base_collections", AsyncMock(return_value=(["col_a"], True))
        ), patch.object(
            cplus_mcp, "_filter_collections_for_vector", AsyncMock(return_value=([("col_a", None)], []))
        ):
            payload = {"query": "q", "collection": "col_a"}
            payload.update(overrides)
            return await tool(**payload)

    async def test_narrowed_hit_previews_text_and_drops_raw_text(self):
        result = await self._search(self._store())
        hit = result["results"][0]
        self.assertNotIn("text", hit["payload"])
        self.assertEqual(len(hit["payload"]["content"]), 401)  # 400 + ellipsis
        self.assertTrue(hit["payload"]["content"].endswith("…"))
        self.assertEqual(hit["_collection"], "col_a")

    async def test_include_raw_fields_keeps_lazily_fetched_text(self):
        store = self._store()
        result = await self._search(store, include_raw_fields=True)
        hit = result["results"][0]
        self.assertEqual(len(hit["payload"]["text"]), 2000)
        self.assertEqual(store.retrieve.call_count, 1)

    async def test_lazy_fetch_failure_degrades_without_crash(self):
        store = self._store()
        store.retrieve.side_effect = RuntimeError("remote down")
        result = await self._search(store)
        hit = result["results"][0]
        # content falls back to the name, no exception surfaced
        self.assertEqual(hit["payload"]["content"], "nar_symbol")
        self.assertNotIn("text", hit["payload"])


class PreloadAutoDeviceTests(unittest.TestCase):
    def test_preload_uses_auto_when_env_unset(self):
        import contextlib
        import io

        import fastmcp_server

        seen = {}
        buffer = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "EMBED_DEVICE"}
        with patch.dict(os.environ, env, clear=True), patch.object(
            fastmcp_server, "PRELOAD_EMBEDDER_ON_STARTUP", "1"
        ), patch.object(fastmcp_server, "DEFAULT_MODEL", "stub"), patch.object(
            embed_runtime,
            "get_embedder",
            side_effect=lambda model, device_name=None: seen.update(
                device=device_name
            )
            or (None, None, "cpu"),
        ), contextlib.redirect_stdout(buffer):
            fastmcp_server._preload_embedder_on_startup()
        self.assertEqual(seen["device"], "auto")


if __name__ == "__main__":
    unittest.main()

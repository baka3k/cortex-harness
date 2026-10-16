"""Repo-scope tests for the vector path (plan 261016 phase 06, RR5).

Covers the shared post-filter helpers in ``repo_scope_support`` plus the
two tools that consume them: ``semantic_search`` (over-fetch + Python
post-filter) and ``explore_graph`` (matched-node filter). The fake store
serves payloads with heterogeneous ``repo`` flavors.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CODE_TINY = _REPO_ROOT / "code-tiny"
_MCP_DIR = _CODE_TINY / "mcp"
for path in (_CODE_TINY, _MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from repo_scope_support import (  # noqa: E402
    REPO_POSTFETCH_FACTOR,
    filter_vector_hits_by_repo,
    vector_repo_scope_result,
)
from tools.common.project_scope import matches_repo_scope  # noqa: E402


def _hit(node_id: str, repo, score: float = 0.9) -> dict:
    payload = {"symbol_id": node_id, "name": f"sym_{node_id}"}
    if repo is not None:
        payload["repo"] = repo
    return {"id": node_id, "score": score, "payload": payload}


class FilterVectorHitsTests(unittest.TestCase):
    def test_unscoped_passthrough(self) -> None:
        items = [_hit("a", "proj/s3"), _hit("b", "/Users/x/xs3")]
        kept, pre = filter_vector_hits_by_repo(items, None, False)
        self.assertEqual(kept, items)
        self.assertEqual(pre, 2)

    def test_scoped_keeps_both_flavors_drops_xs3_and_empty(self) -> None:
        items = [
            _hit("a", "/Users/x/s3"),
            _hit("b", "proj/s3"),
            _hit("c", "proj/xs3"),
            _hit("d", ""),
            _hit("e", None),
        ]
        kept, pre = filter_vector_hits_by_repo(items, "s3", True)
        self.assertEqual([item["id"] for item in kept], ["a", "b"])
        self.assertEqual(pre, 5)

    def test_matches_repo_scope_rule_still_shared(self) -> None:
        self.assertTrue(matches_repo_scope("/Users/x/s3", "s3"))
        self.assertFalse(matches_repo_scope("proj/xs3", "s3"))


class VectorScopeResultTests(unittest.TestCase):
    def test_unscoped_has_no_warnings(self) -> None:
        scope, warnings = vector_repo_scope_result(None, False, 5, 5, 40)
        self.assertEqual(scope["filter_applied"], False)
        self.assertEqual(warnings, [])
        self.assertEqual(scope["warnings"], [])

    def test_zero_post_hits_warns(self) -> None:
        scope, warnings = vector_repo_scope_result("xs3", True, 40, 0, 40)
        self.assertEqual(scope["pre_filter_hits"], 40)
        self.assertEqual(scope["post_filter_hits"], 0)
        self.assertEqual(warnings, ["repo 'xs3' matched 0 of 40 vector hits"])

    def test_exhausted_overfetch_warns(self) -> None:
        scope, warnings = vector_repo_scope_result("s3", True, 40, 3, 40)
        self.assertTrue(any("fetch window was exhausted" in w for w in warnings))

    def test_partial_window_is_quiet(self) -> None:
        scope, warnings = vector_repo_scope_result("s3", True, 12, 3, 40)
        self.assertEqual(warnings, [])


class SemanticSearchRepoScopeTest(unittest.IsolatedAsyncioTestCase):
    """Drive the fastmcp semantic_search with a fake Qdrant merge layer."""

    def setUp(self) -> None:
        import fastmcp_server

        self.fastmcp = fastmcp_server
        self.merged: list[dict] = []
        self.fetch_capture: dict = {}

        def _fake_merge(collections, vector, top_k, qdrant_url, project_id, embedding_model=None):
            self.fetch_capture["top_k"] = top_k
            return list(self.merged), []

        self._patches = [
            patch.object(fastmcp_server, "_merge_qdrant_results", _fake_merge),
            patch.object(fastmcp_server, "_resolve_base_collections", AsyncMock(return_value=([("c", "n")], True))),
            patch.object(fastmcp_server, "_filter_collections_for_vector", AsyncMock(return_value=([("c", "n")], []))),
            patch.object(fastmcp_server, "_embed_query", lambda query, model: [0.0]),
            patch.object(fastmcp_server, "_lazy_fetch_missing_payload_text", lambda results, mode: None),
            patch.dict("os.environ", {"SEMANTIC_SEARCH_TIMING": ""}),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_scoped_overfetches_and_post_filters(self) -> None:
        self.merged = [_hit("a", "/Users/x/s3"), _hit("b", "proj/xs3"), _hit("c", "proj/s3")]
        result = await self.fastmcp.tool_semantic_search(query="pay", top_k=5, repo="s3")
        self.assertEqual(self.fetch_capture["top_k"], 5 * REPO_POSTFETCH_FACTOR)
        self.assertEqual([item["id"] for item in result["results"]], ["a", "c"])
        self.assertEqual(result["scope"]["pre_filter_hits"], 3)
        self.assertEqual(result["scope"]["post_filter_hits"], 2)
        self.assertEqual(result["scope"]["filter_applied"], True)
        self.assertEqual(result["warnings"], [])

    async def test_unscoped_unchanged_fetch(self) -> None:
        self.merged = [_hit("a", "/Users/x/s3")]
        result = await self.fastmcp.tool_semantic_search(query="pay", top_k=5, repo=None)
        self.assertEqual(self.fetch_capture["top_k"], 5)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(
            result["scope"],
            {
                "repo": None,
                "filter_applied": False,
                "pre_filter_hits": 1,
                "post_filter_hits": 1,
                "warnings": [],
            },
        )
        self.assertEqual(result["scope"]["warnings"], result["warnings"])

    async def test_zero_hits_after_filter_warns(self) -> None:
        self.merged = [_hit("b", "proj/xs3")]
        result = await self.fastmcp.tool_semantic_search(query="pay", top_k=2, repo="s3")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["warnings"], ["repo 's3' matched 0 of 1 vector hits"])


def _explore_node(node_id: str, repo, entry: bool = False) -> dict:
    node = {
        "node_id": node_id,
        "name": f"sym_{node_id}",
        "qualified_name": f"mod::{node_id}",
        "score": 0.9,
        "properties": {},
    }
    if repo is not None:
        node["properties"]["repo"] = repo
    if entry:
        node["is_entry_point"] = True
    return node


class ExploreGraphRepoScopeTest(unittest.IsolatedAsyncioTestCase):
    """Drive the unified explore wrapper with a fake service (RR5)."""

    def setUp(self) -> None:
        import unified_mcp

        self.unified = unified_mcp
        self.explore_capture: dict = {}
        self.fake_response: dict = {}

        outer = self

        class _FakeService:
            async def explore(self, *, query, top_k, **kwargs):
                outer.explore_capture["top_k"] = top_k
                return {
                    "matched_nodes": list(outer.fake_response.get("matched_nodes", [])),
                    "entry_points": list(outer.fake_response.get("entry_points", [])),
                    "related_paths": [],
                    "explanation": "fake",
                    "confidence": 0.5,
                    "query_analysis": {},
                    "mode": "hybrid",
                }

        patcher = patch(
            "services.explore_service.get_explore_service",
            return_value=_FakeService(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_scoped_explore_filters_nodes_and_entry_points(self) -> None:
        self.fake_response = {
            "matched_nodes": [
                _explore_node("a", "proj/s3"),
                _explore_node("b", "proj/xs3"),
                _explore_node("c", "/Users/x/s3"),
            ],
            "entry_points": [_explore_node("a", "proj/s3"), _explore_node("b", "proj/xs3")],
        }
        result = await self.unified.tool_explore_graph(query="payment flow", top_k=10, repo="s3")
        self.assertEqual(self.explore_capture["top_k"], 10 * REPO_POSTFETCH_FACTOR)
        self.assertEqual([n["node_id"] for n in result["matched_nodes"]], ["a", "c"])
        self.assertEqual([n["node_id"] for n in result["entry_points"]], ["a"])
        self.assertEqual(result["scope"]["pre_filter_hits"], 3)
        self.assertEqual(result["scope"]["post_filter_hits"], 2)
        self.assertEqual(result["scope"]["warnings"], result["warnings"])

    async def test_scoped_explore_overfetch_capped_at_service_ceiling(self) -> None:
        self.fake_response = {"matched_nodes": [], "entry_points": []}
        await self.unified.tool_explore_graph(query="pay", top_k=50, repo="s3")
        self.assertEqual(self.explore_capture["top_k"], 100)

    async def test_unscoped_explore_passthrough_scope(self) -> None:
        self.fake_response = {
            "matched_nodes": [_explore_node("a", "proj/s3")],
            "entry_points": [],
        }
        result = await self.unified.tool_explore_graph(query="pay", top_k=10, repo=None)
        self.assertEqual(self.explore_capture["top_k"], 10)
        self.assertEqual(len(result["matched_nodes"]), 1)
        self.assertEqual(
            result["scope"],
            {
                "repo": None,
                "filter_applied": False,
                "pre_filter_hits": 1,
                "post_filter_hits": 1,
                "warnings": [],
            },
        )


if __name__ == "__main__":
    unittest.main()

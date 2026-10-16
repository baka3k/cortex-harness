"""Repo-scope tests for the fastmcp backend (plan 261016 phase 02).

Covers RR1 (unscoped no-op), RR2/RR3 (anchor filter, exact-or-suffix),
the scope block + warnings mirror, and the template checklist: every query
template that carries a ``$project_id IS NULL`` predicate also carries the
shared ``repo_scope_predicate`` fragment unless the tool is in the
documented exclusion set.
"""

from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CODE_TINY = _REPO_ROOT / "code-tiny"
_MCP_DIR = _CODE_TINY / "mcp"
for path in (_CODE_TINY, _MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import fastmcp_server  # noqa: E402
from tools.common.project_scope import matches_repo_scope  # noqa: E402


def _node(node_id: str, repo: str | None) -> dict:
    node = {"id": node_id, "labels": ["Function"], "name": f"sym_{node_id}"}
    if repo is not None:
        node["repo"] = repo
    return node


class _CapturingFastBackend:
    """Patch points that capture ``(query, params)`` and serve canned rows."""

    def __init__(self) -> None:
        self.cypher_calls: list[tuple[str, dict]] = []
        self.cypher_rows: list[dict] = []
        self.nodes_by_id: dict[str, dict] = {}

    @property
    def main_calls(self) -> list[tuple[str, dict]]:
        """Cypher calls minus the empty-branch DISTINCT-repo helper query."""
        return [
            (query, params)
            for query, params in self.cypher_calls
            if "DISTINCT n.repo" not in query
        ]

    @property
    def main_call(self) -> tuple[str, dict]:
        assert self.main_calls, "no primary cypher call captured"
        return self.main_calls[-1]

    async def run_cypher(self, query: str, params: dict, db: str):
        self.cypher_calls.append((query, dict(params)))
        if "DISTINCT n.repo" in query:
            return []
        return list(self.cypher_rows)

    async def list_databases(self):
        return []

    def driver(self):
        stub = SimpleNamespace(
            find_node_by_id=AsyncMock(
                side_effect=lambda node_id, **kw: self.nodes_by_id.get(str(node_id))
            ),
            find_nodes_by_idss=AsyncMock(),
            query_function_subgraph=AsyncMock(return_value=[]),
            find_function_paths=AsyncMock(return_value=[]),
        )

        async def find_nodes_by_ids(ids, **kwargs):
            return [self.nodes_by_id[i] for i in ids if i in self.nodes_by_id]

        stub.find_nodes_by_ids = AsyncMock(side_effect=find_nodes_by_ids)
        return stub


class FastMCPRepoScopeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.harness = _CapturingFastBackend()
        self.stub = self.harness.driver()
        self._patches = [
            unittest.mock.patch.object(fastmcp_server, "_run_cypher", self.harness.run_cypher),
            unittest.mock.patch.object(fastmcp_server, "_list_databases", self.harness.list_databases),
            unittest.mock.patch.object(fastmcp_server, "_get_graph_driver", AsyncMock(return_value=self.stub)),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    # ── RR1: unscoped queries stay no-op ────────────────────────────────

    async def test_unscoped_repo_binds_empty_string_noop(self) -> None:
        await fastmcp_server.tool_search_by_code(query="payment", project_id=None, repo=None)
        query, params = self.harness.main_call
        self.assertIn("$repo = ''", query)
        self.assertEqual(params.get("repo"), "")
        self.assertEqual(params.get("repo_suffix"), "")

    async def test_unscoped_query_structure_unchanged(self) -> None:
        # RR1: with no repo the predicate is present but inert — the row
        # flow must be identical to the pre-repo template (same RETURN/LIMIT
        # suffix, no extra MATCH).
        await fastmcp_server.tool_listup_symbols_matching_file_path(
            modules=["svc"], repo=None
        )
        query, _ = self.harness.main_call
        self.assertTrue(query.strip().endswith("RETURN n"))

    # ── RR2/RR3: scoped filter reaches the Cypher params ────────────────

    async def test_scoped_repo_injects_suffix_param(self) -> None:
        await fastmcp_server.tool_search_by_code(query="payment", repo="s3")
        query, params = self.harness.main_call
        self.assertIn("$repo = ''", query)
        self.assertEqual(params["repo"], "s3")
        self.assertEqual(params["repo_suffix"], "/s3")

    async def test_scoped_repo_covers_both_template_variants(self) -> None:
        # search_by_code tries the fulltext template first and the contains
        # fallback second — BOTH must carry the repo predicate (param-drop
        # trap: a template without it leaves the filter dangling).
        await fastmcp_server.tool_search_by_code(query="payment", repo="s3")
        queries = [q for q, _ in self.harness.main_calls]
        self.assertTrue(queries, "expected at least one cypher call")
        for query in queries:
            self.assertIn("$repo = ''", query)

    # ── Anchor rules ────────────────────────────────────────────────────

    async def test_find_path_between_module_filters_source_side_only(self) -> None:
        # plan.md RT#1: the s/t template filters only the traversal origin;
        # the far endpoint stays free so cross-repo targets remain visible.
        await fastmcp_server.tool_find_path_between_module(
            source_modules=["a"], target_modules=["b"], repo="s3"
        )
        query, params = self.harness.main_call
        self.assertEqual(params["repo"], "s3")
        self.assertIn("s.repo = $repo", query)
        self.assertIn("s.repo ENDS WITH $repo_suffix", query)
        self.assertNotIn("t.repo = $repo", query)
        self.assertNotIn("t.repo ENDS WITH", query)

    async def test_list_up_entrypoint_filters_result_functions_only(self) -> None:
        await fastmcp_server.tool_list_up_entrypoint(modules=["m1"], repo="s3")
        query, _ = self.harness.main_call
        self.assertIn("f.repo = $repo", query)
        self.assertNotIn("caller.repo", query)

    async def test_plan_file_dependency_filters_caller_side_only(self) -> None:
        await fastmcp_server.tool_plan_file_dependency_order(modules=["m1"], repo="s3")
        query, params = self.harness.main_call
        self.assertEqual(params["repo_suffix"], "/s3")
        self.assertIn("caller.repo = $repo", query)
        self.assertNotIn("callee.repo", query)

    # ── Scope block + warnings mirror ───────────────────────────────────

    async def test_scope_block_present_when_scoped_with_results(self) -> None:
        self.harness.cypher_rows = [{"n": _node("n1", "proj/s3")}]
        result = await fastmcp_server.tool_listup_symbols_matching_file_path(
            modules=["svc"], repo="s3"
        )
        self.assertEqual(result["scope"], {"repo": "s3", "filter_applied": True, "warnings": []})
        self.assertEqual(result["warnings"], [])

    async def test_scope_block_present_when_unscoped(self) -> None:
        self.harness.cypher_rows = [{"n": _node("n1", "proj/s3")}]
        result = await fastmcp_server.tool_listup_symbols_matching_file_path(
            modules=["svc"], repo=None
        )
        self.assertEqual(
            result["scope"],
            {"repo": None, "filter_applied": False, "warnings": []},
        )

    async def test_blank_repo_is_unscoped(self) -> None:
        result = await fastmcp_server.tool_listup_symbols_matching_file_path(
            modules=["svc"], repo="   "
        )
        self.assertEqual(
            result["scope"],
            {"repo": None, "filter_applied": False, "warnings": []},
        )

    async def test_empty_scoped_result_warns_with_known_repos(self) -> None:
        # First call (the search) returns nothing; the follow-up DISTINCT
        # repo query returns the actionable alternatives.
        async def run_cypher(query: str, params: dict, db: str):
            self.harness.cypher_calls.append((query, dict(params)))
            if "DISTINCT n.repo" in query:
                return [{"repo": "/Users/x/s3"}, {"repo": "projA/s1"}]
            return []

        with unittest.mock.patch.object(fastmcp_server, "_run_cypher", run_cypher):
            result = await fastmcp_server.tool_search_by_code(query="ghost", repo="xs3")
        self.assertEqual(result["results"], [])
        self.assertTrue(result["scope"]["filter_applied"])
        self.assertEqual(
            result["warnings"],
            ["repo 'xs3' matched 0 nodes. Known repo values: /Users/x/s3, projA/s1"],
        )
        self.assertEqual(result["scope"]["warnings"], result["warnings"])

    async def test_scoped_result_silences_warning(self) -> None:
        self.harness.cypher_rows = [{"n": _node("n1", "proj/s3")}]
        result = await fastmcp_server.tool_search_by_code(query="payment", repo="s3")
        self.assertEqual(result["warnings"], [])

    # ── Anchor post-filters for driver-based tools ──────────────────────

    async def test_get_node_details_filters_by_repo_suffix(self) -> None:
        self.harness.nodes_by_id = {
            "a": _node("a", "/Users/x/s3"),
            "b": _node("b", "projA/xs3"),
            "c": _node("c", None),
        }
        result = await fastmcp_server.tool_get_node_details(
            node_ids=["a", "b", "c"], repo="s3"
        )
        self.assertEqual([node["id"] for node in result["nodes"]], ["a"])
        self.assertEqual(result["scope"]["filter_applied"], True)

    async def test_get_symbol_repo_mismatch_reports_miss(self) -> None:
        self.harness.nodes_by_id = {"a": _node("a", "projA/s1")}
        result = await fastmcp_server.tool_get_symbol(node_id="a", repo="s3")
        self.assertIsNone(result["node"])
        self.assertEqual(result["scope"]["filter_applied"], True)

    async def test_get_symbol_repo_match_returns_node(self) -> None:
        self.harness.nodes_by_id = {"a": _node("a", "projA/s3")}
        result = await fastmcp_server.tool_get_symbol(node_id="a", repo="s3")
        self.assertIsNotNone(result["node"])
        self.assertEqual(result["warnings"], [])

    def _subgraph_path(self, anchor: dict, neighbor: dict) -> dict:
        return {
            "nodes": [dict(anchor), dict(neighbor)],
            "edges": [{"id": "r1", "type": "CALLS", "start": anchor["id"], "end": neighbor["id"]}],
        }

    async def test_query_subgraph_checks_anchor_not_neighbors(self) -> None:
        anchor = _node("a", "proj/s3")
        neighbor = _node("b", "/Other/repo")
        stub = self.stub

        async def query_function_subgraph(*, function_id, **kwargs):
            return [self._subgraph_path(anchor, neighbor)]

        stub.query_function_subgraph = AsyncMock(side_effect=query_function_subgraph)
        result = await fastmcp_server.tool_query_subgraph(function_id="a", repo="s3")
        # The cross-repo CALLS neighbor stays in the graph (RR2).
        self.assertEqual({node["id"] for node in result["nodes"]}, {"a", "b"})
        self.assertEqual(result["scope"]["repo"], "s3")

    async def test_query_subgraph_anchor_mismatch_is_empty(self) -> None:
        anchor = _node("a", "proj/s1")
        neighbor = _node("b", "proj/s1")
        stub = self.stub

        async def query_function_subgraph(*, function_id, **kwargs):
            return [self._subgraph_path(anchor, neighbor)]

        stub.query_function_subgraph = AsyncMock(side_effect=query_function_subgraph)
        result = await fastmcp_server.tool_query_subgraph(function_id="a", repo="s3")
        self.assertEqual(result["reason"], "no_subgraph")
        self.assertTrue(result["scope"]["filter_applied"])

    async def test_find_paths_checks_both_endpoint_anchors(self) -> None:
        start = _node("a", "proj/s3")
        end = _node("b", "/Users/x/s3")

        async def find_function_paths(*, start_id, end_id, **kwargs):
            return [{"nodes": [dict(start), dict(end)], "edges": []}]

        stub = self.stub
        stub.find_function_paths = AsyncMock(side_effect=find_function_paths)
        result = await fastmcp_server.tool_find_paths(
            start_function_id="a", end_function_id="b", repo="s3"
        )
        self.assertEqual({node["id"] for node in result["nodes"]}, {"a", "b"})

        async def find_foreign_paths(*, start_id, end_id, **kwargs):
            return [{"nodes": [dict(start), _node("z", "other/repo")], "edges": []}]

        stub.find_function_paths = AsyncMock(side_effect=find_foreign_paths)
        result = await fastmcp_server.tool_find_paths(
            start_function_id="a", end_function_id="z", repo="s3"
        )
        self.assertEqual(result["reason"], "no_path_found")


# ── Template checklist (red-team #1: no dangling filter) ────────────────

_TOOL_BLOCKS_RE = re.compile(
    r"@mcp_server\.tool\(\s*name=\"(?P<name>[a-z_]+)\".*?(?=\n@mcp_server\.tool|\Z)",
    re.DOTALL,
)

# Tools whose project_id predicate intentionally has NO repo counterpart:
# annotate_node is a write (R6 mirror); list_workflows/search_workflows
# match :Workflow nodes which carry no repo property; get_workflow_steps is
# workflow_id-addressed; semantic_search is the vector path (phase 06);
# the rest are metadata-only tools with no project predicate at all.
_REPO_EXCLUDED_TOOLS = {
    "annotate_node",
    "list_workflows",
    "search_workflows",
    "get_workflow_steps",
    "list_databases",
    "list_qdrant_collections",
    "list_parsers",
    "list_mcp_functions",
    "compute_scc",
    "topological_sort",
    "plan_dependency_order",
}


class FastMCPTemplateChecklistTest(unittest.TestCase):
    """Every project-scoped query template carries the repo predicate."""

    def setUp(self) -> None:
        self.source = Path(fastmcp_server.__file__).read_text()
        self.blocks = {
            match.group("name"): match.group(0)
            for match in _TOOL_BLOCKS_RE.finditer(self.source)
        }

    def test_project_scoped_templates_have_repo_predicate(self) -> None:
        missing = []
        for name, block in self.blocks.items():
            if name in _REPO_EXCLUDED_TOOLS:
                continue
            if "$project_id IS NULL" in block and "repo_scope_predicate(" not in block:
                missing.append(name)
        self.assertEqual(missing, [])

    def test_excluded_tools_do_not_expose_repo(self) -> None:
        for name in _REPO_EXCLUDED_TOOLS:
            block = self.blocks.get(name)
            if block is None:
                continue
            self.assertNotIn("repo: Optional[str] = None", block, name)

    def test_known_query_tools_expose_repo(self) -> None:
        expected = {
            "get_symbol",
            "get_node_details",
            "query_subgraph",
            "find_paths",
            "find_path_between_module",
            "plan_file_dependency_order",
            "plan_function_dependency_order",
            "listup_symbols_matching_file_path",
            "listup_class_matching_path",
            "list_up_entrypoint",
            "search_functions",
            "search_by_code",
        }
        for name in expected:
            self.assertIn(name, self.blocks, name)
            self.assertIn("repo: Optional[str] = None", self.blocks[name], name)

    def test_matches_repo_scope_shared_semantics(self) -> None:
        # The post-filter helpers delegate to the phase-01 helper.
        self.assertTrue(matches_repo_scope("/Users/x/s3", "s3"))
        self.assertFalse(matches_repo_scope("projA/xs3", "s3"))


if __name__ == "__main__":
    unittest.main()

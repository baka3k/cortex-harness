"""Repo-scope tests for the cplus backend (plan 261016 phase 03).

Covers RR1/RR2/RR3 on a representative fan-out search tool, the 2-alias
trace templates (anchor-side only), the wrapper param injection through
``_run_cypher_first``, and the template checklist against the source.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MCP_DIR = _REPO_ROOT / "code-tiny" / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


if "falkordb_discovery" not in sys.modules:
    sys.modules["falkordb_discovery"] = _load(
        "falkordb_discovery_under_test", _MCP_DIR / "falkordb_discovery.py"
    )

cplus_mcp = _load("cplus_mcp_repo_scope_under_test", _MCP_DIR / "cplus" / "cplus_mcp.py")


class _Harness:
    def __init__(self) -> None:
        self.cypher_calls: list[tuple[str, dict]] = []
        self.rows: list[dict] = []
        self.nodes_by_id: dict[str, dict] = {}

    @property
    def main_calls(self) -> list[tuple[str, dict]]:
        return [
            (query, params)
            for query, params in self.cypher_calls
            if "DISTINCT n.repo" not in query
            and "CALL db.relationshipTypes()" not in query
        ]

    @property
    def main_call(self) -> tuple[str, dict]:
        assert self.main_calls, "no primary cypher call captured"
        return self.main_calls[-1]

    async def run_cypher(self, query: str, params: dict, db: str):
        self.cypher_calls.append((query, dict(params)))
        if "DISTINCT n.repo" in query:
            return []
        return list(self.rows)

    async def list_databases(self):
        return []

    def driver(self):
        from types import SimpleNamespace

        async def find_nodes_by_ids(ids, **kwargs):
            return [self.nodes_by_id[i] for i in ids if i in self.nodes_by_id]

        return SimpleNamespace(
            find_node_by_id=AsyncMock(
                side_effect=lambda node_id, **kw: self.nodes_by_id.get(str(node_id))
            ),
            find_nodes_by_ids=AsyncMock(side_effect=find_nodes_by_ids),
            query_function_subgraph=AsyncMock(return_value=[]),
            find_function_paths=AsyncMock(return_value=[]),
            find_paths_between_modules=AsyncMock(return_value=[]),
            list_possible_calls=AsyncMock(return_value=([], [])),
        )


class CPlusRepoScopeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.h = _Harness()
        self.stub = self.h.driver()
        for target, replacement in (
            ("_run_cypher", self.h.run_cypher),
            ("_list_databases", self.h.list_databases),
        ):
            patcher = unittest.mock.patch.object(cplus_mcp, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        driver_patch = unittest.mock.patch.object(
            cplus_mcp, "_get_graph_driver", AsyncMock(return_value=self.stub)
        )
        driver_patch.start()
        self.addCleanup(driver_patch.stop)

    async def test_unscoped_search_is_noop(self) -> None:
        self.h.rows = [{"n": {"id": "n1", "labels": ["Function"], "name": "x"}, "labels": ["Function"]}]
        result = await cplus_mcp.tool_search_functions(query="pay", repo=None)
        self.assertEqual(
            result["scope"],
            {"repo": None, "filter_applied": False, "warnings": []},
        )
        _, params = self.h.main_call
        self.assertEqual(params.get("repo"), "")
        self.assertEqual(params.get("repo_suffix"), "")

    async def test_scoped_search_injects_suffix(self) -> None:
        await cplus_mcp.tool_search_functions(query="pay", repo="s3")
        query, params = self.h.main_call
        self.assertEqual(params["repo"], "s3")
        self.assertEqual(params["repo_suffix"], "/s3")
        self.assertIn("$repo = ''", query)

    async def test_scoped_search_covers_both_templates(self) -> None:
        await cplus_mcp.tool_search_functions(query="pay", repo="s3")
        for query, _ in self.h.main_calls:
            self.assertIn("$repo = ''", query)

    async def test_search_by_code_fallback_and_fulltext_have_predicate(self) -> None:
        await cplus_mcp.tool_search_by_code(query="payment logic", repo="s3")
        for query, params in self.h.main_calls:
            self.assertIn("$repo = ''", query)
            self.assertEqual(params["repo_suffix"], "/s3")

    async def test_trace_flow_filters_both_addressed_endpoints(self) -> None:
        await cplus_mcp.tool_trace_flow(start_id="a", end_id="b", repo="s3")
        query, params = self.h.main_call
        self.assertEqual(params["repo"], "s3")
        self.assertIn("a.repo = $repo", query)
        self.assertIn("b.repo = $repo", query)

    async def test_trace_flow_single_anchor_filters_origin_only(self) -> None:
        await cplus_mcp.tool_trace_flow(start_id="a", repo="s3")
        query, _ = self.h.main_call
        self.assertIn("a.repo = $repo", query)
        self.assertNotIn("n.repo = $repo", query)

    async def test_trace_flow_between_module_filters_source_side(self) -> None:
        await cplus_mcp.tool_trace_flow_between_module(
            source_modules=["src/a"], target_modules=["src/b"], repo="s3"
        )
        for query, params in self.h.main_calls:
            self.assertIn("s.repo = $repo", query)
            self.assertNotIn("t.repo = $repo", query)
            self.assertEqual(params["repo_suffix"], "/s3")

    async def test_listup_symbols_scoped_and_unscoped(self) -> None:
        self.h.rows = [
            {"n": {"id": "n1", "labels": ["Function"], "name": "x", "repo": "proj/s3"}}
        ]
        result = await cplus_mcp.tool_listup_symbols_matching_file_path(
            modules=["svc"], repo="s3"
        )
        self.assertEqual(result["scope"]["filter_applied"], True)
        self.assertEqual(result["warnings"], [])
        _, params = self.h.main_call
        self.assertEqual(params["repo_suffix"], "/s3")

    async def test_empty_scoped_search_warns_with_known_repos(self) -> None:
        async def run_cypher(query: str, params: dict, db: str):
            self.h.cypher_calls.append((query, dict(params)))
            if "DISTINCT n.repo" in query:
                return [{"repo": "projA/s3"}, {"repo": "/Users/x/s1"}]
            return []

        with unittest.mock.patch.object(cplus_mcp, "_run_cypher", run_cypher):
            result = await cplus_mcp.tool_search_by_code(query="ghost", repo="xs3")
        self.assertEqual(result["results"], [])
        self.assertEqual(
            result["warnings"],
            ["repo 'xs3' matched 0 nodes. Known repo values: projA/s3, /Users/x/s1"],
        )

    async def test_get_node_details_post_filters_by_repo(self) -> None:
        self.h.nodes_by_id = {
            "a": {"id": "a", "labels": ["Function"], "name": "a", "repo": "/Users/x/s3"},
            "b": {"id": "b", "labels": ["Function"], "name": "b", "repo": "proj/xs3"},
        }
        result = await cplus_mcp.tool_get_node_details(node_ids=["a", "b"], repo="s3")
        self.assertEqual([node["id"] for node in result["nodes"]], ["a"])

    async def test_get_symbol_mismatch_reports_miss(self) -> None:
        self.h.nodes_by_id = {"a": {"id": "a", "labels": ["Function"], "name": "a", "repo": "proj/s1"}}
        result = await cplus_mcp.tool_get_symbol(node_id="a", repo="s3")
        self.assertIsNone(result["node"])
        self.assertTrue(result["scope"]["filter_applied"])

    async def test_find_paths_endpoint_check_via_driver_repo_kwarg(self) -> None:
        # cplus find_paths delegates to the driver method; the repo scope
        # rides the repo kwarg (driver-side predicate, phase 03 decision).
        captured: dict = {}

        async def find_function_paths(**kwargs):
            captured.update(kwargs)
            return []

        self.stub.find_function_paths = AsyncMock(side_effect=find_function_paths)
        result = await cplus_mcp.tool_find_paths(
            start_function_id="a", end_function_id="b", repo="s3"
        )
        self.assertEqual(captured.get("repo"), "s3")
        self.assertEqual(result["reason"], "no_path_found")
        self.assertTrue(result["scope"]["filter_applied"])

    async def test_get_ipc_message_predicate_and_passthrough(self) -> None:
        await cplus_mcp.tool_get_ipc_message(sender="svc", repo="s3")
        query, params = self.h.main_call
        self.assertIn("$repo = ''", query)
        self.assertIn("m.repo ENDS WITH $repo_suffix", query)
        self.assertEqual(params["repo"], "s3")


# ── Template checklist ───────────────────────────────────────────────────

_TOOL_BLOCKS_RE = re.compile(
    r"@mcp_server\.tool\(\s*name=\"(?P<name>[a-z_]+)\".*?(?=\n@mcp_server\.tool|\Z)",
    re.DOTALL,
)

# Message nodes carry repo (message_scan.py), so get_ipc_message IS scoped
# — it just cannot carry a scope block because the tool returns a bare
# list. find_screen_workflows is deferred: its queries live in
# tools/ts/workflow_finder.py (outside this plan's file list). The rest
# are write/metadata/special-label tools without a repo-able anchor.
_REPO_EXCLUDED_TOOLS = {
    "annotate_node",
    "list_databases",
    "list_qdrant_collections",
    "list_mcp_functions",
    "list_parsers",
    "find_screen_workflows",
}


class CPlusTemplateChecklistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Path(cplus_mcp.__file__).read_text()
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


if __name__ == "__main__":
    unittest.main()

"""Repo-scope tests for the android + java backends (plan 261016 phase 04).

Representative coverage per backend (search + one relationship tool + one
id-addressed post-filter) plus the template checklist. The two files
duplicated the query templates historically, so counts are asserted
independently per file.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
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

android_mcp = _load("android_mcp_repo_scope_under_test", _MCP_DIR / "android" / "android_mcp.py")
java_mcp = _load("java_mcp_repo_scope_under_test", _MCP_DIR / "java" / "java_mcp.py")


def _row(node_id: str, repo: str | None) -> dict:
    node = {"id": node_id, "labels": ["Function"], "name": f"sym_{node_id}"}
    if repo is not None:
        node["repo"] = repo
    return {"n": node}


class _Harness:
    def __init__(self, module) -> None:
        self.module = module
        self.cypher_calls: list[tuple[str, dict]] = []
        self.rows: list[dict] = []
        self.nodes_by_id: dict[str, dict] = {}
        self.stub = SimpleNamespace(
            find_node_by_id=AsyncMock(
                side_effect=lambda node_id, **kw: self.nodes_by_id.get(str(node_id))
            ),
            list_possible_calls=AsyncMock(return_value=([], [])),
        )

        async def find_nodes_by_ids(ids, **kwargs):
            return [self.nodes_by_id[i] for i in ids if i in self.nodes_by_id]

        self.stub.find_nodes_by_ids = AsyncMock(side_effect=find_nodes_by_ids)

    @property
    def main_call(self) -> tuple[str, dict]:
        mains = [
            (query, params)
            for query, params in self.cypher_calls
            if "DISTINCT n.repo" not in query
        ]
        assert mains, "no primary cypher call captured"
        return mains[-1]

    async def run_cypher(self, query: str, params: dict, db: str):
        self.cypher_calls.append((query, dict(params)))
        if "DISTINCT n.repo" in query:
            return []
        return list(self.rows)

    async def list_databases(self):
        return []


class _BackendCase(unittest.IsolatedAsyncioTestCase):
    module = None

    def setUp(self) -> None:
        self.h = _Harness(self.module)
        for target, replacement in (
            ("_run_cypher", self.h.run_cypher),
            ("_list_databases", self.h.list_databases),
        ):
            patcher = unittest.mock.patch.object(self.module, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        driver_patch = unittest.mock.patch.object(
            self.module, "_get_graph_driver", AsyncMock(return_value=self.h.stub)
        )
        driver_patch.start()
        self.addCleanup(driver_patch.stop)


class AndroidRepoScopeTest(_BackendCase):
    module = android_mcp

    async def test_search_functions_scoped_and_unscoped(self) -> None:
        self.h.rows = [_row("n1", "proj/s3")]
        scoped = await android_mcp.tool_search_functions(query="pay", repo="s3")
        self.assertEqual(scoped["scope"]["filter_applied"], True)
        self.assertEqual(scoped["warnings"], [])
        query, params = self.h.main_call
        self.assertEqual(params["repo_suffix"], "/s3")
        self.assertIn("$repo = ''", query)

        unscoped = await android_mcp.tool_search_functions(query="pay", repo=None)
        self.assertEqual(
            unscoped["scope"],
            {"repo": None, "filter_applied": False, "warnings": []},
        )

    async def test_search_by_code_covers_both_templates(self) -> None:
        await android_mcp.tool_search_by_code(query="payment", repo="s3")
        for query, params in self.h.cypher_calls:
            if "DISTINCT n.repo" in query:
                continue
            self.assertIn("$repo = ''", query)
            self.assertEqual(params["repo_suffix"], "/s3")

    async def test_list_possible_calls_filters_caller_side(self) -> None:
        await android_mcp.tool_list_possible_calls(repo="s3")
        query, params = self.h.main_call
        self.assertIn("a.repo = $repo", query)
        self.assertNotIn("b.repo = $repo", query)
        self.assertEqual(params["repo"], "s3")

    async def test_find_paths_filters_both_endpoints(self) -> None:
        await android_mcp.tool_find_paths(start_function_id="a", end_function_id="b", repo="s3")
        query, _ = self.h.main_call
        self.assertIn("a.repo = $repo", query)
        self.assertIn("b.repo = $repo", query)

    async def test_trace_flow_between_module_filters_source_side(self) -> None:
        await android_mcp.tool_trace_flow_between_module(
            source_modules=["src/a"], target_modules=["src/b"], repo="s3"
        )
        for query, params in self.h.cypher_calls:
            if "DISTINCT n.repo" in query or "CALL db.relationshipTypes()" in query:
                continue
            self.assertIn("s.repo = $repo", query)
            self.assertNotIn("t.repo = $repo", query)
            self.assertEqual(params["repo_suffix"], "/s3")

    async def test_get_symbol_post_filters_anchor(self) -> None:
        self.h.nodes_by_id = {"a": {"id": "a", "labels": ["Function"], "name": "a", "repo": "proj/s1"}}
        result = await android_mcp.tool_get_symbol(node_id="a", repo="s3")
        self.assertIsNone(result["node"])
        self.assertTrue(result["scope"]["filter_applied"])

        self.h.nodes_by_id = {"b": {"id": "b", "labels": ["Function"], "name": "b", "repo": "/Users/x/s3"}}
        result = await android_mcp.tool_get_symbol(node_id="b", repo="s3")
        self.assertIsNotNone(result["node"])

    async def test_get_ipc_message_predicate(self) -> None:
        await android_mcp.tool_get_ipc_message(sender="svc", repo="s3")
        query, params = self.h.main_call
        self.assertIn("m.repo ENDS WITH $repo_suffix", query)
        self.assertEqual(params["repo"], "s3")

    async def test_empty_scoped_result_warns(self) -> None:
        async def run_cypher(query: str, params: dict, db: str):
            self.h.cypher_calls.append((query, dict(params)))
            if "DISTINCT n.repo" in query:
                return [{"repo": "projA/s3"}]
            return []

        with unittest.mock.patch.object(android_mcp, "_run_cypher", run_cypher):
            result = await android_mcp.tool_search_functions(query="ghost", repo="xs3")
        self.assertEqual(
            result["warnings"],
            ["repo 'xs3' matched 0 nodes. Known repo values: projA/s3"],
        )


class AndroidPayloadDispatchRegressionTest(_BackendCase):
    """Same fn(payload=merged) regression as the cplus file: the repo value
    must reach the Cypher params from the payload, not the kwarg."""

    module = android_mcp

    async def test_trace_flow_repo_rides_payload_only(self) -> None:
        await android_mcp.tool_trace_flow(start_id="a", end_id="b", payload={"repo": "s3"})
        query, params = self.h.main_call
        self.assertEqual(params["repo"], "s3")
        self.assertEqual(params["repo_suffix"], "/s3")
        self.assertIn("a.repo = $repo", query)
        self.assertIn("b.repo = $repo", query)

    async def test_listup_symbols_repo_rides_payload_only(self) -> None:
        await android_mcp.tool_listup_symbols_matching_file_path(
            modules=["svc"], payload={"repo": "s3"}
        )
        query, params = self.h.main_call
        self.assertEqual(params["repo"], "s3")
        self.assertIn("n.repo = $repo", query)


class JavaRepoScopeTest(_BackendCase):
    module = java_mcp

    async def test_search_functions_scoped(self) -> None:
        self.h.rows = [_row("n1", "/Users/x/s3")]
        result = await java_mcp.tool_search_functions(query="pay", repo="s3")
        self.assertEqual(result["scope"]["repo"], "s3")
        query, params = self.h.main_call
        self.assertEqual(params["repo_suffix"], "/s3")

    async def test_search_by_code_both_templates(self) -> None:
        await java_mcp.tool_search_by_code(query="payment", repo="s3")
        for query, params in self.h.cypher_calls:
            if "DISTINCT n.repo" in query:
                continue
            self.assertIn("$repo = ''", query)
            self.assertEqual(params["repo_suffix"], "/s3")

    async def test_list_possible_calls_via_driver_repo_kwarg(self) -> None:
        captured: dict = {}

        async def list_possible_calls(**kwargs):
            captured.update(kwargs)
            return ([{"id": "a", "labels": ["Function"], "name": "a", "repo": "proj/s3"}], [])

        self.h.stub.list_possible_calls = AsyncMock(side_effect=list_possible_calls)
        result = await java_mcp.tool_list_possible_calls(repo="s3")
        self.assertEqual(captured.get("repo"), "s3")
        self.assertEqual(result["scope"]["filter_applied"], True)

    async def test_find_path_between_module_filters_source_side(self) -> None:
        await java_mcp.tool_find_path_between_module(
            source_modules=["src/a"], target_modules=["src/b"], repo="s3"
        )
        query, params = self.h.main_call
        self.assertIn("s.repo = $repo", query)
        self.assertNotIn("t.repo = $repo", query)
        self.assertEqual(params["repo_suffix"], "/s3")

    async def test_get_node_details_predicate_and_scope(self) -> None:
        self.h.rows = [_row("n1", "proj/s3")]
        result = await java_mcp.tool_get_node_details(node_ids=["n1"], repo="s3")
        self.assertEqual([node["id"] for node in result["nodes"]], ["n1"])
        query, params = self.h.main_call
        self.assertIn("$repo = ''", query)
        self.assertEqual(params["repo"], "s3")


# ── Template checklist (independent per file — copy-paste origin) ────────

_TOOL_BLOCKS_RE = re.compile(
    r"@mcp_server\.tool\(\s*name=\"(?P<name>[a-z_]+)\".*?(?=\n@mcp_server\.tool|\Z)",
    re.DOTALL,
)

_ANDROID_EXCLUDED = {
    "annotate_node",
    "list_databases",
    "list_qdrant_collections",
    "list_mcp_functions",
}
_JAVA_EXCLUDED = {
    "annotate_node",
    "list_databases",
    "list_qdrant_collections",
    "list_mcp_functions",
}


class AndroidTemplateChecklistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Path(android_mcp.__file__).read_text()
        self.blocks = {
            m.group("name"): m.group(0) for m in _TOOL_BLOCKS_RE.finditer(self.source)
        }

    def test_project_scoped_templates_have_repo_predicate(self) -> None:
        missing = [
            name
            for name, block in self.blocks.items()
            if name not in _ANDROID_EXCLUDED
            and "$project_id IS NULL" in block
            and "repo_scope_predicate(" not in block
        ]
        self.assertEqual(missing, [])

    def test_excluded_tools_do_not_expose_repo(self) -> None:
        for name in _ANDROID_EXCLUDED:
            block = self.blocks.get(name)
            if block is not None:
                self.assertNotIn("repo: Optional[str] = None", block, name)


class JavaTemplateChecklistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.source = Path(java_mcp.__file__).read_text()
        self.blocks = {
            m.group("name"): m.group(0) for m in _TOOL_BLOCKS_RE.finditer(self.source)
        }

    def test_project_scoped_templates_have_repo_predicate(self) -> None:
        missing = [
            name
            for name, block in self.blocks.items()
            if name not in _JAVA_EXCLUDED
            and "$project_id IS NULL" in block
            and "repo_scope_predicate(" not in block
        ]
        self.assertEqual(missing, [])

    def test_excluded_tools_do_not_expose_repo(self) -> None:
        for name in _JAVA_EXCLUDED:
            block = self.blocks.get(name)
            if block is not None:
                self.assertNotIn("repo: Optional[str] = None", block, name)


if __name__ == "__main__":
    unittest.main()

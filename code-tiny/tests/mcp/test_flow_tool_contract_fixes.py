import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

_REPO_ROOT = Path(__file__).resolve().parents[2]  # code-tiny/
_MCP_DIR = _REPO_ROOT / "mcp"
for path in (_REPO_ROOT, _MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from tools.graph.driver.neo4j_driver import (  # noqa: E402
    Neo4jDriver,
    normalize_graph_direction,
)


class NormalizeGraphDirectionTest(unittest.TestCase):
    def test_aliases_map_to_canonical_values(self):
        cases = {
            "in": "in", "IN": "in", "incoming": "in", "upstream": "in", "callers": "in",
            "out": "out", "outgoing": "out", "downstream": "out", "callees": "out",
            "both": "both", "all": "both", "any": "both", "undirected": "both",
            "": "both", None: "both",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_graph_direction(raw), expected, repr(raw))

    def test_unknown_direction_raises_instead_of_silent_both(self):
        for bad in ("sideways", "diagonal", " up "):
            with self.assertRaises(ValueError, msg=repr(bad)):
                normalize_graph_direction(bad)


class _StubDriver(Neo4jDriver):
    """Driver instance whose transport is replaced by a capturing stub."""

    def __init__(self):  # noqa: D107 — deliberately skip the real constructor
        self.execute_query = AsyncMock(return_value=([], 0, []))


class FindFunctionPathsCypherTest(unittest.IsolatedAsyncioTestCase):
    async def test_cypher_avoids_shortestpath_and_orders_by_length(self):
        driver = _StubDriver()
        await driver.find_function_paths(
            start_id="a", end_id="b",
            relationship_types=["CALLS", "CONTAINS"],
            max_depth=6, project_id=None, database="sampledb",
        )
        cypher = driver.execute_query.call_args.args[0]
        self.assertNotIn("shortestPath", cypher)
        self.assertIn("ORDER BY length(p)", cypher)
        self.assertIn("[:CALLS|CONTAINS*..6]", cypher)
        params = driver.execute_query.call_args.args[1]
        self.assertEqual(params["limit"], 10)

    async def test_query_function_subgraph_rejects_sideways(self):
        driver = _StubDriver()
        with self.assertRaises(ValueError):
            await driver.query_function_subgraph(
                function_id="x", relationship_types=["CALLS"], direction="sideways",
            )
        driver.execute_query.assert_not_awaited()

    async def test_query_function_subgraph_maps_downstream_to_outgoing(self):
        driver = _StubDriver()
        await driver.query_function_subgraph(
            function_id="x", relationship_types=["CALLS"],
            direction="downstream", max_depth=2,
        )
        cypher = driver.execute_query.call_args.args[0]
        self.assertIn("-[:CALLS*1..2]->", cypher)


def _load_unified():
    import unified_mcp

    return unified_mcp


class UnifiedDefaultsDbMirrorTest(unittest.TestCase):
    def test_db_mirrors_into_missing_project_id(self):
        unified = _load_unified()
        merged = unified._apply_unified_defaults({"query": "x", "db": "sampledb"})
        self.assertEqual(merged["project_id"], "sampledb")
        self.assertEqual(merged["db"], "sampledb")

    def test_explicit_project_id_wins(self):
        unified = _load_unified()
        merged = unified._apply_unified_defaults({"db": "sampledb", "project_id": "hyperpack"})
        self.assertEqual(merged["project_id"], "hyperpack")

    def test_blank_db_is_ignored(self):
        unified = _load_unified()
        merged = unified._apply_unified_defaults({"db": "   "})
        self.assertNotIn("project_id", merged)


class CatalogInputsSignatureSyncTest(unittest.TestCase):
    def test_discovery_inputs_match_registered_callables(self):
        unified = _load_unified()
        import cplus.cplus_mcp as cplus_backend
        import inspect

        for name, backend_fn in (
            ("query_subgraph", cplus_backend.tool_query_subgraph),
            ("find_paths", cplus_backend.tool_find_paths),
            ("trace_flow", cplus_backend.tool_trace_flow),
            ("search_functions", cplus_backend.tool_search_functions),
        ):
            entry = unified._CATALOG_BY_NAME[name]
            advertised = [i["name"] for i in entry["inputs"]]
            signature = inspect.signature(getattr(backend_fn, "fn", backend_fn))
            callable_params = [
                param_name
                for param_name, param in signature.parameters.items()
                if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            ]
            self.assertEqual(advertised, callable_params, name)

    def test_stale_fastmcp_only_params_are_gone_from_discovery(self):
        unified = _load_unified()
        entry = unified._CATALOG_BY_NAME["query_subgraph"]
        names = {i["name"] for i in entry["inputs"]}
        # Callable contract now carries both relationship param names…
        self.assertIn("rel_types", names)
        self.assertIn("relationship_types", names)
        # …and no longer advertises fastmcp_server-only parameters.
        self.assertNotIn("node_type", names)
        self.assertNotIn("expand_search", names)


if __name__ == "__main__":
    unittest.main()

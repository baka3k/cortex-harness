import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(ROOT, "code-tiny")
MCP_DIR = os.path.join(CODE_TINY, "mcp")
for path in (CODE_TINY, MCP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import unified_mcp
from java import java_mcp
from services.explore_service import _make_graph_driver
from services.workflow_service import run_find_screen_workflows
from tools.graph.core.shared_runtime import reset_shared_graph_drivers


class FrameworkMcpFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_service_defaults_to_falkordb_graph_name(self):
        captured = {}

        async def fake_finder(_driver, database, **kwargs):
            captured.update(database=database, kwargs=kwargs)
            return {"workflows": []}

        with patch.dict(os.environ, {}, clear=True), patch(
            "tools.ts.workflow_finder.find_screen_workflows", side_effect=fake_finder,
        ):
            await run_find_screen_workflows(
                AsyncMock(return_value=object()),
                {"project_id": "demo", "node_a": "Home"},
            )

        self.assertEqual(captured["database"], "hyper_graph")

    async def test_bridge_defaults_to_shared_falkordb_driver(self):
        class FakeDriver:
            def __init__(self):
                self.calls = []
                self.close_calls = 0

            async def execute_query(self, _query, _params, _database):
                self.calls.append((_query, _params, _database))
                return [], [], None

            def close(self):
                self.close_calls += 1

        driver = FakeDriver()
        with patch.object(
            unified_mcp.cplus_backend,
            "_get_graph_driver",
            new=AsyncMock(return_value=driver),
        ) as get_driver:
            await unified_mcp._run_bridge_query("RETURN 1", {}, "requested_graph")

        get_driver.assert_awaited_once_with()
        self.assertEqual(driver.calls, [("RETURN 1", {}, "requested_graph")])
        self.assertEqual(driver.close_calls, 0)

    async def test_unscoped_bridge_queries_every_registered_graph(self):
        class FakeDriver:
            def __init__(self):
                self.calls = []

            async def execute_query(self, _query, _params, database):
                self.calls.append(database)
                return [
                    {"database": "shared"},
                    {"database": database},
                ], [], None

        driver = FakeDriver()
        with patch.object(
            unified_mcp.cplus_backend,
            "_get_graph_driver",
            new=AsyncMock(return_value=driver),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_resolve_db_candidates",
            return_value=["alpha_graph", "beta_graph"],
        ):
            records = await unified_mcp._run_bridge_query(
                "RETURN 1", {"limit": 2}, None,
            )

        self.assertIsNone(unified_mcp._resolve_graph_database())
        self.assertEqual(driver.calls, ["alpha_graph", "beta_graph"])
        self.assertEqual(
            records,
            [{"database": "shared"}, {"database": "alpha_graph"}],
        )

    async def test_backend_cypher_helpers_aggregate_only_unscoped_candidates(self):
        backends = (
            unified_mcp.cplus_backend,
            unified_mcp.android_backend,
            unified_mcp.fast_backend,
            java_mcp,
        )
        for backend in backends:
            calls = []

            async def fake_run(_query, _params, database):
                calls.append(database)
                return [{"shared": True}, {"database": database}]

            with self.subTest(backend=backend.__name__), patch.object(
                backend, "_list_databases", new=AsyncMock(return_value=["alpha", "beta"]),
            ), patch.object(backend, "_run_cypher", side_effect=fake_run):
                used_db, rows = await backend._run_cypher_first(
                    "RETURN 1", {}, ["alpha", "beta"],
                )
                self.assertEqual(used_db, "alpha")
                self.assertEqual(calls, ["alpha", "beta"])
                self.assertEqual(
                    rows,
                    [{"shared": True}, {"database": "alpha"}, {"database": "beta"}],
                )

                calls.clear()
                _, limited_rows = await backend._run_cypher_first(
                    "RETURN 1", {"limit": 2}, ["alpha", "beta"],
                )
                self.assertEqual(calls, ["alpha", "beta"])
                self.assertEqual(
                    limited_rows,
                    [{"shared": True}, {"database": "alpha"}],
                )

                calls.clear()
                used_db, rows = await backend._run_cypher_first(
                    "RETURN 1", {"project_id": "alpha"}, ["alpha", "beta"],
                )
                self.assertEqual(used_db, "alpha")
                self.assertEqual(calls, ["alpha"])
                self.assertEqual(rows, [{"shared": True}, {"database": "alpha"}])

    async def test_unified_backends_share_one_real_embedded_driver(self):
        modules = (
            unified_mcp.cplus_backend,
            unified_mcp.android_backend,
            unified_mcp.fast_backend,
            java_mcp,
        )
        reset_shared_graph_drivers(close=True)
        for module in modules:
            module._graph_driver = None
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(
                os.environ,
                {"FALKORDB_PATH": str(Path(directory) / "code" / "data.rdb")},
                clear=False,
            ):
                drivers = [await module._get_graph_driver() for module in modules]
                self.assertIs(drivers[0], drivers[1])
                self.assertIs(drivers[0], drivers[2])
                await drivers[0].execute_query(
                    "CREATE (:LeaseProbe {id: $id})", {"id": "alpha"}, "alpha_graph"
                )
                rows, _, _ = await drivers[2].execute_query(
                    "MATCH (n:LeaseProbe {id: $id}) RETURN n.id AS id",
                    {"id": "alpha"},
                    "alpha_graph",
                )
                self.assertEqual(rows, [{"id": "alpha"}])
                await drivers[0].execute_query(
                    "CREATE (:LeaseProbe {id: $id})", {"id": "beta"}, "beta_graph"
                )
                for module in modules:
                    with self.subTest(live_discovery=module.__name__):
                        discovered = await module._list_databases()
                        self.assertIn("alpha_graph", discovered)
                        self.assertIn("beta_graph", discovered)
                        used_db, multi_rows = await module._run_cypher_first(
                            "MATCH (n:LeaseProbe) RETURN n.id AS id", {},
                            ["alpha_graph", "beta_graph"],
                        )
                        self.assertEqual(used_db, "alpha_graph")
                        self.assertEqual(
                            multi_rows, [{"id": "alpha"}, {"id": "beta"}],
                        )

                explore_driver = await _make_graph_driver(
                    "bolt://localhost:7687", "", "", "beta_graph",
                    provider="falkordb",
                )
                self.assertIs(explore_driver, drivers[0])
        finally:
            for module in modules:
                module._graph_driver = None
            reset_shared_graph_drivers(close=True)

    async def test_java_neo4j_rollback_requires_explicit_credentials(self):
        java_mcp._graph_driver = None
        with patch.object(java_mcp, "DEFAULT_GRAPH_PROVIDER", "neo4j"), patch.object(
            java_mcp, "DEFAULT_NEO4J_USER", None,
        ), patch.object(java_mcp, "DEFAULT_NEO4J_PASSWORD", None):
            with self.assertRaisesRegex(RuntimeError, "NEO4J_USER and NEO4J_PASS"):
                await java_mcp._get_graph_driver()

    def test_default_backend_candidates_cover_registered_graphs_once(self):
        targets = {
            "Alpha": SimpleNamespace(code_graph="shared_graph"),
            "Beta": SimpleNamespace(code_graph="beta_graph"),
            "Gamma": SimpleNamespace(code_graph="shared_graph"),
        }
        with patch.object(
            unified_mcp.cplus_backend,
            "list_registered_projects",
            return_value=list(targets),
        ), patch.object(
            unified_mcp.cplus_backend,
            "resolve_project_targets",
            side_effect=lambda project_id: targets[project_id],
        ):
            candidates = unified_mcp.cplus_backend._resolve_db_candidates(None)

        self.assertEqual(candidates, ["shared_graph", "beta_graph"])

    async def test_api_chain_supports_endpoint_directions_servlet_bridge_and_mybatis_tables(self):
        captured = {}

        async def fake_query(cypher, params, database):
            captured.update(cypher=cypher, params=params, database=database)
            return [{
                "be_endpoint_path": "/catalog/{id}", "be_method": "GET", "be_framework": "spring",
                "be_controller": "CatalogController", "be_service": "CatalogService",
                "be_repository": "CatalogMapper", "persistence_fact": "find", "database_table": "catalog",
            }]

        tool = getattr(unified_mcp.tool_get_api_call_chain, "fn", unified_mcp.tool_get_api_call_chain)
        capability = AsyncMock(
            return_value=(
                "spring",
                ["CALLS", "CALLS_API", "MATCHES", "HANDLES"],
                {"canonical_parser": "spring"},
                None,
                None,
            )
        )
        with patch.object(
            unified_mcp, "_run_bridge_query", side_effect=fake_query
        ), patch.object(
            unified_mcp,
            "_resolve_direct_capability_context",
            capability,
        ):
            result = await tool(
                endpoint_path="/catalog/{id}",
                project_id="hyper_graph",
                parser_type="spring",
            )

        query = captured["cypher"]
        self.assertIn("(ep)-[:HANDLES]->(forwardCtrl:Controller)", query)
        self.assertIn("(reverseCtrl:Controller)-[:HANDLES]->(ep)", query)
        self.assertIn("(ep)-[:SEMANTIC_OF]->(servletHandler:Function)", query)
        self.assertIn("MyBatisMapperMethod", query)
        self.assertIn("READS_FROM|WRITES_TO|REFERENCES_TABLE", query)
        self.assertIn("WITH ep WHERE true", query)
        self.assertEqual(result["chains"][0]["database_table"], "catalog")


if __name__ == "__main__":
    unittest.main()

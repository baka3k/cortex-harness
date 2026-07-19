import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(ROOT, "code-tiny")
MCP_DIR = os.path.join(CODE_TINY, "mcp")
for path in (CODE_TINY, MCP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import unified_mcp
from services.workflow_service import run_find_screen_workflows


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
            async def execute_query(self, _query, _params, _database):
                return [], [], None

            def close(self):
                return None

        create_driver = AsyncMock(return_value=FakeDriver())
        with patch.dict(
            os.environ,
            {"NEO4J_URI": "bolt://legacy-host:7687"},
            clear=True,
        ), patch.object(
            unified_mcp.GraphDriverFactory, "create_driver", create_driver,
        ):
            await unified_mcp._run_bridge_query("RETURN 1", {}, "hyper_graph")

        self.assertEqual(create_driver.await_args.args[0], unified_mcp.GraphProvider.FALKORDB)
        self.assertIsNone(create_driver.await_args.args[1]["uri"])

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
        with patch.object(unified_mcp, "_run_bridge_query", side_effect=fake_query):
            result = await tool(endpoint_path="/catalog/{id}", db="hyper_graph")

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

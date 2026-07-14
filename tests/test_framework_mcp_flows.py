import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(ROOT, "code-tiny")
MCP_DIR = os.path.join(CODE_TINY, "mcp")
for path in (CODE_TINY, MCP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import unified_mcp


class FrameworkMcpFlowTests(unittest.IsolatedAsyncioTestCase):
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
            result = await tool(endpoint_path="/catalog/{id}", db="neo4j")

        query = captured["cypher"]
        self.assertIn("(ep)-[:HANDLES]->(forwardCtrl:Controller)", query)
        self.assertIn("(reverseCtrl:Controller)-[:HANDLES]->(ep)", query)
        self.assertIn("(ep)-[:SEMANTIC_OF]->(servletHandler:Function)", query)
        self.assertIn("MyBatisMapperMethod", query)
        self.assertIn("READS_FROM|WRITES_TO|REFERENCES_TABLE", query)
        self.assertIn("ServletJspAnalysisState", query)
        self.assertEqual(result["chains"][0]["database_table"], "catalog")


if __name__ == "__main__":
    unittest.main()

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

from cplus import cplus_mcp


class FrameworkMcpSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_includes_framework_filters_and_generation_freshness(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append((query, params, dbs))
            return dbs[0], [{"n": {"id": "statement-1", "name": "findCatalog", "kind": "MyBatisStatement", "framework": "mybatis"}}]

        tool = getattr(cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions)
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            result = await tool(query="catalog", db="neo4j", framework="mybatis", kinds=["MyBatisStatement"])

        query, params, _ = calls[0]
        self.assertIn("node.framework IN ['spring', 'servlet_jsp', 'mybatis']", query)
        self.assertIn("ServletJspAnalysisState", query)
        self.assertEqual(params["framework"], "mybatis")
        self.assertEqual(params["kinds"], ["MyBatisStatement"])
        self.assertEqual(result["ids"], ["statement-1"])


if __name__ == "__main__":
    unittest.main()

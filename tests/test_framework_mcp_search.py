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
    def test_compact_node_output_does_not_leak_raw_note_source(self):
        node = {
            "id": "file-1",
            "labels": ["File"],
            "name": "batch_job.c",
            "summary": "Batch orchestration source file",
            "note": "Code:\n#include <stdio.h>\nint main(void) { return 0; }",
            "code": "#include <stdio.h>\nint main(void) { return 0; }",
        }

        result = cplus_mcp._record_node(
            node, content_mode="summary", include_raw_fields=False
        )

        properties = result["properties"]
        self.assertEqual(properties["content"], "Batch orchestration source file")
        self.assertNotIn("note", properties)
        self.assertNotIn("code", properties)

    async def test_cplus_symbol_search_excludes_container_file_nodes(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append(query)
            return dbs[0], []

        tool = getattr(
            cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions
        )
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            await tool(
                query="batch_job_run",
                project_id="procsample",
                payload={"parser_type": "proc"},
            )

        self.assertTrue(calls)
        self.assertIn("node:Function", calls[0])
        self.assertNotIn("node:File", calls[0])

    async def test_search_result_keeps_labels_and_compact_name_fallback(self):
        async def fake_run(query, params, dbs):
            return dbs[0], [
                {
                    "n": {
                        "id": "function-1",
                        "name": "batch_job_run",
                        "summary": "",
                        "code": "int batch_job_run(void) { return 0; }",
                    },
                    "labels": ["Function"],
                }
            ]

        tool = getattr(
            cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions
        )
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            result = await tool(
                query="batch_job_run",
                project_id="procsample",
                content_mode="summary",
                payload={"parser_type": "proc"},
            )

        node = result["results"][0]
        self.assertEqual(node["labels"], ["Function"])
        self.assertEqual(node["properties"]["content"], "batch_job_run")
        self.assertNotIn("id", node["properties"])
        self.assertNotIn("code", node["properties"])

    async def test_parser_profile_applies_labels_without_inventing_framework_filter(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append((query, params))
            return dbs[0], []

        tool = getattr(cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions)
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            await tool(query="users", project_id="graph", payload={"parser_type": "python"})

        self.assertTrue(calls)
        self.assertIn("node:ApiEndpoint", calls[0][0])
        self.assertIsNone(calls[0][1]["framework"])

    async def test_explicit_framework_filter_is_exact_in_fulltext_and_fallback(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append((query, params))
            return dbs[0], []

        tool = getattr(cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions)
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            await tool(
                query="users", project_id="graph", framework="fastapi",
                payload={"parser_type": "python"},
            )

        self.assertEqual(len(calls), 2)
        for query, params in calls:
            self.assertIn("$framework IS NULL OR", query)
            self.assertIn("framework = $framework", query)
            self.assertEqual(params["framework"], "fastapi")
        self.assertNotIn("node.framework = $framework OR (", calls[0][0])

    async def test_profile_search_merges_fulltext_and_property_fallback_results(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append(query)
            if "fulltext.queryNodes" in query:
                return dbs[0], [{"n": {"id": "endpoint-1", "name": "Orders", "framework": "aspnet_core"}}]
            return dbs[0], [{"n": {"id": "config-1", "config_key": "Orders", "framework": "aspnet_core"}}]

        tool = getattr(cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions)
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            result = await tool(query="orders", project_id="neo4j", framework="aspnet_core")

        self.assertEqual(len(calls), 2)
        self.assertIn("node:HttpEndpoint", calls[0])
        self.assertIn("n.config_key", calls[1])
        self.assertEqual(result["ids"], ["endpoint-1", "config-1"])

    async def test_search_includes_framework_filters_and_generation_freshness(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append((query, params, dbs))
            return dbs[0], [{"n": {"id": "statement-1", "name": "findCatalog", "kind": "MyBatisStatement", "framework": "mybatis"}}]

        tool = getattr(cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions)
        with patch.dict(os.environ, {"CODE_GRAPH_PROVIDER": "neo4j"}):
            with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
                result = await tool(query="catalog", project_id="neo4j", framework="mybatis", kinds=["MyBatisStatement"])

        query, params, _ = calls[0]
        self.assertIn("node.framework IN ['spring', 'servlet_jsp', 'mybatis']", query)
        self.assertIn("ServletJspAnalysisState", query)
        self.assertEqual(params["framework"], "mybatis")
        self.assertEqual(params["kinds"], ["MyBatisStatement"])
        self.assertEqual(result["ids"], ["statement-1"])

    async def test_struts_query_uses_framework_filtered_property_fallback(self):
        calls = []

        async def fake_run(query, params, dbs):
            calls.append((query, params))
            if "fulltext.queryNodes" in query:
                return dbs[0], []
            return dbs[0], [
                {
                    "n": {
                        "id": "struts-action-1",
                        "name": "checkout",
                        "kind": "StrutsAction",
                        "framework": "struts",
                        "project_id": "fixture",
                        "file_path": "src/main/resources/struts.xml",
                    }
                }
            ]

        tool = getattr(cplus_mcp.tool_search_functions, "fn", cplus_mcp.tool_search_functions)
        with patch.object(cplus_mcp, "_run_cypher_first", side_effect=fake_run):
            result = await tool(
                query="checkout action",
                project_id="neo4j",
                framework="struts",
                include_raw_fields=True,
            )

        self.assertEqual(len(calls), 2)
        self.assertIn("fulltext.queryNodes", calls[0][0])
        self.assertIn("n.name", calls[1][0])
        self.assertEqual(calls[1][1]["framework"], "struts")
        self.assertEqual(result["ids"], ["struts-action-1"])
        self.assertEqual(
            result["results"][0]["properties"]["file_path"],
            "src/main/resources/struts.xml",
        )


if __name__ == "__main__":
    unittest.main()

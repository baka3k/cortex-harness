import os
import json
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
from tools.common.workflow_impact_scorer import WorkflowImpactScorer


class UnifiedMcpInputCoercionTests(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_only_explore_does_not_require_graph_relationships(self):
        captured = {}

        class FakeExploreService:
            async def explore(self, **kwargs):
                captured.update(kwargs)
                return {"matched_nodes": []}

        tool = getattr(unified_mcp.tool_explore_graph, "fn", unified_mcp.tool_explore_graph)
        with patch(
            "services.explore_service.get_explore_service",
            return_value=FakeExploreService(),
        ):
            with patch.object(
                unified_mcp.cplus_backend,
                "_resolve_rel_types_with_diagnostics",
                AsyncMock(side_effect=AssertionError("semantic mode must not inspect graph schema")),
            ):
                result = await tool(
                    query="orders", mode="semantic", parser_type="spring",
                )

        self.assertIsNone(captured["graph_rel_types"])
        self.assertIn("ApiEndpoint", captured["searchable_labels"])
        self.assertEqual(result["capability"]["canonical_parser"], "spring")

    async def test_impact_analysis_propagates_parser_profile_to_subgraph(self):
        captured = {}

        async def fake_dispatch(tool_name, payload):
            captured["tool_name"] = tool_name
            captured["payload"] = payload
            return {
                "nodes": [], "edges": [],
                "capability": {"canonical_parser": "spring"},
            }

        tool = getattr(
            unified_mcp.tool_analyze_workflow_impact,
            "fn",
            unified_mcp.tool_analyze_workflow_impact,
        )
        with patch.object(unified_mcp, "_dispatch_tool", side_effect=fake_dispatch):
            with patch.dict(os.environ, {"WORKFLOW_IMPACT_DISABLED": "1"}):
                result = await tool(function_id="entry", parser_type="spring-boot")

        self.assertEqual(captured["tool_name"], "query_subgraph")
        self.assertEqual(captured["payload"]["parser_type"], "spring-boot")
        self.assertEqual(result["capability"]["canonical_parser"], "spring")

    async def test_endpoint_context_uses_tool_specific_relationship_profile(self):
        diagnostics = {"support_status": "supported"}
        resolver = AsyncMock(return_value=(["CALLS_API", "MATCHES"], diagnostics))
        with patch.object(
            unified_mcp.cplus_backend,
            "_resolve_rel_types_with_diagnostics",
            resolver,
        ):
            with patch.object(
                unified_mcp.cplus_backend,
                "_list_node_labels",
                AsyncMock(return_value=["ApiEndpoint", "ApiCall"]),
            ):
                selected, relationships, routing, returned_diagnostics, error = (
                    await unified_mcp._resolve_direct_capability_context(
                        "find_callers_of_endpoint", "spring-boot", "graph",
                        required_labels=("ApiEndpoint",),
                    )
                )

        requested = resolver.await_args.args[0]
        self.assertEqual(selected, "spring-boot")
        self.assertEqual(requested, ["CALLS_API", "MATCHES"])
        self.assertEqual(relationships, ["CALLS_API", "MATCHES"])
        self.assertEqual(routing["canonical_parser"], "spring")
        self.assertIs(returned_diagnostics, diagnostics)
        self.assertIsNone(error)

    async def test_direct_context_rejects_missing_mandatory_relationship(self):
        diagnostics = {
            "schema_status": "available",
            "support_status": "partial",
            "omitted_relationships": ["MATCHES"],
        }
        with patch.object(
            unified_mcp.cplus_backend,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=(["CALLS_API"], diagnostics)),
        ):
            with patch.object(
                unified_mcp.cplus_backend,
                "_list_node_labels",
                AsyncMock(return_value=["ApiEndpoint", "ApiCall"]),
            ):
                *_, error = await unified_mcp._resolve_direct_capability_context(
                    "find_callers_of_endpoint",
                    "spring",
                    "graph",
                    required_relationships=("CALLS_API", "MATCHES"),
                    required_labels=("ApiEndpoint", "ApiCall"),
                )

        self.assertEqual(error["error"]["type"], "capability_unavailable")
        self.assertEqual(
            error["capability_diagnostics"]["missing_required_relationships"],
            ["MATCHES"],
        )

    async def test_direct_context_rejects_missing_required_endpoint_label(self):
        diagnostics = {
            "schema_status": "available",
            "support_status": "supported",
            "available_relationships": ["CALLS_API", "MATCHES"],
        }
        with patch.object(
            unified_mcp.cplus_backend,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=(["CALLS_API", "MATCHES"], diagnostics)),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_list_node_labels",
            AsyncMock(return_value=["Function", "ApiCall"]),
        ):
            *_, error = await unified_mcp._resolve_direct_capability_context(
                "find_callers_of_endpoint",
                "typescript",
                "graph",
                required_relationships=("CALLS_API", "MATCHES"),
                required_labels=("ApiEndpoint", "ApiCall"),
            )

        self.assertEqual(error["error"]["type"], "capability_unavailable")
        self.assertEqual(
            error["capability_diagnostics"]["missing_required_labels"],
            ["ApiEndpoint"],
        )

    async def test_direct_context_fails_closed_when_label_schema_is_unavailable(self):
        diagnostics = {
            "schema_status": "available",
            "support_status": "supported",
            "available_relationships": ["HANDLES"],
        }
        with patch.object(
            unified_mcp.cplus_backend,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=(["HANDLES"], diagnostics)),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_list_node_labels",
            AsyncMock(return_value=None),
        ):
            *_, error = await unified_mcp._resolve_direct_capability_context(
                "get_api_call_chain",
                "typescript",
                "graph",
                required_relationships=("HANDLES",),
                required_labels=("ApiEndpoint",),
            )

        self.assertEqual(error["error"]["type"], "capability_unavailable")
        self.assertEqual(
            error["capability_diagnostics"]["label_schema_status"],
            "unavailable",
        )

    async def test_direct_context_fails_closed_when_relationship_schema_is_unavailable(self):
        diagnostics = {
            "schema_status": "unavailable",
            "support_status": "unknown",
            "available_relationships": [],
        }
        with patch.object(
            unified_mcp.cplus_backend,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=(["HANDLES"], diagnostics)),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_list_node_labels",
            AsyncMock(return_value=["ApiEndpoint"]),
        ):
            *_, error = await unified_mcp._resolve_direct_capability_context(
                "get_api_call_chain",
                "spring",
                "graph",
                required_relationships=("HANDLES",),
                required_labels=("ApiEndpoint",),
            )

        self.assertEqual(error["error"]["type"], "capability_unavailable")
        self.assertIn("relationship schema", error["error"]["message"])

    async def test_endpoint_chain_and_workflow_queries_use_profile_relationships(self):
        context = (
            "spring", ["CALLS", "SEMANTIC_OF"],
            {"canonical_parser": "spring"}, {"support_status": "supported"}, None,
        )
        bridge = AsyncMock(return_value=[])
        api_tool = getattr(
            unified_mcp.tool_get_api_call_chain,
            "fn",
            unified_mcp.tool_get_api_call_chain,
        )
        with patch.object(
            unified_mcp,
            "_resolve_direct_capability_context",
            AsyncMock(return_value=context),
        ):
            with patch.object(unified_mcp, "_run_bridge_query", bridge):
                await api_tool(component_name="Orders", parser_type="spring")

        self.assertIn("[:CALLS|SEMANTIC_OF*", bridge.await_args.args[0])

        queries = []

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def run(self, query, _params):
                queries.append(query)
                return []

        class FakeDriver:
            def session(self, **_kwargs):
                return FakeSession()

            def close(self):
                return None

        workflow_tool = getattr(
            unified_mcp.tool_find_workflows_containing,
            "fn",
            unified_mcp.tool_find_workflows_containing,
        )
        with patch.object(
            unified_mcp,
            "_resolve_direct_capability_context",
            AsyncMock(return_value=context),
        ):
            with patch.object(unified_mcp, "_get_bridge_driver", return_value=FakeDriver()):
                result = await workflow_tool(function_id="entry", parser_type="spring")

        self.assertTrue(any("[:CALLS|SEMANTIC_OF*" in query for query in queries))
        self.assertEqual(result["capability"]["canonical_parser"], "spring")

    async def test_workflow_impact_scorer_uses_supplied_profile_relationships(self):
        queries = []

        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def run(self, query, _params):
                queries.append(query)
                return []

        class FakeDriver:
            def session(self, **_kwargs):
                return FakeSession()

        scorer = WorkflowImpactScorer(
            FakeDriver(),
            flow_relationships=["CALLS", "SEMANTIC_OF"],
            workflow_relationship="HAS_STEP",
        )
        await scorer.score("entry", [], max_depth=2)

        self.assertTrue(any("[:CALLS|SEMANTIC_OF*1..2]" in query for query in queries))
        self.assertTrue(any("[:HAS_STEP]" in query for query in queries))

    async def test_dispatch_applies_framework_defaults_and_reports_capability(self):
        captured = {}

        async def fake_trace_flow(payload):
            captured.update(payload)
            return {"nodes": [], "edges": []}

        with patch.object(unified_mcp.cplus_backend, "tool_trace_flow", fake_trace_flow):
            result = await unified_mcp._dispatch_tool(
                "trace_flow",
                {"parser_type": "spring-boot", "start_id": "entry"},
            )

        self.assertIn("SEMANTIC_OF", captured["rel_types"])
        self.assertTrue(captured["_capability_default_relationships"])
        self.assertEqual(result["query_engine"], "graph_generic")
        self.assertNotIn("backend", result)
        self.assertEqual(result["capability"]["canonical_parser"], "spring")
        self.assertEqual(result["capability"]["support_level"], "full")

    async def test_dispatch_keeps_parser_profile_separate_from_framework_filter(self):
        captured = {}

        async def fake_search(payload):
            captured.update(payload)
            return {"results": [], "ids": []}

        with patch.object(unified_mcp.cplus_backend, "tool_search_functions", fake_search):
            result = await unified_mcp._dispatch_tool(
                "search_functions",
                {"parser_type": "python", "query": "users"},
            )

        self.assertEqual(captured["parser_type"], "python")
        self.assertNotIn("framework", captured)
        self.assertEqual(result["capability"]["canonical_parser"], "python")

    async def test_dispatch_rejects_unknown_nonempty_parser(self):
        result = await unified_mcp._dispatch_tool(
            "trace_flow",
            {"parser_type": "pyhton", "start_id": "entry"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "unsupported_parser")
        self.assertEqual(result["error"]["parser_type"], "pyhton")
        self.assertIn("python", result["error"]["supported_parsers"])
        self.assertEqual(result["query_engine"], "graph_generic")

    async def test_activate_project_rejects_unknown_parser(self):
        tool = getattr(unified_mcp.tool_activate_project, "fn", unified_mcp.tool_activate_project)
        result = await tool(parser_type="laravle", database_name="graph")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "unsupported_parser")
        self.assertEqual(result["error"]["parser_type"], "laravle")

    async def test_provider_relationship_filter_reports_partial_and_unsupported(self):
        resolver = unified_mcp.cplus_backend._resolve_rel_types_with_diagnostics
        with patch.object(
            unified_mcp.cplus_backend,
            "_list_relationship_types",
            AsyncMock(return_value=["CALLS", "CONTAINS"]),
        ):
            used, partial = await resolver(
                ["CALLS", "SEMANTIC_OF"], "spring", ["graph"], explicit=False,
            )
            missing, unsupported = await resolver(
                ["SEMANTIC_OF"], "spring", ["graph"], explicit=True,
            )

        self.assertEqual(used, ["CALLS"])
        self.assertEqual(partial["support_status"], "partial")
        self.assertEqual(partial["omitted_relationships"], ["SEMANTIC_OF"])
        self.assertEqual(missing, [])
        self.assertEqual(unsupported["support_status"], "unsupported")

    async def test_provider_relationship_filter_distinguishes_unknown_from_empty_schema(self):
        resolver = unified_mcp.cplus_backend._resolve_rel_types_with_diagnostics
        with patch.object(
            unified_mcp.cplus_backend,
            "_list_relationship_types",
            AsyncMock(return_value=None),
        ):
            retained, unknown = await resolver(
                ["CALLS"], "python", ["graph"], explicit=False,
            )
        with patch.object(
            unified_mcp.cplus_backend,
            "_list_relationship_types",
            AsyncMock(return_value=[]),
        ):
            omitted, empty = await resolver(
                ["CALLS"], "python", ["graph"], explicit=False,
            )

        self.assertEqual(retained, ["CALLS"])
        self.assertEqual(unknown["schema_status"], "unavailable")
        self.assertEqual(omitted, [])
        self.assertEqual(empty["schema_status"], "available")
        self.assertEqual(empty["support_status"], "unsupported")

    async def test_list_parsers_returns_canonical_capability_catalog(self):
        tool = getattr(unified_mcp.tool_list_parsers, "fn", unified_mcp.tool_list_parsers)
        result = await tool()

        self.assertEqual(result["capability_contract_version"], 1)
        profiles = {item["canonical_parser"]: item for item in result["capabilities"]}
        self.assertEqual(profiles["android"]["query_engine"], "android_graph")
        self.assertEqual(profiles["spring"]["query_engine"], "graph_generic")
        self.assertNotIn("backend", profiles["spring"])
        self.assertEqual(
            set(profiles["spring"]["support"]),
            {"symbols", "calls", "endpoints", "database"},
        )
        self.assertEqual(profiles["perl"]["support_level"], "generic")
        self.assertIn("asp.net-framework", result["parsers"])

    async def test_public_tool_catalog_is_provider_neutral_and_exposes_inspector(self):
        tool = getattr(
            unified_mcp.tool_list_mcp_functions,
            "fn",
            unified_mcp.tool_list_mcp_functions,
        )
        catalog = json.loads(await tool())
        serialized = json.dumps(catalog)
        names = {item["name"] for item in catalog["functions"]}

        self.assertIn("inspect_parser_capabilities", names)
        self.assertNotIn("Neo4j database", serialized)
        self.assertNotIn("raw Neo4j", serialized)
        self.assertNotIn("aliases, backends", serialized)

    async def test_inspect_parser_capabilities_reports_live_effective_support(self):
        tool = getattr(
            unified_mcp.tool_inspect_parser_capabilities,
            "fn",
            unified_mcp.tool_inspect_parser_capabilities,
        )
        with patch.object(
            unified_mcp.cplus_backend,
            "_list_node_labels",
            AsyncMock(return_value=["Function", "ApiEndpoint"]),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_list_relationship_types",
            AsyncMock(return_value=["CALLS", "HANDLES"]),
        ):
            result = await tool(parser_type="python", db="graph")

        self.assertTrue(result["ok"])
        self.assertEqual(result["canonical_parser"], "python")
        self.assertEqual(result["effective_support"]["symbols"], "full")
        self.assertEqual(result["effective_support"]["endpoints"], "partial")
        self.assertEqual(result["effective_support"]["database"], "none")
        self.assertEqual(result["recommended_action"], "none")
        self.assertTrue(result["schema_fingerprint"])

    async def test_inspect_parser_capabilities_recommends_sync_for_missing_schema(self):
        tool = getattr(
            unified_mcp.tool_inspect_parser_capabilities,
            "fn",
            unified_mcp.tool_inspect_parser_capabilities,
        )
        with patch.object(
            unified_mcp.cplus_backend,
            "_list_node_labels",
            AsyncMock(return_value=["Function"]),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_list_relationship_types",
            AsyncMock(return_value=["CALLS"]),
        ):
            result = await tool(parser_type="python", db="graph")

        self.assertEqual(result["effective_support"]["endpoints"], "none")
        self.assertEqual(result["recommended_action"], "run_incremental_sync")
        self.assertIn("endpoints", result["unavailable_dimensions"])

    async def test_inspect_parser_capabilities_rejects_unknown_parser(self):
        tool = getattr(
            unified_mcp.tool_inspect_parser_capabilities,
            "fn",
            unified_mcp.tool_inspect_parser_capabilities,
        )
        result = await tool(parser_type="pyhton", db="graph")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "unsupported_parser")

    def test_parse_positive_int_accepts_numeric_and_string_values(self):
        self.assertEqual(unified_mcp._parse_positive_int(20, "top_k"), (20, None))
        self.assertEqual(unified_mcp._parse_positive_int("20", "top_k"), (20, None))
        self.assertEqual(unified_mcp._parse_positive_int(20.0, "top_k"), (20, None))
        self.assertEqual(unified_mcp._parse_positive_int("", "top_k"), (None, None))

    def test_parse_positive_int_rejects_non_integer_values(self):
        self.assertEqual(
            unified_mcp._parse_positive_int(20.5, "top_k"),
            (None, "top_k must be a positive integer."),
        )
        self.assertEqual(
            unified_mcp._parse_positive_int(True, "top_k"),
            (None, "top_k must be a positive integer."),
        )
        self.assertEqual(
            unified_mcp._parse_positive_int(-1, "top_k"),
            (None, "top_k must be greater than 0."),
        )

    async def test_semantic_search_normalizes_numeric_knobs_before_dispatch(self):
        captured = {}

        async def fake_dispatch(tool_name, payload):
            captured["tool_name"] = tool_name
            captured["payload"] = payload
            return {"results": []}

        tool = getattr(
            unified_mcp.tool_semantic_search,
            "fn",
            unified_mcp.tool_semantic_search,
        )
        with patch.object(unified_mcp, "_dispatch_tool", side_effect=fake_dispatch):
            result = await tool(
                query="validation",
                top_k=20,
                graph_depth=2.0,
                graph_limit="50",
            )

        self.assertEqual(result, {"results": []})
        self.assertEqual(captured["tool_name"], "semantic_search")
        self.assertEqual(captured["payload"]["top_k"], 20)
        self.assertEqual(captured["payload"]["graph_depth"], 2)
        self.assertEqual(captured["payload"]["graph_limit"], 50)

    async def test_semantic_search_returns_tool_error_for_bad_numeric_input(self):
        tool = getattr(
            unified_mcp.tool_semantic_search,
            "fn",
            unified_mcp.tool_semantic_search,
        )

        result = await tool(query="validation", top_k="many")

        self.assertEqual(result["error"]["type"], "invalid_parameters")
        self.assertIn("top_k must be a positive integer", result["error"]["message"])

    async def test_explore_graph_returns_tool_error_for_bad_numeric_input(self):
        tool = getattr(
            unified_mcp.tool_explore_graph,
            "fn",
            unified_mcp.tool_explore_graph,
        )

        result = await tool(query="validation", top_k="many")

        self.assertEqual(result["error"]["type"], "invalid_parameters")
        self.assertIn("top_k must be a positive integer", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()

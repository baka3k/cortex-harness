import os
import json
import inspect
import sys
import types
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
    def test_proxy_marks_structured_application_errors_as_mcp_errors(self):
        result = unified_mcp._ProxyMiddleware._wrap_dispatch_result(
            {
                "ok": False,
                "error": {
                    "type": "unsupported_capability",
                    "message": "graph unavailable",
                },
            }
        )

        self.assertTrue(result.is_error)
        self.assertFalse(result.structured_content["ok"])

        direct_result = unified_mcp.ToolResult(
            content="graph unavailable",
            structured_content={"ok": False, "error": {"message": "graph unavailable"}},
        )
        normalized = unified_mcp._ProxyMiddleware._wrap_dispatch_result(direct_result)
        self.assertTrue(normalized.is_error)
        self.assertFalse(normalized.structured_content["ok"])

    async def test_hybrid_explore_degrades_to_semantic_when_graph_is_unavailable(self):
        captured = {}

        class FakeExploreService:
            async def explore(self, **kwargs):
                captured.update(kwargs)
                return {"matched_nodes": [{"node_id": "login"}]}

        diagnostics = {
            "schema_status": "available",
            "support_status": "unsupported",
            "requested_relationships": ["CALLS"],
            "used_relationships": [],
            "available_relationships": [],
        }
        tool = getattr(unified_mcp.tool_explore_graph, "fn", unified_mcp.tool_explore_graph)
        with patch(
            "services.explore_service.get_explore_service",
            return_value=FakeExploreService(),
        ), patch.object(
            unified_mcp.cplus_backend,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=([], diagnostics)),
        ):
            result = await tool(
                query="login", mode="hybrid", parser_type="python",
                project_id="stock",
            )

        self.assertEqual(captured["mode"], "semantic")
        self.assertEqual(result["requested_mode"], "hybrid")
        self.assertEqual(result["graph_expansion"]["outcome"], "unavailable")
        self.assertEqual(result["matched_nodes"][0]["node_id"], "login")

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
                    project_id="project-a",
                )

        self.assertIsNone(captured["graph_rel_types"])
        self.assertEqual(captured["project_id"], "project-a")
        self.assertEqual(captured["db"], "project-a")
        self.assertEqual(captured["collection"], "project-a")
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

    async def test_direct_context_fails_closed_when_no_parser_for_project_context_tool(self):
        *_, routing, _diagnostics, error = (
            await unified_mcp._resolve_direct_capability_context(
                "get_public_apis",
                None,
                "",
                required_relationships=("EXPOSES_API",),
                required_labels=("ProjectModule",),
                error_payload={"project_id": "cortext"},
            )
        )

        self.assertIsNotNone(error)
        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["type"], "capability_unavailable")
        self.assertIsNone(routing["canonical_parser"])
        self.assertIn("No parser selected", error["error"]["message"])
        self.assertIn("parser_type", error["error"]["message"])

    async def test_public_apis_returns_capability_error_when_no_parser_active(self):
        tool = getattr(unified_mcp.tool_get_public_apis, "fn", unified_mcp.tool_get_public_apis)
        bridge = AsyncMock(return_value=[])
        with patch.object(unified_mcp, "_run_bridge_query", bridge):
            result = await tool(project_id="cortext", limit=50)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "capability_unavailable")
        self.assertIn("No parser selected", result["error"]["message"])
        self.assertIsNone(result["capability"]["canonical_parser"])
        bridge.assert_not_called()

    def test_capability_summary_no_warning_when_parser_omitted(self):
        # Mirrors the project_id contract: omitting parser_type means "search
        # every parser". No warning should be emitted — the absence of
        # parser_type is an intentional, supported mode.
        summary = unified_mcp._capability_summary(None, "cplus")
        self.assertIsNone(summary["canonical_parser"])
        self.assertNotIn("warning", summary)

        # Empty string and whitespace also mean "not provided".
        for empty in ("", "   "):
            summary = unified_mcp._capability_summary(empty, "cplus")
            self.assertNotIn("warning", summary)

    def test_capability_summary_warns_only_for_unknown_explicit_parser(self):
        # An explicit but unknown parser is still a typo / unregistered
        # profile — surface the warning so the caller can fix it.
        summary = unified_mcp._capability_summary("pyhton", "cplus")
        self.assertIsNone(summary["canonical_parser"])
        self.assertIn("warning", summary)
        self.assertIn("pyhton", summary["warning"])
        self.assertIn("not registered", summary["warning"])

    async def test_fanout_dispatch_runs_each_parser_and_merges_results(self):
        # When parser_type is omitted and the tool is in the search-tool
        # set, ``_dispatch_tool`` must fan-out once per physical query
        # engine (BACKENDS), tag each hit with its source engine, and
        # surface the raw per-engine results. Per-engine payloads must NOT
        # carry a parser_type key (it is the engine-level fan-out mode).
        tools = [
            "search_functions",
            "search_by_code",
            "get_symbol",
            "get_node_details",
            "query_subgraph",
            "find_paths",
            "find_path_between_module",
            "listup_symbols_matching_file_path",
            "listup_class_matching_path",
            "list_up_entrypoint",
            "trace_flow",
            "trace_flow_between_module",
            "list_possible_calls",
        ]
        for tool_name in tools:
            self.assertIn(tool_name, unified_mcp._FANOUT_SEARCH_TOOLS)

        seen_payloads: list[dict] = []
        call_count = {"cplus": 0, "android": 0}

        async def fake_search_functions(payload=None):
            seen_payloads.append(dict(payload or {}))
            backend = (payload or {}).get("_backend_tag")
            call_count[backend] = call_count.get(backend, 0) + 1
            if backend == "android":
                return {"db": "android_g", "results": [{"id": "a1", "name": "alpha"}]}
            return {"db": "cplus_g", "results": [{"id": "c1", "name": "beta"}]}

        async def fake_cplus(*args, **kwargs):
            payload = kwargs.get("payload")
            if not payload and args:
                payload = args[-1]
            p = dict(payload or {})
            p["_backend_tag"] = "cplus"
            return await fake_search_functions(p)

        async def fake_android(*args, **kwargs):
            payload = kwargs.get("payload")
            if not payload and args:
                payload = args[-1]
            p = dict(payload or {})
            p["_backend_tag"] = "android"
            return await fake_search_functions(p)

        async def run_dispatch() -> dict:
            return await unified_mcp._dispatch_tool(
                "search_functions", {"query": "foo"}
            )

        fake_cplus_module = types.SimpleNamespace(tool_search_functions=fake_cplus)
        fake_android_module = types.SimpleNamespace(tool_search_functions=fake_android)

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=fake_cplus_module),
                "android": unified_mcp.BackendInfo(name="android", module=fake_android_module),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus", "spring"}),
            ):
                result = await run_dispatch()

        self.assertTrue(result["ok"])
        # Fan-out is per engine, not per parser alias.
        self.assertEqual(result["parsers_searched"], ["android", "cplus"])
        # Each engine dispatched exactly once (breadth = 2, not 3).
        self.assertEqual(call_count, {"cplus": 1, "android": 1})
        # Per-engine payloads must NOT leak parser_type (R1 regression guard).
        for payload in seen_payloads:
            self.assertNotIn("parser_type", payload)
            self.assertTrue(payload.get("_fanout"))
        self.assertIn("results", result)
        # Each hit is tagged with its source engine.
        parsers_seen = {hit["parser_type"] for hit in result["results"]}
        self.assertEqual(parsers_seen, {"android", "cplus"})
        # Per-engine raw results are kept.
        self.assertIn("parser_results", result)
        self.assertIn("android", result["parser_results"])
        self.assertIn("cplus", result["parser_results"])
        self.assertEqual(result["query_engine"], "graph_fanout")

    async def test_fanout_dispatch_does_not_trigger_when_parser_explicit(self):
        # When parser_type is explicitly passed, fan-out must NOT trigger.
        # The call should drop through to the single-parser path.
        async def fake_search_functions(payload=None):
            return {"db": "solo_g", "results": [{"id": "s1"}]}

        fake_module = types.SimpleNamespace(tool_search_functions=fake_search_functions)

        with patch.object(
            unified_mcp,
            "BACKENDS",
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=fake_module),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                with patch.object(
                    unified_mcp,
                    "_resolve_backend_name",
                    return_value="cplus",
                ):
                    result = await unified_mcp._dispatch_tool(
                        "search_functions",
                        {"query": "foo", "parser_type": "cplus"},
                    )

        # Single-parser result, not the fan-out wrapper.
        self.assertNotIn("parsers_searched", result)
        self.assertEqual(result["db"], "solo_g")
        self.assertEqual(result["results"], [{"id": "s1"}])

    async def test_fanout_dispatch_records_per_parser_errors(self):
        # One parser fails, the other succeeds. The merged result stays
        # ok=True and the failure is recorded under parser_errors.
        async def good_parser(payload=None):
            return {"db": "good_g", "results": [{"id": "g1"}]}

        async def bad_parser(payload=None):
            raise ValueError("mocked backend failure")

        fake_module = types.SimpleNamespace(tool_search_functions=good_parser)
        bad_module = types.SimpleNamespace(tool_search_functions=bad_parser)

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=fake_module),
                "android": unified_mcp.BackendInfo(name="android", module=bad_module),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["parsers_searched"], ["cplus"])
        self.assertIn("android", result["parsers_failed"])
        self.assertIn("android", result["parser_errors"])
        self.assertEqual(result["parser_errors"]["android"]["message"], "mocked backend failure")

    async def test_fanout_dispatch_returns_top_level_error_when_all_parsers_fail(self):
        async def bad_parser(payload=None):
            raise ValueError("nope")

        fake_module = types.SimpleNamespace(tool_search_functions=bad_parser)

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=fake_module),
                "android": unified_mcp.BackendInfo(name="android", module=fake_module),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "fanout_failed")
        self.assertIn("android", result["parsers_failed"])
        self.assertIn("cplus", result["parsers_failed"])

    async def test_resolve_fanout_parsers_uses_project_parser_when_registered(self):
        # When project_id is registered, fan-out collapses to the project's
        # single parser_type — no fan-out needed.
        with patch.object(
            unified_mcp,
            "resolve_project_targets",
            return_value=type(
                "T", (), {"parser_type": "spring"}
            )(),
        ):
            parsers, error = unified_mcp._resolve_fanout_parsers("project-x")
        self.assertIsNone(error)
        self.assertEqual(parsers, ["spring"])

    async def test_resolve_fanout_parsers_falls_back_to_all_when_project_unregistered(self):
        # Unregistered project_id → fan-out across every registered query
        # engine (backend representatives), NOT every parser alias.
        with patch.object(
            unified_mcp,
            "resolve_project_targets",
            side_effect=unified_mcp.ProjectNotRegisteredError("missing", []),
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus", "spring"}),
            ):
                parsers, error = unified_mcp._resolve_fanout_parsers("missing")
        self.assertIsNone(error)
        self.assertEqual(parsers, sorted(unified_mcp.BACKENDS.keys()))

    async def test_resolve_fanout_parsers_emits_error_when_no_engines_registered(self):
        with patch.dict(unified_mcp.BACKENDS, {}, clear=True):
            parsers, error = unified_mcp._resolve_fanout_parsers(None)
        self.assertEqual(parsers, [])
        self.assertIsNotNone(error)
        self.assertEqual(error["error"]["type"], "no_query_engines_registered")

    async def test_project_context_tool_scopes_database_to_project_id(self):
        # Each project's topology lives in its own graph (named after the
        # project). When a caller passes project_id without an explicit db,
        # the project-context tools must read from that project's graph, not
        # the server's startup/active graph. Regression for the "empty
        # modules for every project" bug.
        context = (
            "android", ["HAS_DESCRIPTOR"],
            {"canonical_parser": "android", "query_engine": "android_graph"},
            None, None,
        )
        tool = getattr(
            unified_mcp.tool_get_project_modules,
            "fn",
            unified_mcp.tool_get_project_modules,
        )
        bridge = AsyncMock(return_value=[])
        with patch.object(
            unified_mcp,
            "_resolve_direct_capability_context",
            AsyncMock(return_value=context),
        ):
            with patch.dict(os.environ, {"FALKORDB_GRAPH": "cortext"}):
                with patch.object(unified_mcp, "_run_bridge_query", bridge):
                    result = await tool(
                        project_id="digital_key", parser_type="android", limit=50
                    )

        self.assertTrue(result["ok"])
        # The bridge query must target the digital_key graph, not the
        # server's startup/active cortext graph. digital_key is not
        # registered in the harness config, so the resolver must fall back
        # to the code_graph == project_id naming convention rather than
        # leaking the server's env-default graph.
        self.assertEqual(bridge.await_args.args[2], "digital_key")

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

        async def workflow_query(query, _params, _database):
            queries.append(query)
            return []

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
            with patch.object(unified_mcp, "_run_bridge_query", side_effect=workflow_query):
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
            result = await tool(parser_type="python", project_id="graph")

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
            result = await tool(parser_type="python", project_id="graph")

        self.assertEqual(result["effective_support"]["endpoints"], "none")
        self.assertEqual(result["recommended_action"], "run_incremental_sync")
        self.assertIn("endpoints", result["unavailable_dimensions"])

    async def test_inspect_parser_capabilities_rejects_unknown_parser(self):
        tool = getattr(
            unified_mcp.tool_inspect_parser_capabilities,
            "fn",
            unified_mcp.tool_inspect_parser_capabilities,
        )
        result = await tool(parser_type="pyhton", project_id="graph")

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

    def test_semantic_search_proxy_exposes_project_id_only_for_scope(self):
        backend = unified_mcp._resolve_proxy_backend_module("semantic_search")
        raw = getattr(backend, "tool_semantic_search")
        fn = unified_mcp._unwrap_tool_callable(raw)
        params = inspect.signature(fn).parameters
        self.assertIn("project_id", params)
        self.assertNotIn("db", params)
        self.assertNotIn("search_full", params)

    def test_semantic_search_catalog_accepts_project_id(self):
        accepted = unified_mcp._accepted_params("semantic_search")
        self.assertIn("project_id", accepted)
        self.assertNotIn("db", accepted)
        self.assertNotIn("search_full", accepted)

    async def test_semantic_search_without_project_dispatches_unscoped_once(self):
        seen_projects = []

        async def fake_semantic_search(payload=None):
            project_id = (payload or {}).get("project_id")
            seen_projects.append(project_id)
            return {
                "mode": "combined",
                "query": "orders",
                "content_mode": "summary",
                "results": [{
                    "id": "shared-id",
                    "score": 0.9,
                    "payload": {"name": project_id},
                }],
            }

        fake_module = types.SimpleNamespace(
            tool_semantic_search=fake_semantic_search,
        )
        with patch.object(
            unified_mcp,
            "list_registered_projects",
            side_effect=AssertionError(
                "unscoped semantic search must not enumerate the project registry"
            ),
            create=True,
        ), patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(
                    name="cplus", module=fake_module,
                ),
            },
            clear=True,
        ):
            result = await unified_mcp._dispatch_tool(
                "semantic_search",
                {"query": "orders", "top_k": 1},
            )

        self.assertEqual(seen_projects, [None])
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["score"], 0.9)
        self.assertNotIn("projects_searched", result)

    async def test_semantic_search_with_project_uses_single_target(self):
        seen_projects = []

        async def fake_semantic_search(payload=None):
            seen_projects.append((payload or {}).get("project_id"))
            return {"results": []}

        fake_module = types.SimpleNamespace(
            tool_semantic_search=fake_semantic_search,
        )
        with patch.object(
            unified_mcp,
            "list_registered_projects",
            side_effect=AssertionError("scoped search must not enumerate projects"),
            create=True,
        ), patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(
                    name="cplus", module=fake_module,
                ),
            },
            clear=True,
        ):
            result = await unified_mcp._dispatch_tool(
                "semantic_search",
                {"query": "orders", "project_id": "procsample"},
            )

        self.assertEqual(seen_projects, ["procsample"])
        self.assertTrue(result["ok"])
        self.assertNotIn("projects_searched", result)

    async def test_build_tool_error_received_params_exclude_missing_values(self):
        # A param that is empty must not be reported as both "received" and
        # "missing". Regression for the contradiction in the user-facing
        # error payload (project_id appeared in both lists).
        error = unified_mcp._build_tool_error(
            "get_public_apis",
            {"project_id": "", "parser_type": "c++"},
            ValueError("boom"),
        )

        self.assertIn("project_id", error["error"]["missing_required_params"])
        self.assertNotIn("project_id", error["error"]["received_params"])
        self.assertIn("parser_type", error["error"]["received_params"])
        self.assertNotIn("parser_type", error["error"]["missing_required_params"])

    async def test_build_tool_error_example_follows_caller_parser(self):
        # A c++ caller must not be shown a kotlin-flavored retry example.
        error = unified_mcp._build_tool_error(
            "get_public_apis",
            {"project_id": "cortext", "parser_type": "c++"},
            ValueError("boom"),
        )

        example = error["error"]["example"]
        self.assertIn("parser_type='c++'", example)
        self.assertNotIn("kotlin", example)

        # Caller already on the catalog's parser keeps the catalog example.
        same = unified_mcp._build_tool_error(
            "get_public_apis",
            {"project_id": "shop", "parser_type": "kotlin", "language": "kotlin"},
            ValueError("boom"),
        )
        self.assertEqual(
            same["error"]["example"],
            unified_mcp._CATALOG_BY_NAME["get_public_apis"]["example"],
        )

    async def test_project_context_tool_reports_service_error_with_resolved_db(self):
        # When the underlying service raises, the error handler must build a
        # structured error (previously it referenced an undefined ``_db``
        # local and crashed with NameError, masking the real failure).
        context = (
            "c++", ["EXPOSES_API"],
            {"canonical_parser": "cplus"}, None, None,
        )

        class FailingService:
            def __init__(self, _runner):
                pass

            async def get_public_apis(self, **_kwargs):
                raise RuntimeError("mock service failure")

        tool = getattr(
            unified_mcp.tool_get_public_apis,
            "fn",
            unified_mcp.tool_get_public_apis,
        )
        with patch.object(
            unified_mcp,
            "_resolve_direct_capability_context",
            AsyncMock(return_value=context),
        ):
            with patch(
                "services.project_context_service.ProjectContextService",
                FailingService,
            ):
                result = await tool(project_id="cortext", parser_type="c++")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["type"], "tool_execution_error")
        self.assertIn("mock service failure", result["error"]["message"])
        self.assertIn("db", result["error"]["received_params"])

    async def test_explore_graph_returns_tool_error_for_bad_numeric_input(self):
        tool = getattr(
            unified_mcp.tool_explore_graph,
            "fn",
            unified_mcp.tool_explore_graph,
        )

        result = await tool(query="validation", top_k="many")

        self.assertEqual(result["error"]["type"], "invalid_parameters")
        self.assertIn("top_k must be a positive integer", result["error"]["message"])

    # ------------------------------------------------------------------
    # Fan-out contract hardening (plans/260818-1458-fanout-contract-hardening)
    # ------------------------------------------------------------------

    async def test_fanout_breadth_is_per_backend_not_per_alias(self):
        """A parser-less fan-out call dispatches once per physical backend.

        Regression for the 88-alias fan-out that overflowed the admission
        lane: backend representatives must collapse alias sets to the count
        of query engines.
        """
        seen = []

        async def fake_cplus(*args, **kwargs):
            seen.append("cplus")
            return {"db": "cplus_g", "results": [{"id": "c1"}]}

        async def fake_android(*args, **kwargs):
            seen.append("android")
            return {"db": "android_g", "results": [{"id": "a1"}]}

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=types.SimpleNamespace(tool_search_functions=fake_cplus)),
                "android": unified_mcp.BackendInfo(name="android", module=types.SimpleNamespace(tool_search_functions=fake_android)),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"a", "b", "c", "d", "e", "f", "g"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        self.assertEqual(sorted(seen), ["android", "cplus"])
        self.assertEqual(len(seen), len(unified_mcp.BACKENDS))
        self.assertEqual(result["parsers_searched"], ["android", "cplus"])

    async def test_fanout_dedup_collapses_repeated_node_ids(self):
        duplicate = {"id": "shared", "labels": ["Function"], "properties": {"name": "shared"}}
        only_first = {"id": "first-only", "labels": ["Function"], "properties": {"name": "first"}}

        async def fake_cplus(*args, **kwargs):
            return {"db": "cplus_g", "results": [duplicate, only_first]}

        async def fake_android(*args, **kwargs):
            return {"db": "android_g", "results": [duplicate, {"id": "android-only", "labels": ["Function"]}]}

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=types.SimpleNamespace(tool_search_functions=fake_cplus)),
                "android": unified_mcp.BackendInfo(name="android", module=types.SimpleNamespace(tool_search_functions=fake_android)),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        ids = [r["id"] for r in result["results"]]
        self.assertEqual(sorted(ids), ["android-only", "first-only", "shared"])
        self.assertEqual(result["dedup_removed"], 1)
        # First-seen wins for the kept item. Fan-out dispatches in sorted
        # backend order (``["android", "cplus"]``), so android gets there
        # first for this fixture.
        shared = next(r for r in result["results"] if r["id"] == "shared")
        self.assertEqual(shared["parser_type"], "android")

    async def test_fanout_dedup_edges_on_composite_key(self):
        # Android is the first engine in sorted dispatch order, so its
        # a→CALLS→b edge wins first-seen. The duplicate from cplus (with
        # a different ``properties`` payload) is dropped.
        async def fake_android(*args, **kwargs):
            return {
                "db": "android_g",
                "edges": [
                    {"start_id": "a", "type": "CALLS", "end_id": "b", "properties": {"weight": 99}},
                ],
            }

        async def fake_cplus(*args, **kwargs):
            return {
                "db": "cplus_g",
                "edges": [
                    {"start_id": "a", "type": "CALLS", "end_id": "b", "properties": {"weight": 1}},
                    {"start_id": "a", "type": "CALLS", "end_id": "c"},
                ],
            }

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=types.SimpleNamespace(tool_search_functions=fake_cplus)),
                "android": unified_mcp.BackendInfo(name="android", module=types.SimpleNamespace(tool_search_functions=fake_android)),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        edges = result["edges"]
        keys = {(e["start_id"], e["type"], e["end_id"]) for e in edges}
        self.assertEqual(keys, {("a", "CALLS", "b"), ("a", "CALLS", "c")})
        # First-seen wins; the duplicate cplus edge weight=1 is dropped.
        ab = next(e for e in edges if e["end_id"] == "b")
        self.assertEqual(ab["parser_type"], "android")
        self.assertEqual(ab["properties"]["weight"], 99)
        self.assertEqual(result["dedup_removed"], 1)

    async def test_fanout_dedup_ids_key_by_string(self):
        async def fake_cplus(*args, **kwargs):
            return {"db": "cplus_g", "ids": ["n1", "n2", "n3"]}

        async def fake_android(*args, **kwargs):
            return {"db": "android_g", "ids": ["n1", "n3", "n4"]}

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=types.SimpleNamespace(tool_search_functions=fake_cplus)),
                "android": unified_mcp.BackendInfo(name="android", module=types.SimpleNamespace(tool_search_functions=fake_android)),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        self.assertEqual(sorted(result["ids"]), ["n1", "n2", "n3", "n4"])
        self.assertEqual(result["dedup_removed"], 2)

    async def test_fanout_recall_uses_backend_label_union(self):
        """Fan-out predicate must include labels outside the legacy hardcoded set.

        Regression for the recall hole (plan D3): a parser-less backend
        query that fell back to the legacy hardcoded label list would
        silently drop nodes with labels like ``Class`` or ``Service``.
        """
        from cplus.cplus_mcp import (
            _LEGACY_SEARCH_LABELS,
            _search_label_predicate,
        )
        union_labels = {"Class", "Service", "Route", "Controller"}
        fanout_predicate = _search_label_predicate(
            "n", profile_labels=(), fanout=True
        )
        for label in union_labels:
            self.assertIn(f"n:{label}", fanout_predicate, label)
        # Legacy labels remain covered.
        for label in _LEGACY_SEARCH_LABELS:
            self.assertIn(f"n:{label}", fanout_predicate, label)
        # Non-fanout mode also still uses the legacy set (zero behavior
        # change for direct parser-less calls outside fan-out).
        direct_predicate = _search_label_predicate(
            "n", profile_labels=(), fanout=False
        )
        for label in _LEGACY_SEARCH_LABELS:
            self.assertIn(f"n:{label}", direct_predicate, label)
        self.assertNotIn("n:Class", direct_predicate)

    async def test_fanout_strips_parser_type_from_per_engine_payload(self):
        captured: list[dict] = []

        async def fake_cplus(*args, **kwargs):
            payload = kwargs.get("payload") or {}
            captured.append(dict(payload))
            return {"db": "cplus_g", "results": []}

        async def fake_android(*args, **kwargs):
            payload = kwargs.get("payload") or {}
            captured.append(dict(payload))
            return {"db": "android_g", "results": []}

        with patch.dict(
            unified_mcp.BACKENDS,
            {
                "cplus": unified_mcp.BackendInfo(name="cplus", module=types.SimpleNamespace(tool_search_functions=fake_cplus)),
                "android": unified_mcp.BackendInfo(name="android", module=types.SimpleNamespace(tool_search_functions=fake_android)),
            },
        ):
            with patch.object(
                unified_mcp,
                "parser_aliases",
                return_value=frozenset({"android", "cplus"}),
            ):
                result = await unified_mcp._dispatch_tool(
                    "search_functions", {"query": "foo"}
                )

        # No engine receives parser_type; _fanout flag is set.
        self.assertEqual(len(captured), 2)
        for payload in captured:
            self.assertNotIn("parser_type", payload)
            self.assertTrue(payload.get("_fanout"))
        # Dispatch succeeded (no engine errors).
        self.assertEqual(sorted(result["parsers_searched"]), ["android", "cplus"])
        self.assertEqual(result["parsers_failed"], [])


if __name__ == "__main__":
    unittest.main()

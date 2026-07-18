import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "code-tiny" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from framework_registry import (  # noqa: E402
    CAPABILITIES,
    capability_catalog,
    capability_for_parser,
    default_relationships,
    framework_for_parser,
    parser_aliases,
    searchable_labels,
    servlet_active_generation_predicate,
    validate_capability_registry,
)


class FrameworkMcpRoutingTest(unittest.TestCase):
    def test_capability_registry_is_canonical_unique_and_backend_aware(self):
        aliases = validate_capability_registry()
        self.assertEqual(len(aliases), len(parser_aliases()))
        self.assertEqual(capability_for_parser("android-kotlin").backend, "android")
        self.assertEqual(capability_for_parser("asp.net-core").backend, "cplus")
        self.assertEqual(capability_for_parser("java").name, "jvm")
        self.assertEqual(capability_for_parser("perl").support_level, "generic")
        self.assertEqual(
            {item["canonical_parser"] for item in capability_catalog()},
            set(CAPABILITIES),
        )

    def test_primary_language_analyzers_have_query_capabilities(self):
        expected = {
            "python": "python",
            "fastapi": "python",
            "django": "python",
            "js": "javascript",
            "javascript": "javascript",
            "ts": "typescript",
            "typescript": "typescript",
            "express": "typescript",
            "nestjs": "typescript",
            "php": "php",
            "laravel": "php",
            "csharp": "csharp",
            "c#": "csharp",
            "sql": "sql",
            "plsql": "plsql",
        }
        for alias, canonical in expected.items():
            with self.subTest(alias=alias):
                capability = capability_for_parser(alias)
                self.assertIsNotNone(capability)
                self.assertEqual(capability.name, canonical)

    def test_web_capabilities_expose_endpoint_graph_contracts(self):
        for parser in ("fastapi", "django", "express", "nestjs", "laravel"):
            with self.subTest(parser=parser):
                capability = capability_for_parser(parser)
                self.assertIn("endpoint_queries", capability.features)
                self.assertTrue(
                    {"ApiEndpoint", "HttpEndpoint"} & set(capability.labels),
                    capability.labels,
                )
                self.assertIn("HANDLES", default_relationships(parser, "get_api_call_chain"))

        typescript = capability_for_parser("typescript")
        self.assertIn("ApiCall", typescript.labels)
        self.assertIn("CALLS_API", default_relationships("typescript", "find_callers_of_endpoint"))
        self.assertIn("MATCHES", default_relationships("typescript", "find_callers_of_endpoint"))

    def test_framework_aliases_resolve_without_losing_core_relationships(self):
        self.assertEqual(framework_for_parser("spring-boot").name, "spring")
        self.assertEqual(framework_for_parser("servlet").name, "servlet_jsp")
        self.assertEqual(framework_for_parser("my-batis").name, "mybatis")
        self.assertEqual(framework_for_parser("struts2").name, "struts")
        self.assertEqual(framework_for_parser("dart").name, "flutter")
        self.assertEqual(framework_for_parser("aspnet-core").name, "aspnet_core")
        self.assertEqual(framework_for_parser("asp.net-framework").name, "aspnet_framework")
        self.assertIn("spring_boot", parser_aliases())
        self.assertIn("apache-struts", parser_aliases())
        self.assertIn("flutter", parser_aliases())

        spring_relationships = default_relationships("spring")
        self.assertIn("CALLS", spring_relationships)
        self.assertIn("SEMANTIC_OF", spring_relationships)
        self.assertIn("DECLARES_QUERY", spring_relationships)
        self.assertEqual(len(spring_relationships), len(set(spring_relationships)))

    def test_searchable_labels_and_servlet_freshness_predicate_cover_framework_nodes(self):
        self.assertIn("ApiEndpoint", searchable_labels("spring"))
        self.assertIn("JSPView", searchable_labels("servlet_jsp"))
        self.assertIn("MyBatisStatement", searchable_labels("mybatis"))
        self.assertIn("Action", searchable_labels("struts"))
        self.assertIn("Function", searchable_labels("flutter"))
        self.assertIn("HttpEndpoint", searchable_labels("aspnet_core"))
        self.assertIn("WebFormPage", searchable_labels("aspnet_framework"))

        with patch.dict("os.environ", {"CODE_GRAPH_PROVIDER": "neo4j"}):
            predicate = servlet_active_generation_predicate("node")
        self.assertIn("ServletJspAnalysisState", predicate)
        self.assertIn("state.active_generation = node.generation_id", predicate)
        self.assertIn("node.project_id", predicate)

    def test_falkordb_freshness_predicate_avoids_exists_subquery(self):
        with patch.dict("os.environ", {"CODE_GRAPH_PROVIDER": "falkordb"}):
            predicate = servlet_active_generation_predicate("node")

        self.assertEqual(predicate, "true")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
for path in (str(CODE_TINY), str(MCP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from framework_registry import (  # noqa: E402
    CAPABILITIES,
    SUPPORT_DIMENSIONS,
    evaluate_capability_schema,
)
from tools.database_schema.pipeline import analyze_project as analyze_database  # noqa: E402
from tools.sync.incremental_sync import ANALYZERS  # noqa: E402
from tools.web_framework.pipeline import analyze_project as analyze_web  # noqa: E402


def _row(symbols, calls, endpoints, database, evidence):
    return {
        "support": dict(zip(SUPPORT_DIMENSIONS, (symbols, calls, endpoints, database))),
        "evidence": evidence,
    }


# This is deliberately independent from framework_registry. A registry change must
# update this acceptance contract and its evidence rather than silently changing
# what MCP advertises.
ACCEPTANCE_MATRIX = {
    "android": _row("full", "full", "none", "none", "tests/test_common_analyzer_registry.py"),
    "cplus": _row("full", "full", "none", "none", "tests/test_cplus_windows_resource_parser.py"),
    "python": _row("full", "partial", "partial", "none", "tests/fixtures/web-framework-application/python"),
    "javascript": _row("full", "partial", "partial", "none", "tests/fixtures/web-framework-application/js"),
    "typescript": _row("full", "full", "none", "none", "tests/test_primary_analyzer_vector_contract.py"),
    "php": _row("full", "partial", "partial", "none", "tests/fixtures/web-framework-application/php"),
    "csharp": _row("full", "full", "none", "none", "tests/test_aspnet_fixture_analysis.py"),
    "sql": _row("full", "none", "none", "full", "tests/fixtures/database-schema-application/schema.sql"),
    "plsql": _row("full", "none", "none", "full", "tests/fixtures/database-schema-application/audit.pkb"),
    "jvm": _row("generic", "generic", "none", "none", "tests/test_framework_fixture_analysis.py"),
    "go": _row("generic", "generic", "none", "none", "tests/test_primary_analyzer_vector_contract.py"),
    "perl": _row("generic", "generic", "none", "none", "tests/test_perl_parser.py"),
    "shell": _row("generic", "generic", "none", "none", "tests/test_shell_parser.py"),
    "jp1": _row("generic", "generic", "none", "none", "tests/test_jp1_parser.py"),
    "rust": _row("generic", "generic", "none", "none", "tests/test_primary_analyzer_vector_contract.py"),
    "swift": _row("generic", "generic", "none", "none", "tests/test_primary_analyzer_vector_contract.py"),
    "delphi": _row("generic", "generic", "none", "none", "tests/test_common_analyzer_registry.py"),
    "vbnet": _row("generic", "generic", "none", "none", "tests/test_common_analyzer_registry.py"),
    "visual_basic": _row("generic", "generic", "none", "none", "tests/test_common_analyzer_registry.py"),
    "cobol": _row("full", "full", "none", "none", "tests/test_cobol_fixture_analysis.py"),
    "spring": _row("full", "full", "full", "full", "tests/test_framework_fixture_analysis.py"),
    "servlet_jsp": _row("full", "full", "full", "none", "tests/test_framework_fixture_analysis.py"),
    "mybatis": _row("full", "full", "none", "full", "tests/test_framework_fixture_analysis.py"),
    "struts": _row("full", "full", "full", "full", "tests/test_struts_scan_filtering.py"),
    "flutter": _row("full", "full", "none", "none", "tests/test_dart_fixture_analysis.py"),
    "aspnet_framework": _row("full", "full", "full", "full", "tests/test_aspnet_fixture_analysis.py"),
    "aspnet_core": _row("full", "full", "full", "full", "tests/test_aspnet_fixture_analysis.py"),
}


PRIMARY_TO_PROFILE = {
    "cobol": "cobol", "dart": "flutter", "cplus": "cplus", "delphi": "delphi",
    "java": "jvm", "kotlin": "jvm", "android": "android", "vbnet": "vbnet",
    "vb6": "visual_basic", "vba": "visual_basic", "vbscript": "visual_basic",
    "python": "python", "go": "go", "perl": "perl", "shell": "shell", "jp1": "jp1", "rust": "rust",
    "swift": "swift", "js": "javascript", "ts": "typescript", "php": "php",
    "csharp": "csharp", "sql": "sql", "plsql": "plsql",
}


class McpAcceptanceMatrixTest(unittest.TestCase):
    def test_every_advertised_dimension_has_a_satisfiable_schema_contract(self):
        for profile, capability in CAPABILITIES.items():
            with self.subTest(profile=profile):
                evaluation = evaluate_capability_schema(
                    capability,
                    available_labels=capability.labels,
                    available_relationships=capability.relationships_for(),
                )
                for dimension, advertised in capability.support.items():
                    expected = "none" if advertised == "none" else advertised
                    self.assertEqual(
                        evaluation["dimensions"][dimension]["effective"],
                        expected,
                        evaluation["dimensions"][dimension],
                    )

    def test_every_advertised_profile_has_an_independent_matrix_row(self):
        self.assertEqual(set(ACCEPTANCE_MATRIX), set(CAPABILITIES))
        for profile, row in ACCEPTANCE_MATRIX.items():
            with self.subTest(profile=profile):
                self.assertEqual(row["support"], dict(CAPABILITIES[profile].support))
                self.assertTrue((ROOT / row["evidence"]).exists(), row["evidence"])

    def test_every_primary_language_parser_maps_to_a_profile_and_real_entrypoint(self):
        self.assertEqual(set(PRIMARY_TO_PROFILE), set(ANALYZERS))
        for parser, profile in PRIMARY_TO_PROFILE.items():
            with self.subTest(parser=parser):
                self.assertIn(profile, ACCEPTANCE_MATRIX)
                self.assertTrue(Path(ANALYZERS[parser].script_path).is_file())

    def test_web_rows_are_backed_by_extracted_endpoint_and_handler_facts(self):
        fixture = ROOT / "tests" / "fixtures" / "web-framework-application"
        result = analyze_web(
            fixture, "acceptance", ("fastapi", "django", "express_js", "laravel")
        )
        frameworks = {endpoint.framework for endpoint in result.endpoints}
        node_rows, relationship_rows = result.graph_rows()

        self.assertTrue({"fastapi", "django", "express_js", "laravel"}.issubset(frameworks))
        self.assertTrue(node_rows)
        self.assertTrue(all({"id", "http_method", "path", "handler_name"} <= set(row) for row in node_rows))
        self.assertTrue(relationship_rows)
        self.assertTrue(all(row["type"] == "HANDLES" for row in relationship_rows))

    def test_database_rows_are_backed_by_extracted_schema_and_lineage_facts(self):
        fixture = ROOT / "tests" / "fixtures" / "database-schema-application"
        result = analyze_database(fixture, "acceptance", ("sql", "plsql"))
        node_rows, relationship_rows = result.graph_rows()

        self.assertTrue({"Table", "View", "Procedure"}.issubset({row["label"] for row in node_rows}))
        self.assertTrue(
            {"READS_FROM", "WRITES_TO", "REFERENCES_TABLE"}.issubset(
                {row["type"] for row in relationship_rows}
            )
        )


if __name__ == "__main__":
    unittest.main()

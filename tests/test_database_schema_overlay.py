import asyncio
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.database_schema.pipeline import analyze_project  # noqa: E402
from tools.database_schema.database_schema_analyzer import main as overlay_main  # noqa: E402
from tools.graph.writer.database_schema_writer import DatabaseSchemaWriter  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "database-schema-application"


class RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, params, database=None):
        self.calls.append((query, params, database))
        return [{"count": len(params.get("rows", []))}], None, None


class DatabaseSchemaOverlayTest(unittest.TestCase):
    def test_cli_dry_run_emits_fixture_backed_database_rows(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = overlay_main([
                "--root", str(FIXTURE), "--project-id", "fixture",
                "--dialect", "sql", "--dry-run",
            ])
        payload = json.loads(output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["nodes"])
        self.assertTrue(payload["relationships"])

    def test_sql_extracts_objects_and_read_write_lineage(self):
        result = analyze_project(FIXTURE, "fixture", ("sql",))
        objects = {(item.label, item.name) for item in result.objects}
        relationships = {(item.rel_type, item.source_name, item.target_name) for item in result.relationships}

        self.assertIn(("Table", "customers"), objects)
        self.assertIn(("View", "active_customers"), objects)
        self.assertIn(("Procedure", "refresh_orders"), objects)
        self.assertIn(("READS_FROM", "active_customers", "customers"), relationships)
        self.assertIn(("WRITES_TO", "refresh_orders", "order_archive"), relationships)
        self.assertIn(("WRITES_TO", "refresh_orders", "sessions"), relationships)
        self.assertIn(("REFERENCES_TABLE", "refresh_orders", "orders"), relationships)

    def test_plsql_extracts_schema_qualified_semantics(self):
        result = analyze_project(FIXTURE, "fixture", ("plsql",))
        relationships = {(item.rel_type, item.source_name, item.target_name) for item in result.relationships}

        self.assertIn(("READS_FROM", "user_roles", "users"), relationships)
        self.assertIn(("READS_FROM", "user_roles", "roles"), relationships)
        self.assertIn(("WRITES_TO", "log_user", "audit_log"), relationships)
        self.assertIn(("WRITES_TO", "log_user", "users"), relationships)

    def test_writer_persists_allowlisted_database_contract(self):
        result = analyze_project(FIXTURE, "fixture", ("sql", "plsql"))
        node_rows, relationship_rows = result.graph_rows()
        driver = RecordingDriver()
        summary = asyncio.run(
            DatabaseSchemaWriter(driver, database="fixture").write_all(
                node_rows=node_rows,
                relationship_rows=relationship_rows,
            )
        )

        self.assertEqual(summary["nodes"], len(node_rows))
        self.assertEqual(summary["relationships"], len(relationship_rows))
        combined_queries = "\n".join(query for query, _, _ in driver.calls)
        self.assertIn("MERGE (node:Table", combined_queries)
        self.assertIn("MERGE (source)-[rel:READS_FROM", combined_queries)
        self.assertIn("MERGE (source)-[rel:WRITES_TO", combined_queries)
        self.assertIn("MERGE (source)-[rel:REFERENCES_TABLE", combined_queries)


if __name__ == "__main__":
    unittest.main()

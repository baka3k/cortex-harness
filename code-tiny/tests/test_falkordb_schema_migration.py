import unittest

from scripts.setup_constraints import (
    apply_falkordb_schema,
    ensure_falkordb_unique_constraint,
    parse_fulltext_index,
    parse_range_index,
    parse_unique_constraint,
)
from scripts.setup_graph_project import setup_project_graph_with_driver
from tools.graph.core.base import GraphProvider


class _FakeGraph:
    def __init__(self, statuses=None):
        self.calls = []
        self.statuses = list(statuses or ["OPERATIONAL"])
        self.constraints = []

    def create_node_range_index(self, label, *properties):
        self.calls.append(("range", label, properties))

    def create_node_unique_constraint(self, label, *properties):
        self.calls.append(("unique", label, properties))
        self.constraints.append((label, properties))

    def create_node_fulltext_index(self, label, *properties):
        self.calls.append(("fulltext", label, properties))

    def list_constraints(self):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return [
            {
                "type": "UNIQUE",
                "label": label,
                "properties": list(properties),
                "entitytype": "NODE",
                "status": status,
            }
            for label, properties in self.constraints
        ]


class _FakeDriver:
    def __init__(self, graph):
        self.graph = graph
        self.queries = []

    def execute_query_sync(self, query, parameters=None, database=None):
        self.queries.append((query, parameters or {}, database))
        return [], [], None


class FalkorDBSchemaMigrationTests(unittest.TestCase):
    def test_schema_ddl_parsers(self):
        self.assertEqual(
            parse_range_index("CREATE INDEX x IF NOT EXISTS FOR (n:File) ON (n.project_id, n.id)"),
            ("File", ("project_id", "id")),
        )
        self.assertEqual(
            parse_unique_constraint(
                "CREATE CONSTRAINT x IF NOT EXISTS FOR (n:Project) REQUIRE n.project_id IS UNIQUE"
            ),
            ("Project", ("project_id",)),
        )
        self.assertEqual(
            parse_fulltext_index(
                "CREATE FULLTEXT INDEX x IF NOT EXISTS FOR (n:Function|Class) ON EACH [n.name, n.summary]"
            ),
            (("Function", "Class"), ("name", "summary")),
        )

    def test_unique_constraint_waits_until_operational(self):
        graph = _FakeGraph(["UNDER CONSTRUCTION", "OPERATIONAL"])
        result = ensure_falkordb_unique_constraint(
            graph,
            "Project",
            ("project_id",),
            timeout=1,
            interval=0,
        )
        self.assertEqual(result["status"], "OPERATIONAL")
        self.assertEqual(
            graph.calls[:2],
            [
                ("range", "Project", ("project_id",)),
                ("unique", "Project", ("project_id",)),
            ],
        )

    def test_failed_constraint_is_reported(self):
        graph = _FakeGraph(["FAILED"])
        with self.assertRaisesRegex(RuntimeError, "failed with status"):
            ensure_falkordb_unique_constraint(
                graph,
                "Project",
                ("project_id",),
                timeout=0,
                interval=0,
            )

    def test_schema_expands_multilabel_fulltext(self):
        graph = _FakeGraph()
        summary = apply_falkordb_schema(
            _FakeDriver(graph),
            constraint_statements=[
                (
                    "unique_project",
                    "CREATE CONSTRAINT x IF NOT EXISTS FOR (n:Project) REQUIRE n.project_id IS UNIQUE",
                )
            ],
            index_statements=[
                ("file_idx", "CREATE INDEX x IF NOT EXISTS FOR (n:File) ON (n.id)")
            ],
            fulltext_statements=[
                (
                    "symbol_ft",
                    "CREATE FULLTEXT INDEX x IF NOT EXISTS FOR (n:Function|Class) ON EACH [n.name]",
                )
            ],
            constraint_timeout=0,
            poll_interval=0,
        )
        self.assertEqual(summary, {"constraints": 1, "indexes": 1, "fulltext_indexes": 2})
        self.assertIn(("fulltext", "Function", ("name",)), graph.calls)
        self.assertIn(("fulltext", "Class", ("name",)), graph.calls)

    def test_project_setup_uses_falkordb_constraints_and_shared_query(self):
        graph = _FakeGraph()
        driver = _FakeDriver(graph)
        setup_project_graph_with_driver(
            driver,
            provider=GraphProvider.FALKORDB,
            project_id="alpha",
            project_name="Alpha Project",
            repo_name="alpha/repo",
            database="alpha_graph",
            constraint_timeout=0,
            poll_interval=0,
        )
        self.assertEqual([call[1] for call in graph.calls if call[0] == "unique"], ["Project", "Repository"])
        self.assertEqual(driver.queries[0][1]["project_slug"], "alpha-project")
        self.assertEqual(driver.queries[0][2], "alpha_graph")


if __name__ == "__main__":
    unittest.main()

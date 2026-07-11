import argparse
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(REPO_ROOT, "code-tiny")
if CODE_TINY not in sys.path:
    sys.path.insert(0, CODE_TINY)

from tools.graph.core.base import GraphProvider
from tools.sync import incremental_sync


class FakeGraphDriver:
    def __init__(self):
        self.indexes = []
        self.queries = []
        self.closed = False

    async def create_indexes(self, indexes, database=None):
        self.indexes.append((indexes, database))

    async def execute_query(self, query, parameters=None, database=None):
        self.queries.append((query, parameters, database))
        return [], [], None

    def close(self):
        self.closed = True


class IncrementalSyncGraphSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_falkordb_setup_uses_graph_driver_not_neo4j_subprocess(self):
        driver = FakeGraphDriver()
        args = argparse.Namespace(
            graph_provider="falkordb",
            falkordb_uri="redis://localhost:6379",
            falkordb_host="localhost",
            falkordb_port=6379,
            falkordb_user="",
            falkordb_password="",
            falkordb_graph="cortext",
            falkordb_ssl=False,
            neo4j_db="cortext",
        )

        async def fake_create_driver(provider, config):
            self.assertEqual(provider, GraphProvider.FALKORDB)
            self.assertEqual(config["uri"], "redis://localhost:6379")
            self.assertEqual(config["graph"], "cortext")
            return driver

        with tempfile.TemporaryDirectory() as root:
            with patch.object(
                incremental_sync.GraphDriverFactory,
                "create_driver",
                side_effect=fake_create_driver,
            ), patch.object(
                incremental_sync.subprocess,
                "run",
                side_effect=AssertionError("Neo4j setup subprocess should not run"),
            ):
                await incremental_sync._ensure_project_repository_graph(
                    args=args,
                    root=root,
                    project_id="cortext",
                    project_name="Cortext Project",
                )

        self.assertTrue(driver.closed)
        self.assertEqual(
            driver.indexes[0][0],
            [
                {"label": "Project", "property": "project_id"},
                {"label": "Repository", "property": "name"},
            ],
        )
        query, params, database = driver.queries[0]
        self.assertIn("MERGE (p:Project", query)
        self.assertEqual(params["project_id"], "cortext")
        self.assertEqual(params["project_slug"], "cortext-project")
        self.assertEqual(params["repo_name"], f"Cortext Project/{os.path.basename(root)}")
        self.assertEqual(database, "cortext")


if __name__ == "__main__":
    unittest.main()

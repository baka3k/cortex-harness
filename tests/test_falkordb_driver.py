import os
import sys
import unittest
from unittest.mock import patch


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(REPO_ROOT, "code-tiny")
if CODE_TINY not in sys.path:
    sys.path.insert(0, CODE_TINY)

from tools.graph.core.base import GraphProvider
from tools.graph.core.factory import GraphDriverFactory


class FakeNode:
    labels = ["Function"]

    def __init__(self, properties):
        self.properties = properties


class FakeRelationship:
    relation = "CALLS"
    src_node = 1
    dest_node = 2

    def __init__(self, properties):
        self.properties = properties


class FakeQueryResult:
    header = [["n", 8], ["r", 7], ["count", 3]]

    def __init__(self):
        self.result_set = [
            [
                FakeNode({"id": "fn-1", "name": "main"}),
                FakeRelationship({"weight": 1.0}),
                1,
            ]
        ]


class FakeGraph:
    name = "test_graph"

    def __init__(self):
        self.calls = []

    def query(self, query, params=None):
        self.calls.append((query, params))
        return FakeQueryResult()


class FakeFalkorDB:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.selected = []
        self.graph = FakeGraph()

    @classmethod
    def from_url(cls, url, **kwargs):
        instance = cls(**kwargs)
        instance.url = url
        return instance

    def select_graph(self, graph):
        self.selected.append(graph)
        return self.graph

    def list_graphs(self):
        return ["test_graph"]

    def close(self):
        self.closed = True


class FalkorDBDriverTests(unittest.IsolatedAsyncioTestCase):
    async def test_factory_creates_falkordb_driver(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {
                    "uri": "falkor://localhost:6379",
                    "database": "test_graph",
                    "user": "default",
                    "password": "secret",
                },
            )

        self.assertEqual(driver.provider, GraphProvider.FALKORDB)
        self.assertEqual(driver.database, "test_graph")

    async def test_execute_query_normalizes_falkordb_rows_to_dict_records(self):
        with patch("falkordb.FalkorDB", FakeFalkorDB):
            driver = await GraphDriverFactory.create_driver(
                GraphProvider.FALKORDB,
                {"host": "localhost", "port": 6379, "database": "test_graph"},
            )

        records, keys, summary = driver.execute_query_sync(
            "MATCH (n) RETURN n",
            {"limit": 1},
        )

        self.assertEqual(keys, ["n", "r", "count"])
        self.assertEqual(records[0]["n"], {"id": "fn-1", "name": "main"})
        self.assertEqual(records[0]["r"]["_type"], "CALLS")
        self.assertEqual(records[0]["r"]["_start_id"], 1)
        self.assertEqual(records[0]["r"]["_end_id"], 2)
        self.assertEqual(records[0]["count"], 1)
        self.assertIsInstance(summary, FakeQueryResult)


if __name__ == "__main__":
    unittest.main()

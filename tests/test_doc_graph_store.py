import os
import sys
import unittest
from argparse import Namespace
from unittest.mock import patch


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOC_TINY = os.path.join(REPO_ROOT, "doc-tiny")
if DOC_TINY not in sys.path:
    sys.path.insert(0, DOC_TINY)

from graph_store import (
    FalkorDBResult,
    FalkorDBSession,
    create_graph_store_from_args,
    normalize_provider,
)


class FakeDriver:
    def __init__(self):
        self.calls = []

    def execute_query_sync(self, query, parameters=None):
        self.calls.append((query, parameters))
        return [{"id": "entity-1"}], ["id"], object()


class FakeGraph:
    def query(self, query, params=None):
        raise AssertionError("not used")


class FakeFalkorDB:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def select_graph(self, graph):
        self.selected_graph = graph
        return FakeGraph()

    def close(self):
        pass


class DocGraphStoreTests(unittest.TestCase):
    def test_normalize_provider_accepts_falkor_alias(self):
        self.assertEqual(normalize_provider("falkor"), "falkordb")
        self.assertEqual(normalize_provider("falkordb"), "falkordb")
        self.assertEqual(normalize_provider("neo4j"), "neo4j")

    def test_falkordb_session_merges_parameters_and_kwargs(self):
        driver = FakeDriver()
        session = FalkorDBSession(driver)

        result = session.run("RETURN $id", {"id": "base"}, limit=1)

        self.assertIsInstance(result, FalkorDBResult)
        self.assertEqual(result.single(), {"id": "entity-1"})
        self.assertEqual(driver.calls[0], ("RETURN $id", {"id": "base", "limit": 1}))

    def test_create_graph_store_from_args_can_select_falkordb(self):
        args = Namespace(
            graph_provider="falkordb",
            falkordb_uri=None,
            falkordb_host="localhost",
            falkordb_port=6379,
            falkordb_user=None,
            falkordb_pass="",
            falkordb_graph="docs",
            falkordb_ssl=False,
            neo4j_uri="bolt://localhost:7687",
            neo4j_user="neo4j",
            neo4j_pass="password",
        )

        with patch("falkordb.FalkorDB", FakeFalkorDB):
            store = create_graph_store_from_args(args)

        self.assertEqual(store.provider, "falkordb")


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY_DIR = ROOT / "code-tiny"
MCP_DIR = CODE_TINY_DIR / "mcp"
for path in (CODE_TINY_DIR, MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services.explore_service import _make_graph_driver  # noqa: E402
from tools.common.graph_expander import GraphExpander  # noqa: E402
from tools.common.intelligent_retrieval import (  # noqa: E402
    IntelligentRetrievalEngine,
    _graph_keyword_search,
)
from tools.graph import GraphProvider  # noqa: E402


class SessionlessFalkorLikeDriver:
    def __init__(self):
        self.queries = []

    def session(self, **kwargs):
        raise AssertionError("FalkorDB driver does not expose Neo4j sessions")

    def execute_query_sync(self, query, parameters=None, database=None):
        parameters = parameters or {}
        self.queries.append((query, parameters, database))

        if "RETURN n LIMIT" in query:
            return (
                [
                    {
                        "n": {
                            "id": "seed-function",
                            "name": "searchKeyword",
                            "qualified_name": "pkg.searchKeyword",
                            "kind": "Function",
                            "file_path": "src/search.py",
                        }
                    }
                ],
                ["n"],
                None,
            )

        if "MATCH (seed)-" in query:
            return (
                [
                    {
                        "node_id": "neighbor-function",
                        "name": "neighbor",
                        "qualified_name": "pkg.neighbor",
                        "kind": "Function",
                        "file_path": "src/neighbor.py",
                        "doc_confidence": 0.8,
                    }
                ],
                [],
                None,
            )

        return ([], [], None)


class ExploreGraphFalkorCompatTest(unittest.IsolatedAsyncioTestCase):
    async def test_make_graph_driver_selects_falkordb_for_redis_uri(self):
        fake_driver = object()
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "tools.graph.GraphDriverFactory.create_driver",
                new=AsyncMock(return_value=fake_driver),
            ) as create_driver:
                driver = await _make_graph_driver(
                    "redis://localhost:6379",
                    "",
                    "",
                    "code_graph",
                )

        self.assertIs(driver, fake_driver)
        provider, config = create_driver.await_args.args
        self.assertEqual(provider, GraphProvider.FALKORDB)
        self.assertEqual(config["uri"], "redis://localhost:6379")
        self.assertEqual(config["database"], "code_graph")

    async def test_make_graph_driver_ignores_legacy_bolt_uri_when_provider_is_falkor(self):
        fake_driver = object()
        with patch.dict(os.environ, {}, clear=True):
            with patch("services.explore_service._DEFAULT_GRAPH_PROVIDER", "falkordb"):
                with patch(
                    "tools.graph.GraphDriverFactory.create_driver",
                    new=AsyncMock(return_value=fake_driver),
                ) as create_driver:
                    driver = await _make_graph_driver(
                        "bolt://localhost:7687",
                        "",
                        "",
                        "code_graph",
                    )

        self.assertIs(driver, fake_driver)
        provider, config = create_driver.await_args.args
        self.assertEqual(provider, GraphProvider.FALKORDB)
        self.assertIsNone(config["uri"])
        self.assertEqual(config["host"], "localhost")
        self.assertEqual(config["port"], 6379)
        self.assertEqual(config["database"], "code_graph")

    def test_keyword_search_uses_graph_driver_execute_query_sync(self):
        driver = SessionlessFalkorLikeDriver()

        results = _graph_keyword_search(driver, "search keyword", "code_graph", 5)

        self.assertEqual(results[0]["id"], "seed-function")
        self.assertEqual(driver.queries[0][2], "code_graph")

    def test_graph_expander_uses_graph_driver_execute_query_sync(self):
        driver = SessionlessFalkorLikeDriver()
        expander = GraphExpander(driver, database="code_graph")

        nodes = expander.expand(
            ["seed-function"],
            rel_types=["CALLS"],
            include_seeds=False,
        )

        self.assertEqual(nodes[0].node_id, "neighbor-function")
        self.assertEqual(nodes[0].graph_proximity, 0.8)

    def test_intelligent_retrieval_expands_from_falkor_keyword_seed(self):
        driver = SessionlessFalkorLikeDriver()
        engine = IntelligentRetrievalEngine(
            graph_driver=driver,
            embedder=None,
            collection="",
            database="code_graph",
        )

        results = engine.search("search keyword", top_k=5, expand_graph=True)
        ids = {result.node_id for result in results}

        self.assertIn("seed-function", ids)
        self.assertIn("neighbor-function", ids)


if __name__ == "__main__":
    unittest.main()

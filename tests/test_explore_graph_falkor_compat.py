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

from services import explore_service as explore_service_module  # noqa: E402
from services.explore_service import ExploreService, _make_graph_driver  # noqa: E402
from tools.common.graph_expander import GraphExpander  # noqa: E402
from tools.common.intelligent_retrieval import (  # noqa: E402
    IntelligentRetrievalEngine,
    _graph_keyword_search,
)
from tools.graph import GraphProvider  # noqa: E402
from tools.graph.core.shared_runtime import reset_shared_graph_drivers  # noqa: E402


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

        if "MATCH p = (seed)-" in query:
            return (
                [
                    {
                        "seed_id": "seed-function",
                        "seed_ids": ["seed-function"],
                        "hops": 2,
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
    def setUp(self):
        reset_shared_graph_drivers()

    def tearDown(self):
        reset_shared_graph_drivers()

    def test_explore_defaults_match_unified_falkordb_runtime(self):
        self.assertEqual(explore_service_module._DEFAULT_GRAPH_PROVIDER, "falkordb")
        self.assertEqual(explore_service_module._DEFAULT_GRAPH_DB, "hyper_graph")
        service = ExploreService()
        self.assertEqual(service._graph_provider, "falkordb")
        self.assertEqual(service._neo4j_db, "hyper_graph")

    async def test_make_graph_driver_uses_local_path_and_ignores_legacy_redis_uri(self):
        fake_driver = object()
        local_path = str(ROOT / "storage-test" / "code" / "data.rdb")
        with patch.dict(os.environ, {"FALKORDB_PATH": local_path}, clear=True):
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
        self.assertEqual(config["path"], local_path)
        self.assertEqual(config["database"], "code_graph")
        self.assertFalse({"uri", "host", "port", "user", "password"} & config.keys())

    async def test_make_graph_driver_ignores_legacy_bolt_uri_when_provider_is_falkor(self):
        fake_driver = object()
        local_path = str(ROOT / "storage-test" / "code" / "data.rdb")
        with patch.dict(os.environ, {"FALKORDB_PATH": local_path}, clear=True):
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
        self.assertEqual(config["path"], local_path)
        self.assertEqual(config["database"], "code_graph")
        self.assertFalse({"uri", "host", "port", "user", "password"} & config.keys())

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
        self.assertEqual(nodes[0].hop_distance, 2)
        self.assertEqual(nodes[0].graph_proximity, 0.6)
        self.assertEqual(nodes[0].properties["seed_ids"], ["seed-function"])

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

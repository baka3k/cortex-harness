import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
for path in (CODE_TINY, MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from services import explore_service as explore_service_module  # noqa: E402
from services.explore_service import ExploreService  # noqa: E402
from tools.common.graph_expander import AsyncGraphExpander, GraphExpander  # noqa: E402
from tools.common import intelligent_retrieval as ir  # noqa: E402


class _GraphDriver:
    def __init__(self):
        self.queries = []

    def execute_query_sync(self, query, parameters=None, database=None):
        self.queries.append((query, dict(parameters or {}), database))
        if "RETURN n LIMIT" in query:
            return ([{"n": {"id": "seed", "project_id": "project-a"}}], [], None)
        if "MATCH (seed)-" in query:
            return ([{
                "node_id": "neighbor",
                "project_id": "project-a",
                "name": "neighbor",
            }], [], None)
        return ([], [], None)


def _candidate(node_id, project_id, score=0.9):
    return ir._qdrant_hit_to_candidate(
        {
            "id": node_id,
            "score": score,
            "payload": {"symbol_id": node_id, "project_id": project_id, "name": node_id},
        },
        score,
    )


class ExploreProjectScopeTests(unittest.TestCase):
    def test_embedder_falls_back_to_semantic_backend_for_remote_code_models(self):
        with patch.dict(sys.modules, {"sentence_transformers": None}), patch(
            "cplus.cplus_mcp._embed_query",
            return_value=[0.1, 0.2],
        ) as backend_embed:
            embedder = explore_service_module._make_embedder("jinaai/jina-embeddings-v3")

            self.assertIsNotNone(embedder)
            self.assertEqual(embedder("orders"), [0.1, 0.2])
            backend_embed.assert_called_once_with(
                "orders", "jinaai/jina-embeddings-v3",
            )

    def test_keyword_search_filters_in_graph_query(self):
        driver = _GraphDriver()

        ir._graph_keyword_search(
            driver, "seed", "cortext", 5, project_id="project-a",
        )

        query, params, database = driver.queries[0]
        self.assertIn("n.project_id = $project_id", query)
        self.assertEqual(params["project_id"], "project-a")
        self.assertEqual(database, "cortext")

    def test_graph_expansion_filters_seed_and_neighbor_projects(self):
        driver = _GraphDriver()
        expander = GraphExpander(driver, database="cortext")

        nodes = expander.expand(
            ["seed"], rel_types=["CALLS"], include_seeds=False,
            project_id="project-a",
        )

        self.assertEqual([node.node_id for node in nodes], ["neighbor"])
        query, params, database = driver.queries[0]
        self.assertIn("seed.project_id = $project_id", query)
        self.assertIn("neighbor.project_id = $project_id", query)
        self.assertEqual(params["project_id"], "project-a")
        self.assertEqual(database, "cortext")

    def test_scoped_search_drops_foreign_candidates_and_bm25_only_hits(self):
        class _Bm25:
            def score(self, _query):
                return {"scoped": 0.7, "bm25-foreign": 1.0}

        engine = ir.IntelligentRetrievalEngine(bm25_ranker=_Bm25())
        with patch.object(
            engine,
            "_retrieve_qdrant",
            return_value=[
                _candidate("scoped", "project-a"),
                _candidate("foreign", "project-b"),
            ],
        ), patch.object(engine, "_retrieve_keyword", return_value=[]):
            results = engine.search(
                "orders", project_id="project-a", expand_graph=False,
            )

        self.assertEqual([result.node_id for result in results], ["scoped"])


class ExploreProjectScopeAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_graph_expansion_uses_same_project_predicates(self):
        class _AsyncDriver:
            def __init__(self):
                self.queries = []

            async def execute_query(self, query, parameters=None, database=None):
                self.queries.append((query, dict(parameters or {}), database))
                return ([{
                    "node_id": "neighbor",
                    "project_id": "project-a",
                }], [], None)

        driver = _AsyncDriver()
        expander = AsyncGraphExpander(driver, database="cortext")
        nodes = await expander.expand(
            ["seed"], include_seeds=False, project_id="project-a",
        )

        self.assertEqual([node.node_id for node in nodes], ["neighbor"])
        query, params, database = driver.queries[0]
        self.assertIn("seed.project_id = $project_id", query)
        self.assertIn("neighbor.project_id = $project_id", query)
        self.assertEqual(params["project_id"], "project-a")
        self.assertEqual(database, "cortext")

    async def test_service_propagates_project_scope_to_retrieval(self):
        service = ExploreService()
        run_retrieval = AsyncMock(return_value=[])
        with patch.object(service, "_run_retrieval", run_retrieval):
            await service.explore(
                "orders", mode="semantic", project_id="project-a",
            )

        self.assertEqual(run_retrieval.await_args.kwargs["project_id"], "project-a")


if __name__ == "__main__":
    unittest.main()

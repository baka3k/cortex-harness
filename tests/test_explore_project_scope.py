import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
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
from tools.common.retrieval_scorer import ScoredResult  # noqa: E402


class _GraphDriver:
    def __init__(self):
        self.queries = []

    def execute_query_sync(self, query, parameters=None, database=None):
        self.queries.append((query, dict(parameters or {}), database))
        if "RETURN n LIMIT" in query:
            return ([{"n": {"id": "seed", "project_id": "project-a"}}], [], None)
        if "MATCH p = (seed)-" in query:
            return ([{
                "seed_id": "seed",
                "seed_ids": ["seed"],
                "hops": 1,
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
            driver, "seed", "cortext", 5, project_id="PrOjEcT-A",
        )

        query, params, database = driver.queries[0]
        self.assertIn("n.project_id_normalized = $project_id_normalized", query)
        self.assertEqual(params["project_id"], "PrOjEcT-A")
        self.assertEqual(params["project_id_normalized"], "project-a")
        self.assertEqual(database, "cortext")

    def test_graph_expansion_filters_seed_and_neighbor_projects(self):
        driver = _GraphDriver()
        expander = GraphExpander(driver, database="cortext")

        nodes = expander.expand(
            ["seed"], rel_types=["CALLS"], include_seeds=False,
            project_id="PrOjEcT-A",
        )

        self.assertEqual([node.node_id for node in nodes], ["neighbor"])
        query, params, database = driver.queries[0]
        self.assertIn("seed.project_id_normalized = $project_id_normalized", query)
        self.assertIn("neighbor.project_id_normalized = $project_id_normalized", query)
        self.assertEqual(params["project_id"], "PrOjEcT-A")
        self.assertEqual(params["project_id_normalized"], "project-a")
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
    async def test_unscoped_service_searches_registered_graph_collection_pairs(self):
        targets = {
            "Alpha": SimpleNamespace(
                project_id_normalized="alpha",
                code_graph="alpha_graph",
                code_qdrant_collection="alpha_vectors",
            ),
            "Beta": SimpleNamespace(
                project_id_normalized="beta",
                code_graph="beta_graph",
                code_qdrant_collection="beta_vectors",
            ),
        }
        service = ExploreService()

        async def fake_retrieval(**kwargs):
            project = kwargs["database"].removesuffix("_graph")
            return [ScoredResult(
                node_id="shared-symbol",
                score=0.8 if project == "alpha" else 0.9,
                node={"project_id": project, "name": project},
            )]

        with patch(
            "tools.common.project_registry.list_registered_projects",
            return_value=["Alpha", "Beta"],
        ), patch(
            "tools.common.project_registry.resolve_project_targets",
            side_effect=lambda project_id: targets[project_id],
        ), patch.object(
            explore_service_module, "_make_embedder", return_value=None,
        ), patch.object(
            explore_service_module, "_make_graph_driver",
            new=AsyncMock(return_value=object()),
        ), patch.object(
            service, "_run_retrieval", side_effect=fake_retrieval,
        ) as run_retrieval:
            result = await service.explore("orders", mode="hybrid", top_k=1)

        calls = run_retrieval.await_args_list
        self.assertEqual(
            [(call.kwargs["database"], call.kwargs["collection"]) for call in calls],
            [("alpha_graph", "alpha_vectors"), ("beta_graph", "beta_vectors")],
        )
        self.assertEqual(result["matched_nodes"][0]["name"], "beta")
        self.assertIsNone(result["retrieval"]["graph_database"])
        self.assertEqual(
            result["retrieval"]["graph_databases"],
            ["alpha_graph", "beta_graph"],
        )
        self.assertEqual(
            result["retrieval"]["qdrant_collections"],
            ["alpha_vectors", "beta_vectors"],
        )

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
            ["seed"], include_seeds=False, project_id="PrOjEcT-A",
        )

        self.assertEqual([node.node_id for node in nodes], ["neighbor"])
        query, params, database = driver.queries[0]
        self.assertIn("seed.project_id_normalized = $project_id_normalized", query)
        self.assertIn("neighbor.project_id_normalized = $project_id_normalized", query)
        self.assertEqual(params["project_id"], "PrOjEcT-A")
        self.assertEqual(params["project_id_normalized"], "project-a")
        self.assertEqual(database, "cortext")

    async def test_service_propagates_project_scope_to_retrieval(self):
        service = ExploreService()
        run_retrieval = AsyncMock(return_value=[])
        with patch.object(service, "_run_retrieval", run_retrieval):
            result = await service.explore(
                "orders", mode="semantic", project_id="PrOjEcT-A",
            )

        self.assertEqual(run_retrieval.await_args.kwargs["project_id"], "PrOjEcT-A")
        self.assertEqual(result["retrieval"]["graph_provider"], "falkordb")
        self.assertFalse(result["retrieval"]["graph_requested"])
        self.assertFalse(result["retrieval"]["degraded"])


if __name__ == "__main__":
    unittest.main()

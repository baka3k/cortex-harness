import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "code-tiny" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from semantic_graph_expansion import expand_semantic_results  # noqa: E402


class SemanticGraphExpansionTest(unittest.IsolatedAsyncioTestCase):
    async def test_project_scope_filters_seeds_neighbors_and_edges(self):
        calls = []

        async def fake_run_cypher_first(query, params, dbs):
            calls.append((query, params, dbs))
            if "MATCH p =" in query:
                return "cortext", [{
                    "seed_id": "seed",
                    "node_id": "neighbor",
                    "project_id": "project-a",
                    "hop_distance": 1,
                }]
            return "cortext", []

        await expand_semantic_results(
            {"results": [{"payload": {"symbol_id": "seed"}}]},
            run_cypher_first=fake_run_cypher_first,
            db_candidates=["cortext"],
            expand_graph=True,
            project_id="PrOjEcT-A",
        )

        node_query, node_params, _ = calls[0]
        edge_query, edge_params, _ = calls[1]
        self.assertIn("seed.project_id_normalized = $project_id_normalized", node_query)
        self.assertIn("neighbor.project_id_normalized = $project_id_normalized", node_query)
        self.assertIn("source.project_id_normalized = $project_id_normalized", edge_query)
        self.assertIn("target.project_id_normalized = $project_id_normalized", edge_query)
        self.assertEqual(node_params["project_id"], "PrOjEcT-A")
        self.assertEqual(edge_params["project_id"], "PrOjEcT-A")
        self.assertEqual(node_params["project_id_normalized"], "project-a")
        self.assertEqual(edge_params["project_id_normalized"], "project-a")

    async def test_expand_semantic_results_uses_vector_hits_as_graph_seeds(self):
        calls = []

        async def fake_run_cypher_first(query, params, dbs):
            calls.append((query, params, dbs))
            if "MATCH p =" in query:
                return "neo4j", [
                    {
                        "seed_id": "seed-function",
                        "seed_ids": ["seed-function"],
                        "node_id": "neighbor-function",
                        "name": "helper",
                        "qualified_name": "pkg.helper",
                        "kind": "Function",
                        "file_path": "src/helper.py",
                        "hop_distance": 1,
                    }
                ]
            return "neo4j", [
                {
                    "source": "seed-function",
                    "target": "neighbor-function",
                    "type": "CALLS",
                    "confidence": 0.9,
                    "call_depth": 1,
                }
            ]

        payload = {"results": [{"payload": {"symbol_id": "seed-function"}}]}
        result = await expand_semantic_results(
            payload,
            run_cypher_first=fake_run_cypher_first,
            db_candidates=["neo4j"],
            expand_graph=True,
            graph_depth=2,
            graph_direction="out",
            graph_rel_types="CALLS",
        )

        expansion = result["graph_expansion"]
        self.assertIs(expansion["enabled"], True)
        self.assertEqual(expansion["seed_ids"], ["seed-function"])
        self.assertEqual(expansion["results"][0]["node_id"], "neighbor-function")
        self.assertEqual(expansion["results"][0]["graph_proximity"], 0.8)
        self.assertEqual(expansion["results"][0]["seed_ids"], ["seed-function"])
        self.assertEqual(expansion["edges"][0]["type"], "CALLS")
        self.assertEqual(calls[0][1]["seed_ids"], ["seed-function"])

    async def test_named_framework_queries_expand_from_primary_language_seeds(self):
        cases = (
            ("spring order service transaction", "java-order-service", "spring-bean", "SpringBean"),
            ("servlet login endpoint", "java-login-servlet", "servlet-endpoint", "ServletEndpoint"),
            ("mybatis catalog statement", "java-catalog-repository", "mybatis-statement", "MyBatisStatement"),
            ("flutter home route", "dart-home-widget", "flutter-route", "FlutterRoute"),
            ("aspnet core orders endpoint", "csharp-orders-controller", "aspnet-core-endpoint", "HttpEndpoint"),
            ("aspnet framework home endpoint", "csharp-home-controller", "aspnet-framework-endpoint", "HttpEndpoint"),
        )
        for query, seed_id, node_id, kind in cases:
            with self.subTest(query=query):
                async def fake_run_cypher_first(cypher, params, dbs):
                    if "MATCH p =" in cypher:
                        return "neo4j", [
                            {
                                "seed_id": seed_id,
                                "node_id": node_id,
                                "name": query,
                                "qualified_name": node_id,
                                "kind": kind,
                                "file_path": "fixture/source",
                                "hop_distance": 1,
                            }
                        ]
                    return "neo4j", [
                        {
                            "source": seed_id,
                            "target": node_id,
                            "type": "FRAMEWORK_RELATION",
                            "confidence": 1.0,
                            "call_depth": 1,
                        }
                    ]

                result = await expand_semantic_results(
                    {"results": [{"payload": {"symbol_id": seed_id, "query": query}}]},
                    run_cypher_first=fake_run_cypher_first,
                    db_candidates=["neo4j"],
                    expand_graph=True,
                    graph_depth=2,
                )

                expansion = result["graph_expansion"]
                self.assertEqual(expansion["seed_ids"], [seed_id])
                self.assertEqual(expansion["results"][0]["node_id"], node_id)
                self.assertEqual(expansion["results"][0]["kind"], kind)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "code-tiny" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from semantic_graph_expansion import expand_semantic_results  # noqa: E402


class SemanticGraphExpansionTest(unittest.IsolatedAsyncioTestCase):
    async def test_expand_semantic_results_uses_vector_hits_as_graph_seeds(self):
        calls = []

        async def fake_run_cypher_first(query, params, dbs):
            calls.append((query, params, dbs))
            if "MATCH p =" in query:
                return "neo4j", [
                    {
                        "seed_id": "seed-function",
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
        self.assertEqual(expansion["edges"][0]["type"], "CALLS")
        self.assertEqual(calls[0][1]["seed_ids"], ["seed-function"])


if __name__ == "__main__":
    unittest.main()

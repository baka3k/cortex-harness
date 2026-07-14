import asyncio
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project, graph_rows, write_graph_facts  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


class CapturingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, parameters or {}, database))
        rows = (parameters or {}).get("rows", [])
        return ([{"count": len(rows)}], [], None)


class CobolGraphContractTest(unittest.TestCase):
    def test_provider_neutral_writer_receives_namespaced_nodes_and_typed_edges(self):
        facts, _ = analyze_project(FIXTURE, project_id="fixture")
        node_rows, relations = graph_rows(facts)
        self.assertIn("CobolProgram", node_rows)
        self.assertTrue(all(row["properties"]["project_id"] == "fixture" for rows in node_rows.values() for row in rows))
        self.assertTrue(any(row["rel_type"] == "PERFORMS_THRU" for row in relations))

        driver = CapturingDriver()
        writer = LanguageCodeWriter(driver, database="fixture", batch_size=100)
        counts = asyncio.run(write_graph_facts(writer, facts, repo="fixture"))
        self.assertGreater(counts["CobolProgram"], 0)
        self.assertTrue(any("MERGE (n:CobolProgram" in query for query, _, _ in driver.calls))
        self.assertTrue(any("PERFORMS_THRU" in query for query, _, _ in driver.calls))
        self.assertTrue(any("MERGE (r)-[:HAS_FILE]->(f)" in query for query, _, _ in driver.calls))


if __name__ == "__main__":
    unittest.main()

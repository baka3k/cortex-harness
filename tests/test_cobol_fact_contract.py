import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.models import stable_id  # noqa: E402
from tools.cobol.pipeline import analyze_project  # noqa: E402


class CobolFactContractTest(unittest.TestCase):
    def test_ids_and_serialization_are_deterministic(self):
        first, _ = analyze_project(FIXTURE, project_id="fixture")
        second, _ = analyze_project(FIXTURE, project_id="fixture")
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(stable_id("fixture", "program", "MAIN"), stable_id("fixture", "program", "MAIN"))
        self.assertNotEqual(stable_id("fixture", "program", "MAIN"), stable_id("other", "program", "MAIN"))

    def test_all_edges_are_project_scoped_and_source_backed(self):
        facts, _ = analyze_project(FIXTURE, project_id="fixture")
        self.assertTrue(facts.edges)
        self.assertTrue(all(edge.evidence.file for edge in facts.edges))
        self.assertTrue(all(edge.properties.get("project_id") == "fixture" for edge in facts.edges))


if __name__ == "__main__":
    unittest.main()

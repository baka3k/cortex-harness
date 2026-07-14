import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project  # noqa: E402


class CobolErrorRecoveryTest(unittest.TestCase):
    def test_malformed_file_preserves_surrounding_facts_and_diagnostics(self):
        facts, _ = analyze_project(FIXTURE, project_id="fixture")
        malformed_nodes = [node for node in facts.nodes if node.file_path == "malformed.cbl"]
        self.assertTrue(any(node.label == "CobolProgram" and node.name == "MALFORMED" for node in malformed_nodes))
        self.assertTrue(any(node.label == "CobolParagraph" and node.name == "START-PARA" for node in malformed_nodes))
        errors = [item for item in facts.diagnostics if item.evidence and item.evidence.file == "malformed.cbl"]
        self.assertTrue(any(item.code.startswith("COBOL_SYNTAX_") for item in errors))


if __name__ == "__main__":
    unittest.main()

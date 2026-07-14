import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project  # noqa: E402


class CobolFixtureAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts, cls.dependencies = analyze_project(FIXTURE, project_id="fixture")

    def test_extracts_structure_data_and_copybooks(self):
        labels = {node.label for node in self.facts.nodes}
        names = {node.name for node in self.facts.nodes}
        self.assertTrue({"CobolProgram", "CobolSection", "CobolParagraph", "CobolDataItem", "CobolCopybook"}.issubset(labels))
        self.assertTrue({"MAINPROG", "SUBPROG", "WS-STATUS", "COMMON", "NESTED"}.issubset(names))
        self.assertIn("common.cpy", self.dependencies.dependencies["main.cbl"])
        self.assertIn("nested.copy", self.dependencies.dependencies["common.cpy"])

    def test_builds_control_flow_calls_and_resource_facts(self):
        relationships = {edge.relationship for edge in self.facts.edges}
        self.assertTrue({"PERFORMS_THRU", "RETURNS", "GOES_TO_DYNAMIC", "FALLS_THROUGH", "ALTERS", "EXITS", "CALLS", "READS"}.issubset(relationships))
        labels = {node.label for node in self.facts.nodes}
        self.assertIn("CobolSqlStatement", labels)
        self.assertIn("CobolCicsCommand", labels)
        self.assertTrue(any(item.code == "COBOL_DYNAMIC_CALL" for item in self.facts.diagnostics))

    def test_missing_copybook_and_grammar_recovery_are_diagnostic(self):
        codes = {item.code for item in self.facts.diagnostics}
        self.assertIn("COBOL_COPYBOOK_NOT_FOUND", codes)
        self.assertIn("COBOL_SYNTAX_ERROR", codes)
        sql = next(node for node in self.facts.nodes if node.label == "CobolSqlStatement")
        self.assertEqual(sql.properties["operation"], "SELECT")

    def test_copybook_cycles_terminate_with_full_chain_diagnostic(self):
        cycle = next(item for item in self.facts.diagnostics if item.code == "COBOL_COPYBOOK_CYCLE")
        self.assertIn("cycle-a.copy", cycle.message)
        self.assertIn("cycle-b.copy", cycle.message)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "code-tiny" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from framework_registry import default_relationships, framework_for_parser, parser_aliases, searchable_labels  # noqa: E402


class CobolMcpRoutingTest(unittest.TestCase):
    def test_aliases_labels_and_relationships_are_scoped_to_cobol(self):
        self.assertEqual(framework_for_parser("ibm-cobol").name, "cobol")
        self.assertIn("gnucobol", parser_aliases())
        self.assertIn("CobolParagraph", searchable_labels("cobol"))
        relationships = default_relationships("cobol")
        self.assertIn("PERFORMS_THRU", relationships)
        self.assertIn("GOES_TO_DYNAMIC", relationships)
        self.assertNotIn("PERFORMS_THRU", default_relationships("java"))


if __name__ == "__main__":
    unittest.main()

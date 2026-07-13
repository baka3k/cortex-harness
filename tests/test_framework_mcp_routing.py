import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "code-tiny" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from framework_registry import (  # noqa: E402
    default_relationships,
    framework_for_parser,
    parser_aliases,
    searchable_labels,
    servlet_active_generation_predicate,
)


class FrameworkMcpRoutingTest(unittest.TestCase):
    def test_framework_aliases_resolve_without_losing_core_relationships(self):
        self.assertEqual(framework_for_parser("spring-boot").name, "spring")
        self.assertEqual(framework_for_parser("servlet").name, "servlet_jsp")
        self.assertEqual(framework_for_parser("my-batis").name, "mybatis")
        self.assertIn("spring_boot", parser_aliases())

        spring_relationships = default_relationships("spring")
        self.assertIn("CALLS", spring_relationships)
        self.assertIn("SEMANTIC_OF", spring_relationships)
        self.assertIn("DECLARES_QUERY", spring_relationships)
        self.assertEqual(len(spring_relationships), len(set(spring_relationships)))

    def test_searchable_labels_and_servlet_freshness_predicate_cover_framework_nodes(self):
        self.assertIn("ApiEndpoint", searchable_labels("spring"))
        self.assertIn("JSPView", searchable_labels("servlet_jsp"))
        self.assertIn("MyBatisStatement", searchable_labels("mybatis"))

        predicate = servlet_active_generation_predicate("node")
        self.assertIn("ServletJspAnalysisState", predicate)
        self.assertIn("state.active_generation = node.generation_id", predicate)
        self.assertIn("node.project_id", predicate)


if __name__ == "__main__":
    unittest.main()

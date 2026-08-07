import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "framework-java-app"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.mybatis.pipeline import run_mybatis_foundation  # noqa: E402
from tools.servlet_jsp.models import ResourceBudgets  # noqa: E402
from tools.servlet_jsp.pipeline import run_servlet_jsp_analysis  # noqa: E402
from tools.spring.pipeline import run_spring_foundation  # noqa: E402


class FrameworkFixtureAnalysisTest(unittest.TestCase):
    def test_mixed_fixture_produces_framework_facts_without_graph_services(self):
        spring = run_spring_foundation(
            root=str(FIXTURE),
            project_id="fixture",
            project_name="Framework Fixture",
            languages=("java",),
        )
        servlet = run_servlet_jsp_analysis(
            root=str(FIXTURE),
            project_id="fixture",
            project_name="Framework Fixture",
            budgets=ResourceBudgets(max_peak_rss_bytes=0),
        )
        mybatis = run_mybatis_foundation(
            root=str(FIXTURE),
            project_id="fixture",
            project_name="Framework Fixture",
            languages=("java",),
        )

        spring_kinds = {fact.kind for fact in spring.semantic_facts}
        servlet_kinds = {fact.kind for fact in servlet.semantic_facts}
        mybatis_kinds = {fact.kind for fact in mybatis.semantic_facts}

        self.assertIn("SpringModule", spring_kinds)
        self.assertIn("Controller", spring_kinds)
        self.assertIn("ApiEndpoint", spring_kinds)
        self.assertIn("ServletJspModule", servlet_kinds)
        self.assertIn("WebDescriptor", servlet_kinds)
        self.assertIn("JSPView", servlet_kinds)
        self.assertIn("MyBatisModule", mybatis_kinds)
        self.assertIn("MyBatisStatement", mybatis_kinds)
        self.assertIn("DatabaseTable", mybatis_kinds)

        for framework, result in (
            ("spring", spring),
            ("servlet_jsp", servlet),
            ("mybatis", mybatis),
        ):
            with self.subTest(framework=framework):
                anchored_facts = [
                    fact for fact in result.semantic_facts
                    if getattr(fact, "source_symbol_id", "")
                ]
                semantic_targets = {
                    relationship.to_id
                    for relationship in result.relationships
                    if relationship.type == "SEMANTIC_OF"
                }
                self.assertTrue(anchored_facts)
                self.assertTrue(
                    {fact.source_symbol_id for fact in anchored_facts} & semantic_targets
                )
                self.assertTrue(
                    all(fact.project_id == "fixture" for fact in anchored_facts)
                )
                self.assertTrue(
                    all(fact.source.file_path for fact in anchored_facts)
                )
                self.assertTrue(
                    all(
                        relationship.from_label and relationship.to_label
                        for relationship in result.relationships
                    )
                )


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

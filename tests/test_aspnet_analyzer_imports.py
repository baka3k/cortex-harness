from __future__ import annotations

import sys
import unittest
from pathlib import Path

CODE_TINY = Path(__file__).resolve().parents[1] / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


class AspNetAnalyzerImportsTest(unittest.TestCase):
    def test_public_packages_import_without_graph_services(self) -> None:
        from tools.aspnet_core import run_aspnet_core_analysis
        from tools.aspnet_framework import run_aspnet_framework_analysis
        from tools.common.aspnet import AnalysisResult, SemanticFact

        self.assertTrue(callable(run_aspnet_core_analysis))
        self.assertTrue(callable(run_aspnet_framework_analysis))
        self.assertIsNotNone(AnalysisResult)
        self.assertIsNotNone(SemanticFact)


if __name__ == "__main__":
    unittest.main()

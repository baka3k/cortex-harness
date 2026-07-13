import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


class FrameworkAnalyzerImportsTest(unittest.TestCase):
    def test_framework_analyzer_entrypoints_import_on_current_platform(self):
        for module_name in (
            "tools.spring.spring_analyzer",
            "tools.servlet_jsp.servlet_jsp_analyzer",
            "tools.mybatis.mybatis_analyzer",
        ):
            with self.subTest(module=module_name):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()

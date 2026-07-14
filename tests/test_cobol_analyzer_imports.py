import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


class CobolAnalyzerImportsTest(unittest.TestCase):
    def test_entrypoint_and_support_modules_import(self):
        for module_name in (
            "tools.cobol.cobol_analyzer",
            "tools.cobol.models",
            "tools.cobol.parser_runtime",
            "tools.cobol.parser",
            "tools.cobol.resolver",
            "tools.cobol.cfg",
            "tools.cobol.semantics",
            "tools.cobol.pipeline",
        ):
            with self.subTest(module=module_name):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()

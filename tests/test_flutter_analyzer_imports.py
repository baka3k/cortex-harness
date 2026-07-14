import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


class FlutterAnalyzerImportsTest(unittest.TestCase):
    def test_flutter_entrypoint_and_support_modules_import(self):
        for module_name in (
            "tools.flutter.flutter_analyzer",
            "tools.flutter.dart_parser",
            "tools.flutter.detector",
            "tools.flutter.protocol",
            "tools.flutter.models",
        ):
            with self.subTest(module=module_name):
                importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()

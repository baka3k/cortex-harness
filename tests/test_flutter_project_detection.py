import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "flutter-app"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.flutter.detector import detect_flutter_project, project_package_name  # noqa: E402
from tools.flutter.dart_parser import create_parser, parser_version  # noqa: E402
from tools.flutter.flutter_analyzer import main  # noqa: E402


class FlutterProjectDetectionTest(unittest.TestCase):
    def test_detects_flutter_sdk_dependency(self):
        project = detect_flutter_project(FIXTURE)
        self.assertIsNotNone(project)
        self.assertEqual(project.package_name, "cortex_flutter_fixture")
        self.assertIn("dependencies.flutter.sdk=flutter", project.evidence)

    def test_does_not_treat_pure_dart_package_as_flutter(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "pubspec.yaml").write_text(
                "name: dart_only\nenvironment:\n  sdk: ^3.11.0\n",
                encoding="utf-8",
            )
            self.assertIsNone(detect_flutter_project(root))
            self.assertEqual(project_package_name(root), "dart_only")

    def test_python_parser_does_not_require_a_dart_sdk(self):
        parser = create_parser()
        tree = parser.parse(b"void main() {}")
        self.assertFalse(tree.root_node.has_error)
        self.assertNotEqual(parser_version(), "unknown")

    def test_all_mode_still_analyzes_a_pure_dart_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib").mkdir()
            (root / "pubspec.yaml").write_text("name: dart_only\n", encoding="utf-8")
            (root / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
            with patch(
                "tools.flutter.flutter_analyzer.write_graph",
                new=AsyncMock(return_value={}),
            ):
                result = main(
                    [
                        "--root",
                        str(root),
                        "--mode",
                        "all",
                        "--facts-output",
                        str(root / "facts.json"),
                    ]
                )
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()

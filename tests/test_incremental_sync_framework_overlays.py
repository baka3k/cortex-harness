import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.sync import incremental_sync  # noqa: E402


class DetectionResult:
    is_spring = False
    is_servlet_jsp = False
    is_mybatis = False
    evidence = ()


class ModuleDetector:
    is_spring = False
    is_servlet_jsp = False
    is_mybatis = False
    evidence = ("module-evidence",)

    def __init__(self, root):
        self.root = root

    def discover_modules(self):
        return [{"rel_path": ".", "evidence": self.evidence}]

    def detect_path(self, path):
        return DetectionResult()


class EmptyDetector:
    def __init__(self, root):
        self.root = root

    def discover_modules(self):
        return []

    def detect_path(self, path):
        return DetectionResult()


class IncrementalSyncFrameworkOverlaysTest(unittest.TestCase):
    def test_selecting_framework_auto_adds_base_language_prerequisites(self):
        selected, auto_mode = incremental_sync._selected_parsers("spring,mybatis,struts,flutter")

        self.assertFalse(auto_mode)
        self.assertIn("spring", selected)
        self.assertIn("mybatis", selected)
        self.assertIn("java", selected)
        self.assertIn("kotlin", selected)
        self.assertIn("struts", selected)
        self.assertIn("flutter", selected)
        self.assertIn("dart", selected)

    def test_struts_and_flutter_projects_route_overlay_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            struts_path = "src/main/resources/struts.xml"
            java_path = "src/main/java/example/HomeAction.java"
            dart_path = "lib/main.dart"
            for relative in (struts_path, java_path, dart_path):
                target = Path(root, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("", encoding="utf-8")
            Path(root, "pubspec.yaml").write_text(
                "dependencies:\n  flutter:\n    sdk: flutter\n",
                encoding="utf-8",
            )

            with patch("tools.spring.detector.SpringProjectDetector", EmptyDetector), patch(
                "tools.servlet_jsp.detector.ServletJspProjectDetector", EmptyDetector
            ), patch("tools.mybatis.detector.MyBatisProjectDetector", EmptyDetector):
                overlays, evidence = incremental_sync._group_paths_by_framework(
                    [struts_path, java_path, dart_path, "pubspec.yaml"],
                    root=root,
                )

        self.assertEqual(overlays["struts"], {struts_path, java_path, "pubspec.yaml"})
        self.assertEqual(overlays["flutter"], {struts_path, dart_path, "pubspec.yaml"})
        self.assertTrue(any("struts-config" in item for item in evidence["struts"]))
        self.assertTrue(evidence["flutter"])

    def test_same_java_file_can_feed_primary_parser_and_all_framework_overlays(self):
        with tempfile.TemporaryDirectory() as root:
            java_path = "src/main/java/example/CatalogController.java"
            os.makedirs(os.path.join(root, "src", "main", "java", "example"))
            Path(root, java_path).write_text("class CatalogController {}", encoding="utf-8")

            primary = incremental_sync._group_paths_by_parser([java_path], root=root)
            with patch("tools.spring.detector.SpringProjectDetector", ModuleDetector), patch(
                "tools.servlet_jsp.detector.ServletJspProjectDetector", ModuleDetector
            ), patch("tools.mybatis.detector.MyBatisProjectDetector", ModuleDetector):
                overlays, evidence = incremental_sync._group_paths_by_framework([java_path], root=root)

        self.assertEqual(primary["java"], {java_path})
        self.assertEqual(overlays["spring"], {java_path})
        self.assertEqual(overlays["servlet_jsp"], {java_path})
        self.assertEqual(overlays["mybatis"], {java_path})
        self.assertEqual(evidence["spring"], ["module-evidence"])

    def test_strong_deleted_candidates_route_to_framework_cleanup_without_file_contents(self):
        paths = [
            "src/main/resources/mappers/CatalogMapper.xml",
            "src/main/webapp/WEB-INF/web.xml",
            "src/main/resources/application.yml",
            "src/main/resources/struts.xml",
            "pubspec.yaml",
        ]
        with tempfile.TemporaryDirectory() as root:
            with patch("tools.spring.detector.SpringProjectDetector", EmptyDetector), patch(
                "tools.servlet_jsp.detector.ServletJspProjectDetector", EmptyDetector
            ), patch("tools.mybatis.detector.MyBatisProjectDetector", EmptyDetector):
                overlays, evidence = incremental_sync._group_paths_by_framework(paths, root=root)

        self.assertEqual(overlays["mybatis"], {"src/main/resources/mappers/CatalogMapper.xml"})
        self.assertEqual(overlays["servlet_jsp"], {"src/main/webapp/WEB-INF/web.xml"})
        self.assertEqual(overlays["spring"], {"src/main/resources/application.yml"})
        self.assertEqual(overlays["struts"], {"src/main/resources/struts.xml"})
        self.assertEqual(overlays["flutter"], {"pubspec.yaml"})
        self.assertIn("src/main/resources/mappers/CatalogMapper.xml:strong-candidate", evidence["mybatis"])


if __name__ == "__main__":
    unittest.main()

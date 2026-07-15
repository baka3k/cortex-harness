import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.struts.pipeline import _iter_files  # noqa: E402


class StrutsScanFilteringTest(unittest.TestCase):
    def test_iter_files_skips_ignored_directories_and_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kept_files = {
                root / "src" / "main" / "java" / "ExampleAction.java",
                root / "src" / "main" / "resources" / "struts.xml",
            }
            ignored_files = {
                root / "target" / "classes" / "struts.xml",
                root / "generated-sources" / "GeneratedAction.java",
                root / ".hidden" / "struts.xml",
                root / "src" / "main" / "java" / "ExampleAction.class",
                root / "src" / "main" / "java" / "ExampleAction.java~",
                root / "src" / "main" / "resources" / "scan.log",
            }

            for path in kept_files | ignored_files:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            scanned = set(_iter_files(root))

            self.assertEqual(kept_files, scanned)
            self.assertTrue(ignored_files.isdisjoint(scanned))

    def test_iter_files_matches_patterns_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            kept = root / "src" / "Action.java"
            ignored = root / "BUILD" / "Action.java"
            ignored_backup = root / "src" / "Action.JAVA~"

            for path in (kept, ignored, ignored_backup):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            self.assertEqual({kept}, set(_iter_files(root)))


if __name__ == "__main__":
    unittest.main()

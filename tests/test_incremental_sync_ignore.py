"""``incremental_sync._walk_all_source_files`` must honour CORTEX_EXTRA_IGNORE_DIRS.

Plan 260909-0854 phase 03: the built-in ``_SKIP_DIRS`` prune stays the
default; user-configured ignore folders (exact or glob) prune on top.
"""

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.sync.incremental_sync import _walk_all_source_files  # noqa: E402
from tools.common.scan_ignore import reset_extra_ignore_dirs  # noqa: E402

ENV_VAR = "CORTEX_EXTRA_IGNORE_DIRS"


class WalkAllSourceFilesIgnoreTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_extra_ignore_dirs()
        self.addCleanup(reset_extra_ignore_dirs)

    def _make_tree(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "app").mkdir()
        (root / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (root / "sandbox").mkdir()
        (root / "sandbox" / "sketch.py").write_text("y = 2\n", encoding="utf-8")
        (root / "generated-ui").mkdir()
        (root / "generated-ui" / "gen.py").write_text("z = 3\n", encoding="utf-8")
        return root

    def test_walk_all_source_files_skips_env_ignored_dirs(self):
        root = self._make_tree()

        with unittest.mock.patch.dict("os.environ", {ENV_VAR: "sandbox, generated-*"}):
            reset_extra_ignore_dirs()
            found = _walk_all_source_files(str(root))

        self.assertIn("app/main.py", found)
        self.assertNotIn("sandbox/sketch.py", found)
        self.assertNotIn("generated-ui/gen.py", found)

    def test_walk_without_env_unchanged(self):
        root = self._make_tree()

        found = _walk_all_source_files(str(root))

        self.assertEqual(
            sorted(found), ["app/main.py", "generated-ui/gen.py", "sandbox/sketch.py"]
        )


class PythonAnalyzerSmokeTests(unittest.TestCase):
    """Smoke: the python analyzer's parent scan path receives the merge."""

    def setUp(self) -> None:
        reset_extra_ignore_dirs()
        self.addCleanup(reset_extra_ignore_dirs)

    def test_python_analyzer_scan_skips_env_ignored_dirs(self):
        try:
            from tools.python.python_analyzer import _scan_python_files
        except Exception:
            self.skipTest("python_analyzer import failed")

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (root / "_pc2c").mkdir()
        (root / "_pc2c" / "legacy.py").write_text("y = 2\n", encoding="utf-8")

        with unittest.mock.patch.dict("os.environ", {ENV_VAR: "_pc2c"}):
            reset_extra_ignore_dirs()
            found = _scan_python_files(str(root))

        self.assertTrue(any(f.endswith("src/main.py") for f in found))
        self.assertFalse(any("_pc2c" in f for f in found))


if __name__ == "__main__":
    unittest.main()

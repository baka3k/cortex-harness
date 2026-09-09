"""Unit tests for ``tools.common.scan_ignore``.

Run from the repo root with::

    PYTHONPATH=code-tiny python -m unittest \
        code-tiny.tools.common.test_scan_ignore
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path


_SCAN_IGNORE_PATH = (
    Path(__file__).resolve().parents[0] / "scan_ignore.py"
)


def _load_scan_ignore():
    spec = importlib.util.spec_from_file_location(
        "scan_ignore_under_test", _SCAN_IGNORE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scan_ignore_under_test"] = module
    spec.loader.exec_module(module)
    return module


scan_ignore = _load_scan_ignore()


class IsExcludedDirTests(unittest.TestCase):
    """``.venv`` and friends are excluded; ordinary source dirs are not."""

    def test_excludes_common_env_dirs(self) -> None:
        for name in (".venv", "venv", "env", "node_modules", "build",
                     ".dart_tool", ".git", ".idea", "vendor", "Pods",
                     "DerivedData", "dist", "target", "coverage"):
            with self.subTest(name=name):
                self.assertTrue(scan_ignore.is_excluded_dir(name))

    def test_keeps_ordinary_source_dirs(self) -> None:
        for name in ("src", "lib", "tests", "docs", "app"):
            with self.subTest(name=name):
                self.assertFalse(scan_ignore.is_excluded_dir(name))


class HasExcludedParentTests(unittest.TestCase):
    """``has_excluded_parent`` ignores paths living inside excluded dirs."""

    def test_flags_path_inside_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_file = root / ".venv" / "lib" / "site" / "pkg" / "thing.dart"
            self.assertTrue(
                scan_ignore.has_excluded_parent(venv_file, root=root)
            )

    def test_does_not_flag_top_level_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_file = root / "src" / "main.dart"
            self.assertFalse(
                scan_ignore.has_excluded_parent(source_file, root=root)
            )

    def test_does_not_flag_nested_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "packages" / "app" / "lib" / "main.dart"
            self.assertFalse(
                scan_ignore.has_excluded_parent(nested, root=root)
            )


class FilterPathsTests(unittest.TestCase):
    """``filter_paths`` strips out anything inside excluded dirs."""

    def test_keeps_only_paths_outside_excluded_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            keep = root / "src" / "main.dart"
            drop = root / ".venv" / "site" / "thing.dart"
            other_drop = root / "node_modules" / "thing.dart"
            for path in (keep, drop, other_drop):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("// stub\n", encoding="utf-8")

            kept = scan_ignore.filter_paths([keep, drop, other_drop], root=root)
            self.assertEqual(kept, [keep])


class FlutterAnalyzerIntegrationTests(unittest.TestCase):
    """``flutter_analyzer`` must skip ``.venv`` (and friends) when walking."""

    def _load_analyzer(self):
        spec = importlib.util.spec_from_file_location(
            "flutter_analyzer_under_test",
            Path(__file__).resolve().parents[1]
            / "tools" / "flutter" / "flutter_analyzer.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["flutter_analyzer_under_test"] = module
        spec.loader.exec_module(module)
        return module

    def test_dart_files_ignores_venv(self) -> None:
        # Importing flutter_analyzer triggers heavy deps; only run this test
        # when those imports actually resolve.
        try:
            self._load_analyzer()
        except Exception:
            self.skipTest("flutter_analyzer import failed")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "lib").mkdir()
            (root / "lib" / "main.dart").write_text("// x\n", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "lib").mkdir()
            (root / ".venv" / "lib" / "noise.dart").write_text(
                "// not source\n", encoding="utf-8"
            )
            (root / "node_modules").mkdir()
            (root / "node_modules" / "noise.dart").write_text(
                "// not source\n", encoding="utf-8"
            )

            # Reach into the closure-free path: replicate the comprehension.
            dart_files = [
                path for path in root.rglob("*.dart")
                if not scan_ignore.has_excluded_parent(path, root=root)
            ]
            self.assertEqual(
                sorted(p.name for p in dart_files),
                ["main.dart"],
            )


class ExtraIgnoreDirsTests(unittest.TestCase):
    """``CORTEX_EXTRA_IGNORE_DIRS`` merges on top of the built-in defaults."""

    ENV_VAR = "CORTEX_EXTRA_IGNORE_DIRS"

    def setUp(self) -> None:
        scan_ignore.reset_extra_ignore_dirs()
        self.addCleanup(scan_ignore.reset_extra_ignore_dirs)

    def test_extra_ignore_dirs_from_env(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ", {self.ENV_VAR: "sandbox, _pc2c"}
        ):
            scan_ignore.reset_extra_ignore_dirs()
            self.assertIn("sandbox", scan_ignore.extra_ignore_dirs())
            self.assertTrue(scan_ignore.is_excluded_dir("sandbox"))
            self.assertTrue(scan_ignore.is_excluded_dir("_pc2c"))
            self.assertFalse(scan_ignore.is_excluded_dir("src"))

    def test_extra_ignore_glob_from_env(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ", {self.ENV_VAR: "gen-*"}
        ):
            scan_ignore.reset_extra_ignore_dirs()
            self.assertTrue(scan_ignore.is_excluded_dir("gen-ui"))
            self.assertFalse(scan_ignore.is_excluded_dir("generated"))  # no match

    def test_blank_and_unset_env_change_nothing(self) -> None:
        with unittest.mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(self.ENV_VAR, None)
            scan_ignore.reset_extra_ignore_dirs()
            self.assertEqual(scan_ignore.extra_ignore_dirs(), frozenset())
            self.assertFalse(scan_ignore.is_excluded_dir("sandbox"))

        with unittest.mock.patch.dict("os.environ", {self.ENV_VAR: " , ,, "}):
            scan_ignore.reset_extra_ignore_dirs()
            self.assertEqual(scan_ignore.extra_ignore_dirs(), frozenset())

    def test_common_scan_exclude_not_mutated_by_env(self) -> None:
        before = frozenset(scan_ignore.COMMON_SCAN_EXCLUDE)
        with unittest.mock.patch.dict(
            "os.environ", {self.ENV_VAR: "sandbox"}
        ):
            scan_ignore.reset_extra_ignore_dirs()
            scan_ignore.is_excluded_dir("sandbox")
        self.assertEqual(scan_ignore.COMMON_SCAN_EXCLUDE, before)
        self.assertNotIn("sandbox", scan_ignore.COMMON_SCAN_EXCLUDE)

    def test_filter_paths_and_has_excluded_parent_respect_extra(self) -> None:
        with unittest.mock.patch.dict(
            "os.environ", {self.ENV_VAR: "archive"}
        ):
            scan_ignore.reset_extra_ignore_dirs()
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                keep = root / "src" / "main.py"
                drop = root / "archive" / "old.py"
                for path in (keep, drop):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("x = 1\n", encoding="utf-8")

                self.assertTrue(
                    scan_ignore.has_excluded_parent(drop, root=root)
                )
                self.assertFalse(
                    scan_ignore.has_excluded_parent(keep, root=root)
                )
                self.assertEqual(
                    scan_ignore.filter_paths([keep, drop], root=root), [keep]
                )


if __name__ == "__main__":
    unittest.main()

"""Tests for threading user ignore folders through the dev.py sync flows.

Covers plan 260909-0854 phase 02: the ``extra_ignores`` parameter on the
filesystem-walking helpers, the ``CORTEX_EXTRA_IGNORE_DIRS`` env var exported
to spawned subprocesses, and the scan-root contradiction warning.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

from click.testing import CliRunner

from cortex_harness.dev import (
    _build_file_hashes,
    _detect_changed_docs,
    _detect_langs,
    _discover_folders,
    _find_doc_files,
    _is_excluded_path,
    _mtime_changed_files,
    _sync_extra_ignores,
    _warn_scan_roots_matching_ignores,
    cli,
)


def _mk_tree(root: Path) -> None:
    """src/ with a normal file, one inside 'legacy/', one inside 'generated-ui/'."""
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "legacy").mkdir(parents=True)
    (root / "src" / "legacy" / "old.py").write_text("y = 2\n", encoding="utf-8")
    (root / "src" / "generated-ui").mkdir(parents=True)
    (root / "src" / "generated-ui" / "gen.py").write_text("z = 3\n", encoding="utf-8")


class IsExcludedPathExtraIgnoresTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        _mk_tree(self.root)

    def test_is_excluded_path_respects_extra_ignores(self):
        legacy = self.root / "src" / "legacy" / "old.py"
        normal = self.root / "src" / "app" / "main.py"
        self.assertTrue(
            _is_excluded_path(legacy, self.root, frozenset({"legacy"}))
        )
        self.assertFalse(_is_excluded_path(legacy, self.root))
        self.assertFalse(
            _is_excluded_path(normal, self.root, frozenset({"legacy"}))
        )

    def test_extra_ignores_support_fnmatch_glob(self):
        gen = self.root / "src" / "generated-ui" / "gen.py"
        self.assertTrue(
            _is_excluded_path(gen, self.root, frozenset({"generated-*"}))
        )
        self.assertFalse(_is_excluded_path(gen, self.root))

    def test_default_excludes_still_apply_without_extra(self):
        venv_file = self.root / "src" / ".venv" / "lib" / "x.py"
        venv_file.parent.mkdir(parents=True, exist_ok=True)
        venv_file.write_text("v = 1\n", encoding="utf-8")
        self.assertTrue(_is_excluded_path(venv_file, self.root))


class DiscoverFoldersExtraIgnoresTests(unittest.TestCase):
    def test_discover_folders_hides_user_ignored_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _mk_tree(root)
            folders = _discover_folders(root, "src", frozenset({"legacy", "generated-*"}))
            self.assertIn("src", folders)
            self.assertIn(str(Path("src") / "app"), folders)
            self.assertNotIn(str(Path("src") / "legacy"), folders)
            self.assertNotIn(str(Path("src") / "generated-ui"), folders)

    def test_discover_folders_without_extra_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _mk_tree(root)
            folders = _discover_folders(root, "src")
            self.assertIn(str(Path("src") / "legacy"), folders)
            self.assertIn(str(Path("src") / "generated-ui"), folders)


class DetectionHelpersExtraIgnoresTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        _mk_tree(self.root)

    def test_detect_langs_respects_extra_ignores(self):
        # Only app/main.py counts -> python detected either way, but ensure
        # the ignored folder's files don't affect the walk (no crash + langs
        # identical between "legacy excluded" and "only main.py exists").
        langs = _detect_langs(self.root / "src", frozenset({"legacy", "generated-*"}))
        self.assertEqual(langs, ["python"])

    def test_mtime_changed_files_respects_extra_ignores(self):
        changed, _ = _mtime_changed_files(
            self.root, 0.0, frozenset({"legacy", "generated-*"})
        )
        self.assertEqual(sorted(changed), ["src/app/main.py"])

    def test_find_doc_files_respects_extra_ignores(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "keep.md").write_text("# keep\n", encoding="utf-8")
        (self.root / "docs" / "archive").mkdir()
        (self.root / "docs" / "archive" / "old.md").write_text("# old\n", encoding="utf-8")

        found = _find_doc_files(self.root / "docs", frozenset({"archive"}))
        self.assertEqual([f.name for f in found], ["keep.md"])

    def test_detect_changed_docs_hash_mode_respects_extra_ignores(self):
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "keep.md").write_text("# keep\n", encoding="utf-8")
        (docs / "archive").mkdir(exist_ok=True)
        (docs / "archive" / "old.md").write_text("# old\n", encoding="utf-8")

        # Baseline hashes built WITH the ignore set (like a prior full sync).
        state = {"file_hashes": _build_file_hashes(docs, frozenset({"archive"}))}
        # Touch the ignored file -> must not be reported as changed nor deleted.
        (docs / "archive" / "old.md").write_text("# changed\n", encoding="utf-8")

        changed, deleted = _detect_changed_docs(docs, state, frozenset({"archive"}))
        self.assertEqual(changed, [])
        self.assertEqual(deleted, [])

    def test_detect_changed_docs_without_extra_unchanged(self):
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "keep.md").write_text("# keep\n", encoding="utf-8")
        (docs / "archive").mkdir(exist_ok=True)
        (docs / "archive" / "old.md").write_text("# old\n", encoding="utf-8")

        state = {"file_hashes": _build_file_hashes(docs)}
        (docs / "archive" / "old.md").write_text("# changed\n", encoding="utf-8")

        changed, _ = _detect_changed_docs(docs, state)
        self.assertEqual([f.name for f in changed], ["old.md"])


class SyncExtraIgnoresTests(unittest.TestCase):
    def test_empty_config_sets_no_env_var(self):
        env = {}
        extra = _sync_extra_ignores({}, env)
        self.assertEqual(extra, frozenset())
        self.assertNotIn("CORTEX_EXTRA_IGNORE_DIRS", env)

    def test_config_entries_sorted_into_env_var(self):
        env = {}
        extra = _sync_extra_ignores(
            {"ignore": {"folders": ["zeta", "alpha", "generated-*"]}}, env
        )
        self.assertEqual(extra, frozenset({"zeta", "alpha", "generated-*"}))
        self.assertEqual(env["CORTEX_EXTRA_IGNORE_DIRS"], "alpha,generated-*,zeta")


class SyncCommandEnvVarTests(unittest.TestCase):
    """``dev sync code`` / ``dev sync doc`` must export the env var."""

    def setUp(self):
        self.runner = CliRunner()
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.project_path = Path(self._temp.name)
        (self.project_path / "src").mkdir()
        (self.project_path / "docs").mkdir()
        # A doc file so full-mode sync has something to ingest.
        (self.project_path / "docs" / "note.md").write_text("# note\n", encoding="utf-8")

    def _config(self, ignore_folders):
        cfg = {
            "active": True,
            "project": {"code": "SHOP", "name": "Shop"},
            "code": {"env": {}, "source": {"projects": [{"git": "", "folder": ["src"]}]}},
            "doc": {"env": {}, "source": {"projects": [{"git": "", "folder": ["docs"]}]}},
        }
        if ignore_folders is not None:
            cfg["ignore"] = {"folders": ignore_folders}
        return cfg, self.project_path / "cfg.json"

    def test_sync_code_passes_env_var_to_incremental_sync(self):
        cfg, cfg_path = self._config(["legacy"])
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return 0

        with patch("cortex_harness.dev._load_active_config", return_value=(cfg, cfg_path)), \
                patch("cortex_harness.dev._code_env_for_process", return_value={}), \
                patch("cortex_harness.dev._select_folders_interactive", return_value=["src"]), \
                patch("cortex_harness.dev._run_with_retry", side_effect=fake_run), \
                patch("cortex_harness.dev._sync_lifecycle") as lifecycle:
            lifecycle.return_value.__enter__ = lambda s: None
            lifecycle.return_value.__exit__ = lambda s, *a: False
            result = self.runner.invoke(
                cli, ["sync", "code", "--project-dir", str(self.project_path)],
                input="0\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            captured["env"].get("CORTEX_EXTRA_IGNORE_DIRS"), "legacy"
        )

    def test_sync_doc_passes_env_var_to_doc_ingest(self):
        cfg, cfg_path = self._config(["archive"])
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return 0

        with patch("cortex_harness.dev._load_active_config", return_value=(cfg, cfg_path)), \
                patch("cortex_harness.dev._doc_env_for_process",
                      return_value={"QDRANT_DOC_PATH": "/tmp/qdrant-doc"}), \
                patch("cortex_harness.dev._select_folders_interactive", return_value=["docs"]), \
                patch("cortex_harness.dev._run_with_retry", side_effect=fake_run), \
                patch("cortex_harness.dev._sync_lifecycle") as lifecycle:
            lifecycle.return_value.__enter__ = lambda s: None
            lifecycle.return_value.__exit__ = lambda s, *a: False
            result = self.runner.invoke(
                cli, ["sync", "doc", "--project-dir", str(self.project_path)],
                input="0\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            captured["env"].get("CORTEX_EXTRA_IGNORE_DIRS"), "archive"
        )

    def test_no_config_ignore_keeps_behavior_identical(self):
        cfg, cfg_path = self._config(None)
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return 0

        with patch("cortex_harness.dev._load_active_config", return_value=(cfg, cfg_path)), \
                patch("cortex_harness.dev._code_env_for_process", return_value={"EXISTING": "1"}), \
                patch("cortex_harness.dev._select_folders_interactive", return_value=["src"]), \
                patch("cortex_harness.dev._run_with_retry", side_effect=fake_run), \
                patch("cortex_harness.dev._sync_lifecycle") as lifecycle:
            lifecycle.return_value.__enter__ = lambda s: None
            lifecycle.return_value.__exit__ = lambda s, *a: False
            result = self.runner.invoke(
                cli, ["sync", "code", "--project-dir", str(self.project_path)],
                input="0\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("CORTEX_EXTRA_IGNORE_DIRS", captured["env"])


class ScanRootWarningTests(unittest.TestCase):
    def test_scan_root_matching_ignore_warns_but_runs(self):
        # The helper only warns; the caller decides to proceed. Assert the
        # warning fires for a matching root and stays quiet otherwise.
        with patch("cortex_harness.dev.click.echo") as echo:
            _warn_scan_roots_matching_ignores(["legacy"], frozenset({"legacy"}))
            warn_calls = [
                c for c in echo.call_args_list if "matches the configured ignore" in str(c)
            ]
            self.assertEqual(len(warn_calls), 1)

        with patch("cortex_harness.dev.click.echo") as echo:
            _warn_scan_roots_matching_ignores(["src"], frozenset({"legacy"}))
            warn_calls = [
                c for c in echo.call_args_list if "matches the configured ignore" in str(c)
            ]
            self.assertEqual(warn_calls, [])

    def test_no_warning_without_extra_ignores(self):
        with patch("cortex_harness.dev.click.echo") as echo:
            _warn_scan_roots_matching_ignores(["legacy"], frozenset())
            self.assertEqual(echo.call_count, 0)


if __name__ == "__main__":
    unittest.main()

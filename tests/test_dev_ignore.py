"""Tests for user-configurable ignore folders (plan 260909-0854).

Covers the ``ignore.folders`` config section written by ``dev init``, the
``dev ignore add|remove|list`` command group, and the ``_ignore_folders``
config helper.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cortex_harness.dev import _ignore_folders, cli


class DevIgnoreInitTests(unittest.TestCase):
    """``dev init`` must persist the ignore prompt answer to config."""

    _LOCAL_TAIL = [""] * 60

    def _config_path(self, project_path: Path, env: str = "dev") -> Path:
        return project_path / ".cortext-harness" / "config" / f"{env}.json"

    def _write_existing_config(self, project_path: Path, env: str, cfg: dict) -> Path:
        cfg_dir = project_path / ".cortext-harness" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        path = cfg_dir / f"{env}.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def _existing_minimal(self) -> dict:
        return {
            "active": True,
            "project": {"code": "SHOP", "name": "Shop"},
            "storage_backend": "local",
            "code": {"env": {}, "source": {"projects": [
                {"git": "", "folder": ["src"]},
            ]}},
            "doc": {"env": {}, "source": {"projects": [
                {"git": "", "folder": ["docs"]},
            ]}},
        }

    def test_init_persists_ignore_folders_to_config(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            inputs = [
                "SHOP",                       # project code
                "",                           # project name
                "local",                      # storage backend
                "default",                    # CORTEX_STORAGE_INSTANCE
                "",                           # CORTEX_DATA_HOME
                "",                           # code GRAPH_PROVIDER -> falkordb
                "",                           # code FALKORDB_GRAPH
                "",                           # code QDRANT_COLLECTION
                "",                           # code EMBEDDING_MODEL
                "",                           # code BATCH_SIZE
                "",                           # code MAX_EMBED_CHARS
                "",                           # code device
                "",                           # doc GRAPH_PROVIDER
                "",                           # doc FALKORDB_GRAPH
                "",                           # doc EMBEDDING_MODEL
                "",                           # doc BATCH_SIZE
                "",                           # doc MAX_EMBED_CHARS
                "",                           # doc device
                "",                           # code git URL (blank = local)
                "src",                        # code source folders
                "",                           # doc git URL
                "docs",                       # doc folders
                "legacy, generated-*",        # ignore folders
            ]
            result = runner.invoke(
                cli, ["init", str(project_path)], input="\n".join(inputs)
            )

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["ignore"]["folders"], ["legacy", "generated-*"])

    def test_init_blank_ignore_prompt_writes_no_entry(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            result = runner.invoke(
                cli, ["init", str(project_path)], input="\n" * 60
            )

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            # Fresh init + blank answer -> no ignore section, and certainly no
            # empty-string entry.
            self.assertNotIn("ignore", cfg)

    def test_reinit_keeps_existing_ignore_folders_as_default(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            existing = self._existing_minimal()
            existing["ignore"] = {"folders": ["legacy", "sandbox"]}
            self._write_existing_config(project_path, "dev", existing)

            # All-Enter: the ignore prompt must default to the stored list.
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            self.assertEqual(cfg["ignore"]["folders"], ["legacy", "sandbox"])

    def test_reinit_can_clear_last_ignore_entry(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            existing = self._existing_minimal()
            existing["ignore"] = {"folders": ["legacy"]}
            self._write_existing_config(project_path, "dev", existing)

            inputs = [""] * 22 + [","]  # comma-only answer -> no entries
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(inputs))

            self.assertEqual(result.exit_code, 0, result.output)
            cfg = json.loads(self._config_path(project_path).read_text(encoding="utf-8"))
            # Existing section + cleared answer -> stored as [] (key kept).
            self.assertEqual(cfg["ignore"]["folders"], [])


class DevIgnoreCommandsTests(unittest.TestCase):
    """``dev ignore add|remove|list`` edit the active env config."""

    def setUp(self):
        self.runner = CliRunner()
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.project_path = Path(self._temp.name)
        cfg_dir = self.project_path / ".cortext-harness" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = cfg_dir / "dev.json"

    def _write_config(self, ignore_folders):
        cfg = {
            "active": True,
            "project": {"code": "SHOP", "name": "Shop"},
            "code": {"env": {}, "source": {"projects": []}},
            "doc": {"env": {}, "source": {"projects": []}},
        }
        if ignore_folders is not None:
            cfg["ignore"] = {"folders": ignore_folders}
        self.cfg_path.write_text(json.dumps(cfg), encoding="utf-8")

    def _read_config(self) -> dict:
        return json.loads(self.cfg_path.read_text(encoding="utf-8"))

    def test_ignore_add_merges_and_dedupes(self):
        self._write_config(["legacy"])

        result = self.runner.invoke(
            cli,
            ["ignore", "add", "--project-dir", str(self.project_path),
             "generated-*", "legacy", "sandbox"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        cfg = self._read_config()
        self.assertEqual(cfg["ignore"]["folders"], ["legacy", "generated-*", "sandbox"])
        self.assertIn("[ok] ignore add: generated-*", result.output)
        self.assertIn("already ignored: legacy", result.output)

    def test_ignore_remove_reports_missing(self):
        self._write_config(["legacy", "sandbox"])

        result = self.runner.invoke(
            cli,
            ["ignore", "remove", "--project-dir", str(self.project_path),
             "legacy", "nope"],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        cfg = self._read_config()
        self.assertEqual(cfg["ignore"]["folders"], ["sandbox"])
        self.assertIn("[ok] ignore remove: legacy", result.output)
        self.assertIn("not in ignore list: nope", result.output)

    def test_ignore_list_prints_entries(self):
        self._write_config(["legacy", "generated-*"])

        result = self.runner.invoke(
            cli, ["ignore", "list", "--project-dir", str(self.project_path)]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("legacy", result.output)
        self.assertIn("generated-*", result.output)

    def test_ignore_list_empty_message(self):
        self._write_config([])

        result = self.runner.invoke(
            cli, ["ignore", "list", "--project-dir", str(self.project_path)]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No ignore folders configured", result.output)

    def test_ignore_commands_fail_closed_without_init(self):
        result = self.runner.invoke(
            cli,
            ["ignore", "add", "--project-dir", str(self.project_path), "legacy"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Run 'dev init' first", result.output)

        result = self.runner.invoke(
            cli, ["ignore", "list", "--project-dir", str(self.project_path)]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Run 'dev init' first", result.output)


class IgnoreFoldersHelperTests(unittest.TestCase):
    """``_ignore_folders`` tolerates missing/malformed config sections."""

    def test_missing_section_returns_empty(self):
        self.assertEqual(_ignore_folders({}), ())

    def test_null_section_returns_empty(self):
        self.assertEqual(_ignore_folders({"ignore": None}), ())

    def test_non_dict_section_returns_empty(self):
        self.assertEqual(_ignore_folders({"ignore": "legacy"}), ())

    def test_non_list_folders_returns_empty(self):
        self.assertEqual(_ignore_folders({"ignore": {"folders": "legacy"}}), ())

    def test_non_string_entries_skipped(self):
        cfg = {"ignore": {"folders": ["legacy", 42, None, "sandbox"]}}
        self.assertEqual(_ignore_folders(cfg), ("legacy", "sandbox"))

    def test_blank_entries_skipped_and_order_preserved(self):
        cfg = {"ignore": {"folders": ["b", " a ", "b", "a", ""]}}
        self.assertEqual(_ignore_folders(cfg), ("b", "a"))


if __name__ == "__main__":
    unittest.main()

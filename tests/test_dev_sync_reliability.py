import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cortex_harness.dev import _code_sync_summary_path, _dedupe_scan_roots, _run_with_retry, cli


class DevSyncReliabilityTests(unittest.TestCase):
    def _write_local_config(self, root: Path, source: Path) -> None:
        config_path = root / ".cortext-harness" / "config" / "dev.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({
                "active": True,
                "project": {"code": "shop", "name": "Shop"},
                "code": {
                    "env": {
                        "GRAPH_PROVIDER": "falkordb",
                        "CODE_GRAPH_PROVIDER": "falkordb",
                        "FALKORDB_GRAPH": "shop",
                        "QDRANT_COLLECTION": "shop",
                        "CORTEX_DATA_HOME": str(root / "data"),
                    },
                    "source": {"projects": [{"git": "", "folder": [str(source)]}]},
                },
                "doc": {
                    "env": {
                        "GRAPH_PROVIDER": "falkordb",
                        "DOC_GRAPH_PROVIDER": "falkordb",
                        "FALKORDB_GRAPH": "shop_doc",
                        "CORTEX_DATA_HOME": str(root / "data"),
                    },
                    "source": {"projects": [{"git": "", "folder": [str(source)]}]},
                },
            }),
            encoding="utf-8",
        )

    def test_child_summary_paths_are_scoped_and_unique(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = _code_sync_summary_path(root, "project")
            second = _code_sync_summary_path(root, "project")
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, root / ".cache" / "incremental_sync_summaries")

    def test_parent_scan_root_covers_configured_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "module").mkdir()
            self.assertEqual(
                _dedupe_scan_roots(["module", "."], root),
                ["."],
            )

    def test_lock_busy_exit_is_not_retried(self):
        completed = subprocess.CompletedProcess(["worker"], 2)
        with patch("cortex_harness.dev.subprocess.run", return_value=completed) as run:
            self.assertEqual(
                _run_with_retry(
                    ["worker"],
                    max_retries=3,
                    non_retryable_exit_codes={2},
                ),
                2,
            )
        run.assert_called_once()

    def test_code_sync_dry_run_uses_owner_local_storage(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "src"
            source.mkdir()
            (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
            self._write_local_config(root, source)

            result = runner.invoke(
                cli,
                ["sync", "code", "--project-dir", str(root), "--dry-run"],
                input="\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--falkordb-path", result.output)
        self.assertNotIn("--falkordb-uri", result.output)
        self.assertNotIn("--qdrant-url", result.output)

    def test_doc_sync_dry_run_uses_doc_owner_paths(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "docs"
            source.mkdir()
            (source / "guide.md").write_text("# Guide\n", encoding="utf-8")
            self._write_local_config(root, source)

            result = runner.invoke(
                cli,
                ["sync", "doc", "--project-dir", str(root), "--dry-run"],
                input="\n",
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--falkordb-path", result.output)
        self.assertIn("--qdrant-path", result.output)
        self.assertIn("/qdrant/doc", result.output)
        self.assertNotIn("--qdrant-url", result.output)


if __name__ == "__main__":
    unittest.main()

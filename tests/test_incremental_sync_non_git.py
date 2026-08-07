import asyncio
import json
import subprocess
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


class IncrementalSyncNonGitTests(unittest.TestCase):
    def test_unborn_git_repository_uses_hash_bootstrap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "main.cpp").write_text("int value = 1;\n", encoding="utf-8")
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "unborn-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                ]
            )
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)

    def test_non_git_root_bootstraps_then_uses_hash_inventory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            summary_path = root / ".cache" / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "non-git-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )
            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)

            source.write_text("int value = 2;\n", encoding="utf-8")
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["change_detection_effective"], "hash")

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 0)


if __name__ == "__main__":
    unittest.main()

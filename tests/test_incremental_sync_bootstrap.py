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


class IncrementalSyncBootstrapTests(unittest.TestCase):
    def test_missing_baseline_bootstraps_all_source_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Test User"],
                check=True,
            )

            (root / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "main.cpp"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-q", "-m", "add source"],
                check=True,
            )

            (root / "README.md").write_text("Documentation only\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "commit", "-q", "-m", "update docs"],
                check=True,
            )

            summary_path = root / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root",
                    str(root),
                    "--project-id",
                    "bootstrap-test",
                    "--parsers",
                    "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path",
                    str(summary_path),
                ]
            )
            commands = []

            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command),
            ):
                return_code = asyncio.run(incremental_sync._run_incremental(args))

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(return_code, 0)
            self.assertEqual(summary["before_sha"], "full_scan")
            self.assertEqual(summary["diff"], {"entries": 1, "changed": 1, "deleted": 0})
            self.assertEqual([item["parser"] for item in summary["primary_parsers"]], ["cplus"])
            self.assertEqual(len(commands), 1)
            self.assertNotIn("--incremental", commands[0])
            self.assertNotIn("--changed-files-manifest", commands[0])
            self.assertNotIn("--deleted-files-manifest", commands[0])


if __name__ == "__main__":
    unittest.main()

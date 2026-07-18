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


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test User")


class IncrementalSyncSubmoduleTests(unittest.TestCase):
    def test_ignore_mode_excludes_submodule_files_from_first_inventory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            child = base / "child"
            parent = base / "parent"
            child.mkdir()
            parent.mkdir()
            init_repo(child)
            (child / "main.cpp").write_text("int child = 1;\n", encoding="utf-8")
            git(child, "add", "-A")
            git(child, "commit", "-q", "-m", "initial")
            init_repo(parent)
            subprocess.run(
                [
                    "git", "-c", "protocol.file.allow=always", "-C", str(parent),
                    "submodule", "add", "-q", str(child), "vendor/child",
                ],
                check=True,
            )
            git(parent, "commit", "-q", "-am", "add child")
            args = incremental_sync.parse_args(
                [
                    "--root", str(parent),
                    "--project-id", "ignore-submodule-test",
                    "--parsers", "cplus",
                    "--submodules", "ignore",
                    "--no-sync-messages",
                ]
            )
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 0)

    def test_dirty_initialized_submodule_is_scanned_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            child = base / "child"
            parent = base / "parent"
            child.mkdir()
            parent.mkdir()
            init_repo(child)
            source = child / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(child, "add", "-A")
            git(child, "commit", "-q", "-m", "initial")
            init_repo(parent)
            subprocess.run(
                [
                    "git", "-c", "protocol.file.allow=always", "-C", str(parent),
                    "submodule", "add", "-q", str(child), "vendor/child",
                ],
                check=True,
            )
            git(parent, "commit", "-q", "-am", "add child")
            sub_source = parent / "vendor" / "child" / "main.cpp"
            summary_path = parent / ".cache" / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(parent),
                    "--project-id", "submodule-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--summary-path", str(summary_path),
                ]
            )

            commands = []
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(len(commands), 1)

            sub_source.write_text("int value = 2;\n", encoding="utf-8")
            commands.clear()
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(len(commands), 1)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["diff"]["changed"], 1)
            self.assertEqual(
                [item["source_prefix"] for item in summary["repositories"]],
                [".", "vendor/child"],
            )

            commands.clear()
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(commands, [])

            sub_repo = parent / "vendor" / "child"
            git(sub_repo, "config", "user.email", "test@example.com")
            git(sub_repo, "config", "user.name", "Test User")
            sub_source.write_text("int value = 3;\n", encoding="utf-8")
            git(sub_repo, "add", "main.cpp")
            git(sub_repo, "commit", "-q", "-m", "independent child commit")
            commands.clear()
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(len(commands), 1)

            subprocess.run(
                ["git", "-C", str(parent), "submodule", "deinit", "-q", "-f", "vendor/child"],
                check=True,
            )
            commands.clear()
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(commands, [])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["outcome"], "partial_coverage")
            self.assertTrue(
                any(
                    item["code"] == "submodule_uninitialized"
                    for item in summary["coverage_warnings"]
                )
            )

            parent_source = parent / "parent.cpp"
            parent_source.write_text("int parent_value = 1;\n", encoding="utf-8")
            args.reconcile = True
            commands.clear()
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(len(commands), 1)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["outcome"], "partial_coverage")


if __name__ == "__main__":
    unittest.main()

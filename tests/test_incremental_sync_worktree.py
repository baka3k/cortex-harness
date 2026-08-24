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
from tools.common.incremental_sync_state import state_file_path  # noqa: E402


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class IncrementalSyncWorktreeTests(unittest.TestCase):
    def test_missing_repository_baseline_reconciles_in_hybrid_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            (root / "main.cpp").write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            project_id = "missing-baseline-test"
            summary_path = root / ".cache" / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", project_id,
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )
            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            state_path = Path(state_file_path(None, project_id, str(root)))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["repositories"]["."]["last_good_sha"] = "deadbeef"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    item["code"] == "repository_baseline_missing"
                    for item in summary["coverage_warnings"]
                )
            )

    def test_explicit_after_sha_must_match_checked_out_head(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "first")
            first_sha = git(root, "rev-parse", "HEAD")
            source.write_text("int value = 2;\n", encoding="utf-8")
            git(root, "commit", "-q", "-am", "second")
            summary_path = root / ".cache" / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "after-sha-test",
                    "--parsers", "cplus",
                    "--after-sha", first_sha,
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )
            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIn("must match the checked-out HEAD", summary["error"])

    def test_full_then_uncommitted_edit_then_unchanged_repeat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            summary_path = root / "summary.json"

            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "worktree-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
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

            source.write_text("int main() { return 1; }\n", encoding="utf-8")
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
            self.assertGreaterEqual(summary["change_sources"]["unstaged"], 1)

            commands.clear()
            with patch.object(
                incremental_sync,
                "_run",
                side_effect=lambda command, **_kwargs: commands.append(command) or "",
            ):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            self.assertEqual(commands, [])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["outcome"], "no_changes")

    def test_revert_after_dirty_scan_is_not_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            original = "int value = 1;\n"
            source.write_text(original, encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "revert-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                ]
            )

            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
            source.write_text("int value = 2;\n", encoding="utf-8")
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)

            source.write_text(original, encoding="utf-8")
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)

    def test_failed_scan_keeps_old_inventory_for_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "failure-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                ]
            )
            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)

            source.write_text("int value = 2;\n", encoding="utf-8")
            with patch.object(incremental_sync, "_run", side_effect=RuntimeError("boom")):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)

    def test_dirty_state_replays_full_scan_when_inventory_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            summary_path = root / "summary.json"
            common_args = [
                "--root", str(root),
                "--project-id", "dirty-replay-test",
                "--parsers", "cplus",
                "--no-sync-messages",
                "--no-graph",
                "--summary-path", str(summary_path),
            ]
            regular_args = incremental_sync.parse_args(common_args)
            full_args = incremental_sync.parse_args([*common_args, "--full-scan"])

            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(
                    asyncio.run(incremental_sync._run_incremental(regular_args)),
                    0,
                )

            child_error = subprocess.CalledProcessError(
                1,
                ["cplus_analyzer.py"],
                stderr="parser failed",
            )
            with patch.object(incremental_sync, "_run", side_effect=child_error):
                self.assertEqual(
                    asyncio.run(incremental_sync._run_incremental(full_args)),
                    1,
                )

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(
                    asyncio.run(incremental_sync._run_incremental(regular_args)),
                    0,
                )
                self.assertEqual(run.call_count, 1)
                recovery_command = run.call_args.args[0]
                self.assertIn("--incremental", recovery_command)
                changed_index = recovery_command.index("--changed-files-manifest")
                changed_manifest = Path(recovery_command[changed_index + 1])
                self.assertEqual(
                    json.loads(changed_manifest.read_text(encoding="utf-8"))["files"],
                    ["main.cpp"],
                )

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["full_scan"])
            self.assertTrue(summary["recovery_full_scan"])
            self.assertEqual(summary["diff"]["changed"], 1)
            self.assertEqual(summary["outcome"], "scanned")

    def test_dirty_recovery_preserves_deleted_file_cleanup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            summary_path = root / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "dirty-deletion-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )

            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)

            source.unlink()
            child_error = subprocess.CalledProcessError(
                1,
                ["cplus_analyzer.py"],
                stderr="delete cleanup failed",
            )
            with patch.object(incremental_sync, "_run", side_effect=child_error):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)
                recovery_command = run.call_args.args[0]
                self.assertIn("--incremental", recovery_command)
                deleted_index = recovery_command.index("--deleted-files-manifest")
                deleted_manifest = Path(recovery_command[deleted_index + 1])
                self.assertEqual(
                    json.loads(deleted_manifest.read_text(encoding="utf-8"))["files"],
                    ["main.cpp"],
                )

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["recovery_full_scan"])
            self.assertEqual(summary["diff"]["changed"], 0)
            self.assertEqual(summary["diff"]["deleted"], 1)
            self.assertFalse(summary["state_after"]["dirty"])

    def test_failed_first_bootstrap_retains_inventory_for_later_deletion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            summary_path = root / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "bootstrap-deletion-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )
            child_error = subprocess.CalledProcessError(
                1,
                ["cplus_analyzer.py"],
                stderr="bootstrap failed after a partial write",
            )

            with patch.object(incremental_sync, "_run", side_effect=child_error):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)

            state_path = Path(
                incremental_sync.state_file_path(
                    None,
                    "bootstrap-deletion-test",
                    str(root),
                )
            )
            dirty_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(dirty_state["dirty"])
            self.assertEqual(len(dirty_state["dirty_inventory_paths"]), 1)

            source.unlink()
            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)
                recovery_command = run.call_args.args[0]
                self.assertIn("--incremental", recovery_command)
                deleted_index = recovery_command.index("--deleted-files-manifest")
                deleted_manifest = Path(recovery_command[deleted_index + 1])
                self.assertEqual(
                    json.loads(deleted_manifest.read_text(encoding="utf-8"))["files"],
                    ["main.cpp"],
                )

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["recovery_full_scan"])
            self.assertEqual(summary["diff"]["deleted"], 1)
            self.assertFalse(summary["state_after"]["dirty"])

    def test_legacy_dirty_state_uses_last_good_inventory_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            (root / "main.cpp").write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            project_id = "legacy-dirty-test"
            summary_path = root / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", project_id,
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )

            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)

            state_path = Path(state_file_path(None, project_id, str(root)))
            legacy_state = json.loads(state_path.read_text(encoding="utf-8"))
            legacy_state["dirty"] = True
            legacy_state.pop("dirty_inventory_paths", None)
            state_path.write_text(json.dumps(legacy_state), encoding="utf-8")

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)
                self.assertEqual(run.call_count, 1)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    item["code"] == "legacy_dirty_recovery_from_last_good_inventory"
                    for item in summary["coverage_warnings"]
                )
            )
            self.assertFalse(summary["state_after"]["dirty"])

    def test_dirty_state_without_any_inventory_remains_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            project_id = "dirty-without-evidence-test"
            summary_path = root / "summary.json"
            state_path = Path(state_file_path(None, project_id, str(root)))
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"schema_version": 2, "dirty": True}),
                encoding="utf-8",
            )
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", project_id,
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)
                self.assertEqual(run.call_count, 0)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIn("project-scoped storage reconciliation", summary["error"])
            self.assertTrue(summary["state_after"]["dirty"])

    def test_dirty_recovery_blocks_when_last_good_inventory_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "old.cpp"
            source.write_text("int old_value = 1;\n", encoding="utf-8")
            git(root, "add", "old.cpp")
            git(root, "commit", "-q", "-m", "initial")
            project_id = "missing-last-good-test"
            summary_path = root / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", project_id,
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )

            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)

            source.unlink()
            child_error = subprocess.CalledProcessError(
                1,
                ["cplus_analyzer.py"],
                stderr="delete cleanup failed",
            )
            with patch.object(incremental_sync, "_run", side_effect=child_error):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)

            state_path = Path(state_file_path(None, project_id, str(root)))
            dirty_state = json.loads(state_path.read_text(encoding="utf-8"))
            Path(dirty_state["inventory_path"]).unlink()

            with patch.object(incremental_sync, "_run", return_value="") as run:
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)
                self.assertEqual(run.call_count, 0)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIn("last-good inventory", summary["error"])
            self.assertTrue(summary["state_after"]["dirty"])

    def test_mid_run_source_change_marks_state_dirty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            git(root, "config", "user.email", "test@example.com")
            git(root, "config", "user.name", "Test User")
            source = root / "main.cpp"
            source.write_text("int value = 1;\n", encoding="utf-8")
            git(root, "add", "main.cpp")
            git(root, "commit", "-q", "-m", "initial")
            summary_path = root / ".cache" / "summary.json"
            args = incremental_sync.parse_args(
                [
                    "--root", str(root),
                    "--project-id", "drift-test",
                    "--parsers", "cplus",
                    "--no-sync-messages",
                    "--no-graph",
                    "--summary-path", str(summary_path),
                ]
            )
            with patch.object(incremental_sync, "_run", return_value=""):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 0)

            source.write_text("int value = 2;\n", encoding="utf-8")

            def mutate_during_scan(*_args, **_kwargs):
                source.write_text("int value = 3;\n", encoding="utf-8")
                return ""

            with patch.object(incremental_sync, "_run", side_effect=mutate_during_scan):
                self.assertEqual(asyncio.run(incremental_sync._run_incremental(args)), 1)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["outcome"], "source_changed")
            self.assertTrue(summary["state_after"]["dirty"])


if __name__ == "__main__":
    unittest.main()

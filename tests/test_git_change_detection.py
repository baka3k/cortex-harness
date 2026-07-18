import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.git_diff import (  # noqa: E402
    collect_git_diff_entries,
    collect_worktree_entries,
    discover_repository_scopes,
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
    ).strip()


def init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test User")


class GitChangeDetectionTests(unittest.TestCase):
    def test_nested_module_diff_is_relative_and_excludes_sibling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            init_repo(repo)
            module = repo / "module-a"
            sibling = repo / "module-b"
            (module / "src").mkdir(parents=True)
            sibling.mkdir()
            (module / "src" / "main.py").write_text("one\n", encoding="utf-8")
            (sibling / "other.py").write_text("one\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "initial")
            before = git(repo, "rev-parse", "HEAD")

            (module / "src" / "main.py").write_text("two\n", encoding="utf-8")
            (sibling / "other.py").write_text("two\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "update")
            after = git(repo, "rev-parse", "HEAD")

            entries = collect_git_diff_entries(str(module), before, after)
            self.assertEqual(
                {(item.status, item.old_path, item.new_path) for item in entries},
                {("M", None, "src/main.py")},
            )

    def test_worktree_collector_detects_staged_unstaged_and_untracked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            init_repo(repo)
            for name in ("staged.py", "unstaged.py"):
                Path(repo, name).write_text("one\n", encoding="utf-8")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "initial")

            Path(repo, "staged.py").write_text("two\n", encoding="utf-8")
            git(repo, "add", "staged.py")
            Path(repo, "unstaged.py").write_text("two\n", encoding="utf-8")
            Path(repo, "untracked.py").write_text("new\n", encoding="utf-8")

            entries = collect_worktree_entries(str(repo))
            by_path = {item.new_path or item.old_path: item.source for item in entries}
            self.assertEqual(by_path["staged.py"], "staged")
            self.assertEqual(by_path["unstaged.py"], "unstaged")
            self.assertEqual(by_path["untracked.py"], "untracked")

    def test_recursive_scope_discovery_reports_uninitialized_submodule(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            child = base / "child"
            parent = base / "parent"
            child.mkdir()
            parent.mkdir()
            init_repo(child)
            Path(child, "lib.py").write_text("one\n", encoding="utf-8")
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

            scopes, warnings = discover_repository_scopes(str(parent), recursive=True)
            self.assertEqual([item.source_prefix for item in scopes], [".", "vendor/child"])
            self.assertEqual(warnings, [])

            subprocess.run(
                ["git", "-C", str(parent), "submodule", "deinit", "-q", "-f", "vendor/child"],
                check=True,
            )
            scopes, warnings = discover_repository_scopes(str(parent), recursive=True)
            self.assertEqual([item.source_prefix for item in scopes], ["."])
            self.assertTrue(any(item["code"] == "submodule_uninitialized" for item in warnings))


if __name__ == "__main__":
    unittest.main()

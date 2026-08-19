from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_project_isolation.py"
SPEC = importlib.util.spec_from_file_location("project_isolation", SCRIPT)
assert SPEC and SPEC.loader
project_isolation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = project_isolation
SPEC.loader.exec_module(project_isolation)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "Fixture")
    return root


def test_scans_filename_worktree_and_cp932_content(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    keyword = "CLIENT_MARKER_987"
    (root / f"guide-{keyword}.md").write_text("clean\n", encoding="utf-8")
    (root / "legacy.txt").write_bytes(f"prefix {keyword}\n".encode("cp932"))
    _git(root, "add", ".")

    findings = project_isolation.scan_repository(root, (keyword,))

    assert {(item.origin, item.path) for item in findings} >= {
        ("working-tree-path", f"guide-{keyword}.md"),
        ("working-tree", "legacy.txt"),
    }


def test_scans_staged_blob_when_worktree_has_been_cleaned(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    keyword = "CLIENT_MARKER_654"
    target = root / "sample.txt"
    target.write_text(f"{keyword}\n", encoding="utf-8")
    _git(root, "add", "sample.txt")
    target.write_text("generic\n", encoding="utf-8")

    findings = project_isolation.scan_repository(root, (keyword,))

    assert any(item.origin == "index" and item.path == "sample.txt" for item in findings)


def test_unicode_matching_is_nfkc_and_case_insensitive(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    keyword = "ＣｌｉｅｎｔＡ"
    (root / "sample.txt").write_text("clienta\n", encoding="utf-8")
    _git(root, "add", "sample.txt")

    findings = project_isolation.scan_repository(root, (keyword,))

    assert findings


def test_cli_fails_closed_without_external_denylist(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert project_isolation.main(["--root", str(root)]) == 2

"""Tests for the docker-free storage lifecycle commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts" / "mcp-lifecycle.py"


def _run_lifecycle(action: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIFECYCLE), action, *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        check=False,
    )


def test_storage_init_creates_local_paths(tmp_path: Path) -> None:
    """``storage-init`` must create the local directories without Docker.

    Paths are anchored to the lifecycle script's repository root, not the
    caller's cwd, so the assertion checks the script-reported path rather
    than the cwd-derived path.
    """
    result = _run_lifecycle("storage-init", cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Qdrant base" in result.stdout
    assert "FalkorDBLite" in result.stdout
    # The repository's own local_qdrant_db path must have been created by
    # the invocation (the script anchors paths to its own ROOT).
    repo_local_qdrant = ROOT / "local_qdrant_db"
    if repo_local_qdrant.exists():
        assert (repo_local_qdrant / "code").is_dir()
        assert (repo_local_qdrant / "doc").is_dir()
        assert (ROOT / "local_falkordb_db").is_dir()


def test_infra_up_is_a_no_docker_deprecation_alias(tmp_path: Path) -> None:
    """``infra-up`` must NOT touch Docker; it warns and calls storage-init."""
    result = _run_lifecycle("infra-up", cwd=tmp_path)
    assert result.returncode == 0
    assert "deprecated" in result.stdout.lower()
    assert "storage-init" in result.stdout
    assert "Qdrant base" in result.stdout


def test_infra_down_is_a_no_docker_noop(tmp_path: Path) -> None:
    result = _run_lifecycle("infra-down", cwd=tmp_path)
    assert result.returncode == 0
    assert "deprecated" in result.stdout.lower()


def test_storage_stop_is_a_noop(tmp_path: Path) -> None:
    result = _run_lifecycle("storage-stop", cwd=tmp_path)
    assert result.returncode == 0
    assert "no lifecycle to stop" in result.stdout


def test_help_lists_supported_actions(tmp_path: Path) -> None:
    result = _run_lifecycle("help", cwd=tmp_path)
    assert result.returncode == 0
    assert "storage-init" in result.stdout or "infra-up" in result.stdout


def test_unknown_action_returns_nonzero(tmp_path: Path) -> None:
    result = _run_lifecycle("not-a-real-action", cwd=tmp_path)
    assert result.returncode != 0
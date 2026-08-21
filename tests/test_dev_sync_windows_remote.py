"""Windows remote-sync plumbing tests (plan 260821-2115).

Covers the changes that make ``dev sync code`` work on Windows against a
local Docker FalkorDB without touching the macOS local-embedded default:

- ``storage_overlay`` remote branch (FALKORDB_URI in, local paths out)
- local-mode overlay stays byte-identical (macOS no-change guard)
- ``_neo4j_args_code`` / ``_env_to_neo4j_args`` emit ``--falkordb-uri``
- ``_storage_env_for_process`` honors top-level ``storage_backend``/``remote``
- ``_normalize_embed_device`` drops platform-impossible devices
- ``_write_summary`` survives Windows (no directory fsync)
- ``sync_processes(include_launchers=False)`` never matches other launchers
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "code-tiny"))

from cortex_harness.storage import resolve_storage, storage_overlay  # noqa: E402
from cortex_harness.storage.config import (  # noqa: E402
    ENV_FALKORDB_CODE,
    ENV_FALKORDB_DOC,
    ENV_FALKORDB_PATH,
    BackendMode,
)
from cortex_harness import dev as dev_module  # noqa: E402


REMOTE_CFG = {
    "storage_backend": "remote",
    "remote": {"falkordb_uri": "redis://127.0.0.1:6379"},
}


def _resolved(config: dict) -> "resolve_storage.__annotations__":
    return resolve_storage(REPO_ROOT, config=config)


# ---------------------------------------------------------------------------
# storage_overlay
# ---------------------------------------------------------------------------


def test_overlay_remote_falkordb_uri_replaces_local_paths() -> None:
    resolved = _resolved(dict(REMOTE_CFG))
    assert resolved.backend_mode == BackendMode.REMOTE
    overlay = storage_overlay(resolved, owner="code")
    assert overlay["FALKORDB_URI"] == "redis://127.0.0.1:6379"
    for key in (ENV_FALKORDB_PATH, ENV_FALKORDB_CODE, ENV_FALKORDB_DOC):
        assert key not in overlay, f"{key} must not leak into remote overlay"


def test_overlay_local_mode_unchanged() -> None:
    """macOS guard: the local overlay keys are exactly what they used to be."""
    resolved = _resolved({})
    assert resolved.backend_mode == BackendMode.LOCAL
    overlay = storage_overlay(resolved, owner="code")
    assert overlay[ENV_FALKORDB_PATH].endswith("data.rdb")
    assert ENV_FALKORDB_PATH in overlay
    assert "FALKORDB_URI" not in overlay


def test_overlay_remote_without_falkordb_keeps_local_graph() -> None:
    """qdrant-only remote projects keep the embedded graph path keys."""
    resolved = _resolved(
        {"storage_backend": "remote", "remote": {"qdrant_url": "http://127.0.0.1:6333"}}
    )
    overlay = storage_overlay(resolved, owner="code")
    assert overlay.get("QDRANT_URL") == "http://127.0.0.1:6333"
    assert ENV_FALKORDB_PATH in overlay
    assert "FALKORDB_URI" not in overlay


# ---------------------------------------------------------------------------
# dev.py argument builders
# ---------------------------------------------------------------------------


def test_neo4j_args_code_prefers_uri() -> None:
    args = dev_module._neo4j_args_code(
        {"CODE_GRAPH_PROVIDER": "falkordb", "FALKORDB_URI": "redis://127.0.0.1:6379",
         "FALKORDB_GRAPH": "cortext"}
    )
    assert "--falkordb-uri" in args
    assert args[args.index("--falkordb-uri") + 1] == "redis://127.0.0.1:6379"
    assert "--falkordb-path" not in args


def test_neo4j_args_code_local_unchanged() -> None:
    args = dev_module._neo4j_args_code(
        {"CODE_GRAPH_PROVIDER": "falkordb",
         "FALKORDB_PATH": r"C:\store\data.rdb", "FALKORDB_GRAPH": "cortext"}
    )
    assert "--falkordb-path" in args
    assert "--falkordb-uri" not in args


def test_env_to_neo4j_args_prefers_uri() -> None:
    args = dev_module._env_to_neo4j_args(
        {"DOC_GRAPH_PROVIDER": "falkordb", "FALKORDB_URI": "localhost:6379"}
    )
    assert "--falkordb-uri" in args
    assert "--falkordb-path" not in args


def test_storage_env_for_process_reads_top_level_backend() -> None:
    cfg = {
        "project": {"code": "cortext", "name": "cortext"},
        "storage_backend": "remote",
        "remote": {"falkordb_uri": "redis://127.0.0.1:6379"},
        "code": {
            "env": {
                "CODE_GRAPH_PROVIDER": "falkordb",
                "FALKORDB_GRAPH": "cortext",
                "QDRANT_COLLECTION": "cortext",
            },
            "source": {"projects": [{"git": "", "folder": ["."]}]},
        },
    }
    env = dev_module._storage_env_for_process(cfg, REPO_ROOT, dev_module.StorageRole.CODE)
    assert env.get("FALKORDB_URI") == "redis://127.0.0.1:6379"
    assert not env.get("FALKORDB_PATH")


# ---------------------------------------------------------------------------
# embed device normalization
# ---------------------------------------------------------------------------


def test_normalize_embed_device_passthrough_cpu() -> None:
    assert dev_module._normalize_embed_device("cpu") == "cpu"
    assert dev_module._normalize_embed_device("auto") == "auto"


def test_normalize_embed_device_drops_unavailable_mps() -> None:
    if sys.platform == "darwin":
        pytest.skip("mps is valid on darwin")
    value = dev_module._normalize_embed_device("mps")
    assert value in {"cpu", "cuda"}


def test_normalize_embed_device_drops_unavailable_cuda() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            pytest.skip("cuda genuinely available here")
    except Exception:
        pass
    assert dev_module._normalize_embed_device("cuda") == "cpu"


# ---------------------------------------------------------------------------
# _write_summary on win32 (no directory fsync crash)
# ---------------------------------------------------------------------------


def test_write_summary_roundtrip(tmp_path: Path) -> None:
    from tools.sync.incremental_sync import _write_summary

    target = tmp_path / "summaries" / "summary.json"
    _write_summary(str(target), {"status": "success", "counts": [1, 2, 3]})
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    # No stray tmp files left behind.
    leftovers = [p for p in target.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# sync process discovery: launchers are protected from lifecycle sweeps
# ---------------------------------------------------------------------------


def test_sync_processes_excludes_launchers_when_disabled(monkeypatch) -> None:
    from cortex_harness import sync_processes as sp

    dev_sync_argv = (
        r"C:\ai\cortex-harness\.venv\Scripts\python.exe",
        r"C:\ai\cortex-harness\cortex_harness\dev.py",
        "sync",
        "code",
    )
    worker_argv = (
        r"C:\ai\cortex-harness\.venv\Scripts\python.exe",
        r"C:\ai\cortex-harness\code-tiny\tools\sync\incremental_sync.py",
        "--root", r"C:\ai\cortex-harness",
    )
    table = {
        111: sp.ProcessRecord(pid=111, ppid=1, argv=dev_sync_argv),
        222: sp.ProcessRecord(pid=222, ppid=1, argv=worker_argv),
    }
    monkeypatch.setattr(sp, "process_table", lambda: table)

    matched_all = sp.sync_processes("code", root=REPO_ROOT, processes=table)
    assert {r.pid for r in matched_all} == {111, 222}

    matched_workers = sp.sync_processes(
        "code", root=REPO_ROOT, processes=table, include_launchers=False
    )
    assert {r.pid for r in matched_workers} == {222}


def test_graph_cli_prepare_graph_args_prefers_uri(monkeypatch) -> None:
    from tools.graph import cli as graph_cli

    args = SimpleNamespace(
        graph_provider="falkordb",
        falkordb_path=None,
        falkordb_graph=None,
        project_id="cortext",
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
    )
    monkeypatch.setenv("FALKORDB_URI", "redis://127.0.0.1:6379")
    monkeypatch.delenv("FALKORDB_PATH", raising=False)
    assert graph_cli.prepare_graph_args(args) is True
    assert args.falkordb_uri == "redis://127.0.0.1:6379"
    assert args.falkordb_path is None


def test_graph_cli_prepare_graph_args_local_synthesis(monkeypatch) -> None:
    from tools.graph import cli as graph_cli

    monkeypatch.delenv("FALKORDB_URI", raising=False)
    monkeypatch.delenv("FALKORDB_PATH", raising=False)
    args = SimpleNamespace(
        graph_provider="falkordb",
        falkordb_path=None,
        falkordb_graph="cortext",
        project_id="cortext",
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
    )
    assert graph_cli.prepare_graph_args(args) is True
    # Local mode still synthesizes the embedded path (macOS behavior).
    assert args.falkordb_path and args.falkordb_path.endswith("data.rdb")


def test_analyzer_env_propagates_uri(monkeypatch) -> None:
    from tools.sync.incremental_sync import _build_analyzer_env

    monkeypatch.setenv("FALKORDB_URI", "redis://127.0.0.1:6379")
    monkeypatch.delenv("FALKORDB_PATH", raising=False)
    args = SimpleNamespace(
        graph_provider="falkordb",
        falkordb_uri=None,
        falkordb_password=None,
        falkordb_ssl=False,
        falkordb_path=None,
        falkordb_graph="cortext",
        neo4j_uri=None,
        neo4j_user=None,
        neo4j_password=None,
        neo4j_db="cortext",
        no_graph=False,
        qdrant_url=None,
        cache_dir=None,
        embed_model=None,
        embed_device=None,
        embed_batch_size=None,
        max_embed_chars=None,
    )
    env = _build_analyzer_env(args)
    assert env["FALKORDB_URI"] == "redis://127.0.0.1:6379"
    assert "FALKORDB_PATH" not in env

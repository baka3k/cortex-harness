"""Tests for the docker-free storage lifecycle commands."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts" / "mcp-lifecycle.py"


def _load_lifecycle_module():
    spec = importlib.util.spec_from_file_location("mcp_lifecycle_storage_test", LIFECYCLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_lifecycle(
    action: str,
    *args: str,
    cwd: Path | None = None,
    data_home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if data_home is not None:
        environment["CORTEX_DATA_HOME"] = str(data_home)
        environment["CORTEX_STORAGE_INSTANCE"] = "test"
    return subprocess.run(
        [sys.executable, str(LIFECYCLE), action, *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=environment,
        check=False,
    )


def test_storage_init_creates_centralized_instance_manifest(tmp_path: Path) -> None:
    data_home = tmp_path / "account-data"
    result = _run_lifecycle("storage-init", cwd=tmp_path, data_home=data_home)
    assert result.returncode == 0, result.stdout + result.stderr
    instance = data_home / "v1" / "instances" / "test"
    assert (instance / "manifest.json").is_file()
    assert (instance / "qdrant" / "code").is_dir()
    assert (instance / "qdrant" / "doc").is_dir()
    assert (instance / "falkordb" / "code").is_dir()
    assert (instance / "falkordb" / "doc").is_dir()
    assert str(data_home) in result.stdout


def test_lifecycle_storage_resolution_uses_active_config_only_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    for key in (
        "CORTEX_DATA_HOME",
        "CORTEX_STORAGE_INSTANCE",
        "QDRANT_PATH",
        "QDRANT_CODE_PATH",
        "QDRANT_DOC_PATH",
        "FALKORDB_PATH",
        "FALKORDB_CODE_PATH",
        "FALKORDB_DOC_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    data_home = tmp_path / "configured-data"
    code_qdrant = tmp_path / "configured-code-qdrant"
    doc_qdrant = tmp_path / "configured-doc-qdrant"
    code_falkor = tmp_path / "configured-code.rdb"
    doc_falkor = tmp_path / "configured-doc.rdb"
    config_path = tmp_path / ".cortext-harness" / "config" / "team.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "active": True,
                "code": {
                    "env": {
                        "CORTEX_DATA_HOME": str(data_home),
                        "CORTEX_STORAGE_INSTANCE": "team-a",
                        "QDRANT_CODE_PATH": str(code_qdrant),
                        "FALKORDB_CODE_PATH": str(code_falkor),
                    }
                },
                "doc": {
                    "env": {
                        "CORTEX_DATA_HOME": str(data_home),
                        "CORTEX_STORAGE_INSTANCE": "team-a",
                        "QDRANT_DOC_PATH": str(doc_qdrant),
                        "FALKORDB_DOC_PATH": str(doc_falkor),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    resolved = _load_lifecycle_module()._resolved_storage(tmp_path)

    assert resolved.data_root == data_home
    assert resolved.instance_id == "team-a"
    assert resolved.qdrant_code_path == code_qdrant
    assert resolved.qdrant_doc_path == doc_qdrant
    assert resolved.falkordb_code_path == code_falkor
    assert resolved.falkordb_doc_path == doc_falkor


def test_storage_layout_reports_manifest_and_leases(tmp_path: Path) -> None:
    data_home = tmp_path / "account-data"
    assert _run_lifecycle("storage-init", data_home=data_home).returncode == 0
    result = _run_lifecycle("storage-layout", data_home=data_home)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "v1"
    assert payload["instance_id"] == "test"
    assert payload["manifest"]["instance_id"] == "test"
    assert set(payload["qdrant"]) == {"code", "doc"}


def test_storage_migration_is_dry_run_by_default(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    (legacy / "local_qdrant_db" / "code").mkdir(parents=True)
    (legacy / "local_qdrant_db" / "code" / "payload.bin").write_bytes(b"legacy")
    data_home = tmp_path / "account-data"
    result = _run_lifecycle(
        "storage-migrate-layout",
        "--legacy-root",
        str(legacy),
        data_home=data_home,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mode: dry-run" in result.stdout
    assert "would-copy" in result.stdout
    assert not (data_home / "v1" / "instances" / "test" / "qdrant" / "code").exists()


def test_storage_backup_copies_and_verifies_owner_data(tmp_path: Path) -> None:
    data_home = tmp_path / "account-data"
    assert _run_lifecycle("storage-init", data_home=data_home).returncode == 0
    instance = data_home / "v1" / "instances" / "test"
    (instance / "qdrant" / "code" / "payload.bin").write_bytes(b"vector")
    (instance / "falkordb" / "code" / "data.rdb").write_bytes(b"graph")
    result = _run_lifecycle("storage-backup", "--owner", "code", data_home=data_home)
    assert result.returncode == 0, result.stdout + result.stderr
    manifests = list((instance / "backups").glob("*/manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["owner_id"] == "code"
    assert {item["backend"] for item in manifest["items"]} == {"qdrant", "falkordb"}


def test_infra_up_is_a_no_docker_deprecation_alias(tmp_path: Path) -> None:
    """``infra-up`` must NOT touch Docker; it warns and calls storage-init."""
    result = _run_lifecycle("infra-up", cwd=tmp_path, data_home=tmp_path / "account-data")
    assert result.returncode == 0
    assert "deprecated" in result.stdout.lower()
    assert "storage-init" in result.stdout
    assert "manifest" in result.stdout


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

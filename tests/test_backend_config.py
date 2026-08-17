"""Tests for backend mode config schema (Phase 01).

Covers :func:`cortex_harness.storage.config.validate_backend_config`
(``storage_backend`` enum + remote section validation) and the
``ProjectTargets`` plumbing that carries those values through to callers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_harness.storage import (
    BackendMode,
    InvalidStorageIdentityError,
    RemoteStorageConfig,
    resolve_storage,
)
from cortex_harness.storage.config import validate_backend_config
from tools.common.project_registry import resolve_project_targets


# ---------------------------------------------------------------------------
# validate_backend_config
# ---------------------------------------------------------------------------


def test_local_default() -> None:
    mode, remote = validate_backend_config("local", None)
    assert mode is BackendMode.LOCAL
    assert remote is None


def test_explicit_local_with_remote_section_ignored() -> None:
    """A ``remote`` block on a local project is silently ignored."""
    mode, remote = validate_backend_config("local", {"qdrant_url": "http://localhost:6333"})
    assert mode is BackendMode.LOCAL
    assert remote is None


def test_remote_valid_qdrant_only() -> None:
    remote_dict = {"qdrant_url": "http://qdrant.internal:6333"}
    mode, remote = validate_backend_config("remote", remote_dict)
    assert mode is BackendMode.REMOTE
    assert remote.qdrant_url == "http://qdrant.internal:6333"
    assert remote.qdrant_api_key is None
    assert remote.falkordb_uri is None


def test_remote_valid_falkordb_only() -> None:
    remote_dict = {"falkordb_uri": "redis://falkordb.internal:6379"}
    mode, remote = validate_backend_config("remote", remote_dict)
    assert mode is BackendMode.REMOTE
    assert remote.falkordb_uri == "redis://falkordb.internal:6379"
    assert remote.qdrant_url is None


def test_remote_full_config() -> None:
    remote_dict = {
        "qdrant_url": "http://qdrant:6333",
        "qdrant_api_key": "secret-key",
        "falkordb_uri": "redis://falkordb:6379",
        "falkordb_password": "secret-pass",
        "falkordb_ssl": True,
    }
    mode, remote = validate_backend_config("remote", remote_dict)
    assert mode is BackendMode.REMOTE
    assert remote.qdrant_api_key == "secret-key"
    assert remote.falkordb_password == "secret-pass"
    assert remote.falkordb_ssl is True


def test_remote_missing_section() -> None:
    with pytest.raises(ValueError) as exc:
        validate_backend_config("remote", None)
    assert "remote" in str(exc.value)


def test_remote_missing_urls() -> None:
    with pytest.raises(ValueError) as exc:
        validate_backend_config("remote", {})
    assert "qdrant_url" in str(exc.value) or "falkordb_uri" in str(exc.value)


def test_remote_empty_section() -> None:
    with pytest.raises(ValueError) as exc:
        validate_backend_config("remote", {"qdrant_url": "", "falkordb_uri": None})
    assert "qdrant_url" in str(exc.value) or "falkordb_uri" in str(exc.value)


def test_unknown_backend_raises_invalid() -> None:
    with pytest.raises(InvalidStorageIdentityError) as exc:
        validate_backend_config("cloud", None)
    assert "cloud" in str(exc.value)


def test_remote_credential_redaction() -> None:
    """__repr__ must mask API keys and passwords."""
    config = RemoteStorageConfig(
        qdrant_url="http://qdrant.internal:6333",
        qdrant_api_key="super-secret",
        falkordb_uri="redis://falkordb.internal:6379",
        falkordb_password="ultra-secret",
        falkordb_ssl=True,
    )
    rendered = repr(config)
    assert "super-secret" not in rendered
    assert "ultra-secret" not in rendered
    assert "***" in rendered
    # URLs (not secrets) should still be visible for diagnostics.
    assert "http://qdrant.internal:6333" in rendered
    assert "redis://falkordb.internal:6379" in rendered


# ---------------------------------------------------------------------------
# resolve_storage — backend_mode plumbed through ResolvedStorage
# ---------------------------------------------------------------------------


def test_resolve_storage_local_default(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    assert resolved.backend_mode is BackendMode.LOCAL
    assert resolved.remote is None


def test_resolve_storage_remote_section(tmp_path: Path) -> None:
    cfg = {
        "storage_backend": "remote",
        "remote": {
            "qdrant_url": "http://qdrant:6333",
            "falkordb_uri": "redis://falkordb:6379",
        },
    }
    resolved = resolve_storage(tmp_path, config=cfg)
    assert resolved.backend_mode is BackendMode.REMOTE
    assert resolved.remote is not None
    assert resolved.remote.qdrant_url == "http://qdrant:6333"
    assert resolved.remote.falkordb_uri == "redis://falkordb:6379"


def test_resolve_storage_invalid_backend(tmp_path: Path) -> None:
    cfg = {"storage_backend": "cloud", "remote": {"qdrant_url": "http://q"}}
    with pytest.raises(InvalidStorageIdentityError):
        resolve_storage(tmp_path, config=cfg)


# ---------------------------------------------------------------------------
# ProjectRegistry — storage_backend + remote_config flow through to targets
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, project_id: str, body: dict) -> Path:
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "remote_proj.json").write_text(json.dumps(body), encoding="utf-8")
    return config_dir


def test_project_registry_local_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_config(
        tmp_path,
        "no_backend_proj",
        {
            "project": {"code": "no_backend_proj", "name": "No Backend"},
            "code": {"env": {"GRAPH_PROVIDER": "falkordb"}},
        },
    )
    targets = resolve_project_targets(
        "no_backend_proj", config_dir=tmp_path / ".cortext-harness" / "config"
    )
    assert targets.storage_backend == "local"
    assert targets.remote_config is None


def test_project_registry_remote(tmp_path: Path) -> None:
    remote_section = {
        "qdrant_url": "http://qdrant:6333",
        "falkordb_uri": "redis://falkordb:6379",
    }
    _write_config(
        tmp_path,
        "remote_proj",
        {
            "project": {"code": "remote_proj", "name": "Remote"},
            "storage_backend": "remote",
            "remote": remote_section,
        },
    )
    targets = resolve_project_targets(
        "remote_proj", config_dir=tmp_path / ".cortext-harness" / "config"
    )
    assert targets.storage_backend == "remote"
    assert targets.remote_config == remote_section


def test_project_registry_remote_qdrant_only(tmp_path: Path) -> None:
    """A project can be remote for only one component."""
    remote_section = {"qdrant_url": "http://qdrant:6333"}
    _write_config(
        tmp_path,
        "mixed_proj",
        {
            "project": {"code": "mixed_proj", "name": "Mixed"},
            "storage_backend": "remote",
            "remote": remote_section,
        },
    )
    targets = resolve_project_targets(
        "mixed_proj", config_dir=tmp_path / ".cortext-harness" / "config"
    )
    assert targets.storage_backend == "remote"
    assert targets.remote_config == remote_section
    assert targets.remote_config["qdrant_url"] == "http://qdrant:6333"

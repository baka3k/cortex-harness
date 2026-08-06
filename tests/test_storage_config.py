"""Tests for the local-storage configuration layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_harness.storage import (
    DEFAULT_FALKORDB_PATH,
    DEFAULT_QDRANT_PATH,
    StorageRole,
    resolve_storage,
)
from cortex_harness.storage.config import (
    LegacyRemoteConfigurationError,
    QdrantStorageRole,
    env_to_config,
    storage_overlay,
)


def test_defaults_are_project_root_relative(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_base == (tmp_path / DEFAULT_QDRANT_PATH).resolve()
    assert resolved.qdrant_code_path == (tmp_path / "local_qdrant_db" / "code").resolve()
    assert resolved.qdrant_doc_path == (tmp_path / "local_qdrant_db" / "doc").resolve()
    assert resolved.falkordb_path == (tmp_path / DEFAULT_FALKORDB_PATH).resolve()
    assert resolved.has_legacy_keys is False


def test_qdrant_role_subdirectories_are_distinct(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_code_path != resolved.qdrant_doc_path
    assert resolved.qdrant_code_path.name == "code"
    assert resolved.qdrant_doc_path.name == "doc"


def test_path_resolution_independent_of_cwd(tmp_path: Path, monkeypatch) -> None:
    """resolve_storage must anchor paths to project_root, never cwd."""
    monkeypatch.chdir(tmp_path.parent)
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_code_path.is_relative_to(tmp_path.resolve())
    assert resolved.falkordb_path.is_relative_to(tmp_path.resolve())


def test_config_overrides_wins_over_defaults(tmp_path: Path) -> None:
    cfg = {
        "QDRANT_PATH": "./custom_qdrant",
        "QDRANT_CODE_PATH": "./custom_qdrant/c",
        "FALKORDB_PATH": "./custom_falkordb/cortex.rdb",
    }
    resolved = resolve_storage(tmp_path, config=cfg)
    assert resolved.qdrant_base == (tmp_path / "custom_qdrant").resolve()
    assert resolved.qdrant_code_path == (tmp_path / "custom_qdrant" / "c").resolve()
    assert resolved.falkordb_path == (tmp_path / "custom_falkordb" / "cortex.rdb").resolve()


def test_absolute_paths_pass_through(tmp_path: Path) -> None:
    """Absolute config paths must be honored verbatim (still scoped to project for safety)."""
    absolute = tmp_path / "abs_qdrant"
    absolute.mkdir()
    cfg = {"QDRANT_CODE_PATH": str(absolute)}
    resolved = resolve_storage(tmp_path, config=cfg)
    assert resolved.qdrant_code_path == absolute.resolve()


def test_env_overrides_apply_when_no_cli_or_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QDRANT_CODE_PATH", str(tmp_path / "env_qdrant"))
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_code_path == (tmp_path / "env_qdrant").resolve()


def test_legacy_remote_keys_raise(tmp_path: Path) -> None:
    cfg = {
        "QDRANT_HOST": "localhost",
        "QDRANT_PORT": "6333",
        "FALKORDB_HOST": "localhost",
        "FALKORDB_PORT": "6379",
    }
    with pytest.raises(LegacyRemoteConfigurationError) as exc:
        resolve_storage(tmp_path, config=cfg)
    message = str(exc.value)
    assert "QDRANT_HOST" in message
    assert "FALKORDB_HOST" in message


def test_empty_legacy_values_are_ignored(tmp_path: Path) -> None:
    """Empty string legacy values must not trigger the migration error."""
    cfg = {"QDRANT_HOST": "", "FALKORDB_PORT": ""}
    resolved = resolve_storage(tmp_path, config=cfg)
    assert resolved.has_legacy_keys is False


def test_ensure_directories_creates_paths(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    resolved.ensure_directories()
    assert resolved.qdrant_code_path.is_dir()
    assert resolved.qdrant_doc_path.is_dir()
    assert resolved.falkordb_path.parent.is_dir()


def test_path_for_role_resolves(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    assert resolved.path_for_role(QdrantStorageRole.CODE) == resolved.qdrant_code_path
    assert resolved.path_for_role(QdrantStorageRole.DOCUMENT) == resolved.qdrant_doc_path


def test_storage_overlay_emits_role_paths(tmp_path: Path) -> None:
    resolved = resolve_storage(
        tmp_path,
        code_collection="myproj_code",
        doc_collection="myproj_doc",
        code_graph="myproj",
        doc_graph="myproj_doc",
    )
    overlay = storage_overlay(resolved)
    assert overlay["QDRANT_PATH"] == str(resolved.qdrant_base)
    assert overlay["QDRANT_CODE_PATH"] == str(resolved.qdrant_code_path)
    assert overlay["QDRANT_DOC_PATH"] == str(resolved.qdrant_doc_path)
    assert overlay["FALKORDB_PATH"] == str(resolved.falkordb_path)
    assert overlay["QDRANT_COLLECTION"] == "myproj_code"
    assert overlay["QDRANT_COLLECTION_DOC"] == "myproj_doc"
    assert overlay["FALKORDB_GRAPH"] == "myproj"
    assert overlay["DOC_FALKORDB_GRAPH"] == "myproj_doc"


def test_resolved_storage_is_immutable(tmp_path: Path) -> None:
    """ResolvedStorage must be frozen so downstream cannot mutate it."""
    resolved = resolve_storage(tmp_path)
    with pytest.raises(Exception):
        resolved.qdrant_code_path = tmp_path / "tampered"  # type: ignore[misc]


def test_env_to_config_round_trip() -> None:
    pairs = [("QDRANT_CODE_PATH", "./local_qdrant_db/code"), ("NEO4J_USER", "neo4j")]
    cfg = env_to_config(pairs)
    assert cfg == {
        "QDRANT_CODE_PATH": "./local_qdrant_db/code",
        "NEO4J_USER": "neo4j",
    }


def test_invocation_outside_project_root(tmp_path: Path, monkeypatch) -> None:
    """Calling resolve_storage from a sibling directory must still anchor to project_root."""
    sibling = tmp_path.parent / "sibling"
    sibling.mkdir()
    monkeypatch.chdir(sibling)
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_code_path.is_relative_to(tmp_path.resolve())


def test_default_roles_match_storage_role_enum(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    # The resolved subdirectories are short on-disk names ("code", "doc") that
    # correspond to the StorageRole enum values; the underlying logical role
    # is "code" / "document".
    assert resolved.qdrant_code_path.name == StorageRole.CODE.value
    assert resolved.qdrant_doc_path.name == "doc"  # short subdir name on disk


def test_legacy_remote_only_triggers_for_known_keys(tmp_path: Path) -> None:
    cfg = {"UNRELATED_KEY": "value", "FALKORDB_GRAPH": "ok"}
    resolved = resolve_storage(tmp_path, config=cfg)
    assert resolved.has_legacy_keys is False
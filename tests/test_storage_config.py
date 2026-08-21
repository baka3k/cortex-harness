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


def test_defaults_are_centralized_under_account_home(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "account"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    resolved = resolve_storage(tmp_path)
    instance = fake_home / ".cortext-harness" / "v1" / "instances" / "default"
    assert resolved.data_root == fake_home / ".cortext-harness"
    assert resolved.qdrant_base == instance / "qdrant"
    assert resolved.qdrant_code_path == instance / "qdrant" / "code"
    assert resolved.qdrant_doc_path == instance / "qdrant" / "doc"
    assert resolved.falkordb_code_path == instance / "falkordb" / "code" / "data.rdb"
    assert resolved.falkordb_doc_path == instance / "falkordb" / "doc" / "data.rdb"
    assert resolved.has_legacy_keys is False


def test_qdrant_role_subdirectories_are_distinct(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_code_path != resolved.qdrant_doc_path
    assert resolved.qdrant_code_path.name == "code"
    assert resolved.qdrant_doc_path.name == "doc"


def test_path_resolution_independent_of_project_and_cwd(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.chdir(tmp_path.parent)
    first = resolve_storage(tmp_path / "project-a")
    second = resolve_storage(tmp_path / "unrelated" / "project-b")
    assert first.qdrant_code_path == second.qdrant_code_path
    assert first.falkordb_path == second.falkordb_path
    assert not first.qdrant_code_path.is_relative_to(first.project_root)


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
    """Default database paths must stay centralized when invoked elsewhere."""
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    sibling = tmp_path.parent / "sibling"
    sibling.mkdir()
    monkeypatch.chdir(sibling)
    resolved = resolve_storage(tmp_path)
    assert resolved.qdrant_code_path.is_relative_to(fake_home / ".cortext-harness")


def test_default_roles_match_storage_role_enum(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path)
    # The resolved subdirectories are short on-disk names ("code", "doc") that
    # correspond to the StorageRole enum values; the underlying logical role
    # is "code" / "document".
    assert resolved.qdrant_code_path.name == StorageRole.CODE.value
    assert resolved.qdrant_doc_path.name == StorageRole.DOCUMENT.value


def test_instance_and_owner_isolation(tmp_path: Path) -> None:
    a = resolve_storage(tmp_path, data_home=tmp_path / "data", instance_id="alpha")
    b = resolve_storage(tmp_path, data_home=tmp_path / "data", instance_id="beta")
    assert a.qdrant_code_path != b.qdrant_code_path
    assert a.falkordb_code_path != b.falkordb_code_path
    assert a.qdrant_code_path != a.qdrant_doc_path
    assert a.falkordb_code_path != a.falkordb_doc_path


def test_data_home_override_is_not_project_relative_when_absolute(tmp_path: Path) -> None:
    data_home = tmp_path.parent / "central-data"
    resolved = resolve_storage(tmp_path, data_home=data_home)
    assert resolved.data_root == data_home.resolve()


def test_relative_data_home_anchors_under_account_home_not_project_root(
    tmp_path: Path, monkeypatch
) -> None:
    """Bare-name ``data_home`` must live under ~/.cortext-harness, never under project_root.

    Regression guard for the silent project-root anchor introduced by commit
    2704da8 (2026-08-06). Before the fix, a relative value such as
    ``data_home="sampledb"`` was joined onto the source repository's project
    root, trapping data inside scanned repositories and breaking the
    "centralized per-account data home" contract documented at the top of
    ``cortex_harness/storage/config.py``. Operators running ``dev sync code``
    against multiple projects must be able to find every instance under
    ``~/.cortext-harness/v1/instances/<id>/...`` regardless of where the
    source tree lives.
    """
    fake_home = tmp_path / "account"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    project = tmp_path / "sources" / "sampledb"

    resolved = resolve_storage(project, data_home="")

    # Lives under the per-account data home, not the source tree.
    assert resolved.data_root == (fake_home / ".cortext-harness" / "sampledb").resolve()
    assert not resolved.data_root.is_relative_to(project.resolve())
    # FalkorDB + Qdrant both follow the relocated data_root.
    assert resolved.data_root.is_relative_to(fake_home / ".cortext-harness")
    assert resolved.falkordb_code_path.is_relative_to(resolved.data_root)
    assert resolved.qdrant_code_path.is_relative_to(resolved.data_root)
    assert resolved.path_provenance == "explicit-relative-anchored-to-home"


def test_project_local_config_discovery_does_not_change_default_data_root(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "account"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    project = tmp_path / "source"
    (project / ".cortext-harness" / "config").mkdir(parents=True)
    resolved = resolve_storage(project)
    assert resolved.data_root == fake_home / ".cortext-harness"


def test_ten_unrelated_projects_share_owner_paths_and_keep_logical_targets(tmp_path: Path) -> None:
    data_home = tmp_path / "central"
    resolved = []
    for index in range(10):
        project_id = f"project-{index:02d}"
        project_root = tmp_path / f"volume-{index}" / "src" / project_id
        item = resolve_storage(
            project_root,
            data_home=data_home,
            code_graph=project_id,
            doc_graph=f"{project_id}_doc",
            code_collection=project_id,
            doc_collection=f"{project_id}_doc",
        )
        resolved.append(item)
    assert len({item.qdrant_code_path for item in resolved}) == 1
    assert len({item.falkordb_doc_path for item in resolved}) == 1
    assert len({item.code_graph for item in resolved}) == 10
    assert all(not item.qdrant_code_path.is_relative_to(item.project_root) for item in resolved)


def test_legacy_remote_only_triggers_for_known_keys(tmp_path: Path) -> None:
    cfg = {"UNRELATED_KEY": "value", "FALKORDB_GRAPH": "ok"}
    resolved = resolve_storage(tmp_path, config=cfg)
    assert resolved.has_legacy_keys is False

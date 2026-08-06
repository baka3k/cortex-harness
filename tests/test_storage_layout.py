from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_harness.storage import migration as storage_migration
from cortex_harness.storage import (
    StorageLease,
    StorageLeaseConflictError,
    ensure_layout,
    migrate_legacy_layout,
    resolve_storage,
)


def test_manifest_is_idempotent_and_contains_stable_identity(tmp_path: Path) -> None:
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "data", instance_id="team-a")
    first = ensure_layout(resolved)
    second = ensure_layout(resolved)
    assert first == second
    assert first["schema_version"] == "v1"
    assert first["instance_id"] == "team-a"
    assert set(first["owners"]) == {"code", "doc"}
    assert "pid" not in json.dumps(first)


def test_manifest_rejects_owner_identity_drift_without_creating_new_owner_paths(
    tmp_path: Path,
) -> None:
    data_home = tmp_path / "data"
    original = resolve_storage(tmp_path, data_home=data_home, instance_id="team-a")
    ensure_layout(original)
    drifted = resolve_storage(
        tmp_path,
        data_home=data_home,
        instance_id="team-a",
        code_owner_id="worker",
    )

    with pytest.raises(ValueError, match="owner.*drift|owner.*mismatch") as exc:
        ensure_layout(drifted)

    assert "storage-migrate-layout" in str(exc.value)
    assert not drifted.qdrant_code_path.exists()
    assert not Path(drifted.falkordb_code_path).parent.exists()


@pytest.mark.parametrize("backend", ["qdrant", "falkordb"])
def test_manifest_rejects_canonical_backend_path_drift(
    tmp_path: Path,
    backend: str,
) -> None:
    data_home = tmp_path / "data"
    original = resolve_storage(tmp_path, data_home=data_home, instance_id="team-a")
    ensure_layout(original)
    override = tmp_path / "alternate" / ("qdrant-code" if backend == "qdrant" else "code.rdb")
    kwargs = (
        {"qdrant_code_path": override}
        if backend == "qdrant"
        else {"falkordb_code_path": override}
    )
    drifted = resolve_storage(
        tmp_path,
        data_home=data_home,
        instance_id="team-a",
        **kwargs,
    )

    with pytest.raises(ValueError, match="path.*drift|path.*mismatch") as exc:
        ensure_layout(drifted)

    assert "storage-migrate-layout" in str(exc.value)
    assert not override.exists()


def test_duplicate_owner_lease_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "qdrant" / "code"
    first = StorageLease(target, instance_id="default", owner_id="code", backend="qdrant").acquire()
    try:
        with pytest.raises(StorageLeaseConflictError):
            StorageLease(target, instance_id="default", owner_id="code", backend="qdrant").acquire()
    finally:
        first.release()


def test_legacy_migration_dry_run_copy_and_verified_noop(tmp_path: Path) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
    from redislite.falkordb_client import FalkorDB

    legacy = tmp_path / "legacy"
    for role in ("code", "doc"):
        client = QdrantClient(path=str(legacy / "local_qdrant_db" / role))
        client.create_collection(
            f"{role}_collection",
            vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE),
        )
        client.close()
    (legacy / "local_falkordb_db").mkdir(parents=True)
    graph = FalkorDB(str(legacy / "local_falkordb_db" / "cortex.rdb"))
    graph.select_graph("legacy").query("CREATE (:Probe {id: 1})")
    graph.close()
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "central")

    dry = migrate_legacy_layout(resolved, legacy, dry_run=True)
    assert {item.action for item in dry} == {"would-copy"}
    assert not resolved.qdrant_code_path.exists()

    copied = migrate_legacy_layout(resolved, legacy, dry_run=False)
    assert {item.action for item in copied} == {"copied"}
    assert any(item.inventory == ("code_collection",) for item in copied)
    assert any(item.inventory == ("legacy",) for item in copied)
    assert (legacy / "local_falkordb_db" / "cortex.rdb").exists()

    noop = migrate_legacy_layout(resolved, legacy, dry_run=False)
    assert {item.action for item in noop} == {"verified-noop"}


def test_legacy_migration_rejects_an_active_target_owner(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    source = legacy / "local_qdrant_db" / "code"
    source.mkdir(parents=True)
    (source / "payload.bin").write_bytes(b"legacy")
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "central")
    active = StorageLease(
        resolved.qdrant_code_path,
        instance_id=resolved.instance_id,
        owner_id=resolved.code_owner_id,
        backend="qdrant",
    ).acquire()
    try:
        with pytest.raises(StorageLeaseConflictError):
            migrate_legacy_layout(resolved, legacy, dry_run=False)
    finally:
        active.release()

    assert not resolved.qdrant_code_path.exists()
    assert (source / "payload.bin").read_bytes() == b"legacy"


def test_legacy_migration_rejects_an_active_source_owner(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    source = legacy / "local_qdrant_db" / "code"
    source.mkdir(parents=True)
    (source / "payload.bin").write_bytes(b"legacy")
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "central")
    active = StorageLease(
        source,
        instance_id=resolved.instance_id,
        owner_id=f"legacy-{resolved.code_owner_id}",
        backend="legacy-qdrant",
    ).acquire()
    try:
        with pytest.raises(StorageLeaseConflictError):
            migrate_legacy_layout(resolved, legacy, dry_run=False)
    finally:
        active.release()

    assert not resolved.qdrant_code_path.exists()
    assert (source / "payload.bin").read_bytes() == b"legacy"


def test_legacy_migration_holds_owner_lease_through_reopen_and_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = tmp_path / "legacy"
    source = legacy / "local_qdrant_db" / "code"
    source.mkdir(parents=True)
    (source / "payload.bin").write_bytes(b"legacy")
    resolved = resolve_storage(tmp_path, data_home=tmp_path / "central")
    observed: list[str] = []
    original_copytree = storage_migration.shutil.copytree
    original_tree_digest = storage_migration._tree_digest
    original_write_marker = storage_migration._write_marker

    def assert_target_is_leased(phase: str) -> None:
        contender = StorageLease(
            resolved.qdrant_code_path,
            instance_id=resolved.instance_id,
            owner_id=resolved.code_owner_id,
            backend="qdrant",
        )
        with pytest.raises(StorageLeaseConflictError):
            contender.acquire()
        observed.append(phase)

    def assert_source_is_leased() -> None:
        contender = StorageLease(
            source,
            instance_id=resolved.instance_id,
            owner_id=f"legacy-{resolved.code_owner_id}",
            backend="legacy-qdrant",
        )
        with pytest.raises(StorageLeaseConflictError):
            contender.acquire()

    def copytree(source: Path, target: Path, *, dirs_exist_ok: bool) -> None:
        assert target == resolved.qdrant_code_path
        assert_source_is_leased()
        assert_target_is_leased("copy")
        original_copytree(source, target, dirs_exist_ok=dirs_exist_ok)

    def tree_digest(path: Path) -> str:
        if path == resolved.qdrant_code_path and path.exists():
            assert_target_is_leased("digest")
        return original_tree_digest(path)

    def reopen(path: Path, backend: str) -> tuple[str, ...]:
        assert path == resolved.qdrant_code_path
        assert backend == "qdrant"
        assert_source_is_leased()
        assert_target_is_leased("reopen")
        return ("probe",)

    def write_marker(
        target: Path,
        *,
        source: Path,
        digest: str,
        inventory: tuple[str, ...],
    ) -> None:
        assert_target_is_leased("marker")
        original_write_marker(
            target,
            source=source,
            digest=digest,
            inventory=inventory,
        )

    monkeypatch.setattr(storage_migration.shutil, "copytree", copytree)
    monkeypatch.setattr(storage_migration, "_tree_digest", tree_digest)
    monkeypatch.setattr(storage_migration, "_reopen_inventory", reopen)
    monkeypatch.setattr(storage_migration, "_write_marker", write_marker)

    report = migrate_legacy_layout(resolved, legacy, dry_run=False)

    assert observed == ["copy", "digest", "reopen", "marker"]
    assert report[0].inventory == ("probe",)

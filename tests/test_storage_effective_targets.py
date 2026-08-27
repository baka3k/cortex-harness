"""Compatibility contracts for canonical effective storage topology."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_harness.storage import (
    BackendMode,
    ENV_EFFECTIVE_GRAPH_FINGERPRINT,
    ENV_EFFECTIVE_GRAPH_TARGET,
    ENV_EFFECTIVE_TOPOLOGY,
    ENV_EFFECTIVE_VECTOR_FINGERPRINT,
    GenerationManager,
    RemoteStorageConfig,
    ResolvedStorage,
    StoreGateway,
    StorageFactory,
    effective_graph_target_from_env,
    resolve_storage,
    storage_overlay,
)
from tools.graph.journal import RunStatus, SQLiteJournal
from tools.graph.journal.config import (
    JournalError,
    configure_journal_env,
    legacy_physical_target_from_env,
    physical_target_from_env,
)


def _resolved(tmp_path: Path) -> ResolvedStorage:
    return ResolvedStorage(
        project_root=tmp_path,
        qdrant_base=tmp_path / "qdrant",
        qdrant_code_path=tmp_path / "qdrant" / "code",
        qdrant_doc_path=tmp_path / "qdrant" / "doc",
        falkordb_path=tmp_path / "falkordb" / "code" / "data.rdb",
        falkordb_code_path=tmp_path / "falkordb" / "code" / "data.rdb",
        falkordb_doc_path=tmp_path / "falkordb" / "doc" / "data.rdb",
        code_graph="demo",
        doc_graph="demo_doc",
        code_collection="demo",
        doc_collection="demo_doc",
    )


def test_remote_falkordb_target_uses_normalized_credential_free_uri(tmp_path: Path) -> None:
    remote = RemoteStorageConfig(
        falkordb_uri="redis://tenant:uri-secret@DB.EXAMPLE./",
        falkordb_password="config-secret",
        falkordb_ssl=True,
    )
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved(tmp_path),
        remote=remote,
    )

    target = factory.effective_graph_target("demo")

    assert target.mode == "remote"
    assert target.location == "redis://db.example:6379"
    assert target.namespace == "demo"
    assert target.tls is True
    assert target.principal_fingerprint
    serialized = target.canonical_json
    assert "tenant" not in serialized
    assert "uri-secret" not in serialized
    assert "config-secret" not in serialized


def test_mixed_topology_is_explicit_and_fingerprinted(tmp_path: Path) -> None:
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved(tmp_path),
        remote=RemoteStorageConfig(qdrant_url="HTTP://QDRANT.EXAMPLE:6333/"),
        project_scope="demo",
        code_graph="demo",
        code_collection="demo",
    )

    topology = factory.effective_topology(generation_id="generation-1")

    assert topology.graph.mode == "file"
    assert topology.vector.mode == "remote"
    assert topology.vector.location == "http://qdrant.example:6333"
    assert topology.graph_fingerprint != topology.vector_fingerprint
    assert topology.fingerprint.startswith("storage-topology:v1:")
    assert json.loads(topology.canonical_json)["generation_id"] == "generation-1"


def test_force_local_creates_a_distinct_effective_topology(tmp_path: Path, monkeypatch) -> None:
    kwargs = {
        "backend_mode": BackendMode.REMOTE,
        "resolved": _resolved(tmp_path),
        "remote": RemoteStorageConfig(
            qdrant_url="http://qdrant.example:6333",
            falkordb_uri="redis://falkordb.example:6379",
        ),
        "project_scope": "demo",
        "code_graph": "demo",
        "code_collection": "demo",
    }
    remote = StorageFactory(**kwargs).effective_topology(generation_id="generation-1")

    monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", "1")
    forced = StorageFactory(**kwargs).effective_topology(generation_id="generation-1")

    assert forced.forced_local is True
    assert forced.graph.mode == "file"
    assert forced.vector.mode == "file"
    assert forced.fingerprint != remote.fingerprint
    assert forced.graph_fingerprint != remote.graph_fingerprint
    assert forced.vector_fingerprint != remote.vector_fingerprint


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_false_looking_force_local_values_do_not_override_remote(
    tmp_path: Path, monkeypatch, value: str
) -> None:
    monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", value)
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved(tmp_path),
        remote=RemoteStorageConfig(qdrant_url="http://qdrant.example:6333"),
    )

    assert factory.forced_local is False
    assert factory.backend_mode is BackendMode.REMOTE


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            {"FALKORDB_URI": "redis://graph-a:6379", "FALKORDB_GRAPH": "demo"},
            {"FALKORDB_URI": "redis://graph-b:6379", "FALKORDB_GRAPH": "demo"},
        ),
        (
            {"FALKORDB_URI": "redis://graph-a:6379", "FALKORDB_GRAPH": "demo"},
            {"FALKORDB_URI": "redis://graph-a:6379", "FALKORDB_GRAPH": "other"},
        ),
        (
            {"FALKORDB_URI": "redis://graph-a:6379", "FALKORDB_GRAPH": "demo"},
            {"FALKORDB_PATH": "/tmp/demo.rdb", "FALKORDB_GRAPH": "demo"},
        ),
    ],
)
def test_journal_targets_are_isolated(left: dict[str, str], right: dict[str, str]) -> None:
    assert physical_target_from_env(left) != physical_target_from_env(right)


def test_journal_target_prefers_factory_descriptor_and_checks_fingerprint(tmp_path: Path) -> None:
    resolved = resolve_storage(
        tmp_path,
        config={
            "storage_backend": "remote",
            "remote": {
                "qdrant_url": "https://qdrant.example:6333",
                "falkordb_uri": "rediss://falkordb.example:6379",
                "falkordb_password": "never-persist-me",
            },
        },
        code_graph="demo",
        doc_graph="demo_doc",
        code_collection="demo",
        doc_collection="demo_doc",
    )
    overlay = storage_overlay(resolved, owner="code")

    target = effective_graph_target_from_env(overlay)
    assert overlay[ENV_EFFECTIVE_GRAPH_FINGERPRINT] == target.fingerprint
    assert physical_target_from_env(overlay) == target.fingerprint
    assert "never-persist-me" not in overlay[ENV_EFFECTIVE_GRAPH_TARGET]
    assert "never-persist-me" not in overlay[ENV_EFFECTIVE_TOPOLOGY]
    assert overlay[ENV_EFFECTIVE_VECTOR_FINGERPRINT]

    overlay[ENV_EFFECTIVE_GRAPH_FINGERPRINT] = "tampered"
    with pytest.raises(JournalError, match="does not match"):
        physical_target_from_env(overlay)


def test_stale_descriptor_cannot_hide_a_later_runtime_target_override(tmp_path: Path) -> None:
    resolved = resolve_storage(
        tmp_path,
        config={
            "storage_backend": "remote",
            "remote": {
                "qdrant_url": "http://qdrant.example:6333",
                "falkordb_uri": "redis://graph-a.example:6379",
            },
        },
        code_graph="demo",
        doc_graph="demo_doc",
        code_collection="demo",
        doc_collection="demo_doc",
    )
    overlay = storage_overlay(resolved, owner="code")
    overlay["FALKORDB_URI"] = "redis://graph-b.example:6379"

    with pytest.raises(JournalError, match="does not match runtime"):
        physical_target_from_env(overlay)


def test_explicit_zero_port_is_not_rewritten_to_default() -> None:
    target = effective_graph_target_from_env(
        {"FALKORDB_URI": "redis://graph.example:0", "FALKORDB_GRAPH": "demo"}
    )

    assert target.location == "redis://graph.example:0"


def test_required_journal_quarantines_pre_v1_target_identity(tmp_path: Path) -> None:
    env = {
        "FALKORDB_PATH": str(tmp_path / "graph.rdb"),
        "FALKORDB_GRAPH": "demo",
    }
    legacy = configure_journal_env(
        env,
        root=tmp_path,
        project_id="demo",
        parser="python",
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=legacy_physical_target_from_env(env),
        mode="required",
        generation="generation-1",
    )
    with SQLiteJournal(legacy.path) as journal:
        old_run = journal.open_run(legacy.metadata)

    current = configure_journal_env(
        env,
        root=tmp_path,
        project_id="demo",
        parser="python",
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=physical_target_from_env(env),
        mode="required",
        generation="generation-1",
    )

    with SQLiteJournal(current.path) as journal:
        assert journal.get_run(old_run.run_id).status is RunStatus.QUARANTINED


def test_password_and_api_key_values_never_enter_target_fingerprints(tmp_path: Path) -> None:
    def topology(api_key: str, password: str):
        return StorageFactory(
            backend_mode=BackendMode.REMOTE,
            resolved=_resolved(tmp_path),
            remote=RemoteStorageConfig(
                qdrant_url="https://qdrant.example:6333",
                qdrant_api_key=api_key,
                falkordb_uri="rediss://tenant@graph.example:6379",
                falkordb_password=password,
            ),
            project_scope="demo",
            code_graph="demo",
            code_collection="demo",
        ).effective_topology(generation_id="generation-1")

    first = topology("weak-api-key", "weak-password")
    second = topology("other-api-key", "other-password")

    assert first.fingerprint == second.fingerprint
    assert "weak-api-key" not in first.canonical_json
    assert "weak-password" not in first.canonical_json


def test_generation_manager_rejects_a_different_vector_topology(tmp_path: Path) -> None:
    def factory(qdrant_url: str) -> StorageFactory:
        return StorageFactory(
            backend_mode=BackendMode.REMOTE,
            resolved=_resolved(tmp_path),
            remote=RemoteStorageConfig(
                qdrant_url=qdrant_url,
                falkordb_uri="redis://graph.example:6379",
            ),
            project_scope="demo",
            code_graph="demo",
            code_collection="demo",
        )

    first = GenerationManager.from_storage_factory(
        tmp_path / "first",
        factory("https://qdrant-a.example:6333"),
        graph_name="demo",
        collection_name="demo",
        project_scope="demo",
    )
    second = GenerationManager.from_storage_factory(
        tmp_path / "second",
        factory("https://qdrant-b.example:6333"),
        graph_name="demo",
        collection_name="demo",
        project_scope="demo",
    )
    manifest = first.allocate("revision-1", generation_id="generation-1")

    assert first.target == second.target
    first_compatibility = manifest.validation["storage_compatibility"]
    assert first_compatibility["graph_target_fingerprint"]
    assert first_compatibility["vector_target_fingerprint"]
    assert first_compatibility["topology_fingerprint"]
    with pytest.raises(ValueError, match="effective storage topology"):
        second.publish(manifest, lambda _: None)


def test_generation_validator_cannot_retarget_reserved_compatibility(
    tmp_path: Path,
) -> None:
    def factory(qdrant_url: str) -> StorageFactory:
        return StorageFactory(
            backend_mode=BackendMode.REMOTE,
            resolved=_resolved(tmp_path),
            remote=RemoteStorageConfig(qdrant_url=qdrant_url),
            project_scope="demo",
            code_graph="demo",
            code_collection="demo",
        )

    first = GenerationManager.from_storage_factory(
        tmp_path / "generations",
        factory("https://qdrant-a.example:6333"),
        graph_name="demo",
        collection_name="demo",
        project_scope="demo",
    )
    other = GenerationManager.from_storage_factory(
        tmp_path / "other",
        factory("https://qdrant-b.example:6333"),
        graph_name="demo",
        collection_name="demo",
        project_scope="demo",
    )
    manifest = first.allocate("revision-1", generation_id="generation-1")
    other_compatibility = other.allocate(
        "revision-1", generation_id="generation-1"
    ).validation["storage_compatibility"]

    def retarget(candidate) -> None:
        candidate.validation["storage_compatibility"] = dict(other_compatibility)

    with pytest.raises(ValueError, match="effective storage topology"):
        first.publish(manifest, retarget)
    assert first.load_active() is None


def test_local_factory_migrates_an_unfenced_legacy_active_manifest(
    tmp_path: Path,
) -> None:
    resolved = _resolved(tmp_path)
    factory = StorageFactory(backend_mode=BackendMode.LOCAL, resolved=resolved)
    root = tmp_path / "generations"
    legacy = GenerationManager.from_storage_factory(
        root,
        factory,
        graph_name="demo",
        collection_name="demo",
        project_scope="demo",
    )
    unfenced = GenerationManager(root, legacy.target)
    published = unfenced.publish(
        unfenced.allocate("revision-1", generation_id="generation-1"),
        lambda _: None,
    )
    assert "storage_compatibility" not in published.validation

    migrated = legacy.load_active()

    assert migrated is not None
    compatibility = migrated.validation["storage_compatibility"]
    assert compatibility["graph_mode"] == "file"
    assert compatibility["vector_mode"] == "file"
    persisted = json.loads(legacy.manifest_path.read_text(encoding="utf-8"))
    assert persisted["validation"]["storage_compatibility"] == compatibility


def test_gateway_factory_keeps_leases_local_and_fences_remote_topology(
    tmp_path: Path,
) -> None:
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved(tmp_path),
        remote=RemoteStorageConfig(
            qdrant_url="https://qdrant.example:6333",
            falkordb_uri="redis://graph.example:6379",
        ),
        project_scope="demo",
        code_graph="demo",
        code_collection="demo",
    )

    gateway = StoreGateway.from_storage_factory(
        factory,
        str(tmp_path / "generations"),
        graph_name="demo",
        collection_name="demo",
        project_scope="demo",
    )
    manifest = gateway.generations.allocate(
        "revision-1", generation_id="generation-1"
    )

    assert gateway.target.canonical_paths == (
        resolved_path := _resolved(tmp_path).falkordb_code_path.resolve(),
        _resolved(tmp_path).qdrant_code_path.resolve(),
    )
    assert all(path.is_absolute() for path in gateway.target.canonical_paths)
    assert resolved_path.as_posix().startswith(tmp_path.as_posix())
    compatibility = manifest.validation["storage_compatibility"]
    assert compatibility["graph_mode"] == "remote"
    assert compatibility["vector_mode"] == "remote"


def test_force_local_overlay_does_not_export_remote_runtime_targets(tmp_path: Path, monkeypatch) -> None:
    resolved = resolve_storage(
        tmp_path,
        config={
            "storage_backend": "remote",
            "remote": {
                "qdrant_url": "http://qdrant.example:6333",
                "falkordb_uri": "redis://falkordb.example:6379",
            },
        },
        code_graph="demo",
        doc_graph="demo_doc",
        code_collection="demo",
        doc_collection="demo_doc",
    )
    monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", "1")

    overlay = storage_overlay(resolved, owner="code")
    target = effective_graph_target_from_env(overlay)

    assert "FALKORDB_URI" not in overlay
    assert "QDRANT_URL" not in overlay
    assert target.mode == "file"
    assert json.loads(overlay[ENV_EFFECTIVE_TOPOLOGY])["forced_local"] is True


def test_falkordb_overlay_does_not_override_neo4j_journal_identity(tmp_path: Path) -> None:
    resolved = resolve_storage(
        tmp_path,
        code_graph="unused-falkor-name",
        doc_graph="unused-doc-name",
        code_collection="demo",
        doc_collection="demo_doc",
    )
    overlay = storage_overlay(resolved, owner="code", graph_provider="neo4j")
    overlay.update(
        {
            "CODE_GRAPH_PROVIDER": "neo4j",
            "NEO4J_URI": "neo4j+s://tenant:secret@GRAPH.EXAMPLE",
            "NEO4J_USER": "tenant",
            "NEO4J_PASSWORD": "other-secret",
            "NEO4J_DB": "demo",
        }
    )

    assert ENV_EFFECTIVE_GRAPH_TARGET not in overlay
    descriptor = effective_graph_target_from_env(overlay)
    assert descriptor.provider == "neo4j"
    assert descriptor.location == "neo4j+s://graph.example:7687"
    assert descriptor.namespace == "demo"
    assert descriptor.tls is True
    assert "tenant" not in descriptor.canonical_json
    assert "secret" not in descriptor.canonical_json

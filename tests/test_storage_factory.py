"""Tests for the storage factory (Phase 03).

Covers :class:`cortex_harness.storage.factory.StorageFactory`, the
``create_storage`` convenience function, and the fallback rules that mix
local + remote for the same project.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cortex_harness.storage import (
    BackendMode,
    QdrantStorageRole,
    RemoteStorageConfig,
    ResolvedStorage,
    StorageFactory,
    StorageRole,
    create_storage,
)


# ---------------------------------------------------------------------------
# Test fakes — keep the storage factory independent of qdrant_client /
# falkordb_driver imports during tests.
# ---------------------------------------------------------------------------


class _FakeFalkorDBDriver:
    """Stand-in for ``tools.graph.driver.falkordb_driver.FalkorDBDriver``."""

    instances: list[_FakeFalkorDBDriver] = []

    def __init__(
        self,
        *,
        path: str | None = None,
        uri: str | None = None,
        password: str | None = None,
        ssl: bool = False,
        graph: str | None = None,
        owner_id: str | None = None,
        instance_id: str | None = None,
        **_kwargs: Any,
    ) -> None:
        self.path = path
        self.uri = uri
        self.password = password
        self.ssl = ssl
        self.graph = graph
        self.owner_id = owner_id
        self.instance_id = instance_id
        _FakeFalkorDBDriver.instances.append(self)


def _install_falkordb_stub(monkeypatch) -> None:
    """Replace ``tools.graph.driver.falkordb_driver.FalkorDBDriver`` with a fake."""
    fake = types.ModuleType("tools.graph.driver.falkordb_driver")
    fake.FalkorDBDriver = _FakeFalkorDBDriver  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tools.graph.driver.falkordb_driver", fake)


@pytest.fixture(autouse=True)
def _fakes(monkeypatch) -> None:
    _FakeFalkorDBDriver.instances = []
    _install_falkordb_stub(monkeypatch)
    from cortex_harness.storage import factory as factory_mod

    # Ensure env var doesn't leak across tests.
    monkeypatch.delenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", raising=False)
    # Force reset the local client cache between tests so each starts clean.
    from cortex_harness.storage import qdrant as qdrant_mod
    from cortex_harness.storage import qdrant_remote as qremote_mod

    def _reset_local() -> None:
        with qdrant_mod._client_lock:
            for client in qdrant_mod._clients.values():
                try:
                    client.close()
                except Exception:
                    pass
            for lease in qdrant_mod._leases.values():
                lease.release()
            qdrant_mod._clients.clear()
            qdrant_mod._leases.clear()

    with qremote_mod._remote_client_lock:
        qremote_mod._remote_clients.clear()
    _reset_local()
    yield
    with qremote_mod._remote_client_lock:
        qremote_mod._remote_clients.clear()
    _reset_local()


def _resolved_storage(tmp_path: Path) -> ResolvedStorage:
    return ResolvedStorage(
        project_root=tmp_path,
        qdrant_base=tmp_path / "qdrant",
        qdrant_code_path=tmp_path / "qdrant" / "code",
        qdrant_doc_path=tmp_path / "qdrant" / "doc",
        falkordb_path=tmp_path / "falkordb" / "code" / "data.rdb",
        falkordb_code_path=tmp_path / "falkordb" / "code" / "data.rdb",
        falkordb_doc_path=tmp_path / "falkordb" / "doc" / "data.rdb",
        instance_id="default",
    )


def test_local_falkordb_document_role_uses_document_path_and_owner(tmp_path):
    resolved = _resolved_storage(tmp_path)
    factory = StorageFactory(backend_mode=BackendMode.LOCAL, resolved=resolved)

    driver = factory.get_falkordb_driver("stock_doc", role=StorageRole.DOCUMENT)

    assert driver.path == str(resolved.falkordb_doc_path)
    assert driver.owner_id == resolved.doc_owner_id
    assert driver.graph == "stock_doc"


class _FakeTargets:
    """Minimal stand-in for :class:`ProjectTargets` covering the four fields
    the factory actually reads."""

    def __init__(
        self,
        *,
        project_id: str = "demo",
        storage_backend: str = "local",
        remote_config: dict | None = None,
    ) -> None:
        self.project_id = project_id
        self.storage_backend = storage_backend
        self.remote_config = remote_config


# ---------------------------------------------------------------------------
# Local backend routing
# ---------------------------------------------------------------------------


def test_local_qdrant_route(tmp_path: Path) -> None:
    factory = StorageFactory(backend_mode=BackendMode.LOCAL, resolved=_resolved_storage(tmp_path))
    store = factory.get_qdrant_store(QdrantStorageRole.CODE)
    from cortex_harness.storage import LocalQdrantStore

    assert isinstance(store, LocalQdrantStore)


def test_local_falkordb_route(tmp_path: Path) -> None:
    factory = StorageFactory(backend_mode=BackendMode.LOCAL, resolved=_resolved_storage(tmp_path))
    driver = factory.get_falkordb_driver("hyper")
    assert isinstance(driver, _FakeFalkorDBDriver)
    # Local driver opens a file path; no URI.
    assert driver.path is not None
    assert driver.uri is None
    assert driver.graph == "hyper"


# ---------------------------------------------------------------------------
# Remote backend routing
# ---------------------------------------------------------------------------


def test_remote_qdrant_route(tmp_path: Path) -> None:
    remote = RemoteStorageConfig(qdrant_url="http://qdrant:6333")
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved_storage(tmp_path),
        remote=remote,
    )
    store = factory.get_qdrant_store(QdrantStorageRole.CODE)
    from cortex_harness.storage import RemoteQdrantStore

    assert isinstance(store, RemoteQdrantStore)
    assert store.url == "http://qdrant:6333"


def test_remote_falkordb_route(tmp_path: Path) -> None:
    remote = RemoteStorageConfig(falkordb_uri="redis://falkordb:6379")
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved_storage(tmp_path),
        remote=remote,
    )
    driver = factory.get_falkordb_driver("hyper")
    assert isinstance(driver, _FakeFalkorDBDriver)
    # Remote driver opens a URI; no file path.
    assert driver.uri == "redis://falkordb:6379"
    assert driver.path is None


def test_remote_falkordb_connection_failure_never_retries_local(
    tmp_path: Path, monkeypatch
) -> None:
    attempts: list[dict[str, Any]] = []

    class _FailingRemoteDriver:
        def __init__(self, **kwargs: Any) -> None:
            attempts.append(kwargs)
            raise ConnectionError("remote transport failed")

    monkeypatch.setattr(
        sys.modules["tools.graph.driver.falkordb_driver"],
        "FalkorDBDriver",
        _FailingRemoteDriver,
    )
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved_storage(tmp_path),
        remote=RemoteStorageConfig(falkordb_uri="redis://falkordb:6379"),
    )

    with pytest.raises(ConnectionError, match="remote transport failed"):
        factory.get_falkordb_driver("hyper")

    assert len(attempts) == 1
    assert attempts[0]["uri"] == "redis://falkordb:6379"
    assert "path" not in attempts[0]


# ---------------------------------------------------------------------------
# Mixed / partial remote
# ---------------------------------------------------------------------------


def test_remote_qdrant_falls_back_to_local_falkordb(tmp_path: Path) -> None:
    """A project may be remote for Qdrant only.

    The graph backend should fall back to local without raising.
    """
    remote = RemoteStorageConfig(qdrant_url="http://qdrant:6333")  # no falkordb_uri
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved_storage(tmp_path),
        remote=remote,
    )
    qdrant_store = factory.get_qdrant_store(QdrantStorageRole.CODE)
    from cortex_harness.storage import RemoteQdrantStore

    assert isinstance(qdrant_store, RemoteQdrantStore)
    driver = factory.get_falkordb_driver("hyper")
    # FalkorDB fell back to local because remote.falkordb_uri was unset.
    assert driver.uri is None
    assert driver.path is not None


def test_remote_falkordb_falls_back_to_local_qdrant(tmp_path: Path) -> None:
    remote = RemoteStorageConfig(falkordb_uri="redis://falkordb:6379")
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved_storage(tmp_path),
        remote=remote,
    )
    store = factory.get_qdrant_store(QdrantStorageRole.CODE)
    from cortex_harness.storage import LocalQdrantStore

    assert isinstance(store, LocalQdrantStore)
    driver = factory.get_falkordb_driver("hyper")
    assert driver.uri == "redis://falkordb:6379"


# ---------------------------------------------------------------------------
# from_targets + create_storage convenience
# ---------------------------------------------------------------------------


def test_from_targets_parses_remote(tmp_path: Path) -> None:
    targets = _FakeTargets(
        project_id="remote_proj",
        storage_backend="remote",
        remote_config={
            "qdrant_url": "http://qdrant:6333",
            "falkordb_uri": "redis://falkordb:6379",
        },
    )
    factory = StorageFactory.from_targets(targets, _resolved_storage(tmp_path))
    assert factory.backend_mode is BackendMode.REMOTE
    assert factory.is_remote() is True


def test_from_targets_defaults_to_local(tmp_path: Path) -> None:
    targets = _FakeTargets(project_id="local_proj", storage_backend="local")
    factory = StorageFactory.from_targets(targets, _resolved_storage(tmp_path))
    assert factory.backend_mode is BackendMode.LOCAL
    assert factory.is_remote() is False


def test_from_targets_unknown_backend_raises(tmp_path: Path) -> None:
    targets = _FakeTargets(project_id="bad", storage_backend="cloud")
    with pytest.raises(ValueError) as exc:
        StorageFactory.from_targets(targets, _resolved_storage(tmp_path))
    assert "cloud" in str(exc.value)


def test_from_targets_remote_without_remote_section_fails_closed(tmp_path: Path) -> None:
    targets = _FakeTargets(project_id="bad-remote", storage_backend="remote")
    with pytest.raises(ValueError, match="requires a 'remote' section"):
        StorageFactory.from_targets(targets, _resolved_storage(tmp_path))


def test_create_storage_returns_factory(tmp_path: Path, monkeypatch) -> None:
    """``create_storage`` resolves local paths and builds a factory."""
    monkeypatch.chdir(tmp_path)
    targets = _FakeTargets(project_id="local_proj", storage_backend="local")
    factory = create_storage(targets, project_root=tmp_path)
    assert factory.backend_mode is BackendMode.LOCAL


@pytest.mark.parametrize(
    ("storage_backend", "remote_config", "expected_mode"),
    [
        ("local", None, BackendMode.LOCAL),
        (
            "remote",
            {"qdrant_url": "http://qdrant:6333"},
            BackendMode.REMOTE,
        ),
    ],
)
def test_create_storage_none_project_root_uses_cwd(
    tmp_path: Path,
    monkeypatch,
    storage_backend: str,
    remote_config: dict | None,
    expected_mode: BackendMode,
) -> None:
    """Optional wrapper roots must preserve both local and remote routing."""
    monkeypatch.chdir(tmp_path)
    targets = _FakeTargets(
        project_id=f"{storage_backend}_proj",
        storage_backend=storage_backend,
        remote_config=remote_config,
    )

    factory = create_storage(targets, project_root=None)

    assert factory.backend_mode is expected_mode
    assert factory.resolved.project_root == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Emergency rollback (Red Team Q6)
# ---------------------------------------------------------------------------


def test_force_local_env_overrides_remote(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", "1")
    remote = RemoteStorageConfig(
        qdrant_url="http://qdrant:6333",
        falkordb_uri="redis://falkordb:6379",
    )
    factory = StorageFactory(
        backend_mode=BackendMode.REMOTE,
        resolved=_resolved_storage(tmp_path),
        remote=remote,
    )
    assert factory.backend_mode is BackendMode.LOCAL
    assert factory.is_remote() is False
    # Both components should fall back to local.
    from cortex_harness.storage import LocalQdrantStore

    assert isinstance(factory.get_qdrant_store(QdrantStorageRole.CODE), LocalQdrantStore)
    driver = factory.get_falkordb_driver("hyper")
    assert driver.uri is None
    assert driver.path is not None

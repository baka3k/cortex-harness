"""Light-touch coverage for Phase 04 wrapper refactors.

The full MCP-integration test surface lives in test_storage_factory and
test_backend_config; here we only verify the two wrappers correctly delegate
to the factory when ``project_id`` is supplied and preserve the legacy
``RemoteQdrantUnsupportedError`` rejection when only a URL-shaped locator is
given.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeFalkorDBDriver:
    instances: list[_FakeFalkorDBDriver] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        _FakeFalkorDBDriver.instances.append(self)


def _install_stubs() -> None:
    """Replace *only* the falkordb_driver module — keep the real ``tools`` package intact.

    Importing ``tools.common.project_registry`` requires the real
    ``tools`` package; pre-creating ``sys.modules['tools'] = ModuleType``
    would shadow it. Instead we just override the leaf driver module.
    """
    fake = types.ModuleType("tools.graph.driver.falkordb_driver")
    fake.FalkorDBDriver = _FakeFalkorDBDriver  # type: ignore[attr-defined]
    sys.modules["tools.graph.driver.falkordb_driver"] = fake


@pytest.fixture(autouse=True)
def _setup(monkeypatch, tmp_path_factory) -> None:
    _FakeFalkorDBDriver.instances = []
    _install_stubs()
    # Reset cache between tests.
    from cortex_harness.storage import qdrant_remote as qremote_mod
    from cortex_harness.storage import qdrant as qdrant_mod

    with qremote_mod._remote_client_lock:
        qremote_mod._remote_clients.clear()
    with qdrant_mod._client_lock:
        for c in qdrant_mod._clients.values():
            try:
                c.close()
            except Exception:
                pass
        for l in qdrant_mod._leases.values():
            l.release()
        qdrant_mod._clients.clear()
        qdrant_mod._leases.clear()

    # Redirect the registry's default discovery to the test's config dir so
    # tests aren't influenced by the real `.cortext-harness/config/` tree.
    from tools.common import project_registry as pr

    test_root = tmp_path_factory.mktemp("registries")
    pr._test_root = test_root  # type: ignore[attr-defined]
    monkeypatch.setattr(pr, "_default_config_dir", lambda: test_root / "config")
    monkeypatch.setattr(pr, "DEFAULT_CONFIG_DIRNAME", "config")
    yield


def _write_project_config(test_root: Path, name: str, body: dict) -> Path:
    config_dir = test_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{name}.json").write_text(__import__('json').dumps(body), encoding="utf-8")
    return config_dir


def _get_test_root() -> Path:
    from tools.common import project_registry as pr

    return pr._test_root  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# doc_local_qdrant.get_document_qdrant_store
# ---------------------------------------------------------------------------


def test_doc_wrapper_routes_via_factory(tmp_path: Path, monkeypatch) -> None:
    """``project_id`` argument makes the wrapper go through the factory.

    With a remote ``storage_backend``, ``RemoteQdrantStore`` should be
    returned; with default local, ``LocalQdrantStore``.
    """
    from doc_local_qdrant import get_document_qdrant_store

    test_root = _get_test_root()
    _write_project_config(
        test_root,
        "remote_doc_proj",
        {
            "project": {"code": "remote_doc_proj", "name": "Remote Doc"},
            "storage_backend": "remote",
            "remote": {"qdrant_url": "http://qdrant:6333"},
        },
    )

    # Stub QdrantClient to avoid network.
    from qdrant_client import QdrantClient  # type: ignore
    monkeypatch.setattr(QdrantClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(QdrantClient, "close", lambda self: None)
    monkeypatch.chdir(tmp_path)

    store = get_document_qdrant_store(project_id="remote_doc_proj")
    from cortex_harness.storage import RemoteQdrantStore

    assert isinstance(store, RemoteQdrantStore)


def test_doc_wrapper_project_id_preserves_local_mode(
    tmp_path: Path, monkeypatch
) -> None:
    """Project routing without an explicit root also keeps local storage."""
    from doc_local_qdrant import get_document_qdrant_store

    test_root = _get_test_root()
    _write_project_config(
        test_root,
        "local_doc_proj",
        {
            "project": {"code": "local_doc_proj", "name": "Local Doc"},
            "storage_backend": "local",
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORTEX_DATA_HOME", str(tmp_path / "data-home"))

    store = get_document_qdrant_store(project_id="local_doc_proj")
    from cortex_harness.storage import LocalQdrantStore

    assert isinstance(store, LocalQdrantStore)
    assert store.path.is_relative_to(tmp_path)


def test_doc_wrapper_falls_back_to_local(tmp_path: Path, monkeypatch) -> None:
    """Without ``project_id`` and without an explicit URL, returns local."""
    from doc_local_qdrant import get_document_qdrant_store
    from cortex_harness.storage import LocalQdrantStore

    store = get_document_qdrant_store(project_root=tmp_path)
    assert isinstance(store, LocalQdrantStore)


def test_doc_wrapper_legacy_url_locator_rejected(tmp_path: Path) -> None:
    """Legacy URL locator still raises ``RemoteQdrantUnsupportedError``."""
    from doc_local_qdrant import RemoteQdrantUnsupportedError, get_document_qdrant_store

    with pytest.raises(RemoteQdrantUnsupportedError):
        get_document_qdrant_store(locator="http://example.invalid:6333", project_root=tmp_path)


# ---------------------------------------------------------------------------
# local_qdrant.get_code_qdrant_store
# ---------------------------------------------------------------------------


def test_code_wrapper_routes_via_factory(tmp_path: Path, monkeypatch) -> None:
    """``project_id`` makes ``get_code_qdrant_store`` go through the factory."""
    from tools.common.local_qdrant import get_code_qdrant_store

    test_root = _get_test_root()
    _write_project_config(
        test_root,
        "remote_code_proj",
        {
            "project": {"code": "remote_code_proj", "name": "Remote Code"},
            "storage_backend": "remote",
            "remote": {"qdrant_url": "http://qdrant:6333"},
        },
    )

    from qdrant_client import QdrantClient  # type: ignore
    monkeypatch.setattr(QdrantClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(QdrantClient, "close", lambda self: None)

    store = get_code_qdrant_store(
        project_id="remote_code_proj", project_root=tmp_path,
    )
    from cortex_harness.storage import RemoteQdrantStore

    assert isinstance(store, RemoteQdrantStore)


def test_code_wrapper_legacy_url_locator_rejected(tmp_path: Path) -> None:
    from tools.common.local_qdrant import (
        RemoteQdrantUnsupportedError,
        get_code_qdrant_store,
    )

    with pytest.raises(RemoteQdrantUnsupportedError):
        get_code_qdrant_store(locator="http://example.invalid:6333", project_root=tmp_path)


def test_code_wrapper_local_default(tmp_path: Path) -> None:
    """No project_id and no URL locator → local store."""
    from tools.common.local_qdrant import get_code_qdrant_store
    from cortex_harness.storage import LocalQdrantStore

    store = get_code_qdrant_store(project_root=tmp_path)
    assert isinstance(store, LocalQdrantStore)

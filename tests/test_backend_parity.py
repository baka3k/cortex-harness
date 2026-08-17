"""Local/Remote backend parity tests (Phase 05).

Verify that :class:`LocalQdrantStore` and :class:`RemoteQdrantStore` expose
the same surface area so callers can swap one for the other without code
changes. Both implementations are exercised through the same API surface;
the underlying ``qdrant_client.QdrantClient`` is mocked so no real service
is required.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class _ScriptableQdrant:
    """Test double that exposes a configurable ``collections``/``points`` map.

    Each ``QdrantClient(...)`` call returns the same instance — every
    storage layer ends up sharing the in-memory dataset so local vs remote
    operations are truly comparable. Shared state is kept on a single
    class-level ``_shared`` instance; per-instance attributes only store
    kwargs for diagnostics.
    """

    _shared: "_ScriptableQdrant | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        if _ScriptableQdrant._shared is None:
            _ScriptableQdrant._shared = self
            self.collections = {}
            self.points = []
        else:
            # Re-bind to the existing shared state so every client call sees
            # the same dataset.
            self.collections = _ScriptableQdrant._shared.collections
            self.points = _ScriptableQdrant._shared.points

    def get_collections(self) -> Any:
        return MagicMock(
            collections=[MagicMock(**{"name": n}) for n in self.collections]
        )

    def collection_exists(self, *, collection_name: str) -> bool:
        return collection_name in self.collections

    def get_collection(self, *, collection_name: str) -> Any:
        info = self.collections.get(collection_name)
        return MagicMock(
            config=MagicMock(
                params=MagicMock(vectors=MagicMock(size=(info or {}).get("size", 4)))
            )
        )

    def create_collection(self, *, collection_name: str, vectors_config: Any, **kwargs: Any) -> Any:
        self.collections[collection_name] = {"size": getattr(vectors_config, "size", 4)}
        return MagicMock()

    def recreate_collection(self, *, collection_name: str, vectors_config: Any, **kwargs: Any) -> Any:
        self.collections[collection_name] = {"size": getattr(vectors_config, "size", 4)}
        return MagicMock()

    def delete_collection(self, *, collection_name: str) -> Any:
        self.collections.pop(collection_name, None)
        return MagicMock()

    def upsert(self, *, collection_name: str, points: Any, **kwargs: Any) -> Any:
        for point in points:
            self.points.append(point)
        return MagicMock()

    def upload_points(self, *, collection_name: str, points: Any, **kwargs: Any) -> Any:
        for point in points:
            self.points.append(point)
        return MagicMock()

    def search(self, **kwargs: Any) -> list[Any]:
        return [MagicMock(id="hit-1"), MagicMock(id="hit-2")]

    def query_points(self, **kwargs: Any) -> Any:
        return MagicMock(points=[MagicMock(id="hit-1"), MagicMock(id="hit-2")])

    def scroll(self, **kwargs: Any) -> tuple[list[Any], Any]:
        return [MagicMock(id="p1")], None

    def retrieve(self, **kwargs: Any) -> list[Any]:
        return [MagicMock(id="r1")]

    def count(self, **kwargs: Any) -> Any:
        return MagicMock(count=len(self.points))

    def delete(self, *, collection_name: str, points_selector: Any, **kwargs: Any) -> Any:
        return MagicMock()

    def set_payload(self, **kwargs: Any) -> Any:
        return MagicMock()

    def overwrite_payload(self, **kwargs: Any) -> Any:
        return MagicMock()

    def create_payload_index(self, **kwargs: Any) -> Any:
        return MagicMock()

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _scriptable_qdrant(monkeypatch) -> Any:
    """Patch every ``QdrantClient(...)`` call site to a single test double."""
    _ScriptableQdrant._shared = None
    fake = _ScriptableQdrant
    # Patch both the source module attribute *and* the bound names in the
    # adapter modules so order-independent test runs see the fake.
    monkeypatch.setattr("qdrant_client.QdrantClient", fake)
    monkeypatch.setattr("cortex_harness.storage.qdrant.QdrantClient", fake)
    monkeypatch.setattr("cortex_harness.storage.qdrant_remote.QdrantClient", fake)
    # Drop cached remote/local clients so a fresh construction reaches the fake.
    from cortex_harness.storage import qdrant as qdrant_mod
    from cortex_harness.storage import qdrant_remote as qremote_mod

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
    yield fake
    _ScriptableQdrant._shared = None


def _build_local(tmp_path_factory) -> Any:
    from cortex_harness.storage import (
        LocalQdrantStore,
        QdrantStorageRole,
        resolve_storage,
    )

    root = tmp_path_factory.mktemp("local_parity")
    resolved = resolve_storage(root)
    return LocalQdrantStore(resolved, QdrantStorageRole.CODE)


def _build_remote() -> Any:
    from cortex_harness.storage import RemoteQdrantStore

    return RemoteQdrantStore(url="http://qdrant:6333")


# ---------------------------------------------------------------------------
# Method-name parity
# ---------------------------------------------------------------------------


def _public_methods(store: Any) -> set[str]:
    return {name for name in dir(store) if not name.startswith("_")}


def test_method_parity(tmp_path_factory) -> None:
    """Both stores expose the same public methods up to backend-specific extras."""
    local = _build_local(tmp_path_factory)
    remote = _build_remote()
    public_local = _public_methods(local)
    public_remote = _public_methods(remote)
    # Backend-specific extras on RemoteQdrantStore.
    extra_on_remote = {"url", "check_connection", "ensure_reachable"}
    missing_on_remote = public_local - public_remote
    assert missing_on_remote.issubset({"path"}), f"Missing on remote: {missing_on_remote}"
    unexpected_on_remote = (public_remote - public_local) - extra_on_remote
    assert not unexpected_on_remote, f"Unexpected extras: {unexpected_on_remote}"


# ---------------------------------------------------------------------------
# Behaviour parity
# ---------------------------------------------------------------------------


def test_search_returns_list_of_points(tmp_path_factory) -> None:
    local = _build_local(tmp_path_factory)
    remote = _build_remote()
    local.create_collection("c", vectors_config=MagicMock(size=4))
    remote.create_collection("c", vectors_config=MagicMock(size=4))
    local_result = local.search("c", [0.1, 0.2, 0.3, 0.4], limit=2)
    remote_result = remote.search("c", [0.1, 0.2, 0.3, 0.4], limit=2)
    assert isinstance(local_result, list) and isinstance(remote_result, list)
    assert len(local_result) == len(remote_result)


def test_upsert_normalization(tmp_path_factory) -> None:
    local = _build_local(tmp_path_factory)
    remote = _build_remote()
    point = {"id": 1, "vector": [0.1, 0.2], "payload": {"k": "v"}}
    local.upsert("c", [point], wait=True)
    remote.upsert("c", [point], wait=True)
    shared = _ScriptableQdrant._shared
    assert shared is not None
    assert len(shared.points) == 2
    # Both stores normalize dict-shaped points to the same struct via
    # ``qmodels.PointStruct``; the fake just records them.
    for recorded in shared.points:
        assert recorded.id == 1


def test_collection_lifecycle(tmp_path_factory) -> None:
    local = _build_local(tmp_path_factory)
    remote = _build_remote()
    for store in (local, remote):
        assert store.collection_exists("c") is False
        store.create_collection("c", vectors_config=MagicMock(size=4))
        assert store.collection_exists("c") is True
        store.delete_collection("c")
        assert store.collection_exists("c") is False

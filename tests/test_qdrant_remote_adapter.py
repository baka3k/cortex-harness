"""Tests for the Qdrant remote adapter (Phase 02).

Verifies:

* :class:`RemoteQdrantStore` mirrors :class:`LocalQdrantStore` semantics.
* Client cache is keyed by ``(url, api_key)`` so different credentials do
  not collide but identical lookups share one connection.
* :class:`BackendConnectionError` is raised with an actionable message.
* Repr never leaks the API key.

The underlying ``qdrant_client.QdrantClient`` is patched at runtime via
``monkeypatch.setattr`` so each test is independent of import order.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeClient:
    """Records constructor args and operation calls for assertions.

    Each ``QdrantClient(...)`` call is independent (no shared state) so tests
    that need to inspect ``get_collections`` results can configure their
    instance directly.
    """

    instances: list["_FakeClient"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[tuple[str, tuple, dict]] = []
        self.collections_result = MagicMock(collections=[])
        _FakeClient.instances.append(self)

    def get_collections(self) -> Any:
        self.calls.append(("get_collections", (), {}))
        return self.collections_result

    def close(self) -> None:
        self.calls.append(("close", (), {}))


@pytest.fixture(autouse=True)
def _install_qdrant_stub(monkeypatch) -> None:
    """Patch ``qdrant_client.QdrantClient`` *and* the bound reference inside
    :mod:`cortex_harness.storage.qdrant_remote` so order-independent runs
    consistently see the recording fake.
    """
    _FakeClient.instances = []
    monkeypatch.setattr("qdrant_client.QdrantClient", _FakeClient)
    monkeypatch.setattr(
        "cortex_harness.storage.qdrant_remote.QdrantClient", _FakeClient
    )
    # Reset remote client cache so each test starts clean.
    from cortex_harness.storage import qdrant_remote as qremote_mod

    with qremote_mod._remote_client_lock:
        qremote_mod._remote_clients.clear()
    yield
    with qremote_mod._remote_client_lock:
        qremote_mod._remote_clients.clear()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_remote_store_creation() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    assert store.url == "http://qdrant:6333"
    assert store.client is _FakeClient.instances[-1]
    assert _FakeClient.instances[-1].kwargs["url"] == "http://qdrant:6333"
    assert _FakeClient.instances[-1].kwargs["check_compatibility"] is False


def test_remote_store_repr_redacts_api_key() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333", api_key="super-secret")  # sensitive-guard:allow -- local test fixture
    rendered = repr(store)
    assert "super-secret" not in rendered
    assert "***" in rendered


def test_remote_store_passes_api_key_to_client() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    RemoteQdrantStore(url="http://qdrant:6333", api_key="secret")  # sensitive-guard:allow -- local test fixture
    assert _FakeClient.instances[-1].kwargs.get("api_key") == "secret"


# ---------------------------------------------------------------------------
# Client cache
# ---------------------------------------------------------------------------


def test_remote_client_caching() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    a = RemoteQdrantStore(url="http://qdrant:6333")
    b = RemoteQdrantStore(url="http://qdrant:6333")
    assert a.client is b.client
    assert len(_FakeClient.instances) == 1


def test_remote_client_cache_key_includes_api_key() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    a = RemoteQdrantStore(url="http://qdrant:6333", api_key="key-a")  # sensitive-guard:allow -- local test fixture
    b = RemoteQdrantStore(url="http://qdrant:6333", api_key="key-b")  # sensitive-guard:allow -- local test fixture
    assert a.client is not b.client
    assert len(_FakeClient.instances) == 2


def test_remote_client_different_urls() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    RemoteQdrantStore(url="http://qdrant-a:6333")
    RemoteQdrantStore(url="http://qdrant-b:6333")
    assert len(_FakeClient.instances) == 2


def test_reset_remote_clients_closes_all() -> None:
    from cortex_harness.storage import RemoteQdrantStore, reset_remote_clients

    RemoteQdrantStore(url="http://qdrant-a:6333")
    RemoteQdrantStore(url="http://qdrant-b:6333")
    reset_remote_clients()
    for client in _FakeClient.instances:
        assert ("close", (), {}) in client.calls


# ---------------------------------------------------------------------------
# Operation delegation
# ---------------------------------------------------------------------------


def test_remote_store_list_collection_names() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    a, b = MagicMock(), MagicMock()
    a.name = "a"
    b.name = "b"
    target.collections_result = MagicMock(collections=[a, b])
    assert store.list_collection_names() == ["a", "b"]


def test_remote_store_collection_exists() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    target.collection_exists = MagicMock(return_value=True)
    assert store.collection_exists("foo") is True


def test_remote_store_search_uses_legacy_search_when_available() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    target.search = MagicMock(return_value=["hit-1"])
    result = store.search("col", [0.1, 0.2], limit=5)
    assert result == ["hit-1"]
    target.search.assert_called_once()


def test_remote_store_upsert_delegates() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    target.upsert = MagicMock(return_value="ok")
    result = store.upsert("col", [{"id": 1, "vector": [0.1]}])
    assert result == "ok"
    target.upsert.assert_called_once()


def test_remote_store_scroll_passes_filter() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    target.scroll = MagicMock(return_value=("scrolled", None))
    result = store.scroll("col", limit=10, scroll_filter="flt")
    assert result == ("scrolled", None)
    target.scroll.assert_called_once_with(
        collection_name="col", scroll_filter="flt", limit=10,
        with_payload=True, with_vectors=False, offset=None,
    )


# ---------------------------------------------------------------------------
# Health checks and error mapping
# ---------------------------------------------------------------------------


def test_check_connection_true_when_reachable() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    assert store.check_connection() is True


def test_check_connection_false_on_error() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    target.get_collections = MagicMock(side_effect=ConnectionError("refused"))
    assert store.check_connection() is False


def test_ensure_reachable_raises_backend_connection_error() -> None:
    from cortex_harness.storage import BackendConnectionError, RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    cause = ConnectionError("refused")
    target.get_collections = MagicMock(side_effect=cause)
    with pytest.raises(BackendConnectionError) as exc:
        store.ensure_reachable()
    assert exc.value.backend == "Qdrant"
    assert exc.value.url == "http://qdrant:6333"
    assert exc.value.cause is cause
    assert "running" in str(exc.value)


def test_close_drops_client_from_cache() -> None:
    from cortex_harness.storage import RemoteQdrantStore

    store = RemoteQdrantStore(url="http://qdrant:6333")
    target = _FakeClient.instances[-1]
    store.close()
    assert ("close", (), {}) in target.calls
    RemoteQdrantStore(url="http://qdrant:6333")
    assert len(_FakeClient.instances) == 2

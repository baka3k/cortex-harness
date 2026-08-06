"""Tests for the shared LocalQdrantStore adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cortex_harness.storage import (
    LocalQdrantStore,
    QdrantStorageRole,
    ResolvedStorage,
    build_filter,
    get_client,
    reset_clients,
)


def _fake_resolved(tmp_path: Path) -> ResolvedStorage:
    return ResolvedStorage(
        project_root=tmp_path,
        qdrant_base=tmp_path / "local_qdrant_db",
        qdrant_code_path=tmp_path / "local_qdrant_db" / "code",
        qdrant_doc_path=tmp_path / "local_qdrant_db" / "doc",
        falkordb_path=tmp_path / "local_falkordb_db" / "cortex.rdb",
    )


def test_get_client_caches_per_path(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_client = MagicMock()
    with patch("cortex_harness.storage.qdrant.QdrantClient", return_value=fake_client) as factory:
        first = get_client(resolved, QdrantStorageRole.CODE)
        second = get_client(resolved, QdrantStorageRole.CODE)
    assert first is fake_client
    assert second is fake_client
    factory.assert_called_once_with(path=str(resolved.qdrant_code_path))
    reset_clients()


def test_get_client_returns_distinct_clients_for_distinct_roles(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_clients = [MagicMock(name=f"client{i}") for i in range(2)]
    with patch("cortex_harness.storage.qdrant.QdrantClient", side_effect=fake_clients) as factory:
        code = get_client(resolved, QdrantStorageRole.CODE)
        doc = get_client(resolved, QdrantStorageRole.DOCUMENT)
    assert code is not doc
    assert code is fake_clients[0]
    assert doc is fake_clients[1]
    assert factory.call_count == 2
    reset_clients()


def test_local_qdrant_store_passes_role_path(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_client = MagicMock()
    with patch("cortex_harness.storage.qdrant.QdrantClient", return_value=fake_client):
        store = LocalQdrantStore(resolved, QdrantStorageRole.DOCUMENT)
    assert store.path == resolved.qdrant_doc_path
    assert store.role == QdrantStorageRole.DOCUMENT


def test_collection_operations_call_client(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_client = MagicMock()
    with patch("cortex_harness.storage.qdrant.QdrantClient", return_value=fake_client):
        store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
        store.list_collection_names()
        store.collection_exists("foo")
        store.get_collection_info("foo")
        store.create_collection("foo", vectors_config={"size": 4, "distance": "Cosine"})
        store.recreate_collection("foo", vectors_config={"size": 4, "distance": "Cosine"})
        store.delete_collection("foo")
    assert fake_client.get_collections.called
    assert fake_client.collection_exists.called
    assert fake_client.get_collection.called
    assert fake_client.create_collection.called
    assert fake_client.recreate_collection.called
    assert fake_client.delete_collection.called


def test_point_operations_call_client(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_client = MagicMock()
    with patch("cortex_harness.storage.qdrant.QdrantClient", return_value=fake_client):
        store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
        store.upsert("foo", points=[object()])
        store.search("foo", query_vector=[0.0, 0.1, 0.2, 0.3], limit=5)
        store.query_points("foo", query=[0.0, 0.1, 0.2, 0.3])
        store.scroll("foo", limit=10)
        store.retrieve("foo", ids=["a", "b"])
        store.count("foo")
        store.delete("foo", points_selector_ids=["a"])
        store.set_payload("foo", payload={"k": "v"}, points=["a"])
        store.overwrite_payload("foo", payload={"k": "v"}, points=["a"])
        store.create_payload_index("foo", field_name="project_id")
    assert fake_client.upsert.called
    assert fake_client.search.called
    assert fake_client.query_points.called
    assert fake_client.scroll.called
    assert fake_client.retrieve.called
    assert fake_client.count.called
    assert fake_client.delete.called
    assert fake_client.set_payload.called
    assert fake_client.overwrite_payload.called
    assert fake_client.create_payload_index.called


def test_store_close_releases_only_owned_client(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_clients = [MagicMock(name=f"client{i}") for i in range(2)]
    with patch("cortex_harness.storage.qdrant.QdrantClient", side_effect=fake_clients):
        code_store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
        doc_store = LocalQdrantStore(resolved, QdrantStorageRole.DOCUMENT)
        code_store.close()
    assert fake_clients[0].close.called
    assert not fake_clients[1].close.called
    # Doc store still usable after code store closes.
    with patch("cortex_harness.storage.qdrant.QdrantClient", return_value=fake_clients[1]):
        doc_store.list_collection_names()
    assert fake_clients[1].get_collections.called
    reset_clients()


def test_reset_clients_clears_cache(tmp_path: Path) -> None:
    resolved = _fake_resolved(tmp_path)
    fake_client = MagicMock()
    with patch("cortex_harness.storage.qdrant.QdrantClient", return_value=fake_client):
        get_client(resolved, QdrantStorageRole.CODE)
        reset_clients()
        get_client(resolved, QdrantStorageRole.CODE)
    assert fake_client.close.called


def test_build_filter_translates_plain_dicts() -> None:
    """build_filter must convert plain dicts to ``models.FieldCondition``."""
    from qdrant_client.http import models as qmodels

    filt = build_filter([
        {"key": "project_id", "match": {"value": "demo"}},
        {"key": "kind", "match": {"value": "Function"}},
    ])
    assert isinstance(filt, qmodels.Filter)
    assert len(filt.must) == 2
    for cond in filt.must:
        assert isinstance(cond, qmodels.FieldCondition)
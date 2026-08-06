"""Integration test: LocalQdrantStore against a real on-disk Qdrant local backend.

Skipped automatically if the ``qdrant_client`` package is not installed.
Uses a temporary directory so the test never touches the repository's real
local database.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client.http import models as qmodels  # noqa: E402

from cortex_harness.storage import (  # noqa: E402
    LocalQdrantStore,
    QdrantStorageRole,
    ResolvedStorage,
    reset_clients,
)


def _resolved(tmp_path: Path) -> ResolvedStorage:
    return ResolvedStorage(
        project_root=tmp_path,
        qdrant_base=tmp_path / "local_qdrant_db",
        qdrant_code_path=tmp_path / "local_qdrant_db" / "code",
        qdrant_doc_path=tmp_path / "local_qdrant_db" / "doc",
        falkordb_path=tmp_path / "local_falkordb_db" / "cortex.rdb",
    )


def _unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def test_create_upsert_search_roundtrip(tmp_path: Path) -> None:
    reset_clients()
    resolved = _resolved(tmp_path)
    name = _unique_name("roundtrip")
    store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    store.create_collection(
        name,
        vectors_config=qmodels.VectorParams(size=4, distance=qmodels.Distance.COSINE),
    )
    try:
        store.upsert(
            name,
            points=[
                qmodels.PointStruct(id=1, vector=[0.0, 0.1, 0.2, 0.3], payload={"project_id": "demo"}),
                qmodels.PointStruct(id=2, vector=[0.4, 0.5, 0.6, 0.7], payload={"project_id": "demo"}),
            ],
        )
        hits = store.query_points(name, query=[0.0, 0.1, 0.2, 0.3], limit=2)
        ids = {int(h.id) for h in hits.points}
        assert ids == {1, 2}
    finally:
        store.delete_collection(name)
        store.close()
        reset_clients()


def test_persistence_across_close_reopen(tmp_path: Path) -> None:
    """A point written, then the client closed, must survive a reopen."""
    reset_clients()
    resolved = _resolved(tmp_path)
    name = _unique_name("persist")
    first = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    first.create_collection(
        name,
        vectors_config=qmodels.VectorParams(size=4, distance=qmodels.Distance.COSINE),
    )
    first.upsert(
        name,
        points=[
            qmodels.PointStruct(id=1, vector=[1.0, 0.0, 0.0, 0.0], payload={"k": "v"}),
        ],
    )
    first.close()
    reset_clients()

    second = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    try:
        assert second.collection_exists(name)
        hits = second.retrieve(name, ids=[1])
        assert hits and hits[0].payload == {"k": "v"}
    finally:
        second.delete_collection(name)
        second.close()
        reset_clients()


def test_code_and_doc_roles_do_not_lock_each_other(tmp_path: Path) -> None:
    """Both roles must be openable simultaneously — Qdrant local locks per dir."""
    reset_clients()
    resolved = _resolved(tmp_path)
    code_store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    doc_store = LocalQdrantStore(resolved, QdrantStorageRole.DOCUMENT)
    name_code = _unique_name("c")
    name_doc = _unique_name("d")
    code_store.create_collection(
        name_code,
        vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE),
    )
    doc_store.create_collection(
        name_doc,
        vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE),
    )
    try:
        code_store.upsert(
            name_code,
            points=[qmodels.PointStruct(id=1, vector=[0.0, 1.0], payload={})],
        )
        doc_store.upsert(
            name_doc,
            points=[qmodels.PointStruct(id=1, vector=[1.0, 0.0], payload={})],
        )
        assert code_store.collection_exists(name_code)
        assert doc_store.collection_exists(name_doc)
    finally:
        code_store.delete_collection(name_code)
        doc_store.delete_collection(name_doc)
        code_store.close()
        doc_store.close()
        reset_clients()


def test_filter_query_returns_only_matching_points(tmp_path: Path) -> None:
    reset_clients()
    resolved = _resolved(tmp_path)
    name = _unique_name("filter")
    store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    store.create_collection(
        name,
        vectors_config=qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE),
    )
    try:
        store.upsert(
            name,
            points=[
                qmodels.PointStruct(id=1, vector=[0.0, 1.0], payload={"project_id": "alpha"}),
                qmodels.PointStruct(id=2, vector=[1.0, 0.0], payload={"project_id": "beta"}),
            ],
        )
        hits, _ = store.scroll(
            name,
            scroll_filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="project_id", match=qmodels.MatchValue(value="alpha"))],
            ),
            limit=10,
        )
        ids = [int(p.id) for p in hits]
        assert ids == [1]
    finally:
        store.delete_collection(name)
        store.close()
        reset_clients()


def test_two_project_collections_share_owner_but_reset_is_isolated(tmp_path: Path) -> None:
    reset_clients()
    resolved = _resolved(tmp_path)
    store = LocalQdrantStore(resolved, QdrantStorageRole.CODE)
    alpha = _unique_name("alpha")
    beta = _unique_name("beta")
    vector_config = qmodels.VectorParams(size=2, distance=qmodels.Distance.COSINE)
    store.create_collection(alpha, vectors_config=vector_config)
    store.create_collection(beta, vectors_config=vector_config)
    try:
        store.upsert(alpha, [qmodels.PointStruct(id=1, vector=[1.0, 0.0], payload={})])
        store.upsert(beta, [qmodels.PointStruct(id=1, vector=[0.0, 1.0], payload={})])
        store.delete_collection(alpha)
        assert not store.collection_exists(alpha)
        assert store.collection_exists(beta)
        assert store.retrieve(beta, [1])
    finally:
        if store.collection_exists(alpha):
            store.delete_collection(alpha)
        if store.collection_exists(beta):
            store.delete_collection(beta)
        store.close()
        reset_clients()

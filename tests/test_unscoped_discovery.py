"""Phase 04 — unscoped search-all is discovery-based and directory-independent.

"Search all" means "everything the shared stores actually hold": each
fallback store is listed live for ``*_doc`` shards (registry pins first,
fan-out capped with a visible warning), so results are identical no matter
which project directory the server was started from (operator amendment
A1).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
for path in (str(DOC_TINY), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import mcp_graph_rag


class _Hit:
    def __init__(self, score, payload):
        self.score = score
        self.payload = payload


class FakeQdrant:
    def __init__(self, collections=None, hits=None, listing_error=None, path=None):
        self._collections = list(collections or [])
        self._hits = dict(hits or {})
        self._listing_error = listing_error
        self.search_calls = []
        self.list_calls = 0
        # Stable store identity (mirrors LocalQdrantStore.path / RemoteQdrantStore.url).
        self.path = path or f"fake-store://{id(self)}"

    def list_collection_names(self):
        self.list_calls += 1
        if self._listing_error is not None:
            raise self._listing_error
        return list(self._collections)

    def search(self, **kwargs):
        name = kwargs["collection_name"]
        self.search_calls.append(name)
        return list(self._hits.get(name, []))


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    env = {k: v for k, v in os.environ.items() if k != "QDRANT_URL"}
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(mcp_graph_rag, "_qdrant_stores", {})
    monkeypatch.setattr(mcp_graph_rag, "_graph_drivers", {})


def _patch_registry(monkeypatch, projects, targets):
    """Registered pins for ``projects``; ids not in targets raise."""
    def fake_get_store(project_id=None):
        if project_id and project_id not in targets:
            raise mcp_graph_rag.ProjectNotRegisteredError(project_id, [])
        return stores_for(project_id)

    def stores_for(project_id):
        return local_store

    local_store = FakeQdrant()
    monkeypatch.setattr(mcp_graph_rag, "get_document_qdrant_store", fake_get_store)
    monkeypatch.setattr(
        mcp_graph_rag, "list_registered_projects", lambda: list(projects)
    )
    monkeypatch.setattr(
        mcp_graph_rag,
        "resolve_doc_candidates",
        lambda pid: (
            [SimpleNamespace(project_id=pid, doc_qdrant_collection=f"{pid.lower()}_doc")]
            if pid in targets
            else []
        ),
    )
    monkeypatch.setattr(
        mcp_graph_rag,
        "resolve_project_targets",
        lambda pid: SimpleNamespace(
            project_id=pid, doc_qdrant_collection=f"{pid.lower()}_doc"
        ),
    )
    return local_store


def test_ac1_unscoped_results_are_directory_independent(monkeypatch):
    """Two registries that know different projects still find X_doc identically."""
    shared_remote = FakeQdrant(
        ["x_doc"],
        {"x_doc": [_Hit(0.9, {"text": "shared", "source_id": "s", "paragraph_id": 1})]},
    )
    instance = FakeQdrant([])
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [shared_remote, instance]
    )

    def run_unscoped(known_projects):
        store = _patch_registry(
            monkeypatch, known_projects, {p: True for p in known_projects}
        )
        # The pin store is the same local instance store for both runs.
        monkeypatch.setattr(
            mcp_graph_rag,
            "get_document_qdrant_store",
            lambda project_id=None: (
                store
                if project_id in known_projects or project_id is None
                else (_ for _ in ()).throw(
                    mcp_graph_rag.ProjectNotRegisteredError(project_id, [])
                )
            ),
        )
        rows, scope = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)
        return rows, scope

    rows_a, scope_a = run_unscoped(["Proj_a"])
    rows_b, scope_b = run_unscoped(["Proj_b"])

    assert [r["text"] for r in rows_a] == [r["text"] for r in rows_b] == ["shared"]
    assert scope_a["per_collection_hits"] == scope_b["per_collection_hits"]
    assert scope_a["per_collection_hits"] == {"x_doc": 1}
    assert scope_a["discovered_collections"] == ["x_doc"]
    assert scope_b["discovered_collections"] == ["x_doc"]
    # Both runs searched the unregistered data shard on the shared store.
    assert shared_remote.search_calls.count("x_doc") == 2  # once per run


def test_ac2_shard_created_between_calls_is_discovered(monkeypatch):
    _patch_registry(monkeypatch, [], {})
    store = FakeQdrant(
        [],
        {"fresh_doc": [_Hit(0.9, {"text": "new", "source_id": "s", "paragraph_id": 1})]},
    )
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [store]
    )

    rows1, scope1 = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)
    assert rows1 == []
    assert scope1["discovered_collections"] == []

    store._collections = ["fresh_doc"]  # ingest lands after server start
    rows2, scope2 = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)
    assert [r["text"] for r in rows2] == ["new"]
    assert scope2["discovered_collections"] == ["fresh_doc"]


def test_ac3_fan_out_cap_truncates_with_warning_pins_first(monkeypatch,):
    pins = {f"proj_{i}": True for i in range(2)}
    _patch_registry(monkeypatch, [f"proj_{i}" for i in range(2)], pins)
    store = FakeQdrant([f"shard_{i}_doc" for i in range(5)])
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [store]
    )
    monkeypatch.setenv("SCOPE_MAX_TARGETS", "3")

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)

    # 2 registry pins + 5 discovered shards + 1 instance tail, capped at 3.
    assert scope["targets_total"] == 8
    assert scope["targets_searched"] == 3
    assert any(
        "capped at 3 targets" in w and "5 shards not searched" in w
        for w in scope["warnings"]
    )


def test_ac3b_pins_precede_discovered_shards(monkeypatch):
    _patch_registry(monkeypatch, ["proj_a"], {"proj_a": True})
    store = FakeQdrant(["x_doc"])
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [store]
    )

    targets = mcp_graph_rag._qdrant_search_targets(None, None)
    names = [names for _store, names in targets if names]
    # Registry pin pair comes first; the discovered shard follows.
    assert names[0] == ["proj_a_doc"]
    assert ["x_doc"] in names


def test_ac4_hits_are_attributed_per_collection(monkeypatch):
    _patch_registry(monkeypatch, [], {})
    store = FakeQdrant(
        ["one_doc", "two_doc"],
        {
            "one_doc": [
                _Hit(0.9, {"text": "a", "source_id": "a", "paragraph_id": 1}),
                _Hit(0.8, {"text": "b", "source_id": "b", "paragraph_id": 1}),
            ],
            "two_doc": [_Hit(0.7, {"text": "c", "source_id": "c", "paragraph_id": 1})],
        },
    )
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [store]
    )

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)
    assert scope["per_collection_hits"] == {"one_doc": 2, "two_doc": 1}
    assert {r["collection"] for r in rows} == {"one_doc", "two_doc"}
    # Tail (None) must not re-search collections the discovery pair covered.
    assert sorted(store.search_calls) == ["one_doc", "two_doc"]


def test_ac4b_pin_store_and_tail_are_distinct_objects_over_one_backend(monkeypatch):
    """Regression: the registry pin and the fallback tail resolve to two
    different objects over the same physical store — the tail must not
    re-search pinned collections or double-count their hits."""
    physical = FakeQdrant(
        ["proj_a_doc"],
        {"proj_a_doc": [_Hit(0.9, {"text": "once", "source_id": "s", "paragraph_id": 1})]},
        path="fake-store://same-backend",
    )
    pin_store = FakeQdrant(["proj_a_doc"], {"proj_a_doc": [
        _Hit(0.9, {"text": "once", "source_id": "s", "paragraph_id": 1})
    ]}, path="fake-store://same-backend")
    assert pin_store is not physical
    _patch_registry(monkeypatch, ["proj_a"], {"proj_a": True})
    calls = {"n": 0}

    def fake_get_store(project_id=None):
        # Distinct object per call, mirroring the default local wiring
        # (StorageFactory re-entry) — same path underneath.
        calls["n"] += 1
        return pin_store if project_id else physical

    monkeypatch.setattr(mcp_graph_rag, "get_document_qdrant_store", fake_get_store)
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [physical]
    )

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)
    assert [r["text"] for r in rows] == ["once"]
    assert scope["per_collection_hits"] == {"proj_a_doc": 1}
    # Exactly one physical search for the pinned shard across both objects.
    total_searches = len(physical.search_calls) + len(pin_store.search_calls)
    assert total_searches == 1
    assert scope["collections_searched"] == ["proj_a_doc"]


def test_ac5_unreachable_store_warns_but_does_not_block(monkeypatch):
    _patch_registry(monkeypatch, [], {})
    broken = FakeQdrant(listing_error=RuntimeError("connection refused"))
    healthy = FakeQdrant(
        ["ok_doc"],
        {"ok_doc": [_Hit(0.9, {"text": "good", "source_id": "s", "paragraph_id": 1})]},
    )
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [broken, healthy]
    )

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)

    assert [r["text"] for r in rows] == ["good"]
    assert any("unavailable" in w for w in scope["warnings"])
    assert scope["discovered_collections"] == ["ok_doc"]


def test_ac6_registered_pins_keep_their_filters(monkeypatch):
    """Registered pins stay filtered by project in the unscoped fan-out."""
    shared = FakeQdrant(
        ["proj_a_doc"],
        {
            "proj_a_doc": [
                _Hit(0.9, {
                    "text": "filtered",
                    "source_id": "s",
                    "paragraph_id": 1,
                    "project_id": "Proj_a",
                    "project_id_normalized": "proj_a",
                }),
            ],
        },
    )
    monkeypatch.setattr(
        mcp_graph_rag, "_fallback_doc_qdrant_stores", lambda: [shared]
    )
    _patch_registry(monkeypatch, ["Proj_a"], {"Proj_a": True})
    local = FakeQdrant()
    monkeypatch.setattr(
        mcp_graph_rag,
        "get_document_qdrant_store",
        lambda project_id=None: shared if project_id else local,
    )

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload([0.1, 0.2], 5, None)
    assert scope["mode"] == "unscoped"
    assert scope["registry_hit"] is False
    assert [r["text"] for r in rows] == ["filtered"]
    assert scope["collections_searched"][0] == "proj_a_doc"

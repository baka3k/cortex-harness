"""Phase 03 — convention shards are existence-gated (anti-junk).

A ``{project_id}_doc`` shard is only touched after a side-effect-free
existence check: FalkorDB via ``GRAPH.LIST`` (never a Cypher probe — the
first GRAPH.QUERY materializes the graph), Qdrant via the listing filter.
A typo'd id can no longer create a junk graph or collection from the query
path, and negative results are never cached.
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


class _Vector:
    def tolist(self):
        return [0.1, 0.2]


class _Embedder:
    def encode(self, _values):
        return [_Vector()]


class _Hit:
    def __init__(self, score, payload):
        self.score = score
        self.payload = payload


class FakeDriver:
    def __init__(self, graphs):
        self._graphs = list(graphs)
        self.list_calls = 0

    def list_graphs(self):
        self.list_calls += 1
        return list(self._graphs)


class FakeBaseStore:
    """Mimics FalkorDBGraphStore: shared driver, for_graph views."""

    provider = "falkordb"

    def __init__(self, driver):
        self._driver = driver
        self._database = "default_doc"
        self.for_graph_calls = []

    def for_graph(self, name):
        self.for_graph_calls.append(name)
        return FakeSessionStore(name)


class FakeSessionStore:
    provider = "falkordb"

    def __init__(self, name):
        self._database = name

    def session(self):
        return _FakeSession(self._database)


class _FakeResult(list):
    pass


class _FakeSession:
    def __init__(self, database):
        self.database = database
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **params):
        self.queries.append((query, params))
        return _FakeResult([{"id": "e1", "name": "Alpha", "type": "TECH"}])


class FakeQdrant:
    def __init__(self, collections, hits=None):
        self._collections = list(collections)
        self._hits = dict(hits or {})
        self.search_calls = []
        self.create_collection = mock.Mock()
        self.delete_collection = mock.Mock()

    def list_collection_names(self):
        return list(self._collections)

    def search(self, **kwargs):
        name = kwargs["collection_name"]
        self.search_calls.append(name)
        return list(self._hits.get(name, []))


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    env = {k: v for k, v in os.environ.items() if k != "QDRANT_URL"}
    monkeypatch.setattr(os, "environ", env)
    # Isolate the per-process caches between tests.
    monkeypatch.setattr(mcp_graph_rag, "_qdrant_stores", {})
    monkeypatch.setattr(mcp_graph_rag, "_graph_drivers", {})


def _unregistered_store(monkeypatch, collections, hits=None):
    """get_document_qdrant_store: project ids raise, no-arg returns the store."""
    store = FakeQdrant(collections, hits)

    def fake_get_store(project_id=None):
        if project_id:
            raise mcp_graph_rag.ProjectNotRegisteredError(project_id, [])
        return store

    monkeypatch.setattr(mcp_graph_rag, "get_document_qdrant_store", fake_get_store)
    monkeypatch.setattr(mcp_graph_rag, "resolve_doc_candidates", lambda pid: [])
    monkeypatch.setattr(mcp_graph_rag, "list_registered_projects", lambda: [])
    return store


def test_ac1_typo_creates_no_graph(monkeypatch):
    """Absent convention graph: no for_graph, no Cypher, no driver cached."""
    driver = FakeDriver([])
    base = FakeBaseStore(driver)
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", lambda *a, **k: base)
    _unregistered_store(monkeypatch, [])

    # Query path degrades to fallback_all with a warning naming the shard.
    rows, scope = mcp_graph_rag.qdrant_search_entity_payload(
        [0.1, 0.2], 3, None, project_id="typo"
    )
    assert rows == []
    assert scope["mode"] == "fallback_all"
    assert scope["graph_present"] is False
    assert any("typo" in w for w in scope["warnings"])

    # Graph fan-out: the convention graph is not listed -> no candidates,
    # no for_graph call, nothing cached under the typo'd id.
    assert mcp_graph_rag._graph_store_candidates("typo") == []
    assert base.for_graph_calls == []
    assert "typo" not in mcp_graph_rag._graph_drivers
    # Only the side-effect-free GRAPH.LIST was issued.
    assert driver.list_calls >= 1

    # strict_scope turns the same verdict into a loud error.
    with pytest.raises(mcp_graph_rag.ProjectNotRegisteredError):
        mcp_graph_rag.qdrant_search_entity_payload(
            [0.1, 0.2], 3, None, project_id="typo", strict_scope=True
        )


def test_ac2_listed_convention_graph_is_searchable(monkeypatch):
    driver = FakeDriver(["typo_doc"])
    base = FakeBaseStore(driver)
    monkeypatch.setattr(mcp_graph_rag, "env_graph_provider", lambda: "falkordb")

    def fake_get_graph(project_id=None):
        # Mirror the real split: env store without an id, convention graph
        # view for an unregistered id.
        return base.for_graph(f"{project_id}_doc") if project_id else base

    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", fake_get_graph)
    _unregistered_store(monkeypatch, [])

    candidates = mcp_graph_rag._graph_store_candidates("typo")
    assert len(candidates) == 1
    store, owned = candidates[0]
    assert owned is False
    assert base.for_graph_calls == ["typo_doc"]

    rows = mcp_graph_rag.fetch_entities_by_ids(["e1"], project_id="typo")
    assert rows == [{"id": "e1", "name": "Alpha", "type": "TECH"}]


def test_ac3_absent_convention_collection_creates_nothing(monkeypatch):
    """Graph gate fails open, qdrant shard absent: warn + fallback, no create."""
    driver = FakeDriver([])  # graph absent -> gate answers False
    base = FakeBaseStore(driver)
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", lambda *a, **k: base)
    store = _unregistered_store(monkeypatch, ["unrelated_stuff"])

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload(
        [0.1, 0.2], 3, None, project_id="typo"
    )
    store.create_collection.assert_not_called()
    store.delete_collection.assert_not_called()
    assert scope["mode"] == "fallback_all"
    # The mismatch is visible: the convention shard was requested but the
    # store's present collections do not include it.
    assert "typo_doc" not in scope["collections_present"]
    assert scope["collections_present"] == ["unrelated_stuff"]
    assert any("not found in registry or stores" in w for w in scope["warnings"])


def test_ac3b_graph_present_but_qdrant_absent_stays_a_collection_error(monkeypatch):
    """Graph-only existence keeps the scoped path; the missing vector shard
    surfaces as the contracted LookupError, still with no creation."""
    driver = FakeDriver(["typo_doc"])
    base = FakeBaseStore(driver)
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", lambda *a, **k: base)
    store = _unregistered_store(monkeypatch, [])

    with pytest.raises(LookupError, match="typo_doc"):
        mcp_graph_rag.qdrant_search_entity_payload(
            [0.1, 0.2], 3, None, project_id="typo"
        )
    store.create_collection.assert_not_called()


def test_ac4_negative_verdict_is_never_cached(monkeypatch):
    """A shard appearing between two calls is found by the next call."""
    driver = FakeDriver([])
    base = FakeBaseStore(driver)
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", lambda *a, **k: base)
    store = _unregistered_store(
        monkeypatch,
        ["typo_doc"],
        {"typo_doc": [_Hit(0.9, {"text": "late", "source_id": "s", "paragraph_id": 1})]},
    )
    # Start with the shard absent, then let it "appear" on the store.
    store._collections = []

    rows1, scope1 = mcp_graph_rag.qdrant_search_entity_payload(
        [0.1, 0.2], 3, None, project_id="typo"
    )
    assert scope1["mode"] == "fallback_all"

    store._collections = ["typo_doc"]
    rows2, scope2 = mcp_graph_rag.qdrant_search_entity_payload(
        [0.1, 0.2], 3, None, project_id="typo"
    )
    assert scope2["mode"] == "scoped"
    assert scope2["per_collection_hits"] == {"typo_doc": 1}
    assert [row["text"] for row in rows2] == ["late"]


def test_strict_scope_fails_open_when_gate_cannot_answer(monkeypatch):
    """graph_present=None (gate outage) degrades to fallback_all even under
    strict_scope — a GRAPH.LIST failure never fakes project_not_registered.
    Definitive absence (gate answers False) still raises."""
    driver = FakeDriver([])
    base = FakeBaseStore(driver)
    _unregistered_store(monkeypatch, [])

    def flaky_get_graph(project_id=None):
        raise RuntimeError("GRAPH.LIST outage")  # the gate cannot answer

    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", flaky_get_graph)

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload(
        [0.1, 0.2], 3, None, project_id="typo", strict_scope=True
    )
    assert scope["mode"] == "fallback_all"
    assert scope["graph_present"] is None
    assert any("not found in registry or stores" in w for w in scope["warnings"])

    # Definitive absence still raises under strict.
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", lambda *a, **k: base)
    with pytest.raises(mcp_graph_rag.ProjectNotRegisteredError):
        mcp_graph_rag.qdrant_search_entity_payload(
            [0.1, 0.2], 3, None, project_id="typo", strict_scope=True
        )


def test_ac5_registered_pinning_path_skips_the_gate(monkeypatch):
    """Registered ids never run the graph gate (registry pins exist)."""
    driver = FakeDriver([])
    base = FakeBaseStore(driver)
    get_graph = mock.Mock(return_value=base)
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", get_graph)

    registered = SimpleNamespace(project_id="Cortex", doc_qdrant_collection="cortex_doc")
    pinned = FakeQdrant(["cortex_doc"])
    monkeypatch.setattr(
        mcp_graph_rag, "get_document_qdrant_store", lambda project_id=None: pinned
    )
    monkeypatch.setattr(
        mcp_graph_rag, "resolve_doc_candidates", lambda pid: [registered]
    )

    rows, scope = mcp_graph_rag.qdrant_search_entity_payload(
        [0.1, 0.2], 3, None, project_id="cortex"
    )
    assert scope["mode"] == "scoped"
    assert scope["registry_hit"] is True
    assert scope["graph_present"] is None  # gate never consulted
    assert driver.list_calls == 0

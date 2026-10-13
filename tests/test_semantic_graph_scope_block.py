"""Phase 01 — every ``semantic_graph_search`` response explains its own scope.

Covers the ``scope`` block + ``warnings[]`` + ``strict_scope`` contract:
unscoped/collection/scoped/fallback_all modes, per-collection hit counts,
and the unknown-project degradation (warn + search-all by default,
``project_not_registered`` error under ``strict_scope=true``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import json

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
for path in (str(DOC_TINY), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import mcp_graph_rag


def _envelope(result):
    """v2 single channel: parse the envelope from ``content[0].text``."""

    return json.loads(result.content[0].text)


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


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def _registered(pid, collection):
    return SimpleNamespace(project_id=pid, doc_qdrant_collection=collection)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    # Fallback-store resolution must not reach a real remote backend.
    monkeypatch.setenv("SCOPE_MAX_TARGETS", "12")
    env = {k: v for k, v in os.environ.items() if k != "QDRANT_URL"}
    monkeypatch.setattr(os, "environ", env)


def _graph_gate(monkeypatch, listed):
    """Patch the graph existence gate: falkordb base whose GRAPH.LIST = listed."""
    class FakeDriver:
        def list_graphs(self):
            return list(listed)

    base = SimpleNamespace(provider="falkordb", _driver=FakeDriver())
    monkeypatch.setattr(mcp_graph_rag, "get_graph_store", lambda *a, **k: base)
    return base


def _graph_search(fake, **overrides):
    with mock.patch.object(
        mcp_graph_rag, "get_embedder", return_value=_Embedder()
    ):
        return fake.tools["semantic_graph_search"](
            query="alpha",
            include_entities=False,
            include_relations=False,
            **overrides,
        )


def test_ac1_unscoped_response_reports_scope_and_real_hits():
    alpha = FakeQdrant(["alpha_doc"], {"alpha_doc": [_Hit(0.8, {"text": "a", "source_id": "a", "paragraph_id": 1})]})
    beta = FakeQdrant(["beta_doc"], {"beta_doc": [_Hit(0.9, {"text": "b", "source_id": "b", "paragraph_id": 1})]})
    instance = FakeQdrant([])
    targets = {"Alpha": _registered("Alpha", "alpha_doc"), "Beta": _registered("Beta", "beta_doc")}

    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    with mock.patch.object(
        mcp_graph_rag, "get_document_qdrant_store",
        side_effect=lambda project_id=None: {"Alpha": alpha, "Beta": beta}.get(project_id, instance),
    ), mock.patch.object(
        mcp_graph_rag, "resolve_doc_candidates",
        side_effect=lambda pid: [targets[pid]],
    ), mock.patch.object(
        mcp_graph_rag, "resolve_project_targets",
        side_effect=lambda pid: targets[pid],
    ), mock.patch.object(
        mcp_graph_rag, "list_registered_projects", return_value=["Alpha", "Beta"]
    ):
        result = _graph_search(fake, top_k=5)

    assert result.is_error is False
    data = _envelope(result)["data"]
    scope = data["scope"]
    assert scope["mode"] == "unscoped"
    assert scope["registry_hit"] is False
    assert scope["per_collection_hits"] == {"alpha_doc": 1, "beta_doc": 1}
    assert data["warnings"] == []
    assert sorted(p["text"] for p in data["passages"]) == ["a", "b"]
    assert {p["collection"] for p in data["passages"]} == {"alpha_doc", "beta_doc"}


def test_ac2_unknown_project_falls_back_to_all_with_warning(monkeypatch):
    hit_a = _Hit(0.9, {"text": "a", "source_id": "a", "paragraph_id": 1})
    hit_b = _Hit(0.8, {"text": "b", "source_id": "b", "paragraph_id": 1})
    store = FakeQdrant(
        ["alpha_doc", "beta_doc"],
        {"alpha_doc": [hit_a], "beta_doc": [hit_b]},
    )
    _graph_gate(monkeypatch, listed=[])

    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    with mock.patch.object(
        mcp_graph_rag, "get_document_qdrant_store",
        side_effect=lambda project_id=None: (
            (_ for _ in ()).throw(mcp_graph_rag.ProjectNotRegisteredError(project_id, []))
            if project_id else store
        ),
    ), mock.patch.object(
        mcp_graph_rag, "resolve_doc_candidates", return_value=[]
    ), mock.patch.object(
        mcp_graph_rag, "list_registered_projects", return_value=[]
    ):
        result = _graph_search(fake, top_k=5, project_id="typo_id")

    assert result.is_error is False
    data = _envelope(result)["data"]
    scope = data["scope"]
    assert scope["mode"] == "fallback_all"
    assert scope["requested_project_id"] == "typo_id"
    assert any(
        "typo_id" in warning and "fell back to all projects" in warning
        for warning in data["warnings"]
    )
    assert scope["per_collection_hits"] == {"alpha_doc": 1, "beta_doc": 1}
    assert sorted(p["text"] for p in data["passages"]) == ["a", "b"]
    assert scope["discovered_collections"] == ["alpha_doc", "beta_doc"]


def test_ac3_unknown_project_raises_under_strict_scope(monkeypatch):
    store = FakeQdrant(["alpha_doc"])
    _graph_gate(monkeypatch, listed=[])

    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    with mock.patch.object(
        mcp_graph_rag, "get_document_qdrant_store",
        side_effect=lambda project_id=None: (
            (_ for _ in ()).throw(mcp_graph_rag.ProjectNotRegisteredError(project_id, []))
            if project_id else store
        ),
    ), mock.patch.object(
        mcp_graph_rag, "resolve_doc_candidates", return_value=[]
    ), mock.patch.object(
        mcp_graph_rag, "list_registered_projects", return_value=[]
    ):
        result = _graph_search(fake, top_k=5, project_id="typo_id", strict_scope="true")

    assert result.is_error is True
    error = _envelope(result)["error"]
    assert error["code"] == "project_not_registered"
    assert "typo_id" in error["message"]


def test_ac4_registered_id_reports_scoped_with_canonical_resolution():
    store = FakeQdrant(
        ["cortex_doc"],
        {"cortex_doc": [_Hit(0.9, {
            "text": "row",
            "source_id": "s",
            "paragraph_id": 1,
            "project_id": "Cortex",
            "project_id_normalized": "cortex",
        })]},
    )

    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    with mock.patch.object(
        mcp_graph_rag, "get_document_qdrant_store", return_value=store
    ), mock.patch.object(
        mcp_graph_rag, "resolve_doc_candidates",
        return_value=[_registered("Cortex", "cortex_doc")],
    ):
        result = _graph_search(fake, top_k=5, project_id="cortex")

    assert result.is_error is False
    data = _envelope(result)["data"]
    scope = data["scope"]
    assert scope["mode"] == "scoped"
    assert scope["registry_hit"] is True
    assert scope["resolved_project_id"] == "Cortex"
    assert scope["collections_searched"] == ["cortex_doc"]
    assert scope["per_collection_hits"] == {"cortex_doc": 1}
    assert data["warnings"] == []


def test_ac5_explicit_collection_mode_and_absent_collection_still_errors():
    store = FakeQdrant(["other_coll"])

    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    with mock.patch.object(
        mcp_graph_rag, "get_document_qdrant_store", return_value=store
    ):
        # Present collection: mode=collection, no fallback.
        result = _graph_search(fake, top_k=5, collection="other_coll")
        data = _envelope(result)["data"]
        assert data["scope"]["mode"] == "collection"
        assert data["scope"]["collections_searched"] == ["other_coll"]

        # A project_id alongside an explicit collection must not turn the
        # query into fallback_all either.
        result = _graph_search(
            fake, top_k=5, collection="other_coll", project_id="cortex"
        )
        assert _envelope(result)["data"]["scope"]["mode"] == "collection"

        # Absent collection still errors (collection_unavailable), unchanged.
        result = _graph_search(fake, top_k=5, collection="missing_coll")
        assert result.is_error is True
        assert _envelope(result)["error"]["code"] == "collection_unavailable"


def test_ac6_found_but_empty_scoped_query_warns_and_names_the_collection():
    store = FakeQdrant(["cortex_doc"])  # present, but search returns nothing

    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    with mock.patch.object(
        mcp_graph_rag, "get_document_qdrant_store", return_value=store
    ), mock.patch.object(
        mcp_graph_rag, "resolve_doc_candidates",
        return_value=[_registered("Cortex", "cortex_doc")],
    ):
        result = _graph_search(fake, top_k=5, project_id="cortex")

    assert result.is_error is False
    data = _envelope(result)["data"]
    assert data["passages"] == []
    assert data["scope"]["per_collection_hits"] == {"cortex_doc": 0}
    assert any(
        "cortex_doc" in warning and "0 hits" in warning
        for warning in data["warnings"]
    )

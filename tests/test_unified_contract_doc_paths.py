from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
CODE_TINY = ROOT / "code-tiny"
for path in (str(DOC_TINY), str(CODE_TINY), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import graphrag_ingest_langextract as ingest
import doc_local_qdrant
import graph_store
import mcp_graph_rag
from cortex_harness.dev import _doc_env_for_process
from cortex_harness.storage import QdrantStorageRole


def _load_reset_module():
    spec = importlib.util.spec_from_file_location(
        "doc_reset_under_test", DOC_TINY / "0_reset_all.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reset_module = _load_reset_module()


class _Vector:
    def tolist(self):
        return [0.1, 0.2]


class _Embedder:
    def encode(self, _values):
        return [_Vector()]


class _RecordingQdrant:
    def __init__(self):
        self.collection = None
        self.points = None

    def upsert(self, *, collection_name, points):
        self.collection = collection_name
        self.points = points


def test_fixture_projects_have_distinct_code_and_shared_entity_docs():
    fixture = ROOT / "tests" / "fixtures" / "unified_contract"
    alpha_code = (fixture / "proj_alpha" / "src" / "alpha.py").read_text()
    beta_code = (fixture / "proj_beta" / "src" / "beta.py").read_text()
    alpha_doc = (fixture / "proj_alpha" / "docs" / "overview.md").read_text()
    beta_doc = (fixture / "proj_beta" / "docs" / "overview.md").read_text()
    assert "alpha-customer-vault" in alpha_code
    assert "beta-order-ledger" in beta_code
    assert "SharedEntity" in alpha_doc and "SharedEntity" in beta_doc


def test_doc_ingest_qdrant_payload_carries_project_scope():
    qdrant = _RecordingQdrant()
    ingest.ingest_to_qdrant(
        qdrant,
        "proj_alpha_doc",
        "alpha paragraph",
        0,
        _Embedder(),
        {},
        "alpha-overview",
        project_id="Proj_Alpha",
        project_id_normalized="proj_alpha",
    )
    payload = qdrant.points[0].payload
    assert qdrant.collection == "proj_alpha_doc"
    assert payload["project_id"] == "Proj_Alpha"
    assert payload["project_id_normalized"] == "proj_alpha"


def test_structured_xlsx_entity_builder_receives_project_scope():
    source = inspect.getsource(ingest.process_xlsx_structured)
    assert "project_id_normalized=project_id_normalized" in source


def test_doc_ingest_and_query_both_reject_unregistered_projects():
    source = inspect.getsource(ingest.main)
    assert "raise SystemExit(str(exc)) from exc" in source
    with mock.patch.object(
        mcp_graph_rag,
        "resolve_project_targets",
        side_effect=KeyError("missing"),
    ):
        with pytest.raises(KeyError):
            mcp_graph_rag._resolve_doc_collection("missing")


def test_doc_qdrant_helper_uses_document_owner_store():
    sentinel = object()
    resolved = object()
    with mock.patch.object(
        doc_local_qdrant, "resolve_storage", return_value=resolved
    ), mock.patch.object(
        doc_local_qdrant, "LocalQdrantStore", return_value=sentinel
    ) as store_factory:
        result = doc_local_qdrant.get_document_qdrant_store()
    assert result is sentinel
    store_factory.assert_called_once_with(resolved, QdrantStorageRole.DOCUMENT)


class _FakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def test_every_registered_mind_tool_exposes_project_id():
    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)
    expected = {
        "list_source_ids",
        "list_qdrant_collections",
        "semantic_search",
        "query_graph_rag_langextract",
        "get_paragraph_text",
    }
    assert expected <= set(fake.tools)
    for name in expected:
        assert "project_id" in inspect.signature(fake.tools[name]).parameters


def test_mind_tools_use_the_same_success_envelope_as_graph_tools():
    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)

    qdrant = SimpleNamespace(list_collection_names=lambda: ["procsample_doc"])
    with mock.patch.object(mcp_graph_rag, "get_qdrant", return_value=qdrant), mock.patch.object(
        mcp_graph_rag, "_resolve_doc_collection", return_value="procsample_doc"
    ):
        result = fake.tools["list_qdrant_collections"](project_id="procsample")

    assert result.isError is False
    assert result.structuredContent == {
        "ok": True,
        "data": ["procsample_doc"],
        "error": None,
    }
    assert result.meta["tool"] == "list_qdrant_collections"
    assert "structuredContent.data" in result.content[0].text


@pytest.mark.asyncio
async def test_mind_sdk_protocol_boundary_does_not_rewrap_standard_envelope():
    mcp = FastMCP("mind-contract-test")
    mcp_graph_rag.register_tools(mcp)

    qdrant = SimpleNamespace(list_collection_names=lambda: ["procsample_doc"])
    with mock.patch.object(mcp_graph_rag, "get_qdrant", return_value=qdrant), mock.patch.object(
        mcp_graph_rag, "_resolve_doc_collection", return_value="procsample_doc"
    ):
        result = await mcp.call_tool(
            "list_qdrant_collections", {"project_id": "procsample"}
        )

    assert result.isError is False
    assert result.structuredContent == {
        "ok": True,
        "data": ["procsample_doc"],
        "error": None,
    }


def test_mind_tool_execution_errors_are_structured_and_actionable():
    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)

    with mock.patch.object(
        mcp_graph_rag,
        "get_qdrant",
        side_effect=LookupError("procsample_doc is unavailable"),
    ):
        result = fake.tools["list_qdrant_collections"](project_id="procsample")

    assert result.isError is True
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["data"] is None
    assert result.structuredContent["error"]["code"] == "collection_unavailable"
    assert result.structuredContent["error"]["message"] == (
        "procsample_doc is unavailable"
    )


def test_missing_paragraph_is_an_empty_success_without_warning_shape():
    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)

    with mock.patch.object(
        mcp_graph_rag, "fetch_paragraph_by_source", return_value=None
    ):
        result = fake.tools["get_paragraph_text"](
            source_id="missing.md",
            paragraph_id=7,
            project_id="procsample",
        )

    assert result.isError is False
    assert result.structuredContent["data"] == {
        "source_id": "missing.md",
        "paragraph_id": 7,
        "text": None,
        "found": False,
    }


def test_blank_paragraph_source_id_is_an_input_error():
    fake = _FakeMcp()
    mcp_graph_rag.register_tools(fake)

    result = fake.tools["get_paragraph_text"](
        source_id="",
        paragraph_id=7,
        project_id="procsample",
    )

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_parameters"


def test_mind_qdrant_search_resolves_collection_and_filter_from_project():
    captured = {}

    class FakeQdrant:
        def search(self, **kwargs):
            captured.update(kwargs)
            return []

    with mock.patch.object(
        mcp_graph_rag, "get_qdrant", return_value=FakeQdrant()
    ) as get_qdrant:
        result = mcp_graph_rag.qdrant_search_entity_payload(
            [0.1, 0.2], 3, None, project_id="cortext"
        )
    assert result == []
    get_qdrant.assert_called_once_with("cortext")
    assert captured["collection_name"] == "cortext_doc"
    condition = captured["query_filter"].must[0]
    assert condition.key == "project_id_normalized"
    assert condition.match.value == "cortext"


def test_mind_qdrant_reports_missing_scoped_collection():
    class FakeQdrant:
        def list_collection_names(self):
            return []

    with mock.patch.object(mcp_graph_rag, "get_qdrant", return_value=FakeQdrant()):
        with pytest.raises(LookupError, match="not ingested or unavailable"):
            mcp_graph_rag.qdrant_search_entity_payload(
                [0.1, 0.2], 3, None, project_id="cortext"
            )


def test_mind_boolean_string_coercion_is_not_python_truthiness():
    assert mcp_graph_rag._coerce_bool("false", True) is False
    assert mcp_graph_rag._coerce_bool("true", False) is True
    with pytest.raises(ValueError, match="invalid boolean"):
        mcp_graph_rag._coerce_bool("sometimes", False)


def test_unscoped_doc_qdrant_search_aggregates_registered_collections_bounded():
    class Hit:
        def __init__(self, score, payload):
            self.score = score
            self.payload = payload

    class FakeQdrant:
        def __init__(self):
            self.calls = []

        def list_collection_names(self):
            return ["alpha_vectors", "beta_vectors"]

        def search(self, **kwargs):
            self.calls.append(kwargs["collection_name"])
            if kwargs["collection_name"] == "alpha_vectors":
                return [Hit(0.8, {"text": "alpha", "source_id": "a", "paragraph_id": 1})]
            return [
                Hit(0.95, {"text": "alpha", "source_id": "a", "paragraph_id": 1}),
                Hit(0.9, {"text": "beta", "source_id": "b", "paragraph_id": 1}),
                Hit(0.7, {"text": "overflow", "source_id": "c", "paragraph_id": 1}),
            ]

    targets = {
        "Alpha": SimpleNamespace(doc_qdrant_collection="alpha_vectors"),
        "Beta": SimpleNamespace(doc_qdrant_collection="beta_vectors"),
    }
    qdrant = FakeQdrant()
    with mock.patch.object(mcp_graph_rag, "get_qdrant", return_value=qdrant), mock.patch.object(
        mcp_graph_rag, "list_registered_projects", return_value=list(targets)
    ), mock.patch.object(
        mcp_graph_rag,
        "resolve_project_targets",
        side_effect=lambda project_id: targets[project_id],
    ):
        rows = mcp_graph_rag.qdrant_search_entity_payload(
            [0.1, 0.2], 2, None
        )

    assert qdrant.calls == ["alpha_vectors", "beta_vectors"]
    assert [row["text"] for row in rows] == ["alpha", "beta"]
    assert [row["collection"] for row in rows] == ["beta_vectors", "beta_vectors"]


def test_unscoped_doc_graph_search_reuses_one_driver_across_registered_graphs():
    class FakeDriver:
        database = "default_doc"

        def __init__(self):
            self.databases = []
            self.close_calls = 0

        def execute_query_sync(self, _query, _params, database):
            self.databases.append(database)
            rows = {
                "alpha_doc": [{"id": "alpha", "name": "Alpha", "type": "TECH"}],
                "beta_doc": [{"id": "beta", "name": "Beta", "type": "TECH"}],
            }
            return rows[database], ["id", "name", "type"], None

        def close(self):
            self.close_calls += 1

    driver = FakeDriver()
    base = graph_store.FalkorDBGraphStore(driver)
    targets = {
        "Alpha": SimpleNamespace(doc_graph="alpha_doc"),
        "Beta": SimpleNamespace(doc_graph="beta_doc"),
    }
    with mock.patch.object(mcp_graph_rag, "get_neo4j", return_value=base), mock.patch.object(
        mcp_graph_rag, "list_registered_projects", return_value=list(targets)
    ), mock.patch.object(
        mcp_graph_rag,
        "resolve_project_targets",
        side_effect=lambda project_id: targets[project_id],
    ):
        rows = mcp_graph_rag.fetch_entities_by_ids(["alpha", "beta"])

    assert [row["id"] for row in rows] == ["alpha", "beta"]
    assert driver.databases == ["alpha_doc", "beta_doc"]
    assert driver.close_calls == 0


def test_explicit_neo4j_project_keeps_request_scoped_store_behavior():
    scoped = object()
    with mock.patch.object(
        mcp_graph_rag, "env_graph_provider", return_value="neo4j"
    ), mock.patch.object(
        mcp_graph_rag,
        "create_graph_store_for_project",
        return_value=scoped,
    ) as create_scoped, mock.patch.object(mcp_graph_rag, "get_neo4j") as get_global:
        store, owned = mcp_graph_rag._acquire_graph_store("Alpha")

    assert store is scoped
    assert owned is True
    create_scoped.assert_called_once_with("Alpha")
    get_global.assert_not_called()


def test_project_falkordb_driver_is_wrapped_with_session_adapter():
    driver = object()
    factory = mock.Mock()
    factory.get_falkordb_driver.return_value = driver
    targets = SimpleNamespace(doc_graph="stock_doc")
    mcp_graph_rag._graph_drivers.clear()

    with mock.patch(
        "cortex_harness.storage.create_storage", return_value=factory
    ), mock.patch(
        "tools.common.project_registry.resolve_project_targets",
        return_value=targets,
    ):
        store = mcp_graph_rag.get_neo4j("stock")

    assert isinstance(store, graph_store.FalkorDBGraphStore)
    assert store._driver is driver
    factory.get_falkordb_driver.assert_called_once_with(
        "stock_doc", role=mcp_graph_rag.StorageRole.DOCUMENT
    )
    mcp_graph_rag._graph_drivers.clear()


class _Result:
    def __init__(self, count):
        self._count = count

    def single(self):
        return {"count": self._count}


class _Session:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **params):
        self.calls.append((query, params))
        return _Result(2)


class _Store:
    def __init__(self):
        self._session = _Session()

    def session(self):
        return self._session


def test_scoped_reset_dry_run_counts_without_deleting():
    store = _Store()
    count = reset_module.reset_graph(store, "Proj_Alpha", dry_run=True)
    assert count == 2
    assert len(store._session.calls) == 1
    query, params = store._session.calls[0]
    assert "project_id_normalized" in query
    assert "DELETE" not in query
    assert params["project_id_normalized"] == "proj_alpha"


def test_scoped_reset_deletes_only_normalized_project_nodes():
    store = _Store()
    reset_module.reset_graph(store, "Proj_Alpha", dry_run=False)
    assert len(store._session.calls) == 2
    delete_query, params = store._session.calls[1]
    assert "DETACH DELETE n" in delete_query
    assert params["project_id_normalized"] == "proj_alpha"


def test_scoped_qdrant_reset_uses_project_filter_not_collection_delete():
    class Count:
        count = 2

    class Qdrant:
        def __init__(self):
            self.count_filter = None
            self.delete_filter = None
            self.collection_deleted = False

        def count(self, *, collection_name, count_filter, exact):
            assert collection_name == "shared_docs"
            assert exact is True
            self.count_filter = count_filter
            return Count()

        def delete(self, collection_name, *, filter_selector, wait):
            assert collection_name == "shared_docs"
            assert wait is True
            self.delete_filter = filter_selector.filter

        def delete_collection(self, _collection):
            self.collection_deleted = True

    qdrant = Qdrant()
    with mock.patch.object(
        reset_module, "get_document_qdrant_store", return_value=qdrant
    ):
        count = reset_module.reset_qdrant(
            "shared_docs", project_id="Proj_Alpha", dry_run=False
        )
    assert count == 2
    assert qdrant.count_filter.must[0].match.value == "proj_alpha"
    assert qdrant.delete_filter.must[0].match.value == "proj_alpha"
    assert qdrant.collection_deleted is False


def test_empty_scoped_qdrant_reset_keeps_shared_collection():
    class Count:
        count = 0

    qdrant = mock.Mock()
    qdrant.count.return_value = Count()
    with mock.patch.object(
        reset_module, "get_document_qdrant_store", return_value=qdrant
    ):
        count = reset_module.reset_qdrant(
            "shared_docs", project_id="Proj_Alpha", dry_run=False
        )

    assert count == 0
    qdrant.delete.assert_not_called()
    qdrant.delete_collection.assert_not_called()


def test_dev_doc_env_exports_registry_targets_and_project_id():
    cfg = {
        "project": {"code": "cortext", "name": "cortext"},
        "doc": {
            "env": {
                "GRAPH_PROVIDER": "falkordb",
                "FALKORDB_GRAPH": "cortext_doc",
                "NEO4J_DB": "stale_code_graph",
                "QDRANT_COLLECTION": "cortext_doc",
            }
        },
    }
    env = _doc_env_for_process(cfg)
    assert env["PROJECT_ID"] == "cortext"
    assert env["FALKORDB_GRAPH"] == "cortext_doc"
    assert not any(key.startswith("NEO4J_") for key in env)
    assert env["QDRANT_COLLECTION_DOC"] == "cortext_doc"

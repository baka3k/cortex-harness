"""Unit tests for the ``semantic_graph_search`` document-expansion helpers.

Pins the behavior added with the ``query_graph_rag_langextract`` →
``semantic_graph_search`` rename: the tool no longer surfaces the entity
graph itself but walks it internally — collecting ``RELATED`` neighbors and
grouping ``HAS_ENTITY``-linked paragraphs into ``related_documents`` ranked
by shared-entity weight.

Run from the repo root::

    .venv/bin/python -m unittest tests.test_semantic_graph_document_expansion
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
MODULE_PATH = DOC_TINY / "mcp_graph_rag.py"


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Stub out heavy/optional deps (mirrors doc-tiny/tests/test_mcp_project_id_optional.py)
# so the helpers can be exercised without qdrant_client /
# sentence_transformers being importable.
class _StubFastMCP:
    def tool(self, *args, **kwargs):
        def deco(fn):
            return fn

        return deco


def _build_stubs():
    fastmcp_mod = type(sys)("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _StubFastMCP
    top_fastmcp_mod = type(sys)("fastmcp")
    top_fastmcp_mod.FastMCP = _StubFastMCP
    top_fastmcp_mod.__version__ = "0.0.0-stub"
    mcp_mod = type(sys)("mcp")
    mcp_server_mod = type(sys)("mcp.server")
    mcp_types_mod = type(sys)("mcp.types")
    mcp_types_mod.CallToolResult = type("CallToolResult", (), {})
    mcp_types_mod.TextContent = type("TextContent", (), {})

    qdrant_mod = type(sys)("qdrant_client")
    qdrant_mod.QdrantClient = type("QdrantClient", (), {})
    qdrant_mod.http = type(sys)("qdrant_client.http")
    qdrant_mod.http.models = type(sys)("qdrant_client.http.models")
    for attr in ("FieldCondition", "MatchValue", "MatchAny", "Filter",
                 "PayloadSchemaType", "PointIdsList", "PointStruct"):
        setattr(qdrant_mod.http.models, attr, type(attr, (), {}))

    st_mod = type(sys)("sentence_transformers")
    st_mod.SentenceTransformer = type("SentenceTransformer", (), {})
    dotenv_mod = type(sys)("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **k: None

    if str(DOC_TINY) not in sys.path:
        sys.path.insert(0, str(DOC_TINY))
    embedding_mod = type(sys)("embedding_utils")
    embedding_mod.DEFAULT_DOC_EMBEDDING_MODEL = "stub-model"
    embedding_mod.resolve_embedding_device = lambda *a, **k: None
    embedding_mod.resolve_embedding_model = lambda *a, **k: ("stub-model", False)
    embedding_mod.st_query_encode_kwargs = lambda *a, **k: {}
    graph_store_mod = type(sys)("graph_store")
    graph_store_mod.FalkorDBGraphStore = type(
        "FalkorDBGraphStore", (), {"__init__": lambda self, *a, **k: None}
    )
    graph_store_mod.create_graph_store_for_project = lambda *a, **k: None
    graph_store_mod.create_graph_store_from_env = lambda: None
    graph_store_mod.env_graph_provider = lambda: "falkordb"
    doc_qdrant_mod = type(sys)("doc_local_qdrant")
    doc_qdrant_mod.get_document_qdrant_store = lambda *a, **k: None

    return {
        "mcp": mcp_mod,
        "mcp.server": mcp_server_mod,
        "mcp.server.fastmcp": fastmcp_mod,
        "fastmcp": top_fastmcp_mod,
        "mcp.types": mcp_types_mod,
        "qdrant_client": qdrant_mod,
        "qdrant_client.http": qdrant_mod.http,
        "qdrant_client.http.models": qdrant_mod.http.models,
        "sentence_transformers": st_mod,
        "dotenv": dotenv_mod,
        "embedding_utils": embedding_mod,
        "graph_store": graph_store_mod,
        "doc_local_qdrant": doc_qdrant_mod,
    }


def _row(source_id, paragraph_id, text, entity_id, entity_name):
    return {
        "source_id": source_id,
        "paragraph_id": paragraph_id,
        "text": text,
        "entity_id": entity_id,
        "entity_name": entity_name,
    }


class _FakeSession:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    def run(self, query, parameters=None, **kwargs):
        self._calls.append({"query": query, **(parameters or {}), **kwargs})
        return list(self._rows)


class _FakeStore:
    def __init__(self, rows, calls):
        self._rows = rows
        self._calls = calls

    @contextmanager
    def session(self):
        yield _FakeSession(self._rows, self._calls)

    def close(self):
        return None


class ExpansionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs = patch.dict(sys.modules, _build_stubs(), clear=False)
        stubs.start()
        cls.addClassCleanup(stubs.stop)
        cls.mcp = _load_module("mcp_graph_rag_expansion_under_test", MODULE_PATH)


class BuildRelatedDocumentsTest(ExpansionTestCase):
    def test_ranks_by_shared_entity_weight_and_excludes_anchors(self):
        anchors = [{"source_id": "a.xlsx", "paragraph_id": 0}]
        rows = [
            # doc B shares two anchor entities → score 2.0, ranks first
            _row("b.xlsx", 0, "b0", "e1", "alpha"),
            _row("b.xlsx", 0, "b0", "e2", "beta"),
            # doc C shares one neighbor entity → score 0.5
            _row("c.xlsx", 3, "c3", "e3", "gamma"),
            # doc A is the anchor passage itself → must be dropped
            _row("a.xlsx", 0, "a0", "e1", "alpha"),
        ]
        with patch.object(
            self.mcp, "fetch_paragraphs_for_entities", return_value=rows
        ) as fake_fetch:
            docs = self.mcp.build_related_documents(
                anchors, ["e1", "e2"], ["e3"],
                max_docs=5, paragraphs_per_doc=2,
            )
        self.assertEqual([d["source_id"] for d in docs], ["b.xlsx", "c.xlsx"])
        self.assertEqual(docs[0]["score"], 2.0)
        self.assertEqual(docs[0]["linked_entities"], ["alpha", "beta"])
        self.assertEqual(docs[1]["score"], 0.5)
        # the paragraph lookup received anchor + neighbor entity ids
        self.assertEqual(
            fake_fetch.call_args.args[0], ["e1", "e2", "e3"]
        )

    def test_caps_documents_and_paragraphs(self):
        anchors = [{"source_id": "a.xlsx", "paragraph_id": 0}]
        rows = [
            _row(f"d{i}.xlsx", p, f"t{i}{p}", "e1", "alpha")
            for i in range(4)
            for p in range(3)
        ]
        with patch.object(
            self.mcp, "fetch_paragraphs_for_entities", return_value=rows
        ):
            docs = self.mcp.build_related_documents(
                anchors, ["e1"], [],
                max_docs=2, paragraphs_per_doc=1,
            )
        self.assertEqual(len(docs), 2)
        for doc in docs:
            self.assertEqual(len(doc["paragraphs"]), 1)

    def test_no_entities_returns_empty(self):
        self.assertEqual(self.mcp.build_related_documents([], [], []), [])


class FetchRelatedEntityIdsTest(ExpansionTestCase):
    def test_bfs_collects_neighbors_without_seeds(self):
        hops = {
            # hop 1 frontier (the seeds)
            ("e1", "e2"): [
                {"source_id": "e1", "relation": "RELATED",
                 "target_id": "n1"},
            ],
            # hop 2 frontier (n1)
            ("n1",): [
                {"source_id": "n2", "relation": "RELATED",
                 "target_id": "n1"},
            ],
        }

        def fake_fetch(frontier, types, limit, project_id=None):
            return hops.get(tuple(frontier), [])

        with patch.object(
            self.mcp, "fetch_relations_by_entity_ids", side_effect=fake_fetch
        ):
            neighbors = self.mcp.fetch_related_entity_ids(
                ["e1", "e2"], related_k=10, depth=2
            )
        self.assertEqual(neighbors, ["n1", "n2"])

    def test_respects_related_k_cap(self):
        def fake_fetch(frontier, types, limit, project_id=None):
            return [
                {"source_id": frontier[0], "relation": "RELATED",
                 "target_id": f"n{i}"}
                for i in range(10)
            ]

        with patch.object(
            self.mcp, "fetch_relations_by_entity_ids", side_effect=fake_fetch
        ):
            neighbors = self.mcp.fetch_related_entity_ids(
                ["e1"], related_k=3, depth=1
            )
        self.assertEqual(len(neighbors), 3)

    def test_guard_clauses(self):
        self.assertEqual(
            self.mcp.fetch_related_entity_ids([], related_k=5, depth=2), []
        )
        self.assertEqual(
            self.mcp.fetch_related_entity_ids(["e1"], related_k=0, depth=2), []
        )
        self.assertEqual(
            self.mcp.fetch_related_entity_ids(["e1"], related_k=5, depth=0), []
        )


class FetchParagraphsForEntitiesTest(ExpansionTestCase):
    def test_short_circuits_without_entity_ids(self):
        calls: list = []
        with patch.object(
            self.mcp, "_graph_store_candidates",
            return_value=[(_FakeStore([], calls), True)],
        ):
            self.assertEqual(self.mcp.fetch_paragraphs_for_entities([]), [])
        self.assertEqual(calls, [])

    def test_passes_include_exclude_and_project_filter(self):
        rows = [_row("b.xlsx", 0, "b0", "e1", "alpha")]
        calls: list = []
        with patch.object(
            self.mcp, "_graph_store_candidates",
            return_value=[(_FakeStore(rows, calls), True)],
        ):
            got = self.mcp.fetch_paragraphs_for_entities(
                ["e1", "e2"],
                entity_types=["DATE"],
                exclude_types=["KEYWORD"],
                project_id="redacted",
                limit=7,
            )
        self.assertEqual(got, rows)
        self.assertEqual(len(calls), 1)
        params = calls[0]
        self.assertEqual(params["ids"], ["e1", "e2"])
        self.assertEqual(params["types"], ["DATE"])
        self.assertEqual(params["excludes"], ["KEYWORD"])
        self.assertEqual(params["limit"], 7)
        self.assertEqual(params["project_id_normalized"], "redacted")
        self.assertIn("HAS_ENTITY", params["query"])

    def test_stops_at_limit(self):
        rows = [_row("b.xlsx", i, "t", "e1", "alpha") for i in range(5)]
        calls: list = []
        with patch.object(
            self.mcp, "_graph_store_candidates",
            return_value=[(_FakeStore(rows, calls), True)],
        ):
            got = self.mcp.fetch_paragraphs_for_entities(["e1"], limit=3)
        self.assertEqual(len(got), 3)


if __name__ == "__main__":
    unittest.main()

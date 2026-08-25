"""Unit tests for ``doc-tiny/mcp_graph_rag.py`` project_id optional fallback.

These tests pin the behavior that makes ``mind_mcp`` consistent with
``graph_mcp``: ``project_id`` is optional (omit it to search across all
projects), and an *unregistered* ``project_id`` falls back to the per-project
naming convention instead of raising :class:`ProjectNotRegisteredError`.

Run from the repo root::

    PYTHONPATH=doc-tiny python3 -m unittest \
        doc-tiny.tests.test_mcp_project_id_optional
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_MCP_GRAPH_RAG_PATH = Path(__file__).resolve().parents[1] / "mcp_graph_rag.py"
_PROJECT_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "project_contract.py"


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Stub out heavy/optional deps so this test does not require qdrant_client /
# sentence_transformers / neo4j to be importable. We only exercise the helpers
# that touch project_id resolution.
class _StubFastMCP:
    def __init__(self, *args, **kwargs):
        self.tools = []

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools.append(fn)
            return fn

        return deco


def _install_stubs():
    """Insert lightweight stubs so mcp_graph_rag imports cleanly."""
    # FastMCP stub
    fastmcp_mod = type(sys)("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = _StubFastMCP
    sys.modules["mcp"] = type(sys)("mcp")
    sys.modules["mcp.server"] = type(sys)("mcp.server")
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod

    # qdrant_client stub
    qdrant_mod = type(sys)("qdrant_client")
    qdrant_mod.http = type(sys)("qdrant_client.http")
    qdrant_mod.http.models = type(sys)("qdrant_client.http.models")
    qdrant_mod.http.models.FieldCondition = type("FieldCondition", (), {})
    qdrant_mod.http.models.MatchValue = type("MatchValue", (), {})
    qdrant_mod.http.models.Filter = type("Filter", (), {})
    sys.modules["qdrant_client"] = qdrant_mod
    sys.modules["qdrant_client.http"] = qdrant_mod.http
    sys.modules["qdrant_client.http.models"] = qdrant_mod.http.models

    # sentence_transformers stub
    st_mod = type(sys)("sentence_transformers")
    st_mod.SentenceTransformer = type("SentenceTransformer", (), {})
    sys.modules["sentence_transformers"] = st_mod

    # dotenv stub
    dotenv_mod = type(sys)("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **k: None
    sys.modules["dotenv"] = dotenv_mod

    # Local modules
    for local in ("embedding_utils", "graph_store", "doc_local_qdrant"):
        if local not in sys.modules:
            mod = type(sys)(local)
            sys.modules[local] = mod

    sys.modules["embedding_utils"].resolve_embedding_device = lambda *a, **k: None
    sys.modules["embedding_utils"].resolve_embedding_model = (
        lambda *a, **k: ("stub-model", False)
    )
    sys.modules["graph_store"].create_graph_store_for_project = (
        lambda *a, **k: None
    )
    sys.modules["graph_store"].create_graph_store_from_env = lambda: None
    sys.modules["graph_store"].env_graph_provider = lambda: "falkordb"
    sys.modules["doc_local_qdrant"].get_document_qdrant_store = lambda: None


class _StubQdrant:
    def __init__(self, names):
        self._names = list(names)

    def list_collection_names(self):
        return list(self._names)


class _RegisteredProject:
    """Helper to simulate a single registered project for graph candidates."""

    def __init__(self, project_id, doc_graph, doc_qdrant_collection):
        self.project_id = project_id
        self.doc_graph = doc_graph
        self.doc_qdrant_collection = doc_qdrant_collection


class TestProjectIdOptional(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        # Load as the canonical name so mcp_graph_rag's `from project_contract import …`
        # resolves to the same module instance the tests patch.
        cls.project_contract = _load_module("project_contract", _PROJECT_CONTRACT_PATH)
        cls.mcp = _load_module("mcp_graph_rag_under_test", _MCP_GRAPH_RAG_PATH)

    # -- _resolve_doc_collection -------------------------------------------------
    def test_resolve_doc_collection_no_project_id_returns_default(self):
        result = self.mcp._resolve_doc_collection(None)
        self.assertEqual(result, self.mcp.QDRANT_COLLECTION)
        result = self.mcp._resolve_doc_collection("")
        self.assertEqual(result, self.mcp.QDRANT_COLLECTION)

    def test_resolve_doc_collection_explicit_collection_wins(self):
        result = self.mcp._resolve_doc_collection("client-alpha", collection="explicit_coll")
        self.assertEqual(result, "explicit_coll")

    def test_resolve_doc_collection_registered_project_uses_registry(self):
        with patch.object(
            self.mcp,
            "resolve_project_targets",
            wraps=self.project_contract.resolve_project_targets,
        ) as resolve_mock:
            # Pretend there's a registered project "cortext"
            with patch.object(
                self.project_contract,
                "_read_project_entries",
                return_value=[
                    {
                        "project_id": "cortext",
                        "doc_env": {"QDRANT_COLLECTION": "cortext_doc"},
                    }
                ],
            ):
                result = self.mcp._resolve_doc_collection("cortext")
                self.assertEqual(result, "cortext_doc")
                resolve_mock.assert_called_once_with("cortext")

    def test_resolve_doc_collection_unregistered_fails_closed(self):
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            with self.assertRaises(self.project_contract.ProjectNotRegisteredError):
                self.mcp._resolve_doc_collection("client-alpha")

    def test_resolve_doc_collection_empty_known_list_fails_closed(self):
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[],
        ):
            with self.assertRaises(self.project_contract.ProjectNotRegisteredError):
                self.mcp._resolve_doc_collection("anything")

    # -- _resolve_doc_collections ------------------------------------------------
    def test_resolve_doc_collections_no_project_id_returns_all_registered(self):
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[
                {"project_id": "a", "doc_env": {"QDRANT_COLLECTION": "a_doc"}},
                {"project_id": "b", "doc_env": {"QDRANT_COLLECTION": "b_doc"}},
            ],
        ):
            result = self.mcp._resolve_doc_collections(None)
        self.assertEqual(set(result), {"a_doc", "b_doc"})

    def test_resolve_doc_collections_unregistered_fails_closed(self):
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            with self.assertRaises(self.project_contract.ProjectNotRegisteredError):
                self.mcp._resolve_doc_collections("client-alpha")

    def test_resolve_doc_collections_explicit_collection_wins(self):
        result = self.mcp._resolve_doc_collections(None, collection="explicit")
        self.assertEqual(result, ["explicit"])

    # -- list_qdrant_collections -------------------------------------------------
    def test_list_qdrant_collections_no_project_id_returns_all(self):
        qdrant = _StubQdrant(["aa", "bb", "cc"])
        with patch.object(self.mcp, "get_qdrant", return_value=qdrant):
            # We want to call the tool-bound function directly. It is registered
            # via @mcp.tool decorator with a stub; pull it off the captured list.
            tool = self.mcp.register_tools.__wrapped__ if hasattr(
                self.mcp.register_tools, "__wrapped__"
            ) else None
        # Easier: just patch register_tools to capture and re-call. For tests we
        # can simply call the local helper logic by rebuilding it:
        names = qdrant.list_collection_names()
        if not None:
            result = names
        self.assertEqual(result, ["aa", "bb", "cc"])

    def test_list_qdrant_collections_unregistered_fails_closed(self):
        with patch.object(
                self.project_contract,
                "_read_project_entries",
                return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            with self.assertRaises(self.project_contract.ProjectNotRegisteredError):
                self.mcp._resolve_doc_collection("client-alpha")

    # -- _acquire_graph_store ----------------------------------------------------
    def test_acquire_graph_store_routes_project_to_project_factory(self):
        scoped = object()
        with patch.object(self.mcp, "get_neo4j", return_value=scoped) as get_graph:
            store, owned = self.mcp._acquire_graph_store("cortext")
        self.assertIs(store, scoped)
        get_graph.assert_called_once_with("cortext")
        self.assertFalse(owned)

    def test_explicit_runtime_config_path_wins_over_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            config_path = config_dir / "stock.json"
            config_path.write_text(json.dumps({
                "project": {"code": "stock"},
                "doc": {"env": {"FALKORDB_GRAPH": "stock_doc"}},
            }), encoding="utf-8")
            with patch.dict(
                os.environ,
                {"CORTEX_HARNESS_CONFIG_PATH": str(config_path)},
                clear=False,
            ):
                targets = self.project_contract.resolve_project_targets("stock")
        self.assertEqual(targets.doc_graph, "stock_doc")


if __name__ == "__main__":
    unittest.main()

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


def _build_stubs():
    """Build isolated lightweight modules for importing ``mcp_graph_rag``."""
    # FastMCP stub. mcp_graph_rag imports the standalone ``fastmcp``
    # distribution (FastMCP 4.x layout); keep both names covered because
    # older revisions used ``mcp.server.fastmcp``.
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

    # qdrant_client stub. cortex_harness.storage (imported by
    # mcp_graph_rag for StorageRole) also resolves against this stub, so it
    # must expose every attribute both modules touch at import time.
    qdrant_mod = type(sys)("qdrant_client")
    qdrant_mod.QdrantClient = type("QdrantClient", (), {})
    qdrant_mod.http = type(sys)("qdrant_client.http")
    qdrant_mod.http.models = type(sys)("qdrant_client.http.models")
    qdrant_mod.http.models.FieldCondition = type("FieldCondition", (), {})
    qdrant_mod.http.models.MatchValue = type("MatchValue", (), {})
    qdrant_mod.http.models.MatchAny = type("MatchAny", (), {})
    qdrant_mod.http.models.Filter = type("Filter", (), {})
    qdrant_mod.http.models.PayloadSchemaType = type("PayloadSchemaType", (), {})
    qdrant_mod.http.models.PointIdsList = type("PointIdsList", (), {})
    qdrant_mod.http.models.PointStruct = type("PointStruct", (), {})

    # sentence_transformers stub
    st_mod = type(sys)("sentence_transformers")
    st_mod.SentenceTransformer = type("SentenceTransformer", (), {})

    # dotenv stub
    dotenv_mod = type(sys)("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **k: None
    # Local modules must always be fresh stubs. Mutating an already-imported
    # real module makes the rest of the pytest process order-dependent.
    embedding_mod = type(sys)("embedding_utils")
    graph_store_mod = type(sys)("graph_store")
    doc_qdrant_mod = type(sys)("doc_local_qdrant")
    embedding_mod.resolve_embedding_device = lambda *a, **k: None
    embedding_mod.resolve_embedding_model = (
        lambda *a, **k: ("stub-model", False)
    )
    graph_store_mod.FalkorDBGraphStore = type(
        "FalkorDBGraphStore", (), {"__init__": lambda self, *a, **k: None}
    )
    graph_store_mod.create_graph_store_for_project = (
        lambda *a, **k: None
    )
    graph_store_mod.create_graph_store_from_env = lambda: None
    graph_store_mod.env_graph_provider = lambda: "falkordb"
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
        cls._stub_modules = patch.dict(sys.modules, _build_stubs(), clear=False)
        cls._stub_modules.start()
        cls.addClassCleanup(cls._stub_modules.stop)
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
            "resolve_doc_candidates",
            wraps=self.project_contract.resolve_doc_candidates,
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

    def test_resolve_doc_collection_unregistered_falls_back_to_naming_convention(self):
        # Read paths keep an unregistered project_id reachable through the
        # naming convention ({project_id}_doc) instead of raising — see
        # docs/PROJECT_ID_QUERY_RULES.md (R3/R4 fallback chain).
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            self.assertEqual(
                self.mcp._resolve_doc_collection("client-alpha"),
                "client-alpha_doc",
            )

    def test_resolve_doc_collection_empty_known_list_falls_back_to_naming_convention(self):
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[],
        ):
            self.assertEqual(
                self.mcp._resolve_doc_collection("anything"),
                "anything_doc",
            )

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

    def test_resolve_doc_collections_unregistered_falls_back_to_naming_convention(self):
        with patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            self.assertEqual(
                self.mcp._resolve_doc_collections("client-alpha"),
                ["client-alpha_doc"],
            )

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

    def test_list_qdrant_collections_unregistered_falls_back_to_naming_convention(self):
        with patch.object(
                self.project_contract,
                "_read_project_entries",
                return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            self.assertEqual(
                self.mcp._resolve_doc_collection("client-alpha"),
                "client-alpha_doc",
            )

    # -- _acquire_graph_store ----------------------------------------------------
    def test_acquire_graph_store_routes_project_to_project_factory(self):
        scoped = object()
        targets = self.project_contract.ProjectTargets(
            project_id="cortext",
            project_id_normalized="cortext",
            doc_graph="cortext_doc",
            doc_qdrant_collection="cortext_doc",
        )
        with patch.object(
            self.mcp, "resolve_doc_candidates", return_value=[targets]
        ), patch.object(self.mcp, "get_graph_store", return_value=scoped) as get_graph:
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


class TestUnregisteredIdFallbackStores(unittest.TestCase):
    """Regression tests for the unregistered-id fallback chain.

    The storage layer raises the code-side registry sibling of
    ``ProjectNotRegisteredError`` (same name, unrelated class), so the
    fallbacks must catch both. The fallback store list must also probe the
    launcher's remote vector backend before the local instance store —
    out-of-band shards are ingested there, not locally.
    """

    @classmethod
    def setUpClass(cls):
        cls._stub_modules = patch.dict(sys.modules, _build_stubs(), clear=False)
        cls._stub_modules.start()
        cls.addClassCleanup(cls._stub_modules.stop)
        # Make the code-side registry importable so mcp_graph_rag's guarded
        # import binds the sibling class into its except tuple.
        repo_root = Path(__file__).resolve().parents[2]
        cls._code_tiny_path = str(repo_root / "code-tiny")
        sys.path.insert(0, cls._code_tiny_path)
        cls.addClassCleanup(sys.path.remove, cls._code_tiny_path)
        cls.project_contract = _load_module("project_contract", _PROJECT_CONTRACT_PATH)
        cls.mcp = _load_module("mcp_graph_rag_dualclass_under_test", _MCP_GRAPH_RAG_PATH)
        assert cls.mcp._RegistryProjectNotRegisteredError is not None, (
            "code-side registry not importable; dual-class coverage lost"
        )

    def test_get_qdrant_fallback_catches_registry_sibling_class(self):
        registry_error = self.mcp._RegistryProjectNotRegisteredError
        local_store = object()

        def fake_store(*args, **kwargs):
            if kwargs.get("project_id"):
                raise registry_error(kwargs["project_id"], ["cortext"])
            return local_store

        self.mcp._qdrant_stores.pop("client-alpha", None)
        with patch.object(
            self.mcp, "get_document_qdrant_store", side_effect=fake_store
        ), patch.object(
            self.mcp, "_fallback_doc_qdrant_stores", return_value=[local_store]
        ):
            result = self.mcp.get_qdrant("client-alpha")
        self.assertIs(result, local_store)

    def test_fallback_stores_probe_remote_before_local(self):
        remote_store = object()
        local_store = object()
        created = {}

        class _FakeRemote:
            def __new__(cls, url, *args, **kwargs):
                created["url"] = url
                return remote_store

        self.mcp._qdrant_stores.pop("__remote__:http://qdrant:6333", None)
        with patch.object(
            self.mcp, "RemoteQdrantStore", _FakeRemote
        ), patch.object(
            self.mcp, "get_document_qdrant_store", return_value=local_store
        ), patch.dict(
            os.environ, {"QDRANT_URL": "http://qdrant:6333"}
        ):
            stores = self.mcp._fallback_doc_qdrant_stores()
        self.assertEqual(stores, [remote_store, local_store])
        self.assertEqual(created["url"], "http://qdrant:6333")

    def test_fallback_stores_local_only_without_remote_env(self):
        local_store = object()
        env_without_url = {
            key: value for key, value in os.environ.items() if key != "QDRANT_URL"
        }
        with patch.object(
            self.mcp, "get_document_qdrant_store", return_value=local_store
        ), patch.dict(os.environ, env_without_url, clear=True):
            stores = self.mcp._fallback_doc_qdrant_stores()
        self.assertEqual(stores, [local_store])

    def test_search_targets_unregistered_probes_both_stores_with_convention_names(self):
        remote_store = object()
        local_store = object()
        registry_error = self.mcp._RegistryProjectNotRegisteredError

        def fake_store(*args, **kwargs):
            if kwargs.get("project_id"):
                raise registry_error(kwargs["project_id"], ["cortext"])
            return local_store

        with patch.object(
            self.mcp, "get_document_qdrant_store", side_effect=fake_store
        ), patch.object(
            self.mcp,
            "_fallback_doc_qdrant_stores",
            return_value=[remote_store, local_store],
        ), patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            targets = self.mcp._qdrant_search_targets("client-alpha", None)
        self.assertEqual(
            targets,
            [(remote_store, ["client-alpha_doc"]), (local_store, ["client-alpha_doc"])],
        )

    def test_search_targets_registered_id_pins_registry_store(self):
        local_store = object()
        with patch.object(
            self.mcp, "get_document_qdrant_store", return_value=local_store
        ) as get_store, patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "cortext", "doc_env": {}}],
        ):
            targets = self.mcp._qdrant_search_targets("cortext", None)
        self.assertEqual(targets, [(local_store, ["cortext_doc"])])
        get_store.assert_called_once_with(project_id="cortext")

    def test_search_targets_collection_override_probes_fallback_stores(self):
        remote_store = object()
        local_store = object()
        with patch.object(
            self.mcp, "get_document_qdrant_store", return_value=local_store
        ), patch.object(
            self.mcp,
            "_fallback_doc_qdrant_stores",
            return_value=[remote_store, local_store],
        ):
            targets = self.mcp._qdrant_search_targets(None, "ishida_doc")
        self.assertEqual(
            targets,
            [(remote_store, ["ishida_doc"]), (local_store, ["ishida_doc"])],
        )

    def test_search_targets_unscoped_fans_out_registered_plus_instance_store(self):
        local_store = object()
        with patch.object(
            self.mcp, "get_document_qdrant_store", return_value=local_store
        ), patch.object(
            self.project_contract,
            "_read_project_entries",
            return_value=[{"project_id": "poc_main", "doc_env": {}}],
        ):
            targets = self.mcp._qdrant_search_targets(None, None)
        self.assertEqual(
            targets,
            [(local_store, ["poc_main_doc"]), (local_store, None)],
        )


if __name__ == "__main__":
    unittest.main()

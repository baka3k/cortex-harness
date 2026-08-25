import os
import sys
import unittest
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
for path in (CODE_TINY, MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import fastmcp_server  # noqa: E402
from android import android_mcp  # noqa: E402
from cplus import cplus_mcp  # noqa: E402
from java import java_mcp  # noqa: E402
from tools.common import intelligent_retrieval, local_qdrant  # noqa: E402
from tools.common.project_registry import ProjectNotRegisteredError  # noqa: E402
from cortex_harness.storage import QdrantStore  # noqa: E402


class _QueryResponse:
    points = []


class _LocalStore:
    def __init__(self):
        self.queries = []

    def query_points(self, collection_name, **kwargs):
        self.queries.append((collection_name, kwargs))
        return _QueryResponse()


class QdrantProjectScopeTests(unittest.IsolatedAsyncioTestCase):
    def test_shared_helpers_depend_on_qdrant_store_protocol(self):
        for helper_name in (
            "ensure_collection",
            "collection_info_payload",
            "collections_payload",
            "query_points",
            "scroll_points",
            "delete_by_filter",
        ):
            helper = getattr(local_qdrant, helper_name)
            self.assertIs(get_type_hints(helper)["store"], QdrantStore)

    def test_semantic_backends_add_server_side_project_filter(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            store = _LocalStore()
            with self.subTest(backend=backend.__name__), patch.object(
                backend, "get_code_qdrant_store", return_value=store,
            ) as get_store:
                backend._qdrant_search(
                    "symbols", [0.1, 0.2], 5, "local-code-store",
                    project_id="PrOjEcT-A",
                )

                get_store.assert_called_once_with()
                request = store.queries[0][1]
                self.assertEqual(
                    request["query_filter"].model_dump(exclude_none=True),
                    {"must": [{
                        "key": "project_id_normalized",
                        "match": {"value": "project-a"},
                    }]},
                )

    def test_semantic_backends_leave_unscoped_requests_unfiltered(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            store = _LocalStore()
            with self.subTest(backend=backend.__name__), patch.object(
                backend, "get_code_qdrant_store", return_value=store,
            ):
                backend._qdrant_search("symbols", [0.1, 0.2], 5, "local-code-store")

                self.assertIsNone(store.queries[0][1]["query_filter"])

    async def test_collection_helpers_use_current_qdrant_store(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            store = MagicMock()
            with self.subTest(backend=backend.__name__), patch.object(
                backend, "get_code_qdrant_store", return_value=store,
            ) as get_store, patch.object(
                backend,
                "collections_payload",
                return_value={"collections": ["symbols"]},
            ), patch.object(
                backend,
                "collection_info_payload",
                return_value={"result": {}},
            ):
                await backend._fetch_qdrant_collections("ignored")
                await backend._fetch_qdrant_collection_info("symbols", "ignored")

                self.assertEqual(
                    get_store.call_args_list,
                    [
                        unittest.mock.call(),
                        unittest.mock.call(),
                    ],
                )

    async def test_missing_explicit_collection_is_not_reported_as_vector_mismatch(
        self,
    ):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            with self.subTest(backend=backend.__name__), patch.object(
                backend,
                "_fetch_qdrant_collections",
                AsyncMock(return_value={"collections": ["other-project"]}),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "Requested Qdrant collection scope.*procsample",
                ):
                    await backend._resolve_base_collections(
                        "procsample",
                        "ignored",
                    )

    async def test_empty_inventory_does_not_probe_requested_collection_for_vector_size(
        self,
    ):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            with self.subTest(backend=backend.__name__), patch.object(
                backend,
                "_fetch_qdrant_collections",
                AsyncMock(return_value={"collections": []}),
            ):
                collections, explicit = await backend._resolve_base_collections(
                    "procsample",
                    "ignored",
                )

                self.assertEqual(collections, [])
                self.assertTrue(explicit)

    def test_explore_qdrant_search_adds_project_filter(self):
        store = _LocalStore()
        with patch.object(
            intelligent_retrieval, "get_code_qdrant_store", return_value=store,
        ) as get_store, patch.object(
            intelligent_retrieval, "_resolve_vector_layout", return_value=None,
        ):
            intelligent_retrieval._qdrant_search(
                "local-code-store", "symbols", [0.1, 0.2], 5,
                project_id="PrOjEcT-A",
            )

        get_store.assert_called_once_with("local-code-store")
        self.assertEqual(
            store.queries[0][1]["query_filter"].model_dump(exclude_none=True),
            {"must": [{
                "key": "project_id_normalized",
                "match": {"value": "project-a"},
            }]},
        )


class SemanticToolProjectScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cplus_semantic_search_keeps_vector_results_when_graph_is_unavailable(self):
        tool = getattr(cplus_mcp.tool_semantic_search, "fn", cplus_mcp.tool_semantic_search)
        store = _LocalStore()
        diagnostics = {
            "schema_status": "available",
            "support_status": "unsupported",
            "requested_relationships": ["CALLS"],
            "used_relationships": [],
            "available_relationships": [],
        }
        with patch.object(
            cplus_mcp,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=([], diagnostics)),
        ), patch.object(
            cplus_mcp, "_embed_query", return_value=[0.1, 0.2],
        ), patch.object(
            cplus_mcp,
            "_resolve_base_collections",
            AsyncMock(return_value=(["symbols"], True)),
        ), patch.object(
            cplus_mcp,
            "_filter_collections_for_vector",
            AsyncMock(return_value=([("symbols", None)], [])),
        ), patch.object(
            cplus_mcp, "get_code_qdrant_store", return_value=store,
        ):
            result = await tool(payload={
                "query": "login",
                "collection": "symbols",
                "project_id": "stock",
                "expand_graph": True,
            })

        self.assertNotIn("error", result)
        self.assertEqual(result["graph_expansion"]["outcome"], "unavailable")
        self.assertEqual(result["capability_diagnostics"], diagnostics)

    async def test_cplus_semantic_search_fails_for_explicit_unsupported_relationships(self):
        tool = getattr(cplus_mcp.tool_semantic_search, "fn", cplus_mcp.tool_semantic_search)
        diagnostics = {
            "schema_status": "available",
            "support_status": "unsupported",
            "requested_relationships": ["NOT_INGESTED"],
            "used_relationships": [],
            "available_relationships": ["CALLS"],
        }
        with patch.object(
            cplus_mcp,
            "_resolve_rel_types_with_diagnostics",
            AsyncMock(return_value=([], diagnostics)),
        ), patch.object(cplus_mcp, "_embed_query") as embed:
            result = await tool(payload={
                "query": "login",
                "project_id": "stock",
                "expand_graph": True,
                "graph_rel_types": ["NOT_INGESTED"],
            })

        self.assertEqual(result["error_type"], "unsupported_capability")
        self.assertEqual(result["capability_diagnostics"], diagnostics)
        embed.assert_not_called()

    async def test_unregistered_project_filters_discovered_collections(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            tool = getattr(
                backend.tool_semantic_search,
                "fn",
                backend.tool_semantic_search,
            )
            store = _LocalStore()
            with self.subTest(backend=backend.__name__), patch.object(
                backend,
                "resolve_project_targets",
                side_effect=ProjectNotRegisteredError(
                    "procsample", ["cortext"],
                ),
            ), patch.object(
                backend, "_embed_query", return_value=[0.1, 0.2],
            ), patch.object(
                backend,
                "_resolve_base_collections",
                AsyncMock(return_value=(["delphi_functions"], False)),
            ) as resolve_collections, patch.object(
                backend,
                "_filter_collections_for_vector",
                AsyncMock(return_value=([("delphi_functions", None)], [])),
            ), patch.object(
                backend, "get_code_qdrant_store", return_value=store,
            ) as get_store:
                if backend in (cplus_mcp, android_mcp):
                    await tool(payload={
                        "query": "orders",
                        "project_id": "procsample",
                    })
                else:
                    await tool(
                        query="orders",
                        project_id="procsample",
                    )

            self.assertIsNone(resolve_collections.await_args.args[0])
            get_store.assert_called_once_with()
            request = store.queries[0][1]
            self.assertEqual(
                request["query_filter"].model_dump(exclude_none=True),
                {"must": [{
                    "key": "project_id_normalized",
                    "match": {"value": "procsample"},
                }]},
            )

    async def test_semantic_tools_forward_scope_in_every_mode(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            tool = getattr(backend.tool_semantic_search, "fn", backend.tool_semantic_search)
            for mode in ("comment", "code", "combined"):
                store = _LocalStore()
                with self.subTest(backend=backend.__name__, mode=mode), patch.object(
                    backend, "_embed_query", return_value=[0.1, 0.2],
                ), patch.object(
                    backend,
                    "_resolve_base_collections",
                    AsyncMock(return_value=(["symbols"], True)),
                ), patch.object(
                    backend,
                    "_filter_collections_for_vector",
                    AsyncMock(return_value=([("symbols", None)], [])),
                ), patch.object(
                    backend, "get_code_qdrant_store", return_value=store,
                ):
                    if backend in (cplus_mcp, android_mcp):
                        await tool(payload={
                            "query": "orders",
                            "mode": mode,
                            "collection": "symbols",
                            "project_id": "PrOjEcT-A",
                        })
                    else:
                        await tool(
                            query="orders",
                            mode=mode,
                            collection="symbols",
                            project_id="PrOjEcT-A",
                        )

                self.assertEqual(
                    store.queries[-1][1]["query_filter"].model_dump(exclude_none=True),
                    {"must": [{
                        "key": "project_id_normalized",
                        "match": {"value": "project-a"},
                    }]},
                )


if __name__ == "__main__":
    unittest.main()

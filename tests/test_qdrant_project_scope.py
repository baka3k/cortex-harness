import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


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
from tools.common import intelligent_retrieval  # noqa: E402


class _QueryResponse:
    points = []


class _LocalStore:
    def __init__(self):
        self.queries = []

    def query_points(self, collection_name, **kwargs):
        self.queries.append((collection_name, kwargs))
        return _QueryResponse()


class QdrantProjectScopeTests(unittest.TestCase):
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

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


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"points": []}}


class QdrantProjectScopeTests(unittest.TestCase):
    def test_semantic_backends_add_server_side_project_filter(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            with self.subTest(backend=backend.__name__), patch.object(
                backend.httpx, "post", return_value=_Response(),
            ) as post:
                backend._qdrant_search(
                    "symbols", [0.1, 0.2], 5, "http://qdrant",
                    project_id="project-a",
                )

                request = post.call_args.kwargs["json"]
                self.assertEqual(
                    request["filter"],
                    {"must": [{"key": "project_id", "match": {"value": "project-a"}}]},
                )

    def test_semantic_backends_leave_unscoped_requests_unfiltered(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            with self.subTest(backend=backend.__name__), patch.object(
                backend.httpx, "post", return_value=_Response(),
            ) as post:
                backend._qdrant_search("symbols", [0.1, 0.2], 5, "http://qdrant")

                self.assertNotIn("filter", post.call_args.kwargs["json"])

    def test_explore_qdrant_search_adds_project_filter(self):
        with patch.object(
            intelligent_retrieval.httpx, "post", return_value=_Response(),
        ) as post, patch.object(
            intelligent_retrieval, "_resolve_vector_layout", return_value=None,
        ):
            intelligent_retrieval._qdrant_search(
                "http://qdrant", "symbols", [0.1, 0.2], 5,
                project_id="project-a",
            )

        self.assertEqual(
            post.call_args.kwargs["json"]["filter"],
            {"must": [{"key": "project_id", "match": {"value": "project-a"}}]},
        )


class SemanticToolProjectScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_tools_forward_scope_in_every_mode(self):
        for backend in (fastmcp_server, cplus_mcp, android_mcp, java_mcp):
            tool = getattr(backend.tool_semantic_search, "fn", backend.tool_semantic_search)
            for mode in ("comment", "code", "combined"):
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
                    backend.httpx, "post", return_value=_Response(),
                ) as post:
                    if backend in (cplus_mcp, android_mcp):
                        await tool(payload={
                            "query": "orders",
                            "mode": mode,
                            "collection": "symbols",
                            "project_id": "project-a",
                        })
                    else:
                        await tool(
                            query="orders",
                            mode=mode,
                            collection="symbols",
                            project_id="project-a",
                        )

                self.assertEqual(
                    post.call_args.kwargs["json"]["filter"],
                    {"must": [{
                        "key": "project_id",
                        "match": {"value": "project-a"},
                    }]},
                )


if __name__ == "__main__":
    unittest.main()

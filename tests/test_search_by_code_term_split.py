import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
for path in (str(CODE_TINY), str(MCP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")


def _load_cplus_backend():
    spec = importlib.util.spec_from_file_location(
        "cplus_backend_term_split", MCP_DIR / "cplus" / "cplus_mcp.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cplus_backend = _load_cplus_backend()


class SearchByCodeTermSplitTest(unittest.IsolatedAsyncioTestCase):
    """search_by_code treats whitespace like '|' — CJK queries arrive space-separated."""

    async def test_whitespace_and_pipe_separated_query_becomes_or_terms(self):
        captured: list[dict] = []

        async def fake_run_cypher_first(query, params, dbs):
            captured.append(dict(params))
            if "index_name" in params:
                return "hyper_graph", []
            return "hyper_graph", [{"n": {"id": "hit", "name": "SP_MENU_CONTROL"}, "labels": ["Function"]}]

        with patch.object(cplus_backend, "_run_cypher_first", fake_run_cypher_first):
            result = await cplus_backend.tool_search_by_code(
                query="権限 権限制御 | パスワード",
                limit=100,
            )

        self.assertEqual(captured[0]["query"], "権限 OR 権限制御 OR パスワード")
        fallback = captured[1]
        self.assertEqual(fallback["qs"], ["権限", "権限制御", "パスワード"])
        self.assertEqual(len(result["results"]), 1)

    async def test_pipe_only_query_is_unchanged(self):
        captured: list[dict] = []

        async def fake_run_cypher_first(query, params, dbs):
            captured.append(dict(params))
            if "index_name" in params:
                return "hyper_graph", [{"n": {"id": "hit", "name": "malloc"}, "labels": ["Function"]}]
            return "hyper_graph", []

        with patch.object(cplus_backend, "_run_cypher_first", fake_run_cypher_first):
            await cplus_backend.tool_search_by_code(query="malloc|calloc", limit=10)

        self.assertEqual(captured[0]["query"], "malloc OR calloc")
        self.assertEqual(captured[0]["limit"], 10)


if __name__ == "__main__":
    unittest.main()

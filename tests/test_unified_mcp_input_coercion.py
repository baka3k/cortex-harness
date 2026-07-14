import os
import sys
import unittest
from unittest.mock import patch


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(ROOT, "code-tiny")
MCP_DIR = os.path.join(CODE_TINY, "mcp")
for path in (CODE_TINY, MCP_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import unified_mcp


class UnifiedMcpInputCoercionTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_positive_int_accepts_numeric_and_string_values(self):
        self.assertEqual(unified_mcp._parse_positive_int(20, "top_k"), (20, None))
        self.assertEqual(unified_mcp._parse_positive_int("20", "top_k"), (20, None))
        self.assertEqual(unified_mcp._parse_positive_int(20.0, "top_k"), (20, None))
        self.assertEqual(unified_mcp._parse_positive_int("", "top_k"), (None, None))

    def test_parse_positive_int_rejects_non_integer_values(self):
        self.assertEqual(
            unified_mcp._parse_positive_int(20.5, "top_k"),
            (None, "top_k must be a positive integer."),
        )
        self.assertEqual(
            unified_mcp._parse_positive_int(True, "top_k"),
            (None, "top_k must be a positive integer."),
        )
        self.assertEqual(
            unified_mcp._parse_positive_int(-1, "top_k"),
            (None, "top_k must be greater than 0."),
        )

    async def test_semantic_search_normalizes_numeric_knobs_before_dispatch(self):
        captured = {}

        async def fake_dispatch(tool_name, payload):
            captured["tool_name"] = tool_name
            captured["payload"] = payload
            return {"results": []}

        tool = getattr(
            unified_mcp.tool_semantic_search,
            "fn",
            unified_mcp.tool_semantic_search,
        )
        with patch.object(unified_mcp, "_dispatch_tool", side_effect=fake_dispatch):
            result = await tool(
                query="validation",
                top_k=20,
                graph_depth=2.0,
                graph_limit="50",
            )

        self.assertEqual(result, {"results": []})
        self.assertEqual(captured["tool_name"], "semantic_search")
        self.assertEqual(captured["payload"]["top_k"], 20)
        self.assertEqual(captured["payload"]["graph_depth"], 2)
        self.assertEqual(captured["payload"]["graph_limit"], 50)

    async def test_semantic_search_returns_tool_error_for_bad_numeric_input(self):
        tool = getattr(
            unified_mcp.tool_semantic_search,
            "fn",
            unified_mcp.tool_semantic_search,
        )

        result = await tool(query="validation", top_k="many")

        self.assertEqual(result["error"]["type"], "invalid_parameters")
        self.assertIn("top_k must be a positive integer", result["error"]["message"])

    async def test_explore_graph_returns_tool_error_for_bad_numeric_input(self):
        tool = getattr(
            unified_mcp.tool_explore_graph,
            "fn",
            unified_mcp.tool_explore_graph,
        )

        result = await tool(query="validation", top_k="many")

        self.assertEqual(result["error"]["type"], "invalid_parameters")
        self.assertIn("top_k must be a positive integer", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()

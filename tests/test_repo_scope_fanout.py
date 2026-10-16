"""Fan-out regression tests for the repo scope (review fix cycle 1).

Covers the CRITICAL found in review: scoped-empty backends emit
``warnings: ["repo 'x' matched 0 nodes…"]`` and the generic fan-out list
merge used to tag those strings as result items, crashing the string
dedupe with ``TypeError: unhashable type: 'dict'`` — exactly on the
default (parser-less = fan-out) path.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CODE_TINY = _REPO_ROOT / "code-tiny"
_MCP_DIR = _CODE_TINY / "mcp"
for path in (_CODE_TINY, _MCP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import unified_mcp  # noqa: E402

_WARNING = "repo 's3' matched 0 nodes. Known repo values: projA/s3"
_SCOPE = {"repo": "s3", "filter_applied": True, "warnings": [_WARNING]}


class MergeFanoutWarningsTest(unittest.TestCase):
    def test_scoped_empty_warnings_merge_without_crash(self) -> None:
        parser_results = {
            "cplus": {"ok": True, "results": [], "warnings": [_WARNING], "scope": dict(_SCOPE)},
            "android": {"ok": True, "results": [], "warnings": [_WARNING], "scope": dict(_SCOPE)},
        }
        merged = unified_mcp._merge_fanout_results(parser_results, {})
        # Deduped to one copy, plain strings — the documented list[str] shape.
        self.assertEqual(merged["warnings"], [_WARNING])
        self.assertEqual(merged["results"], [])

    def test_divergent_warnings_union_preserves_order(self) -> None:
        parser_results = {
            "cplus": {"ok": True, "results": [], "warnings": [_WARNING, "engine-a note"]},
            "android": {"ok": True, "results": [], "warnings": [_WARNING, "engine-b note"]},
        }
        merged = unified_mcp._merge_fanout_results(parser_results, {})
        # Parsers are visited in sorted order (android before cplus).
        self.assertEqual(
            merged["warnings"], [_WARNING, "engine-b note", "engine-a note"]
        )

    def test_non_string_warning_is_coerced(self) -> None:
        parser_results = {
            "cplus": {"ok": True, "results": [], "warnings": [{"oops": 1}]},
        }
        merged = unified_mcp._merge_fanout_results(parser_results, {})
        self.assertEqual(merged["warnings"], ["{'oops': 1}"])

    def test_unscoped_empty_warnings_list(self) -> None:
        parser_results = {
            "cplus": {"ok": True, "results": [], "warnings": []},
            "android": {"ok": True, "results": [], "warnings": []},
        }
        merged = unified_mcp._merge_fanout_results(parser_results, {})
        self.assertEqual(merged["warnings"], [])


class DispatchScopePromotionTest(unittest.IsolatedAsyncioTestCase):
    async def test_fanout_dispatch_promotes_scope_block(self) -> None:
        merged_like = {
            "ok": True,
            "parsers_searched": ["cplus", "android"],
            "parser_results": {
                "cplus": {"ok": True, "results": [], "warnings": [], "scope": dict(_SCOPE)},
                "android": {"ok": True, "results": [], "warnings": [], "scope": dict(_SCOPE)},
            },
            "warnings": [],
        }
        with patch.object(
            unified_mcp, "_fanout_dispatch", AsyncMock(return_value=merged_like)
        ), patch.object(
            unified_mcp,
            "_resolve_fanout_parsers",
            return_value=(["cplus", "android"], None),
        ):
            result = await unified_mcp._dispatch_tool(
                "search_functions", {"query": "pay", "repo": "s3"}
            )
        self.assertEqual(result["scope"]["repo"], "s3")
        self.assertEqual(result["scope"]["filter_applied"], True)
        self.assertEqual(result["warnings"], [])
        # Per-parser copies stay intact for introspection.
        self.assertEqual(result["parser_results"]["android"]["scope"]["repo"], "s3")


if __name__ == "__main__":
    unittest.main()

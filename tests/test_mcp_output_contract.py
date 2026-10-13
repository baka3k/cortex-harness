"""v2 single-channel output contract (plan 261013-0000).

Maps phase-01 criteria: one JSON envelope in ``content[0].text`` for every
tool outcome, no ``structuredContent`` on the wire, structural truncation
under the size cap, bounded error details, and exception-routed direct
tools (red-team F1).
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
DOC_TINY = ROOT / "doc-tiny"
for path in (str(ROOT), str(CODE_TINY), str(MCP_DIR), str(DOC_TINY)):
    if path not in sys.path:
        sys.path.insert(0, path)
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")

import unified_mcp
import mcp_graph_rag
from cortex_harness.mcp_contract import (
    MCP_CONTRACT_NAME,
    MCP_CONTRACT_VERSION,
    normalize_error,
    normalize_success,
    result_envelope_json,
    result_meta,
    result_summary,
)

V2_KEYS = {
    "contract",
    "contractVersion",
    "tool",
    "message",
    "ok",
    "data",
    "error",
    "truncation",
}


def _wire(result):
    """ToolResult -> protocol-level CallToolResult (what clients receive)."""

    return result.to_mcp_result()


def _envelope_of(result):
    mcp_result = _wire(result)
    return json.loads(mcp_result.content[0].text)


class EnvelopeJsonTests(unittest.TestCase):
    def test_success_envelope_carries_full_payload_in_data(self):
        payload = {"db": "procsample", "results": [{"id": "f1"}], "ids": ["f1"]}

        envelope = json.loads(
            result_envelope_json("search_functions", ok=True, data=payload)
        )

        self.assertEqual(set(envelope), V2_KEYS)
        self.assertEqual(envelope["contract"], MCP_CONTRACT_NAME)
        self.assertEqual(envelope["contractVersion"], MCP_CONTRACT_VERSION)
        self.assertEqual(envelope["tool"], "search_functions")
        self.assertEqual(envelope["message"], "Success: 1 result.")
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"], payload)
        self.assertIsNone(envelope["error"])
        self.assertIsNone(envelope["truncation"])

    def test_error_envelope_keeps_stable_code_and_message(self):
        envelope = json.loads(
            result_envelope_json(
                "search_functions",
                ok=False,
                data=None,
                error={
                    "code": "invalid_parameters",
                    "message": "bad limit",
                    "retryable": False,
                    "details": {},
                },
                message="bad limit",
            )
        )

        self.assertEqual(set(envelope), V2_KEYS)
        self.assertFalse(envelope["ok"])
        self.assertIsNone(envelope["data"])
        self.assertEqual(
            envelope["error"],
            {
                "code": "invalid_parameters",
                "message": "bad limit",
                "retryable": False,
                "details": {},
            },
        )
        self.assertEqual(envelope["message"], "bad limit")

    def test_envelope_text_never_mentions_structured_content(self):
        success = result_envelope_json("t", ok=True, data={"results": []})
        failure = result_envelope_json(
            "t", ok=False, error={"code": "x", "message": "m"}, message="m"
        )

        self.assertNotIn("structuredContent", success)
        self.assertNotIn("structuredContent", failure)

    def test_string_payload_is_wrapped_as_text_data(self):
        envelope = json.loads(result_envelope_json("t", ok=True, data="raw text"))

        self.assertEqual(envelope["data"], {"text": "raw text"})

    def test_non_native_payload_still_serializes(self):
        envelope = json.loads(
            result_envelope_json(
                "t",
                ok=True,
                data={"when": datetime(2026, 10, 13), "ids": {1, 2}},
            )
        )

        self.assertTrue(envelope["ok"])
        self.assertIn("when", envelope["data"])


class TruncationTests(unittest.TestCase):
    def test_oversize_payload_degrades_to_preview_under_cap(self):
        payload = {"results": [{"note": "x" * 5000}]}
        with mock.patch.dict(
            os.environ, {"MCP_CONTENT_MAX_CHARS": "600"}
        ):
            text = result_envelope_json("t", ok=True, data=payload)

        self.assertLessEqual(len(text), 600)
        envelope = json.loads(text)
        self.assertEqual(envelope["truncation"]["reason"], "content_cap_exceeded")
        self.assertEqual(envelope["truncation"]["cap_chars"], 600)
        self.assertTrue(envelope["truncation"]["truncated"])
        self.assertTrue(envelope["data"]["truncated"])
        self.assertTrue(envelope["data"]["preview"])

    def test_cap_below_overhead_yields_empty_preview(self):
        with mock.patch.dict(os.environ, {"MCP_CONTENT_MAX_CHARS": "50"}):
            text = result_envelope_json(
                "t", ok=True, data={"results": ["x" * 5000]}
            )

        envelope = json.loads(text)
        self.assertEqual(envelope["data"]["preview"], "")
        self.assertTrue(envelope["data"]["truncated"])

    def test_cap_disabled_keeps_full_payload(self):
        payload = {"results": ["x" * 5000]}
        with mock.patch.dict(os.environ, {"MCP_CONTENT_MAX_CHARS": "0"}):
            text = result_envelope_json("t", ok=True, data=payload)

        envelope = json.loads(text)
        self.assertIsNone(envelope["truncation"])
        self.assertEqual(envelope["data"], payload)

    def test_escape_inflating_payload_still_respects_cap(self):
        payload = {"results": [{"note": '"' * 3000}]}
        with mock.patch.dict(os.environ, {"MCP_CONTENT_MAX_CHARS": "800"}):
            text = result_envelope_json("t", ok=True, data=payload)

        self.assertLessEqual(len(text), 800)
        envelope = json.loads(text)
        self.assertTrue(envelope["truncation"]["truncated"])


class ErrorBoundTests(unittest.TestCase):
    def test_error_details_lists_are_bounded(self):
        envelope = json.loads(
            result_envelope_json(
                "t",
                ok=False,
                error={
                    "code": "tool_execution_error",
                    "message": "m" * 1000,
                    "retryable": False,
                    "details": {"violations": list(range(60))},
                },
                message="m" * 1000,
            )
        )

        self.assertEqual(len(envelope["error"]["message"]), 500)
        self.assertEqual(len(envelope["error"]["details"]["violations"]), 50)
        self.assertTrue(envelope["error"]["details"]["details_truncated"])

    def test_oversize_error_keeps_data_null_and_stays_bounded(self):
        text = result_envelope_json(
            "t",
            ok=False,
            error={
                "code": "tool_execution_error",
                "message": "x" * 25_000,
                "retryable": False,
                "details": {"trace": "y" * 25_000},
            },
            message="x" * 25_000,
        )

        envelope = json.loads(text)
        self.assertIsNone(envelope["data"])
        self.assertLessEqual(len(envelope["error"]["message"]), 500)
        self.assertEqual(envelope["error"]["details"]["trace"], "y" * 500)
        self.assertLessEqual(len(text), 20_000)

    def test_oversize_error_under_tight_cap_drops_details_keeps_data_null(self):
        with mock.patch.dict(os.environ, {"MCP_CONTENT_MAX_CHARS": "400"}):
            text = result_envelope_json(
                "t",
                ok=False,
                error={
                    "code": "tool_execution_error",
                    "message": "m" * 25_000,
                    "retryable": False,
                    "details": {"trace": "y" * 25_000},
                },
                message="m" * 25_000,
            )

        envelope = json.loads(text)
        self.assertIsNone(envelope["data"])
        self.assertEqual(envelope["truncation"]["reason"], "error_details_dropped")
        self.assertEqual(envelope["error"]["details"], {})
        self.assertIn("code", envelope["error"])

    def test_framework_error_aliases_map_to_canonical_codes(self):
        for raw, expected in (
            ("tool_error", "tool_execution_error"),
            ("validation_error", "invalid_parameters"),
        ):
            envelope = normalize_error(
                {"ok": False, "error": {"type": raw, "message": "m"}}
            )
            self.assertEqual(envelope["error"]["code"], expected)

    def test_summary_message_drops_legacy_pointer(self):
        self.assertEqual(
            result_summary({"results": [{"note": "x" * 10_000}]}, ok=True),
            "Success: 1 result.",
        )
        self.assertEqual(result_summary(None, ok=False), "Tool execution failed.")
        self.assertNotIn("structuredContent", result_summary({"ids": []}, ok=True))


class WireTests(unittest.TestCase):
    def test_wire_result_has_no_structured_content(self):
        result = unified_mcp._ProxyMiddleware._wrap_dispatch_result(
            {"results": [{"id": "f1"}]}, tool_name="search_functions"
        )
        mcp_result = _wire(result)

        self.assertFalse(mcp_result.is_error)
        self.assertIsNone(getattr(mcp_result, "structured_content", None))
        envelope = json.loads(mcp_result.content[0].text)
        self.assertEqual(set(envelope), V2_KEYS)
        self.assertEqual(envelope["data"], {"results": [{"id": "f1"}]})

    def test_dispatch_error_wire_round_trips_is_error(self):
        result = unified_mcp._ProxyMiddleware._wrap_dispatch_result(
            {
                "ok": False,
                "error": {"type": "capability_unavailable", "message": "gone"},
            },
            tool_name="t",
        )
        mcp_result = _wire(result)

        self.assertTrue(mcp_result.is_error)
        self.assertIsNone(getattr(mcp_result, "structured_content", None))
        envelope = json.loads(mcp_result.content[0].text)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["message"], "gone")
        self.assertEqual(envelope["error"]["code"], "capability_unavailable")


class _FakeMessage:
    name = "list_parsers"
    arguments = {"detail_level": "bogus"}


class _FakeContext:
    message = _FakeMessage()


async def _raising_call_next(_context):
    raise ValueError("detail_level must be 'summary' or 'full'.")


class DirectToolExceptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_raising_direct_tool_still_emits_envelope(self):
        middleware = unified_mcp._proxy_middleware

        result = await middleware.on_call_tool(_FakeContext(), _raising_call_next)
        mcp_result = _wire(result)

        self.assertTrue(mcp_result.is_error)
        self.assertIsNone(getattr(mcp_result, "structured_content", None))
        envelope = json.loads(mcp_result.content[0].text)
        self.assertEqual(envelope["error"]["code"], "invalid_parameters")
        self.assertIn("detail_level", envelope["error"]["message"])

    async def test_preconverted_error_result_keeps_original_message(self):
        # fastmcp converts tool exceptions to plain-text error ToolResults
        # in some paths; the envelope must keep the original text.
        async def _converted_call_next(_context):
            return unified_mcp.ToolResult(
                content="detail_level must be 'summary' or 'full'.",
                is_error=True,
            )

        result = await unified_mcp._proxy_middleware.on_call_tool(
            _FakeContext(), _converted_call_next
        )
        mcp_result = _wire(result)

        self.assertTrue(mcp_result.is_error)
        envelope = json.loads(mcp_result.content[0].text)
        self.assertEqual(envelope["error"]["code"], "tool_execution_error")
        self.assertIn("detail_level", envelope["error"]["message"])


class _EnvelopeFakeMcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


class MindServerWireTests(unittest.TestCase):
    def test_standard_tool_wire_has_no_structured_content(self):
        fake = _EnvelopeFakeMcp()

        @mcp_graph_rag._standard_tool(fake)
        def boom():
            return {"results": [1]}

        result = fake.tools["boom"]()
        self.assertFalse(result.is_error)
        self.assertIsNone(result.structured_content)
        envelope = json.loads(result.content[0].text)
        self.assertEqual(set(envelope), V2_KEYS)
        self.assertEqual(envelope["data"], {"results": [1]})

    def test_standard_tool_error_wire_is_envelope(self):
        fake = _EnvelopeFakeMcp()

        @mcp_graph_rag._standard_tool(fake)
        def boom():
            raise LookupError("collection missing")

        result = fake.tools["boom"]()
        self.assertTrue(result.is_error)
        self.assertIsNone(result.structured_content)
        envelope = json.loads(result.content[0].text)
        self.assertEqual(envelope["error"]["code"], "collection_unavailable")


if __name__ == "__main__":
    unittest.main()

import json
from unittest.mock import patch

import pytest

from testtool.mcp_client import MCPClient, MCPError


def _rpc_response(result):
    return 200, {"content-type": "application/json"}, json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": result}
    )


def test_call_tool_prefers_structured_content_when_text_is_empty():
    client = MCPClient()
    response = _rpc_response(
        {
            "content": [{"type": "text", "text": ""}],
            "structuredContent": {"result": ["procsample"]},
            "isError": False,
        }
    )

    with patch.object(client, "_post", return_value=response):
        result = client.call_tool("list_qdrant_collections", {})

    assert result == {"result": ["procsample"]}


def test_call_tool_raises_when_mcp_marks_result_as_error():
    client = MCPClient()
    response = _rpc_response(
        {
            "content": [
                {
                    "type": "text",
                    "text": "Requested document collection is not ingested",
                }
            ],
            "structuredContent": {
                "ok": False,
                "error": {"type": "collection_unavailable"},
            },
            "isError": True,
        }
    )

    with patch.object(client, "_post", return_value=response):
        with pytest.raises(MCPError, match="collection is not ingested"):
            client.call_tool("semantic_search", {"query": "batch"})


def test_call_tool_keeps_json_text_fallback_for_unstructured_results():
    client = MCPClient()
    response = _rpc_response(
        {
            "content": [{"type": "text", "text": '{"ok": true}'}],
            "isError": False,
        }
    )

    with patch.object(client, "_post", return_value=response):
        result = client.call_tool("legacy_tool", {})

    assert result == {"ok": True}


def test_call_tool_raw_preserves_complete_jsonrpc_response():
    client = MCPClient()
    response = _rpc_response(
        {
            "content": [{"type": "text", "text": "Success."}],
            "structuredContent": {"ok": True, "data": {"ids": ["n1"]}, "error": None},
            "isError": False,
        }
    )

    with patch.object(client, "_post", return_value=response):
        result = client.call_tool_raw("get_node_details", {"node_ids": ["n1"]})

    assert result["jsonrpc"] == "2.0"
    assert result["result"]["structuredContent"]["data"] == {"ids": ["n1"]}

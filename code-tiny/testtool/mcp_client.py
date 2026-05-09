"""
Minimal MCP streamable-http client.

Handles:
- Session initialization (initialize + notifications/initialized)
- tools/list
- tools/call
- SSE or direct-JSON response parsing
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

DEFAULT_ENDPOINT = "http://127.0.0.1:8788/mcp"
PROTOCOL_VERSION = "2026-02-2"
TIMEOUT = 120.0


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, timeout: float = TIMEOUT) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> Dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["mcp-session-id"] = self._session_id
        return h

    def _post(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, str], str]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.endpoint, headers=self._headers(), json=body)
        session = resp.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        return resp.status_code, dict(resp.headers), resp.text

    def _parse_response(self, status: int, headers: Dict[str, str], text: str) -> Any:
        if status >= 400:
            raise MCPError(f"HTTP {status}: {text[:300]}")
        ct = headers.get("content-type", "")
        if "text/event-stream" in ct:
            return self._parse_sse(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return self._parse_sse(text)

    @staticmethod
    def _parse_sse(text: str) -> Any:
        """Extract last JSON object from SSE stream data lines."""
        results = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    results.append(json.loads(data))
                except json.JSONDecodeError:
                    pass
        if not results:
            raise MCPError(f"No parseable SSE data in response:\n{text[:500]}")
        # Return the last message (typically the final result)
        return results[-1]

    def _unwrap(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            if "error" in payload:
                err = payload["error"]
                raise MCPError(f"MCP error: {err}")
            if "result" in payload:
                return payload["result"]
        return payload

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def initialize(self) -> Dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-tester", "version": "1.0.0"},
            },
        }
        status, headers, text = self._post(body)
        result = self._unwrap(self._parse_response(status, headers, text))
        # Send initialized notification (no response expected)
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            self._post(notif)
        except Exception:
            pass
        return result

    def list_tools(self) -> List[Dict[str, Any]]:
        body = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list"}
        status, headers, text = self._post(body)
        result = self._unwrap(self._parse_response(status, headers, text))
        if isinstance(result, dict):
            return result.get("tools", [])
        return []

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        status, headers, text = self._post(body)
        result = self._unwrap(self._parse_response(status, headers, text))
        # tools/call result: {"content": [...], "isError": bool}
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                combined = "\n".join(parts)
                try:
                    return json.loads(combined)
                except json.JSONDecodeError:
                    return combined
            return content
        return result

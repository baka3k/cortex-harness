"""FastMCP middleware emitting the shared Cortex MCP v2 result envelope.

Older servers monkey-patched ``FastMCP._call_tool_mcp`` — a private API that
was removed in fastmcp 4.x, breaking module import. This module provides the
same wire contract through the public middleware API, on the v2 single-
channel contract: every outcome is one text block holding the JSON envelope
(``cortex_harness.mcp_contract.result_envelope_json``);
``structuredContent`` is never emitted.

``wrap_tool_result`` is the single envelope assembly point shared by the
standalone fast server (via ``ToolErrorMiddleware`` /
``ToolEnvelopeMiddleware``) and the unified server (``_ProxyMiddleware``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from cortex_harness.mcp_contract import (
    normalize_error,
    normalize_success,
    result_envelope_json,
    result_meta,
    result_summary,
)


def is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def extract_call_payload(arguments: Any) -> Dict[str, Any]:
    if not isinstance(arguments, dict):
        return {}
    merged = dict(arguments)
    payload = arguments.get("payload")
    if isinstance(payload, dict):
        merged.update(payload)
    return merged


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: List[str] = []
    if isinstance(content, list):
        for block in content:
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


def wrap_tool_result(result: Any, *, tool_name: Optional[str] = None) -> ToolResult:
    """Emit the v2 single-channel envelope for any tool outcome.

    Accepts a raw business payload, a pre-built envelope dict (``ok`` key),
    a plain string payload, or an existing ``ToolResult`` (its structured
    content, or JSON/text content, is unwrapped first).  The wire shape is
    always one text block with the JSON envelope; ``structured_content`` is
    left unset so FastMCP cannot auto-generate one.
    """

    existing_meta: Dict[str, Any] = {}
    is_error = False
    if isinstance(result, ToolResult):
        existing_meta = dict(result.meta or {})
        structured = result.structured_content
        is_error = bool(result.is_error) or (
            isinstance(structured, dict) and structured.get("ok") is False
        )
        if structured is not None:
            payload = structured
        else:
            raw_text = _content_text(result.content)
            try:
                payload = json.loads(raw_text)
            except (TypeError, json.JSONDecodeError):
                payload = raw_text or None
    else:
        payload = result
        is_error = isinstance(payload, dict) and payload.get("ok") is False

    meta = {**existing_meta, **result_meta(tool_name)}
    if is_error:
        envelope = normalize_error(payload)
        message = envelope["error"]["message"]
        return ToolResult(
            content=result_envelope_json(
                tool_name,
                ok=False,
                data=None,
                error=envelope["error"],
                message=message,
            ),
            meta=meta,
            is_error=True,
        )

    if isinstance(payload, dict) and payload.get("ok") is True:
        payload = {key: value for key, value in payload.items() if key != "ok"}
    envelope = normalize_success(payload)
    return ToolResult(
        content=result_envelope_json(
            tool_name,
            ok=True,
            data=envelope["data"],
            message=result_summary(payload, ok=True),
        ),
        meta=meta,
        is_error=False,
    )


class ToolErrorMiddleware(Middleware):
    """Catch tool exceptions and return the structured error payload."""

    def __init__(
        self,
        *,
        inputs_by_tool: Dict[str, List[Dict[str, Any]]],
    ) -> None:
        super().__init__()
        self._inputs_by_tool = inputs_by_tool

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> ToolResult:
        try:
            return await call_next(context)
        except Exception as exc:
            message = context.message
            key = str(getattr(message, "name", "") or "")
            arguments = dict(getattr(message, "arguments", {}) or {})
            provided = extract_call_payload(arguments)
            input_entries = self._inputs_by_tool.get(key, [])
            required = [
                str(entry.get("name"))
                for entry in input_entries
                if isinstance(entry, dict) and entry.get("required") and entry.get("name")
            ]
            missing = [name for name in required if is_missing_value(provided.get(name))]
            error_type = "tool_execution_error"
            if missing:
                error_type = "missing_required_parameters"
            elif isinstance(exc, (ValueError, TypeError)):
                error_type = "invalid_parameters"

            payload: Dict[str, Any] = {
                "ok": False,
                "error": {
                    "type": error_type,
                    "message": str(exc),
                    "missing_required_params": missing,
                },
            }
            return wrap_tool_result(payload, tool_name=key or None)


class ToolEnvelopeMiddleware(Middleware):
    """Re-wrap every successful tool result into the v2 envelope.

    The standalone fast server's tools return raw business payloads; left
    alone FastMCP auto-generates ``structuredContent`` for dict returns.
    Installing this middleware keeps the standalone server on the same
    JSON-in-content wire shape as the unified and mind servers.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> ToolResult:
        result = await call_next(context)
        name = str(getattr(getattr(context, "message", None), "name", "") or "")
        return wrap_tool_result(result, tool_name=name or None)


def install_tool_error_middleware(
    server: FastMCP,
    *,
    inputs_by_tool: Dict[str, List[Dict[str, Any]]],
) -> bool:
    """Install the error middleware once; return whether it was installed."""

    if getattr(server, "_safe_tool_wrapper_installed", False):
        return False
    server.add_middleware(ToolErrorMiddleware(inputs_by_tool=inputs_by_tool))
    setattr(server, "_safe_tool_wrapper_installed", True)
    return True


def install_tool_envelope_middleware(server: FastMCP) -> bool:
    """Install the v2 envelope middleware once (standalone fast server)."""

    if getattr(server, "_tool_envelope_middleware_installed", False):
        return False
    server.add_middleware(ToolEnvelopeMiddleware())
    setattr(server, "_tool_envelope_middleware_installed", True)
    return True

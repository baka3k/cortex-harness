"""FastMCP middleware converting tool-call exceptions into structured errors.

Older servers monkey-patched ``FastMCP._call_tool_mcp`` — a private API that
was removed in fastmcp 4.x, breaking module import. This module provides the
same wire contract through the public middleware API: any exception raised by
a tool call is converted into the legacy ``ok=False`` payload with parameter
diagnostics taken from the server's tool catalog.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult


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


class ToolErrorMiddleware(Middleware):
    """Catch tool exceptions and return the structured error payload."""

    def __init__(
        self,
        *,
        inputs_by_tool: Dict[str, List[Dict[str, Any]]],
        example_for: Optional[Callable[[str], Any]] = None,
        backend: str = "fast",
    ) -> None:
        super().__init__()
        self._inputs_by_tool = inputs_by_tool
        self._example_for = example_for or (lambda _name: None)
        self._backend = backend

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
            accepted = [
                str(entry.get("name"))
                for entry in input_entries
                if isinstance(entry, dict) and entry.get("name")
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
                    "tool": key,
                    "backend": self._backend,
                    "message": str(exc),
                    "missing_required_params": missing,
                    "required_params": required,
                    "accepted_params": accepted,
                    "received_params": sorted(
                        [
                            name
                            for name in provided.keys()
                            if not is_missing_value(provided.get(name))
                        ]
                    ),
                    "example": self._example_for(key),
                    "next_step": (
                        "Call list_mcp_functions and retry with exact parameter names."
                    ),
                },
            }
            return ToolResult(
                content=json.dumps(payload, ensure_ascii=False),
                structured_content=payload,
                is_error=True,
            )


def install_tool_error_middleware(
    server: FastMCP,
    *,
    inputs_by_tool: Dict[str, List[Dict[str, Any]]],
    example_for: Optional[Callable[[str], Any]] = None,
    backend: str = "fast",
) -> bool:
    """Install the error middleware once; return whether it was installed."""

    if getattr(server, "_safe_tool_wrapper_installed", False):
        return False
    server.add_middleware(
        ToolErrorMiddleware(
            inputs_by_tool=inputs_by_tool,
            example_for=example_for,
            backend=backend,
        )
    )
    setattr(server, "_safe_tool_wrapper_installed", True)
    return True

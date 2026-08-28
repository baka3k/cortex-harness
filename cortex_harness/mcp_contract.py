"""Shared protocol-boundary contract for Cortex MCP tool results.

Business payloads stay tool-specific inside ``data``.  The wire-facing
success/error shape is deliberately small and identical for graph_mcp and
mind_mcp so clients never have to infer whether a dict is data or an error.
"""

from __future__ import annotations

from typing import Any, Mapping


MCP_CONTRACT_NAME = "cortex.mcp.tool-result"
MCP_CONTRACT_VERSION = "1.0"


def normalize_success(data: Any) -> dict[str, Any]:
    """Return the canonical success envelope without copying ``data``."""

    return {"ok": True, "data": data, "error": None}


def _exception_code(exc: BaseException) -> str:
    name = type(exc).__name__
    words: list[str] = []
    current = ""
    for character in name:
        if character.isupper() and current:
            words.append(current)
            current = character.lower()
        else:
            current += character.lower()
    if current:
        words.append(current)
    return "_".join(words) or "tool_execution_error"


def normalize_error(
    value: Any = None,
    *,
    code: str | None = None,
    message: str | None = None,
    retryable: bool | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert legacy errors or exceptions to the canonical error envelope."""

    legacy_context: dict[str, Any] = {}
    legacy_details: dict[str, Any] = {}
    resolved_code = code
    resolved_message = message
    resolved_retryable = retryable

    if isinstance(value, BaseException):
        resolved_code = resolved_code or _exception_code(value)
        resolved_message = resolved_message or str(value) or type(value).__name__
    elif isinstance(value, Mapping):
        raw_error = value.get("error")
        legacy_context = {
            str(key): item
            for key, item in value.items()
            if key not in {"ok", "data", "error"}
        }
        if isinstance(raw_error, Mapping):
            resolved_code = resolved_code or str(
                raw_error.get("code")
                or raw_error.get("type")
                or "tool_execution_error"
            )
            resolved_message = resolved_message or str(
                raw_error.get("message") or "Tool execution failed."
            )
            if resolved_retryable is None:
                resolved_retryable = bool(raw_error.get("retryable", False))
            legacy_details = {
                str(key): item
                for key, item in raw_error.items()
                if key not in {"code", "type", "message", "retryable"}
            }
        elif raw_error is not None:
            resolved_message = resolved_message or str(raw_error)

    merged_details = {**legacy_details, **dict(details or {})}
    if legacy_context:
        merged_details["context"] = legacy_context

    return {
        "ok": False,
        "data": None,
        "error": {
            "code": resolved_code or "tool_execution_error",
            "message": resolved_message or "Tool execution failed.",
            "retryable": bool(resolved_retryable),
            "details": merged_details,
        },
    }


def result_meta(tool_name: str | None) -> dict[str, str]:
    """Metadata placed in MCP ``_meta``, not in the business payload."""

    return {
        "contract": MCP_CONTRACT_NAME,
        "contractVersion": MCP_CONTRACT_VERSION,
        "tool": str(tool_name or "unknown"),
    }


def result_summary(data: Any, *, ok: bool, message: str | None = None) -> str:
    """Build bounded human content while full data stays structured."""

    if not ok:
        return message or "Tool execution failed. See structuredContent.error."

    count: int | None = None
    label = "item"
    if isinstance(data, Mapping):
        for key, singular in (
            ("results", "result"),
            ("passages", "passage"),
            ("items", "item"),
            ("ids", "ID"),
        ):
            candidate = data.get(key)
            if isinstance(candidate, (list, tuple)):
                count = len(candidate)
                label = singular
                break
    elif isinstance(data, (list, tuple)):
        count = len(data)

    if count is None:
        return "Success. Read structuredContent.data."
    suffix = label if count == 1 else f"{label}s"
    return f"Success: {count} {suffix}. Read structuredContent.data."


__all__ = [
    "MCP_CONTRACT_NAME",
    "MCP_CONTRACT_VERSION",
    "normalize_error",
    "normalize_success",
    "result_meta",
    "result_summary",
]

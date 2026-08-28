"""Shared protocol-boundary contract for Cortex MCP tool results.

Business payloads stay tool-specific inside ``data``.  The wire-facing
success/error shape is deliberately small and identical for graph_mcp and
mind_mcp so clients never have to infer whether a dict is data or an error.
"""

from __future__ import annotations

from typing import Any, Mapping


MCP_CONTRACT_NAME = "cortex.mcp.tool-result"
MCP_CONTRACT_VERSION = "1.0"

_ERROR_CODE_ALIASES = {
    "unsupported_capability": "capability_unavailable",
    "value_error": "invalid_parameters",
    "type_error": "invalid_parameters",
    "lookup_error": "collection_unavailable",
    "project_not_registered_error": "project_not_registered",
}

# These fields describe server internals or duplicate information available
# from discovery tools.  They are useful in logs and capability inspectors,
# but make ordinary tool errors unstable and unnecessarily large.
_INTERNAL_ERROR_DETAIL_KEYS = {
    "accepted_params",
    "available_labels",
    "available_relationships",
    "capability",
    "capability_diagnostics",
    "context",
    "example",
    "next_step",
    "query_engine",
    "received_params",
    "required_params",
    "supported_aliases",
    "supported_parsers",
    "tool",
}


def _canonical_error_code(value: Any) -> str:
    raw = str(value or "tool_execution_error").strip().lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    return _ERROR_CODE_ALIASES.get(normalized, normalized)


def normalize_success(data: Any) -> dict[str, Any]:
    """Return the canonical success envelope without copying ``data``."""

    return {"ok": True, "data": data, "error": None}


def _exception_code(exc: BaseException) -> str:
    name = type(exc).__name__
    stable_codes = {
        "ValueError": "invalid_parameters",
        "TypeError": "invalid_parameters",
        "LookupError": "collection_unavailable",
        "ProjectNotRegisteredError": "project_not_registered",
        "TimeoutError": "storage_unavailable",
        "ConnectionError": "storage_unavailable",
    }
    if name in stable_codes:
        return stable_codes[name]
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


def _non_empty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _sanitized_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep intentional details while excluding catalog-sized diagnostics."""

    return {
        str(key): item
        for key, item in (details or {}).items()
        if str(key) not in _INTERNAL_ERROR_DETAIL_KEYS and _non_empty(item)
    }


def _compact_legacy_details(
    error_code: str,
    raw_error: Mapping[str, Any],
    outer: Mapping[str, Any],
) -> dict[str, Any]:
    """Extract only stable, actionable fields from a legacy error payload.

    Legacy graph errors carry parameter help plus complete provider schemas.
    Those catalogs belong in discovery/inspection tools, not every failed
    response.  This boundary deliberately uses an allowlist instead of
    copying unknown fields.
    """

    compact: dict[str, Any] = {}
    embedded_details = raw_error.get("details")
    if isinstance(embedded_details, Mapping):
        compact.update(_sanitized_details(embedded_details))
    elif isinstance(embedded_details, list) and embedded_details:
        compact["violations"] = embedded_details

    if error_code == "capability_unavailable":
        capability = outer.get("capability")
        diagnostics = outer.get("capability_diagnostics")
        capability = capability if isinstance(capability, Mapping) else {}
        diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}

        parser = raw_error.get("parser_type") or capability.get("requested_parser")
        missing_relationships = (
            raw_error.get("missing_relationships")
            or raw_error.get("missing_required_relationships")
            or diagnostics.get("missing_required_relationships")
        )
        missing_labels = (
            raw_error.get("missing_labels")
            or raw_error.get("missing_required_labels")
            or diagnostics.get("missing_required_labels")
        )
        if _non_empty(parser):
            compact["parser"] = parser
        if _non_empty(missing_relationships):
            compact["missing_relationships"] = missing_relationships
        if _non_empty(missing_labels):
            compact["missing_labels"] = missing_labels

    if error_code == "unsupported_parser":
        parser = raw_error.get("parser_type")
        if _non_empty(parser):
            compact["parser"] = parser

    missing_parameters = raw_error.get("missing_required_params")
    if _non_empty(missing_parameters):
        compact["missing_parameters"] = missing_parameters

    # Small operational fields let clients decide whether/how to retry while
    # avoiding the full backend state snapshot.
    for key in (
        "retry_after_ms",
        "correlation_id",
        "capacity",
        "accepted_limit",
        "project_id",
        "collection",
        "parameter",
        "field",
    ):
        item = raw_error.get(key)
        if _non_empty(item):
            compact[key] = item

    return compact


def normalize_error(
    value: Any = None,
    *,
    code: str | None = None,
    message: str | None = None,
    retryable: bool | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert legacy errors or exceptions to the canonical error envelope."""

    legacy_details: dict[str, Any] = {}
    resolved_code = code
    resolved_message = message
    resolved_retryable = retryable

    if isinstance(value, BaseException):
        resolved_code = resolved_code or _exception_code(value)
        resolved_message = resolved_message or str(value) or type(value).__name__
        if resolved_retryable is None and isinstance(
            value, (TimeoutError, ConnectionError)
        ):
            resolved_retryable = True
    elif isinstance(value, Mapping):
        raw_error = value.get("error")
        if isinstance(raw_error, Mapping):
            resolved_code = resolved_code or _canonical_error_code(
                raw_error.get("code")
                or raw_error.get("type")
                or "tool_execution_error"
            )
            resolved_message = resolved_message or str(
                raw_error.get("message") or "Tool execution failed."
            )
            if resolved_retryable is None:
                resolved_retryable = bool(raw_error.get("retryable", False))
            legacy_details = _compact_legacy_details(
                _canonical_error_code(resolved_code), raw_error, value
            )
        elif raw_error is not None:
            resolved_message = resolved_message or str(raw_error)

    merged_details = {**legacy_details, **_sanitized_details(details)}

    return {
        "ok": False,
        "data": None,
        "error": {
            "code": _canonical_error_code(resolved_code),
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

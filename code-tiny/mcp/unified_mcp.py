from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import json
import os
import signal
import sys

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv

from fastapi import Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool

load_dotenv()  # Load environment variables from .env file if present


def _load_module(module_name: str, file_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module '{module_name}' from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROOT_DIR = Path(__file__).resolve().parent

_mcp_dir = str(ROOT_DIR)
if _mcp_dir not in sys.path:
    sys.path.insert(0, _mcp_dir)

android_backend = _load_module("android_backend", ROOT_DIR / "android" / "android_mcp.py")
cplus_backend = _load_module("cplus_backend", ROOT_DIR / "cplus" / "cplus_mcp.py")
fast_backend = _load_module("fast_backend", ROOT_DIR / "fastmcp_server.py")

from tool_metadata import build_catalog  # noqa: E402
from framework_registry import (  # noqa: E402
    CAPABILITY_CONTRACT_VERSION,
    capability_catalog,
    capability_for_parser,
    default_relationships,
    evaluate_capability_schema,
    framework_for_parser,
    parser_aliases,
    query_engine_for_backend,
    servlet_active_generation_predicate,
)
from tools.common.project_scope import project_id_lookup_key  # noqa: E402
from tools.common.project_registry import (  # noqa: E402
    ProjectNotRegisteredError,
    ProjectRegistryError,
    resolve_project_targets,
)
from tools.graph import GraphDriverFactory, GraphProvider  # noqa: E402

_UNIFIED_TOOL_NAMES: frozenset = frozenset(
    {
        "search_functions",
        "search_by_code",
        "get_symbol",
        "get_node_details",
        "query_subgraph",
        "find_paths",
        "find_path_between_module",
        "listup_symbols_matching_file_path",
        "listup_class_matching_path",
        "list_up_entrypoint",
        "trace_flow",
        "trace_flow_between_module",
        "find_screen_workflows",
        "explore_graph",
        "semantic_search",
        "get_ipc_message",
        "list_possible_calls",
        "annotate_node",
        "list_databases",
        "list_qdrant_collections",
        "list_parsers",
        "inspect_parser_capabilities",
        "list_mcp_functions",
        "compute_scc",
        "topological_sort",
        "plan_dependency_order",
        "plan_file_dependency_order",
        "plan_function_dependency_order",
        "reconstruct_flow",
        "find_callers_of_endpoint",
        "get_api_call_chain",
        "analyze_workflow_impact",
        "find_workflows_containing",
        "get_project_modules",
        "get_public_apis",
        "get_endpoints",
        "get_module_architecture_summary",
        "get_project_special_files",
        "get_framework_context",
    }
)
_unified_catalog = build_catalog(_UNIFIED_TOOL_NAMES)
_CATALOG_BY_NAME: Dict[str, Dict[str, Any]] = {
    item.get("name", ""): item for item in _unified_catalog if item.get("name")
}
_PARAMETER_GUIDELINES: Dict[str, Any] = {
    "always_call_first": "list_mcp_functions",
    "rules": [
        "Use exact parameter names from tool metadata; avoid inventing aliases.",
        "Send required fields explicitly on every call.",
        "Pass parser_type on every call to select a query profile (see list_parsers for aliases).",
        "When list-like params are accepted, prefer arrays over comma-separated strings.",
        "On error.invalid_parameters, follow required_params + example and retry once.",
    ],
}
_MCP_FUNCTIONS_JSON: str = json.dumps(
    {
        "total_count": len(_unified_catalog),
        "parameter_guidelines": _PARAMETER_GUIDELINES,
        "functions": _unified_catalog,
    },
    ensure_ascii=False,
)


MCP_NAME = os.getenv("MCP_SERVER_NAME", "Project Call Graph Unified")

INSTRUCTIONS = """Unified MCP for multi-language code graphs (single server/port).

Discovery first:
- Call `list_mcp_functions` at session start to get the exact, current tool set and parameter docs.
- Call `list_parsers` to inspect canonical profiles, aliases, query engines, dimensional support, and feature gates.

Routing:
- Pass `parser_type` on every call to select a query profile (stateless — no session defaults).
- Project-context tools (get_project_modules, get_public_apis, get_endpoints, get_module_architecture_summary, get_project_special_files, get_framework_context) require `parser_type`.
- Parser mapping:
  - android/android-kotlin/kotlin-android -> android_graph query engine
  - Other registered profiles -> graph_generic query engine with parser-aware labels and traversal defaults
- A parser profile describes graph capabilities; it does not imply a separate MCP server.
- Provider schema filtering is reported through capability_diagnostics when relationships are omitted.

Tool families available in unified MCP:
- Symbol/graph queries: search/get/subgraph/paths/module-path/entrypoint
- Flow and workflow analysis: trace_flow, find_screen_workflows, reconstruct_flow, analyze_workflow_impact, find_workflows_containing
- Dependency planning: compute_scc, topological_sort, plan_dependency_order, plan_file_dependency_order, plan_function_dependency_order
- Fullstack bridge analysis: find_callers_of_endpoint, get_api_call_chain
- Semantic/vector utilities: explore_graph, semantic_search, list_qdrant_collections


Input contract:
- Tools accept typed top-level parameters.
- Empty string values are treated as "not provided".
"""

mcp_server = FastMCP(
    name=MCP_NAME,
    version="1.2.0",
    instructions=INSTRUCTIONS,
)

@mcp_server.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    return JSONResponse({"status": "healthy", "service": "fastmcp-server"})



@dataclass(frozen=True)
class BackendInfo:
    name: str
    module: Any


BACKENDS: Dict[str, BackendInfo] = {
    "android": BackendInfo(name="android", module=android_backend),
    "cplus": BackendInfo(name="cplus", module=cplus_backend),
}

DEFAULT_BACKEND = os.environ.get("MCP_UNIFIED_DEFAULT_BACKEND", "cplus").strip().lower() or "cplus"
if DEFAULT_BACKEND not in BACKENDS:
    DEFAULT_BACKEND = "cplus"

PARSER_ALIASES_ANDROID = set(parser_aliases("android"))
PARSER_ALIASES_CPLUS = set(parser_aliases("cplus"))

# ``active_project`` was removed per the unified ingest/query contract plan.
# Callers must pass ``parser_type`` and ``project_id`` explicitly on every
# tool call. See docs/PROJECT_REGISTRY.md for the new contract.


def _coerce_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict.")
    return payload


def _merge_payload(payload: Optional[Dict[str, Any]], values: Dict[str, Any]) -> Dict[str, Any]:
    merged = {key: value for key, value in values.items() if value is not None}
    merged.update(_coerce_payload(payload))
    return merged


def _normalize_string_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text or ";" in text:
            parts = [part.strip() for part in text.replace(";", ",").split(",")]
            return [part for part in parts if part]
        return [text]
    if isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items
    text = str(value).strip()
    return [text] if text else []


def _coerce_list_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(payload)

    alias_pairs = [
        ("module", "modules"),
        ("source_module", "source_modules"),
        ("target_module", "target_modules"),
        ("class_name", "class_names"),
        ("file_path", "file_paths"),
        ("relationship_types", "rel_types"),
    ]
    for src, dest in alias_pairs:
        if dest not in merged and src in merged:
            merged[dest] = merged[src]

    for key in ("modules", "source_modules", "target_modules", "class_names", "file_paths", "rel_types"):
        if key in merged:
            normalized = _normalize_string_list(merged.get(key))
            if normalized is not None:
                merged[key] = normalized

    if "node_ids" in merged:
        normalized_ids = _normalize_string_list(merged.get("node_ids"))
        if normalized_ids is not None:
            merged["node_ids"] = normalized_ids

    return merged


def _normalize_parser_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _resolve_backend_name(parser_type: Optional[str]) -> str:
    parser = _normalize_parser_type(parser_type)
    capability = capability_for_parser(parser)
    if capability:
        return capability.backend
    return DEFAULT_BACKEND


def _capability_summary(parser_type: Optional[str], backend_name: str) -> Dict[str, Any]:
    parser = _normalize_parser_type(parser_type)
    capability = capability_for_parser(parser)
    if capability:
        return {
            "requested_parser": parser,
            "canonical_parser": capability.name,
            "query_engine": query_engine_for_backend(capability.backend),
            "support_level": capability.support_level,
            "support": dict(capability.support),
            "features": sorted(capability.features),
            "labels": sorted(capability.labels),
            "searchable_properties": list(capability.searchable_properties),
        }
    return {
        "requested_parser": parser,
        "canonical_parser": None,
        "query_engine": query_engine_for_backend(backend_name),
        "support_level": "generic",
        "support": {
            "symbols": "generic",
            "calls": "generic",
            "endpoints": "none",
            "database": "none",
        },
        "features": [],
        "labels": [],
        "searchable_properties": [],
        "warning": (
            f"Parser '{parser}' is not registered; generic query behavior is being used."
            if parser else "No parser selected; generic query behavior is being used."
        ),
    }


async def _resolve_direct_capability_context(
    tool_name: str,
    parser_type: Optional[str],
    db: Optional[str],
    required_relationships: Optional[Iterable[str]] = None,
    required_labels: Optional[Iterable[str]] = None,
    error_payload: Optional[Dict[str, Any]] = None,
) -> Tuple[
    Optional[str], List[str], Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]
]:
    selected_parser = (
        _normalize_parser_type(parser_type)
        or _normalize_parser_type(parser_type)
    )
    backend_name = _resolve_backend_name(selected_parser)
    routing = _capability_summary(selected_parser, backend_name)
    capability = capability_for_parser(selected_parser)
    if selected_parser and capability is None:
        error = _unsupported_parser_result(
            tool_name,
            {"parser_type": selected_parser, **(error_payload or {})},
            selected_parser,
        )
        return selected_parser, [], routing, None, error
    if not selected_parser and (required_relationships or required_labels):
        required = list(dict.fromkeys(required_relationships or ()))
        labels_required = list(dict.fromkeys(required_labels or ()))
        error = _build_tool_error(
            tool_name,
            {"parser_type": selected_parser, **(error_payload or {})},
            ValueError(
                f"No parser selected. '{tool_name}' requires a parser profile "
                f"(labels={labels_required}, relationships={required}). "
                f"Pass parser_type on this call — see list_parsers for supported values."
            ),
            backend_name=backend_name,
        )
        error["error"]["type"] = "capability_unavailable"
        error["capability"] = routing
        error["error"]["next_step"] = (
            "Call list_parsers to see supported parsers, then retry with "
            "parser_type set to one that supports this tool."
        )
        return selected_parser, [], routing, None, error
    relationships: List[str] = []
    diagnostics: Optional[Dict[str, Any]] = None
    if capability and backend_name != "android":
        db_candidates = cplus_backend._resolve_db_candidates(db or None)
        relationships, diagnostics = await cplus_backend._resolve_rel_types_with_diagnostics(
            list(default_relationships(capability.name, tool_name)),
            selected_parser,
            db_candidates,
            explicit=False,
        )
        routing["default_relationships_applied"] = relationships
        required = list(dict.fromkeys(required_relationships or ()))
        missing_required = [value for value in required if value not in relationships]
        labels_required = list(dict.fromkeys(required_labels or ()))
        available_labels = (
            await cplus_backend._list_node_labels(db_candidates)
            if labels_required else []
        )
        label_schema_available = available_labels is not None
        missing_labels = (
            [value for value in labels_required if value not in set(available_labels or [])]
            if label_schema_available else []
        )
        schema_available = diagnostics.get("schema_status") == "available" if diagnostics else False
        if diagnostics is not None:
            diagnostics["required_relationships"] = required
            diagnostics["missing_required_relationships"] = missing_required if schema_available else []
            diagnostics["required_labels"] = labels_required
            diagnostics["available_labels"] = available_labels or []
            diagnostics["missing_required_labels"] = missing_labels
            diagnostics["label_schema_status"] = (
                "available" if label_schema_available else "unavailable"
            )
        label_gate_failed = bool(
            labels_required and (not label_schema_available or missing_labels)
        )
        relationship_gate_failed = bool(
            required and (not schema_available or missing_required)
        )
        if not relationships or relationship_gate_failed or label_gate_failed:
            missing_text = (
                " Missing required relationships: " + ", ".join(missing_required) + "."
                if missing_required else ""
            )
            missing_label_text = (
                " Missing required labels: " + ", ".join(missing_labels) + "."
                if missing_labels else ""
            )
            inspection_text = (
                " Provider label schema could not be inspected."
                if labels_required and not label_schema_available else ""
            )
            relationship_inspection_text = (
                " Provider relationship schema could not be inspected."
                if required and not schema_available else ""
            )
            error = _build_tool_error(
                tool_name,
                {
                    "parser_type": selected_parser,
                    **(error_payload or {}),
                },
                ValueError(
                    f"Parser '{selected_parser}' cannot execute '{tool_name}' on the active provider."
                    + missing_text + missing_label_text + inspection_text
                    + relationship_inspection_text
                ),
                backend_name=backend_name,
            )
            error["error"]["type"] = "capability_unavailable"
            error["capability"] = routing
            error["capability_diagnostics"] = diagnostics
            return selected_parser, relationships, routing, diagnostics, error
    return selected_parser, relationships, routing, diagnostics, None


def _relationship_pattern(relationships: List[str], fallback: str = "CALLS") -> str:
    values = relationships or [fallback]
    return "|".join(dict.fromkeys(values))


def _apply_unified_defaults(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(payload)
    # Both branches below used to fall back to ``active_project``. Per the
    # unified ingest/query contract plan, that state has been removed —
    # callers must pass ``parser_type`` and ``db`` (or ``project_id``)
    # explicitly. We leave the merged dict untouched so missing values
    # surface as the env-default full-search path downstream.
    return merged


def _unwrap_tool_callable(obj: Any) -> Any:
    if obj is None:
        return None
    fn = getattr(obj, "fn", None)
    if callable(fn):
        return fn
    if callable(obj):
        return obj
    return None


def _catalog_inputs(tool_name: str) -> List[Dict[str, Any]]:
    item = _CATALOG_BY_NAME.get(tool_name, {})
    inputs = item.get("inputs")
    if isinstance(inputs, list):
        return [entry for entry in inputs if isinstance(entry, dict)]
    return []


def _required_params(tool_name: str) -> List[str]:
    required: List[str] = []
    for entry in _catalog_inputs(tool_name):
        if entry.get("required"):
            name = str(entry.get("name") or "").strip()
            if name:
                required.append(name)
    return required


def _accepted_params(tool_name: str) -> List[str]:
    accepted: List[str] = []
    for entry in _catalog_inputs(tool_name):
        name = str(entry.get("name") or "").strip()
        if name:
            accepted.append(name)
    return accepted


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _missing_required_params(tool_name: str, payload: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for name in _required_params(tool_name):
        if _is_missing_value(payload.get(name)):
            missing.append(name)
    return missing


def _error_type_from_exception(exc: Exception, missing_required: List[str]) -> str:
    if missing_required:
        return "missing_required_parameters"
    if isinstance(exc, ValueError):
        return "invalid_parameters"
    if isinstance(exc, TypeError):
        return "invalid_parameters"
    return "tool_execution_error"


def _build_tool_error(
    tool_name: str,
    payload: Dict[str, Any],
    exc: Exception,
    backend_name: Optional[str] = None,
) -> Dict[str, Any]:
    missing_required = _missing_required_params(tool_name, payload)
    entry = _CATALOG_BY_NAME.get(tool_name, {})
    received = sorted([key for key in payload.keys() if payload.get(key) is not None])
    return {
        "ok": False,
        "query_engine": query_engine_for_backend(backend_name),
        "error": {
            "type": _error_type_from_exception(exc, missing_required),
            "tool": tool_name,
            "query_engine": query_engine_for_backend(backend_name),
            "message": str(exc),
            "missing_required_params": missing_required,
            "required_params": _required_params(tool_name),
            "accepted_params": _accepted_params(tool_name),
            "received_params": received,
            "example": entry.get("example"),
            "next_step": "Call list_mcp_functions and retry with exact parameter names.",
        },
    }


def _unsupported_parser_result(
    tool_name: str,
    payload: Dict[str, Any],
    parser_type: str,
) -> Dict[str, Any]:
    parser = _normalize_parser_type(parser_type) or ""
    error = _build_tool_error(
        tool_name,
        payload,
        ValueError(f"Parser '{parser}' is not registered."),
        backend_name=DEFAULT_BACKEND,
    )
    error["error"].update(
        {
            "type": "unsupported_parser",
            "parser_type": parser,
            "supported_parsers": sorted(capability["canonical_parser"] for capability in capability_catalog()),
            "supported_aliases": sorted(parser_aliases()),
            "next_step": "Call list_parsers and retry with a canonical parser or registered alias.",
        }
    )
    return error


def _coerce_error_result(tool_name: str, payload: Dict[str, Any], result: Any, backend_name: str) -> Optional[Dict[str, Any]]:
    if isinstance(result, dict) and isinstance(result.get("error"), str):
        err = ValueError(result.get("error") or "Invalid tool arguments")
        normalized = _build_tool_error(tool_name, payload, err, backend_name=backend_name)
        if result.get("error_type"):
            normalized["error"]["type"] = str(result["error_type"])
        details = result.get("details")
        if isinstance(details, list):
            normalized["error"]["details"] = details
        if isinstance(result.get("capability_diagnostics"), dict):
            normalized["capability_diagnostics"] = result["capability_diagnostics"]
        return normalized
    return None


def _parse_positive_int(raw: Any, param_name: str) -> Tuple[Optional[int], Optional[str]]:
    if raw is None:
        return None, None
    if isinstance(raw, bool):
        return None, f"{param_name} must be a positive integer."
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            return None, f"{param_name} must be a positive integer."
        value = int(raw)
    else:
        text = str(raw).strip()
        if not text:
            return None, None
        if not text.isdigit():
            return None, f"{param_name} must be a positive integer."
        value = int(text)
    if value <= 0:
        return None, f"{param_name} must be greater than 0."
    return value, None


# ── Pass-through proxy registration ──────────────────────────────────────────
# These sets drive both the dynamic registration below and the middleware
# routing.  Tools in ``_PLANNER_TOOL_NAMES`` are dispatched to ``fast_backend``
# via ``_dispatch_planner_tool``; everything else is dispatched to whichever
# backend ``_resolve_backend_name`` picks (cplus / android).
_PLANNER_TOOL_NAMES: frozenset = frozenset(
    {
        "compute_scc",
        "topological_sort",
        "plan_dependency_order",
        "plan_file_dependency_order",
        "plan_function_dependency_order",
    }
)

# Tools whose wrapper only marshals args and calls ``_dispatch_tool`` /
# ``_dispatch_planner_tool`` — i.e. they have no business logic of their own.
# These are dynamically registered from the backend callable so their schema
# always matches the backend (no more drift).
_PROXIED_TOOL_NAMES: frozenset = frozenset(
    {
        # Planner set → fast_backend
        "compute_scc",
        "topological_sort",
        "plan_dependency_order",
        "plan_file_dependency_order",
        "plan_function_dependency_order",
        # cplus_backend → _dispatch_tool
        "list_databases",
        "list_qdrant_collections",
        "annotate_node",
        "semantic_search",
        "trace_flow_between_module",
        "trace_flow",
        "find_screen_workflows",
        "list_up_entrypoint",
        "listup_class_matching_path",
        "listup_symbols_matching_file_path",
        "find_path_between_module",
        "find_paths",
        "query_subgraph",
        "get_node_details",
        "list_possible_calls",
        "get_symbol",
        "search_by_code",
        "search_functions",
        "get_ipc_message",
    }
)


def _resolve_proxy_backend_module(tool_name: str) -> Any:
    """Return the backend module whose ``tool_<name>`` will back this proxy."""
    if tool_name in _PLANNER_TOOL_NAMES:
        return fast_backend
    # All non-planner proxied tools currently route through the parser-based
    # dispatch (``_dispatch_tool``), which itself looks at the
    # ``BACKENDS`` dict.  Default to ``cplus_backend`` for the introspected
    # callable signature only — the actual runtime routing still happens
    # inside ``_dispatch_tool`` via ``_resolve_backend_name``.
    return BACKENDS["cplus"].module


class _ProxyMiddleware(Middleware):
    """Routes ``tools/call`` for proxied tools to the existing dispatch helpers.

    FastMCP's normal validation pipeline has already coerced ``arguments``
    against the registered tool's JSON schema before this middleware runs, so
    any field accepted by the backend's signature is also accepted here.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> Any:
        message = context.message
        name = getattr(message, "name", None)
        # ``message.arguments`` is a Mapping on CallToolRequestParams
        arguments = dict(getattr(message, "arguments", {}) or {})
        if name in _PROXIED_TOOL_NAMES:
            if name in _PLANNER_TOOL_NAMES:
                return await _dispatch_planner_tool(name, arguments)
            return await _dispatch_tool(name, arguments)
        return await call_next(context)


_proxy_middleware = _ProxyMiddleware()


def _register_proxy_tools() -> None:
    """Dynamically register proxied tools from their backend callables.

    Each proxied tool's schema is derived from the backend function signature
    by FastMCP's ``Tool.from_function`` machinery — so adding or removing a
    parameter in the backend is automatically reflected here on next reload.
    """
    for name in _PROXIED_TOOL_NAMES:
        backend_module = _resolve_proxy_backend_module(name)
        raw = getattr(backend_module, f"tool_{name}", None)
        fn = _unwrap_tool_callable(raw)
        if fn is None:
            # Surface a clear error at import time rather than at first call.
            raise RuntimeError(
                f"Cannot proxy tool {name!r}: backend module "
                f"{backend_module.__name__!r} has no callable tool_{name}."
            )
        catalog_entry = _CATALOG_BY_NAME.get(name, {})
        description = catalog_entry.get("description") or f"Proxied to {backend_module.__name__}"
        mcp_server.add_tool(
            Tool.from_function(
                fn,
                name=name,
                description=description,
                output_schema=None,
            )
        )


# Attach the proxy middleware and register proxied tools now that
# ``_dispatch_tool`` / ``_dispatch_planner_tool`` / ``_CATALOG_BY_NAME`` are
# all defined.
mcp_server.middleware.append(_proxy_middleware)
_register_proxy_tools()


async def _dispatch_tool(tool_name: str, payload: Dict[str, Any]) -> Any:
    merged = _apply_unified_defaults(payload)
    merged = _coerce_list_fields(merged)
    selected_parser = _normalize_parser_type(merged.get("parser_type"))
    capability = capability_for_parser(selected_parser)
    if selected_parser and capability is None:
        return _unsupported_parser_result(tool_name, merged, selected_parser)
    backend_name = _resolve_backend_name(selected_parser)
    framework = framework_for_parser(merged.get("parser_type"))
    relationships_applied: List[str] = []
    if capability and backend_name != "android":
        relationship_defaults = list(default_relationships(capability.name, tool_name))
        if tool_name in {
            "query_subgraph", "find_paths", "trace_flow",
            "find_path_between_module", "trace_flow_between_module",
            "find_screen_workflows",
        }:
            if not merged.get("relationship_types") and not merged.get("rel_types"):
                merged["relationship_types"] = relationship_defaults
                merged["rel_types"] = relationship_defaults
                merged["_capability_default_relationships"] = True
                relationships_applied = relationship_defaults
        if tool_name == "semantic_search" and not merged.get("graph_rel_types"):
            merged["graph_rel_types"] = ",".join(relationship_defaults)
            merged["_capability_default_relationships"] = True
            relationships_applied = relationship_defaults
            if framework and "expand_graph" not in merged:
                merged["expand_graph"] = True
    routing = _capability_summary(merged.get("parser_type"), backend_name)
    routing["default_relationships_applied"] = relationships_applied
    backend = BACKENDS[backend_name]
    fn = _unwrap_tool_callable(getattr(backend.module, f"tool_{tool_name}", None))
    if fn is None:
        return _build_tool_error(
            tool_name,
            merged,
            ValueError(
                f"Tool '{tool_name}' is not available in query engine "
                f"'{query_engine_for_backend(backend_name)}'."
            ),
            backend_name=backend_name,
        )
    try:
        result = await fn(payload=merged)
    except Exception as exc:
        return _build_tool_error(tool_name, merged, exc, backend_name=backend_name)
    normalized_error = _coerce_error_result(tool_name, merged, result, backend_name)
    if normalized_error is not None:
        normalized_error["capability"] = routing
        return normalized_error
    if isinstance(result, dict):
        result.setdefault("ok", True)
        result.pop("backend", None)
        result.setdefault("query_engine", query_engine_for_backend(backend_name))
        result.setdefault("capability", routing)
    return result


@mcp_server.tool(
    name="list_mcp_functions",
    description="List all available MCP functions/tools with their descriptions, inputs (parameters), and outputs.",
    output_schema=None,
)
async def tool_list_mcp_functions() -> str:
    return _MCP_FUNCTIONS_JSON


@mcp_server.tool(name="list_parsers", description="List available parser types supported by unified MCP.", output_schema=None)
async def tool_list_parsers() -> Dict[str, Any]:
    capabilities = list(capability_catalog())
    return {
        "parsers": sorted(parser_aliases()),
        "capabilities": capabilities,
        "capability_contract_version": CAPABILITY_CONTRACT_VERSION,
        "default_query_engine": query_engine_for_backend(DEFAULT_BACKEND),
        # ``active_parser_type`` is always None now — the stateful default has
        # been removed. Callers must pass parser_type explicitly per call.
        "active_parser_type": None,
        "active_capability": _capability_summary(None, None),
    }


@mcp_server.tool(
    name="inspect_parser_capabilities",
    description=(
        "Compare a parser profile's advertised support with labels and relationships "
        "observed in the active graph provider."
    ),
    output_schema=None,
)
async def tool_inspect_parser_capabilities(
    parser_type: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    _db = _resolve_graph_database(project_id=project_id or None)
    selected_parser = (
        _normalize_parser_type(parser_type)
        or _normalize_parser_type(parser_type)
    )
    if not selected_parser:
        return _build_tool_error(
            "inspect_parser_capabilities",
            {"parser_type": parser_type, "db": _db},
            ValueError("parser_type is required when no project profile is active."),
        )
    capability = capability_for_parser(selected_parser)
    if capability is None:
        return _unsupported_parser_result(
            "inspect_parser_capabilities",
            {"parser_type": selected_parser, "db": _db},
            selected_parser,
        )

    db_candidates = cplus_backend._resolve_db_candidates(project_id)
    labels, relationships = await asyncio.gather(
        cplus_backend._list_node_labels(db_candidates),
        cplus_backend._list_relationship_types(db_candidates),
    )
    evaluation = evaluate_capability_schema(
        capability,
        available_labels=labels,
        available_relationships=relationships,
    )
    dimensions = evaluation["dimensions"]
    effective_support = {
        dimension: details["effective"]
        for dimension, details in dimensions.items()
    }
    unavailable_dimensions = [
        dimension for dimension, details in dimensions.items()
        if details["advertised"] != "none" and details["observed"] == "unavailable"
    ]
    unknown_dimensions = [
        dimension for dimension, details in dimensions.items()
        if details["advertised"] != "none" and details["observed"] == "unknown"
    ]
    recommended_action = (
        "inspect_provider_schema"
        if unknown_dimensions else "run_incremental_sync"
        if unavailable_dimensions else "none"
    )
    return {
        "ok": True,
        "requested_parser": selected_parser,
        "canonical_parser": capability.name,
        "query_engine": query_engine_for_backend(capability.backend),
        "db": _db,
        "advertised_support": dict(capability.support),
        "effective_support": effective_support,
        "schema_status": evaluation["schema_status"],
        "schema_fingerprint": evaluation["schema_fingerprint"],
        "contract_version": evaluation["contract_version"],
        "dimensions": dimensions,
        "available_labels": sorted(labels or []),
        "available_relationships": sorted(relationships or []),
        "unavailable_dimensions": unavailable_dimensions,
        "unknown_dimensions": unknown_dimensions,
        "recommended_action": recommended_action,
    }



# Define annotate_node separately with specific parameters
# Define semantic_search separately with specific parameters
# Define trace_flow_between_module separately with specific parameters
# Define trace_flow separately with specific parameters
# Define listup_class_matching_path separately with specific parameters
# Define listup_symbols_matching_file_path separately with specific parameters

# Define find_path_between_module separately with specific parameters
# Define find_paths separately with specific parameters
# Define query_subgraph separately with specific parameters
# Define get_node_details separately with specific parameters
# Define list_possible_calls separately with specific parameters
# Define search_by_code separately with specific parameters
# Define search_functions separately with specific parameters
# Define get_ipc_message separately with fewer parameters
# ── Graph Explorer — Intent-Aware Semantic Search ─────────────────────────────
# Language-agnostic, multi-strategy retrieval: semantic + keyword + graph expansion.
# Accepts natural language / paragraphs (EN + VI).

@mcp_server.tool(
    name="explore_graph",
    description=(
        "Intent-aware, multi-strategy Graph Explorer search. "
        "Accepts natural language, paragraphs, or vague descriptions (English or Vietnamese). "
        "Extracts entities, domain signals, and actions from the query, then fuses "
        "semantic vector search + BM25 keyword search + call-graph expansion. "
        "Returns explainable ranked nodes with per-node WHY reasons, entry points, "
        "related graph paths, and overall confidence score."
    ),
    output_schema=None,
)
async def tool_explore_graph(
    query:      str  = "",
    mode:       str  = "hybrid",
    top_k:      int | float | str  = "",
    collection: str  = "",
    debug:      bool = False,
    parser_type: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """
    Intent-aware graph search combining semantic + keyword + graph expansion.

    Args:
        query:      Natural language text (keyword, sentence, or multi-line paragraph).
        mode:       "semantic" | "hybrid" (default) | "graph_expanded"
        top_k:      Max matched nodes (default 10).
        db:         Graph database or graph name override.
        collection: Qdrant collection name override.
        debug:      Include per-signal score breakdown in each node.
        project_id: Restrict every retrieval and expansion stage to one project.

    Returns:
        {
          "matched_nodes":  [...],   # top-K nodes with score + reason
          "entry_points":   [...],   # high-importance / exported nodes
          "related_paths":  [...],   # graph-expanded neighbors
          "explanation":    str,     # human-readable summary
          "confidence":     float,   # 0.0–1.0
          "query_analysis": {...},   # extracted intent / entities / domain_signals
          "mode":           str,
        }
    """
    from services.explore_service import get_explore_service

    q = (query or "").strip()
    if not q:
        return {
            "matched_nodes": [], "entry_points": [], "related_paths": [],
            "explanation": "No query provided.", "confidence": 0.0,
            "query_analysis": {}, "mode": mode,
        }

    parsed_top_k, top_k_error = _parse_positive_int(top_k, "top_k")
    if top_k_error:
        return _build_tool_error(
            "explore_graph",
            {"query": q, "top_k": top_k},
            ValueError(top_k_error),
        )
    k = parsed_top_k or 10
    selected_parser = (
        _normalize_parser_type(parser_type)
        or _normalize_parser_type(parser_type)
    )
    if selected_parser and capability_for_parser(selected_parser) is None:
        return _unsupported_parser_result(
            "explore_graph",
            {"query": q, "parser_type": selected_parser},
            selected_parser,
        )
    backend_name = _resolve_backend_name(selected_parser)
    capability = capability_for_parser(selected_parser)
    relationship_types: Optional[List[str]] = None
    capability_diagnostics: Optional[Dict[str, Any]] = None
    if capability and backend_name != "android" and (mode or "hybrid") != "semantic":
        relationship_types, capability_diagnostics = (
            await cplus_backend._resolve_rel_types_with_diagnostics(
                list(default_relationships(capability.name, "explore_graph")),
                selected_parser,
                cplus_backend._resolve_db_candidates(project_id),
                explicit=False,
            )
        )
        if not relationship_types:
            result = _build_tool_error(
                "explore_graph",
                {"query": q, "parser_type": selected_parser},
                ValueError(
                    f"Parser '{selected_parser}' has no relationships available in the active provider."
                ),
                backend_name=backend_name,
            )
            result["error"]["type"] = "unsupported_capability"
            result["capability"] = _capability_summary(selected_parser, backend_name)
            result["capability_diagnostics"] = capability_diagnostics
            return result
    service = get_explore_service()
    active_db = _resolve_graph_database(project_id=project_id or None) if project_id else None
    result = await service.explore(
        query      = q,
        top_k      = k,
        mode       = mode or "hybrid",
        db         = active_db,
        collection = collection or None,
        debug      = debug,
        graph_rel_types= relationship_types,
        searchable_labels= sorted(capability.labels) if capability else None,
        searchable_properties= list(capability.searchable_properties) if capability else None,
        project_id = project_id or None,
    )
    result.pop("backend", None)
    result["query_engine"] = query_engine_for_backend(backend_name)
    result["capability"] = _capability_summary(selected_parser, backend_name)
    if capability_diagnostics:
        result["capability_diagnostics"] = capability_diagnostics
    return result


# ── Unified Flow Reconstructor ────────────────────────────────────────────────
# This tool is backend-agnostic: it operates on pre-fetched path data from
# find_paths / query_subgraph and reconstructs execution flows per V1.1 spec.

from services.flow_reconstructor import reconstruct_flows  # noqa: E402


@mcp_server.tool(
    name="reconstruct_flow",
    description=(
        "Reconstruct POSSIBLE execution flows from candidate graph paths "
        "(output of find_paths / query_subgraph). Returns grounded, traceable "
        "flows consumable by AI agents for reasoning and impact analysis."
    ),
    output_schema=None,
)
async def tool_reconstruct_flow(
    entry_context_json: str = "",
    paths_json: str = "",
) -> Dict[str, Any]:
    """
    Reconstruct flows from entry_context + candidate paths (Unified Flow Reconstructor V1.1).

    Args:
        entry_context_json: JSON string with keys: type, entry_point,
                            entry_node_id, screen (nullable), trigger (nullable).
        paths_json:         JSON string — array of path objects with nodes and edges.

    Returns:
        {"flows": [...], "uncertainties": [...]}
    """
    import json as _json

    if not entry_context_json or not paths_json:
        return {"flows": [], "uncertainties": ["entry_context_json and paths_json are required"]}

    try:
        entry_context = _json.loads(entry_context_json)
    except (ValueError, TypeError) as exc:
        return {"flows": [], "uncertainties": [f"Invalid entry_context_json: {exc}"]}

    try:
        paths = _json.loads(paths_json)
    except (ValueError, TypeError) as exc:
        return {"flows": [], "uncertainties": [f"Invalid paths_json: {exc}"]}

    if not isinstance(entry_context, dict):
        return {"flows": [], "uncertainties": ["entry_context_json must be a JSON object"]}
    if not isinstance(paths, list):
        return {"flows": [], "uncertainties": ["paths_json must be a JSON array"]}

    return reconstruct_flows(entry_context, paths)


# ── Frontend → Backend API Contract Bridge tools ──────────────────────────────


def _resolve_graph_database(project_id: Optional[str] = None) -> str:
    """Resolve one provider-neutral graph/database name for direct MCP tools.

    Precedence (highest first):
    1. ``project_id`` — resolves through the ProjectRegistry so the reader
       targets the same shard the topology writer filled. An unregistered
       ``project_id`` falls back to the naming convention
       ``code_graph == project_id`` (every project's topology lives in its
       own graph named after the project). This lets callers query any
       FalkorDB graph by ``project_id`` without first registering it in the
       local harness config.
    2. Neither — falls through to env defaults (``FALKORDB_GRAPH`` /
       ``NEO4J_DB`` / ``DEFAULT_GRAPH_DB`` / ``"hyper_graph"``). This is the
       implicit full-search path: omit ``project_id`` to query across every
       project.

    Returns a non-empty graph name string. Never raises for a missing
    ``project_id``; a present but unregistered ``project_id`` resolves to
    itself (the per-project graph convention) rather than the env graph.
    """

    if project_id:
        normalized = project_id_lookup_key(project_id)
        if normalized:
            try:
                targets = resolve_project_targets(project_id)
                return targets.code_graph
            except ProjectNotRegisteredError:
                # Naming convention: code_graph == project_id. Each
                # project's topology is written into its own graph named
                # after the project, so an unregistered project_id still
                # names the right graph rather than silently leaking into
                # the server's env-default graph.
                return project_id

    # Full-search path: resolve from env defaults.
    return (
        os.environ.get("FALKORDB_GRAPH")
        or os.environ.get("NEO4J_DB")
        or str(cplus_backend.DEFAULT_GRAPH_DB)
        or "hyper_graph"
    )


async def _run_bridge_query(cypher: str, params: Dict[str, Any], database: str) -> List[Dict[str, Any]]:
    provider_name = (
        os.environ.get("CODE_GRAPH_PROVIDER")
        or os.environ.get("GRAPH_PROVIDER")
        or os.environ.get("MCP_GRAPH_PROVIDER")
        or "falkordb"
    ).strip().lower()
    provider = GraphProvider.FALKORDB if provider_name in {"falkor", "falkordb"} else GraphProvider.NEO4J
    if provider == GraphProvider.FALKORDB:
        config = {
            # Intentionally omit "uri": FalkorDB.from_url() creates a redis
            # client whose connection buffer corrupts during nested response
            # parsing in a long-running uvicorn server. The host/port
            # constructor path (FalkorDB.__init__) does not have this issue.
            "host": os.environ.get("FALKORDB_HOST", "localhost"),
            "port": int(os.environ.get("FALKORDB_PORT", "6379")),
            "user": os.environ.get("FALKORDB_USER", ""),
            "password": os.environ.get("FALKORDB_PASSWORD", ""),
            "graph": database,
            "database": database,
            "ssl": os.environ.get("FALKORDB_SSL", "").lower() in {"1", "true", "yes", "on"},
        }
    else:
        config = {
            "uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            "user": os.environ.get("NEO4J_USER", ""),
            "password": os.environ.get("NEO4J_PASS", ""),
            "database": database,
        }
    driver = await GraphDriverFactory.create_driver(provider, config)
    try:
        records, _, _ = await driver.execute_query(cypher, params, database)
        return records
    finally:
        close_result = driver.close()
        if hasattr(close_result, "__await__"):
            await close_result


async def _run_project_context_tool(
    *,
    tool_name: str,
    project_id: str,
    parser_type: str,
    required_labels: Tuple[str, ...],
    required_relationships: Tuple[str, ...],
    method_name: str,
    method_args: Dict[str, Any],
) -> Dict[str, Any]:
    # Each project's topology lives in its own graph named after the project
    # (by convention --project doubles as the graph/collection name). The
    # reader scopes the query to the caller's project_id so it targets the
    # same graph the topology writer filled. Without this, a server started
    # for project A silently reads A's graph for every call and returns empty
    # results for every other project.
    database = _resolve_graph_database(
        project_id=project_id or None,
    )
    _, _, routing, capability_diagnostics, capability_error = (
        await _resolve_direct_capability_context(
            tool_name,
            parser_type,
            database,
            required_labels=required_labels,
            required_relationships=required_relationships,
            error_payload={"project_id": project_id},
        )
    )
    if capability_error:
        return capability_error
    from services.project_context_service import ProjectContextService

    async def runner(cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await _run_bridge_query(cypher, params, database)

    service = ProjectContextService(runner)
    method = getattr(service, method_name)
    try:
        result = await method(project_id=project_id, **method_args)
    except Exception as exc:
        result = _build_tool_error(
            tool_name,
            {
                "project_id": project_id,
                "parser_type": parser_type,
                "db": _db,
                **method_args,
            },
            exc,
            backend_name=_resolve_backend_name(parser_type),
        )
    result["capability"] = routing
    if capability_diagnostics:
        result["capability_diagnostics"] = capability_diagnostics
    return result


@mcp_server.tool(
    name="get_project_modules",
    description="Return canonical project modules, descriptors, and dependencies.",
    output_schema=None,
)
async def tool_get_project_modules(
    project_id: str = "",
    module_id: str = "",
    module_path: str = "",
    include_dependencies: bool = True,
    offset: int = 0,
    limit: int = 50,
    parser_type: str = "",
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        tool_name="get_project_modules",
        project_id=project_id,
        parser_type=parser_type,
        required_labels=("ProjectModule", "BuildDescriptor"),
        required_relationships=("HAS_DESCRIPTOR",),
        method_name="get_project_modules",
        method_args={
            "module_id": module_id,
            "module_path": module_path,
            "include_dependencies": include_dependencies,
            "offset": offset,
            "limit": limit,
        },
    )


@mcp_server.tool(
    name="get_public_apis",
    description="Return strict source-level public/exported APIs by module.",
    output_schema=None,
)
async def tool_get_public_apis(
    project_id: str = "",
    module_id: str = "",
    symbol_kinds: Optional[List[str]] = None,
    language: str = "",
    include_inferred: bool = False,
    offset: int = 0,
    limit: int = 50,
    parser_type: str = "",
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        tool_name="get_public_apis",
        project_id=project_id,
        parser_type=parser_type,
        required_labels=("ProjectModule",),
        required_relationships=("EXPOSES_API",),
        method_name="get_public_apis",
        method_args={
            "module_id": module_id,
            "symbol_kinds": symbol_kinds or [],
            "language": language,
            "include_inferred": include_inferred,
            "offset": offset,
            "limit": limit,
        },
    )


@mcp_server.tool(
    name="get_endpoints",
    description="Return normalized HTTP, route, page, service, and gRPC endpoints.",
    output_schema=None,
)
async def tool_get_endpoints(
    project_id: str = "",
    module_id: str = "",
    protocol: str = "",
    framework: str = "",
    http_method: str = "",
    query: str = "",
    offset: int = 0,
    limit: int = 50,
    parser_type: str = "",
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        tool_name="get_endpoints",
        project_id=project_id,
        parser_type=parser_type,
        required_labels=("ProjectModule",),
        required_relationships=("EXPOSES_ENDPOINT",),
        method_name="get_endpoints",
        method_args={
            "module_id": module_id,
            "protocol": protocol,
            "framework": framework,
            "http_method": http_method,
            "query": query,
            "offset": offset,
            "limit": limit,
        },
    )


@mcp_server.tool(
    name="get_module_architecture_summary",
    description="Return bounded indexed-graph architecture context.",
    output_schema=None,
)
async def tool_get_module_architecture_summary(
    project_id: str = "",
    module_id: str = "",
    all_modules: bool = False,
    detail_level: str = "standard",
    item_limit: int = 10,
    parser_type: str = "",
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        tool_name="get_module_architecture_summary",
        project_id=project_id,
        parser_type=parser_type,
        required_labels=("ProjectModule",),
        required_relationships=(),
        method_name="get_module_architecture_summary",
        method_args={
            "module_id": module_id,
            "all_modules": all_modules,
            "detail_level": detail_level,
            "item_limit": item_limit,
        },
    )


@mcp_server.tool(
    name="get_project_special_files",
    description="Return decisive project files with redaction-safe summaries.",
    output_schema=None,
)
async def tool_get_project_special_files(
    project_id: str = "",
    module_id: str = "",
    role: str = "",
    parser: str = "",
    framework: str = "",
    parse_depth: str = "",
    status: str = "",
    include_generated: bool = True,
    offset: int = 0,
    limit: int = 50,
    parser_type: str = "",
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        tool_name="get_project_special_files",
        project_id=project_id,
        parser_type=parser_type,
        required_labels=("ProjectModule", "BuildDescriptor"),
        required_relationships=("HAS_DESCRIPTOR",),
        method_name="get_project_special_files",
        method_args={
            "module_id": module_id,
            "role": role,
            "parser": parser,
            "framework": framework,
            "parse_depth": parse_depth,
            "status": status,
            "include_generated": include_generated,
            "offset": offset,
            "limit": limit,
        },
    )


@mcp_server.tool(
    name="get_framework_context",
    description="Return framework instances and dimension-specific context.",
    output_schema=None,
)
async def tool_get_framework_context(
    project_id: str = "",
    module_id: str = "",
    framework: str = "",
    dimensions: Optional[List[str]] = None,
    offset: int = 0,
    limit: int = 50,
    parser_type: str = "",
) -> Dict[str, Any]:
    return await _run_project_context_tool(
        tool_name="get_framework_context",
        project_id=project_id,
        parser_type=parser_type,
        required_labels=("ProjectModule", "FrameworkInstance"),
        required_relationships=("USES_FRAMEWORK",),
        method_name="get_framework_context",
        method_args={
            "module_id": module_id,
            "framework": framework,
            "dimensions": dimensions or [],
            "offset": offset,
            "limit": limit,
        },
    )


def _format_props(props: List[str]) -> str:
    return "{" + ", ".join(props) + "}"


@mcp_server.tool(
    name="find_callers_of_endpoint",
    description=(
        "Return all frontend functions / screens that call a specific backend API endpoint. "
        "Traverses: Function -[CALLS_API]-> ApiCall -[MATCHES]-> ApiEndpoint. "
        "Useful for answering: 'Which screens call /api/users/:id?'"
    ),
    output_schema=None,
)
async def tool_find_callers_of_endpoint(
    endpoint_path: str = "",
    http_method:   str = "GET",
    be_project_id: str = "",
    fe_project_id: str = "",
    project_id:    str = "",
    parser_type:   str = "",
) -> Dict[str, Any]:
    """
    Args:
        endpoint_path: Backend endpoint path, e.g. '/api/users/:id'
        http_method:   HTTP method (GET/POST/…), case-insensitive. Empty = any.
        be_project_id: project_id of the backend project.
        fe_project_id: project_id of the frontend project (empty = all projects).
        project_id:    project_id of the shard to query. Resolved via the
                        ProjectRegistry. Optional when ``be_project_id`` or
                        ``fe_project_id`` is set — those narrow the query to
                        one project. Omit every project_id to query across
                        all projects.
        db:            Graph database or graph name (explicit override).

    Returns:
        {
          "endpoint_path": str,
          "callers": [
            { "function_name": str, "qualified_name": str, "react_role": str,
              "file_path": str, "start_line": int, "project_id": str,
              "url_pattern": str, "confidence": float }
          ],
          "total": int
        }
    """
    if not endpoint_path:
        return {"endpoint_path": "", "callers": [], "total": 0,
                "error": "endpoint_path is required"}

    # Resolve the graph shard. Precedence: db (explicit) → project_id
    # (registry) → be_project_id/fe_project_id (registry). When multiple
    # project_id args are present, project_id wins, then be_project_id, then
    # fe_project_id. Omit every project_id to query across all projects.
    effective_project_id = (
        project_id or be_project_id or fe_project_id
    )
    database = _resolve_graph_database(
        project_id=effective_project_id or None,
    )
    _, _, routing, capability_diagnostics, capability_error = (
        await _resolve_direct_capability_context(
            "find_callers_of_endpoint", parser_type, database,
            required_relationships=("CALLS_API", "MATCHES"),
            required_labels=("ApiEndpoint", "ApiCall"),
            error_payload={"endpoint_path": endpoint_path},
        )
    )
    if capability_error:
        return capability_error
    method_filter = (http_method.strip().upper() or "")
    params: Dict[str, Any] = {"path": endpoint_path}
    ep_props = ["path: $path"]
    if be_project_id:
        ep_props.append("project_id_normalized: $be_project_normalized")
        params["be_project"] = be_project_id

    endpoint_match_lines: List[str] = []
    if method_filter and method_filter != "ALL":
        params["method"] = method_filter
        exact_props = _format_props(ep_props + ["http_method: $method"])
        all_props = _format_props(ep_props + ["http_method: 'ALL'"])
        endpoint_match_lines.extend(
            [
                "CALL () {",
                f"  MATCH (ep:ApiEndpoint {exact_props})",
                "  RETURN ep",
                "  UNION",
                f"  MATCH (ep:ApiEndpoint {all_props})",
                "  RETURN ep",
                "}",
            ]
        )
    elif method_filter == "ALL":
        all_props = _format_props(ep_props + ["http_method: 'ALL'"])
        endpoint_match_lines.append(f"MATCH (ep:ApiEndpoint {all_props})")
    else:
        endpoint_match_lines.append(f"MATCH (ep:ApiEndpoint {_format_props(ep_props)})")

    api_call_match = "MATCH (ac:ApiCall)-[m:MATCHES]->(ep)"
    if fe_project_id:
        params["fe_project"] = fe_project_id
        api_call_match = "MATCH (ac:ApiCall {project_id_normalized: $fe_project_normalized})-[m:MATCHES]->(ep)"

    cypher = "\n".join(
        endpoint_match_lines
        + [
            f"WITH ep WHERE {servlet_active_generation_predicate('ep')}",
            api_call_match,
            "MATCH (f:Function)-[:CALLS_API]->(ac)",
            "RETURN f.name          AS function_name,",
            "       f.qualified_name AS qualified_name,",
            "       f.react_role    AS react_role,",
            "       f.file_path     AS file_path,",
            "       f.start_line    AS start_line,",
            "       f.project_id    AS project_id,",
            "       ac.url_pattern  AS url_pattern,",
            "       m.confidence    AS confidence",
            "ORDER BY m.confidence DESC",
            "LIMIT 50",
        ]
    )
    try:
        callers = await _run_bridge_query(cypher, params, database)
        result = {"endpoint_path": endpoint_path, "callers": callers, "total": len(callers)}
    except Exception as exc:
        result = {"endpoint_path": endpoint_path, "callers": [], "total": 0, "error": str(exc)}
    result["capability"] = routing
    if capability_diagnostics:
        result["capability_diagnostics"] = capability_diagnostics
    return result


@mcp_server.tool(
    name="get_api_call_chain",
    description=(
        "Return the end-to-end fullstack call chain for a component or endpoint. "
        "Traverses: Screen/Component → (Function CALLS chain) → ApiCall → ApiEndpoint → Controller → Service → Repository → Database. "
        "Use to answer: 'What DB does this button ultimately query?'"
    ),
    output_schema=None,
)
async def tool_get_api_call_chain(
    component_name: str = "",
    endpoint_path:  str = "",
    fe_project_id:  str = "",
    be_project_id:  str = "",
    project_id:     str = "",
    max_depth:      str = "5",
    parser_type:    str = "",
) -> Dict[str, Any]:
    """
    Args:
        component_name: Frontend component/screen name, e.g. 'UserProfileScreen'
        endpoint_path:  Backend endpoint path, e.g. '/api/users/:id' (used if component not given)
        fe_project_id:  project_id of the frontend project.
        be_project_id:  project_id of the backend project.
        project_id:     project_id of the shard to query. Resolved via the
                        ProjectRegistry. Optional when ``fe_project_id`` or
                        ``be_project_id`` is set. Omit every project_id to
                        query across all projects.
        max_depth:      Max CALLS hops in FE chain (default 5).
        db:             Graph database or graph name (explicit override).

    Returns:
        {
          "chains": [
            {
              "fe_function": str, "api_call": { url_pattern, method },
              "be_endpoint": { path, method, framework },
              "be_controller": str, "be_service": str,
              "be_repository": str, "be_database": str,
              "confidence": float
            }
          ],
          "total": int
        }
    """
    effective_project_id = (
        project_id or be_project_id or fe_project_id
    )
    database = _resolve_graph_database(
        project_id=effective_project_id or None,
    )
    _depth = int(max_depth) if str(max_depth).isdigit() else 5

    if not component_name and not endpoint_path:
        return {"chains": [], "total": 0, "error": "component_name or endpoint_path required"}

    selected_parser, relationships, routing, capability_diagnostics, capability_error = (
        await _resolve_direct_capability_context(
            "get_api_call_chain", parser_type, database,
            required_relationships=("CALLS", "CALLS_API", "MATCHES", "HANDLES"),
            required_labels=("ApiEndpoint",),
            error_payload={
                "component_name": component_name,
                "endpoint_path": endpoint_path,
            },
        )
    )
    if capability_error:
        return capability_error
    selected_capability = capability_for_parser(selected_parser)
    flow_defaults = set(
        default_relationships(selected_capability.name)
        if selected_capability else ("CALLS",)
    )
    flow_relationships = _relationship_pattern([
        relationship for relationship in relationships if relationship in flow_defaults
    ])

    component_return = """
RETURN fe.name        AS fe_component,
       caller.name    AS fe_api_caller,
       caller.file_path AS fe_file_path,
       ac.url_pattern AS url_pattern,
       ac.http_method AS http_method,
       m.confidence   AS match_confidence,
       ep.path        AS be_endpoint_path,
       ep.http_method AS be_method,
       ep.framework   AS be_framework,
       ctrl.name      AS be_controller,
       svc.name       AS be_service,
       repo.name      AS be_repository,
       dbnode.name    AS be_database,
       persistence.name AS persistence_fact,
       table.name     AS database_table
ORDER BY m.confidence DESC
LIMIT 30
"""

    endpoint_return = """
RETURN caller.name    AS fe_component,
       caller.name    AS fe_api_caller,
       caller.file_path AS fe_file_path,
       ac.url_pattern AS url_pattern,
       ac.http_method AS http_method,
       m.confidence   AS match_confidence,
       ep.path        AS be_endpoint_path,
       ep.http_method AS be_method,
       ep.framework   AS be_framework,
       ctrl.name      AS be_controller,
       svc.name       AS be_service,
       repo.name      AS be_repository,
       dbnode.name    AS be_database,
       persistence.name AS persistence_fact,
       table.name     AS database_table
ORDER BY m.confidence DESC
LIMIT 30
"""

    if component_name:
        # Traverse from FE component → ApiCall → ApiEndpoint → Controller → Service → Repo → DB
        fe_props = ["name: $component_name"]
        params = {"component_name": component_name}
        if fe_project_id:
            fe_props.append("project_id_normalized: $fe_project_normalized")
            params["fe_project"] = fe_project_id

        endpoint_match = "MATCH (caller)-[:CALLS_API]->(ac:ApiCall)-[m:MATCHES]->(ep:ApiEndpoint)"
        if be_project_id:
            endpoint_match = (
                "MATCH (caller)-[:CALLS_API]->(ac:ApiCall)-[m:MATCHES]->"
                "(ep:ApiEndpoint {project_id_normalized: $be_project_normalized})"
            )
            params["be_project"] = be_project_id

        cypher = "\n".join(
            [
                f"MATCH (fe:Function {_format_props(fe_props)})",
                f"MATCH (fe)-[:{flow_relationships}*0..{_depth}]->(caller:Function)",
                endpoint_match,
                f"WITH fe, caller, ac, m, ep WHERE {servlet_active_generation_predicate('ep')}",
                "OPTIONAL MATCH (ep)-[:HANDLES]->(forwardCtrl:Controller)",
                "OPTIONAL MATCH (reverseCtrl:Controller)-[:HANDLES]->(ep)",
                "OPTIONAL MATCH (ep)-[:SEMANTIC_OF]->(servletHandler:Function)",
                "WITH fe, caller, ac, m, ep, coalesce(forwardCtrl, reverseCtrl, servletHandler) AS ctrl",
                f"OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..3]->(svc:Service)",
                f"OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..5]->(repo)",
                "WHERE repo:Repository OR repo:DataRepository OR repo:MyBatisMapper OR repo:MyBatisMapperMethod",
                "OPTIONAL MATCH (repo)-[:DECLARES_QUERY|DERIVES_QUERY|QUERIES|BINDS_STATEMENT|DECLARES_STATEMENT*0..3]-(persistence)",
                "OPTIONAL MATCH (persistence)-[:READS_FROM|WRITES_TO|REFERENCES_TABLE]->(table:DatabaseTable)",
                "OPTIONAL MATCH (repo)-[:QUERIES]->(dbnode:Database)",
                component_return.strip(),
            ]
        )
    else:
        # Start from endpoint, traverse both ways
        ep_props = ["path: $endpoint_path"]
        params = {"endpoint_path": endpoint_path}
        if be_project_id:
            ep_props.append("project_id_normalized: $be_project_normalized")
            params["be_project"] = be_project_id

        api_call_match = "MATCH (ac:ApiCall)-[m:MATCHES]->(ep)"
        if fe_project_id:
            api_call_match = "MATCH (ac:ApiCall {project_id_normalized: $fe_project_normalized})-[m:MATCHES]->(ep)"
            params["fe_project"] = fe_project_id

        cypher = "\n".join(
            [
                f"MATCH (ep:ApiEndpoint {_format_props(ep_props)})",
                f"WITH ep WHERE {servlet_active_generation_predicate('ep')}",
                api_call_match,
                "MATCH (caller:Function)-[:CALLS_API]->(ac)",
                "WITH caller, ac, m, ep",
                "OPTIONAL MATCH (ep)-[:HANDLES]->(forwardCtrl:Controller)",
                "OPTIONAL MATCH (reverseCtrl:Controller)-[:HANDLES]->(ep)",
                "OPTIONAL MATCH (ep)-[:SEMANTIC_OF]->(servletHandler:Function)",
                "WITH caller, ac, m, ep, coalesce(forwardCtrl, reverseCtrl, servletHandler) AS ctrl",
                f"OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..3]->(svc:Service)",
                f"OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..5]->(repo)",
                "WHERE repo:Repository OR repo:DataRepository OR repo:MyBatisMapper OR repo:MyBatisMapperMethod",
                "OPTIONAL MATCH (repo)-[:DECLARES_QUERY|DERIVES_QUERY|QUERIES|BINDS_STATEMENT|DECLARES_STATEMENT*0..3]-(persistence)",
                "OPTIONAL MATCH (persistence)-[:READS_FROM|WRITES_TO|REFERENCES_TABLE]->(table:DatabaseTable)",
                "OPTIONAL MATCH (repo)-[:QUERIES]->(dbnode:Database)",
                endpoint_return.strip(),
            ]
        )

    try:
        rows = await _run_bridge_query(cypher, params, database)
        chains = [
            {
                "fe_component":    r.get("fe_component"),
                "fe_api_caller":   r.get("fe_api_caller"),
                "fe_file_path":    r.get("fe_file_path"),
                "api_call": {
                    "url_pattern": r.get("url_pattern"),
                    "http_method": r.get("http_method"),
                },
                "match_confidence": r.get("match_confidence"),
                "be_endpoint": {
                    "path":      r.get("be_endpoint_path"),
                    "method":    r.get("be_method"),
                    "framework": r.get("be_framework"),
                },
                "be_controller": r.get("be_controller"),
                "be_service":    r.get("be_service"),
                "be_repository": r.get("be_repository"),
                "be_database":   r.get("be_database"),
                "persistence_fact": r.get("persistence_fact"),
                "database_table": r.get("database_table"),
            }
            for r in rows
        ]
        result = {"chains": chains, "total": len(chains)}
    except Exception as exc:
        result = {"chains": [], "total": 0, "error": str(exc)}
    result["capability"] = routing
    if capability_diagnostics:
        result["capability_diagnostics"] = capability_diagnostics
    return result


# ── Workflow-Aware Impact Assessment tools ────────────────────────────────────
# Uses the same provider-neutral graph-driver path as the bridge tools and has
# no FastAPI Request dependency.

_EXTERNAL_MARKERS = ("third_party", "external", "vendor", "/usr", "node_modules")


@mcp_server.tool(
    name="analyze_workflow_impact",
    description=(
        "Analyze the full impact of changing a function/screen on all workflows. "
        "Returns function-level call graph expansion PLUS workflow-level severity, "
        "navigator route impacts, shared-screen cascade detection, "
        "an overall_risk_score, and a rule-based recommendation."
    ),
    output_schema=None,
)
async def tool_analyze_workflow_impact(
    function_id: str,
    project_id: str = "",
    direction: str = "downstream",
    max_depth: int = 4,
    parser_type: str = "",
) -> Dict[str, Any]:
    """
    Args:
        function_id: symbol_id of the function/screen to analyze
        db:          Graph database or graph name (explicit override)
        project_id:  project_id of the shard to query. Resolved via the
                     ProjectRegistry. Omit it to query across all projects.
        direction:   'downstream' or 'upstream' (default: 'downstream')
        max_depth:   CALLS traversal depth, capped at 4 (default: 4)

    Returns:
        {
          "risk_score": float,
          "node_count": int,
          "edge_count": int,
          "external_dependency_count": int,
          "impacted_nodes": [...],
          "workflow_impact": {
            "directly_affected_workflows": [{"name", "domain", "severity", "step_index", "reason"}],
            "indirectly_affected_workflows": [{"name", "domain", "severity", "call_depth"}],
            "cascade_workflows": [{"name", "domain", "severity", "reason"}],
            "navigator_impacts": [{"navigator", "route", "impact_type"}],
            "shared_screen_conflict": bool,
            "workflow_risk_score": float,
            "overall_risk_score": float,
            "recommendation": str
          }
        }
    """
    import sys as _sys  # noqa: PLC0415

    database = _resolve_graph_database(
        project_id=project_id or None,
    )
    capped = min(int(max_depth), 4)

    # 1. Call-graph expansion via existing dispatch system
    try:
        subgraph = await _dispatch_tool("query_subgraph", {
            "function_id": function_id,
            "db": database,
            "direction": direction,
            "max_depth": capped,
            "parser_type": parser_type or None,
        })
    except Exception as exc:
        subgraph = {"error": str(exc)}

    subgraph_error = subgraph.get("error")
    if isinstance(subgraph_error, dict) and subgraph_error.get("type") == "unsupported_capability":
        return subgraph

    nodes: List[Dict[str, Any]] = subgraph.get("nodes") or subgraph.get("subgraph") or []
    edges: List[Dict[str, Any]] = subgraph.get("edges", [])

    # 2. Base function-level risk (mirrors ImpactAnalyzer formula)
    externals = [n for n in nodes if any(m in (n.get("file") or "").lower() for m in _EXTERNAL_MARKERS)]
    base_risk = min(1.0, 0.2 + len(nodes) / 50.0 + len(edges) / 150.0 + len(externals) * 0.05)

    base_result: Dict[str, Any] = {
        "risk_score": round(base_risk, 3),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "external_dependency_count": len(externals),
        "impacted_nodes": [
            {"id": n.get("id"), "qual_name": n.get("qual_name"),
             "file": n.get("file"), "depth": n.get("depth")}
            for n in nodes
        ],
        "capability": subgraph.get("capability") or _capability_summary(
            parser_type or None,
            _resolve_backend_name(parser_type or None),
        ),
    }
    if subgraph.get("capability_diagnostics"):
        base_result["capability_diagnostics"] = subgraph["capability_diagnostics"]

    if subgraph.get("error"):
        base_result["subgraph_error"] = subgraph["error"]

    # 3. Workflow impact layer — shared provider-neutral graph driver
    if os.environ.get("WORKFLOW_IMPACT_DISABLED", "").strip() == "1":
        return base_result

    selected_parser, workflow_relationships, routing, workflow_diagnostics, capability_error = (
        await _resolve_direct_capability_context(
            "analyze_workflow_impact",
            parser_type,
            database,
            required_relationships=("HAS_STEP", "CALLS"),
            error_payload={"function_id": function_id},
        )
    )
    if capability_error:
        return capability_error
    selected_capability = capability_for_parser(selected_parser)
    flow_defaults = set(
        default_relationships(selected_capability.name)
        if selected_capability else ("CALLS",)
    )
    workflow_flow_relationships = [
        relationship
        for relationship in workflow_relationships
        if relationship in flow_defaults
    ]
    base_result["capability"] = routing
    if workflow_diagnostics:
        base_result["capability_diagnostics"] = workflow_diagnostics

    try:
        # Ensure the hyper-graph root is importable
        _hg_root = str(Path(__file__).resolve().parent.parent)
        if _hg_root not in _sys.path:
            _sys.path.insert(0, _hg_root)

        from tools.common.workflow_impact_scorer import WorkflowImpactScorer  # noqa: PLC0415

        drv = await cplus_backend._get_graph_driver()
        scorer = WorkflowImpactScorer(
            drv,
            database=database,
            flow_relationships=workflow_flow_relationships,
            workflow_relationship="HAS_STEP",
        )
        wf_impact = await scorer.score(function_id, nodes, max_depth=capped)

        overall = min(1.0, round(0.4 * base_risk + 0.6 * wf_impact.workflow_risk_score, 3))
        wf_impact.overall_risk_score = overall

        base_result["workflow_impact"] = {
            "directly_affected_workflows": [
                {"name": w.workflow_name, "domain": w.domain,
                 "severity": w.severity, "step_index": w.step_index, "reason": w.reason}
                for w in wf_impact.directly_affected_workflows
            ],
            "indirectly_affected_workflows": [
                {"name": w.workflow_name, "domain": w.domain,
                 "severity": w.severity, "call_depth": w.call_depth}
                for w in wf_impact.indirectly_affected_workflows
            ],
            "cascade_workflows": [
                {"name": w.workflow_name, "domain": w.domain,
                 "severity": w.severity, "reason": w.reason}
                for w in wf_impact.cascade_workflows
            ],
            "navigator_impacts": [
                {"navigator": n.var_name, "route": n.affected_route,
                 "impact_type": n.impact_type}
                for n in wf_impact.navigator_impacts
            ],
            "shared_screen_conflict": wf_impact.shared_screen_conflict,
            "workflow_risk_score": wf_impact.workflow_risk_score,
            "overall_risk_score": overall,
            "recommendation": wf_impact.recommendation,
        }
        base_result["risk_score"] = overall
    except Exception as exc:
        base_result["workflow_impact"] = {"error": str(exc)}

    return base_result


@mcp_server.tool(
    name="find_workflows_containing",
    description=(
        "Find all workflows that contain this function as a step — "
        "directly (HAS_STEP) or indirectly (via CALLS chain). "
        "Useful before making changes: 'which workflows will I break?'"
    ),
    output_schema=None,
)
async def tool_find_workflows_containing(
    function_id: str,
    project_id: str = "",
    include_indirect: bool = True,
    max_depth: int = 4,
    parser_type: str = "",
) -> Dict[str, Any]:
    """
    Args:
        function_id:      symbol_id of the function to look up
        db:               Graph database or graph name (explicit override)
        project_id:       project_id of the shard to query. Resolved via the
                          ProjectRegistry. Omit it to query across all
                          projects.
        include_indirect: Also find workflows reachable via CALLS chain (default True)
        max_depth:        Max CALLS hops for indirect search (default 4)

    Returns:
        {
          "function_id": str,
          "direct_workflows":   [{"workflow_id", "name", "domain", "confidence", "step_index"}],
          "indirect_workflows": [{"workflow_id", "name", "domain", "confidence", "call_depth"}],
          "total": int
        }
    """
    database = _resolve_graph_database(
        project_id=project_id or None,
    )
    capped = min(int(max_depth), 4)
    selected_parser, relationships, routing, capability_diagnostics, capability_error = (
        await _resolve_direct_capability_context(
            "find_workflows_containing", parser_type, database,
            required_relationships=(
                ("HAS_STEP", "CALLS") if include_indirect else ("HAS_STEP",)
            ),
            error_payload={"function_id": function_id},
        )
    )
    if capability_error:
        return capability_error
    selected_capability = capability_for_parser(selected_parser)
    flow_defaults = set(
        default_relationships(selected_capability.name)
        if selected_capability else ("CALLS",)
    )
    flow_relationships = _relationship_pattern([
        relationship for relationship in relationships if relationship in flow_defaults
    ])

    direct_cypher = """
MATCH (w:Workflow)-[s:HAS_STEP]->(f:Function)
WHERE f.symbol_id = $id OR f.file_path = $id
RETURN w.workflow_id                AS workflow_id,
       w.name                       AS name,
       coalesce(w.domain, '')       AS domain,
       coalesce(w.confidence, 0.5)  AS confidence,
       coalesce(s.order, -1)        AS step_index
ORDER BY w.confidence DESC
"""
    # Note: Cypher path-length range (*1..N) cannot be parameterised — use safe
    # integer interpolation after capping at 4 to prevent injection.
    indirect_cypher = f"""
MATCH (w:Workflow)-[:HAS_STEP]->(entry:Function)
MATCH path = (entry)-[:{flow_relationships}*1..{capped}]->(f:Function)
WHERE (f.symbol_id = $id OR f.file_path = $id)
  AND NOT w.workflow_id IN $direct_ids
RETURN DISTINCT
       w.workflow_id                AS workflow_id,
       w.name                       AS name,
       coalesce(w.domain, '')       AS domain,
       coalesce(w.confidence, 0.5)  AS confidence,
       length(path)                 AS call_depth
ORDER BY call_depth ASC, confidence DESC
LIMIT 30
"""

    try:
        direct_rows = await _run_bridge_query(
            direct_cypher, {"id": function_id}, database,
        )
        indirect_rows: List[Dict[str, Any]] = []
        if include_indirect:
            direct_ids = [row["workflow_id"] for row in direct_rows]
            indirect_rows = await _run_bridge_query(
                indirect_cypher,
                {"id": function_id, "direct_ids": direct_ids},
                database,
            )
        result = {
            "function_id": function_id,
            "direct_workflows": direct_rows,
            "indirect_workflows": indirect_rows,
            "total": len(direct_rows) + len(indirect_rows),
        }
    except Exception as exc:
        result = {
            "function_id": function_id,
            "direct_workflows": [],
            "indirect_workflows": [],
            "total": 0,
            "error": str(exc),
        }
    result["capability"] = routing
    if capability_diagnostics:
        result["capability_diagnostics"] = capability_diagnostics
    return result


# ===========================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified FastMCP server for multi-language code graphs.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.getenv("FASTMCP_TRANSPORT", "streamable-http"),
    )
    parser.add_argument("--host", default=os.getenv("FASTMCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FASTMCP_PORT", "8788")))
    parser.add_argument(
        "--path",
        dest="stream_path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path",
    )
    parser.add_argument(
        "--stream-path",
        dest="stream_path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path (deprecated, use --path)",
    )
    return parser.parse_args()


def main() -> None:
    force_quit = {"armed": False}

    def _handle_sigint(signum, _frame) -> None:
        if force_quit["armed"]:
            print("Force quitting now.")
            os._exit(0)
        force_quit["armed"] = True
        if signum == signal.SIGTERM:
            print("Received SIGTERM. Send again to force quit.")
        else:
            print("Received SIGINT. Press Ctrl+C again to force quit.")

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)
    if hasattr(signal, "SIGQUIT"):
        signal.signal(signal.SIGQUIT, _handle_sigint)

    args = parse_args()
    transport = args.transport
    stream_path = args.stream_path
    if stream_path and not stream_path.startswith("/"):
        stream_path = "/" + stream_path
    endpoint = f"http://{args.host}:{args.port}{stream_path}"
    print(f"Starting MCP server: {MCP_NAME}")
    print(f"Transport: {transport}")
    if transport == "streamable-http":
        print(f"Endpoint: {endpoint}")
    else:
        print("Endpoint: (stdio)")
    kwargs: Dict[str, Any] = {"transport": transport}
    if transport != "stdio":
        kwargs.update({"host": args.host, "port": args.port})
        if stream_path:
            kwargs["path"] = stream_path
    if transport == "streamable-http":
        kwargs["stateless_http"] = True
        kwargs["json_response"] = True
    mcp_server.run(**kwargs)


if __name__ == "__main__":
    main()

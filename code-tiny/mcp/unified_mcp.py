from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import os
import signal
import sys

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from fastapi import Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

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

_UNIFIED_TOOL_NAMES: frozenset = frozenset(
    {
        "activate_project",
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
    }
)
_unified_catalog = build_catalog(_UNIFIED_TOOL_NAMES)
_MCP_FUNCTIONS_JSON: str = json.dumps(
    {"total_count": len(_unified_catalog), "functions": _unified_catalog},
    ensure_ascii=False,
)


MCP_NAME = "Project Call Graph Unified"

INSTRUCTIONS = """Unified MCP for multi-language code graphs (single server/port).

Discovery first:
- Call `list_mcp_functions` at session start to get the exact, current tool set and parameter docs.

Routing:
- Use `activate_project(parser_type=..., database_name=...)` to set defaults.
- Most tools also accept `parser_type` directly.
- Parser mapping:
  - android/android-kotlin/kotlin-android -> Android backend
  - cplus/cpp/c++/c/clang/java/kotlin/jvm/delphi/pascal/vbnet/vb6/vba/vbscript -> C++ backend

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
    version="1.1.0",
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

PARSER_ALIASES_ANDROID = {"android", "android-kotlin", "kotlin-android"}
PARSER_ALIASES_CPLUS = {
    "cplus",
    "cpp",
    "c++",
    "c",
    "clang",
    "java",
    "kotlin",
    "jvm",
    "delphi",
    "pascal",
    "vbnet",
    "vb6",
    "vba",
    "vbscript",
}

active_project: Dict[str, Optional[str]] = {
    "parser_type": None,
    "database_name": None,
}


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
    parser = _normalize_parser_type(parser_type) or _normalize_parser_type(active_project.get("parser_type"))
    if parser in PARSER_ALIASES_ANDROID:
        return "android"
    if parser in PARSER_ALIASES_CPLUS:
        return "cplus"
    return DEFAULT_BACKEND


def _apply_unified_defaults(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(payload)
    if merged.get("parser_type") is None and active_project.get("parser_type"):
        merged["parser_type"] = active_project["parser_type"]
    if merged.get("db") is None and active_project.get("database_name"):
        merged["db"] = active_project["database_name"]
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


async def _dispatch_tool(tool_name: str, payload: Dict[str, Any]) -> Any:
    merged = _apply_unified_defaults(payload)
    merged = _coerce_list_fields(merged)
    backend_name = _resolve_backend_name(merged.get("parser_type"))
    backend = BACKENDS[backend_name]
    fn = _unwrap_tool_callable(getattr(backend.module, f"tool_{tool_name}", None))
    if fn is None:
        raise ValueError(f"Tool '{tool_name}' is not available in backend '{backend_name}'.")
    result = await fn(payload=merged)
    if isinstance(result, dict):
        result.setdefault("backend", backend_name)
    return result


@mcp_server.tool(
    name="activate_project",
    description="Set default parser_type and optional database_name for subsequent tool calls.",
    output_schema=None,
)
async def tool_activate_project(
    parser_type: str = "",
    database_name: str = "",
) -> Dict[str, Optional[str]]:
    values = {"parser_type": parser_type if parser_type else None, "database_name": database_name if database_name else None}
    merged = {k: v for k, v in values.items() if v is not None}
    backend_name = _resolve_backend_name(merged.get("parser_type"))
    fn = _unwrap_tool_callable(getattr(BACKENDS[backend_name].module, "tool_activate_project", None))
    if fn is None:
        raise ValueError(f"Backend '{backend_name}' does not expose activate_project.")
    response = await fn(payload=merged)
    parser = _normalize_parser_type((response or {}).get("parser_type") or merged.get("parser_type"))
    if parser:
        active_project["parser_type"] = parser
    db_name = (response or {}).get("database_name")
    if db_name:
        active_project["database_name"] = str(db_name)
    if not isinstance(response, dict):
        response = {"parser_type": parser, "database_name": db_name}
    response["backend"] = backend_name
    return response


@mcp_server.tool(
    name="list_mcp_functions",
    description="List all available MCP functions/tools with their descriptions, inputs (parameters), and outputs.",
    output_schema=None,
)
async def tool_list_mcp_functions() -> str:
    return _MCP_FUNCTIONS_JSON


@mcp_server.tool(name="list_parsers", description="List available parser types supported by unified MCP.", output_schema=None)
async def tool_list_parsers() -> Dict[str, Any]:
    parser_values: List[str] = []
    for backend in BACKENDS.values():
        fn = _unwrap_tool_callable(getattr(backend.module, "tool_list_parsers", None))
        if fn is None:
            continue
        result = await fn(payload={})
        for parser in result.get("parsers", []):
            parser_text = str(parser).strip()
            if parser_text and parser_text not in parser_values:
                parser_values.append(parser_text)
    for extra in ["android", "android-kotlin", "cplus", "cpp", "java", "kotlin", "jvm", "vbnet", "vb6", "vba", "vbscript"]:
        if extra not in parser_values:
            parser_values.append(extra)
    return {
        "parsers": sorted(parser_values),
        "default_backend": DEFAULT_BACKEND,
        "active_parser_type": active_project.get("parser_type"),
    }


@mcp_server.tool(name="list_databases", description="List available Neo4j databases.", output_schema=None)
async def tool_list_databases(
    parser_type: str = "",
) -> Dict[str, Any]:
    merged = {"parser_type": parser_type} if parser_type else {}
    return await _dispatch_tool("list_databases", merged)


@mcp_server.tool(
    name="list_qdrant_collections",
    description="List available Qdrant collections.",
    output_schema=None,
)
async def tool_list_qdrant_collections(
    parser_type: str = "",
    db: str = "neo4j",
    qdrant_url: str = "http://localhost:6333",
    include_vectors: bool = False,
) -> Dict[str, Any]:
    values = {
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "qdrant_url": qdrant_url,
        "include_vectors": include_vectors if include_vectors else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    return await _dispatch_tool("list_qdrant_collections", merged)


async def _dispatch_planner_tool(tool_name: str, payload: Dict[str, Any]) -> Any:
    merged = _apply_unified_defaults(_coerce_list_fields(dict(payload)))
    fn = _unwrap_tool_callable(getattr(fast_backend, f"tool_{tool_name}", None))
    if fn is None:
        raise ValueError(f"Planner tool '{tool_name}' is not available in fast backend.")
    params = inspect.signature(fn).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        filtered = merged
    else:
        filtered = {k: v for k, v in merged.items() if k in params}
    return await fn(**filtered)


@mcp_server.tool(
    name="compute_scc",
    description="Compute strongly connected components (SCC) from a directed dependency graph.",
    output_schema=None,
)
async def tool_compute_scc(
    nodes: str = "",
    edges: Optional[List[Dict[str, Any]]] = None,
    edge_semantics: str = "depends_on",
    include_singletons: bool = True,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "nodes": _normalize_string_list(nodes) if nodes else None,
        "edges": edges if edges is not None else None,
        "edge_semantics": edge_semantics if edge_semantics else None,
        "include_singletons": include_singletons,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return await _dispatch_planner_tool("compute_scc", payload)


@mcp_server.tool(
    name="topological_sort",
    description="Topologically sort dependency graph and return linear order and/or waves.",
    output_schema=None,
)
async def tool_topological_sort(
    nodes: str = "",
    edges: Optional[List[Dict[str, Any]]] = None,
    edge_semantics: str = "depends_on",
    output_mode: str = "both",
    on_cycle: str = "auto_condense_scc",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "nodes": _normalize_string_list(nodes) if nodes else None,
        "edges": edges if edges is not None else None,
        "edge_semantics": edge_semantics if edge_semantics else None,
        "output_mode": output_mode if output_mode else None,
        "on_cycle": on_cycle if on_cycle else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return await _dispatch_planner_tool("topological_sort", payload)


@mcp_server.tool(
    name="plan_dependency_order",
    description="Plan module-level dependency order from CALLS edges.",
    output_schema=None,
)
async def tool_plan_dependency_order(
    modules: str = "",
    parser_type: str = "",
    db: str = "",
    edge_semantics: str = "depends_on",
    on_cycle: str = "auto_condense_scc",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "modules": _normalize_string_list(modules) if modules else None,
        "parser_type": parser_type if parser_type else None,
        "db": db if db else None,
        "edge_semantics": edge_semantics if edge_semantics else None,
        "on_cycle": on_cycle if on_cycle else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return await _dispatch_planner_tool("plan_dependency_order", payload)


@mcp_server.tool(
    name="plan_file_dependency_order",
    description="Plan file-level dependency order per module from CALLS edges.",
    output_schema=None,
)
async def tool_plan_file_dependency_order(
    modules: str = "",
    parser_type: str = "",
    db: str = "",
    edge_semantics: str = "depends_on",
    on_cycle: str = "auto_condense_scc",
    include_cross_module: bool = False,
    max_files_per_module: int = 2000,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "modules": _normalize_string_list(modules) if modules else None,
        "parser_type": parser_type if parser_type else None,
        "db": db if db else None,
        "edge_semantics": edge_semantics if edge_semantics else None,
        "on_cycle": on_cycle if on_cycle else None,
        "include_cross_module": include_cross_module if include_cross_module else None,
        "max_files_per_module": max_files_per_module if max_files_per_module > 0 else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return await _dispatch_planner_tool("plan_file_dependency_order", payload)


@mcp_server.tool(
    name="plan_function_dependency_order",
    description="Plan function-level dependency order per module from CALLS edges.",
    output_schema=None,
)
async def tool_plan_function_dependency_order(
    modules: str = "",
    parser_type: str = "",
    db: str = "",
    edge_semantics: str = "depends_on",
    on_cycle: str = "auto_condense_scc",
    include_cross_module: bool = False,
    include_lambdas: bool = False,
    max_functions_per_module: int = 5000,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "modules": _normalize_string_list(modules) if modules else None,
        "parser_type": parser_type if parser_type else None,
        "db": db if db else None,
        "edge_semantics": edge_semantics if edge_semantics else None,
        "on_cycle": on_cycle if on_cycle else None,
        "include_cross_module": include_cross_module if include_cross_module else None,
        "include_lambdas": include_lambdas if include_lambdas else None,
        "max_functions_per_module": max_functions_per_module if max_functions_per_module > 0 else None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return await _dispatch_planner_tool("plan_function_dependency_order", payload)


def _register_payload_tool(tool_name: str, description: str) -> None:
    @mcp_server.tool(name=tool_name, description=description, output_schema=None)
    async def _tool(
        parser_type: str = "",
        db: str = "",
        query: str = "",
        mode: str = "",
        top_k: int = 0,
        model_path: str = "",
        qdrant_url: str = "",
        collection: str = "",
        collection_comment: str = "",
        collection_code: str = "",
        content_mode: str = "",
        include_raw_fields: bool = False,
        show_snippet: bool = False,
        show_comment: bool = False,
        with_neo4j: bool = False,
        neo4j_db: str = "",
        neo4j_include_signature: bool = False,
        neo4j_include_comment: bool = False,
        neo4j_cache_path: str = "",
        node_id: str = "",
        node_ids: str = "",
        function_id: str = "",
        start_function_id: str = "",
        end_function_id: str = "",
        start_id: str = "",
        end_id: str = "",
        source_modules: str = "",
        target_modules: str = "",
        source_module: str = "",
        target_module: str = "",
        class_names: str = "",
        class_name: str = "",
        file_paths: str = "",
        file_path: str = "",
        modules: str = "",
        module: str = "",
        max_depth: int = 0,
        direction: str = "",
        rel_types: str = "",
        relationship_types: str = "",
        limit: int = 0,
        include_possible: bool = False,
        include_fp: bool = False,
        project_id: str = "",
        note: str = "",
        tags: str = "",
        severity: str = "",
        sender: str = "",
        receiver: str = "",
        senders: str = "",
        receivers: str = "",
    ) -> Any:
        values = {
            "parser_type": parser_type if parser_type else None,
            "db": db if db else None,
            "query": query if query else None,
            "mode": mode if mode else None,
            "top_k": top_k if top_k > 0 else None,
            "model_path": model_path if model_path else None,
            "qdrant_url": qdrant_url if qdrant_url else None,
            "collection": collection if collection else None,
            "collection_comment": collection_comment if collection_comment else None,
            "collection_code": collection_code if collection_code else None,
            "content_mode": content_mode if content_mode else None,
            "include_raw_fields": include_raw_fields if include_raw_fields else None,
            "show_snippet": show_snippet if show_snippet else None,
            "show_comment": show_comment if show_comment else None,
            "with_neo4j": with_neo4j if with_neo4j else None,
            "neo4j_db": neo4j_db if neo4j_db else None,
            "neo4j_include_signature": neo4j_include_signature if neo4j_include_signature else None,
            "neo4j_include_comment": neo4j_include_comment if neo4j_include_comment else None,
            "neo4j_cache_path": neo4j_cache_path if neo4j_cache_path else None,
            "node_id": node_id if node_id else None,
            "node_ids": node_ids if node_ids else None,
            "function_id": function_id if function_id else None,
            "start_function_id": start_function_id if start_function_id else None,
            "end_function_id": end_function_id if end_function_id else None,
            "start_id": start_id if start_id else None,
            "end_id": end_id if end_id else None,
            "source_modules": source_modules if source_modules else None,
            "target_modules": target_modules if target_modules else None,
            "source_module": source_module if source_module else None,
            "target_module": target_module if target_module else None,
            "class_names": class_names if class_names else None,
            "class_name": class_name if class_name else None,
            "file_paths": file_paths if file_paths else None,
            "file_path": file_path if file_path else None,
            "modules": modules if modules else None,
            "module": module if module else None,
            "max_depth": max_depth if max_depth > 0 else None,
            "direction": direction if direction else None,
            "rel_types": rel_types if rel_types else None,
            "relationship_types": relationship_types if relationship_types else None,
            "limit": limit if limit > 0 else None,
            "include_possible": include_possible if include_possible else None,
            "include_fp": include_fp if include_fp else None,
            "project_id": project_id if project_id else None,
            "note": note if note else None,
            "tags": tags if tags else None,
            "severity": severity if severity else None,
            "sender": sender if sender else None,
            "receiver": receiver if receiver else None,
            "senders": senders if senders else None,
            "receivers": receivers if receivers else None,
        }
        merged = {k: v for k, v in values.items() if v is not None}
        return await _dispatch_tool(tool_name, merged)


# Define annotate_node separately with specific parameters
@mcp_server.tool(name="annotate_node", description="Add or update annotations for a node.", output_schema=None)
async def tool_annotate_node(
    node_id: str = "",
    note: str = "",
    tags: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    project_id: str = "",
) -> Dict[str, Any]:
    """Add or update annotations for a node."""
    values = {
        "node_id": node_id if node_id else None,
        "note": note if note else None,
        "tags": tags if tags else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("annotate_node", merged)
    return result


# Define semantic_search separately with specific parameters
@mcp_server.tool(name="semantic_search", description="Semantic search over Qdrant embeddings.", output_schema=None)
async def tool_semantic_search(
    query: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    top_k: str = "",
    collection: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """Semantic search over Qdrant embeddings."""
    values = {
        "query": query if query else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "top_k": int(top_k) if top_k and top_k.isdigit() else None,
        "collection": collection if collection else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("semantic_search", merged)
    if result == []:
        return {"results": []}
    if isinstance(result, list):
        return {"results": result}
    return result


# Define trace_flow_between_module separately with specific parameters
@mcp_server.tool(name="trace_flow_between_module", description="Trace flow paths between modules.", output_schema=None)
async def tool_trace_flow_between_module(
    source_module: str = "",
    target_module: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """Trace flow paths between modules."""
    values = {
        "source_module": source_module if source_module else None,
        "target_module": target_module if target_module else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    try:
        result = await _dispatch_tool("trace_flow_between_module", merged)
    except RuntimeError as exc:
        if "No path found in any db" in str(exc):
            return {
                "nodes": [],
                "edges": [],
                "reason": "no_path",
            }
        raise
    if result == []:
        return {"flows": []}
    if isinstance(result, list):
        return {"flows": result}
    return result


# Define trace_flow separately with specific parameters
@mcp_server.tool(name="trace_flow", description="Trace flow paths using configurable relationships.", output_schema=None)
async def tool_trace_flow(
    start_id: str = "",
    direction: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """Trace flow paths using configurable relationships."""
    values = {
        "start_id": start_id if start_id else None,
        "direction": direction if direction else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    try:
        result = await _dispatch_tool("trace_flow", merged)
    except RuntimeError as exc:
        if "No path found in any db" in str(exc):
            return {
                "nodes": [],
                "edges": [],
                "reason": "no_path",
            }
        raise
    if result == []:
        return {"flows": []}
    if isinstance(result, list):
        return {"flows": result}
    return result


@mcp_server.tool(
    name="find_screen_workflows",
    description=(
        "Discover ranked screen-only NAVIGATE workflows for a React/TS project. "
        "Input either a pair (node_a + node_b) or a single node_a with a "
        "direction (inbound|outbound|bidirectional). Requires project_id."
    ),
    output_schema=None,
)
async def tool_find_screen_workflows(
    project_id: str = "",
    node_a: str = "",
    node_b: str = "",
    direction: str = "bidirectional",
    max_hops: int = 8,
    max_paths: int = 100,
    include_entry_function: bool = False,
    include_api_calls: bool = False,
    db: str = "",
    parser_type: str = "",
) -> Dict[str, Any]:
    merged = {
        "project_id": project_id or None,
        "node_a": node_a or None,
        "node_b": node_b or None,
        "direction": direction or "bidirectional",
        "max_hops": max_hops,
        "max_paths": max_paths,
        "include_entry_function": include_entry_function,
        "include_api_calls": include_api_calls,
        "db": db or None,
        "parser_type": parser_type or None,
    }
    merged = {k: v for k, v in merged.items() if v is not None}
    return await _dispatch_tool("find_screen_workflows", merged)


# Define list_up_entrypoint separately with specific parameters
@mcp_server.tool(name="list_up_entrypoint", description="List entrypoint functions called from outside modules.", output_schema=None)
async def tool_list_up_entrypoint(
    modules: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    project_id: str = "",
) -> Dict[str, Any]:
    """List entrypoint functions called from outside modules."""
    values = {
        "modules": modules if modules else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("list_up_entrypoint", merged)
    if result == []:
        return {"entrypoints": []}
    if isinstance(result, list):
        return {"entrypoints": result}
    return result


# Define listup_class_matching_path separately with specific parameters
@mcp_server.tool(name="listup_class_matching_path", description="List functions for classes/types by name.", output_schema=None)
async def tool_listup_class_matching_path(
    class_name: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    project_id: str = "",
) -> Dict[str, Any]:
    """List functions for classes/types by name."""
    values = {
        "class_name": class_name if class_name else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("listup_class_matching_path", merged)
    if result == []:
        return {"functions": []}
    if isinstance(result, list):
        return {"functions": result}
    return result


# Define listup_symbols_matching_file_path separately with specific parameters
@mcp_server.tool(
    name="listup_symbols_matching_file_path",
    description=(
        "List symbols by file-path tokens. Accepts a single ``file_path`` "
        "string OR a ``modules`` list of tokens (CONTAINS-matched against "
        "node.file_path/path). Examples: file_path='vtgm01.c' or "
        "modules=['auth/', 'router.ts']."
    ),
    output_schema=None,
)
async def tool_listup_symbols_matching_file_path(
    file_path: str = "",
    modules: Optional[List[str]] = None,
    node_types: Optional[List[str]] = None,
    parser_type: str = "",
    db: str = "neo4j",
    project_id: str = "",
) -> Dict[str, Any]:
    """List symbols by file path token.

    Translates the single-string ``file_path`` surface into the backend's
    required ``modules: List[str]`` parameter (fastmcp_server.py's
    ``tool_listup_symbols_matching_file_path`` accepts only the list
    form). Callers can pass either ``file_path`` (convenience) or
    ``modules`` (explicit multi-token) — the union is forwarded as
    ``modules``. Empty input raises a clear validation error rather
    than the prior cryptic Pydantic ``unexpected keyword`` message.
    """
    tokens: List[str] = []
    if modules:
        tokens.extend(str(m).strip() for m in modules if str(m).strip())
    if file_path:
        token = file_path.strip()
        if token and token not in tokens:
            tokens.append(token)
    if not tokens:
        return {
            "error": (
                "Provide at least one path token via 'file_path' or "
                "'modules'."
            ),
            "symbols": [],
        }
    values: Dict[str, Any] = {
        "modules": tokens,
        "db": db,
        "project_id": project_id if project_id else None,
    }
    if parser_type:
        values["parser_type"] = parser_type
    if node_types:
        values["node_types"] = [
            str(t).strip() for t in node_types if str(t).strip()
        ]
    result = await _dispatch_tool(
        "listup_symbols_matching_file_path", values
    )
    if result == []:
        return {"symbols": []}
    if isinstance(result, list):
        return {"symbols": result}
    return result


# Define find_path_between_module separately with specific parameters
@mcp_server.tool(name="find_path_between_module", description="Find call paths between modules.", output_schema=None)
async def tool_find_path_between_module(
    source_module: str = "",
    target_module: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """Find call paths between modules."""
    values = {
        "source_module": source_module if source_module else None,
        "target_module": target_module if target_module else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("find_path_between_module", merged)
    if result == []:
        return {"paths": []}
    if isinstance(result, list):
        return {"paths": result}
    return result


# Define find_paths separately with specific parameters
@mcp_server.tool(name="find_paths", description="Find call paths between two functions.", output_schema=None)
async def tool_find_paths(
    start_function_id: str = "",
    end_function_id: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    node_type: str = "",
    expand_search: bool = False,
    project_id: str = "",
) -> Dict[str, Any]:
    """Find call paths between two functions."""
    values = {
        "start_function_id": start_function_id if start_function_id else None,
        "end_function_id": end_function_id if end_function_id else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "node_type": node_type if node_type else None,
        "expand_search": expand_search,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    try:
        result = await _dispatch_tool("find_paths", merged)
    except RuntimeError as exc:
        # Normalize common "no path" backend errors into an empty result
        # so clients can handle it as a valid zero-match response.
        if "No path found in any db" in str(exc):
            return {
                "paths": [],
                "nodes": [],
                "edges": [],
                "reason": "no_path",
            }
        raise
    if result == []:
        return {"paths": []}
    if isinstance(result, list):
        return {"paths": result}
    return result


# Define query_subgraph separately with specific parameters
@mcp_server.tool(name="query_subgraph", description="Return call graph context around a function ID.", output_schema=None)
async def tool_query_subgraph(
    function_id: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    node_type: str = "",
    expand_search: bool = False,
    project_id: str = "",
) -> Dict[str, Any]:
    """Return call graph context around a function ID."""
    values = {
        "function_id": function_id if function_id else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "node_type": node_type if node_type else None,
        "expand_search": expand_search,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    try:
        result = await _dispatch_tool("query_subgraph", merged)
    except RuntimeError as exc:
        if "No subgraph found for node" in str(exc):
            return {
                "nodes": [],
                "edges": [],
                "reason": "no_subgraph",
            }
        raise
    if result == []:
        return {"subgraph": []}
    if isinstance(result, list):
        return {"subgraph": result}
    return result


# Define get_node_details separately with specific parameters
@mcp_server.tool(name="get_node_details", description="Fetch metadata for multiple node IDs.", output_schema=None)
async def tool_get_node_details(
    node_ids: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    node_type: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """Fetch metadata for multiple node IDs."""
    values = {
        "node_ids": node_ids if node_ids else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "node_type": node_type if node_type else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("get_node_details", merged)
    if result == []:
        return {"nodes": []}
    if isinstance(result, list):
        return {"nodes": result}
    return result


# Define list_possible_calls separately with specific parameters
@mcp_server.tool(name="list_possible_calls", description="List POSSIBLE_CALLS edges.", output_schema=None)
async def tool_list_possible_calls(
    function_id: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """List POSSIBLE_CALLS edges."""
    values = {
        "function_id": function_id if function_id else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("list_possible_calls", merged)
    if result == []:
        return {"calls": []}
    if isinstance(result, list):
        return {"calls": result}
    return result


# Define get_symbol separately with specific parameters
@mcp_server.tool(name="get_symbol", description="Retrieve metadata for a specific node by id.", output_schema=None)
async def tool_get_symbol(
    node_id: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    node_type: str = "",
    project_id: str = "",
) -> Dict[str, Any]:
    """Retrieve metadata for a specific node by id."""
    values = {
        "node_id": node_id if node_id else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "node_type": node_type if node_type else None,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    try:
        result = await _dispatch_tool("get_symbol", merged)
        if result is None or result == {}:
            return {"symbol": None, "message": "Không tìm thấy symbol", "node_id": node_id}
        return result
    except Exception as e:
        return {"symbol": None, "message": f"Không tìm thấy symbol: {str(e)}", "node_id": node_id}


# Define search_by_code separately with specific parameters
@mcp_server.tool(name="search_by_code", description="Search nodes by matching text in code snippets.", output_schema=None)
async def tool_search_by_code(
    query: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    node_type: str = "",
    expand_search: bool = False,
    project_id: str = "",
) -> Dict[str, Any]:
    """Search nodes by matching text in code snippets."""
    values = {
        "query": query if query else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "node_type": node_type if node_type else None,
        "expand_search": expand_search,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("search_by_code", merged)
    if result == []:
        return {"results": []}
    if isinstance(result, list):
        return {"results": result}
    return result


# Define search_functions separately with specific parameters
@mcp_server.tool(name="search_functions", description="Search nodes by name/qualified_name.", output_schema=None)
async def tool_search_functions(
    query: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    limit: str = "",
    node_type: str = "",
    expand_search: bool = False,
    project_id: str = "",
) -> Dict[str, Any]:
    """Search nodes by name/qualified_name."""
    values = {
        "query": query if query else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "limit": int(limit) if limit and limit.isdigit() else None,
        "node_type": node_type if node_type else None,
        "expand_search": expand_search,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("search_functions", merged)
    if result == []:
        return {"functions": []}
    if isinstance(result, list):
        return {"functions": result}
    return result


# Define get_ipc_message separately with fewer parameters
@mcp_server.tool(name="get_ipc_message", description="Query IPC messages by sender/receiver.", output_schema=None)
async def tool_get_ipc_message(
    sender: str = "",
    receiver: str = "",
    parser_type: str = "",
    db: str = "neo4j",
    project_id: str = "",
) -> Dict[str, Any]:
    """Query IPC messages by sender/receiver."""
    values = {
        "sender": sender if sender else None,
        "receiver": receiver if receiver else None,
        "parser_type": parser_type if parser_type else None,
        "db": db,
        "project_id": project_id if project_id else None,
    }
    merged = {k: v for k, v in values.items() if v is not None}
    result = await _dispatch_tool("get_ipc_message", merged)
    # Wrap empty list to satisfy MCP output_schema requirement
    if result == []:
        return {"messages": []}
    if isinstance(result, list):
        return {"messages": result}
    return result


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
    top_k:      str  = "",
    db:         str  = "",
    collection: str  = "",
    debug:      bool = False,
) -> Dict[str, Any]:
    """
    Intent-aware graph search combining semantic + keyword + graph expansion.

    Args:
        query:      Natural language text (keyword, sentence, or multi-line paragraph).
        mode:       "semantic" | "hybrid" (default) | "graph_expanded"
        top_k:      Max matched nodes (default 10).
        db:         Neo4j database name override.
        collection: Qdrant collection name override.
        debug:      Include per-signal score breakdown in each node.

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

    k = int(top_k) if str(top_k).isdigit() else 10
    service = get_explore_service()
    return await service.explore(
        query      = q,
        top_k      = k,
        mode       = mode or "hybrid",
        db         = db or None,
        collection = collection or None,
        debug      = debug,
    )


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

import neo4j as _neo4j  # noqa: E402  (already in venv)


def _get_bridge_driver() -> Any:
    uri  = os.environ.get("NEO4J_URI",  "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "")
    pwd  = os.environ.get("NEO4J_PASS", "")
    if user and pwd:
        return _neo4j.GraphDatabase.driver(uri, auth=(user, pwd))
    return _neo4j.GraphDatabase.driver(uri)


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
    db:            str = "",
) -> Dict[str, Any]:
    """
    Args:
        endpoint_path: Backend endpoint path, e.g. '/api/users/:id'
        http_method:   HTTP method (GET/POST/…), case-insensitive. Empty = any.
        be_project_id: project_id of the backend project.
        fe_project_id: project_id of the frontend project (empty = all projects).
        db:            Neo4j database name (defaults to NEO4J_DB env var or 'neo4j').

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

    database = db or os.environ.get("NEO4J_DB", "neo4j")
    method_filter = (http_method.strip().upper() or "")
    params: Dict[str, Any] = {"path": endpoint_path}
    ep_props = ["path: $path"]
    if be_project_id:
        ep_props.append("project_id: $be_project")
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
        api_call_match = "MATCH (ac:ApiCall {project_id: $fe_project})-[m:MATCHES]->(ep)"

    cypher = "\n".join(
        endpoint_match_lines
        + [
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
        drv = _get_bridge_driver()
        with drv.session(database=database) as session:
            result = session.run(cypher, params)
            callers = [dict(r) for r in result]
        drv.close()
        return {"endpoint_path": endpoint_path, "callers": callers, "total": len(callers)}
    except Exception as exc:
        return {"endpoint_path": endpoint_path, "callers": [], "total": 0, "error": str(exc)}


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
    max_depth:      str = "5",
    db:             str = "",
) -> Dict[str, Any]:
    """
    Args:
        component_name: Frontend component/screen name, e.g. 'UserProfileScreen'
        endpoint_path:  Backend endpoint path, e.g. '/api/users/:id' (used if component not given)
        fe_project_id:  project_id of the frontend project.
        be_project_id:  project_id of the backend project.
        max_depth:      Max CALLS hops in FE chain (default 5).
        db:             Neo4j database name.

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
    database = db or os.environ.get("NEO4J_DB", "neo4j")
    _depth = int(max_depth) if str(max_depth).isdigit() else 5

    if not component_name and not endpoint_path:
        return {"chains": [], "total": 0, "error": "component_name or endpoint_path required"}

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
       dbnode.name    AS be_database
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
       dbnode.name    AS be_database
ORDER BY m.confidence DESC
LIMIT 30
"""

    if component_name:
        # Traverse from FE component → ApiCall → ApiEndpoint → Controller → Service → Repo → DB
        fe_props = ["name: $component_name"]
        params = {"component_name": component_name}
        if fe_project_id:
            fe_props.append("project_id: $fe_project")
            params["fe_project"] = fe_project_id

        endpoint_match = "MATCH (caller)-[:CALLS_API]->(ac:ApiCall)-[m:MATCHES]->(ep:ApiEndpoint)"
        if be_project_id:
            endpoint_match = (
                "MATCH (caller)-[:CALLS_API]->(ac:ApiCall)-[m:MATCHES]->"
                "(ep:ApiEndpoint {project_id: $be_project})"
            )
            params["be_project"] = be_project_id

        cypher = "\n".join(
            [
                f"MATCH (fe:Function {_format_props(fe_props)})",
                f"MATCH (fe)-[:CALLS*0..{_depth}]->(caller:Function)",
                endpoint_match,
                "OPTIONAL MATCH (ep)-[:HANDLES]->(ctrl:Controller)",
                "OPTIONAL MATCH (ctrl)-[:CALLS*0..3]->(svc:Service)",
                "OPTIONAL MATCH (svc)-[:CALLS*0..2]->(repo:Repository)",
                "OPTIONAL MATCH (repo)-[:QUERIES]->(dbnode:Database)",
                component_return.strip(),
            ]
        )
    else:
        # Start from endpoint, traverse both ways
        ep_props = ["path: $endpoint_path"]
        params = {"endpoint_path": endpoint_path}
        if be_project_id:
            ep_props.append("project_id: $be_project")
            params["be_project"] = be_project_id

        api_call_match = "MATCH (ac:ApiCall)-[m:MATCHES]->(ep)"
        if fe_project_id:
            api_call_match = "MATCH (ac:ApiCall {project_id: $fe_project})-[m:MATCHES]->(ep)"
            params["fe_project"] = fe_project_id

        cypher = "\n".join(
            [
                f"MATCH (ep:ApiEndpoint {_format_props(ep_props)})",
                api_call_match,
                "MATCH (caller:Function)-[:CALLS_API]->(ac)",
                "WITH caller, ac, m, ep",
                "OPTIONAL MATCH (ep)-[:HANDLES]->(ctrl:Controller)",
                "OPTIONAL MATCH (ctrl)-[:CALLS*0..3]->(svc:Service)",
                "OPTIONAL MATCH (svc)-[:CALLS*0..2]->(repo:Repository)",
                "OPTIONAL MATCH (repo)-[:QUERIES]->(dbnode:Database)",
                endpoint_return.strip(),
            ]
        )

    try:
        drv = _get_bridge_driver()
        with drv.session(database=database) as session:
            result = session.run(cypher, params)
            rows = [dict(r) for r in result]
        drv.close()
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
            }
            for r in rows
        ]
        return {"chains": chains, "total": len(chains)}
    except Exception as exc:
        return {"chains": [], "total": 0, "error": str(exc)}


# ── Workflow-Aware Impact Assessment tools ────────────────────────────────────
# Uses the same direct Neo4j driver pattern as tool_find_callers_of_endpoint
# and tool_get_api_call_chain — no FastAPI Request dependency.

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
    db: str = "neo4j",
    direction: str = "downstream",
    max_depth: int = 4,
) -> Dict[str, Any]:
    """
    Args:
        function_id: symbol_id of the function/screen to analyze
        db:          Neo4j database name (default: 'neo4j')
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

    database = db or os.environ.get("NEO4J_DB", "neo4j")
    capped = min(int(max_depth), 4)

    # 1. Call-graph expansion via existing dispatch system
    try:
        subgraph = await _dispatch_tool("query_subgraph", {
            "function_id": function_id,
            "db": database,
            "direction": direction,
            "max_depth": capped,
        })
    except Exception as exc:
        subgraph = {"error": str(exc)}

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
    }

    if subgraph.get("error"):
        base_result["subgraph_error"] = subgraph["error"]

    # 3. Workflow impact layer — direct Neo4j, same pattern as bridge tools
    if os.environ.get("WORKFLOW_IMPACT_DISABLED", "").strip() == "1":
        return base_result

    try:
        # Ensure the hyper-graph root is importable
        _hg_root = str(Path(__file__).resolve().parent.parent)
        if _hg_root not in _sys.path:
            _sys.path.insert(0, _hg_root)

        from tools.common.workflow_impact_scorer import WorkflowImpactScorer  # noqa: PLC0415

        drv = _get_bridge_driver()
        scorer = WorkflowImpactScorer(drv, database=database)
        wf_impact = await scorer.score(function_id, nodes, max_depth=capped)
        drv.close()

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
    db: str = "neo4j",
    include_indirect: bool = True,
    max_depth: int = 4,
) -> Dict[str, Any]:
    """
    Args:
        function_id:      symbol_id of the function to look up
        db:               Neo4j database name
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
    database = db or os.environ.get("NEO4J_DB", "neo4j")
    capped = min(int(max_depth), 4)

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
MATCH path = (entry)-[:CALLS*1..{capped}]->(f:Function)
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
        drv = _get_bridge_driver()
        with drv.session(database=database) as session:
            direct_rows = [dict(r) for r in session.run(direct_cypher, {"id": function_id})]
            indirect_rows: List[Dict[str, Any]] = []
            if include_indirect:
                direct_ids = [r["workflow_id"] for r in direct_rows]
                indirect_rows = [
                    dict(r)
                    for r in session.run(indirect_cypher, {"id": function_id, "direct_ids": direct_ids})
                ]
        drv.close()
        return {
            "function_id": function_id,
            "direct_workflows": direct_rows,
            "indirect_workflows": indirect_rows,
            "total": len(direct_rows) + len(indirect_rows),
        }
    except Exception as exc:
        return {
            "function_id": function_id,
            "direct_workflows": [],
            "indirect_workflows": [],
            "total": 0,
            "error": str(exc),
        }


# ===========================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified FastMCP server for Android/C++ code graphs.",
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
    mcp_server.run(**kwargs)


if __name__ == "__main__":
    main()

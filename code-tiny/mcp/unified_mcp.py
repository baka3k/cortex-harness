from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import sys

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP


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

from tool_metadata import _FULL_CATALOG  # noqa: E402

_MCP_FUNCTIONS_JSON: str = json.dumps(
    {"total_count": len(_FULL_CATALOG), "functions": _FULL_CATALOG},
    ensure_ascii=False,
)


MCP_NAME = "Project Call Graph Unified"

INSTRUCTIONS = """Unified MCP for multi-language code graphs (single server/port).

How routing works:
- Use `activate_project(parser_type=..., database_name=...)` to set defaults.
- Each tool also accepts `parser_type` (inside payload or top-level).
- Supported parser types:
  - android/android-kotlin/kotlin-android -> Android backend
  - cplus/cpp/c++/c/clang/java/kotlin/jvm -> C++ backend

Tool input contract:
- All graph/query tools accept `payload` dict.
- Most common tool parameters are also accepted at top-level (without `payload`).
- If both top-level fields and payload fields are provided, payload takes precedence.

Use `list_mcp_functions` to get comprehensive documentation for all available tools.
"""

mcp_server = FastMCP(
    name=MCP_NAME,
    version="1.0.0",
    instructions=INSTRUCTIONS,
)


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
PARSER_ALIASES_CPLUS = {"cplus", "cpp", "c++", "c", "clang", "java", "kotlin", "jvm"}

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
)
async def tool_activate_project(
    parser_type: Optional[str] = None,
    database_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    merged = _merge_payload(payload, {"parser_type": parser_type, "database_name": database_name})
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
    description="List all available MCP functions/tools with their descriptions, inputs (parameters), and outputs."
)
async def tool_list_mcp_functions(payload: Optional[Dict[str, Any]] = None) -> str:
    return _MCP_FUNCTIONS_JSON


@mcp_server.tool(name="list_parsers", description="List available parser types supported by unified MCP.")
async def tool_list_parsers(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _coerce_payload(payload)
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
    for extra in ["android", "android-kotlin", "cplus", "cpp", "java", "kotlin", "jvm"]:
        if extra not in parser_values:
            parser_values.append(extra)
    return {
        "parsers": sorted(parser_values),
        "default_backend": DEFAULT_BACKEND,
        "active_parser_type": active_project.get("parser_type"),
    }


@mcp_server.tool(name="list_databases", description="List available Neo4j databases.")
async def tool_list_databases(
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _merge_payload(payload, {"parser_type": parser_type})
    return await _dispatch_tool("list_databases", merged)


@mcp_server.tool(
    name="list_qdrant_collections",
    description="List available Qdrant collections.",
)
async def tool_list_qdrant_collections(
    parser_type: Optional[str] = None,
    db: Optional[str] = None,
    qdrant_url: Optional[str] = None,
    include_vectors: Optional[bool] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "db": db,
            "qdrant_url": qdrant_url,
            "include_vectors": include_vectors,
        },
    )
    return await _dispatch_tool("list_qdrant_collections", merged)


def _register_payload_tool(tool_name: str, description: str) -> None:
    @mcp_server.tool(name=tool_name, description=description)
    async def _tool(
        parser_type: Optional[str] = None,
        db: Optional[str] = None,
        query: Optional[str] = None,
        mode: Optional[str] = None,
        top_k: Optional[int] = None,
        model_path: Optional[str] = None,
        qdrant_url: Optional[str] = None,
        collection: Optional[str] = None,
        collection_comment: Optional[str] = None,
        collection_code: Optional[str] = None,
        content_mode: Optional[str] = None,
        include_raw_fields: Optional[bool] = None,
        show_snippet: Optional[bool] = None,
        show_comment: Optional[bool] = None,
        with_neo4j: Optional[bool] = None,
        neo4j_db: Optional[str] = None,
        neo4j_include_signature: Optional[bool] = None,
        neo4j_include_comment: Optional[bool] = None,
        neo4j_cache_path: Optional[str] = None,
        node_id: Any = None,
        node_ids: Optional[Any] = None,
        function_id: Any = None,
        start_function_id: Any = None,
        end_function_id: Any = None,
        start_id: Any = None,
        end_id: Any = None,
        source_modules: Optional[Any] = None,
        target_modules: Optional[Any] = None,
        source_module: Optional[Any] = None,
        target_module: Optional[Any] = None,
        class_names: Optional[Any] = None,
        class_name: Optional[Any] = None,
        file_paths: Optional[Any] = None,
        file_path: Optional[Any] = None,
        modules: Optional[Any] = None,
        module: Optional[Any] = None,
        max_depth: Optional[int] = None,
        direction: Optional[str] = None,
        rel_types: Optional[Any] = None,
        relationship_types: Optional[Any] = None,
        limit: Optional[int] = None,
        include_possible: Optional[bool] = None,
        include_fp: Optional[bool] = None,
        project_id: Optional[str] = None,
        note: Optional[str] = None,
        tags: Optional[str] = None,
        severity: Optional[str] = None,
        sender: Optional[str] = None,
        receiver: Optional[str] = None,
        senders: Optional[Any] = None,
        receivers: Optional[Any] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        merged = _merge_payload(
            payload,
            {
                "parser_type": parser_type,
                "db": db,
                "query": query,
                "mode": mode,
                "top_k": top_k,
                "model_path": model_path,
                "qdrant_url": qdrant_url,
                "collection": collection,
                "collection_comment": collection_comment,
                "collection_code": collection_code,
                "content_mode": content_mode,
                "include_raw_fields": include_raw_fields,
                "show_snippet": show_snippet,
                "show_comment": show_comment,
                "with_neo4j": with_neo4j,
                "neo4j_db": neo4j_db,
                "neo4j_include_signature": neo4j_include_signature,
                "neo4j_include_comment": neo4j_include_comment,
                "neo4j_cache_path": neo4j_cache_path,
                "node_id": node_id,
                "node_ids": node_ids,
                "function_id": function_id,
                "start_function_id": start_function_id,
                "end_function_id": end_function_id,
                "start_id": start_id,
                "end_id": end_id,
                "source_modules": source_modules,
                "target_modules": target_modules,
                "source_module": source_module,
                "target_module": target_module,
                "class_names": class_names,
                "class_name": class_name,
                "file_paths": file_paths,
                "file_path": file_path,
                "modules": modules,
                "module": module,
                "max_depth": max_depth,
                "direction": direction,
                "rel_types": rel_types,
                "relationship_types": relationship_types,
                "limit": limit,
                "include_possible": include_possible,
                "include_fp": include_fp,
                "project_id": project_id,
                "note": note,
                "tags": tags,
                "severity": severity,
                "sender": sender,
                "receiver": receiver,
                "senders": senders,
                "receivers": receivers,
            },
        )
        return await _dispatch_tool(tool_name, merged)


for _name, _description in [
    ("get_ipc_message", "Query IPC messages by sender/receiver."),
    ("search_functions", "Search nodes by name/qualified_name."),
    ("search_by_code", "Search nodes by matching text in code snippets."),
    ("get_symbol", "Retrieve metadata for a specific node by id."),
    ("list_possible_calls", "List POSSIBLE_CALLS edges."),
    ("get_node_details", "Fetch metadata for multiple node IDs."),
    ("query_subgraph", "Return call graph context around a function ID."),
    ("find_paths", "Find call paths between two functions."),
    ("find_path_between_module", "Find call paths between modules."),
    ("listup_symbols_matching_file_path", "List symbols by file path token."),
    ("listup_class_matching_path", "List functions for classes/types by name."),
    ("list_up_entrypoint", "List entrypoint functions called from outside modules."),
    ("trace_flow", "Trace flow paths using configurable relationships."),
    ("trace_flow_between_module", "Trace flow paths between modules."),
    ("semantic_search", "Semantic search over Qdrant embeddings."),
    ("annotate_node", "Add or update annotations for a node."),
]:
    _register_payload_tool(_name, _description)


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
    mcp_server.run(**kwargs)


if __name__ == "__main__":
    main()

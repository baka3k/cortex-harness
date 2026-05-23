from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import signal

from typing import Any, Dict, Iterable, List, Optional, Tuple

import logging

import httpx
import torch
from fastmcp import FastMCP
from neo4j.exceptions import Neo4jError
from transformers import AutoModel, AutoTokenizer

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

_MCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.core.base import GraphDriver
from tool_metadata import build_catalog, ANDROID_OVERRIDES


def _load_env_file(env_path: str) -> None:
    if not os.path.isfile(env_path):
        return
    loaded = []
    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if not key or key in os.environ or value == "":
                continue
            os.environ[key] = value
            loaded.append(key)
    if loaded:
        shown = [key for key in loaded if key not in {"NEO4J_PASS"}]
        if shown:
            summary = ", ".join(f"{key}={os.environ.get(key, '')}" for key in shown)
            print(f"[env] Loaded {summary} from {env_path}")


_load_env_file(os.path.join(os.path.dirname(__file__), "..", ".env"))


def _normalize_transports(transports: List[str]) -> List[str]:
    normalized: List[str] = []
    for transport in transports:
        name = transport.strip()
        if not name:
            continue
        if name == "http":
            name = "streamable-http"
        if name not in normalized:
            normalized.append(name)
    return normalized or ["streamable-http"]


def _parse_transport_env(value: Optional[str]) -> List[str]:
    raw = value or "streamable-http"
    transports = [item.strip() for item in raw.split(",") if item.strip()]
    return _normalize_transports(transports)


DEFAULT_TIMEOUT = float(os.environ.get("MCP_BACKEND_TIMEOUT", "60"))
DEFAULT_TRANSPORTS = _parse_transport_env(os.environ.get("MCP_FASTMCP_TRANSPORT"))
DEFAULT_MODEL = (
    os.environ.get("CODE_EMBEDDING_MODEL_PATH")
    or os.environ.get("CODE_EMBEDDING_MODEL")
    or os.environ.get("JINA_MODEL_PATH")
    or "jinaai/jina-embeddings-v3"
)
PRELOAD_EMBEDDER_ON_STARTUP = os.environ.get("MCP_PRELOAD_EMBEDDER", "1")
DEFAULT_QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
DEFAULT_QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "android_kotlin_functions")
DEFAULT_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.environ.get("NEO4J_USER")
DEFAULT_NEO4J_PASSWORD = os.environ.get("NEO4J_PASS")
DEFAULT_NEO4J_DB = os.environ.get("NEO4J_DB") or "neo4j"
FULLTEXT_SYMBOL_TEXT_INDEX = "mcp_symbol_text_ft"
FULLTEXT_SYMBOL_CODE_INDEX = "mcp_symbol_code_ft"
IPC_MESSAGES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "temp", "ipc_messages.json")


MCP_NAME = "Project Call Graph"

INSTRUCTIONS = """Project Call Graph MCP (local mode) reads directly from Neo4j and Qdrant.

Discovery:
- Call `list_mcp_functions` first to get the exact tool list and parameters supported by this backend.

Core capability groups:
- Symbol/graph search: search_functions, search_by_code, get_symbol, get_node_details
- Call graph traversal: query_subgraph, find_paths, find_path_between_module, trace_flow, trace_flow_between_module
- Module/class views: listup_symbols_matching_file_path, listup_class_matching_path, list_up_entrypoint
- Infrastructure: list_databases, list_qdrant_collections, list_parsers
- Utilities: semantic_search, get_ipc_message, list_possible_calls, annotate_node

Response content controls (most tools):
- content_mode: auto (default), summary, comment, code, name
  - auto fallback order: summary -> comment -> name
- include_raw_fields: false by default; when true, keep summary/comment/code fields in payload

Flow relationship defaults (trace_flow/trace_flow_between_module):
- Optional parser_type selects default rel_types when rel_types is not provided.
- Parser mapping: android/android-kotlin/kotlin-android -> Android rel types; cplus/cpp/c++/c/clang/delphi/pascal -> C++ rel types; others -> generic rel types.
- The server filters default rel_types to relationship types that actually exist in the selected Neo4j database.

Call-path options (query_subgraph/find_paths/find_path_between_module):
- include_possible/include_fp adds POSSIBLE_CALLS and CALLS_FUNCTION_POINTER to CALLS for path queries.
- Relation types are filtered to types available in the selected Neo4j database.

When include_raw_fields=false, only properties.content is returned (plus metadata) to reduce payload size.

Tool inputs:
- Tools accept a payload dict argument named `payload`.
- Top-level tool arguments are also accepted; payload (when provided) overrides them.
- Required fields are validated per tool; missing/invalid payloads raise ValueError.
"""

mcp_server = FastMCP(
    name=MCP_NAME,
    version="2.1.0",
    instructions=INSTRUCTIONS,
)

active_project: Dict[str, Optional[str]] = {
    "parser_type": None,
    "database_name": None,
}

_graph_driver: Optional[GraphDriver] = None
_embedder_cache: Dict[str, Tuple[Any, Any, torch.device]] = {}
logger = logging.getLogger("project_call_graph.mcp.server")


async def _get_graph_driver() -> GraphDriver:
    global _graph_driver
    if _graph_driver is not None:
        return _graph_driver
    if not DEFAULT_NEO4J_USER or not DEFAULT_NEO4J_PASSWORD:
        raise RuntimeError("NEO4J_USER and NEO4J_PASS must be set.")
    config = {
        "uri": DEFAULT_NEO4J_URI,
        "user": DEFAULT_NEO4J_USER,
        "password": DEFAULT_NEO4J_PASSWORD,
    }
    _graph_driver = await GraphDriverFactory.create_driver(GraphProvider.NEO4J, config)
    return _graph_driver


def _normalize_neo4j_db(value: str) -> str:
    name = value.strip()
    if not name:
        return name
    if os.path.isabs(name) or "/" in name or "\\" in name:
        return os.path.basename(name)
    return name


def _normalize_db_name(value: str) -> str:
    name = _normalize_neo4j_db(value)
    while name.endswith(".db.db"):
        name = name[:-3]
    return name


async def _select_database_name(requested: Optional[str]) -> Optional[str]:
    if not requested:
        return None
    normalized = _normalize_db_name(requested)
    available = await _list_databases()
    if available and normalized not in available:
        logger.warning(
            "Requested database not found: %s. Available: %s",
            normalized,
            ", ".join(available),
        )
        default_db = _normalize_db_name(DEFAULT_NEO4J_DB)
        if default_db in available:
            logger.warning("Falling back to default database: %s", default_db)
            return default_db
        return None
    return normalized


def _set_active_project(
    parser_type: Optional[str],
    database_name: Optional[str],
) -> None:
    if parser_type:
        active_project["parser_type"] = parser_type
    if database_name:
        active_project["database_name"] = database_name


def _resolve_db_candidates(db: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if db and str(db).strip():
        candidates.append(_normalize_db_name(str(db).strip()))
    cached = active_project.get("database_name")
    if cached:
        normalized = _normalize_db_name(cached)
        if normalized not in candidates:
            candidates.append(normalized)
    default_db = _normalize_db_name(DEFAULT_NEO4J_DB)
    if default_db and default_db not in candidates:
        candidates.append(default_db)
    return candidates


def _require(value: Optional[Any], description: str) -> Any:
    if value is None:
        raise ValueError(f"{description} is required (set via activate_project or provide explicitly).")
    return value


def _coerce_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict.")
    return payload


def _merge_payload(
    payload: Optional[Dict[str, Any]],
    values: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(values)
    payload = _coerce_payload(payload)
    if payload:
        merged.update(payload)
    return merged


def _normalize_depth(value: Any, default: int = 2, max_limit: int = 10) -> int:
    try:
        depth = int(value)
    except (TypeError, ValueError):
        depth = default
    if depth < 1:
        depth = 1
    if depth > max_limit:
        depth = max_limit
    return depth


def _normalize_content_mode(value: Optional[str]) -> str:
    if not value:
        return "auto"
    mode = str(value).strip().lower()
    if mode in {"auto", "summary", "comment", "code", "name"}:
        return mode
    return "auto"


def _normalize_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if "," in text or ";" in text:
            parts = [part.strip() for part in text.replace(";", ",").split(",")]
            return [part for part in parts if part]
        return [text]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_rel_types(value: Any, default: Optional[List[str]] = None) -> List[str]:
    if value is None:
        return list(default or [])
    items: List[str] = []
    if isinstance(value, str):
        raw = [part.strip() for part in value.replace(";", ",").split(",")]
        items = [part for part in raw if part]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        text = str(value).strip()
        if text:
            items = [text]
    cleaned: List[str] = []
    for item in items:
        upper = item.upper()
        if not upper.replace("_", "").isalnum():
            raise ValueError(f"Invalid relationship type: {item}")
        cleaned.append(upper)
    return cleaned


def _build_rel_match(rel_types: List[str], depth: int, direction: str) -> str:
    rel_token = ""
    if rel_types:
        rel_token = ":" + "|".join(rel_types)
    if direction in {"in", "incoming"}:
        return f"<-[{rel_token}*1..{depth}]-"
    if direction in {"both", "any", "undirected"}:
        return f"-[{rel_token}*1..{depth}]-"
    return f"-[{rel_token}*1..{depth}]->"


DEFAULT_FLOW_REL_TYPES_ANDROID = [
    "CALLS",
    "DECLARES",
    "CONTAINS",
    "USES_RESOURCE",
    "DECLARES_ROUTE",
    "STARTS_WITH_ROUTE",
    "ROUTE_CALLS",
    "DECLARES_COMPONENT",
    "STARTS_COMPONENT",
    "STARTS_INTENT",
    "SENDS_BROADCAST",
    "REGISTERS_RECEIVER",
    "DECLARES_INTENT_ACTION",
    "SENDS_HANDLER_MESSAGE",
    "ACTION_TARGETS_COMPONENT",
    "EMITS_EVENT",
    "HANDLES_EVENT",
    "ANNOTATED_WITH",
    "DEPENDS_ON",
    "TAKES_FUNCTION",
    "IMPLEMENTS",
    "EXTENDS",
]

DEFAULT_FLOW_REL_TYPES_CPLUS = [
    "CALLS",
    "POSSIBLE_CALLS",
    "CALLS_FUNCTION_POINTER",
    "DECLARES",
    "CONTAINS",
    "DEPENDS_ON",
]

DEFAULT_FLOW_REL_TYPES_GENERIC = [
    "CALLS",
    "DECLARES",
    "CONTAINS",
    "DEPENDS_ON",
]

PARSER_ALIASES_ANDROID = {"android", "android-kotlin", "kotlin-android"}
PARSER_ALIASES_CPLUS = {"cplus", "cpp", "c++", "c", "clang", "delphi", "pascal", "vbnet", "vb6", "vba", "vbscript"}
PARSER_ALIASES_JVM = {"java", "kotlin", "jvm"}


def _normalize_parser_type(value: Optional[str]) -> str:
    parser = (value or active_project.get("parser_type") or "").strip().lower()
    return parser


def _get_default_flow_rel_types(parser_type: Optional[str]) -> List[str]:
    parser = _normalize_parser_type(parser_type)
    if not parser:
        return list(DEFAULT_FLOW_REL_TYPES_ANDROID)
    if parser in PARSER_ALIASES_ANDROID:
        return list(DEFAULT_FLOW_REL_TYPES_ANDROID)
    if parser in PARSER_ALIASES_CPLUS:
        return list(DEFAULT_FLOW_REL_TYPES_CPLUS)
    if parser in PARSER_ALIASES_JVM:
        return list(DEFAULT_FLOW_REL_TYPES_ANDROID)
    return list(DEFAULT_FLOW_REL_TYPES_GENERIC)


def _fallback_node_name(properties: Dict[str, Any], node_id: Optional[str]) -> str:
    for key in (
        "name",
        "qualified_name",
        "package_name",
        "class_name",
        "module_path",
        "namespace",
        "application_id",
        "coordinate",
        "group",
        "artifact",
        "res_type",
        "route",
        "action",
        "token",
        "file_path",
        "path",
    ):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if node_id:
        return node_id
    return ""


def _android_symbol_labels() -> str:
    return (
        "(n:Function OR n:Class OR n:Type OR n:Namespace OR n:Package OR n:File "
        "OR n:AndroidManifest OR n:AndroidComponent OR n:AndroidResource "
        "OR n:GradleModule OR n:GradleDependency OR n:AndroidAnnotation "
        "OR n:AndroidNavRoute OR n:AndroidIntentAction OR n:AndroidHandlerMessage)"
    )


def _android_search_predicate() -> str:
    return (
        "any(q IN $qs WHERE "
        "toLower(coalesce(n.name, '')) CONTAINS q OR "
        "toLower(coalesce(n.qualified_name, '')) CONTAINS q OR "
        "toLower(coalesce(n.package_name, '')) CONTAINS q OR "
        "toLower(coalesce(n.class_name, '')) CONTAINS q OR "
        "toLower(coalesce(n.module_path, '')) CONTAINS q OR "
        "toLower(coalesce(n.namespace, '')) CONTAINS q OR "
        "toLower(coalesce(n.application_id, '')) CONTAINS q OR "
        "toLower(coalesce(n.coordinate, '')) CONTAINS q OR "
        "toLower(coalesce(n.group, '')) CONTAINS q OR "
        "toLower(coalesce(n.artifact, '')) CONTAINS q OR "
        "toLower(coalesce(n.version, '')) CONTAINS q OR "
        "toLower(coalesce(n.res_type, '')) CONTAINS q OR "
        "toLower(coalesce(n.component_type, '')) CONTAINS q OR "
        "toLower(coalesce(n.route, '')) CONTAINS q OR "
        "toLower(coalesce(n.action, '')) CONTAINS q OR "
        "toLower(coalesce(n.token, '')) CONTAINS q OR "
        "toLower(coalesce(n.file_path, '')) CONTAINS q OR "
        "toLower(coalesce(n.path, '')) CONTAINS q)"
    )


def _android_file_match_predicate() -> str:
    return (
        "any(token IN $modules WHERE "
        "toLower(coalesce(n.file_path, '')) CONTAINS toLower(token) OR "
        "toLower(coalesce(n.path, '')) CONTAINS toLower(token))"
    )


def _prune_content_fields(properties: Dict[str, Any]) -> None:
    properties.pop("summary", None)
    properties.pop("comment", None)
    properties.pop("code", None)


def _select_content(properties: Dict[str, Any], node_id: Optional[str], mode: str) -> str:
    summary = properties.get("summary")
    comment = properties.get("comment")
    code = properties.get("code")
    summary_text = summary if isinstance(summary, str) else ""
    comment_text = comment if isinstance(comment, str) else ""
    code_text = code if isinstance(code, str) else ""
    if mode == "summary":
        return summary_text
    if mode == "comment":
        return comment_text
    if mode == "code":
        return code_text
    if mode == "name":
        return _fallback_node_name(properties, node_id)
    if summary_text.strip():
        return summary_text
    if comment_text.strip():
        return comment_text
    return _fallback_node_name(properties, node_id)


def _record_node(
    node: Any,
    content_mode: str = "auto",
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
    mode = _normalize_content_mode(content_mode)
    if isinstance(node, dict):
        node_id = node.get("id")
        props = {key: value for key, value in node.items() if key != "labels"}
        content = _select_content(props, node_id, mode)
        if not include_raw_fields:
            _prune_content_fields(props)
        return {
            "id": node_id,
            "labels": list(node.get("labels", [])),
            "properties": {
                **props,
                "content_mode": mode,
                "content": content,
            },
        }
    node_id = node.get("id")
    properties = dict(node)
    properties["content_mode"] = mode
    properties["content"] = _select_content(properties, node_id, mode)
    if not include_raw_fields:
        _prune_content_fields(properties)
    return {
        "id": node_id,
        "labels": list(getattr(node, "labels", [])),
        "properties": properties,
    }


def _record_rel(rel: Any) -> Dict[str, Any]:
    if isinstance(rel, dict):
        return {
            "type": rel.get("type"),
            "properties": dict(rel.get("properties", {})),
            "start_id": rel.get("start_id"),
            "end_id": rel.get("end_id"),
        }
    # neo4j 6.x: record.data() serializes Relationship as (start_node_dict, type_str, end_node_dict)
    if isinstance(rel, (list, tuple)):
        if len(rel) == 3:
            start_node, rel_type, end_node = rel
            return {
                "type": rel_type if isinstance(rel_type, str) else str(rel_type),
                "properties": {},
                "start_id": start_node.get("id") if isinstance(start_node, dict) else None,
                "end_id": end_node.get("id") if isinstance(end_node, dict) else None,
            }
        # Unknown tuple length — return what we can
        return {"type": str(rel), "properties": {}, "start_id": None, "end_id": None}
    # Raw neo4j Relationship object
    try:
        return {
            "type": rel.type,
            "properties": dict(rel),
            "start_id": rel.start_node.get("id"),
            "end_id": rel.end_node.get("id"),
        }
    except AttributeError:
        return {"type": str(type(rel).__name__), "properties": {}, "start_id": None, "end_id": None}


def _paths_to_graph(
    paths: Iterable[Any],
    content_mode: str = "auto",
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    mode = _normalize_content_mode(content_mode)
    for path in paths:
        if isinstance(path, list):
            # neo4j 6.x: record.data() serializes a Path as a flat list
            # [node_dict, (start, type, end), node_dict, ...]; even indices are
            # nodes, odd indices are relationships.
            if path and isinstance(path[0], dict):
                for i, item in enumerate(path):
                    if i % 2 == 0:  # node
                        if isinstance(item, dict):
                            node_id = item.get("id")
                            if node_id and node_id not in nodes:
                                nodes[node_id] = _record_node(item, mode, include_raw_fields=include_raw_fields)
                    else:  # relationship
                        edges.append(_record_rel(item))
                continue
            # fallback: look for a path object with .nodes inside the list
            for item in path:
                if hasattr(item, "nodes") or (
                    isinstance(item, dict) and "nodes" in item and "relationships" in item
                ):
                    path = item
                    break
            else:
                continue
        if hasattr(path, "nodes"):
            path_nodes = path.nodes
            path_rels = path.relationships
        elif isinstance(path, dict) and "nodes" in path and "relationships" in path:
            path_nodes = path.get("nodes", [])
            path_rels = path.get("relationships", [])
        else:
            continue
        for node in path_nodes:
            node_id = node.get("id")
            if node_id and node_id not in nodes:
                nodes[node_id] = _record_node(node, mode, include_raw_fields=include_raw_fields)
        for rel in path_rels:
            edges.append(_record_rel(rel))
    return {"nodes": list(nodes.values()), "edges": edges}


def _should_trust_remote_code(model_name: str) -> bool:
    jina_path = os.environ.get("JINA_MODEL_PATH")
    if jina_path and os.path.normpath(jina_path) == os.path.normpath(model_name):
        return True
    return "jina" in model_name.lower()


def _get_embedder(model_name: str, device_name: Optional[str] = None) -> Tuple[Any, Any, Any]:
    if model_name in _embedder_cache:
        return _embedder_cache[model_name]
    trust_remote_code = _should_trust_remote_code(model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    model = AutoModel.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    device = torch.device(device_name or os.environ.get("EMBED_DEVICE", "cpu"))
    model.to(device)
    model.eval()
    _embedder_cache[model_name] = (tokenizer, model, device)
    return tokenizer, model, device


def _mean_pool(last_hidden: Any, mask: Any) -> Any:
    mask = mask.unsqueeze(-1).type_as(last_hidden)
    summed = (last_hidden * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1)
    return summed / counts


def _encode_texts(model: Any, texts: List[str], device: Any) -> Optional[List[List[float]]]:
    if not hasattr(model, "encode"):
        return None
    try:
        encoded = model.encode(texts, device=str(device))
    except TypeError:
        encoded = model.encode(texts)
    if isinstance(encoded, torch.Tensor):
        return encoded.detach().cpu().tolist()
    if hasattr(encoded, "tolist"):
        return encoded.tolist()
    return [list(vec) for vec in encoded]


def _embed_query(text: str, model_name: str) -> List[float]:
    tokenizer, model, device = _get_embedder(model_name)
    encoded = _encode_texts(model, [text], device)
    if encoded is not None:
        return encoded[0]
    with torch.no_grad():
        encoded = tokenizer(
            [text],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        outputs = model(**encoded)
        embedding = _mean_pool(outputs.last_hidden_state, encoded["attention_mask"]).cpu().tolist()[0]
    return embedding


def _is_preload_enabled(raw: Optional[str]) -> bool:
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _preload_embedder_on_startup() -> None:
    if not _is_preload_enabled(PRELOAD_EMBEDDER_ON_STARTUP):
        print("[embed] startup preload disabled by MCP_PRELOAD_EMBEDDER.")
        return
    model_name = (DEFAULT_MODEL or "").strip()
    if not model_name:
        print("[embed] startup preload skipped: empty model name.")
        return
    device_name = os.environ.get("EMBED_DEVICE", "cpu")
    print(f"[embed] preloading model at startup: model={model_name}, device={device_name}")
    _get_embedder(model_name, device_name=device_name)
    print("[embed] preload completed.")


def _qdrant_search(
    collection: str,
    vector: List[float],
    top_k: int,
    qdrant_url: str,
    vector_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Vector search via Qdrant Query API (``/points/query``).

    Kept byte-identical with the same function in ``fastmcp_server.py``,
    ``mcp/cplus/cplus_mcp.py`` and ``mcp/java/java_mcp.py`` — every
    backend ships its own copy and they all need the same v1/v2 routing
    contract. When changing this body, update those siblings too.

    See ``cplus_mcp.py`` for the full rationale on why this migrated
    away from the legacy ``/points/search`` endpoint (named-vector
    payload format was easy to malform and caused
    ``unified_mcp.semantic_search`` to return Qdrant 400 on v2 summary
    collections).
    """
    url = qdrant_url.rstrip("/") + f"/collections/{collection}/points/query"
    payload: Dict[str, Any] = {
        "query": vector,
        "limit": int(top_k),
        "with_payload": True,
    }
    if vector_name:
        payload["using"] = vector_name
    response = httpx.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    body = response.json()
    result = body.get("result")
    if isinstance(result, dict) and "points" in result:
        body = {**body, "result": result.get("points") or []}
    return body


def _normalize_collections(value: Optional[Any]) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        collections: List[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                collections.append(text)
        return collections
    text = str(value).strip()
    return [text] if text else []


def _merge_qdrant_results(
    collections: List[Tuple[str, Optional[str]]],
    vector: List[float],
    top_k: int,
    qdrant_url: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    combined: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    for col, vector_name in collections:
        try:
            payload = _qdrant_search(col, vector, top_k, qdrant_url, vector_name)
        except Exception as exc:
            errors.append({"collection": col, "error": str(exc)})
            continue
        for item in payload.get("result", []) or []:
            point_id = str(item.get("id"))
            score = item.get("score", 0)
            existing = combined.get(point_id)
            if existing is None or score > existing.get("score", 0):
                combined[point_id] = item
    results = sorted(combined.values(), key=lambda x: x.get("score", 0), reverse=True)[:top_k]
    return results, errors


def _parse_qdrant_collections(payload: Dict[str, Any]) -> List[str]:
    collections = payload.get("result", {}).get("collections", [])
    names: List[str] = []
    for item in collections:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


async def _fetch_qdrant_collections(
    qdrant_url: str,
    include_vectors: bool = False,
) -> Dict[str, Any]:
    url = qdrant_url.rstrip("/") + "/collections"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(url)
        response.raise_for_status()
    payload = response.json()
    collections = _parse_qdrant_collections(payload)
    response_payload: Dict[str, Any] = {"collections": collections, "raw": payload}
    if include_vectors and collections:
        tasks = [asyncio.create_task(_fetch_qdrant_collection_info(col, qdrant_url)) for col in collections]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        vectors_info: Dict[str, Any] = {}
        for col, result in zip(collections, results):
            if isinstance(result, Exception):
                vectors_info[col] = {"error": str(result)}
                continue
            vectors_cfg = (
                result.get("result", {})
                .get("config", {})
                .get("params", {})
                .get("vectors")
            )
            vectors_info[col] = {"sizes": _collect_vector_sizes(vectors_cfg)}
        response_payload["vectors"] = vectors_info
    return response_payload


async def _fetch_qdrant_collection_info(collection: str, qdrant_url: str) -> Dict[str, Any]:
    url = qdrant_url.rstrip("/") + f"/collections/{collection}"
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        response = await client.get(url)
        response.raise_for_status()
    return response.json()


def _collect_vector_sizes(vectors_config: Any) -> Dict[str, int]:
    sizes: Dict[str, int] = {}
    if not isinstance(vectors_config, dict):
        return sizes
    if "size" in vectors_config:
        size = vectors_config.get("size")
        if isinstance(size, (int, float)):
            sizes["default"] = int(size)
        return sizes
    for name, cfg in vectors_config.items():
        if not isinstance(cfg, dict):
            continue
        size = cfg.get("size")
        if isinstance(size, (int, float)):
            sizes[str(name)] = int(size)
    return sizes


def _select_vector_name(vectors_config: Any, vector_len: int) -> Optional[str]:
    if not isinstance(vectors_config, dict) or "size" in vectors_config:
        return None
    if isinstance(vectors_config, dict):
        for name, cfg in vectors_config.items():
            if isinstance(cfg, dict) and cfg.get("size") == vector_len:
                return str(name)
    return None


async def _filter_collections_for_vector(
    collections: List[str],
    vector_len: int,
    qdrant_url: str,
) -> Tuple[List[Tuple[str, Optional[str]]], List[Dict[str, str]]]:
    errors: List[Dict[str, str]] = []
    if not collections:
        return [], errors
    tasks = [asyncio.create_task(_fetch_qdrant_collection_info(col, qdrant_url)) for col in collections]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    selected: List[Tuple[str, Optional[str]]] = []
    for col, result in zip(collections, results):
        if isinstance(result, Exception):
            errors.append({"collection": col, "error": str(result)})
            continue
        vectors_cfg = (
            result.get("result", {})
            .get("config", {})
            .get("params", {})
            .get("vectors")
        )
        vector_name = _select_vector_name(vectors_cfg, vector_len)
        if isinstance(vectors_cfg, dict) and "size" in vectors_cfg:
            if vectors_cfg.get("size") == vector_len:
                selected.append((col, None))
            else:
                actual_size = vectors_cfg.get("size")
                errors.append(
                    {
                        "collection": col,
                        "error": (
                            f"Vector size mismatch (expected {vector_len}, "
                            f"got {actual_size})"
                        ),
                    }
                )
            continue
        if vector_name is not None:
            selected.append((col, vector_name))
        else:
            sizes = _collect_vector_sizes(vectors_cfg)
            if sizes:
                errors.append(
                    {
                        "collection": col,
                        "error": f"No matching vector size (expected {vector_len}); available: {sizes}",
                    }
                )
            else:
                errors.append({"collection": col, "error": "No matching vector size."})
    return selected, errors


def _is_db_not_found(exc: Exception) -> bool:
    if isinstance(exc, Neo4jError):
        code = getattr(exc, "code", "") or ""
        if "DatabaseNotFound" in code:
            return True
    text = str(exc)
    return "Database does not exist" in text or "graph reference" in text


def _format_collection_errors(errors: List[Dict[str, str]], max_items: int = 5) -> str:
    if not errors:
        return ""
    items: List[str] = []
    for err in errors[:max_items]:
        col = err.get("collection", "unknown")
        msg = err.get("error", "")
        if msg:
            items.append(f"{col}: {msg}")
        else:
            items.append(str(col))
    suffix = " ..." if len(errors) > max_items else ""
    return "; ".join(items) + suffix


async def _run_cypher(query: str, params: Dict[str, Any], db: str) -> List[Dict[str, Any]]:
    driver = await _get_graph_driver()
    records, summary, keys = await driver.execute_query(query, params, db)
    return records


async def _list_relationship_types(dbs: List[str]) -> List[str]:
    query_call = (
        "CALL db.relationshipTypes() YIELD relationshipType "
        "RETURN relationshipType AS rel_type"
    )
    query_show = "SHOW RELATIONSHIP TYPES YIELD relationshipType RETURN relationshipType AS rel_type"
    for db in [item for item in dbs if item]:
        try:
            try:
                rows = await _run_cypher(query_call, {}, db)
            except Exception:
                rows = await _run_cypher(query_show, {}, db)
            rel_types: List[str] = []
            for row in rows:
                rel_type = row.get("rel_type")
                if isinstance(rel_type, str):
                    rel_upper = rel_type.upper()
                    if rel_upper not in rel_types:
                        rel_types.append(rel_upper)
            return rel_types
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            logger.warning("Unable to list relationship types from %s: %s", db, exc)
            break
    return []


async def _resolve_trace_rel_types(
    rel_types_input: Any,
    parser_type: Optional[str],
    db_candidates: List[str],
) -> List[str]:
    is_explicit = rel_types_input is not None
    defaults = _get_default_flow_rel_types(parser_type)
    rel_types = _normalize_rel_types(rel_types_input, default=defaults)
    available = await _list_relationship_types(db_candidates)
    if not available:
        return rel_types
    available_set = set(available)
    filtered = [item for item in rel_types if item in available_set]
    if filtered:
        return filtered
    if is_explicit:
        return rel_types
    default_filtered = [item for item in defaults if item in available_set]
    return default_filtered or rel_types


async def _run_cypher_first(query: str, params: Dict[str, Any], dbs: List[str]) -> Tuple[str, List[Dict[str, Any]]]:
    last_error: Optional[Exception] = None
    candidates = [db for db in dbs if db]
    available = await _list_databases()
    if available:
        invalid = [db for db in candidates if db not in available]
        if invalid:
            logger.warning(
                "Ignoring unknown database(s): %s. Available: %s",
                ", ".join(sorted(set(invalid))),
                ", ".join(available),
            )
        candidates = [db for db in candidates if db in available]
        if not candidates:
            default_db = _normalize_db_name(DEFAULT_NEO4J_DB)
            if default_db in available:
                logger.warning("Falling back to default database: %s", default_db)
                candidates = [default_db]
    for db in candidates:
        try:
            result = await _run_cypher(query, params, db)
            return db, result
        except Exception as exc:
            last_error = exc
            if _is_db_not_found(exc):
                continue
            raise
    if last_error and _is_db_not_found(last_error):
        default_db = _normalize_db_name(DEFAULT_NEO4J_DB)
        raise RuntimeError(
            "Database not found. Use list_databases to inspect available DBs and "
            f"activate_project(database_name=...) to switch. Available: {available}. "
            f"Default: {default_db}."
        ) from last_error
    if last_error:
        raise last_error
    raise RuntimeError("No database candidates available")


async def _list_databases() -> List[str]:
    driver = await _get_graph_driver()
    records, summary, keys = await driver.execute_query("SHOW DATABASES", {}, DEFAULT_NEO4J_DB)
    names: List[str] = []
    for record in records:
        name = record.get("name")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def _load_ipc_messages_sync() -> List[Dict[str, Any]]:
    if not os.path.isfile(IPC_MESSAGES_PATH):
        logger.warning("IPC messages file not found: %s", IPC_MESSAGES_PATH)
        return []
    with open(IPC_MESSAGES_PATH, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        logger.warning("IPC messages file format invalid at %s: 'messages' must be a list.", IPC_MESSAGES_PATH)
        return []
    return [msg for msg in messages if isinstance(msg, dict)]


async def _load_ipc_messages() -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_load_ipc_messages_sync)


async def _query_ipc_messages_from_graph(
    *,
    sender_queries: List[str],
    receiver_queries: List[str],
    db_candidates: List[str],
    project_id: Optional[str],
) -> List[Dict[str, Any]]:
    query = """
    MATCH (m:Message)
    WHERE ($project_id = '' OR coalesce(m.project_id, '') = $project_id)
      AND (
        size($sender_queries) = 0
        OR any(q IN $sender_queries WHERE toLower(coalesce(m.sender, '')) CONTAINS toLower(q))
      )
      AND (
        size($receiver_queries) = 0
        OR any(q IN $receiver_queries WHERE toLower(coalesce(m.receiver, '')) CONTAINS toLower(q))
      )
    RETURN
      m.id AS id,
      m.name AS name,
      m.sender AS sender,
      m.receiver AS receiver,
      m.payload AS payload,
      m.response AS response,
      m.explanation AS explanation,
      m.file_path AS file_path,
      m.line AS line,
      m.confidence AS confidence,
      m.language AS language
    ORDER BY coalesce(m.confidence, 0.0) DESC, coalesce(m.file_path, ''), coalesce(m.line, 0)
    LIMIT 500
    """
    _, rows = await _run_cypher_first(
        query,
        {
            "project_id": (project_id or "").strip(),
            "sender_queries": sender_queries,
            "receiver_queries": receiver_queries,
        },
        db_candidates,
    )
    return [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "sender": row.get("sender"),
            "receiver": row.get("receiver"),
            "payload": row.get("payload"),
            "response": row.get("response"),
            "explanation": row.get("explanation"),
            "source": {"file": row.get("file_path"), "line": row.get("line")},
            "confidence": row.get("confidence"),
            "language": row.get("language"),
        }
        for row in rows
    ]


@mcp_server.tool(name="list_databases", description="List available Neo4j databases.", output_schema=None)
async def tool_list_databases(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _coerce_payload(payload)
    names = await _list_databases()
    default_db = _normalize_db_name(DEFAULT_NEO4J_DB)
    return {"databases": names, "default": default_db}


@mcp_server.tool(
    name="get_ipc_message",
    description=(
        "Query IPC messages by sender/receiver (Neo4j Message nodes first, JSON fallback, output_schema=None). "
        "If only sender is provided, return a list of receivers. "
        "If only receiver is provided, return a list of senders. "
        "If both sender and receiver are provided, return matching message objects."
    ),
    output_schema=None
)
async def tool_get_ipc_message(
    sender: Optional[str] = None,
    receiver: Optional[str] = None,
    senders: Optional[Any] = None,
    receivers: Optional[Any] = None,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    payload = _merge_payload(
        payload,
        {
            "sender": sender,
            "receiver": receiver,
            "senders": senders,
            "receivers": receivers,
            "db": db,
            "project_id": project_id,
        },
    )
    sender_queries = _normalize_string_list(payload.get("sender"))
    receiver_queries = _normalize_string_list(payload.get("receiver"))
    if not sender_queries:
        sender_queries = _normalize_string_list(payload.get("senders"))
    if not receiver_queries:
        receiver_queries = _normalize_string_list(payload.get("receivers"))
    if not sender_queries and not receiver_queries:
        raise ValueError("sender or receiver is required.")

    def _matches_any(field: Any, queries: List[str]) -> bool:
        if field is None:
            return False
        lowered = str(field).lower()
        return any(query.lower() in lowered for query in queries)

    graph_messages: List[Dict[str, Any]] = []
    graph_error: Optional[str] = None
    db_candidates = _resolve_db_candidates(payload.get("db"))
    try:
        graph_messages = await _query_ipc_messages_from_graph(
            sender_queries=sender_queries,
            receiver_queries=receiver_queries,
            db_candidates=db_candidates,
            project_id=payload.get("project_id"),
        )
    except Exception as exc:
        graph_error = str(exc)

    if graph_error is None:
        messages = graph_messages
    else:
        messages = await _load_ipc_messages()
        logger.warning("Message graph query failed; fallback to JSON: %s", graph_error)
    if sender_queries and receiver_queries:
        return [
            message
            for message in messages
            if _matches_any(message.get("sender"), sender_queries)
            and _matches_any(message.get("receiver"), receiver_queries)
        ]
    if sender_queries:
        receivers: List[str] = []
        seen: set[str] = set()
        for message in messages:
            if _matches_any(message.get("sender"), sender_queries):
                value = message.get("receiver")
                if value is None:
                    continue
                text = str(value)
                if text not in seen:
                    seen.add(text)
                    receivers.append(text)
        return receivers
    senders: List[str] = []
    seen: set[str] = set()
    for message in messages:
        if _matches_any(message.get("receiver"), receiver_queries):
            value = message.get("sender")
            if value is None:
                continue
            text = str(value)
            if text not in seen:
                seen.add(text)
                senders.append(text)
    return senders


@mcp_server.tool(
    name="activate_project",
    description="Set default parser_type and optional database_name for subsequent tool calls.",
    output_schema=None
)
async def tool_activate_project(
    parser_type: Optional[str] = None,
    database_name: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    payload = _merge_payload(
        payload,
        {"parser_type": parser_type, "database_name": database_name},
    )
    parser_type = payload.get("parser_type")
    database_name = payload.get("database_name")
    parser_type = (parser_type or "").strip() or None
    db_name = None
    if database_name is not None:
        db_text = str(database_name).strip()
        db_name = await _select_database_name(db_text)
    if not any([parser_type, db_name]):
        db_name = await _select_database_name(DEFAULT_NEO4J_DB)
    response = {
        "parser_type": parser_type,
        "database_name": db_name,
    }
    _set_active_project(parser_type, db_name)
    return response


async def _enrich_with_infra_community(
    items: List[Dict[str, Any]],
    db_candidates: List[str],
    infra_label: str = "InfraNode",
    belongs_rel: str = "BELONGS_TO",
) -> None:
    node_ids: List[str] = []
    for item in items:
        p = item.get("payload") or {}
        nid = p.get("node_id") or p.get("symbol_id")
        if nid:
            node_ids.append(str(nid))
    if not node_ids:
        return
    query = f"""
    UNWIND $node_ids AS nid
    MATCH (f {{id: nid}})-[:{belongs_rel}]->(infra:{infra_label})
    RETURN nid          AS node_id,
           infra.id     AS infra_id,
           infra.name   AS infra_name,
           infra.summary AS infra_summary,
           infra.community_id AS community_id
    """
    try:
        _, records = await _run_cypher_first(query, {"node_ids": node_ids}, db_candidates)
    except Exception as exc:
        logger.debug("[infra_enrich] Neo4j query failed (skipped): %s", exc)
        return
    infra_map: Dict[str, Dict[str, Any]] = {}
    for record in records:
        nid = record.get("node_id")
        if nid:
            infra_map[str(nid)] = {
                "id":           record.get("infra_id"),
                "name":         record.get("infra_name"),
                "summary":      record.get("infra_summary"),
                "community_id": record.get("community_id"),
            }
    for item in items:
        p = item.get("payload") or {}
        nid = str(p.get("node_id") or p.get("symbol_id") or "")
        infra = infra_map.get(nid)
        if infra:
            p["infra_community"] = infra


@mcp_server.tool(
    name="semantic_search",
    description=(
        "Semantic search over Qdrant embeddings. Supports content_mode/include_raw_fields. "
        "Use list_qdrant_collections first to discover available collections. "
        "Set with_neo4j=true to auto-attach InfraNode community context to each result."
    ),
    output_schema=None
)
async def tool_semantic_search(
    query: Optional[str] = None,
    mode: str = "combined",
    top_k: int = 10,
    model_path: Optional[str] = None,
    qdrant_url: Optional[str] = None,
    collection: Optional[str] = None,
    collection_comment: Optional[str] = None,
    collection_code: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    show_snippet: bool = False,
    show_comment: bool = False,
    with_neo4j: bool = False,
    neo4j_db: Optional[str] = None,
    neo4j_include_signature: bool = False,
    neo4j_include_comment: bool = False,
    neo4j_cache_path: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Any:
    payload = _merge_payload(
        payload,
        {
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
        },
    )
    query = payload.get("query")
    mode = payload.get("mode", "combined")
    top_k = payload.get("top_k", 10)
    model_path = payload.get("model_path")
    qdrant_url = payload.get("qdrant_url")
    collection = payload.get("collection")
    collection_comment = payload.get("collection_comment")
    collection_code = payload.get("collection_code")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    show_snippet = payload.get("show_snippet", False)
    show_comment = payload.get("show_comment", False)
    with_neo4j = payload.get("with_neo4j", False)
    neo4j_db = payload.get("neo4j_db")
    neo4j_include_signature = payload.get("neo4j_include_signature", False)
    neo4j_include_comment = payload.get("neo4j_include_comment", False)
    neo4j_cache_path = payload.get("neo4j_cache_path")
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required.")
    model_name = model_path or DEFAULT_MODEL
    qdrant_url = qdrant_url or DEFAULT_QDRANT_URL
    vector = _embed_query(query, model_name)
    vector_len = len(vector)
    logger.info("[semantic_search] model=%s vector_len=%s", model_name, vector_len)
    print(f"[semantic_search] model={model_name} vector_len={vector_len}", flush=True)
    base_collections = _normalize_collections(collection)
    explicit_base = bool(base_collections)
    if not base_collections:
        payload = await _fetch_qdrant_collections(qdrant_url)
        base_collections = payload.get("collections", [])
    if not base_collections:
        raise ValueError("No Qdrant collections available. Use list_qdrant_collections to verify.")
    filtered_base, base_errors = await _filter_collections_for_vector(base_collections, vector_len, qdrant_url)
    if not filtered_base:
        details = _format_collection_errors(base_errors)
        if explicit_base:
            message = f"Provided collections do not match embedding size {vector_len}."
        else:
            message = f"No Qdrant collections match embedding size {vector_len}."
        if details:
            message = f"{message} Details: {details}"
        raise ValueError(
            f"{message} Use list_qdrant_collections(include_vectors=true) to verify sizes."
        )
    comment_raw = _normalize_collections(collection_comment)
    if comment_raw:
        comment_collections, comment_errors = await _filter_collections_for_vector(
            comment_raw,
            vector_len,
            qdrant_url,
        )
    else:
        comment_collections, comment_errors = filtered_base, base_errors
    code_raw = _normalize_collections(collection_code)
    if code_raw:
        code_collections, code_errors = await _filter_collections_for_vector(
            code_raw,
            vector_len,
            qdrant_url,
        )
    else:
        code_collections, code_errors = filtered_base, base_errors
    selected_mode = _normalize_content_mode(content_mode)
    results: Dict[str, Any] = {"mode": mode, "query": query, "results": [], "content_mode": selected_mode}
    if mode == "comment":
        items, errors = _merge_qdrant_results(comment_collections, vector, top_k, qdrant_url)
        results["results"] = items
        merged_errors = comment_errors + errors
        if merged_errors:
            results["errors"] = merged_errors
        for item in results["results"]:
            payload_item = item.get("payload")
            if isinstance(payload_item, dict):
                node_id = str(payload_item.get("symbol_id") or item.get("id") or "")
                payload_item["content_mode"] = selected_mode
                payload_item["content"] = _select_content(payload_item, node_id, selected_mode)
                if not include_raw_fields:
                    _prune_content_fields(payload_item)
        if with_neo4j:
            await _enrich_with_infra_community(results["results"], _resolve_db_candidates(neo4j_db))
        return results
    if mode == "code":
        items, errors = _merge_qdrant_results(code_collections, vector, top_k, qdrant_url)
        results["results"] = items
        merged_errors = code_errors + errors
        if merged_errors:
            results["errors"] = merged_errors
        for item in results["results"]:
            payload_item = item.get("payload")
            if isinstance(payload_item, dict):
                node_id = str(payload_item.get("symbol_id") or item.get("id") or "")
                payload_item["content_mode"] = selected_mode
                payload_item["content"] = _select_content(payload_item, node_id, selected_mode)
                if not include_raw_fields:
                    _prune_content_fields(payload_item)
        if with_neo4j:
            await _enrich_with_infra_community(results["results"], _resolve_db_candidates(neo4j_db))
        return results
    combined_map = {(col, name) for col, name in filtered_base}
    combined_map.update(comment_collections)
    combined_map.update(code_collections)
    combined_collections = list(combined_map)
    items, errors = _merge_qdrant_results(combined_collections, vector, top_k, qdrant_url)
    results["results"] = items
    merged_errors = base_errors + comment_errors + code_errors + errors
    if merged_errors:
        results["errors"] = merged_errors
    for item in results["results"]:
        payload = item.get("payload")
        if isinstance(payload, dict):
            node_id = str(payload.get("symbol_id") or item.get("id") or "")
            payload["content_mode"] = selected_mode
            payload["content"] = _select_content(payload, node_id, selected_mode)
            if not include_raw_fields:
                _prune_content_fields(payload)
    if with_neo4j:
        await _enrich_with_infra_community(results["results"], _resolve_db_candidates(neo4j_db))
    return results


@mcp_server.tool(name="list_qdrant_collections", description="List available Qdrant collections.", output_schema=None)
async def tool_list_qdrant_collections(
    qdrant_url: Optional[str] = None,
    include_vectors: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(payload, {"qdrant_url": qdrant_url, "include_vectors": include_vectors})
    qdrant_url = payload.get("qdrant_url") or DEFAULT_QDRANT_URL
    include_vectors = payload.get("include_vectors", False)
    return await _fetch_qdrant_collections(qdrant_url, include_vectors=include_vectors)


@mcp_server.tool(
    name="get_symbol",
    description="Retrieve metadata for a specific node by id. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_get_symbol(
    node_id: Any = None,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "node_id": node_id,
            "db": db,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    node_id = payload.get("node_id")
    db = payload.get("db")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if node_id is None:
        raise ValueError("node_id is required.")
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    node_id = str(node_id)
    
    driver = await _get_graph_driver()
    for db_candidate in candidates:
        try:
            node = await driver.find_node_by_id(node_id, project_id=project_id, database=db_candidate)
            if node:
                mode = _normalize_content_mode(content_mode)
                return {"db": db_candidate, "node": _record_node(node, mode, include_raw_fields)}
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    raise RuntimeError(f"Node {node_id} not found in any db.")


@mcp_server.tool(
    name="list_possible_calls",
    description="List POSSIBLE_CALLS edges (virtual dispatch, output_schema=None). Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_list_possible_calls(
    db: Optional[str] = None,
    limit: int = 200,
    top_k: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "db": db,
            "limit": limit,
            "top_k": top_k,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    db = payload.get("db")
    limit_value = payload.get("limit")
    if limit_value is None:
        limit_value = payload.get("top_k")
    limit = limit_value if limit_value is not None else 200
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    cypher = (
        "MATCH (a:Function)-[r:POSSIBLE_CALLS]->(b:Function) "
        "WHERE ($project_id IS NULL OR a.project_id = $project_id) "
        "AND ($project_id IS NULL OR b.project_id = $project_id) "
        "RETURN a, b, r LIMIT $limit"
    )
    used_db, results = await _run_cypher_first(
        cypher,
        {"limit": int(limit), "project_id": project_id},
        db_candidates,
    )
    mode = _normalize_content_mode(content_mode)
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    for row in results:
        a_node = _record_node(row["a"], mode, include_raw_fields)
        b_node = _record_node(row["b"], mode, include_raw_fields)
        if a_node.get("id"):
            nodes[a_node["id"]] = a_node
        if b_node.get("id"):
            nodes[b_node["id"]] = b_node
        edges.append(_record_rel(row["r"]))
    return {"db": used_db, "nodes": list(nodes.values()), "edges": edges}


@mcp_server.tool(
    name="get_node_details",
    description="Fetch metadata for multiple node IDs. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_get_node_details(
    node_ids: Optional[List[Any]] = None,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "node_ids": node_ids,
            "db": db,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    node_ids = payload.get("node_ids")
    db = payload.get("db")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    node_ids = _normalize_string_list(node_ids)
    if not node_ids:
        raise ValueError("node_ids must be a non-empty list.")
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    ids = [str(item) for item in node_ids]
    query = "MATCH (n) WHERE n.id IN $ids AND ($project_id IS NULL OR n.project_id = $project_id) RETURN n"
    used_db, results = await _run_cypher_first(query, {"ids": ids, "project_id": project_id}, candidates)
    if results:
        mode = _normalize_content_mode(content_mode)
        nodes = [_record_node(item["n"], mode, include_raw_fields) for item in results]
        return {"db": used_db, "nodes": nodes}
    return {"db": used_db, "nodes": []}


@mcp_server.tool(
    name="query_subgraph",
    description="Return call graph context around a function ID. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_query_subgraph(
    db: Optional[str] = None,
    function_id: Any = None,
    direction: str = "all",
    max_depth: int = 2,
    include_possible: bool = False,
    include_fp: bool = False,
    parser_type: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "db": db,
            "function_id": function_id,
            "direction": direction,
            "max_depth": max_depth,
            "include_possible": include_possible,
            "include_fp": include_fp,
            "parser_type": parser_type,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    db = payload.get("db")
    function_id = payload.get("function_id", payload.get("id"))
    direction = payload.get("direction", "all")
    max_depth = payload.get("max_depth", 2)
    include_possible = bool(payload.get("include_possible", False))
    include_fp = bool(payload.get("include_fp", False))
    parser_type = payload.get("parser_type")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if function_id is None:
        raise ValueError("function_id is required.")
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    function_id = str(function_id)
    depth = _normalize_depth(max_depth, default=2, max_limit=10)
    direction = direction.lower()
    base_rel_types = ["CALLS"]
    if include_possible:
        base_rel_types.append("POSSIBLE_CALLS")
    if include_fp:
        base_rel_types.append("CALLS_FUNCTION_POINTER")
    rel_types = await _resolve_trace_rel_types(base_rel_types, parser_type, candidates)
    rel_pattern = f"[:{'|'.join(rel_types)}*1..{depth}]"
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            paths: List[Any] = []
            if direction in {"incoming", "in"}:
                query = (
                    f"MATCH (f:Function) WHERE f.id = $id "
                    "AND ($project_id IS NULL OR f.project_id = $project_id) "
                    f"MATCH p=(n:Function)-{rel_pattern}->(f) RETURN p"
                )
                _, result = await _run_cypher_first(query, {"id": function_id, "project_id": project_id}, [candidate])
                paths.extend([row["p"] for row in result])
            elif direction in {"outgoing", "out"}:
                query = (
                    f"MATCH (f:Function) WHERE f.id = $id "
                    "AND ($project_id IS NULL OR f.project_id = $project_id) "
                    f"MATCH p=(f)-{rel_pattern}->(n:Function) RETURN p"
                )
                _, result = await _run_cypher_first(query, {"id": function_id, "project_id": project_id}, [candidate])
                paths.extend([row["p"] for row in result])
            else:
                query_out = (
                    f"MATCH (f:Function) WHERE f.id = $id "
                    "AND ($project_id IS NULL OR f.project_id = $project_id) "
                    f"MATCH p=(f)-{rel_pattern}->(n:Function) RETURN p"
                )
                query_in = (
                    f"MATCH (f:Function) WHERE f.id = $id "
                    "AND ($project_id IS NULL OR f.project_id = $project_id) "
                    f"MATCH p=(n:Function)-{rel_pattern}->(f) RETURN p"
                )
                _, result_out = await _run_cypher_first(query_out, {"id": function_id, "project_id": project_id}, [candidate])
                _, result_in = await _run_cypher_first(query_in, {"id": function_id, "project_id": project_id}, [candidate])
                paths.extend([row["p"] for row in result_out])
                paths.extend([row["p"] for row in result_in])
            graph = _paths_to_graph(
                paths,
                content_mode=content_mode or "auto",
                include_raw_fields=include_raw_fields,
            )
            graph["db"] = candidate
            return graph
        except Exception as exc:
            last_error = exc
            if _is_db_not_found(exc):
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError(f"No subgraph found for node {function_id} in any db.")


@mcp_server.tool(
    name="find_paths",
    description="Find call paths between two functions. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_find_paths(
    db: Optional[str] = None,
    start_function_id: Any = None,
    end_function_id: Any = None,
    max_depth: int = 8,
    include_possible: bool = False,
    include_fp: bool = False,
    parser_type: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "db": db,
            "start_function_id": start_function_id,
            "end_function_id": end_function_id,
            "max_depth": max_depth,
            "include_possible": include_possible,
            "include_fp": include_fp,
            "parser_type": parser_type,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    db = payload.get("db")
    start_function_id = payload.get("start_function_id")
    end_function_id = payload.get("end_function_id")
    max_depth = payload.get("max_depth", 8)
    include_possible = bool(payload.get("include_possible", False))
    include_fp = bool(payload.get("include_fp", False))
    parser_type = payload.get("parser_type")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if start_function_id is None or end_function_id is None:
        raise ValueError("start_function_id and end_function_id are required.")
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    start_id = str(start_function_id)
    end_id = str(end_function_id)
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    base_rel_types = ["CALLS"]
    if include_possible:
        base_rel_types.append("POSSIBLE_CALLS")
    if include_fp:
        base_rel_types.append("CALLS_FUNCTION_POINTER")
    rel_types = await _resolve_trace_rel_types(base_rel_types, parser_type, candidates)
    rel_pattern = f"[:{'|'.join(rel_types)}*..{depth}]"
    query = (
        f"MATCH (a:Function) WHERE a.id = $start "
        "AND ($project_id IS NULL OR a.project_id = $project_id) "
        f"MATCH (b:Function) WHERE b.id = $end "
        "AND ($project_id IS NULL OR b.project_id = $project_id) "
        f"MATCH p=shortestPath((a)-{rel_pattern}->(b)) RETURN p"
    )
    used_db, result = await _run_cypher_first(query, {"start": start_id, "end": end_id, "project_id": project_id}, candidates)
    if result:
        paths = [row["p"] for row in result]
        graph = _paths_to_graph(
            paths,
            content_mode=content_mode or "auto",
            include_raw_fields=include_raw_fields,
        )
        graph["db"] = used_db
        return graph
    raise RuntimeError("No path found in any db.")


@mcp_server.tool(
    name="find_path_between_module",
    description="Find call paths between modules. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_find_path_between_module(
    source_modules: Optional[List[str]] = None,
    target_modules: Optional[List[str]] = None,
    source_module: Optional[Any] = None,
    target_module: Optional[Any] = None,
    db: Optional[str] = None,
    max_depth: int = 8,
    include_possible: bool = False,
    include_fp: bool = False,
    parser_type: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "source_modules": source_modules,
            "target_modules": target_modules,
            "source_module": source_module,
            "target_module": target_module,
            "db": db,
            "max_depth": max_depth,
            "include_possible": include_possible,
            "include_fp": include_fp,
            "parser_type": parser_type,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    source_modules = payload.get("source_modules")
    if source_modules is None:
        source_modules = payload.get("source_module")
    target_modules = payload.get("target_modules")
    if target_modules is None:
        target_modules = payload.get("target_module")
    db = payload.get("db")
    max_depth = payload.get("max_depth", 8)
    include_possible = bool(payload.get("include_possible", False))
    include_fp = bool(payload.get("include_fp", False))
    parser_type = payload.get("parser_type")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    debug = bool(payload.get("debug", False))
    debug_info: Dict[str, Any] = {}
    source_modules = _normalize_string_list(source_modules)
    target_modules = _normalize_string_list(target_modules)
    if not source_modules or not target_modules:
        raise ValueError("source_modules and target_modules must be non-empty lists.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    base_rel_types = ["CALLS"]
    if include_possible:
        base_rel_types.append("POSSIBLE_CALLS")
    if include_fp:
        base_rel_types.append("CALLS_FUNCTION_POINTER")
    rel_types = await _resolve_trace_rel_types(base_rel_types, parser_type, db_candidates)
    rel_pattern = f"[:{'|'.join(rel_types)}*..{depth}]"
    if debug:
        logger.info(
            "find_path_between_module debug: db_candidates=%s depth=%s sources=%s targets=%s",
            db_candidates,
            depth,
            source_modules,
            target_modules,
        )
        debug_info["db_candidates"] = db_candidates
        debug_info["depth"] = depth
        debug_info["sources"] = source_modules
        debug_info["targets"] = target_modules

        count_query = (
            "WITH [t IN $sources | toLower(t)] AS sources "
            "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
            "WHERE any(token IN sources WHERE "
            "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
            "RETURN count(DISTINCT s) AS count"
        )
        used_db, source_count = await _run_cypher_first(
            count_query,
            {"sources": source_modules},
            db_candidates,
        )
        debug_info["source_function_count"] = source_count
        logger.info(
            "find_path_between_module debug: db=%s source_function_count=%s",
            used_db,
            source_count,
        )

        target_count_query = (
            "WITH [t IN $targets | toLower(t)] AS targets "
            "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
            "WHERE any(token IN targets WHERE "
            "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
            "RETURN count(DISTINCT t) AS count"
        )
        _, target_count = await _run_cypher_first(
            target_count_query,
            {"targets": target_modules},
            db_candidates,
        )
        debug_info["target_function_count"] = target_count
        logger.info(
            "find_path_between_module debug: db=%s target_function_count=%s",
            used_db,
            target_count,
        )

        calls_count_query = (
            "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
            "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
            "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
            "WHERE any(token IN sources WHERE "
            "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
            "AND any(token IN targets WHERE "
            "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
            "MATCH (s)-[:CALLS]->(t) "
            "RETURN count(*) AS count"
        )
        _, calls_count = await _run_cypher_first(
            calls_count_query,
            {"sources": source_modules, "targets": target_modules},
            db_candidates,
        )
        debug_info["direct_calls_count"] = calls_count
        logger.info(
            "find_path_between_module debug: db=%s direct_calls_count=%s",
            used_db,
            calls_count,
        )

        reverse_calls_count_query = (
            "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
            "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
            "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
            "WHERE any(token IN sources WHERE "
            "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
            "AND any(token IN targets WHERE "
            "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
            "MATCH (t)-[:CALLS]->(s) "
            "RETURN count(*) AS count"
        )
        _, reverse_calls_count = await _run_cypher_first(
            reverse_calls_count_query,
            {"sources": source_modules, "targets": target_modules},
            db_candidates,
        )
        debug_info["reverse_calls_count"] = reverse_calls_count
        logger.info(
            "find_path_between_module debug: db=%s reverse_calls_count=%s",
            used_db,
            reverse_calls_count,
        )

        indirect_count_query = (
            "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
            "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
            "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
            "WHERE any(token IN sources WHERE "
            "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
            "AND any(token IN targets WHERE "
            "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
            f"MATCH p=(s)-{rel_pattern}->(t) "
            "RETURN count(p) AS count"
        )
        _, indirect_count = await _run_cypher_first(
            indirect_count_query,
            {"sources": source_modules, "targets": target_modules},
            db_candidates,
        )
        debug_info["indirect_calls_count"] = indirect_count
        logger.info(
            "find_path_between_module debug: db=%s indirect_calls_count=%s",
            used_db,
            indirect_count,
        )
    query = (
        "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
        "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
        "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
        "WHERE any(token IN sources WHERE "
        "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
        "toLower(coalesce(sf.path, '')) CONTAINS token OR "
        "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
        "AND any(token IN targets WHERE "
        "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
        "toLower(coalesce(tf.path, '')) CONTAINS token OR "
        "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
        "AND ($project_id IS NULL OR s.project_id = $project_id) "
        "AND ($project_id IS NULL OR t.project_id = $project_id) "
        "AND s.id <> t.id "
        f"MATCH p=shortestPath((s)-{rel_pattern}->(t)) "
        "RETURN p LIMIT 10"
    )
    used_db, results = await _run_cypher_first(
        query,
        {"sources": source_modules, "targets": target_modules, "project_id": project_id},
        db_candidates,
    )
    if not results:
        fallback_query = (
            "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
            "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
            "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
            "WHERE any(token IN sources WHERE "
            "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
            "AND any(token IN targets WHERE "
            "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
            "AND ($project_id IS NULL OR s.project_id = $project_id) "
            "AND ($project_id IS NULL OR t.project_id = $project_id) "
            "AND s.id <> t.id "
            f"MATCH p=shortestPath((s)-{rel_pattern}-(t)) "
            "RETURN p LIMIT 10"
        )
        used_db, results = await _run_cypher_first(
            fallback_query,
            {"sources": source_modules, "targets": target_modules, "project_id": project_id},
            db_candidates,
        )
    paths = [row["p"] for row in results]
    graph = _paths_to_graph(
        paths,
        content_mode=content_mode or "auto",
        include_raw_fields=include_raw_fields,
    )
    graph["db"] = used_db
    if debug:
        graph["debug"] = debug_info
    return graph


@mcp_server.tool(
    name="listup_symbols_matching_file_path",
    description="List symbols by file path token. Supports content_mode/include_raw_fields. Use node_types=['Function'] to list only functions.",
    output_schema=None
)
async def tool_listup_symbols_matching_file_path(
    modules: Optional[List[str]] = None,
    module: Optional[Any] = None,
    db: Optional[str] = None,
    node_types: Optional[List[str]] = None,
    max_depth: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "modules": modules,
            "module": module,
            "db": db,
            "node_types": node_types,
            "max_depth": max_depth,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    modules = payload.get("modules")
    if modules is None:
        modules = payload.get("module")
    db = payload.get("db")
    node_types_filter = payload.get("node_types")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    modules = _normalize_string_list(modules)
    if not modules:
        raise ValueError("modules must be a non-empty list.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    
    # Build node type filter
    if node_types_filter:
        types = _normalize_string_list(node_types_filter)
        type_conditions = "(" + " OR ".join([f"n:{t}" for t in types]) + ")"
    else:
        type_conditions = _android_symbol_labels()
    
    cypher = (
        f"MATCH (n) WHERE {type_conditions} "
        f"AND {_android_file_match_predicate()} "
        "AND ($project_id IS NULL OR n.project_id = $project_id) "
        "RETURN n"
    )
    used_db, results = await _run_cypher_first(cypher, {"modules": modules, "project_id": project_id}, db_candidates)
    mode = _normalize_content_mode(content_mode)
    nodes = [_record_node(row["n"], mode, include_raw_fields) for row in results]
    return {"db": used_db, "symbols": nodes}


@mcp_server.tool(
    name="listup_class_matching_path",
    description="List functions for classes/types by name. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_listup_class_matching_path(
    class_names: Optional[List[str]] = None,
    class_name: Optional[Any] = None,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "class_names": class_names,
            "class_name": class_name,
            "db": db,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    class_names = payload.get("class_names")
    if class_names is None:
        class_names = payload.get("class_name")
    db = payload.get("db")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    class_names = _normalize_string_list(class_names)
    if not class_names:
        raise ValueError("class_names must be a non-empty list.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    cypher = (
        "MATCH (c) "
        "WHERE (c:Class OR c:Type) "
        "AND any(token IN $classes WHERE "
        "toLower(c.name) CONTAINS toLower(token) OR toLower(c.qualified_name) CONTAINS toLower(token)) "
        "AND ($project_id IS NULL OR c.project_id = $project_id) "
        "OPTIONAL MATCH (c)-[:DECLARES]->(f:Function) "
        "WHERE ($project_id IS NULL OR f.project_id = $project_id) "
        "RETURN c, f"
    )
    used_db, results = await _run_cypher_first(cypher, {"classes": class_names, "project_id": project_id}, db_candidates)
    mode = _normalize_content_mode(content_mode)
    classes_seen: Dict[str, Dict[str, Any]] = {}
    functions: List[Dict[str, Any]] = []
    for row in results:
        c_rec = _record_node(row["c"], mode, include_raw_fields)
        if c_rec.get("id") and c_rec["id"] not in classes_seen:
            classes_seen[c_rec["id"]] = c_rec
        if row.get("f") is not None:
            functions.append(_record_node(row["f"], mode, include_raw_fields))
    return {"db": used_db, "classes": list(classes_seen.values()), "functions": functions}


@mcp_server.tool(
    name="list_up_entrypoint",
    description=(
        "List entrypoint functions that are called from outside the given modules. "
        "Supports content_mode/include_raw_fields."
    ),
    output_schema=None
)
async def tool_list_up_entrypoint(
    modules: Optional[List[str]] = None,
    module: Optional[Any] = None,
    db: Optional[str] = None,
    limit: int = 200,
    top_k: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "modules": modules,
            "module": module,
            "db": db,
            "limit": limit,
            "top_k": top_k,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    modules = payload.get("modules")
    if modules is None:
        modules = payload.get("module")
    db = payload.get("db")
    limit_value = payload.get("limit")
    if limit_value is None:
        limit_value = payload.get("top_k")
    limit = limit_value if limit_value is not None else 200
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    modules = _normalize_string_list(modules)
    if not modules:
        raise ValueError("modules must be a non-empty list.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    cypher = (
        "MATCH (caller:Function)-[:CALLS]->(f:Function) "
        "WHERE any(token IN $modules WHERE toLower(coalesce(f.file_path, '')) CONTAINS toLower(token)) "
        "AND none(token IN $modules WHERE toLower(coalesce(caller.file_path, '')) CONTAINS toLower(token)) "
        "AND (f.kind IS NULL OR f.kind <> 'lambda') "
        "AND ($project_id IS NULL OR f.project_id = $project_id) "
        "RETURN DISTINCT f LIMIT $limit"
    )
    used_db, results = await _run_cypher_first(
        cypher,
        {"modules": modules, "limit": int(limit), "project_id": project_id},
        db_candidates,
    )
    mode = _normalize_content_mode(content_mode)
    functions = [_record_node(row["f"], mode, include_raw_fields) for row in results]
    return {"db": used_db, "functions": functions}


@mcp_server.tool(
    name="trace_flow",
    description=(
        "Trace a call/interaction flow across Android graph edges (UI resources, routes, intents, events, etc., output_schema=None). "
        "Supports content_mode/include_raw_fields."
    ),
    output_schema=None
)
async def tool_trace_flow(
    start_id: Any = None,
    end_id: Any = None,
    db: Optional[str] = None,
    parser_type: Optional[str] = None,
    max_depth: int = 6,
    direction: str = "out",
    rel_types: Optional[List[str]] = None,
    relationship_types: Optional[Any] = None,
    limit: int = 30,
    top_k: Optional[int] = None,
    debug: bool = False,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "start_id": start_id,
            "end_id": end_id,
            "db": db,
            "parser_type": parser_type,
            "max_depth": max_depth,
            "direction": direction,
            "rel_types": rel_types,
            "relationship_types": relationship_types,
            "limit": limit,
            "top_k": top_k,
            "debug": debug,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    start_id = payload.get("start_id")
    end_id = payload.get("end_id")
    db = payload.get("db")
    parser_type = payload.get("parser_type")
    max_depth = payload.get("max_depth", 6)
    direction = (payload.get("direction") or "out").lower()
    limit_value = payload.get("limit")
    if limit_value is None:
        limit_value = payload.get("top_k")
    limit = int(limit_value if limit_value is not None else 30)
    debug = bool(payload.get("debug", False))
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if start_id is None:
        raise ValueError("start_id is required.")
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    rel_value = payload.get("rel_types")
    if rel_value is None:
        rel_value = payload.get("relationship_types")
    rel_types = await _resolve_trace_rel_types(rel_value, parser_type, candidates)
    depth = _normalize_depth(max_depth, default=6, max_limit=20)
    rel_match = _build_rel_match(rel_types, depth, direction)
    start_id = str(start_id)
    end_id = str(end_id) if end_id is not None else None

    if end_id is not None:
        query = (
            "MATCH (a {id: $start}) "
            "WHERE ($project_id IS NULL OR a.project_id = $project_id) "
            "MATCH (b {id: $end}) "
            "WHERE ($project_id IS NULL OR b.project_id = $project_id) "
            f"MATCH p=shortestPath((a){rel_match}(b)) "
            "RETURN p"
        )
        used_db, result = await _run_cypher_first(
            query,
            {"start": start_id, "end": end_id, "project_id": project_id},
            candidates,
        )
        if not result:
            if debug:
                return {
                    "db": used_db,
                    "nodes": [],
                    "edges": [],
                    "direction": direction,
                    "rel_types": rel_types,
                    "max_depth": depth,
                    "reason": "no_path",
                }
            raise RuntimeError("No path found in any db.")
        paths = [row["p"] for row in result]
    else:
        query = (
            "MATCH (a {id: $start}) "
            "WHERE ($project_id IS NULL OR a.project_id = $project_id) "
            f"MATCH p=(a){rel_match}(n) "
            "RETURN p LIMIT $limit"
        )
        used_db, result = await _run_cypher_first(
            query,
            {"start": start_id, "limit": limit, "project_id": project_id},
            candidates,
        )
        if not result:
            return {
                "db": used_db,
                "nodes": [],
                "edges": [],
                "direction": direction,
                "rel_types": rel_types,
                "max_depth": depth,
                "reason": "no_path",
            }
        paths = [row["p"] for row in result]

    graph = _paths_to_graph(
        paths,
        content_mode=content_mode or "auto",
        include_raw_fields=include_raw_fields,
    )
    graph["db"] = used_db
    graph["direction"] = direction
    graph["rel_types"] = rel_types
    graph["max_depth"] = depth
    return graph


@mcp_server.tool(
    name="trace_flow_between_module",
    description=(
        "Trace flow paths between functions in two modules using Android interaction edges (CALLS, routes, intents, events, etc., output_schema=None). "
        "Supports content_mode/include_raw_fields."
    ),
    output_schema=None
)
async def tool_trace_flow_between_module(
    source_modules: Optional[List[str]] = None,
    target_modules: Optional[List[str]] = None,
    source_module: Optional[Any] = None,
    target_module: Optional[Any] = None,
    db: Optional[str] = None,
    parser_type: Optional[str] = None,
    max_depth: int = 8,
    direction: str = "out",
    rel_types: Optional[List[str]] = None,
    relationship_types: Optional[Any] = None,
    limit: int = 10,
    top_k: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "source_modules": source_modules,
            "target_modules": target_modules,
            "source_module": source_module,
            "target_module": target_module,
            "db": db,
            "parser_type": parser_type,
            "max_depth": max_depth,
            "direction": direction,
            "rel_types": rel_types,
            "relationship_types": relationship_types,
            "limit": limit,
            "top_k": top_k,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    source_modules = payload.get("source_modules")
    if source_modules is None:
        source_modules = payload.get("source_module")
    target_modules = payload.get("target_modules")
    if target_modules is None:
        target_modules = payload.get("target_module")
    db = payload.get("db")
    parser_type = payload.get("parser_type")
    max_depth = payload.get("max_depth", 8)
    direction = (payload.get("direction") or "out").lower()
    limit_value = payload.get("limit")
    if limit_value is None:
        limit_value = payload.get("top_k")
    limit = int(limit_value if limit_value is not None else 10)
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    source_modules = _normalize_string_list(source_modules)
    target_modules = _normalize_string_list(target_modules)
    if not source_modules or not target_modules:
        raise ValueError("source_modules and target_modules must be non-empty lists.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    rel_value = payload.get("rel_types")
    if rel_value is None:
        rel_value = payload.get("relationship_types")
    rel_types = await _resolve_trace_rel_types(rel_value, parser_type, db_candidates)
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    rel_match = _build_rel_match(rel_types, depth, direction)
    query = (
        "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
        "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
        "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
        "WHERE any(token IN sources WHERE "
        "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
        "toLower(coalesce(sf.path, '')) CONTAINS token OR "
        "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
        "AND any(token IN targets WHERE "
        "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
        "toLower(coalesce(tf.path, '')) CONTAINS token OR "
        "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
        "AND ($project_id IS NULL OR s.project_id = $project_id) "
        "AND ($project_id IS NULL OR t.project_id = $project_id) "
        "AND s.id <> t.id "
        f"MATCH p=shortestPath((s){rel_match}(t)) "
        "RETURN p LIMIT $limit"
    )
    used_db, results = await _run_cypher_first(
        query,
        {"sources": source_modules, "targets": target_modules, "limit": limit, "project_id": project_id},
        db_candidates,
    )
    if not results and direction not in {"both", "any", "undirected"}:
        rel_match = _build_rel_match(rel_types, depth, "both")
        fallback_query = (
            "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets "
            "MATCH (s:Function)<-[:CONTAINS]-(sf:File) "
            "MATCH (t:Function)<-[:CONTAINS]-(tf:File) "
            "WHERE any(token IN sources WHERE "
            "toLower(coalesce(s.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.path, '')) CONTAINS token OR "
            "toLower(coalesce(sf.file_path, '')) CONTAINS token) "
            "AND any(token IN targets WHERE "
            "toLower(coalesce(t.file_path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.path, '')) CONTAINS token OR "
            "toLower(coalesce(tf.file_path, '')) CONTAINS token) "
            "AND ($project_id IS NULL OR s.project_id = $project_id) "
            "AND ($project_id IS NULL OR t.project_id = $project_id) "
            "AND s.id <> t.id "
            f"MATCH p=shortestPath((s){rel_match}(t)) "
            "RETURN p LIMIT $limit"
        )
        used_db, results = await _run_cypher_first(
            fallback_query,
            {"sources": source_modules, "targets": target_modules, "limit": limit, "project_id": project_id},
            db_candidates,
        )
    paths = [row["p"] for row in results]
    graph = _paths_to_graph(
        paths,
        content_mode=content_mode or "auto",
        include_raw_fields=include_raw_fields,
    )
    graph["db"] = used_db
    graph["direction"] = direction
    graph["rel_types"] = rel_types
    graph["max_depth"] = depth
    return graph


@mcp_server.tool(
    name="search_functions",
    description="Search nodes by name/qualified_name. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_search_functions(
    query: Optional[str] = None,
    limit: int = 50,
    top_k: Optional[int] = None,
    db: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "query": query,
            "limit": limit,
            "top_k": top_k,
            "db": db,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    query = payload.get("query")
    limit_value = payload.get("limit")
    if limit_value is None:
        limit_value = payload.get("top_k")
    limit = limit_value if limit_value is not None else 50
    db = payload.get("db")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    qs = [t.lower().strip() for t in query.split("|") if t.strip()]
    fallback_cypher = (
        f"MATCH (n) WHERE {_android_symbol_labels()} "
        f"AND ({_android_search_predicate()}) "
        "RETURN n LIMIT $limit"
    )
    node_labels_predicate = _android_symbol_labels().replace("n:", "node:")
    fulltext_query = " OR ".join(qs)
    fulltext_cypher = (
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score "
        f"WHERE {node_labels_predicate} "
        "RETURN node AS n ORDER BY score DESC LIMIT $limit"
    )
    try:
        used_db, results = await _run_cypher_first(
            fulltext_cypher,
            {"index_name": FULLTEXT_SYMBOL_TEXT_INDEX, "query": fulltext_query, "limit": int(limit)},
            db_candidates,
        )
        if not results:
            used_db, results = await _run_cypher_first(
                fallback_cypher,
                {"qs": qs, "limit": int(limit)},
                db_candidates,
            )
    except Exception:
        used_db, results = await _run_cypher_first(
            fallback_cypher,
            {"qs": qs, "limit": int(limit)},
            db_candidates,
        )
    mode = _normalize_content_mode(content_mode)
    nodes = [_record_node(row["n"], mode, include_raw_fields) for row in results]
    ids = [node.get("id") for node in nodes if node.get("id")]
    return {"db": used_db, "results": nodes, "ids": ids}


@mcp_server.tool(
    name="search_by_code",
    description="Search nodes by matching text in code snippets. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_search_by_code(
    query: Optional[str] = None,
    limit: int = 50,
    top_k: Optional[int] = None,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "query": query,
            "limit": limit,
            "top_k": top_k,
            "db": db,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    query = payload.get("query")
    limit_value = payload.get("limit")
    if limit_value is None:
        limit_value = payload.get("top_k")
    limit = limit_value if limit_value is not None else 50
    db = payload.get("db")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    qs = [t.strip() for t in query.split("|") if t.strip()]
    fallback_cypher = "MATCH (n) WHERE any(q IN $qs WHERE n.code CONTAINS q) AND ($project_id IS NULL OR n.project_id = $project_id) RETURN n LIMIT $limit"
    fulltext_query = " OR ".join(qs)
    fulltext_cypher = (
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score "
        "WHERE ($project_id IS NULL OR node.project_id = $project_id) "
        "RETURN node AS n ORDER BY score DESC LIMIT $limit"
    )
    try:
        used_db, results = await _run_cypher_first(
            fulltext_cypher,
            {"index_name": FULLTEXT_SYMBOL_CODE_INDEX, "query": fulltext_query, "limit": int(limit), "project_id": project_id},
            db_candidates,
        )
        if not results:
            used_db, results = await _run_cypher_first(
                fallback_cypher,
                {"qs": qs, "limit": int(limit), "project_id": project_id},
                db_candidates,
            )
    except Exception:
        used_db, results = await _run_cypher_first(
            fallback_cypher,
            {"qs": qs, "limit": int(limit), "project_id": project_id},
            db_candidates,
        )
    mode = _normalize_content_mode(content_mode)
    nodes = [_record_node(row["n"], mode, include_raw_fields) for row in results]
    return {"db": used_db, "results": nodes}


@mcp_server.tool(
    name="annotate_node",
    description="Add or update annotations for a node. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_annotate_node(
    node_id: Any = None,
    db: Optional[str] = None,
    note: Optional[str] = None,
    tags: Optional[str] = None,
    severity: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "node_id": node_id,
            "db": db,
            "note": note,
            "tags": tags,
            "severity": severity,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
        },
    )
    node_id = payload.get("node_id")
    db = payload.get("db")
    note = payload.get("note")
    tags = payload.get("tags")
    severity = payload.get("severity")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if node_id is None:
        raise ValueError("node_id is required.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    node_id = str(node_id)
    cypher = (
        "MATCH (n) WHERE n.id = $id "
        "AND ($project_id IS NULL OR n.project_id = $project_id) "
        "SET n.note = $note, n.tags = $tags, n.severity = $severity "
        "RETURN n"
    )
    used_db, result = await _run_cypher_first(
        cypher,
        {"id": node_id, "note": note, "tags": tags, "severity": severity, "project_id": project_id},
        db_candidates,
    )
    if not result:
        raise RuntimeError(f"Unable to annotate node {node_id}.")
    mode = _normalize_content_mode(content_mode)
    return {"db": used_db, "node": _record_node(result[0]["n"], mode, include_raw_fields)}


_ANDROID_TOOL_NAMES: frozenset = frozenset({
    "activate_project", "search_functions", "search_by_code",
    "get_symbol", "get_node_details", "query_subgraph",
    "find_paths", "find_path_between_module",
    "listup_symbols_matching_file_path", "listup_class_matching_path",
    "list_up_entrypoint", "trace_flow", "trace_flow_between_module",
    "semantic_search", "get_ipc_message", "list_possible_calls",
    "annotate_node", "list_databases", "list_qdrant_collections",
    "list_parsers", "list_mcp_functions",
})

_android_catalog = build_catalog(_ANDROID_TOOL_NAMES, overrides=ANDROID_OVERRIDES)
_MCP_FUNCTIONS_JSON: str = json.dumps(
    {"total_count": len(_android_catalog), "functions": _android_catalog},
    ensure_ascii=False,
)

@mcp_server.tool(
    name="list_mcp_functions",
    description="List all available MCP tools with descriptions, parameters, and use cases. Call this FIRST to discover what tools are available before making other calls."
, output_schema=None)
async def tool_list_mcp_functions(payload: Optional[Dict[str, Any]] = None) -> str:
    return _MCP_FUNCTIONS_JSON


@mcp_server.tool(name="list_parsers", description="List available parser types supported locally.", output_schema=None)
async def tool_list_parsers(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _coerce_payload(payload)
    tools_dir = os.path.join(_ROOT_DIR, "tools")
    parsers = []
    exclude_dirs = {"common", "graph", "__pycache__", ".DS_Store"}
    
    if os.path.isdir(tools_dir):
        for entry in os.listdir(tools_dir):
            entry_path = os.path.join(tools_dir, entry)
            if os.path.isdir(entry_path) and entry not in exclude_dirs and not entry.startswith("."):
                parsers.append(entry)
    
    parsers.sort()
    return {"parsers": parsers}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FastMCP server exposing Project Call Graph capabilities (local mode).",
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
    _preload_embedder_on_startup()
    kwargs: Dict[str, Any] = {"transport": transport}
    if transport != "stdio":
        kwargs.update({"host": args.host, "port": args.port})
        if stream_path:
            kwargs["path"] = stream_path
    mcp_server.run(**kwargs)


if __name__ == "__main__":
    main()

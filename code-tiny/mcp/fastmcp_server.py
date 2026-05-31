from __future__ import annotations

import argparse
import asyncio
import os
import sys
import signal

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import logging

import httpx
import torch
from fastmcp import FastMCP
from neo4j.exceptions import Neo4jError
from transformers import AutoModel, AutoTokenizer

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.core.base import GraphDriver
from tool_metadata import build_catalog




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
            value = value.strip()
            # Strip inline ``# comment`` tails so values like
            # ``EMBED_DEVICE=mps # cuda or mps or cpu`` don't end up
            # literally feeding ``mps # cuda or mps or cpu`` into
            # downstream callers (``torch.device`` rejects this with
            # ``RuntimeError: Invalid device string``). Only strip when
            # the ``#`` is preceded by whitespace so it cannot eat a
            # legitimate value containing ``#`` (e.g. a URL fragment or
            # an API token).
            if not (value.startswith('"') or value.startswith("'")):
                hash_at = value.find(" #")
                if hash_at == -1:
                    hash_at = value.find("\t#")
                if hash_at != -1:
                    value = value[:hash_at].rstrip()
            value = value.strip("\"'")
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
DEFAULT_QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "kotlin_functions")
DEFAULT_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.environ.get("NEO4J_USER")
DEFAULT_NEO4J_PASSWORD = os.environ.get("NEO4J_PASS")
DEFAULT_NEO4J_DB = os.environ.get("NEO4J_DB") or "neo4j"
FULLTEXT_SYMBOL_TEXT_INDEX = "mcp_symbol_text_ft"
FULLTEXT_SYMBOL_CODE_INDEX = "mcp_symbol_code_ft"


MCP_NAME = "Project Call Graph"

INSTRUCTIONS = """Project Call Graph MCP (local mode) reads directly from Neo4j and Qdrant.

Discovery:
- Call `list_mcp_functions` first to get the exact tool list and parameters exposed by this server.

Core capability groups:
- Symbol/graph search: search_functions, search_by_code, get_symbol, get_node_details
- Graph traversal/planning: query_subgraph, find_paths, find_path_between_module
- Dependency planning: compute_scc, topological_sort, plan_dependency_order, plan_file_dependency_order, plan_function_dependency_order
- Workflow discovery: list_workflows, get_workflow_steps, search_workflows
- Module/class views: listup_symbols_matching_file_path, listup_class_matching_path, list_up_entrypoint
- Infrastructure: list_databases, list_qdrant_collections, list_parsers
- Utilities: semantic_search, annotate_node, activate_project

Response content controls (most tools):
- content_mode: auto (default), summary, comment, code, name
  - auto fallback order: summary -> comment -> name
- include_raw_fields: false by default; when true, keep summary/comment/code fields in payload

Planner output highlights:
- `plan_dependency_order`: module-level waves/order + depends_on_map + SCC/cycle diagnostics.
- `plan_file_dependency_order`: per-module file waves/order + cross_module_edges + SCC/cycle diagnostics.
- `plan_function_dependency_order`: per-module function waves + `function_order_ids` + function metadata (`name`, `qualified_name`, `file_path`) + `depends_on_map` + `unresolved_cycles`/`node_to_scc` + `cross_module_edges`.

When include_raw_fields=false, only properties.content is returned (plus metadata) to reduce payload size.
"""

mcp_server = FastMCP(
    name=MCP_NAME,
    version="2.2.0",
    instructions=INSTRUCTIONS,
)

active_project: Dict[str, Optional[str]] = {
    "parser_type": None,
    "database_name": None,
}

_graph_driver: Optional[GraphDriver] = None
_embedder_cache: Dict[Tuple[str, str], Tuple[Any, Any, torch.device]] = {}
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


_CODE_LABELS = {
    "Project", "Repository", "Directory", "File", "Class", "Function", "Method",
    "Namespace", "Interface", "Enum", "Type", "Package", "Alias", "Template",
}

_DOC_LABELS = {
    "Document", "Paragraph", "Chunk", "PdfDocument", "WordDocument", "WorksheetDocument", "Slide",
}


def _normalize_node_type(value: Optional[str], default_value: str = "code") -> str:
    text = (value or "").strip().lower()
    if text in {"code", "doc"}:
        return text
    return default_value


def _classify_node_type(labels: List[str], properties: Dict[str, Any]) -> str:
    label_set = set(labels or [])
    if label_set & _DOC_LABELS:
        return "doc"
    if label_set & _CODE_LABELS:
        return "code"
    if properties.get("paragraph_id") is not None or properties.get("doc_id") is not None:
        return "doc"
    if properties.get("source_id") is not None and properties.get("text") is not None:
        return "doc"
    return "code"


def _fallback_node_name(properties: Dict[str, Any], node_id: Optional[str]) -> str:
    for key in ("name", "qualified_name", "file_path", "path"):
        value = properties.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if node_id:
        return node_id
    return ""


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
    requested_node_type: Optional[str] = None,
    compact_bridge: bool = False,
) -> Dict[str, Any]:
    mode = _normalize_content_mode(content_mode)
    if isinstance(node, dict):
        node_id = node.get("id")
        props = {key: value for key, value in node.items() if key != "labels"}
        labels = list(node.get("labels", []))
        node_type = _classify_node_type(labels, props)
        props["node_type"] = node_type
        if compact_bridge and requested_node_type and node_type != requested_node_type:
            return {
                "id": node_id,
                "labels": labels,
                "properties": {
                    "name": _fallback_node_name(props, node_id),
                    "node_type": node_type,
                },
            }
        content = _select_content(props, node_id, mode)
        if not include_raw_fields:
            _prune_content_fields(props)
        return {
            "id": node_id,
            "labels": labels,
            "properties": {
                **props,
                "content_mode": mode,
                "content": content,
            },
        }
    node_id = node.get("id")
    properties = dict(node)
    labels = list(getattr(node, "labels", []))
    node_type = _classify_node_type(labels, properties)
    properties["node_type"] = node_type
    if compact_bridge and requested_node_type and node_type != requested_node_type:
        return {
            "id": node_id,
            "labels": labels,
            "properties": {
                "name": _fallback_node_name(properties, node_id),
                "node_type": node_type,
            },
        }
    properties["content_mode"] = mode
    properties["content"] = _select_content(properties, node_id, mode)
    if not include_raw_fields:
        _prune_content_fields(properties)
    return {
        "id": node_id,
        "labels": labels,
        "properties": properties,
    }


def _record_rel(rel: Any) -> Dict[str, Any]:
    # A bare string IS the relationship type name. Several Neo4j 6.x
    # serialisation paths emit just the type label when start/end nodes
    # aren't included in the record (e.g. ``RETURN type(r)`` or path
    # traversals that pass relationships through ``COLLECT``). Treat
    # these as valid edges with unknown endpoints rather than letting
    # them fall through to the ``type(rel).__name__`` fallback, which
    # would surface them as ``{"type": "str", ...}`` — that's how the
    # observed placeholder edges leak into call-graph outputs.
    if isinstance(rel, str):
        return {
            "type": rel,
            "properties": {},
            "start_id": None,
            "end_id": None,
        }
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
        # Last resort: surface the runtime type so the operator can
        # debug. Suffix with ``?`` so it's obvious this is a fallback
        # and not a real relationship type.
        return {
            "type": f"unknown<{type(rel).__name__}>",
            "properties": {},
            "start_id": None,
            "end_id": None,
        }


def _paths_to_graph(
    paths: Iterable[Any],
    content_mode: str = "auto",
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
    expand_search: bool = False,
) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    mode = _normalize_content_mode(content_mode)
    requested_type = _normalize_node_type(node_type, default_value="code")

    def _accept(rec: Dict[str, Any]) -> bool:
        rec_type = ((rec.get("properties") or {}).get("node_type") or "code").lower()
        return expand_search or rec_type == requested_type

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
                                rec = _record_node(
                                    item,
                                    mode,
                                    include_raw_fields=include_raw_fields,
                                    requested_node_type=requested_type,
                                    compact_bridge=expand_search,
                                )
                                if _accept(rec):
                                    nodes[node_id] = rec
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
                rec = _record_node(
                    node,
                    mode,
                    include_raw_fields=include_raw_fields,
                    requested_node_type=requested_type,
                    compact_bridge=expand_search,
                )
                if _accept(rec):
                    nodes[node_id] = rec
        for rel in path_rels:
            edges.append(_record_rel(rel))
    node_ids = {item.get("id") for item in nodes.values() if item.get("id")}
    filtered_edges = [
        edge for edge in edges
        if edge.get("start_id") in node_ids and edge.get("end_id") in node_ids
    ]
    return {"nodes": list(nodes.values()), "edges": filtered_edges}


def _should_trust_remote_code(model_name: str) -> bool:
    jina_path = os.environ.get("JINA_MODEL_PATH")
    if jina_path and os.path.normpath(jina_path) == os.path.normpath(model_name):
        return True
    return "jina" in model_name.lower()


def _is_embed_cpu_fallback_enabled() -> bool:
    raw = os.environ.get("EMBED_FALLBACK_TO_CPU", "1")
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _is_cuda_runtime_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "cuda" in message and (
        "no kernel image is available for execution on the device" in message
        or "invalid device function" in message
        or "no cuda kernels are available" in message
        or "cuda error" in message
    )


def _resolve_embed_device(device_name: Optional[str] = None) -> torch.device:
    raw_device = (device_name or os.environ.get("EMBED_DEVICE", "cpu") or "cpu").strip()
    if not raw_device:
        raw_device = "cpu"
    normalized = raw_device.lower()
    if normalized.startswith("cuda") and not torch.cuda.is_available():
        logger.warning("[embed] CUDA requested but unavailable; falling back to CPU.")
        return torch.device("cpu")
    if normalized.startswith("mps"):
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is None or not mps_backend.is_available():
            logger.warning("[embed] MPS requested but unavailable; falling back to CPU.")
            return torch.device("cpu")
    return torch.device(raw_device)


def _get_embedder(model_name: str, device_name: Optional[str] = None) -> Tuple[Any, Any, torch.device]:
    device = _resolve_embed_device(device_name)
    cache_key = (model_name, str(device))
    if cache_key in _embedder_cache:
        return _embedder_cache[cache_key]
    trust_remote_code = _should_trust_remote_code(model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    model = AutoModel.from_pretrained(model_name, trust_remote_code=trust_remote_code)
    try:
        model.to(device)
    except RuntimeError as exc:
        if str(device).startswith("cuda") and _is_cuda_runtime_error(exc) and _is_embed_cpu_fallback_enabled():
            logger.warning("[embed] CUDA model load failed (%s). Retrying on CPU.", exc)
            device = torch.device("cpu")
            model.to(device)
            cache_key = (model_name, str(device))
        else:
            raise
    model.eval()
    _embedder_cache[cache_key] = (tokenizer, model, device)
    return tokenizer, model, device


def _mean_pool(last_hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    mask = mask.unsqueeze(-1).type_as(last_hidden)
    summed = (last_hidden * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1)
    return summed / counts


def _encode_texts(model: Any, texts: List[str], device: torch.device) -> Optional[List[List[float]]]:
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


def _embed_query_with_model(tokenizer: Any, model: Any, device: torch.device, text: str) -> List[float]:
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


def _embed_query(text: str, model_name: str) -> List[float]:
    tokenizer, model, device = _get_embedder(model_name)
    try:
        return _embed_query_with_model(tokenizer, model, device, text)
    except RuntimeError as exc:
        if str(device).startswith("cuda") and _is_cuda_runtime_error(exc) and _is_embed_cpu_fallback_enabled():
            logger.warning("[embed] CUDA inference failed (%s). Retrying on CPU.", exc)
            _embedder_cache.pop((model_name, str(device)), None)
            tokenizer_cpu, model_cpu, device_cpu = _get_embedder(model_name, device_name="cpu")
            return _embed_query_with_model(tokenizer_cpu, model_cpu, device_cpu, text)
        raise


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
    _, _, resolved_device = _get_embedder(model_name, device_name=device_name)
    print(f"[embed] preload completed on device={resolved_device}.")


def _qdrant_search(
    collection: str,
    vector: List[float],
    top_k: int,
    qdrant_url: str,
    vector_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Vector search through Qdrant's Query API (``/points/query``).

    Migrated from the legacy ``/points/search`` endpoint because:

      1. The legacy named-vector payload shape is
         ``{"vector": {"name": "semantic", "vector": [...]}}`` — easy to
         get wrong (the prior bug shipped ``{"vector": {"semantic":
         [...]}}`` which Qdrant rejects with 400 "did not match any
         variant of untagged enum NamedVectorStruct").
      2. ``/points/query`` accepts the same flat payload for v1
         (unnamed) and v2 (named) collections — ``using=<name>`` selects
         the vector space when the collection has multiple. Eliminates
         the named/unnamed branching in the caller.

    Response shape is normalised so legacy callers that walked the old
    ``payload["result"]`` list still work: the new ``/points/query``
    returns ``{"result": {"points": [...]}}`` — we unwrap to the same
    list. Keep this in lock-step with
    ``hyper_pack_core.qdrant_search.query_collection`` and
    ``hyper-graph/tools/common/intelligent_retrieval._qdrant_search``;
    all three are the search-side surface for Qdrant traffic.
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
    # Normalise response: the Query API wraps hits inside
    # ``result.points``; the legacy Search API returned them directly
    # under ``result``. Re-shape so callers (e.g. ``_merge_qdrant_results``)
    # don't have to change.
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


def _normalize_edge_semantics(value: Optional[str]) -> str:
    semantics = (value or "depends_on").strip().lower()
    if semantics in {"depends_on", "dependent_to_dependency", "call_graph", "calls", "caller_to_callee"}:
        return "depends_on"
    if semantics in {"dependency_to_dependent", "prerequisite_to_dependent"}:
        return "dependency_to_dependent"
    return "depends_on"


def _build_prerequisite_graph(
    nodes: Iterable[str],
    edges: Iterable[Tuple[str, str]],
    edge_semantics: str = "depends_on",
) -> Tuple[Set[str], Dict[str, Set[str]]]:
    """
    Build adjacency where edge u->v means: u must be done before v.

    Semantics:
    - depends_on: input edge is dependent->dependency, so reverse to dependency->dependent
    - dependency_to_dependent: keep direction as-is
    """
    semantics = _normalize_edge_semantics(edge_semantics)
    node_set: Set[str] = {str(n) for n in nodes if str(n)}
    adjacency: Dict[str, Set[str]] = {}
    for node in node_set:
        adjacency[node] = set()
    for start, end in edges:
        s = str(start).strip()
        e = str(end).strip()
        if not s or not e:
            continue
        node_set.add(s)
        node_set.add(e)
        if s not in adjacency:
            adjacency[s] = set()
        if e not in adjacency:
            adjacency[e] = set()
        if semantics == "depends_on":
            prereq, dependent = e, s
        else:
            prereq, dependent = s, e
        adjacency.setdefault(prereq, set()).add(dependent)
        adjacency.setdefault(dependent, set())
    for node in node_set:
        adjacency.setdefault(node, set())
    return node_set, adjacency


def _compute_scc(nodes: Set[str], adjacency: Dict[str, Set[str]]) -> List[List[str]]:
    """Tarjan SCC, O(V+E)."""
    index_counter = 0
    index: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    stack: List[str] = []
    on_stack: Set[str] = set()
    components: List[List[str]] = []

    def strong_connect(v: str) -> None:
        nonlocal index_counter
        index[v] = index_counter
        lowlink[v] = index_counter
        index_counter += 1
        stack.append(v)
        on_stack.add(v)

        for w in adjacency.get(v, set()):
            if w not in index:
                strong_connect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            component: List[str] = []
            while stack:
                w = stack.pop()
                on_stack.remove(w)
                component.append(w)
                if w == v:
                    break
            component.sort()
            components.append(component)

    for node in sorted(nodes):
        if node not in index:
            strong_connect(node)
    return components


def _build_condensed_dag(
    adjacency: Dict[str, Set[str]],
    components: List[List[str]],
) -> Tuple[List[str], Dict[str, Set[str]], Dict[str, int]]:
    """
    Returns:
    - scc_ids: list of SCC IDs
    - dag_adjacency: SCC DAG adjacency (u -> v means u before v)
    - node_to_scc_idx: node -> scc index mapping
    """
    node_to_scc_idx: Dict[str, int] = {}
    for idx, comp in enumerate(components):
        for node in comp:
            node_to_scc_idx[node] = idx
    scc_ids = [f"scc_{idx}" for idx in range(len(components))]
    dag_adjacency: Dict[str, Set[str]] = {sid: set() for sid in scc_ids}
    for src, neighbors in adjacency.items():
        src_idx = node_to_scc_idx[src]
        src_sid = scc_ids[src_idx]
        for dst in neighbors:
            dst_idx = node_to_scc_idx[dst]
            dst_sid = scc_ids[dst_idx]
            if src_idx != dst_idx:
                dag_adjacency[src_sid].add(dst_sid)
    return scc_ids, dag_adjacency, node_to_scc_idx


def _topological_waves(
    nodes: Iterable[str],
    adjacency: Dict[str, Set[str]],
) -> Tuple[List[List[str]], List[str], Dict[str, int]]:
    """
    Kahn-style topological wave decomposition.

    Returns:
    - waves: each wave contains nodes ready in parallel
    - unresolved_nodes: non-empty if cycles exist
    - indegree_snapshot: final indegree map
    """
    node_list = sorted({str(n) for n in nodes if str(n)})
    indegree: Dict[str, int] = {n: 0 for n in node_list}
    for src in node_list:
        for dst in adjacency.get(src, set()):
            indegree[dst] = indegree.get(dst, 0) + 1
            indegree.setdefault(src, 0)

    current_wave = sorted([n for n, d in indegree.items() if d == 0])
    waves: List[List[str]] = []
    processed: Set[str] = set()

    while current_wave:
        waves.append(current_wave)
        next_candidates: Set[str] = set()
        for node in current_wave:
            if node in processed:
                continue
            processed.add(node)
            for neighbor in adjacency.get(node, set()):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    next_candidates.add(neighbor)
        current_wave = sorted(next_candidates)

    unresolved = sorted([n for n in indegree if n not in processed])
    return waves, unresolved, indegree


def _extract_edges_from_graph_payload(edges: Any) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    if not isinstance(edges, list):
        return pairs
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        start_id = (
            edge.get("start_id")
            or edge.get("from")
            or edge.get("source")
            or edge.get("src")
            or edge.get("start")
            or edge.get("u")
            or edge.get("caller")
            or edge.get("dependent")
        )
        end_id = (
            edge.get("end_id")
            or edge.get("to")
            or edge.get("target")
            or edge.get("dst")
            or edge.get("end")
            or edge.get("v")
            or edge.get("callee")
            or edge.get("dependency")
        )
        if start_id is None or end_id is None:
            continue
        pairs.append((str(start_id), str(end_id)))
    return pairs


async def _run_cypher(query: str, params: Dict[str, Any], db: str) -> List[Dict[str, Any]]:
    driver = await _get_graph_driver()
    records, summary, keys = await driver.execute_query(query, params, db)
    return records


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


@mcp_server.tool(name="list_databases", description="List available Neo4j databases.")
async def tool_list_databases() -> Dict[str, Any]:
    names = await _list_databases()
    default_db = _normalize_db_name(DEFAULT_NEO4J_DB)
    return {"databases": names, "default": default_db}


@mcp_server.tool(
    name="activate_project",
    description="Set default parser_type and optional database_name for subsequent tool calls.",
)
async def tool_activate_project(
    parser_type: Optional[str] = None,
    database_name: Optional[str] = None,
) -> Dict[str, Optional[str]]:
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
    """Batch-enrich Qdrant result items with InfraNode community context.

    For each result whose node_id belongs to an InfraNode, adds an
    'infra_community' key to the payload:
      {
        "id": "project_id:community_id",
        "name": "NFC Card Emulation Handler",
        "summary": "...",
        "community_id": 4441
      }
    Only adds context for nodes that actually have a community; others are
    left unchanged. Errors are silently suppressed (best-effort enrichment).
    """
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
)
async def tool_semantic_search(
    query: str,
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
) -> Any:
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


@mcp_server.tool(name="list_qdrant_collections", description="List available Qdrant collections.")
async def tool_list_qdrant_collections(
    qdrant_url: Optional[str] = None,
    include_vectors: bool = False,
) -> Dict[str, Any]:
    qdrant_url = qdrant_url or DEFAULT_QDRANT_URL
    return await _fetch_qdrant_collections(qdrant_url, include_vectors=include_vectors)


@mcp_server.tool(
    name="get_symbol",
    description="Retrieve metadata for a specific node by id. Supports content_mode/include_raw_fields.",
)
async def tool_get_symbol(
    node_id: Any,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
) -> Dict[str, Any]:
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    node_id = str(node_id)
    
    driver = await _get_graph_driver()
    for db_candidate in candidates:
        try:
            node = await driver.find_node_by_id(node_id, project_id=project_id, database=db_candidate)
            if node:
                mode = _normalize_content_mode(content_mode)
                requested_type = _normalize_node_type(node_type, default_value="code")
                rec = _record_node(node, mode, include_raw_fields, requested_node_type=requested_type)
                if ((rec.get("properties") or {}).get("node_type") or "code") != requested_type:
                    return {"db": db_candidate, "node": None, "reason": "node_type_mismatch"}
                return {"db": db_candidate, "node": rec}
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    raise RuntimeError(f"Node {node_id} not found in any db.")


@mcp_server.tool(
    name="get_node_details",
    description="Fetch metadata for multiple node IDs. Supports content_mode/include_raw_fields.",
)
async def tool_get_node_details(
    node_ids: List[Any],
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
) -> Dict[str, Any]:
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    ids = [str(item) for item in node_ids]
    
    driver = await _get_graph_driver()
    for db_candidate in candidates:
        try:
            nodes = await driver.find_nodes_by_ids(ids, project_id=project_id, database=db_candidate)
            if nodes:
                mode = _normalize_content_mode(content_mode)
                requested_type = _normalize_node_type(node_type, default_value="code")
                result_nodes = []
                for node in nodes:
                    rec = _record_node(node, mode, include_raw_fields, requested_node_type=requested_type)
                    if ((rec.get("properties") or {}).get("node_type") or "code") == requested_type:
                        result_nodes.append(rec)
                return {"db": db_candidate, "nodes": result_nodes}
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    return {"db": candidates[0] if candidates else None, "nodes": []}


@mcp_server.tool(
    name="query_subgraph",
    description="Return call graph context around a function ID. Supports content_mode/include_raw_fields.",
)
async def tool_query_subgraph(
    db: Optional[str],
    function_id: Any,
    direction: str = "all",
    max_depth: int = 2,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
    expand_search: bool = False,
) -> Dict[str, Any]:
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    function_id = str(function_id)
    depth = _normalize_depth(max_depth, default=2, max_limit=10)
    direction = direction.lower()
    
    driver = await _get_graph_driver()
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            paths = await driver.query_function_subgraph(
                function_id=function_id,
                relationship_types=["CALLS"],
                direction=direction,
                max_depth=depth,
                project_id=project_id,
                database=candidate
            )
            if paths:
                graph = _paths_to_graph(
                    paths,
                    content_mode=content_mode or "auto",
                    include_raw_fields=include_raw_fields,
                    node_type=node_type,
                    expand_search=expand_search,
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
    return {
        "db": candidates[0] if candidates else None,
        "nodes": [],
        "edges": [],
        "reason": "no_subgraph",
    }


@mcp_server.tool(
    name="find_paths",
    description="Find call paths between two functions. Supports content_mode/include_raw_fields.",
)
async def tool_find_paths(
    db: Optional[str],
    start_function_id: Any,
    end_function_id: Any,
    max_depth: int = 8,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
    expand_search: bool = False,
) -> Dict[str, Any]:
    candidates = _resolve_db_candidates(db)
    _require(candidates[0] if candidates else None, "db")
    start_id = str(start_function_id)
    end_id = str(end_function_id)
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    
    driver = await _get_graph_driver()
    for db_candidate in candidates:
        try:
            paths = await driver.find_function_paths(
                start_id=start_id,
                end_id=end_id,
                relationship_types=["CALLS"],
                max_depth=depth,
                project_id=project_id,
                database=db_candidate
            )
            if paths:
                graph = _paths_to_graph(
                    paths,
                    content_mode=content_mode or "auto",
                    include_raw_fields=include_raw_fields,
                    node_type=node_type,
                    expand_search=expand_search,
                )
                graph["db"] = db_candidate
                return graph
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    raise RuntimeError("No path found in any db.")


@mcp_server.tool(
    name="find_path_between_module",
    description="Find call paths between modules. Supports content_mode/include_raw_fields.",
)
async def tool_find_path_between_module(
    source_modules: List[str],
    target_modules: List[str],
    db: Optional[str] = None,
    max_depth: int = 8,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
    source_modules = _normalize_string_list(source_modules)
    target_modules = _normalize_string_list(target_modules)
    if not source_modules or not target_modules:
        raise ValueError("source_modules and target_modules must be non-empty lists.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    query = (
        "MATCH (s:Function), (t:Function) "
        "WHERE any(token IN $sources WHERE s.file_path CONTAINS token) "
        "AND any(token IN $targets WHERE t.file_path CONTAINS token) "
        "AND ($project_id IS NULL OR s.project_id = $project_id) "
        "AND ($project_id IS NULL OR t.project_id = $project_id) "
        f"MATCH p=shortestPath((s)-[:CALLS*..{depth}]->(t)) "
        "RETURN p LIMIT 10"
    )
    used_db, results = await _run_cypher_first(query, {"sources": source_modules, "targets": target_modules, "project_id": project_id}, db_candidates)
    paths = [row["p"] for row in results]
    graph = _paths_to_graph(
        paths,
        content_mode=content_mode or "auto",
        include_raw_fields=include_raw_fields,
    )
    graph["db"] = used_db
    return graph


@mcp_server.tool(
    name="compute_scc",
    description=(
        "Compute strongly connected components (SCC) from a directed graph. "
        "Useful for detecting dependency cycles before migration planning."
    ),
)
async def tool_compute_scc(
    nodes: Optional[List[str]] = None,
    edges: Optional[List[Dict[str, Any]]] = None,
    parser_type: Optional[str] = None,
    edge_semantics: str = "depends_on",
    include_singletons: bool = True,
) -> Dict[str, Any]:
    node_list = _normalize_string_list(nodes)
    edge_pairs = _extract_edges_from_graph_payload(edges or [])
    if not node_list and not edge_pairs:
        raise ValueError("Provide at least one node or edge.")

    graph_nodes, adjacency = _build_prerequisite_graph(node_list, edge_pairs, edge_semantics=edge_semantics)
    components = _compute_scc(graph_nodes, adjacency)
    component_payload: List[Dict[str, Any]] = []
    cyclic_count = 0
    self_loop_count = 0
    for idx, comp in enumerate(components):
        if len(comp) == 1:
            node = comp[0]
            has_self_loop = node in adjacency.get(node, set())
            if has_self_loop:
                self_loop_count += 1
            is_cycle = has_self_loop
        else:
            is_cycle = True
        if is_cycle:
            cyclic_count += 1
        if include_singletons or len(comp) > 1 or is_cycle:
            component_payload.append(
                {
                    "scc_id": f"scc_{idx}",
                    "nodes": comp,
                    "size": len(comp),
                    "is_cycle": is_cycle,
                }
            )

    node_to_scc: Dict[str, str] = {}
    for item in component_payload:
        for node in item["nodes"]:
            node_to_scc[node] = item["scc_id"]

    return {
        "edge_semantics": _normalize_edge_semantics(edge_semantics),
        "components": component_payload,
        "node_to_scc": node_to_scc,
        "cycle_summary": {
            "total_scc": len(components),
            "reported_scc": len(component_payload),
            "cyclic_scc": cyclic_count,
            "self_loops": self_loop_count,
        },
    }


@mcp_server.tool(
    name="topological_sort",
    description=(
        "Topologically sort a directed dependency graph. "
        "Returns linear order and parallel waves. Supports cycle handling via SCC condensation."
    ),
)
async def tool_topological_sort(
    nodes: Optional[List[str]] = None,
    edges: Optional[List[Dict[str, Any]]] = None,
    parser_type: Optional[str] = None,
    edge_semantics: str = "depends_on",
    output_mode: str = "both",
    on_cycle: str = "auto_condense_scc",
) -> Dict[str, Any]:
    node_list = _normalize_string_list(nodes)
    edge_pairs = _extract_edges_from_graph_payload(edges or [])
    if not node_list and not edge_pairs:
        raise ValueError("Provide at least one node or edge.")

    graph_nodes, adjacency = _build_prerequisite_graph(node_list, edge_pairs, edge_semantics=edge_semantics)
    waves, unresolved_nodes, indegree = _topological_waves(graph_nodes, adjacency)
    is_dag = len(unresolved_nodes) == 0

    cycle_mode = (on_cycle or "auto_condense_scc").strip().lower()
    if unresolved_nodes and cycle_mode == "error":
        raise RuntimeError(f"Graph contains cycles. Unresolved nodes: {unresolved_nodes}")

    condensed = None
    resolved_waves = waves
    unresolved_cycles: List[Dict[str, Any]] = []
    if unresolved_nodes and cycle_mode == "auto_condense_scc":
        components = _compute_scc(graph_nodes, adjacency)
        scc_ids, dag_adjacency, node_to_scc_idx = _build_condensed_dag(adjacency, components)
        scc_waves, _, _ = _topological_waves(scc_ids, dag_adjacency)
        scc_id_to_nodes = {f"scc_{idx}": comp for idx, comp in enumerate(components)}
        resolved_waves = []
        for scc_wave in scc_waves:
            expanded: List[str] = []
            for scc_id in sorted(scc_wave):
                expanded.extend(sorted(scc_id_to_nodes.get(scc_id, [])))
            resolved_waves.append(expanded)
        for idx, comp in enumerate(components):
            if len(comp) > 1:
                unresolved_cycles.append({"scc_id": f"scc_{idx}", "nodes": comp, "size": len(comp)})
            elif comp and comp[0] in adjacency.get(comp[0], set()):
                unresolved_cycles.append({"scc_id": f"scc_{idx}", "nodes": comp, "size": 1})
        condensed = {
            "scc_count": len(components),
            "dag_nodes": scc_ids,
            "node_to_scc": {node: f"scc_{idx}" for node, idx in node_to_scc_idx.items()},
        }

    linear_order = [node for wave in resolved_waves for node in wave]
    mode = (output_mode or "both").strip().lower()
    payload: Dict[str, Any] = {
        "edge_semantics": _normalize_edge_semantics(edge_semantics),
        "is_dag": is_dag,
        "on_cycle": cycle_mode,
        "unresolved_nodes": unresolved_nodes,
        "unresolved_cycles": unresolved_cycles,
        "diagnostics": {
            "node_count": len(graph_nodes),
            "edge_count": sum(len(v) for v in adjacency.values()),
            "indegree": indegree,
        },
    }
    if condensed is not None:
        payload["condensed"] = condensed
    if mode in {"linear", "both"}:
        payload["linear_order"] = linear_order
    if mode in {"waves", "both"}:
        payload["waves"] = [{"wave": idx, "nodes": wave} for idx, wave in enumerate(resolved_waves)]
    return payload


@mcp_server.tool(
    name="plan_dependency_order",
    description=(
        "Plan module migration order from graph_mcp CALLS edges. "
        "Prioritizes independent prerequisites first and returns wave-based execution order."
    ),
)
async def tool_plan_dependency_order(
    modules: List[str],
    parser_type: Optional[str] = None,
    db: Optional[str] = None,
    edge_semantics: str = "depends_on",
    on_cycle: str = "auto_condense_scc",
) -> Dict[str, Any]:
    module_tokens = _normalize_string_list(modules)
    if not module_tokens:
        raise ValueError("modules must be a non-empty list.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")

    query = (
        "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
        "WHERE any(token IN $modules WHERE caller.file_path CONTAINS token) "
        "AND any(token IN $modules WHERE callee.file_path CONTAINS token) "
        "WITH [token IN $modules WHERE caller.file_path CONTAINS token] AS caller_tokens, "
        "[token IN $modules WHERE callee.file_path CONTAINS token] AS callee_tokens "
        "WHERE size(caller_tokens) > 0 AND size(callee_tokens) > 0 "
        "WITH caller_tokens[0] AS src_module, callee_tokens[0] AS dst_module "
        "WHERE src_module <> dst_module "
        "RETURN src_module, dst_module, count(*) AS call_count "
        "ORDER BY call_count DESC"
    )
    used_db, records = await _run_cypher_first(query, {"modules": module_tokens}, db_candidates)
    edge_pairs: List[Tuple[str, str]] = []
    module_dep_records: List[Dict[str, Any]] = []
    for row in records:
        src = row.get("src_module")
        dst = row.get("dst_module")
        calls = row.get("call_count")
        if not isinstance(src, str) or not isinstance(dst, str):
            continue
        edge_pairs.append((src, dst))
        module_dep_records.append(
            {
                "dependent_module": src,
                "dependency_module": dst,
                "call_count": int(calls) if isinstance(calls, (int, float)) else 0,
            }
        )

    graph_nodes, adjacency = _build_prerequisite_graph(module_tokens, edge_pairs, edge_semantics=edge_semantics)
    waves, unresolved_nodes, _ = _topological_waves(graph_nodes, adjacency)
    components = _compute_scc(graph_nodes, adjacency)
    unresolved_cycles: List[Dict[str, Any]] = []
    for idx, comp in enumerate(components):
        if len(comp) > 1 or (len(comp) == 1 and comp[0] in adjacency.get(comp[0], set())):
            unresolved_cycles.append({"scc_id": f"scc_{idx}", "nodes": comp, "size": len(comp)})

    cycle_mode = (on_cycle or "auto_condense_scc").strip().lower()
    final_waves = waves
    if unresolved_nodes and cycle_mode == "auto_condense_scc":
        scc_ids, dag_adjacency, node_to_scc_idx = _build_condensed_dag(adjacency, components)
        scc_waves, _, _ = _topological_waves(scc_ids, dag_adjacency)
        scc_id_to_nodes = {f"scc_{idx}": comp for idx, comp in enumerate(components)}
        final_waves = []
        for scc_wave in scc_waves:
            expanded: List[str] = []
            for scc_id in sorted(scc_wave):
                expanded.extend(sorted(scc_id_to_nodes.get(scc_id, [])))
            final_waves.append(expanded)
        node_to_scc = {node: f"scc_{idx}" for node, idx in node_to_scc_idx.items()}
    else:
        node_to_scc = {}

    depends_on_map: Dict[str, List[str]] = {}
    for prereq, dependents in adjacency.items():
        for dep in dependents:
            depends_on_map.setdefault(dep, []).append(prereq)
        depends_on_map.setdefault(prereq, [])
    for key in list(depends_on_map.keys()):
        depends_on_map[key] = sorted(set(depends_on_map[key]))

    wave_payload = [{"wave": idx, "modules": wave} for idx, wave in enumerate(final_waves)]
    expanded_order = [m for wave in final_waves for m in wave]
    return {
        "db": used_db,
        "edge_semantics": _normalize_edge_semantics(edge_semantics),
        "on_cycle": cycle_mode,
        "input_modules": module_tokens,
        "module_dependencies": module_dep_records,
        "waves": wave_payload,
        "module_order": expanded_order,
        "depends_on_map": depends_on_map,
        "unresolved_nodes": unresolved_nodes,
        "unresolved_cycles": unresolved_cycles,
        "node_to_scc": node_to_scc,
    }


@mcp_server.tool(
    name="plan_file_dependency_order",
    description=(
        "Plan per-module file migration order from graph_mcp CALLS edges. "
        "Returns wave-based file order, dependency map, and cycle/SCC diagnostics."
    ),
)
async def tool_plan_file_dependency_order(
    modules: List[str],
    parser_type: Optional[str] = None,
    db: Optional[str] = None,
    edge_semantics: str = "depends_on",
    on_cycle: str = "auto_condense_scc",
    include_cross_module: bool = False,
    max_files_per_module: int = 2000,
) -> Dict[str, Any]:
    module_tokens = _normalize_string_list(modules)
    if not module_tokens:
        raise ValueError("modules must be a non-empty list.")
    if max_files_per_module < 1:
        raise ValueError("max_files_per_module must be >= 1.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")

    query = (
        "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
        "WHERE any(token IN $modules WHERE caller.file_path CONTAINS token) "
        "AND any(token IN $modules WHERE callee.file_path CONTAINS token) "
        "AND caller.file_path IS NOT NULL AND callee.file_path IS NOT NULL "
        "WITH caller.file_path AS src_file, callee.file_path AS dst_file, "
        "[token IN $modules WHERE caller.file_path CONTAINS token] AS caller_tokens, "
        "[token IN $modules WHERE callee.file_path CONTAINS token] AS callee_tokens "
        "WHERE size(caller_tokens) > 0 AND size(callee_tokens) > 0 AND src_file <> dst_file "
        "RETURN src_file, dst_file, caller_tokens[0] AS src_module, callee_tokens[0] AS dst_module, "
        "count(*) AS call_count "
        "ORDER BY call_count DESC"
    )
    used_db, records = await _run_cypher_first(query, {"modules": module_tokens}, db_candidates)

    module_files: Dict[str, Set[str]] = {token: set() for token in module_tokens}
    intra_edges: Dict[str, List[Tuple[str, str]]] = {token: [] for token in module_tokens}
    cross_edges: List[Dict[str, Any]] = []
    file_dependencies: Dict[str, List[Dict[str, Any]]] = {token: [] for token in module_tokens}

    for row in records:
        src_file = row.get("src_file")
        dst_file = row.get("dst_file")
        src_module = row.get("src_module")
        dst_module = row.get("dst_module")
        call_count = row.get("call_count")
        if not isinstance(src_file, str) or not isinstance(dst_file, str):
            continue
        if not isinstance(src_module, str) or not isinstance(dst_module, str):
            continue
        if src_module not in module_files:
            module_files[src_module] = set()
            intra_edges[src_module] = []
            file_dependencies[src_module] = []
        if dst_module not in module_files:
            module_files[dst_module] = set()
            intra_edges[dst_module] = []
            file_dependencies[dst_module] = []

        module_files[src_module].add(src_file)
        module_files[dst_module].add(dst_file)

        dep_record = {
            "dependent_file": src_file,
            "dependency_file": dst_file,
            "dependent_module": src_module,
            "dependency_module": dst_module,
            "call_count": int(call_count) if isinstance(call_count, (int, float)) else 0,
        }
        if src_module == dst_module:
            intra_edges[src_module].append((src_file, dst_file))
            file_dependencies[src_module].append(dep_record)
        else:
            cross_edges.append(dep_record)
            if include_cross_module:
                intra_edges[src_module].append((src_file, dst_file))

    module_plans: List[Dict[str, Any]] = []
    cycle_mode = (on_cycle or "auto_condense_scc").strip().lower()
    for module in sorted(module_files.keys()):
        files = sorted(module_files[module])[:max_files_per_module]
        file_set = set(files)
        edges = [(s, d) for s, d in intra_edges.get(module, []) if s in file_set and d in file_set]

        graph_nodes, adjacency = _build_prerequisite_graph(
            files,
            edges,
            edge_semantics=edge_semantics,
        )
        waves, unresolved_nodes, _ = _topological_waves(graph_nodes, adjacency)
        components = _compute_scc(graph_nodes, adjacency)
        unresolved_cycles: List[Dict[str, Any]] = []
        for idx, comp in enumerate(components):
            if len(comp) > 1 or (len(comp) == 1 and comp[0] in adjacency.get(comp[0], set())):
                unresolved_cycles.append({"scc_id": f"scc_{idx}", "nodes": comp, "size": len(comp)})

        node_to_scc: Dict[str, str] = {}
        final_waves = waves
        if unresolved_nodes and cycle_mode == "auto_condense_scc":
            scc_ids, dag_adjacency, node_to_scc_idx = _build_condensed_dag(adjacency, components)
            scc_waves, _, _ = _topological_waves(scc_ids, dag_adjacency)
            scc_id_to_nodes = {f"scc_{idx}": comp for idx, comp in enumerate(components)}
            final_waves = []
            for scc_wave in scc_waves:
                expanded: List[str] = []
                for scc_id in sorted(scc_wave):
                    expanded.extend(sorted(scc_id_to_nodes.get(scc_id, [])))
                final_waves.append(expanded)
            node_to_scc = {node: f"scc_{idx}" for node, idx in node_to_scc_idx.items()}

        depends_on_map: Dict[str, List[str]] = {}
        for prereq, dependents in adjacency.items():
            for dep in dependents:
                depends_on_map.setdefault(dep, []).append(prereq)
            depends_on_map.setdefault(prereq, [])
        for key in list(depends_on_map.keys()):
            depends_on_map[key] = sorted(set(depends_on_map[key]))

        module_plans.append(
            {
                "module": module,
                "file_count": len(graph_nodes),
                "waves": [{"wave": idx, "files": wave} for idx, wave in enumerate(final_waves)],
                "file_order": [f for wave in final_waves for f in wave],
                "depends_on_map": depends_on_map,
                "unresolved_nodes": unresolved_nodes,
                "unresolved_cycles": unresolved_cycles,
                "node_to_scc": node_to_scc,
                "file_dependencies": file_dependencies.get(module, []),
            }
        )

    return {
        "db": used_db,
        "edge_semantics": _normalize_edge_semantics(edge_semantics),
        "on_cycle": cycle_mode,
        "include_cross_module": include_cross_module,
        "input_modules": module_tokens,
        "cross_module_edges": cross_edges,
        "modules": module_plans,
    }


@mcp_server.tool(
    name="plan_function_dependency_order",
    description=(
        "Plan per-module function migration order from graph_mcp CALLS edges. "
        "Returns wave-based function order, dependency map, and cycle/SCC diagnostics."
    ),
)
async def tool_plan_function_dependency_order(
    modules: List[str],
    parser_type: Optional[str] = None,
    db: Optional[str] = None,
    edge_semantics: str = "depends_on",
    on_cycle: str = "auto_condense_scc",
    include_cross_module: bool = False,
    include_lambdas: bool = False,
    max_functions_per_module: int = 5000,
) -> Dict[str, Any]:
    module_tokens = _normalize_string_list(modules)
    if not module_tokens:
        raise ValueError("modules must be a non-empty list.")
    if max_functions_per_module < 1:
        raise ValueError("max_functions_per_module must be >= 1.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")

    lambda_filter = "" if include_lambdas else "AND (caller.kind IS NULL OR caller.kind <> 'lambda') AND (callee.kind IS NULL OR callee.kind <> 'lambda') "
    query = (
        "MATCH (caller:Function)-[:CALLS]->(callee:Function) "
        "WHERE any(token IN $modules WHERE caller.file_path CONTAINS token) "
        "AND any(token IN $modules WHERE callee.file_path CONTAINS token) "
        f"{lambda_filter}"
        "WITH caller, callee, [token IN $modules WHERE caller.file_path CONTAINS token] AS caller_tokens, "
        "[token IN $modules WHERE callee.file_path CONTAINS token] AS callee_tokens "
        "WHERE size(caller_tokens) > 0 AND size(callee_tokens) > 0 "
        "RETURN caller.id AS src_id, caller.name AS src_name, caller.qualified_name AS src_qualified_name, "
        "caller.file_path AS src_file_path, "
        "callee.id AS dst_id, callee.name AS dst_name, callee.qualified_name AS dst_qualified_name, "
        "callee.file_path AS dst_file_path, "
        "caller_tokens[0] AS src_module, callee_tokens[0] AS dst_module, "
        "count(*) AS call_count "
        "ORDER BY call_count DESC"
    )
    used_db, records = await _run_cypher_first(query, {"modules": module_tokens}, db_candidates)

    module_functions: Dict[str, Set[str]] = {token: set() for token in module_tokens}
    intra_edges: Dict[str, List[Tuple[str, str]]] = {token: [] for token in module_tokens}
    cross_edges: List[Dict[str, Any]] = []
    function_dependencies: Dict[str, List[Dict[str, Any]]] = {token: [] for token in module_tokens}
    function_metadata: Dict[str, Dict[str, Dict[str, Any]]] = {token: {} for token in module_tokens}

    for row in records:
        src_id = row.get("src_id")
        dst_id = row.get("dst_id")
        src_module = row.get("src_module")
        dst_module = row.get("dst_module")
        if not isinstance(src_id, str) or not isinstance(dst_id, str):
            continue
        if not isinstance(src_module, str) or not isinstance(dst_module, str):
            continue

        for module in (src_module, dst_module):
            if module not in module_functions:
                module_functions[module] = set()
                intra_edges[module] = []
                function_dependencies[module] = []
                function_metadata[module] = {}

        module_functions[src_module].add(src_id)
        module_functions[dst_module].add(dst_id)
        function_metadata[src_module][src_id] = {
            "id": src_id,
            "name": row.get("src_name"),
            "qualified_name": row.get("src_qualified_name"),
            "file_path": row.get("src_file_path"),
        }
        function_metadata[dst_module][dst_id] = {
            "id": dst_id,
            "name": row.get("dst_name"),
            "qualified_name": row.get("dst_qualified_name"),
            "file_path": row.get("dst_file_path"),
        }

        dep_record = {
            "dependent_function_id": src_id,
            "dependency_function_id": dst_id,
            "dependent_module": src_module,
            "dependency_module": dst_module,
            "call_count": int(row.get("call_count")) if isinstance(row.get("call_count"), (int, float)) else 0,
        }
        if src_module == dst_module:
            intra_edges[src_module].append((src_id, dst_id))
            function_dependencies[src_module].append(dep_record)
        else:
            cross_edges.append(dep_record)
            if include_cross_module:
                intra_edges[src_module].append((src_id, dst_id))

    module_plans: List[Dict[str, Any]] = []
    cycle_mode = (on_cycle or "auto_condense_scc").strip().lower()
    for module in sorted(module_functions.keys()):
        funcs = sorted(module_functions[module])[:max_functions_per_module]
        func_set = set(funcs)
        edges = [(s, d) for s, d in intra_edges.get(module, []) if s in func_set and d in func_set]

        graph_nodes, adjacency = _build_prerequisite_graph(
            funcs,
            edges,
            edge_semantics=edge_semantics,
        )
        waves, unresolved_nodes, _ = _topological_waves(graph_nodes, adjacency)
        components = _compute_scc(graph_nodes, adjacency)
        unresolved_cycles: List[Dict[str, Any]] = []
        for idx, comp in enumerate(components):
            if len(comp) > 1 or (len(comp) == 1 and comp[0] in adjacency.get(comp[0], set())):
                unresolved_cycles.append({"scc_id": f"scc_{idx}", "function_ids": comp, "size": len(comp)})

        node_to_scc: Dict[str, str] = {}
        final_waves = waves
        if unresolved_nodes and cycle_mode == "auto_condense_scc":
            scc_ids, dag_adjacency, node_to_scc_idx = _build_condensed_dag(adjacency, components)
            scc_waves, _, _ = _topological_waves(scc_ids, dag_adjacency)
            scc_id_to_nodes = {f"scc_{idx}": comp for idx, comp in enumerate(components)}
            final_waves = []
            for scc_wave in scc_waves:
                expanded: List[str] = []
                for scc_id in sorted(scc_wave):
                    expanded.extend(sorted(scc_id_to_nodes.get(scc_id, [])))
                final_waves.append(expanded)
            node_to_scc = {node: f"scc_{idx}" for node, idx in node_to_scc_idx.items()}

        depends_on_map: Dict[str, List[str]] = {}
        for prereq, dependents in adjacency.items():
            for dep in dependents:
                depends_on_map.setdefault(dep, []).append(prereq)
            depends_on_map.setdefault(prereq, [])
        for key in list(depends_on_map.keys()):
            depends_on_map[key] = sorted(set(depends_on_map[key]))

        metadata = function_metadata.get(module, {})
        module_plans.append(
            {
                "module": module,
                "function_count": len(graph_nodes),
                "waves": [
                    {
                        "wave": idx,
                        "function_ids": wave,
                        "functions": [metadata.get(fid, {"id": fid}) for fid in wave],
                    }
                    for idx, wave in enumerate(final_waves)
                ],
                "function_order_ids": [f for wave in final_waves for f in wave],
                "function_order": [metadata.get(fid, {"id": fid}) for wave in final_waves for fid in wave],
                "depends_on_map": depends_on_map,
                "unresolved_nodes": unresolved_nodes,
                "unresolved_cycles": unresolved_cycles,
                "node_to_scc": node_to_scc,
                "function_dependencies": function_dependencies.get(module, []),
            }
        )

    return {
        "db": used_db,
        "edge_semantics": _normalize_edge_semantics(edge_semantics),
        "on_cycle": cycle_mode,
        "include_cross_module": include_cross_module,
        "include_lambdas": include_lambdas,
        "input_modules": module_tokens,
        "cross_module_edges": cross_edges,
        "modules": module_plans,
    }


@mcp_server.tool(
    name="listup_symbols_matching_file_path",
    description="List symbols by file path token. Supports content_mode/include_raw_fields. Use node_types=['Function'] to list only functions.",
)
async def tool_listup_symbols_matching_file_path(
    modules: List[str],
    db: Optional[str] = None,
    node_types: Optional[List[str]] = None,
    max_depth: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
    modules = _normalize_string_list(modules)
    if not modules:
        raise ValueError("modules must be a non-empty list.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    
    # Build node type filter
    if node_types:
        types = _normalize_string_list(node_types)
        type_conditions = " OR ".join([f"n:{t}" for t in types])
    else:
        type_conditions = "n:Function OR n:Class OR n:Type OR n:Namespace OR n:Package OR n:File"
    
    cypher = (
        f"MATCH (n) WHERE ({type_conditions}) "
        "AND any(token IN $modules WHERE n.file_path CONTAINS token OR n.path CONTAINS token) "
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
)
async def tool_listup_class_matching_path(
    class_names: List[str],
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
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
)
async def tool_list_up_entrypoint(
    modules: List[str],
    db: Optional[str] = None,
    limit: int = 200,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
    modules = _normalize_string_list(modules)
    if not modules:
        raise ValueError("modules must be a non-empty list.")
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    cypher = (
        "MATCH (caller:Function)-[:CALLS]->(f:Function) "
        "WHERE any(token IN $modules WHERE f.file_path CONTAINS token) "
        "AND none(token IN $modules WHERE caller.file_path CONTAINS token) "
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
    name="search_functions",
    description="Search nodes by name/qualified_name. Supports content_mode/include_raw_fields.",
)
async def tool_search_functions(
    query: str,
    limit: int = 50,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
    expand_search: bool = False,
) -> Dict[str, Any]:
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    qs = [t.lower().strip() for t in query.split("|") if t.strip()]
    fallback_cypher = (
        "MATCH (n) WHERE (n:Function OR n:Class OR n:Type OR n:Namespace OR n:Package) "
        "AND any(q IN $qs WHERE toLower(n.name) CONTAINS q OR toLower(n.qualified_name) CONTAINS q) "
        "AND ($project_id IS NULL OR n.project_id = $project_id) "
        "RETURN n LIMIT $limit"
    )
    fulltext_query = " OR ".join(qs)
    fulltext_cypher = (
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score "
        "WHERE (node:Function OR node:Class OR node:Type OR node:Namespace OR node:Package) "
        "AND ($project_id IS NULL OR node.project_id = $project_id) "
        "RETURN node AS n ORDER BY score DESC LIMIT $limit"
    )
    try:
        used_db, results = await _run_cypher_first(
            fulltext_cypher,
            {"index_name": FULLTEXT_SYMBOL_TEXT_INDEX, "query": fulltext_query, "limit": int(limit), "project_id": project_id},
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
    requested_type = _normalize_node_type(node_type, default_value="code")
    nodes = []
    for row in results:
        rec = _record_node(
            row["n"],
            mode,
            include_raw_fields,
            requested_node_type=requested_type,
            compact_bridge=expand_search,
        )
        rec_type = ((rec.get("properties") or {}).get("node_type") or "code").lower()
        if expand_search or rec_type == requested_type:
            nodes.append(rec)
    ids = [node.get("id") for node in nodes if node.get("id")]
    return {"db": used_db, "results": nodes, "ids": ids}


@mcp_server.tool(
    name="search_by_code",
    description="Search nodes by matching text in code snippets. Supports content_mode/include_raw_fields.",
)
async def tool_search_by_code(
    query: str,
    limit: int = 50,
    db: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    node_type: Optional[str] = None,
    expand_search: bool = False,
) -> Dict[str, Any]:
    db_candidates = _resolve_db_candidates(db)
    _require(db_candidates[0] if db_candidates else None, "db")
    qs = [t.strip() for t in query.split("|") if t.strip()]
    if not qs:
        raise ValueError("query is required.")
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
    requested_type = _normalize_node_type(node_type, default_value="code")
    nodes = []
    for row in results:
        rec = _record_node(
            row["n"],
            mode,
            include_raw_fields,
            requested_node_type=requested_type,
            compact_bridge=expand_search,
        )
        rec_type = ((rec.get("properties") or {}).get("node_type") or "code").lower()
        if expand_search or rec_type == requested_type:
            nodes.append(rec)
    return {"db": used_db, "results": nodes}


@mcp_server.tool(
    name="annotate_node",
    description="Add or update annotations for a node. Supports content_mode/include_raw_fields.",
)
async def tool_annotate_node(
    node_id: Any,
    db: Optional[str] = None,
    note: Optional[str] = None,
    tags: Optional[str] = None,
    severity: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
) -> Dict[str, Any]:
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


# ── Workflow tools ────────────────────────────────────────────────────────────

@mcp_server.tool(
    name="list_workflows",
    description=(
        "List detected business workflows (Login Flow, Payment Flow, etc.) "
        "extracted from source code. Filter by project, language, or domain."
    ),
)
async def tool_list_workflows(
    project: str = "",
    language: str = "",
    domain: str = "",
    limit: int = 50,
    db: str = "",
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    db_candidates = _resolve_db_candidates(db if db else None)
    if not db_candidates:
        db_candidates = [DEFAULT_NEO4J_DB]
    filters = []
    params: Dict[str, Any] = {"limit": limit}
    if project:
        filters.append("w.project = $project")
        params["project"] = project
    if language:
        filters.append("w.language = $language")
        params["language"] = language
    if domain:
        filters.append("w.domain = $domain")
        params["domain"] = domain
    if project_id is not None:
        filters.append("w.project_id = $project_id")
        params["project_id"] = project_id
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    query = f"""
    MATCH (w:Workflow) {where}
    RETURN w.workflow_id   AS workflow_id,
           w.name          AS name,
           w.domain        AS domain,
           w.description   AS description,
           w.confidence    AS confidence,
           w.entrypoint_id AS entrypoint_id,
           w.language      AS language,
           w.project       AS project,
           w.kind          AS kind
    ORDER BY w.confidence DESC, w.name ASC
    LIMIT $limit
    """
    used_db, records = await _run_cypher_first(query, params, db_candidates)
    workflows = [dict(r) for r in records]
    return {"db": used_db, "workflows": workflows, "total": len(workflows)}


@mcp_server.tool(
    name="get_workflow_steps",
    description=(
        "Get the ordered execution steps (Function nodes) of a specific workflow "
        "identified by its workflow_id."
    ),
)
async def tool_get_workflow_steps(
    workflow_id: str,
    db: str = "",
) -> Dict[str, Any]:
    if not workflow_id:
        raise ValueError("workflow_id is required")
    db_candidates = _resolve_db_candidates(db if db else None)
    if not db_candidates:
        db_candidates = [DEFAULT_NEO4J_DB]
    wf_query = """
    MATCH (w:Workflow {workflow_id: $wid})
    RETURN w.workflow_id   AS workflow_id,
           w.name          AS name,
           w.domain        AS domain,
           w.description   AS description,
           w.confidence    AS confidence,
           w.entrypoint_id AS entrypoint_id,
           w.language      AS language,
           w.project       AS project,
           w.kind          AS kind
    """
    steps_query = """
    MATCH (w:Workflow {workflow_id: $wid})-[s:HAS_STEP]->(f:Function)
    RETURN s.order          AS step_order,
           f.id             AS id,
           f.name           AS name,
           f.qualified_name AS qualified_name,
           f.file_path      AS file_path,
           f.start_line     AS start_line,
           f.end_line       AS end_line,
           f.summary        AS summary,
           f.kind           AS kind
    ORDER BY s.order ASC
    """
    params = {"wid": workflow_id}
    used_db, wf_records = await _run_cypher_first(wf_query, params, db_candidates)
    if not wf_records:
        return {"error": f"Workflow '{workflow_id}' not found"}
    _, step_records = await _run_cypher_first(steps_query, params, [used_db])
    return {
        "db": used_db,
        "workflow": dict(wf_records[0]),
        "steps": [dict(r) for r in step_records],
    }


@mcp_server.tool(
    name="search_workflows",
    description=(
        "Search workflows by keyword across names, descriptions, and domains. "
        "Useful for finding 'payment', 'auth', 'login' etc. workflows."
    ),
)
async def tool_search_workflows(
    query: str,
    limit: int = 20,
    db: str = "",
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not query:
        raise ValueError("query is required")
    db_candidates = _resolve_db_candidates(db if db else None)
    if not db_candidates:
        db_candidates = [DEFAULT_NEO4J_DB]
    cypher = """
    MATCH (w:Workflow)
    WHERE (toLower(w.name) CONTAINS toLower($q)
       OR toLower(w.description) CONTAINS toLower($q)
       OR toLower(w.domain) CONTAINS toLower($q))
      AND ($project_id IS NULL OR w.project_id = $project_id)
    RETURN w.workflow_id   AS workflow_id,
           w.name          AS name,
           w.domain        AS domain,
           w.description   AS description,
           w.confidence    AS confidence,
           w.entrypoint_id AS entrypoint_id,
           w.language      AS language,
           w.project       AS project,
           w.kind          AS kind
    ORDER BY w.confidence DESC, w.name ASC
    LIMIT $limit
    """
    params = {"q": query, "limit": limit, "project_id": project_id}
    used_db, records = await _run_cypher_first(cypher, params, db_candidates)
    workflows = [dict(r) for r in records]
    return {"db": used_db, "workflows": workflows, "total": len(workflows)}


@mcp_server.tool(name="list_parsers", description="List available parser types supported locally.")
async def tool_list_parsers() -> Dict[str, Any]:
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


@mcp_server.tool(
    name="list_mcp_functions",
    description="List all available MCP functions/tools with their descriptions, inputs (parameters), and outputs."
)
async def tool_list_mcp_functions() -> Dict[str, Any]:
    """
    Lists all registered MCP tools with comprehensive metadata.
    
    Returns:
        Dict containing:
        - total_count: Number of registered tools
        - functions: List of tool metadata (name, description, inputs, output_description)
    """
    import inspect

    # Get all registered tools from the FastMCP server.
    # Keep a name->tool_obj map to support both metadata catalog and signature fallback.
    tool_map: Dict[str, Any] = {}
    if hasattr(mcp_server, "_tools") and isinstance(mcp_server._tools, dict):
        for key, obj in mcp_server._tools.items():
            name = getattr(obj, "name", None) or (str(key) if key is not None else "")
            if name:
                tool_map[name] = obj
    elif hasattr(mcp_server, "list_tools"):
        for obj in mcp_server.list_tools():
            name = getattr(obj, "name", None)
            if name:
                tool_map[name] = obj
    else:
        # Fallback: inspect module globals for functions decorated with @mcp_server.tool
        for name, obj in globals().items():
            if name.startswith("tool_") and callable(obj):
                tool_map[name.replace("tool_", "")] = obj

    catalog_entries = build_catalog(set(tool_map.keys()))
    catalog_by_name = {entry["name"]: entry for entry in catalog_entries}
    functions_metadata: List[Dict[str, Any]] = []

    for name in sorted(tool_map.keys()):
        if name in catalog_by_name:
            # Prefer curated metadata with accurate use_cases/inputs/outputs.
            functions_metadata.append(catalog_by_name[name])
            continue

        tool_obj = tool_map[name]
        try:
            if hasattr(tool_obj, "__wrapped__"):
                func = tool_obj.__wrapped__
            elif callable(tool_obj):
                func = tool_obj
            else:
                continue

            sig = inspect.signature(func)
            inputs = []
            for param_name, param in sig.parameters.items():
                inputs.append(
                    {
                        "name": param_name,
                        "type": str(param.annotation) if param.annotation != inspect.Parameter.empty else "Any",
                        "required": param.default == inspect.Parameter.empty,
                        "default": str(param.default) if param.default != inspect.Parameter.empty else None,
                    }
                )

            functions_metadata.append(
                {
                    "name": name,
                    "description": getattr(tool_obj, "description", None)
                    or ((tool_obj.__doc__ or "").strip() if hasattr(tool_obj, "__doc__") else "No description available"),
                    "inputs": inputs,
                    "output": "Dict with tool-specific payload",
                }
            )
        except Exception as e:
            logger.warning("Could not introspect tool %s: %s", name, e)

    return {
        "total_count": len(functions_metadata),
        "functions": functions_metadata,
        "server_name": MCP_NAME,
        "server_version": "2.2.0"
    }




def _normalize_http_path(path: str) -> str:
    """Normalize MCP HTTP path, fixing MSYS/Git Bash Windows path conversion.

    On Windows, Git Bash converts Unix paths like ``/mcp`` to Windows absolute
    paths like ``C:/Program Files/Git/mcp``.  This function detects this and
    reverses the conversion via ``cygpath``, falling back to a best-effort
    string extraction so that the returned value always starts with ``/``.
    """
    if not path:
        return "/mcp"
    path = path.strip()
    if path.startswith("/"):
        return path
    # Attempt cygpath reverse-conversion (available in Git Bash / MSYS2).
    import subprocess as _sp
    try:
        r = _sp.run(["cygpath", "-u", path], capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            converted = r.stdout.strip()
            if converted.startswith("/"):
                return converted
    except (FileNotFoundError, _sp.TimeoutExpired, OSError):
        pass
    # Fallback: strip the Windows drive + path prefix and prepend "/".
    import re as _re
    m = _re.match(r"^[A-Za-z]:[/\\](.+)$", path)
    if m:
        return "/" + m.group(1).replace("\\", "/")
    return "/" + path.replace("\\", "/")


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
    import logging as _logging
    from pathlib import Path as _Path

    _log_file = _Path(__file__).resolve().parent.parent / "mcp_server.log"
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            _logging.FileHandler(str(_log_file), encoding="utf-8"),
            _logging.StreamHandler(),
        ],
    )
    _startup_logger = _logging.getLogger(__name__)

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
    stream_path = _normalize_http_path(args.stream_path)
    if stream_path != args.stream_path:
        _startup_logger.info(
            "Normalized stream path: %r -> %r (MSYS path conversion detected)",
            args.stream_path,
            stream_path,
        )
    endpoint = f"http://{args.host}:{args.port}{stream_path}"
    _startup_logger.info("Starting MCP server: %s", MCP_NAME)
    _startup_logger.info("Transport: %s", transport)
    if transport == "streamable-http":
        _startup_logger.info("Endpoint: %s", endpoint)
    else:
        _startup_logger.info("Endpoint: (stdio)")
    try:
        _preload_embedder_on_startup()
        kwargs: Dict[str, Any] = {"transport": transport}
        if transport != "stdio":
            kwargs.update({"host": args.host, "port": args.port})
            if stream_path:
                kwargs["path"] = stream_path
        mcp_server.run(**kwargs)
    except Exception:
        _startup_logger.exception("MCP server startup failed. See log: %s", _log_file)
        raise


if __name__ == "__main__":
    main()

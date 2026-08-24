from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import signal
from pathlib import Path

from typing import Any, Dict, Iterable, List, Optional, Tuple

import logging

import httpx
import mcp.types as mcp_types
import torch
from fastmcp import FastMCP
from transformers import AutoModel, AutoTokenizer


_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

_MCP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.core.provider_contract import (
    is_database_not_found_error,
    normalize_graph_direction,
    normalize_graph_provider_name,
)
from tools.graph.core.shared_runtime import get_shared_graph_driver
from tools.common.project_scope import prepare_project_scope_parameters, qdrant_project_filter
from tools.common.local_qdrant import (
    collection_info_payload,
    collections_payload,
    default_local_qdrant_path,
    get_code_qdrant_store,
    query_points,
)
from tools.common.project_registry import (
    ProjectNotRegisteredError,
    list_registered_projects,
    resolve_project_targets,
)
from semantic_graph_expansion import expand_semantic_results
from tool_metadata import build_catalog
from falkordb_discovery import discover_falkordb_data_files
from framework_registry import (
    backend_label_union,
    backend_property_union,
    backend_text_property_union,
    capability_for_parser,
    default_relationships,
    parser_aliases,
    searchable_labels,
    searchable_properties,
    servlet_active_generation_predicate,
    text_search_properties,
)
from tools.common.call_evidence import (
    RESOLUTION_CLASS_DIRECT_RESOLVED,
    exact_frontier_coverage,
    frontier_coverage,
    suggested_next_semantic_scope,
    traversal_outcome,
)
from tools.cplus.evidence_merge import proc_data_impact_coverage
from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA as _CODE_GRAPH_SCHEMA


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
DEFAULT_QDRANT_PATH = default_local_qdrant_path()
DEFAULT_QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "kotlin_functions")


def _normalize_graph_provider(value: Optional[str]) -> str:
    return normalize_graph_provider_name(value)


DEFAULT_GRAPH_PROVIDER = _normalize_graph_provider(
    os.environ.get("CODE_GRAPH_PROVIDER")
    or os.environ.get("GRAPH_PROVIDER")
    or os.environ.get("MCP_GRAPH_PROVIDER")
)
if DEFAULT_GRAPH_PROVIDER == "neo4j":
    DEFAULT_NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    DEFAULT_NEO4J_USER = os.environ.get("NEO4J_USER")
    DEFAULT_NEO4J_PASSWORD = os.environ.get("NEO4J_PASS")
    DEFAULT_NEO4J_DB = os.environ.get("NEO4J_DB") or "hyper_graph"
else:
    DEFAULT_NEO4J_URI = "bolt://localhost:7687"
    DEFAULT_NEO4J_USER = None
    DEFAULT_NEO4J_PASSWORD = None
    DEFAULT_NEO4J_DB = "hyper_graph"
DEFAULT_FALKORDB_GRAPH = os.environ.get("FALKORDB_GRAPH") or os.environ.get("FALKORDB_DATABASE") or "hyper_graph"
DEFAULT_GRAPH_DB = DEFAULT_FALKORDB_GRAPH if DEFAULT_GRAPH_PROVIDER == "falkordb" else DEFAULT_NEO4J_DB
FULLTEXT_SYMBOL_TEXT_INDEX = "mcp_symbol_text_ft_v2"
FULLTEXT_SYMBOL_CODE_INDEX = "mcp_symbol_code_ft_v2"

IPC_MESSAGES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "temp", "ipc_messages.json")

MCP_NAME = "Project Call Graph"

INSTRUCTIONS = """Project Call Graph MCP (local mode) reads directly from Neo4j and Qdrant.

Discovery:
- Call `list_mcp_functions` first to get the exact tool list and parameters supported by this backend.

Core capability groups:
- Symbol/graph search: search_functions, search_by_code, get_symbol, get_node_details
- Call graph traversal: query_subgraph, find_paths, find_path_between_module, trace_flow, trace_flow_between_module
- Pro*C migration impact: analyze_proc_data_impact (function -> SQL -> tables, host joins, coverage-aware)
- Module/class views: listup_symbols_matching_file_path, listup_class_matching_path, list_up_entrypoint
- Infrastructure: list_databases, list_qdrant_collections, list_parsers
- Utilities: semantic_search, get_ipc_message, list_possible_calls, annotate_node, find_screen_workflows

Response content controls (most tools):
- content_mode: auto (default), summary, comment, code, name
  - auto fallback order: summary -> comment -> name
- include_raw_fields: false by default; when true, keep summary/comment/code fields in payload

Call-path relationship defaults (query_subgraph/find_paths/find_path_between_module):
- Optional parser_type selects default relation types when include_possible/include_fp are both false.
- Parser mapping: cplus/cpp/c++/c/clang/delphi/pascal -> C++ rel types; android/android-kotlin/kotlin-android/java/kotlin/jvm -> Android rel types; others -> generic rel types.
- If include_possible/include_fp is set, relation types are forced to CALLS plus requested optional types.
- query_subgraph accepts query_profile: 'strict' (accepted direct semantic CALLS only) or 'conservative' (unions weaker evidence without relabeling).
- Semantic evidence results carry semantic_coverage (status complete/partial/unknown with reasons); an empty traversal over an incomplete frontier returns outcome='incomplete' with suggested_next_semantic_scope — never treat it as 'no callers'.
- The server filters chosen relation types to relationship types that actually exist in the selected Neo4j database.

When include_raw_fields=false, only properties.content is returned (plus metadata) to reduce payload size.

Tool inputs:
- Tools accept a payload dict argument named `payload`.
- Top-level tool arguments are also accepted; payload (when provided) overrides them.
- Required fields are validated per tool; missing/invalid payloads raise ValueError.
"""

mcp_server = FastMCP(
    name=MCP_NAME,
    version="2.2.0",
    instructions=INSTRUCTIONS,
)

# ``active_project`` was removed per the unified ingest/query contract plan.
# Callers must pass ``parser_type`` and ``project_id`` explicitly on every
# tool call. See docs/PROJECT_REGISTRY.md for the new contract.

_graph_driver: Optional[GraphDriver] = None
_embedder_cache: Dict[Tuple[str, str], Tuple[Any, Any, torch.device]] = {}
logger = logging.getLogger("project_call_graph.mcp.server")


async def _get_graph_driver() -> GraphDriver:
    global _graph_driver
    if _graph_driver is not None:
        return _graph_driver
    if DEFAULT_GRAPH_PROVIDER == "falkordb":
        from cortex_harness.storage import resolve_storage

        config = {
            "path": os.environ.get("FALKORDB_PATH")
            or str(resolve_storage(Path.cwd()).falkordb_code_path),
            "graph": DEFAULT_FALKORDB_GRAPH,
            "owner_id": os.environ.get("CORTEX_STORAGE_OWNER", "code"),
            "instance_id": os.environ.get("CORTEX_STORAGE_INSTANCE", "default"),
            "additional_paths": discover_falkordb_data_files(),
        }
        _graph_driver = await get_shared_graph_driver(GraphProvider.FALKORDB, config)
        return _graph_driver
    if not DEFAULT_NEO4J_USER or not DEFAULT_NEO4J_PASSWORD:
        raise RuntimeError("NEO4J_USER and NEO4J_PASS must be set.")
    config = {
        "uri": DEFAULT_NEO4J_URI,
        "user": DEFAULT_NEO4J_USER,
        "password": DEFAULT_NEO4J_PASSWORD,
    }
    _graph_driver = await get_shared_graph_driver(GraphProvider.NEO4J, config)
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
        default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
        if default_db in available:
            logger.warning("Falling back to default database: %s", default_db)
            return default_db
        return None
    return normalized


def _resolve_db_candidates(project_id: Optional[str]) -> List[str]:
    """Return the ordered list of graph databases a query should target.

    Scoped callers (those that passed an explicit ``project_id``) get back
    a list anchored on that project — the registered graph when the project
    is in the registry, or the literal ``project_id`` itself when it is not
    so out-of-band shards stay reachable. Unscoped callers get the union of
    every registered project's graph plus the env-configured default graph
    (``DEFAULT_GRAPH_DB``), so an unscoped query fans out across the whole
    account *including* the active instance's primary graph — that graph
    may belong to a project that is not registered (e.g. started via
    ``CORTEX_STORAGE_INSTANCE``/``FALKORDB_GRAPH`` without a dev.json), and
    omitting it made schema introspection see only empty graphs.
    ``DEFAULT_GRAPH_DB`` is never a scoped fallback.
    """
    candidates: List[str] = []
    if project_id and str(project_id).strip():
        try:
            targets = resolve_project_targets(project_id)
            graph_name = _normalize_db_name(targets.code_graph)
            if graph_name and graph_name not in candidates:
                candidates.append(graph_name)
        except ProjectNotRegisteredError:
            # Unregistered project: treat the raw id as the graph name so
            # callers can target a shard not yet registered (e.g. a freshly
            # created instance whose dev.json hasn't been picked up).
            if project_id not in candidates:
                candidates.append(project_id)
    else:
        for registered_project in list_registered_projects():
            targets = resolve_project_targets(registered_project)
            graph_name = _normalize_db_name(targets.code_graph)
            if graph_name and graph_name not in candidates:
                candidates.append(graph_name)
        default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
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


# Legacy hardcoded label set used before parser profiles existed. Kept for
# unscoped direct calls; fan-out calls OR it with the per-backend label union
# so a single parser-less dispatch does not silently drop profile labels.
_LEGACY_SEARCH_LABELS: Tuple[str, ...] = (
    "Function", "Type", "Namespace", "File", "Field", "Alias", "Template",
    "FunctionType", "Event", "Project", "Resource", "UIControl",
)
_LEGACY_SEARCH_FRAMEWORKS: Tuple[str, ...] = ("spring", "servlet_jsp", "mybatis")


def _search_label_predicate(
    variable: str,
    profile_labels: Tuple[str, ...],
    fanout: bool = False,
) -> str:
    """Return the label/framework predicate for a symbol search query.

    * ``profile_labels`` present (parser/framework scoped) → use them; the
      legacy framework IN clause is still appended so framework-scoped
      searches keep matching cross-framework symbol types (``Service``,
      ``Controller`` …).
    * fan-out with no profile → union of every label mapped to this query
      engine, OR'd with the legacy set (recall guard, plan D3).
    * otherwise → legacy set only (unchanged behavior).
    """
    if profile_labels:
        labels: Tuple[str, ...] = profile_labels
    elif fanout:
        labels = tuple(sorted(set(backend_label_union("cplus")) | set(_LEGACY_SEARCH_LABELS)))
    else:
        labels = _LEGACY_SEARCH_LABELS
    clauses = [f"{variable}:{label}" for label in labels]
    frameworks = ", ".join(f"'{name}'" for name in _LEGACY_SEARCH_FRAMEWORKS)
    clauses.append(f"{variable}.framework IN [{frameworks}]")
    return "(" + " OR ".join(clauses) + ")"


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


DEFAULT_FLOW_REL_TYPES_ANDROID = list(default_relationships("android"))
DEFAULT_FLOW_REL_TYPES_CPLUS = list(default_relationships("cplus"))

DEFAULT_FLOW_REL_TYPES_GENERIC = [
    "CALLS",
    "DECLARES",
    "CONTAINS",
    "DEPENDS_ON",
]

PARSER_ALIASES_ANDROID = set(parser_aliases("android"))
PARSER_ALIASES_CPLUS = set(parser_aliases("cplus"))
PARSER_ALIASES_JVM = set(capability_for_parser("jvm").aliases)


def _normalize_parser_type(value: Optional[str]) -> str:
    parser = (value or "").strip().lower()
    return parser


def _get_default_flow_rel_types(parser_type: Optional[str]) -> List[str]:
    parser = _normalize_parser_type(parser_type)
    if not parser:
        return list(DEFAULT_FLOW_REL_TYPES_CPLUS)
    capability = capability_for_parser(parser)
    if capability:
        return list(default_relationships(capability.name))
    return list(DEFAULT_FLOW_REL_TYPES_GENERIC)


def _profile_rel_types(parser_type: Optional[str], profile: Optional[str]) -> Optional[List[str]]:
    """Relationship types for a named query profile, or None when absent.

    ``strict`` selects accepted direct semantic CALLS only; ``conservative``
    unions the weaker evidence classes without relabeling them.  Unknown
    profiles fail closed with ``ValueError``.
    """

    normalized = str(profile or "").strip().lower()
    if not normalized or normalized == "default":
        return None
    capability = capability_for_parser(_normalize_parser_type(parser_type) or "cplus")
    if capability is None:
        return None
    relationships = capability.default_query_profiles.get(normalized)
    if relationships is None:
        raise ValueError(
            f"unknown query profile: {profile!r} for parser {capability.name!r}; "
            f"expected one of {sorted(capability.default_query_profiles)}"
        )
    return list(relationships)


async def _semantic_coverage_block(
    dbs: List[str], project_id: Optional[str]
) -> Dict[str, Any]:
    """Aggregate semantic coverage/freshness over the queried databases.

    Reads persisted ``SemanticCoverage`` records for the requested scope.
    A missing coverage plane yields ``unknown`` — which never licenses a
    negative conclusion downstream.
    """

    query = (
        "MATCH (scope:SemanticScopeManifestKey) "
        "WHERE scope.project_id = $project_id OR $project_id IS NULL "
        "OPTIONAL MATCH (coverage:SemanticCoverage) "
        "WHERE coverage.project_id = scope.project_id "
        "AND coverage.generation_id = scope.generation_id "
        "AND coverage.revision = scope.revision "
        "AND coverage.policy_version = scope.policy_version "
        "AND coverage.tu_key = scope.tu_key "
        "AND coverage.config_fingerprint = scope.config_fingerprint "
        "RETURN scope.project_id AS project_id, "
        "scope.generation_id AS generation_id, scope.revision AS revision, "
        "scope.policy_version AS policy_version, scope.tu_key AS tu_key, "
        "scope.config_fingerprint AS config_fingerprint, "
        "coverage.status AS status, coverage.detail AS detail"
    )
    params: Dict[str, Any] = {"project_id": project_id or None}
    records: List[Dict[str, Any]] = []
    for db in dbs:
        try:
            records.extend(await _run_cypher(query, params, db))
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            logger.warning("Unable to read semantic coverage from %s: %s", db, exc)
    revisions = sorted({str(row.get("revision")) for row in records if row.get("revision")})
    expected = [
        {field: row.get(field) for field in (
            "project_id", "generation_id", "revision", "policy_version",
            "tu_key", "config_fingerprint",
        )}
        for row in records
    ]
    actual = [row for row in records if row.get("status") is not None]
    block = exact_frontier_coverage(expected, actual)
    block.update(
        {
            "served_revision": revisions[-1] if revisions else None,
            "served_schema_fingerprint": _CODE_GRAPH_SCHEMA.fingerprint,
            "semantic_policy_version": next(
                (str(row.get("policy_version")) for row in records if row.get("policy_version")),
                None,
            ),
            "evidence_record_count": len(actual),
        }
    )
    return block


def _outcome_payload(
    coverage_block: Dict[str, Any], *, result_is_empty: bool, extra: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Typed outcome fields attached to graph/impact results.

    Fail-closed: an empty traversal under incomplete/unknown coverage is
    reported as ``incomplete`` with reasons and a suggested next semantic
    scope, never as an authoritative negative answer.
    """

    outcome = traversal_outcome(str(coverage_block.get("status")), result_is_empty)
    payload: Dict[str, Any] = {
        "outcome": outcome,
        "semantic_coverage": coverage_block,
    }
    if outcome == "incomplete":
        payload["suggested_next_semantic_scope"] = suggested_next_semantic_scope(coverage_block)
        payload["reason"] = "semantic frontier incomplete; negative conclusions are not authoritative"
    if extra:
        payload.update(extra)
    return payload


def _filter_strict_edges(
    graph: Dict[str, Any], function_id: str
) -> Tuple[Dict[str, Any], int]:
    """Restrict a subgraph to accepted direct semantic CALLS edges.

    Relationship type alone cannot express the strict contract: legacy
    ``CALLS`` edges written without the evidence contract have no
    ``resolution_class`` and must not appear as accepted semantic calls.
    Only edges carrying ``resolution_class = direct_resolved`` survive;
    nodes are pruned to the surviving frontier plus the seed function.
    """

    kept_edges: List[Dict[str, Any]] = []
    dropped = 0
    seed = str(function_id)
    for edge in graph.get("edges") or []:
        props = edge.get("properties") or {}
        if (
            edge.get("type") == "CALLS"
            and props.get("resolution_class") == RESOLUTION_CLASS_DIRECT_RESOLVED
        ):
            kept_edges.append(edge)
        else:
            dropped += 1
    keep_ids = {seed}
    for edge in kept_edges:
        keep_ids.add(str(edge.get("start_id")))
        keep_ids.add(str(edge.get("end_id")))
    graph["edges"] = kept_edges
    graph["nodes"] = [
        node for node in graph.get("nodes") or [] if str(node.get("id")) in keep_ids
    ]
    return graph, dropped


async def _attach_semantic_result_fields(
    dbs: List[str],
    project_id: Optional[str],
    *,
    result_is_empty: bool,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Coverage/outcome fields shared by graph, trace, and impact results."""

    if not dbs:
        coverage = frontier_coverage([])
    else:
        coverage = await _semantic_coverage_block(dbs, project_id)
    return _outcome_payload(coverage, result_is_empty=result_is_empty, extra=extra)


def _fallback_node_name(properties: Dict[str, Any], node_id: Optional[str]) -> str:
    for key in ("name", "qualified_name", "type_signature", "target_name", "file_path", "path"):
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
                    isinstance(item, dict) and "nodes" in item
                    and ("relationships" in item or "edges" in item)
                ):
                    path = item
                    break
            else:
                continue
        if hasattr(path, "nodes"):
            path_nodes = path.nodes
            path_rels = path.relationships
        elif isinstance(path, dict) and "nodes" in path:
            # The FalkorDB driver normalizes Path objects to
            # ``{"nodes": [...], "edges": [...]}`` — accept both key spellings.
            path_nodes = path.get("nodes", [])
            path_rels = path.get("relationships") or path.get("edges") or []
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


def _get_embedder(model_name: str, device_name: Optional[str] = None) -> Tuple[Any, Any, Any]:
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


def _embed_query_with_model(tokenizer: Any, model: Any, device: Any, text: str) -> List[float]:
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
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Vector search via the local Qdrant query API.

    Kept byte-identical with the same function in ``fastmcp_server.py``,
    ``mcp/android/android_mcp.py`` and ``mcp/java/java_mcp.py`` — every
    backend ships its own copy and they all need the same v1/v2 routing
    contract. When changing this body, update those siblings too.

    Migrated from the legacy search operation because that
    endpoint's named-vector payload (``{"vector": {"name": "...",
    "vector": [...]}}``) is easy to malform — the prior bug shipped
    ``{"vector": {"semantic": [...]}}`` and Qdrant returned 400
    ``"did not match any variant of untagged enum NamedVectorStruct"``.
    The Query API takes a flat ``{"query": vec, "using": "<name>"}``
    that works for both single-vector (v1) and named-vector (v2)
    collections.
    """
    project_filter = qdrant_project_filter(project_id)
    # Normalise: Query API wraps hits in ``result.points``; legacy
    # Search API returned them under ``result`` directly. Re-shape so
    # ``_merge_qdrant_results`` (which walks ``payload["result"]``)
    # doesn't need a separate code path per backend version.
    hits = query_points(
        get_code_qdrant_store(),
        collection,
        vector,
        limit=top_k,
        vector_name=vector_name,
        query_filter=project_filter,
    )
    return {"result": hits, "status": "ok"}


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


def _is_project_scope_collection(scope: str, collection: str) -> bool:
    scope = (scope or "").strip()
    collection = (collection or "").strip()
    if not scope or not collection:
        return False
    if collection == f"{scope}_mess":
        return True
    return (
        collection.startswith(f"{scope}_")
        and "__" in collection
        and collection.endswith("_functions")
    )


def _resolve_collection_scopes(tokens: List[str], available: List[str]) -> List[str]:
    resolved: List[str] = []
    for token in tokens:
        if token in available:
            candidates = [token]
        else:
            candidates = [
                name for name in available
                if _is_project_scope_collection(token, name)
            ]
        for candidate in candidates:
            if candidate not in resolved:
                resolved.append(candidate)
    return resolved


async def _resolve_base_collections(
    collection: Optional[Any],
    qdrant_url: str,
) -> Tuple[List[str], bool]:
    tokens = _normalize_collections(collection)
    explicit = bool(tokens)
    if not tokens:
        tokens = _normalize_collections(os.environ.get("QDRANT_COLLECTION", ""))

    payload = await _fetch_qdrant_collections(qdrant_url)
    available = payload.get("collections", [])
    if not available:
        return [], explicit

    if not tokens:
        return available, explicit

    resolved = _resolve_collection_scopes(tokens, available)
    if resolved:
        return resolved, explicit
    if explicit:
        raise ValueError(
            "Requested Qdrant collection scope does not match any available "
            f"collection. scopes={tokens!r} available={available!r}. "
            "Pass a valid collection name/prefix or omit collection to search "
            "every available collection."
        )
    return available, explicit


def _merge_qdrant_results(
    collections: List[Tuple[str, Optional[str]]],
    vector: List[float],
    top_k: int,
    qdrant_url: str,
    project_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    combined: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    for col, vector_name in collections:
        try:
            payload = _qdrant_search(
                col, vector, top_k, qdrant_url, vector_name, project_id
            )
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
    return collections_payload(
        get_code_qdrant_store(),
        include_vectors=include_vectors,
    )


async def _fetch_qdrant_collection_info(collection: str, qdrant_url: str) -> Dict[str, Any]:
    return collection_info_payload(get_code_qdrant_store(), collection)


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
    return is_database_not_found_error(exc)


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


async def _list_relationship_types(dbs: List[str]) -> Optional[List[str]]:
    """Return relationship types, or ``None`` when schema inspection fails.

    Unscoped queries fan out across every discovered graph, so the union of
    all candidates is the authoritative schema: returning the first
    candidate's (possibly empty) schema made an empty graph — e.g. a freshly
    provisioned default shard — veto relationships that exist in the other
    graphs, surfacing as bogus ``unsupported_capability`` errors.
    """
    query_call = (
        "CALL db.relationshipTypes() YIELD relationshipType "
        "RETURN relationshipType AS rel_type"
    )
    query_show = "SHOW RELATIONSHIP TYPES YIELD relationshipType RETURN relationshipType AS rel_type"
    collected: Optional[List[str]] = None
    for db in [item for item in dbs if item]:
        try:
            try:
                rows = await _run_cypher(query_call, {}, db)
            except Exception:
                rows = await _run_cypher(query_show, {}, db)
            if collected is None:
                collected = []
            for row in rows:
                rel_type = row.get("rel_type")
                if isinstance(rel_type, str):
                    rel_upper = rel_type.upper()
                    if rel_upper not in collected:
                        collected.append(rel_upper)
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            logger.warning("Unable to list relationship types from %s: %s", db, exc)
            break
    return collected


async def _list_node_labels(dbs: List[str]) -> Optional[List[str]]:
    """Return provider node labels, or ``None`` when schema inspection fails.

    Mirrors ``_list_relationship_types``: union across all candidate graphs
    rather than trusting the first (possibly empty) one.
    """
    query_call = "CALL db.labels() YIELD label RETURN label"
    query_show = "SHOW NODE LABELS YIELD label RETURN label"
    collected: Optional[List[str]] = None
    for db in [item for item in dbs if item]:
        try:
            try:
                rows = await _run_cypher(query_call, {}, db)
            except Exception:
                rows = await _run_cypher(query_show, {}, db)
            if collected is None:
                collected = []
            for row in rows:
                label = row.get("label")
                if isinstance(label, str) and label not in collected:
                    collected.append(label)
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            logger.warning("Unable to list node labels from %s: %s", db, exc)
            break
    return collected


async def _resolve_call_rel_types(
    include_possible: bool,
    include_fp: bool,
    parser_type: Optional[str],
    db_candidates: List[str],
) -> List[str]:
    if include_possible or include_fp:
        rel_types = ["CALLS"]
        if include_possible:
            rel_types.append("POSSIBLE_CALLS")
        if include_fp:
            rel_types.append("CALLS_FUNCTION_POINTER")
    else:
        rel_types = _get_default_flow_rel_types(parser_type)
    available = await _list_relationship_types(db_candidates)
    if not available:
        return rel_types
    available_set = set(available)
    filtered = [item for item in rel_types if item in available_set]
    return filtered or rel_types


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


async def _resolve_rel_types_with_diagnostics(
    rel_types_input: Any,
    parser_type: Optional[str],
    db_candidates: List[str],
    *,
    defaults: Optional[List[str]] = None,
    explicit: bool = False,
) -> Tuple[List[str], Dict[str, Any]]:
    requested = _normalize_rel_types(
        rel_types_input,
        default=defaults or _get_default_flow_rel_types(parser_type),
    )
    available = await _list_relationship_types(db_candidates)
    if available is None:
        return requested, {
            "schema_status": "unavailable",
            "requested_relationships": requested,
            "used_relationships": requested,
            "omitted_relationships": [],
            "explicit_request": explicit,
            "message": "Provider relationship schema could not be inspected; requested relationships were retained.",
        }

    available_set = set(available)
    used = [item for item in requested if item in available_set]
    omitted = [item for item in requested if item not in available_set]
    status = "supported" if not omitted else ("partial" if used else "unsupported")
    return used, {
        "schema_status": "available",
        "support_status": status,
        "requested_relationships": requested,
        "used_relationships": used,
        "omitted_relationships": omitted,
        "available_relationships": available,
        "explicit_request": explicit,
    }


def _unsupported_relationship_result(
    parser_type: Optional[str],
    diagnostics: Dict[str, Any],
) -> Dict[str, Any]:
    parser = _normalize_parser_type(parser_type) or "generic"
    requested = diagnostics.get("requested_relationships") or []
    return {
        "error": (
            f"Parser '{parser}' requested relationships that are unavailable in the active provider: "
            + ", ".join(requested)
        ),
        "error_type": "unsupported_capability",
        "capability_diagnostics": diagnostics,
    }


async def _run_cypher_first(query: str, params: Dict[str, Any], dbs: List[str]) -> Tuple[str, List[Dict[str, Any]]]:
    params = prepare_project_scope_parameters(query, params)
    last_error: Optional[Exception] = None
    requested = [db for db in dbs if db]
    available = await _list_databases()
    is_scoped = bool(str(params.get("project_id") or "").strip())

    if available:
        invalid = [db for db in requested if db not in available]
        if invalid:
            logger.warning(
                "Ignoring unknown database(s): %s. Available: %s",
                ", ".join(sorted(set(invalid))),
                ", ".join(available),
            )

        if is_scoped:
            # Scoped queries must hit exactly the databases the caller named.
            # Unknown shards are an error condition: do not silently fall back
            # to a default that belongs to another project.
            candidates = [db for db in requested if db in available]
            if not candidates:
                default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
                raise RuntimeError(
                    "No database candidates available for scoped query. "
                    "Database not found. Use list_databases to inspect available DBs "
                    f"and activate_project(database_name=...) to switch. "
                    f"Available: {available}. Default: {default_db}."
                )
        else:
            # Unscoped queries fan out across every discovered database. The
            # caller's ``dbs`` is used as the canonical ordering of preference;
            # any extra discovered graphs are appended so newly provisioned
            # instances are picked up without code changes.
            candidates = [db for db in requested if db in available]
            for db in available:
                if db not in candidates:
                    candidates.append(db)
    else:
        candidates = list(requested)

    aggregate = len(candidates) > 1 and not is_scoped
    used_db: Optional[str] = None
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for db in candidates:
        try:
            result = await _run_cypher(query, params, db)
            if not aggregate:
                return db, result
            used_db = used_db or db
            for record in result:
                marker = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
                if marker not in seen:
                    seen.add(marker)
                    merged.append(record)
        except Exception as exc:
            last_error = exc
            if _is_db_not_found(exc):
                continue
            raise
    if used_db is not None:
        try:
            global_limit = int(params.get("limit")) if params.get("limit") is not None else None
        except (TypeError, ValueError):
            global_limit = None
        if global_limit is not None and global_limit >= 0:
            return used_db, merged[:global_limit]
        return used_db, merged
    if last_error and _is_db_not_found(last_error):
        default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
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
    if DEFAULT_GRAPH_PROVIDER == "falkordb":
        return await driver.list_databases()
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
    WHERE ($project_id = '' OR coalesce(m.project_id_normalized, '') = $project_id)
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
    messages: List[Dict[str, Any]] = []
    for row in rows:
        messages.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "sender": row.get("sender"),
                "receiver": row.get("receiver"),
                "payload": row.get("payload"),
                "response": row.get("response"),
                "explanation": row.get("explanation"),
                "source": {
                    "file": row.get("file_path"),
                    "line": row.get("line"),
                },
                "confidence": row.get("confidence"),
                "language": row.get("language"),
            }
        )
    return messages


@mcp_server.tool(name="list_databases", description="List available Neo4j databases.", output_schema=None)
async def tool_list_databases(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _coerce_payload(payload)
    names = await _list_databases()
    default_db = _normalize_db_name(DEFAULT_GRAPH_DB)
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
            "db": project_id,
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
        print(f"Loaded {len(messages)} IPC messages.")
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
        "Set expand_graph=true to expand vector hits through the configured graph database."
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
    expand_graph: bool = False,
    graph_depth: int = 2,
    graph_direction: str = "both",
    graph_rel_types: Optional[Any] = None,
    graph_limit: int = 50,
    project_id: Optional[str] = None,
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
            "expand_graph": expand_graph,
            "graph_depth": graph_depth,
            "graph_direction": graph_direction,
            "graph_rel_types": graph_rel_types,
            "graph_limit": graph_limit,
            "project_id": project_id,
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
    expand_graph = payload.get("expand_graph", False)
    graph_depth = payload.get("graph_depth", 2)
    graph_direction = payload.get("graph_direction", "both")
    graph_rel_types = payload.get("graph_rel_types")
    graph_limit = payload.get("graph_limit", 50)
    db = payload.get("db")
    project_id = payload.get("project_id")
    capability_diagnostics: Optional[Dict[str, Any]] = None
    if expand_graph:
        graph_rel_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
            graph_rel_types,
            payload.get("parser_type"),
            _resolve_db_candidates(project_id),
            explicit=(
                graph_rel_types is not None
                and not bool(payload.get("_capability_default_relationships"))
            ),
        )
        if not graph_rel_types:
            return _unsupported_relationship_result(
                payload.get("parser_type"), capability_diagnostics,
            )
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required.")
    model_name = model_path or DEFAULT_MODEL
    qdrant_url = qdrant_url or DEFAULT_QDRANT_PATH
    vector = _embed_query(query, model_name)
    vector_len = len(vector)
    logger.info("[semantic_search] model=%s vector_len=%s", model_name, vector_len)
    print(f"[semantic_search] model={model_name} vector_len={vector_len}", flush=True)
    base_collections, explicit_base = await _resolve_base_collections(collection, qdrant_url)
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
        items, errors = _merge_qdrant_results(comment_collections, vector, top_k, qdrant_url, project_id)
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
        await expand_semantic_results(
            results,
            run_cypher_first=_run_cypher_first,
            db_candidates=_resolve_db_candidates(project_id),
            expand_graph=expand_graph,
            graph_depth=graph_depth,
            graph_direction=graph_direction,
            graph_rel_types=graph_rel_types,
            graph_limit=graph_limit,
            project_id=project_id,
        )
        if capability_diagnostics:
            results["capability_diagnostics"] = capability_diagnostics
        return results
    if mode == "code":
        items, errors = _merge_qdrant_results(code_collections, vector, top_k, qdrant_url, project_id)
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
        await expand_semantic_results(
            results,
            run_cypher_first=_run_cypher_first,
            db_candidates=_resolve_db_candidates(project_id),
            expand_graph=expand_graph,
            graph_depth=graph_depth,
            graph_direction=graph_direction,
            graph_rel_types=graph_rel_types,
            graph_limit=graph_limit,
            project_id=project_id,
        )
        if capability_diagnostics:
            results["capability_diagnostics"] = capability_diagnostics
        return results
    combined_map = {(col, name) for col, name in filtered_base}
    combined_map.update(comment_collections)
    combined_map.update(code_collections)
    combined_collections = list(combined_map)
    items, errors = _merge_qdrant_results(combined_collections, vector, top_k, qdrant_url, project_id)
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
    await expand_semantic_results(
        results,
        run_cypher_first=_run_cypher_first,
        db_candidates=_resolve_db_candidates(project_id),
        expand_graph=expand_graph,
        graph_depth=graph_depth,
        graph_direction=graph_direction,
        graph_rel_types=graph_rel_types,
        graph_limit=graph_limit,
        project_id=project_id,
    )
    if capability_diagnostics:
        results["capability_diagnostics"] = capability_diagnostics
    return results


@mcp_server.tool(name="list_qdrant_collections", description="List available Qdrant collections.", output_schema=None)
async def tool_list_qdrant_collections(
    qdrant_url: Optional[str] = None,
    include_vectors: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(payload, {"qdrant_url": qdrant_url, "include_vectors": include_vectors})
    qdrant_url = payload.get("qdrant_url") or DEFAULT_QDRANT_PATH
    include_vectors = payload.get("include_vectors", False)
    return await _fetch_qdrant_collections(qdrant_url, include_vectors=include_vectors)


@mcp_server.tool(
    name="get_symbol",
    description="Retrieve metadata for a specific node by id. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_get_symbol(
    node_id: Any = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "node_id": node_id,
            "db": project_id,
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
    candidates = _resolve_db_candidates(project_id)
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
    limit: int = 200,
    top_k: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    
    driver = await _get_graph_driver()
    mode = _normalize_content_mode(content_mode)
    
    for db_candidate in db_candidates:
        try:
            raw_nodes, raw_edges = await driver.list_possible_calls(
                limit=int(limit),
                project_id=project_id,
                database=db_candidate
            )
            if raw_nodes or raw_edges:
                nodes_dict: Dict[str, Dict[str, Any]] = {}
                for node in raw_nodes:
                    recorded = _record_node(node, mode, include_raw_fields)
                    if recorded.get("id"):
                        nodes_dict[recorded["id"]] = recorded
                edges = [_record_rel(edge) for edge in raw_edges]
                return {"db": db_candidate, "nodes": list(nodes_dict.values()), "edges": edges}
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    return {"db": db_candidates[0] if db_candidates else None, "nodes": [], "edges": []}


@mcp_server.tool(
    name="get_node_details",
    description="Fetch metadata for multiple node IDs. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_get_node_details(
    node_ids: Optional[List[Any]] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "node_ids": node_ids,
            "db": project_id,
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
    candidates = _resolve_db_candidates(project_id)
    _require(candidates[0] if candidates else None, "db")
    ids = [str(item) for item in node_ids]
    
    driver = await _get_graph_driver()
    for db_candidate in candidates:
        try:
            nodes = await driver.find_nodes_by_ids(ids, project_id=project_id, database=db_candidate)
            if nodes:
                mode = _normalize_content_mode(content_mode)
                result_nodes = [_record_node(node, mode, include_raw_fields) for node in nodes]
                return {"db": db_candidate, "nodes": result_nodes}
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    return {"db": candidates[0] if candidates else None, "nodes": []}


@mcp_server.tool(
    name="query_subgraph",
    description=(
        "Return call graph context around a function ID. Supports content_mode/include_raw_fields. "
        "query_profile 'strict' selects accepted direct semantic CALLS only (edges are "
        "post-filtered to resolution_class=direct_resolved; at depth>1 a kept edge may share a "
        "path with a legacy CALLS hop — treat deep strict results as best-effort); "
        "'conservative' unions POSSIBLE_CALLS/CALLS_FUNCTION_POINTER without relabeling them. "
        "Results carry semantic_coverage; an empty traversal over an incomplete frontier "
        "returns outcome='incomplete', never an authoritative negative."
    ),
    output_schema=None
)
async def tool_query_subgraph(
    function_id: Any = None,
    direction: str = "all",
    max_depth: int = 2,
    include_possible: bool = False,
    include_fp: bool = False,
    rel_types: Optional[List[str]] = None,
    relationship_types: Optional[Any] = None,
    query_profile: Optional[str] = None,
    parser_type: Optional[str] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "db": project_id,
            "function_id": function_id,
            "direction": direction,
            "max_depth": max_depth,
            "include_possible": include_possible,
            "include_fp": include_fp,
            "rel_types": rel_types,
            "relationship_types": relationship_types,
            "query_profile": query_profile,
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
    query_profile = payload.get("query_profile")
    parser_type = payload.get("parser_type")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    if function_id is None:
        raise ValueError("function_id is required.")
    candidates = _resolve_db_candidates(project_id)
    _require(candidates[0] if candidates else None, "db")
    function_id = str(function_id)
    depth = _normalize_depth(max_depth, default=2, max_limit=10)
    direction = direction.lower()
    profile_rels = _profile_rel_types(parser_type, query_profile)
    rel_input = payload.get("relationship_types")
    if rel_input is None:
        rel_input = payload.get("rel_types")
    if rel_input is None and profile_rels is not None:
        # A named profile overrides the legacy include_possible/include_fp
        # switches; its evidence-class selection is explicit and versioned.
        rel_input = profile_rels
    if rel_input is None:
        rel_input = ["CALLS"]
        if include_possible:
            rel_input.append("POSSIBLE_CALLS")
        if include_fp:
            rel_input.append("CALLS_FUNCTION_POINTER")
        if not include_possible and not include_fp:
            rel_input = _get_default_flow_rel_types(parser_type)
    rel_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
        rel_input,
        parser_type,
        candidates,
        explicit=not bool(payload.get("_capability_default_relationships")),
    )
    if query_profile:
        capability_diagnostics = dict(capability_diagnostics)
        capability_diagnostics["query_profile"] = str(query_profile).strip().lower()
    if not rel_types:
        return _unsupported_relationship_result(parser_type, capability_diagnostics)

    driver = await _get_graph_driver()
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            paths = await driver.query_function_subgraph(
                function_id=function_id,
                relationship_types=rel_types,
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
                )
                result_is_empty = False
                if str(query_profile or "").strip().lower() == "strict":
                    graph, dropped_edges = _filter_strict_edges(graph, function_id)
                    capability_diagnostics = dict(capability_diagnostics)
                    capability_diagnostics["strict_edges_dropped"] = dropped_edges
                    if not graph["edges"]:
                        result_is_empty = True
                graph["db"] = candidate
                graph["capability_diagnostics"] = capability_diagnostics
                graph.update(
                    await _attach_semantic_result_fields(
                        candidates, project_id, result_is_empty=result_is_empty
                    )
                )
                if result_is_empty and graph.get("outcome") != "incomplete":
                    graph["reason"] = "no_accepted_strict_edges"
                return graph
        except Exception as exc:
            last_error = exc
            if _is_db_not_found(exc):
                continue
            raise
    if last_error:
        raise last_error
    result = {
        "db": candidates[0] if candidates else None,
        "nodes": [],
        "edges": [],
        "capability_diagnostics": capability_diagnostics,
    }
    # Fail closed: an empty subgraph is only an authoritative "no callers"
    # when the semantic frontier is complete.  Otherwise the outcome is a
    # typed incomplete result with a suggested next semantic scope.
    result.update(
        await _attach_semantic_result_fields(candidates, project_id, result_is_empty=True)
    )
    if result.get("outcome") != "incomplete":
        result["reason"] = "no_subgraph"
    return result


@mcp_server.tool(
    name="find_paths",
    description="Find call paths between two functions. Supports content_mode/include_raw_fields.",
    output_schema=None
)
async def tool_find_paths(
    start_function_id: Any = None,
    end_function_id: Any = None,
    max_depth: int = 8,
    include_possible: bool = False,
    include_fp: bool = False,
    parser_type: Optional[str] = None,
    project_id: Optional[str] = None,
    rel_types: Optional[List[str]] = None,
    relationship_types: Optional[Any] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "db": project_id,
            "start_function_id": start_function_id,
            "end_function_id": end_function_id,
            "max_depth": max_depth,
            "include_possible": include_possible,
            "include_fp": include_fp,
            "rel_types": rel_types,
            "relationship_types": relationship_types,
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
    candidates = _resolve_db_candidates(project_id)
    _require(candidates[0] if candidates else None, "db")
    start_id = str(start_function_id)
    end_id = str(end_function_id)
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    rel_input = payload.get("relationship_types")
    if rel_input is None:
        rel_input = payload.get("rel_types")
    if rel_input is None:
        rel_input = ["CALLS"]
        if include_possible:
            rel_input.append("POSSIBLE_CALLS")
        if include_fp:
            rel_input.append("CALLS_FUNCTION_POINTER")
        if not include_possible and not include_fp:
            rel_input = _get_default_flow_rel_types(parser_type)
    rel_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
        rel_input,
        parser_type,
        candidates,
        explicit=not bool(payload.get("_capability_default_relationships")),
    )
    if not rel_types:
        return _unsupported_relationship_result(parser_type, capability_diagnostics)
    
    driver = await _get_graph_driver()
    for db_candidate in candidates:
        try:
            paths = await driver.find_function_paths(
                start_id=start_id,
                end_id=end_id,
                relationship_types=rel_types,
                max_depth=depth,
                project_id=project_id,
                database=db_candidate
            )
            if paths:
                graph = _paths_to_graph(
                    paths,
                    content_mode=content_mode or "auto",
                    include_raw_fields=include_raw_fields,
                )
                graph["db"] = db_candidate
                graph["capability_diagnostics"] = capability_diagnostics
                return graph
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    raise RuntimeError("No path found in any db.")


@mcp_server.tool(
    name="analyze_proc_data_impact",
    description=(
        "Pro*C call-plus-data impact for a function: traverses the evidence plane "
        "EXECUTES_SQL -> SqlStatement -> DatabaseTable (READS_FROM/WRITES_TO/REFERENCES_TABLE), "
        "host-variable declaration joins, and strict CALLS callers. Preserves join quality, "
        "source-map quality, dynamic-SQL flags, and configuration provenance. Dynamic SQL or an "
        "ambiguous/unresolved function/host join makes the data-impact frontier 'partial', so an "
        "empty table list is returned as outcome='incomplete' — never an authoritative "
        "'no data impact'."
    ),
    output_schema=None
)
async def tool_analyze_proc_data_impact(
    function_id: Any = None,
    project_id: Optional[str] = None,
    include_callers: bool = True,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "function_id": function_id,
            "project_id": project_id,
            "include_callers": include_callers,
        },
    )
    function_id = payload.get("function_id")
    project_id = payload.get("project_id")
    include_callers = bool(payload.get("include_callers", True))
    if function_id is None:
        raise ValueError("function_id is required.")
    candidates = _resolve_db_candidates(project_id)
    _require(candidates[0] if candidates else None, "db")
    function_id = str(function_id)

    query = (
        "MATCH (f:Function {id: $function_id}) "
        "OPTIONAL MATCH (f)-[exec:EXECUTES_SQL]->(s:SqlStatement) "
        "OPTIONAL MATCH (s)-[table_rel:READS_FROM|WRITES_TO|REFERENCES_TABLE]->(t:DatabaseTable) "
        "RETURN f.id AS function_id, f.name AS function_name, "
        "s.id AS statement_id, s.operation AS operation, "
        "exec.join_quality AS join_quality, exec.source_map_quality AS source_map_quality, "
        "exec.is_dynamic_sql AS is_dynamic_sql, exec.config_fingerprint AS config_fingerprint, "
        "type(table_rel) AS table_rel_type, t.id AS table_id, t.name AS table_name"
    )
    params: Dict[str, Any] = {"function_id": function_id}
    rows: List[Dict[str, Any]] = []
    used_db: Optional[str] = None
    for db in candidates:
        try:
            rows = await _run_cypher(query, params, db)
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
        if rows:
            used_db = db
            break
    if used_db is None:
        used_db = candidates[0]

    statements: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        statement_id = row.get("statement_id")
        if not statement_id:
            continue
        statement = statements.setdefault(
            str(statement_id),
            {
                "statement_id": str(statement_id),
                "operation": row.get("operation"),
                "join_quality": row.get("join_quality") or "unresolved",
                "source_map_quality": row.get("source_map_quality"),
                "is_dynamic_sql": bool(row.get("is_dynamic_sql")),
                "config_fingerprint": row.get("config_fingerprint"),
                "tables": [],
            },
        )
        if row.get("table_id"):
            statement["tables"].append(
                {
                    "table_id": row.get("table_id"),
                    "table_name": row.get("table_name"),
                    "relation": row.get("table_rel_type"),
                }
            )
    statement_list = sorted(statements.values(), key=lambda item: item["statement_id"])

    host_query = (
        "MATCH (f:Function {id: $function_id})-[exec:EXECUTES_SQL]->(s:SqlStatement) "
        "MATCH (s)-[:BINDS_PARAMETER|DECLARES_STATEMENT*0..1]->(host:SqlHostVariable) "
        "OPTIONAL MATCH (host)-[decl:RESOLVES_HOST_DECLARATION]->(d) "
        "RETURN host.id AS host_variable_id, host.name AS host_name, "
        "decl.join_quality AS join_quality, "
        "d.id AS declaration_id, labels(d) AS declaration_labels"
    )
    host_rows: List[Dict[str, Any]] = []
    host_query_failed = False
    try:
        host_rows = await _run_cypher(host_query, params, used_db)
    except Exception as exc:
        host_query_failed = True
        logger.warning("Unable to read host declarations from %s: %s", used_db, exc)
    host_variables: List[Dict[str, Any]] = []
    seen_hosts: set = set()
    for row in host_rows:
        host_id = str(row.get("host_variable_id") or "")
        if not host_id or host_id in seen_hosts:
            continue
        seen_hosts.add(host_id)
        host_variables.append(
            {
                "host_variable_id": host_id,
                "host_name": row.get("host_name"),
                "declaration_join_quality": row.get("join_quality") or "unresolved",
                "declaration_id": row.get("declaration_id"),
            }
        )

    callers: List[Dict[str, Any]] = []
    caller_query_failed = False
    if include_callers:
        caller_query = (
            "MATCH (caller:Function)-[c:CALLS]->(f:Function {id: $function_id}) "
            "RETURN caller.id AS caller_id, caller.name AS caller_name, "
            "c.site_id AS site_id, c.resolution_class AS resolution_class, "
            "c.semantic_provider AS semantic_provider"
        )
        try:
            caller_rows = await _run_cypher(caller_query, params, used_db)
            callers = [
                {
                    "caller_id": row.get("caller_id"),
                    "caller_name": row.get("caller_name"),
                    "site_id": row.get("site_id"),
                    "resolution_class": row.get("resolution_class"),
                    "semantic_provider": row.get("semantic_provider"),
                }
                for row in caller_rows
            ]
        except Exception as exc:
            caller_query_failed = True
            logger.warning("Unable to read strict callers from %s: %s", used_db, exc)

    coverage = proc_data_impact_coverage(statement_list)
    # A failed evidence query is a partial frontier, never a silent negative:
    # fail closed so "no callers / no host joins" cannot look authoritative.
    if host_query_failed:
        coverage["status"] = "partial"
        coverage["reasons"] = list(coverage.get("reasons") or []) + [
            "host_declaration_query_failed"
        ]
    if caller_query_failed:
        coverage["status"] = "partial"
        coverage["reasons"] = list(coverage.get("reasons") or []) + [
            "strict_caller_query_failed"
        ]
    result_is_empty = not statement_list and not host_variables
    result: Dict[str, Any] = {
        "db": used_db,
        "function_id": function_id,
        "function_name": rows[0].get("function_name") if rows else None,
        "sql_statements": statement_list,
        "host_variables": host_variables,
        "strict_callers": callers,
        "data_impact_coverage": coverage,
    }
    result.update(await _attach_semantic_result_fields(candidates, project_id, result_is_empty=result_is_empty))
    if coverage.get("status") == "partial" and result.get("outcome") != "incomplete":
        # Dynamic SQL, ambiguous joins, or a failed evidence query make the
        # data-impact frontier partial even when SQL statements were found.
        result["outcome"] = "incomplete"
        result["reason"] = (
            "data-impact frontier is partial ("
            + "; ".join(coverage.get("reasons") or [])
            + "); negative conclusions are not authoritative"
        )
    if result_is_empty and result.get("outcome") == "incomplete":
        result["reason"] = (
            "no SQL evidence joined to this function and the semantic frontier is incomplete; "
            "'no data impact' is not authoritative"
        )
    return result


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
    max_depth: int = 8,
    direction: str = "out",
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
            "db": project_id,
            "max_depth": max_depth,
            "direction": direction,
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
    direction = payload.get("direction", "out")
    include_possible = bool(payload.get("include_possible", False))
    include_fp = bool(payload.get("include_fp", False))
    parser_type = payload.get("parser_type")
    project_id = payload.get("project_id")
    content_mode = payload.get("content_mode")
    include_raw_fields = payload.get("include_raw_fields", False)
    source_modules = _normalize_string_list(source_modules)
    target_modules = _normalize_string_list(target_modules)
    if not source_modules or not target_modules:
        raise ValueError("source_modules and target_modules must be non-empty lists.")
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    depth = _normalize_depth(max_depth, default=8, max_limit=20)
    rel_input = payload.get("relationship_types")
    if rel_input is None:
        rel_input = payload.get("rel_types")
    if rel_input is None:
        rel_input = ["CALLS"]
        if include_possible:
            rel_input.append("POSSIBLE_CALLS")
        if include_fp:
            rel_input.append("CALLS_FUNCTION_POINTER")
        if not include_possible and not include_fp:
            rel_input = _get_default_flow_rel_types(parser_type)
    rel_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
        rel_input,
        parser_type,
        db_candidates,
        explicit=not bool(payload.get("_capability_default_relationships")),
    )
    if not rel_types:
        return _unsupported_relationship_result(parser_type, capability_diagnostics)
    
    driver = await _get_graph_driver()
    for db_candidate in db_candidates:
        try:
            paths = await driver.find_paths_between_modules(
                source_modules=source_modules,
                target_modules=target_modules,
                relationship_types=rel_types,
                max_depth=depth,
                limit=10,
                direction=direction,
                project_id=project_id,
                database=db_candidate
            )
            if paths:
                graph = _paths_to_graph(
                    paths,
                    content_mode=content_mode or "auto",
                    include_raw_fields=include_raw_fields,
                )
                graph["db"] = db_candidate
                graph["capability_diagnostics"] = capability_diagnostics
                return graph
        except Exception as exc:
            if _is_db_not_found(exc):
                continue
            raise
    # Return empty graph if no paths found
    return {
        "db": db_candidates[0] if db_candidates else None,
        "nodes": [],
        "edges": [],
        "capability_diagnostics": capability_diagnostics,
    }


@mcp_server.tool(
    name="listup_symbols_matching_file_path",
    description="List symbols by file path token. Supports content_mode/include_raw_fields. Use node_types=['Function'] to list only functions.",
    output_schema=None
)
async def tool_listup_symbols_matching_file_path(
    modules: Optional[List[str]] = None,
    module: Optional[Any] = None,
    node_types: Optional[List[str]] = None,
    max_depth: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "modules": modules,
            "module": module,
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    
    # Build node type filter
    if node_types_filter:
        types = _normalize_string_list(node_types_filter)
        type_conditions = " OR ".join([f"n:{t}" for t in types])
    else:
        type_conditions = _search_label_predicate(
            "n",
            (),
            fanout=bool(payload.get("_fanout")),
        ).lstrip("(").rstrip(")")
    
    cypher = (
        f"MATCH (n) WHERE ({type_conditions}) "
        "AND any(token IN $modules WHERE "
        "toLower(coalesce(n.file_path, '')) CONTAINS toLower(token) OR "
        "toLower(coalesce(n.path, '')) CONTAINS toLower(token)) "
        "AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized) "
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
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "class_names": class_names,
            "class_name": class_name,
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    cypher = (
        "MATCH (c) "
        "WHERE (c:Class OR c:Type) "
        "AND any(token IN $classes WHERE "
        "toLower(c.name) CONTAINS toLower(token) OR toLower(c.qualified_name) CONTAINS toLower(token)) "
        "AND ($project_id IS NULL OR c.project_id_normalized = $project_id_normalized) "
        "OPTIONAL MATCH (c)-[:DECLARES]->(f:Function) "
        "WHERE ($project_id IS NULL OR f.project_id_normalized = $project_id_normalized) "
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
    limit: int = 200,
    top_k: Optional[int] = None,
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "modules": modules,
            "module": module,
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    cypher = (
        "MATCH (caller:Function)-[:CALLS]->(f:Function) "
        "WHERE any(token IN $modules WHERE toLower(coalesce(f.file_path, '')) CONTAINS toLower(token)) "
        "AND none(token IN $modules WHERE toLower(coalesce(caller.file_path, '')) CONTAINS toLower(token)) "
        "AND (f.kind IS NULL OR f.kind <> 'lambda') "
        "AND ($project_id IS NULL OR f.project_id_normalized = $project_id_normalized) "
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
        "Trace a flow across graph relationships using configurable relation types. "
        "Supports content_mode/include_raw_fields."
    ),
    output_schema=None
)
async def tool_trace_flow(
    start_id: Any = None,
    end_id: Any = None,
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
            "db": project_id,
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
    direction = normalize_graph_direction(payload.get("direction") or "out")
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
    candidates = _resolve_db_candidates(project_id)
    _require(candidates[0] if candidates else None, "db")
    rel_value = payload.get("rel_types")
    if rel_value is None:
        rel_value = payload.get("relationship_types")
    rel_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
        rel_value,
        parser_type,
        candidates,
        explicit=(
            rel_value is not None
            and not bool(payload.get("_capability_default_relationships"))
        ),
    )
    if not rel_types:
        return _unsupported_relationship_result(parser_type, capability_diagnostics)
    depth = _normalize_depth(max_depth, default=6, max_limit=20)
    rel_match = _build_rel_match(rel_types, depth, direction)
    start_id = str(start_id)
    end_id = str(end_id) if end_id is not None else None

    if end_id is not None:
        # FalkorDB rejects Neo4j-style ``MATCH p=shortestPath(...)`` (it only
        # allows shortestPaths in WITH/RETURN), so use a variable-length match
        # ordered by path length.
        query = (
            "MATCH (a {id: $start}) "
            "WHERE ($project_id IS NULL OR a.project_id_normalized = $project_id_normalized) "
            "MATCH (b {id: $end}) "
            "WHERE ($project_id IS NULL OR b.project_id_normalized = $project_id_normalized) "
            f"MATCH p=(a){rel_match}(b) "
            "RETURN p ORDER BY length(p) LIMIT $limit"
        )
        used_db, result = await _run_cypher_first(
            query,
            {"start": start_id, "end": end_id, "project_id": project_id, "limit": int(limit)},
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
                    "capability_diagnostics": capability_diagnostics,
                }
            raise RuntimeError("No path found in any db.")
        paths = [row["p"] for row in result]
    else:
        query = (
            "MATCH (a {id: $start}) "
            "WHERE ($project_id IS NULL OR a.project_id_normalized = $project_id_normalized) "
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
                "capability_diagnostics": capability_diagnostics,
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
    graph["capability_diagnostics"] = capability_diagnostics
    return graph


@mcp_server.tool(
    name="trace_flow_between_module",
    description=(
        "Trace flow paths between functions in two modules using configurable relation types. "
        "Supports content_mode/include_raw_fields."
    ),
    output_schema=None
)
async def tool_trace_flow_between_module(
    source_modules: Optional[List[str]] = None,
    target_modules: Optional[List[str]] = None,
    source_module: Optional[Any] = None,
    target_module: Optional[Any] = None,
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
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    rel_value = payload.get("rel_types")
    if rel_value is None:
        rel_value = payload.get("relationship_types")
    rel_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
        rel_value,
        parser_type,
        db_candidates,
        explicit=(
            rel_value is not None
            and not bool(payload.get("_capability_default_relationships"))
        ),
    )
    if not rel_types:
        return _unsupported_relationship_result(parser_type, capability_diagnostics)
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
        "AND ($project_id IS NULL OR s.project_id_normalized = $project_id_normalized) "
        "AND ($project_id IS NULL OR t.project_id_normalized = $project_id_normalized) "
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
            "AND ($project_id IS NULL OR s.project_id_normalized = $project_id_normalized) "
            "AND ($project_id IS NULL OR t.project_id_normalized = $project_id_normalized) "
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
    graph["capability_diagnostics"] = capability_diagnostics
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
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    framework: Optional[str] = None,
    kinds: Optional[List[str]] = None,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "query": query,
            "limit": limit,
            "top_k": top_k,
            "db": project_id,
            "project_id": project_id,
            "content_mode": content_mode,
            "include_raw_fields": include_raw_fields,
            "framework": framework,
            "kinds": kinds,
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
    framework = str(payload.get("framework") or "").strip().lower() or None
    kinds = _normalize_string_list(payload.get("kinds"))
    parser_capability = capability_for_parser(payload.get("parser_type"))
    framework_capability = capability_for_parser(framework) if framework else None
    if framework and (
        not framework_capability
        or "framework_query" not in framework_capability.features
    ):
        raise ValueError(f"framework '{framework}' is not a registered framework capability")
    capability = framework_capability or parser_capability
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required.")
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    qs = [t.lower().strip() for t in query.split("|") if t.strip()]
    fanout = bool(payload.get("_fanout"))
    profile_labels = searchable_labels(capability.name) if capability else ()
    profile_properties = (
        backend_text_property_union("cplus")
        if fanout and not profile_labels
        else (text_search_properties(capability.name) if capability else ())
    )
    label_predicate = _search_label_predicate("n", profile_labels, fanout=fanout)
    property_names = profile_properties or (
        "name", "qualified_name", "file_path", "path", "raw_value", "resolved_value",
        "caption", "text", "summary",
    )
    property_predicate = " OR ".join(
        f"toLower(coalesce(n.{property_name}, '')) CONTAINS q"
        for property_name in property_names
    )
    fallback_cypher = (
        f"MATCH (n) WHERE {label_predicate} "
        f"AND any(q IN $qs WHERE {property_predicate}) "
        "AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized) "
        "AND ($framework IS NULL OR n.framework = $framework OR n.framework IS NULL) "
        "AND ($kinds IS NULL OR size($kinds) = 0 OR n.kind IN $kinds) "
        f"AND {servlet_active_generation_predicate('n')} "
        "RETURN n LIMIT $limit"
    )
    fulltext_query = " OR ".join(qs)
    node_label_predicate = _search_label_predicate("node", profile_labels, fanout=fanout)
    fulltext_node_predicate = node_label_predicate
    fulltext_cypher = (
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score "
        f"WHERE {fulltext_node_predicate} "
        "AND ($project_id IS NULL OR node.project_id_normalized = $project_id_normalized) "
        "AND ($framework IS NULL OR node.framework = $framework) "
        "AND ($kinds IS NULL OR size($kinds) = 0 OR node.kind IN $kinds) "
        f"AND {servlet_active_generation_predicate('node')} "
        "RETURN node AS n ORDER BY score DESC LIMIT $limit"
    )
    try:
        used_db, results = await _run_cypher_first(
            fulltext_cypher,
            {"index_name": FULLTEXT_SYMBOL_TEXT_INDEX, "query": fulltext_query, "limit": int(limit), "project_id": project_id, "framework": framework, "kinds": kinds},
            db_candidates,
        )
        if framework or not results:
            fallback_db, fallback_results = await _run_cypher_first(
                fallback_cypher,
                {"qs": qs, "limit": int(limit), "project_id": project_id, "framework": framework, "kinds": kinds},
                db_candidates,
            )
            used_db = used_db or fallback_db
            by_id = {
                str(_record_node(row.get("n"), "auto", False).get("id") or index): row
                for index, row in enumerate(results)
            }
            for index, row in enumerate(fallback_results, start=len(by_id)):
                key = str(_record_node(row.get("n"), "auto", False).get("id") or index)
                by_id.setdefault(key, row)
            results = list(by_id.values())[: int(limit)]
    except Exception:
        used_db, results = await _run_cypher_first(
            fallback_cypher,
            {"qs": qs, "limit": int(limit), "project_id": project_id, "framework": framework, "kinds": kinds},
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
    project_id: Optional[str] = None,
    content_mode: Optional[str] = None,
    include_raw_fields: bool = False,
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "parser_type": parser_type,
            "query": query,
            "limit": limit,
            "top_k": top_k,
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required.")
    qs = [t.strip() for t in query.split("|") if t.strip()]
    fallback_cypher = "MATCH (n) WHERE any(q IN $qs WHERE n.code CONTAINS q) AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized) RETURN n LIMIT $limit"
    fulltext_query = " OR ".join(qs)
    fulltext_cypher = (
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score "
        "WHERE ($project_id IS NULL OR node.project_id_normalized = $project_id_normalized) "
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
            "db": project_id,
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
    db_candidates = _resolve_db_candidates(project_id)
    _require(db_candidates[0] if db_candidates else None, "db")
    node_id = str(node_id)
    cypher = (
        "MATCH (n) WHERE n.id = $id "
        "AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized) "
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


_CPLUS_TOOL_NAMES: frozenset = frozenset({
    "search_functions", "search_by_code",
    "get_symbol", "get_node_details", "query_subgraph",
    "find_paths", "find_path_between_module",
    "listup_symbols_matching_file_path", "listup_class_matching_path",
    "list_up_entrypoint", "trace_flow", "trace_flow_between_module",
    "semantic_search", "get_ipc_message", "list_possible_calls",
    "annotate_node", "list_databases", "list_qdrant_collections",
    "list_parsers", "list_mcp_functions", "find_screen_workflows",
    "analyze_proc_data_impact",
})
_cplus_catalog = build_catalog(_CPLUS_TOOL_NAMES)
_CPLUS_PARAMETER_GUIDELINES: Dict[str, Any] = {
    "always_call_first": "list_mcp_functions",
    "rules": [
        "Use exact parameter names documented in tool inputs.",
        "Provide all required params; do not rely on implicit defaults for required fields.",
        "When a tool accepts list inputs, prefer arrays over comma-delimited strings.",
        "If a call returns error.missing_required_parameters or error.invalid_parameters, fix params and retry.",
    ],
}
_MCP_FUNCTIONS_JSON: str = json.dumps(
    {
        "total_count": len(_cplus_catalog),
        "parameter_guidelines": _CPLUS_PARAMETER_GUIDELINES,
        "functions": _cplus_catalog,
    },
    ensure_ascii=False,
)


def _extract_call_payload(arguments: Any) -> Dict[str, Any]:
    if not isinstance(arguments, dict):
        return {}
    merged = dict(arguments)
    payload = arguments.get("payload")
    if isinstance(payload, dict):
        merged.update(payload)
    return merged


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _install_global_tool_error_wrapper() -> None:
    if getattr(mcp_server, "_safe_tool_wrapper_installed", False):
        return

    inputs_by_tool: Dict[str, List[Dict[str, Any]]] = {
        str(item.get("name")): list(item.get("inputs") or [])
        for item in _cplus_catalog
        if item.get("name")
    }
    original_call_tool_mcp = mcp_server._call_tool_mcp

    async def _safe_call_tool_mcp(key: str, arguments: Dict[str, Any]) -> Any:
        try:
            return await original_call_tool_mcp(key, arguments)
        except Exception as exc:
            provided = _extract_call_payload(arguments)
            input_entries = inputs_by_tool.get(key, [])
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
            missing = [name for name in required if _is_missing_value(provided.get(name))]
            error_type = "tool_execution_error"
            if missing:
                error_type = "missing_required_parameters"
            elif isinstance(exc, (ValueError, TypeError)):
                error_type = "invalid_parameters"

            example = None
            for item in _cplus_catalog:
                if item.get("name") == key:
                    example = item.get("example")
                    break

            payload: Dict[str, Any] = {
                "ok": False,
                "error": {
                    "type": error_type,
                    "tool": key,
                    "backend": "cplus",
                    "message": str(exc),
                    "missing_required_params": missing,
                    "required_params": required,
                    "accepted_params": accepted,
                    "received_params": sorted([name for name in provided.keys() if not _is_missing_value(provided.get(name))]),
                    "example": example,
                    "next_step": "Call list_mcp_functions and retry with exact parameter names.",
                },
            }
            return mcp_types.CallToolResult(
                isError=True,
                structuredContent=payload,
                content=[mcp_types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            )

    mcp_server._call_tool_mcp = _safe_call_tool_mcp
    setattr(mcp_server, "_safe_tool_wrapper_installed", True)

@mcp_server.tool(
    name="list_mcp_functions",
    description="List all available MCP tools with descriptions, parameters, and use cases. Call this FIRST to discover what tools are available before making other calls.",
    output_schema=None
)
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


@mcp_server.tool(
    name="find_screen_workflows",
    description=(
        "Discover ranked screen-only NAVIGATE workflows for a React/TS project. "
        "Input either a pair (node_a + node_b) or a single node_a with a "
        "direction (inbound|outbound|bidirectional). Returns dedup'd and "
        "confidence-ranked paths. Requires project_id."
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
    parser_type: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = _merge_payload(
        payload,
        {
            "project_id": project_id,
            "node_a": node_a,
            "node_b": node_b or None,
            "direction": direction,
            "max_hops": max_hops,
            "max_paths": max_paths,
            "include_entry_function": include_entry_function,
            "include_api_calls": include_api_calls,
            "db": project_id,
            "parser_type": parser_type,
        },
    )
    from services.workflow_service import run_find_screen_workflows

    parser_type = payload.get("parser_type")
    rel_value = payload.get("relationship_types")
    if rel_value is None:
        rel_value = payload.get("rel_types")
    relationship_types, capability_diagnostics = await _resolve_rel_types_with_diagnostics(
        rel_value,
        parser_type,
        _resolve_db_candidates(payload.get("db")),
        explicit=(
            rel_value is not None
            and not bool(payload.get("_capability_default_relationships"))
        ),
    )
    if not relationship_types:
        return _unsupported_relationship_result(parser_type, capability_diagnostics)
    payload["relationship_types"] = relationship_types
    if not payload.get("db") and not payload.get("database"):
        payload["db"] = _resolve_db_candidates(None)[0]

    driver = await _get_graph_driver()

    async def _provider() -> Any:
        return driver

    result = await run_find_screen_workflows(_provider, payload)
    if isinstance(result, dict):
        result["capability_diagnostics"] = capability_diagnostics
    return result


_install_global_tool_error_wrapper()


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

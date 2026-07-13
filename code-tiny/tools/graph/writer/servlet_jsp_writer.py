from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tools.graph.core.base import GraphDriver


SERVLET_JSP_NODE_LABELS = frozenset({
    "ServletJspModule", "WebDescriptor", "Servlet", "Filter", "Listener", "ServletMapping",
    "FilterMapping", "ApiEndpoint", "JSPView", "JspExpression", "JspTag", "StateSlot",
    "LifecycleEvent", "ErrorPage", "WelcomePage", "SecurityConstraint", "Authority", "WebTarget",
    "WebConfiguration",
})
SERVLET_JSP_RELATIONSHIP_TYPES = frozenset({
    "SEMANTIC_OF", "DECLARES", "HANDLES", "PASSES_THROUGH", "FORWARDS_TO", "REDIRECTS_TO",
    "INCLUDES", "SUBMITS_TO", "LINKS_TO", "READS", "WRITES", "INITIALIZES", "HANDLES_LIFECYCLE",
    "PROTECTS", "REQUIRES_AUTHORITY", "RESOLVES_TO", "CONFIGURES", "MAPS_TO",
})
EXTERNAL_ENDPOINT_LABELS = frozenset({"Class", "Function", "File"})

_COMMON_NODE_PROPERTIES = frozenset({
    "id", "semantic_id", "symbol_id", "generation_id", "name", "kind", "project_id", "project_name",
    "module_id", "language", "framework", "file_path", "start_line", "end_line", "start_column",
    "end_column", "confidence", "extraction_method", "resolution_status", "raw_value", "resolved_value",
    "source_symbol_id", "parser_version", "module_path", "evidence", "artifact_kind", "component_class",
    "component_name", "servlet_class", "servlet_name", "filter_class", "filter_name", "listener_class",
    "path", "raw_url_pattern", "mapping_kind", "http_method", "handler_names", "handler_symbol_ids",
    "controller_class", "declaration_sources", "scope", "key", "dynamic", "event_kind", "target_kind",
    "target", "uri", "prefix", "tag_name", "expression", "variables", "property_paths", "functions",
    "attributes", "order", "order_index", "order_status", "descriptor_order", "dispatcher_types",
    "async_supported", "metadata_complete", "namespace", "version", "doctype", "config_kind", "config_key",
    "config_value", "error_code", "exception_type", "location", "role", "methods", "method_omissions", "resource_collections",
    "transport_guarantee", "auth_method", "realm_name", "form_login_page", "form_error_page", "url_patterns",
    "servlet_names", "init_params", "load_on_startup", "jsp_file", "multipart", "source_files", "provenance",
    "coverage_status", "incomplete", "truncated", "correlation_status", "reason", "body", "method",
})
_RELATIONSHIP_PROPERTY_KEYS = frozenset({
    "confidence", "resolution_status", "source_file", "start_line", "end_line", "reason", "occurrence_key",
    "order_index", "order_status", "mapping_kind", "dispatcher_types", "async_supported", "declaration_source",
    "descriptor_order", "dispatch_type", "correlation_status", "provenance", "raw_value", "resolved_value",
    "methods", "method_omissions", "resource_collection", "resource_collection_index",
})


class ServletJspFactWriter:
    def __init__(
        self,
        driver: GraphDriver,
        database: Optional[str] = None,
        batch_size: int = 1000,
        verbose: bool = False,
    ) -> None:
        self.driver = driver
        self.database = database
        self.batch_size = max(1, int(batch_size))
        self.verbose = verbose

    async def stage_generation(
        self,
        *,
        project_id: str,
        module_id: str,
        generation_id: str,
        node_rows: Sequence[Dict[str, Any]],
        relationship_rows: Sequence[Dict[str, Any]],
    ) -> Dict[str, int]:
        _validate_generation_args(project_id, module_id, generation_id)
        nodes_written = await self.write_fact_nodes(list(node_rows), generation_id)
        relationships_written = await self.write_relationships(list(relationship_rows), generation_id)
        if nodes_written != len(node_rows) or relationships_written != len(relationship_rows):
            raise RuntimeError(
                f"Staged generation count mismatch: nodes={nodes_written}/{len(node_rows)} "
                f"relationships={relationships_written}/{len(relationship_rows)}"
            )
        return {"nodes": nodes_written, "relationships": relationships_written}

    async def write_fact_nodes(self, rows: List[Dict[str, Any]], generation_id: str) -> int:
        if not rows:
            return 0
        _validate_node_rows(rows, generation_id)
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            by_label: Dict[str, List[Dict[str, Any]]] = {}
            for row in batch:
                by_label.setdefault(str(row["kind"]), []).append(row)
            for label, label_rows in sorted(by_label.items()):
                records, _, _ = await self.driver.execute_query(
                    _node_query(label),
                    {"rows": label_rows, "updated_at": _utc_now_iso()},
                    self.database,
                )
                total += _result_count(records, len(label_rows))
        return total

    async def write_relationships(self, rows: List[Dict[str, Any]], generation_id: str) -> int:
        if not rows:
            return 0
        _validate_relationship_rows(rows, generation_id)
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
            for row in batch:
                key = (str(row["from_label"]), str(row["type"]), str(row["to_label"]))
                grouped.setdefault(key, []).append(row)
            for (from_label, rel_type, to_label), rel_rows in sorted(grouped.items()):
                records, _, _ = await self.driver.execute_query(
                    _relationship_query(from_label, rel_type, to_label),
                    {"rows": rel_rows},
                    self.database,
                )
                total += _result_count(records, len(rel_rows))
        return total

    async def promote_generation(
        self,
        *,
        project_id: str,
        module_id: str,
        generation_id: str,
        snapshot_checksum: str,
        coverage_status: str,
    ) -> int:
        _validate_generation_args(project_id, module_id, generation_id)
        records, _, _ = await self.driver.execute_query(
            _PROMOTE_GENERATION_QUERY,
            {
                "state_id": f"servlet_jsp_state::{project_id}::{module_id}",
                "project_id": project_id,
                "module_id": module_id,
                "generation_id": generation_id,
                "snapshot_checksum": snapshot_checksum,
                "coverage_status": coverage_status,
                "updated_at": _utc_now_iso(),
            },
            self.database,
        )
        return _result_count(records, 1)

    async def cleanup_inactive_generations(self, project_id: str, module_id: str) -> Dict[str, int]:
        records, _, _ = await self.driver.execute_query(
            _CLEANUP_INACTIVE_QUERY,
            {"project_id": project_id, "module_id": module_id},
            self.database,
        )
        return {"deleted_nodes": int((records or [{}])[0].get("deleted_nodes", 0))}

    async def get_active_generation(self, project_id: str, module_id: str) -> Dict[str, Any]:
        records, _, _ = await self.driver.execute_query(
            _ACTIVE_GENERATION_QUERY,
            {"project_id": project_id, "module_id": module_id},
            self.database,
        )
        return dict((records or [{}])[0])

    async def list_active_modules(self, project_id: str) -> List[str]:
        records, _, _ = await self.driver.execute_query(
            _LIST_ACTIVE_MODULES_QUERY,
            {"project_id": project_id},
            self.database,
        )
        return sorted({str(row.get("module_id")) for row in records or () if row.get("module_id")})


def _validate_generation_args(project_id: str, module_id: str, generation_id: str) -> None:
    if not project_id or not module_id or not generation_id:
        raise ValueError("project_id, module_id, and generation_id are required")


def _validate_node_rows(rows: Iterable[Dict[str, Any]], generation_id: str) -> None:
    for row in rows:
        label = str(row.get("kind") or "")
        if label not in SERVLET_JSP_NODE_LABELS:
            raise ValueError(f"Unsupported Servlet/JSP node label: {label}")
        required = ("id", "semantic_id", "symbol_id", "project_id", "module_id", "framework", "generation_id")
        if any(not row.get(key) for key in required):
            raise ValueError(f"Servlet/JSP fact row is missing required ownership fields: {label}")
        if row.get("framework") != "servlet_jsp" or row.get("generation_id") != generation_id:
            raise ValueError("Servlet/JSP fact ownership or generation mismatch")
        unknown = set(row) - _COMMON_NODE_PROPERTIES
        if unknown:
            raise ValueError(f"Unsupported Servlet/JSP node properties for {label}: {sorted(unknown)}")
        invalid = [key for key, value in row.items() if not _is_graph_property(value)]
        if invalid:
            raise ValueError(f"Unsupported Servlet/JSP graph property values for {label}: {sorted(invalid)}")


def _validate_relationship_rows(rows: Iterable[Dict[str, Any]], generation_id: str) -> None:
    allowed_labels = SERVLET_JSP_NODE_LABELS | EXTERNAL_ENDPOINT_LABELS
    for row in rows:
        rel_type = str(row.get("type") or "")
        if rel_type not in SERVLET_JSP_RELATIONSHIP_TYPES:
            raise ValueError(f"Unsupported Servlet/JSP relationship type: {rel_type}")
        if row.get("from_label") not in allowed_labels or row.get("to_label") not in allowed_labels:
            raise ValueError("Servlet/JSP relationship endpoint labels must be allowlisted")
        required = ("id", "semantic_id", "from_id", "to_id", "project_id", "module_id", "generation_id")
        if any(not row.get(key) for key in required):
            raise ValueError("Servlet/JSP relationship row is missing identity/ownership fields")
        if row.get("framework") != "servlet_jsp" or row.get("generation_id") != generation_id:
            raise ValueError("Servlet/JSP relationship ownership or generation mismatch")
        properties = row.get("properties") or {}
        if not isinstance(properties, dict):
            raise ValueError("Servlet/JSP relationship properties must be a mapping")
        unknown = set(properties) - _RELATIONSHIP_PROPERTY_KEYS
        if unknown:
            raise ValueError(f"Unsupported Servlet/JSP relationship properties: {sorted(unknown)}")
        invalid = [key for key, value in properties.items() if not _is_graph_property(value)]
        if invalid:
            raise ValueError(f"Unsupported Servlet/JSP relationship property values: {sorted(invalid)}")


def _is_graph_property(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    return isinstance(value, list) and all(item is None or isinstance(item, (str, int, float, bool)) for item in value)


def _node_query(label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row,
        node.updated_at = $updated_at
    RETURN count(node) AS count
    """


def _relationship_query(from_label: str, rel_type: str, to_label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MATCH (source:{from_label} {{id: row.from_id, project_id: row.project_id}})
    MATCH (target:{to_label} {{id: row.to_id, project_id: row.project_id}})
    MERGE (source)-[rel:{rel_type} {{id: row.id}}]->(target)
    SET rel += coalesce(row.properties, {{}}),
        rel.semantic_id = row.semantic_id,
        rel.project_id = row.project_id,
        rel.module_id = row.module_id,
        rel.framework = 'servlet_jsp',
        rel.generation_id = row.generation_id,
        rel.confidence = row.confidence,
        rel.resolution_status = row.resolution_status,
        rel.source_file = row.source_file,
        rel.start_line = row.start_line,
        rel.end_line = row.end_line,
        rel.reason = row.reason
    RETURN count(rel) AS count
    """


_PROMOTE_GENERATION_QUERY = """
MERGE (state:ServletJspAnalysisState {id: $state_id})
SET state.project_id = $project_id,
    state.module_id = $module_id,
    state.framework = 'servlet_jsp',
    state.active_generation = $generation_id,
    state.snapshot_checksum = $snapshot_checksum,
    state.coverage_status = $coverage_status,
    state.updated_at = $updated_at
RETURN count(state) AS count
"""

_CLEANUP_INACTIVE_QUERY = """
MATCH (state:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id})
MATCH (node)
WHERE node.project_id = $project_id
  AND node.module_id = $module_id
  AND node.framework = 'servlet_jsp'
  AND node.generation_id <> state.active_generation
WITH collect(DISTINCT node) AS nodes
UNWIND nodes AS node
DETACH DELETE node
RETURN count(node) AS deleted_nodes
"""

_ACTIVE_GENERATION_QUERY = """
MATCH (state:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id})
RETURN state.active_generation AS active_generation,
       state.snapshot_checksum AS snapshot_checksum,
       state.coverage_status AS coverage_status
"""

_LIST_ACTIVE_MODULES_QUERY = """
MATCH (state:ServletJspAnalysisState {project_id: $project_id})
WHERE state.framework = 'servlet_jsp'
RETURN state.module_id AS module_id
ORDER BY module_id
"""


def _result_count(records: Any, default: int) -> int:
    if not records:
        return default
    return int(records[0].get("count", default))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


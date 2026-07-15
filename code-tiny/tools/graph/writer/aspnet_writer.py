from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tools.common.aspnet.models import ASPNET_NODE_LABELS, ASPNET_RELATIONSHIP_TYPES
from tools.graph.core.base import GraphDriver


EXTERNAL_LABELS = frozenset({"Class", "Function", "File", "Type"})


class AspNetFactWriter:
    """Provider-neutral staged writer for both ASP.NET overlays."""

    def __init__(
        self, driver: GraphDriver, database: Optional[str] = None,
        batch_size: int = 1000, verbose: bool = False,
    ) -> None:
        self.driver = driver
        self.database = database
        self.batch_size = max(1, int(batch_size))
        self.verbose = verbose

    async def stage_generation(
        self, *, project_id: str, module_id: str, framework: str, generation_id: str,
        node_rows: Sequence[Dict[str, Any]], relationship_rows: Sequence[Dict[str, Any]],
    ) -> Dict[str, int]:
        _validate_scope(project_id, module_id, framework, generation_id)
        nodes = await self.write_nodes(node_rows, framework, generation_id)
        relationships = await self.write_relationships(relationship_rows, framework, generation_id)
        if nodes != len(node_rows) or relationships != len(relationship_rows):
            raise RuntimeError(
                f"ASP.NET staged count mismatch nodes={nodes}/{len(node_rows)} "
                f"relationships={relationships}/{len(relationship_rows)}"
            )
        return {"nodes": nodes, "relationships": relationships}

    async def write_nodes(
        self, rows: Sequence[Dict[str, Any]], framework: str, generation_id: str,
    ) -> int:
        _validate_nodes(rows, framework, generation_id)
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows[offset : offset + self.batch_size]:
                grouped.setdefault(str(row["kind"]), []).append(dict(row))
            for label, batch in sorted(grouped.items()):
                records, _, _ = await self.driver.execute_query(
                    _node_query(label), {"rows": batch, "updated_at": _utc_now()}, self.database,
                )
                total += _count(records, len(batch))
        return total

    async def write_relationships(
        self, rows: Sequence[Dict[str, Any]], framework: str, generation_id: str,
    ) -> int:
        _validate_relationships(rows, framework, generation_id)
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            grouped: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
            for row in rows[offset : offset + self.batch_size]:
                key = (str(row["from_label"]), str(row["type"]), str(row["to_label"]))
                grouped.setdefault(key, []).append(dict(row))
            for (from_label, rel_type, to_label), batch in sorted(grouped.items()):
                records, _, _ = await self.driver.execute_query(
                    _relationship_query(from_label, rel_type, to_label), {"rows": batch}, self.database,
                )
                total += _count(records, len(batch))
        return total

    async def promote_generation(
        self, *, project_id: str, module_id: str, framework: str,
        generation_id: str, snapshot_checksum: str, coverage_status: str,
    ) -> int:
        _validate_scope(project_id, module_id, framework, generation_id)
        records, _, _ = await self.driver.execute_query(
            _PROMOTE_QUERY,
            {
                "id": f"aspnet_state::{framework}::{project_id}::{module_id}",
                "project_id": project_id,
                "module_id": module_id,
                "framework": framework,
                "generation_id": generation_id,
                "snapshot_checksum": snapshot_checksum,
                "coverage_status": coverage_status,
                "updated_at": _utc_now(),
            },
            self.database,
        )
        return _count(records, 1)

    async def active_state(
        self, project_id: str, module_id: str, framework: str,
    ) -> Dict[str, str]:
        records, _, _ = await self.driver.execute_query(
            _ACTIVE_STATE_QUERY,
            {"project_id": project_id, "module_id": module_id, "framework": framework},
            self.database,
        )
        if not records:
            return {}
        row = records[0]
        return {
            "active_generation": str(row.get("active_generation") or ""),
            "coverage_status": str(row.get("coverage_status") or ""),
        }

    async def cleanup_inactive_generations(
        self, project_id: str, module_id: str, framework: str,
    ) -> Dict[str, int]:
        records, _, _ = await self.driver.execute_query(
            _CLEANUP_QUERY,
            {"project_id": project_id, "module_id": module_id, "framework": framework},
            self.database,
        )
        return {"deleted_nodes": int((records or [{}])[0].get("deleted_nodes", 0))}


def _validate_scope(project_id: str, module_id: str, framework: str, generation_id: str) -> None:
    if not all((project_id, module_id, framework, generation_id)):
        raise ValueError("project_id, module_id, framework, and generation_id are required")
    if framework not in {"aspnet_core", "aspnet_framework"}:
        raise ValueError(f"unsupported ASP.NET framework: {framework}")


def _validate_nodes(rows: Iterable[Dict[str, Any]], framework: str, generation_id: str) -> None:
    for row in rows:
        if row.get("kind") not in ASPNET_NODE_LABELS:
            raise ValueError(f"unsupported ASP.NET node label: {row.get('kind')}")
        if row.get("framework") != framework or row.get("generation_id") != generation_id:
            raise ValueError("ASP.NET node ownership/generation mismatch")
        if any(not row.get(key) for key in ("id", "semantic_id", "project_id", "module_id")):
            raise ValueError("ASP.NET node is missing identity/scope")


def _validate_relationships(rows: Iterable[Dict[str, Any]], framework: str, generation_id: str) -> None:
    labels = ASPNET_NODE_LABELS | EXTERNAL_LABELS
    for row in rows:
        if row.get("type") not in ASPNET_RELATIONSHIP_TYPES:
            raise ValueError(f"unsupported ASP.NET relationship type: {row.get('type')}")
        if row.get("from_label") not in labels or row.get("to_label") not in labels:
            raise ValueError("ASP.NET relationship labels are not allowlisted")
        if row.get("framework") != framework or row.get("generation_id") != generation_id:
            raise ValueError("ASP.NET relationship ownership/generation mismatch")
        if any(not row.get(key) for key in ("id", "semantic_id", "from_id", "to_id", "project_id", "module_id")):
            raise ValueError("ASP.NET relationship is missing identity/scope")


def _node_query(label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row, node.updated_at = $updated_at
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
        rel.framework = row.framework,
        rel.generation_id = row.generation_id,
        rel.confidence = row.confidence,
        rel.resolution_status = row.resolution_status,
        rel.source_file = row.source_file,
        rel.start_line = row.start_line,
        rel.end_line = row.end_line,
        rel.reason = row.reason
    RETURN count(rel) AS count
    """


_PROMOTE_QUERY = """
MERGE (state:AspNetAnalysisState {id: $id})
SET state.project_id = $project_id,
    state.module_id = $module_id,
    state.framework = $framework,
    state.active_generation = $generation_id,
    state.snapshot_checksum = $snapshot_checksum,
    state.coverage_status = $coverage_status,
    state.updated_at = $updated_at
RETURN count(state) AS count
"""

_ACTIVE_STATE_QUERY = """
MATCH (state:AspNetAnalysisState {project_id: $project_id, module_id: $module_id, framework: $framework})
RETURN state.active_generation AS active_generation, state.coverage_status AS coverage_status
LIMIT 1
"""

_CLEANUP_QUERY = """
MATCH (state:AspNetAnalysisState {project_id: $project_id, module_id: $module_id, framework: $framework})
MATCH (node)
WHERE node.project_id = $project_id
  AND node.module_id = $module_id
  AND node.framework = $framework
  AND node.generation_id <> state.active_generation
WITH collect(DISTINCT node) AS nodes
UNWIND nodes AS node
DETACH DELETE node
RETURN count(node) AS deleted_nodes
"""


def _count(records: Any, default: int) -> int:
    return int((records or [{"count": default}])[0].get("count", default))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

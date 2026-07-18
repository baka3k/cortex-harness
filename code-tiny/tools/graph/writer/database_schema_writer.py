from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, Tuple

from tools.graph.core.base import GraphDriver


_LABELS = frozenset({"Table", "View", "Procedure"})
_RELATIONSHIPS = frozenset({"READS_FROM", "WRITES_TO", "REFERENCES_TABLE"})


class DatabaseSchemaWriter:
    def __init__(self, driver: GraphDriver, database: Optional[str] = None, batch_size: int = 500) -> None:
        self.driver = driver
        self.database = database
        self.batch_size = max(1, int(batch_size))

    async def write_all(
        self,
        *,
        node_rows: Sequence[Dict[str, Any]],
        relationship_rows: Sequence[Dict[str, Any]],
    ) -> Dict[str, int]:
        _validate_nodes(node_rows)
        _validate_relationships(relationship_rows)
        nodes = 0
        for label in sorted(_LABELS):
            selected = [dict(row) for row in node_rows if row.get("label") == label]
            nodes += await self._write_batches(_node_query(label), selected)
        relationships = 0
        grouped: Dict[Tuple[str, str, str], list] = {}
        for row in relationship_rows:
            key = (str(row["source_label"]), str(row["type"]), str(row["target_label"]))
            grouped.setdefault(key, []).append(dict(row))
        for (source_label, rel_type, target_label), rows in sorted(grouped.items()):
            relationships += await self._write_batches(
                _relationship_query(source_label, rel_type, target_label), rows,
            )
        return {"nodes": nodes, "relationships": relationships}

    async def _write_batches(self, query: str, rows: Sequence[Dict[str, Any]]) -> int:
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = list(rows[offset : offset + self.batch_size])
            records, _, _ = await self.driver.execute_query(query, {"rows": batch}, self.database)
            total += _count(records, len(batch))
        return total

    async def delete_paths(self, project_id: str, paths: Sequence[str]) -> int:
        records, _, _ = await self.driver.execute_query(
            _DELETE_PATHS_QUERY,
            {"project_id": project_id, "paths": list(paths)},
            self.database,
        )
        return _count(records, 0)


def _validate_nodes(rows: Sequence[Dict[str, Any]]) -> None:
    for row in rows:
        if row.get("label") not in _LABELS:
            raise ValueError(f"unsupported database node label: {row.get('label')}")
        if any(not row.get(key) for key in ("id", "project_id", "name")):
            raise ValueError("database node row is missing identity or ownership")


def _validate_relationships(rows: Sequence[Dict[str, Any]]) -> None:
    for row in rows:
        if row.get("type") not in _RELATIONSHIPS:
            raise ValueError(f"unsupported database relationship: {row.get('type')}")
        if row.get("source_label") not in _LABELS or row.get("target_label") not in _LABELS:
            raise ValueError("database relationship labels are not allowlisted")


def _count(records: Sequence[Dict[str, Any]], fallback: int) -> int:
    if records and records[0].get("count") is not None:
        return int(records[0]["count"])
    return fallback


def _node_query(label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row
    RETURN count(node) AS count
    """


def _relationship_query(source_label: str, rel_type: str, target_label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MATCH (source:{source_label} {{id: row.source_id, project_id: row.project_id}})
    MATCH (target:{target_label} {{id: row.target_id, project_id: row.project_id}})
    MERGE (source)-[rel:{rel_type} {{id: row.id}}]->(target)
    SET rel.project_id = row.project_id,
        rel.dialect = row.dialect,
        rel.file_path = row.file_path,
        rel.start_line = row.start_line,
        rel.confidence = row.confidence
    RETURN count(rel) AS count
    """


_DELETE_PATHS_QUERY = """
MATCH (node {project_id: $project_id})
WHERE (node:Table OR node:View OR node:Procedure) AND node.file_path IN $paths
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"""


__all__ = ["DatabaseSchemaWriter"]

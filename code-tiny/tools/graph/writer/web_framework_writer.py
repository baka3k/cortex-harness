from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from tools.graph.core.base import GraphDriver


_HANDLER_LABELS = frozenset({"Function", "Class"})


class WebFrameworkWriter:
    """Write normalized web endpoints and link them to canonical symbols."""

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
        nodes = await self._write_nodes(node_rows)
        relationships = await self._write_relationships(relationship_rows)
        return {"nodes": nodes, "relationships": relationships}

    async def _write_nodes(self, rows: Sequence[Dict[str, Any]]) -> int:
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = [dict(row) for row in rows[offset : offset + self.batch_size]]
            records, _, _ = await self.driver.execute_query(_NODE_QUERY, {"rows": batch}, self.database)
            total += _count(records, len(batch))
        return total

    async def _write_relationships(self, rows: Sequence[Dict[str, Any]]) -> int:
        total = 0
        for label in sorted(_HANDLER_LABELS):
            selected = [dict(row) for row in rows if row.get("handler_label") == label]
            for offset in range(0, len(selected), self.batch_size):
                batch = selected[offset : offset + self.batch_size]
                records, _, _ = await self.driver.execute_query(
                    _relationship_query(label), {"rows": batch}, self.database,
                )
                total += _count(records, len(batch))
        return total

    async def delete_paths(self, project_id: str, framework: str, paths: Sequence[str]) -> int:
        records, _, _ = await self.driver.execute_query(
            _DELETE_PATHS_QUERY,
            {"project_id": project_id, "framework": framework, "paths": list(paths)},
            self.database,
        )
        return _count(records, 0)


def _validate_nodes(rows: Sequence[Dict[str, Any]]) -> None:
    for row in rows:
        if any(not row.get(key) for key in ("id", "project_id", "framework", "path", "http_method")):
            raise ValueError("web endpoint row is missing identity, ownership, or route")


def _validate_relationships(rows: Sequence[Dict[str, Any]]) -> None:
    for row in rows:
        if row.get("type") != "HANDLES" or row.get("handler_label") not in _HANDLER_LABELS:
            raise ValueError("unsupported web framework relationship")
        if any(not row.get(key) for key in ("id", "endpoint_id", "project_id", "handler_name")):
            raise ValueError("web relationship row is missing identity or handler")


def _count(records: Sequence[Dict[str, Any]], fallback: int) -> int:
    if records and records[0].get("count") is not None:
        return int(records[0]["count"])
    return fallback


_NODE_QUERY = """
UNWIND $rows AS row
MERGE (node:ApiEndpoint {id: row.id})
SET node += row
RETURN count(node) AS count
"""


def _relationship_query(handler_label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MATCH (endpoint:ApiEndpoint {{id: row.endpoint_id, project_id: row.project_id}})
    MATCH (handler:{handler_label} {{project_id: row.project_id}})
    WHERE handler.name = row.handler_name
      AND (row.handler_file = '' OR replace(handler.file_path, '\\\\', '/') = row.handler_file)
      AND (row.handler_scope = '' OR coalesce(handler.scope_name, handler.class_name, '') = row.handler_scope
           OR coalesce(handler.qualified_name, '') STARTS WITH row.handler_scope + '::')
    MERGE (endpoint)-[rel:HANDLES]->(handler)
    SET rel.id = row.id,
        rel.framework = row.framework,
        rel.project_id = row.project_id,
        rel.confidence = row.confidence,
        rel.resolution_status = row.resolution_status
    MERGE (endpoint)-[:SEMANTIC_OF]->(handler)
    RETURN count(handler) AS count
    """


_DELETE_PATHS_QUERY = """
MATCH (node:ApiEndpoint {project_id: $project_id, framework: $framework})
WHERE node.file_path IN $paths
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"""


__all__ = ["WebFrameworkWriter"]

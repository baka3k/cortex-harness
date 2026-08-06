from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from tools.graph.core.base import GraphDriver


SPRING_NODE_LABELS = frozenset({
    "SpringModule",
    "SpringApplication",
    "SpringConfiguration",
    "SpringBean",
    "JpaEntity",
    "TransactionBoundary",
    "MessageDestination",
    "ScheduledTask",
    "AsyncBoundary",
    "ApplicationEvent",
    "SecurityFilterChain",
    "SecurityRule",
    "Authority",
    "Aspect",
    "Advice",
    "Pointcut",
    "ValidationConstraint",
    "CacheRegion",
    "CacheOperation",
    "ApiEndpoint",
    "Controller",
    "Service",
    "DataRepository",
    "Database",
    "Middleware",
    "MessageEndpoint",
})

SPRING_RELATIONSHIP_TYPES = frozenset({
    "CONTAINS",
    "SEMANTIC_OF",
    "BOOTS_WITH",
    "IMPORTS_CONFIGURATION",
    "DEFINES_BEAN",
    "PRODUCES_BEAN",
    "INJECTS",
    "POSSIBLE_INJECTION",
    "DEPENDS_ON",
    "HANDLES",
    "USES",
    "CALLS",
    "QUERIES",
    "MANAGES_ENTITY",
    "RELATES_TO_ENTITY",
    "DERIVES_QUERY",
    "DECLARES_QUERY",
    "IMPLEMENTS_REPOSITORY",
    "APPLIES_TO",
    "CONSUMES_FROM",
    "PUBLISHES_TO",
    "HANDLED_BY",
    "RUNS",
    "PUBLISHES_EVENT",
    "LISTENS_TO",
    "EXECUTES_ASYNC",
    "HAS_SECURITY_CHAIN",
    "HAS_RULE",
    "PROTECTS",
    "REQUIRES_AUTHORITY",
    "IMPLIES",
    "DECLARES_POINTCUT",
    "APPLIES_ADVICE",
    "MATCHES_POINTCUT",
    "CONSTRAINED_BY",
    "VALIDATES_CASCADE",
    "READS_CACHE",
    "WRITES_CACHE",
    "EVICTS_CACHE",
})


class SpringFactWriter:
    def __init__(
        self,
        driver: GraphDriver,
        database: Optional[str] = None,
        batch_size: int = 1000,
        verbose: bool = False,
    ) -> None:
        self.driver = driver
        self.database = database
        self.batch_size = batch_size
        self.verbose = verbose

    async def write_fact_nodes(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            _validate_rows(batch)
            by_label: Dict[str, List[Dict[str, Any]]] = {}
            for row in batch:
                by_label.setdefault(str(row["kind"]), []).append(row)
            for label, label_rows in by_label.items():
                records, _, _ = await self.driver.execute_query(
                    _node_query(label),
                    {"rows": label_rows, "updated_at": _utc_now_iso()},
                    self.database,
                )
                total += int(records[0].get("count", len(label_rows))) if records else len(label_rows)
            if self.verbose:
                print(f"[spring-writer] facts {offset + len(batch)}/{len(rows)}")
        return total

    async def write_relationships(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            _validate_relationship_rows(batch)
            # Relationship type cannot be parameterized in Cypher, so group by
            # validated type before interpolation.
            by_type: Dict[str, List[Dict[str, Any]]] = {}
            for row in batch:
                by_type.setdefault(str(row["type"]), []).append(row)
            for rel_type, rel_rows in by_type.items():
                query = _relationship_query(rel_type)
                records, _, _ = await self.driver.execute_query(query, {"rows": rel_rows}, self.database)
                total += int(records[0].get("count", len(rel_rows))) if records else len(rel_rows)
        return total

    async def cleanup_files(self, project_id: str, file_paths: List[str]) -> Dict[str, int]:
        paths = _normalize_files(file_paths)
        if not paths:
            return {"deleted_nodes": 0}
        query = """
        MATCH (n)
        WHERE n.project_id = $project_id
          AND n.framework = 'spring'
          AND (
            coalesce(n.file_path, '') IN $paths
            OR coalesce(n.path, '') IN $paths
          )
        WITH collect(DISTINCT n) AS nodes
        UNWIND nodes AS n
        WITH DISTINCT n
        DETACH DELETE n
        RETURN count(n) AS deleted_nodes
        """
        records, _, _ = await self.driver.execute_query(
            query,
            {"project_id": project_id, "paths": paths},
            self.database,
        )
        deleted = int((records or [{}])[0].get("deleted_nodes", 0))
        if self.verbose:
            print(f"[cleanup][graph] deleted_nodes={deleted} deleted_unknown_functions=0")
        return {"deleted_nodes": deleted}


def _validate_rows(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        label = str(row.get("kind") or "")
        if label not in SPRING_NODE_LABELS:
            raise ValueError(f"Unsupported Spring node label: {label}")
        if not row.get("id") or not row.get("symbol_id"):
            raise ValueError("Spring fact rows require both id and symbol_id")


def _validate_relationship_rows(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        rel_type = str(row.get("type") or "")
        if rel_type not in SPRING_RELATIONSHIP_TYPES:
            raise ValueError(f"Unsupported Spring relationship type: {rel_type}")
        if not row.get("from_id") or not row.get("to_id"):
            raise ValueError("Spring relationship rows require from_id and to_id")
        if not row.get("project_id"):
            raise ValueError("Spring relationship rows require project_id")
        row.setdefault("properties", {})


def _node_query(label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row,
        node.symbol_id = row.symbol_id,
        node.framework = 'spring',
        node.updated_at = $updated_at
    RETURN count(node) AS count
    """


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relationship_query(rel_type: str) -> str:
    return f"""
    UNWIND $rows AS row
    MATCH (a {{id: row.from_id}})
    MATCH (b {{id: row.to_id}})
    WHERE a.project_id = row.project_id
      AND b.project_id = row.project_id
    MERGE (a)-[r:{rel_type}]->(b)
    SET r += coalesce(row.properties, {{}}),
        r.confidence = coalesce(row.confidence, row.properties.confidence, 1.0),
        r.resolution_status = coalesce(row.resolution_status, row.properties.resolution_status, 'resolved'),
        r.source_file = coalesce(row.source_file, row.properties.source_file, '')
    RETURN count(r) AS count
    """


def _normalize_files(paths: List[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for path in paths:
        normalized = (path or "").replace("\\", "/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


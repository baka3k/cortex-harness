from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from tools.graph.core.base import GraphDriver


MYBATIS_NODE_LABELS = frozenset({
    "MyBatisModule",
    "MyBatisArtifact",
    "MyBatisMapper",
    "MyBatisMapperMethod",
    "MyBatisParameter",
    "MyBatisJavaProperty",
    "MyBatisXmlDocument",
    "MyBatisStatement",
    "MyBatisSqlFragment",
    "MyBatisResultMap",
    "MyBatisResultMapping",
    "MyBatisInclude",
    "MyBatisDynamicNode",
    "MyBatisConfig",
    "MyBatisSqlStatement",
    "DatabaseTable",
    "DatabaseColumn",
    "MyBatisSqlJoin",
    "MyBatisSqlParameter",
    "MyBatisSqlProvider",
    "MyBatisSpringBridge",
    "MyBatisExtension",
    "MyBatisCache",
})
MYBATIS_RELATIONSHIP_NODE_LABELS = MYBATIS_NODE_LABELS | frozenset({"Class", "Function"})

MYBATIS_RELATIONSHIP_TYPES = frozenset({
    "SEMANTIC_OF",
    "DECLARES_METHOD",
    "DECLARES_STATEMENT",
    "BINDS_STATEMENT",
    "READS_FROM",
    "WRITES_TO",
    "REFERENCES_TABLE",
    "REFERENCES_COLUMN",
    "JOINS_WITH",
    "DEPENDS_ON_PARAMETER",
    "USES_RESULT_MAP",
    "HAS_RESULT_MAPPING",
    "MAPS_PROPERTY",
    "MAPS_COLUMN",
    "NESTED_SELECT",
    "HAS_ASSOCIATION",
    "HAS_COLLECTION",
    "EXTENDS_RESULT_MAP",
})


class MyBatisFactWriter:
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
        self._schema_ready = False

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        ensure = getattr(self.driver, "ensure_schema", None)
        if callable(ensure):
            await ensure(database=self.database)
        self._schema_ready = True

    async def write_fact_nodes(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        _validate_rows(rows)
        await self._ensure_schema()
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
                print(f"[mybatis-writer] facts {offset + len(batch)}/{len(rows)}")
        return total

    async def write_relationships(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        _validate_relationship_rows(rows)
        await self._ensure_schema()
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            _validate_relationship_rows(batch)
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
                count = int(records[0].get("count", 0)) if records else 0
                if count != len(rel_rows):
                    raise RuntimeError(
                        "MyBatis relationship count mismatch "
                        f"{from_label}-[{rel_type}]->{to_label}: {count}/{len(rel_rows)}"
                    )
                total += count
        return total

    async def cleanup_files(self, project_id: str, file_paths: List[str]) -> Dict[str, int]:
        paths = _normalize_files(file_paths)
        if not paths:
            return {"deleted_nodes": 0}
        await self._ensure_schema()
        query = """
        MATCH (n)
        WHERE n.project_id = $project_id
          AND n.framework = 'mybatis'
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
        if label not in MYBATIS_NODE_LABELS:
            raise ValueError(f"Unsupported MyBatis node label: {label}")
        if not row.get("id") or not row.get("symbol_id"):
            raise ValueError("MyBatis fact rows require both id and symbol_id")
        if row.get("framework") not in {None, "mybatis"}:
            raise ValueError("MyBatis fact rows must use framework='mybatis'")


def _validate_relationship_rows(rows: List[Dict[str, Any]]) -> None:
    for row in rows:
        rel_type = str(row.get("type") or "")
        if rel_type not in MYBATIS_RELATIONSHIP_TYPES:
            raise ValueError(f"Unsupported MyBatis relationship type: {rel_type}")
        from_label = str(row.get("from_label") or "")
        to_label = str(row.get("to_label") or "")
        if from_label not in MYBATIS_RELATIONSHIP_NODE_LABELS:
            raise ValueError(f"Unsupported MyBatis relationship source label: {from_label}")
        if to_label not in MYBATIS_RELATIONSHIP_NODE_LABELS:
            raise ValueError(f"Unsupported MyBatis relationship target label: {to_label}")
        if not row.get("from_id") or not row.get("to_id"):
            raise ValueError("MyBatis relationship rows require from_id and to_id")
        if not row.get("project_id"):
            raise ValueError("MyBatis relationship rows require project_id")


def _node_query(label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row,
        node.symbol_id = row.symbol_id,
        node.framework = 'mybatis',
        node.updated_at = $updated_at
    RETURN count(node) AS count
    """


def _relationship_query(from_label: str, rel_type: str, to_label: str) -> str:
    return f"""
    UNWIND $rows AS row
    MATCH (a:{from_label} {{id: row.from_id}})
    MATCH (b:{to_label} {{id: row.to_id}})
    WHERE a.project_id = row.project_id
      AND b.project_id = row.project_id
    MERGE (a)-[r:{rel_type}]->(b)
    SET r += row,
        r.framework = 'mybatis'
    RETURN count(r) AS count
    """


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

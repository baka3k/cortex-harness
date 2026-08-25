"""Provider-neutral Cypher behavior shared by concrete graph drivers.

Transport, connection lifecycle, schema discovery, and provider-specific
query dialects belong to concrete drivers.  This base contains only the
portable operations implemented in terms of ``execute_query``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from tools.graph.core.base import GraphDriver
from tools.graph.core.provider_contract import normalize_graph_direction
from tools.graph.writer.query_contract import RelationshipGroup


logger = logging.getLogger(__name__)


class CypherGraphDriver(GraphDriver):
    """Partial graph driver whose implemented operations use portable Cypher."""

    async def batch_write_nodes(
        self,
        nodes: List[Dict[str, Any]],
        label: str,
        database: Optional[str] = None,
    ) -> int:
        if not nodes:
            return 0

        query = f"""
        UNWIND $nodes AS node
        CREATE (n:{label})
        SET n = node
        RETURN count(n) as count
        """
        records, _, _ = await self.execute_query(query, {"nodes": nodes}, database)
        return records[0]["count"] if records else 0

    async def batch_write_edges(
        self,
        edges: List[Dict[str, Any]],
        relationship_type: str,
        source_label: str,
        target_label: str,
        database: Optional[str] = None,
    ) -> int:
        if not edges:
            return 0

        from tools.graph.schema.manifest import validate_cypher_identifier

        rel_type = validate_cypher_identifier(
            relationship_type, kind="relationship type"
        )
        source_node_label = validate_cypher_identifier(
            source_label, kind="source label"
        )
        target_node_label = validate_cypher_identifier(
            target_label, kind="target label"
        )
        RelationshipGroup(source_node_label, target_node_label, rel_type)
        query = f"""
        UNWIND $edges AS edge
        MATCH (source:{source_node_label} {{id: edge.source_id}})
        MATCH (target:{target_node_label} {{id: edge.target_id}})
        MERGE (source)-[r:{rel_type}]->(target)
        SET r = edge.properties
        RETURN count(r) as count
        """
        records, _, _ = await self.execute_query(query, {"edges": edges}, database)
        return records[0]["count"] if records else 0

    async def verify_connection(self) -> bool:
        try:
            records, _, _ = await self.execute_query("RETURN 1 as test")
            return len(records) > 0 and records[0]["test"] == 1
        except Exception as exc:
            logger.error("Connection verification failed: %s", exc)
            return False

    async def get_node_count(
        self,
        label: Optional[str] = None,
        database: Optional[str] = None,
    ) -> int:
        query = (
            f"MATCH (n:{label}) RETURN count(n) as count"
            if label
            else "MATCH (n) RETURN count(n) as count"
        )
        records, _, _ = await self.execute_query(query, database=database)
        return records[0]["count"] if records else 0

    async def get_edge_count(
        self,
        relationship_type: Optional[str] = None,
        database: Optional[str] = None,
    ) -> int:
        query = (
            f"MATCH ()-[r:{relationship_type}]->() RETURN count(r) as count"
            if relationship_type
            else "MATCH ()-[r]->() RETURN count(r) as count"
        )
        records, _, _ = await self.execute_query(query, database=database)
        return records[0]["count"] if records else 0

    async def find_function_paths(
        self,
        start_id: str,
        end_id: str,
        relationship_types: List[str],
        max_depth: int = 8,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
        limit: int = 10,
    ) -> List[Any]:
        """Find directed function paths using syntax supported by both providers."""

        rel_pattern = f"[:{'|'.join(relationship_types)}*..{max_depth}]"
        cypher = f"""
        MATCH (a:Function) WHERE a.id = $start
          AND ($project_id IS NULL OR a.project_id_normalized = $project_id_normalized)
        MATCH (b:Function) WHERE b.id = $end
          AND ($project_id IS NULL OR b.project_id_normalized = $project_id_normalized)
        AND a.id <> b.id
        MATCH p=(a)-{rel_pattern}->(b)
        RETURN p ORDER BY length(p) LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {
                "start": start_id,
                "end": end_id,
                "project_id": project_id,
                "limit": int(limit),
            },
            database,
        )
        return [record.get("p") for record in records if record.get("p")]

    async def query_function_subgraph(
        self,
        function_id: str,
        relationship_types: List[str],
        direction: str = "both",
        max_depth: int = 2,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Any]:
        direction = normalize_graph_direction(direction)
        rel_pattern = f"[:{'|'.join(relationship_types)}*1..{max_depth}]"
        if direction == "in":
            pattern = f"<-{rel_pattern}-"
        elif direction == "out":
            pattern = f"-{rel_pattern}->"
        else:
            pattern = f"-{rel_pattern}-"

        cypher = f"""
        MATCH (f:Function) WHERE f.id = $id
          AND ($project_id IS NULL OR f.project_id_normalized = $project_id_normalized)
        MATCH p=(f){pattern}(n)
        RETURN p
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"id": function_id, "project_id": project_id},
            database,
        )
        return [record.get("p") for record in records if record.get("p")]

    async def find_paths_between_modules(
        self,
        source_modules: List[str],
        target_modules: List[str],
        relationship_types: List[str],
        max_depth: int = 8,
        limit: int = 10,
        direction: str = "out",
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Any]:
        paths = await self._find_module_paths_directed(
            source_modules,
            target_modules,
            relationship_types,
            max_depth,
            limit,
            direction,
            project_id,
            database,
        )
        if not paths and direction.lower() not in {"both", "any", "undirected"}:
            paths = await self._find_module_paths_directed(
                source_modules,
                target_modules,
                relationship_types,
                max_depth,
                limit,
                "both",
                project_id,
                database,
            )
        return paths

    async def _find_module_paths_directed(
        self,
        source_modules: List[str],
        target_modules: List[str],
        relationship_types: List[str],
        max_depth: int,
        limit: int,
        direction: str,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Any]:
        rel_types_str = "|".join(relationship_types)
        normalized_direction = normalize_graph_direction(direction)
        if normalized_direction == "in":
            rel_pattern = f"<-[:{rel_types_str}*..{max_depth}]-"
        elif normalized_direction == "both":
            rel_pattern = f"-[:{rel_types_str}*..{max_depth}]-"
        else:
            rel_pattern = f"-[:{rel_types_str}*..{max_depth}]->"

        cypher = f"""
        WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets
        MATCH (s:Function)<-[:CONTAINS]-(sf:File)
        MATCH (t:Function)<-[:CONTAINS]-(tf:File)
        WHERE any(token IN sources WHERE
            toLower(coalesce(s.file_path, '')) CONTAINS token OR
            toLower(coalesce(sf.path, '')) CONTAINS token OR
            toLower(coalesce(sf.file_path, '')) CONTAINS token)
          AND ($project_id IS NULL OR s.project_id_normalized = $project_id_normalized)
        AND any(token IN targets WHERE
            toLower(coalesce(t.file_path, '')) CONTAINS token OR
            toLower(coalesce(tf.path, '')) CONTAINS token OR
            toLower(coalesce(tf.file_path, '')) CONTAINS token)
          AND ($project_id IS NULL OR t.project_id_normalized = $project_id_normalized)
        AND s.id <> t.id
        MATCH p=(s){rel_pattern}(t)
        RETURN p ORDER BY length(p)
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {
                "sources": source_modules,
                "targets": target_modules,
                "limit": limit,
                "project_id": project_id,
            },
            database,
        )
        return [record.get("p") for record in records if record.get("p")]

    async def list_possible_calls(
        self,
        limit: int = 200,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        cypher = """
        MATCH (a:Function)-[r:POSSIBLE_CALLS]->(b:Function)
        WHERE ($project_id IS NULL OR a.project_id_normalized = $project_id_normalized)
        AND ($project_id IS NULL OR b.project_id_normalized = $project_id_normalized)
        RETURN a, b, r
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"limit": limit, "project_id": project_id},
            database,
        )
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        seen_ids = set()
        for record in records:
            a_node = record.get("a")
            b_node = record.get("b")
            rel = record.get("r")
            if a_node:
                a_id = a_node.get("id")
                if a_id and a_id not in seen_ids:
                    nodes.append(a_node)
                    seen_ids.add(a_id)
            if b_node:
                b_id = b_node.get("id")
                if b_id and b_id not in seen_ids:
                    nodes.append(b_node)
                    seen_ids.add(b_id)
            if rel:
                edges.append(rel)
        return nodes, edges

    async def list_symbols_by_file_path(
        self,
        file_paths: List[str],
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cypher = """
        WITH [t IN $tokens | toLower(t)] AS tokens
        MATCH (f:Function)<-[:CONTAINS]-(file:File)
        WHERE any(token IN tokens WHERE
            toLower(coalesce(f.file_path, '')) CONTAINS token OR
            toLower(coalesce(file.path, '')) CONTAINS token OR
            toLower(coalesce(file.file_path, '')) CONTAINS token)
          AND ($project_id IS NULL OR f.project_id_normalized = $project_id_normalized)
        RETURN DISTINCT f
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"tokens": file_paths, "project_id": project_id},
            database,
        )
        return [record.get("f") for record in records if record.get("f")]

    async def list_functions_by_class(
        self,
        class_names: List[str],
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cypher = """
        WITH [t IN $tokens | toLower(t)] AS tokens
        MATCH (c:Class)
        WHERE any(token IN tokens WHERE
            toLower(coalesce(c.name, '')) CONTAINS token OR
            toLower(coalesce(c.qualified_name, '')) CONTAINS token)
          AND ($project_id IS NULL OR c.project_id_normalized = $project_id_normalized)
        MATCH (c)-[:CONTAINS]->(f:Function)
        WHERE ($project_id IS NULL OR f.project_id_normalized = $project_id_normalized)
        RETURN DISTINCT f
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"tokens": class_names, "project_id": project_id},
            database,
        )
        return [record.get("f") for record in records if record.get("f")]

    async def list_functions_by_file(
        self,
        file_path: str,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cypher = """
        MATCH (f:Function)<-[:CONTAINS]-(file:File)
        WHERE toLower(coalesce(f.file_path, '')) CONTAINS toLower($token)
           OR toLower(coalesce(file.path, '')) CONTAINS toLower($token)
           OR toLower(coalesce(file.file_path, '')) CONTAINS toLower($token)
          AND ($project_id IS NULL OR f.project_id_normalized = $project_id_normalized)
        RETURN DISTINCT f
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"token": file_path, "project_id": project_id},
            database,
        )
        return [record.get("f") for record in records if record.get("f")]

    async def list_entrypoints(
        self,
        modules: List[str],
        relationship_types: List[str],
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rel_pattern = "|".join(relationship_types)
        cypher = f"""
        WITH [t IN $modules | toLower(t)] AS modules
        MATCH (internalFile:File)-[:CONTAINS]->(internalFn:Function)
        WHERE any(token IN modules WHERE
            toLower(coalesce(internalFn.file_path, '')) CONTAINS token OR
            toLower(coalesce(internalFile.path, '')) CONTAINS token OR
            toLower(coalesce(internalFile.file_path, '')) CONTAINS token)
          AND ($project_id IS NULL OR internalFn.project_id_normalized = $project_id_normalized)
        WITH collect(internalFn.id) AS internalIds, modules
        MATCH (externalFile:File)-[:CONTAINS]->(externalFn:Function)
        WHERE NOT any(token IN modules WHERE
            toLower(coalesce(externalFn.file_path, '')) CONTAINS token OR
            toLower(coalesce(externalFile.path, '')) CONTAINS token OR
            toLower(coalesce(externalFile.file_path, '')) CONTAINS token)
          AND ($project_id IS NULL OR externalFn.project_id_normalized = $project_id_normalized)
        MATCH (externalFn)-[:{rel_pattern}]->(entryFn:Function)
        WHERE entryFn.id IN internalIds
          AND ($project_id IS NULL OR entryFn.project_id_normalized = $project_id_normalized)
        RETURN DISTINCT entryFn
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"modules": modules, "project_id": project_id},
            database,
        )
        return [record.get("entryFn") for record in records if record.get("entryFn")]


__all__ = ["CypherGraphDriver"]

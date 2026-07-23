"""
FalkorDB implementation of the graph driver interface.

This driver keeps the public GraphDriver contract aligned with the existing
Neo4j driver: query execution returns ``(records, keys, summary)`` where each
record is a dictionary keyed by returned column name.
"""

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from tools.graph.core.base import GraphProvider
from tools.graph.driver.neo4j_driver import Neo4jDriver
from tools.common.project_scope import prepare_project_scope_parameters


logger = logging.getLogger(__name__)


def _cypher_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _prepare_falkordb_query(
    query: str,
    parameters: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    params = prepare_project_scope_parameters(query, parameters)
    if "datetime()" not in query:
        return query, params

    param_name = "__falkordb_now"
    while param_name in params:
        param_name = f"_{param_name}"

    return query.replace("datetime()", f"${param_name}"), {
        **params,
        param_name: _utc_timestamp(),
    }


def _result_key(header_item: Any) -> str:
    if isinstance(header_item, (list, tuple)) and header_item:
        header_item = next(
            (item for item in header_item if isinstance(item, (str, bytes))),
            header_item[0],
        )
    if isinstance(header_item, bytes):
        return header_item.decode("utf-8")
    return str(header_item)


def _as_properties(value: Any) -> Dict[str, Any]:
    properties = getattr(value, "properties", None)
    if isinstance(properties, Mapping):
        return dict(properties)
    return {}


def _normalize_falkordb_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalize_falkordb_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_falkordb_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_falkordb_value(item) for item in value)

    if hasattr(value, "properties") and hasattr(value, "labels"):
        return _as_properties(value)

    if hasattr(value, "properties") and hasattr(value, "relation"):
        edge = _as_properties(value)
        edge.setdefault("_type", getattr(value, "relation", None))
        edge.setdefault("_start_id", getattr(value, "src_node", None))
        edge.setdefault("_end_id", getattr(value, "dest_node", None))
        return edge

    if callable(getattr(value, "nodes", None)) and callable(getattr(value, "edges", None)):
        return {
            "nodes": [_normalize_falkordb_value(node) for node in value.nodes()],
            "edges": [_normalize_falkordb_value(edge) for edge in value.edges()],
        }

    return value


class FalkorDBDriver(Neo4jDriver):
    """
    FalkorDB graph driver.

    The class inherits Neo4jDriver's high-level Cypher methods where FalkorDB
    supports the same query shape, and overrides provider-specific connection,
    schema, discovery, full-text, and ID lookup behavior.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        *,
        graph: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        ssl: bool = False,
        **kwargs: Any,
    ):
        try:
            from falkordb import FalkorDB
        except ImportError as exc:
            raise ImportError(
                "FalkorDB provider requires the 'falkordb' package. "
                "Install dependencies from requirements.txt or pyproject.toml."
            ) from exc

        self._uri = uri
        self._user = user
        self._password = password
        self._database = graph or database or "neo4j"

        client_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if user:
            client_kwargs["username"] = user
        if password:
            client_kwargs["password"] = password

        url = uri or client_kwargs.pop("url", None)
        if url and "://" in url:
            parsed = urlparse(url)
            if parsed.scheme in {"falkor", "falkors", "redis", "rediss", "unix"}:
                self._client = FalkorDB.from_url(url, **client_kwargs)
            else:
                raise ValueError(
                    "FalkorDB URI must use falkor://, falkors://, redis://, "
                    f"rediss://, or unix://; got {parsed.scheme!r}"
                )
        else:
            if url and ":" in url and host is None:
                parsed_host, parsed_port = url.rsplit(":", 1)
                host = parsed_host or host
                if parsed_port:
                    port = int(parsed_port)
            self._client = FalkorDB(
                host=host or "localhost",
                port=port or 6379,
                ssl=ssl,
                **client_kwargs,
            )

        self._graph = self._client.select_graph(self._database)

    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.FALKORDB

    @property
    def driver(self) -> Any:
        return self._client

    @property
    def database(self) -> str:
        return self._database

    @property
    def graph(self) -> Any:
        return self._graph

    def session(self, **kwargs: Any) -> None:
        raise NotImplementedError(
            "FalkorDBDriver does not expose Neo4j-style sessions; use execute_query instead."
        )

    def close(self) -> None:
        if self._client:
            self._client.close()
            logger.info("FalkorDB connection closed")

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        return self.execute_query_sync(query, parameters, database)

    def execute_query_sync(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        graph = self._graph_for(database)
        query, params = _prepare_falkordb_query(query, parameters)
        result = graph.query(query, params=params)
        keys = [_result_key(item) for item in result.header]
        records = [
            {
                key: _normalize_falkordb_value(row[index])
                for index, key in enumerate(keys)
            }
            for row in result.result_set
        ]
        return records, keys, result

    def _graph_for(self, database: Optional[str]) -> Any:
        graph_name = database or self._database
        if graph_name == self._database:
            return self._graph
        return self._client.select_graph(graph_name)

    async def create_indexes(
        self,
        indexes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> None:
        graph = self._graph_for(database)
        for idx in indexes:
            label = idx["label"]
            prop = idx["property"]
            props = prop if isinstance(prop, list) else [prop]
            idx_type = idx.get("type", "range")
            try:
                if idx_type == "fulltext":
                    graph.create_node_fulltext_index(label, *props)
                else:
                    graph.create_node_range_index(label, *props)
                logger.info("Created FalkorDB %s index on %s(%s)", idx_type, label, ", ".join(props))
            except Exception as exc:
                if "already indexed" in str(exc).lower():
                    logger.debug(
                        "FalkorDB %s index already exists on %s(%s)",
                        idx_type,
                        label,
                        ", ".join(props),
                    )
                else:
                    logger.warning(
                        "Failed to create FalkorDB %s index on %s(%s): %s",
                        idx_type,
                        label,
                        ", ".join(props),
                        exc,
                    )

    async def list_databases(self) -> List[str]:
        try:
            names = self._client.list_graphs()
            normalized_names = []
            for name in names:
                if isinstance(name, bytes):
                    try:
                        name = name.decode("utf-8")
                    except UnicodeDecodeError:
                        logger.warning("Ignoring non-UTF-8 FalkorDB graph name")
                        continue
                if isinstance(name, str):
                    normalized_names.append(name)
            return normalized_names or [self._database]
        except Exception as exc:
            logger.warning("Failed to list FalkorDB graphs: %s", exc)
            return [self._database]

    async def list_relationship_types(self, database: Optional[str] = None) -> List[str]:
        records, _, _ = await self.execute_query(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS rel_type",
            database=database,
        )
        rel_types: List[str] = []
        for record in records:
            rel_type = record.get("rel_type")
            if isinstance(rel_type, str):
                rel_upper = rel_type.upper()
                if rel_upper not in rel_types:
                    rel_types.append(rel_upper)
        return rel_types

    async def find_node_by_id(
        self,
        node_id: str,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cypher = """
        MATCH (n)
        WHERE n.id = $id
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT 1
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"id": node_id, "project_id": project_id},
            database,
        )
        node = records[0].get("n") if records else None
        if node and node.get("framework") == "servlet_jsp":
            active_records, _, _ = await self.execute_query(
                "MATCH (s:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id}) "
                "WHERE s.active_generation = $generation_id RETURN s.id AS id LIMIT 1",
                {"project_id": node.get("project_id"), "module_id": node.get("module_id"), "generation_id": node.get("generation_id")},
                database,
            )
            if not active_records:
                return None
        return node

    async def find_nodes_by_ids(
        self,
        node_ids: List[str],
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not node_ids:
            return []
        cypher = """
        MATCH (n)
        WHERE n.id IN $ids
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"ids": node_ids, "project_id": project_id},
            database,
        )
        nodes = [record.get("n") for record in records if record.get("n")]
        servlet_nodes = [n for n in nodes if n.get("framework") == "servlet_jsp"]
        if servlet_nodes:
            active_records, _, _ = await self.execute_query(
                "UNWIND $rows AS row "
                "MATCH (s:ServletJspAnalysisState {project_id: row.project_id, module_id: row.module_id}) "
                "WHERE s.active_generation = row.generation_id RETURN row.id AS id",
                {"rows": servlet_nodes},
                database,
            )
            active_ids = {str(row.get("id")) for row in active_records if row.get("id")}
            nodes = [n for n in nodes if n.get("framework") != "servlet_jsp" or str(n.get("id")) in active_ids]
        return nodes

    async def search_functions(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            return await self._fulltext_node_search("Function", query, limit, project_id, database)
        except Exception as exc:
            logger.debug("FalkorDB fulltext search_functions fallback to CONTAINS: %s", exc)

        cypher = """
        MATCH (n:Function)
        WHERE (
            toLower(n.name) CONTAINS toLower($query)
            OR toLower(coalesce(n.qualified_name, '')) CONTAINS toLower($query)
        )
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database,
        )
        return [record.get("n") for record in records if record.get("n")]

    async def search_by_code(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            return await self._fulltext_node_search("Function", query, limit, project_id, database)
        except Exception as exc:
            logger.debug("FalkorDB fulltext search_by_code fallback to CONTAINS: %s", exc)

        cypher = """
        MATCH (n)
        WHERE (
            toLower(coalesce(n.code, '')) CONTAINS toLower($query)
            OR toLower(coalesce(n.comment, '')) CONTAINS toLower($query)
            OR toLower(coalesce(n.summary, '')) CONTAINS toLower($query)
        )
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database,
        )
        return [record.get("n") for record in records if record.get("n")]

    async def _fulltext_node_search(
        self,
        label: str,
        query: str,
        limit: int,
        project_id: Optional[str],
        database: Optional[str],
    ) -> List[Dict[str, Any]]:
        cypher = f"""
        CALL db.idx.fulltext.queryNodes({_cypher_string(label)}, $query) YIELD node, score
        WHERE ($project_id IS NULL OR node.project_id_normalized = $project_id_normalized)
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database,
        )
        return [record.get("n") for record in records if record.get("n")]

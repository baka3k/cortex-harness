"""
Neo4j Implementation of Graph Driver

Concrete implementation of the GraphDriver abstraction for Neo4j.
"""

from __future__ import annotations

import base64
import os
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import logging

from tools.graph.core.base import GraphProvider
from tools.graph.core.cypher_driver import CypherGraphDriver
from tools.graph.core.provider_contract import (
    normalize_graph_direction as normalize_graph_direction,
)
from tools.common.project_scope import (
    matches_project_scope,
    prepare_project_scope_parameters,
)


if TYPE_CHECKING:
    from neo4j import Driver, Session


_FERNET_TOKEN_RE = re.compile(r'^gAAAAA')


def _maybe_decrypt_neo4j_password(password: str) -> str:
    """If *password* is a Fernet-encrypted token, decrypt it using
    HYPER_PACK_ENCRYPTION_PASSWORD (falls back to the compiled-in default key).
    Returns the original value unchanged if decryption is unavailable or fails.
    """
    if not _FERNET_TOKEN_RE.match(password):
        return password
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        return password
    enc_pw = os.environ.get("HYPER_PACK_ENCRYPTION_PASSWORD", "my-secret-encryption-key-2026")
    try:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"static_salt_2026",
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(enc_pw.encode("utf-8")))
        return Fernet(key).decrypt(password.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        logger.warning(
            "[neo4j_driver] Could not decrypt NEO4J_PASS (%s); "
            "using value as-is (wrong HYPER_PACK_ENCRYPTION_PASSWORD?)",
            exc,
        )
        return password


logger = logging.getLogger(__name__)


_FAST_ID_LOOKUP_LABELS: Tuple[str, ...] = (
    "Function",
    "File",
    "Class",
    "Namespace",
    "Type",
    "Property",
    "Event",
    "Interface",
    "Enum",
    "Constant",
    "Variable",
    "UnknownFunction",
    "ParseRun",
    "Document",
    "Entity",
)

_FALLBACK_ID_LOOKUP_LABELS: Tuple[str, ...] = (
    "Type",
    "Package",
    "Field",
    "Alias",
    "Template",
    "FunctionType",
    "Project",
    "Repository",
    "Message",
    "MessageEndpoint",
    "InfraNode",
    "Workflow",
    "Paragraph",
    "Chunk",
    "Slide",
    "AndroidManifest",
    "AndroidComponent",
    "AndroidResource",
    "GradleModule",
    "AndroidIntentAction",
    "AndroidAnnotation",
    "SqlStatement",
    "SqlDirective",
    "SqlCursor",
    "SqlHostVariable",
    "DatabaseTable",
)


def _build_id_lookup_query(for_multiple: bool, labels: Tuple[str, ...]) -> str:
    branches: List[str] = []
    for label in labels:
        if for_multiple:
            branches.append(f"MATCH (n:{label}) WHERE n.id IN $ids RETURN n")
        else:
            branches.append(f"MATCH (n:{label} {{id: $id}}) RETURN n")
    union_query = "\nUNION ALL\n".join(branches)
    tail = "RETURN DISTINCT n" if for_multiple else "RETURN n LIMIT 1"
    return f"CALL () {{\n{union_query}\n}}\n{tail}"


_FIND_NODE_BY_ID_QUERY = _build_id_lookup_query(for_multiple=False, labels=_FAST_ID_LOOKUP_LABELS)
_FIND_NODES_BY_IDS_QUERY = _build_id_lookup_query(for_multiple=True, labels=_FAST_ID_LOOKUP_LABELS)
_FALLBACK_FIND_NODE_BY_ID_QUERY = _build_id_lookup_query(for_multiple=False, labels=_FALLBACK_ID_LOOKUP_LABELS)
_FALLBACK_FIND_NODES_BY_IDS_QUERY = _build_id_lookup_query(for_multiple=True, labels=_FALLBACK_ID_LOOKUP_LABELS)
_FULLTEXT_SYMBOL_TEXT_INDEX = "mcp_symbol_text_ft_v2"
_FULLTEXT_SYMBOL_CODE_INDEX = "mcp_symbol_code_ft_v2"

class Neo4jDriver(CypherGraphDriver):
    """
    Neo4j implementation of the GraphDriver interface
    """
    
    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: Optional[str] = None,
    ):
        """
        Initialize Neo4j driver
        
        Args:
            uri: Neo4j connection URI (e.g., bolt://localhost:7687)
            user: Username
            password: Password
            database: Optional database name (defaults to 'neo4j')
        """
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise ImportError(
                "Neo4j support is optional. Install cortex-harness[neo4j] to use GraphProvider.NEO4J."
            ) from exc
        self._uri = uri
        self._user = user
        self._password = _maybe_decrypt_neo4j_password(password)
        self._database = database or "neo4j"
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, self._password))
        
    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.NEO4J
    
    @property
    def driver(self) -> Driver:
        """Access to underlying Neo4j driver (for compatibility)"""
        return self._driver
    
    @property
    def database(self) -> str:
        """Get current database name"""
        return self._database
    
    def session(self, **kwargs):
        """
        Open a Neo4j session on the underlying driver.

        Delegates to the underlying ``neo4j.Driver.session()`` so that
        analyzers can use ``code_writer.driver.session(database=...)``
        without having to reach through to the private ``_driver`` attribute.
        """
        return self._driver.session(**kwargs)

    def close(self) -> None:
        """Close the Neo4j driver connection (synchronous)."""
        if self._driver:
            self._driver.close()
            logger.info("Neo4j driver connection closed")
    
    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        """
        Execute a Cypher query asynchronously
        
        Returns:
            Tuple of (records as dicts, column headers, summary)
        """
        return self.execute_query_sync(query, parameters, database)
    
    def execute_query_sync(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        """
        Execute a Cypher query synchronously
        """
        db = database or self._database
        params = prepare_project_scope_parameters(query, parameters)
        
        with self._driver.session(database=db) as session:
            result = session.run(query, params)
            records = [record.data() for record in result]
            keys = result.keys()
            summary = result.consume()
            
            return records, keys, summary
    
    async def create_indexes(
        self,
        indexes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> None:
        """
        Create indexes
        
        Each index dict should have:
        - label: str
        - property: str or list of str
        - type: 'btree' or 'fulltext' (optional, defaults to btree)
        """
        for idx in indexes:
            label = idx["label"]
            prop = idx["property"]
            idx_type = idx.get("type", "btree")
            
            if isinstance(prop, list):
                props = ", ".join([f"n.{p}" for p in prop])
                idx_name = f"{label}_{'_'.join(prop)}_{idx_type}_idx"
            else:
                props = f"n.{prop}"
                idx_name = f"{label}_{prop}_{idx_type}_idx"
            
            if idx_type == "fulltext":
                query = f"""
                CREATE FULLTEXT INDEX {idx_name} IF NOT EXISTS
                FOR (n:{label})
                ON EACH [{props}]
                """
            else:
                query = f"""
                CREATE INDEX {idx_name} IF NOT EXISTS
                FOR (n:{label})
                ON ({props})
                """
            
            try:
                await self.execute_query(query, database=database)
                logger.info(f"Created index: {idx_name}")
            except Exception as exc:
                raise RuntimeError(f"Failed to create index {idx_name}: {exc}") from exc

    async def inspect_indexes(
        self,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return Neo4j index metadata in the provider-neutral preflight shape."""

        records, _, _ = await self.execute_query(
            """
            SHOW INDEXES
            YIELD labelsOrTypes, properties, type, entityType, state
            RETURN labelsOrTypes, properties, type, entityType, state
            """,
            database=database,
        )
        normalized: List[Dict[str, Any]] = []
        for record in records:
            labels = record.get("labelsOrTypes") or []
            label = labels[0] if isinstance(labels, (list, tuple)) and labels else labels
            properties_value = record.get("properties") or []
            properties = (
                [properties_value]
                if isinstance(properties_value, str)
                else list(properties_value)
            )
            index_type = str(record.get("type", "")).casefold()
            if index_type == "btree":
                index_type = "range"
            normalized.append(
                {
                    "label": str(label or ""),
                    "properties": properties,
                    "index_type": index_type,
                    "entity_type": str(record.get("entityType") or "node").casefold(),
                    "status": str(record.get("state") or "").upper(),
                }
            )
        return normalized
    
    def _run_transaction(
        self,
        session: Session,
        query: str,
        parameters: Dict[str, Any],
    ) -> Any:
        """Helper method to run a write transaction"""
        def tx_work(tx):
            return tx.run(query, **parameters)
        
        # Support both old and new Neo4j driver APIs
        if hasattr(session, "execute_write"):
            return session.execute_write(tx_work)
        elif hasattr(session, "write_transaction"):
            return session.write_transaction(tx_work)
        else:
            # Fallback for older versions
            return session._run_transaction(  # type: ignore
                tx_work,
                metadata=None,
                timeout=None,
            )
    
    # High-level query methods implementation
    
    async def list_databases(self) -> List[str]:
        """List available Neo4j databases"""
        try:
            query = "SHOW DATABASES"
            records, _, _ = await self.execute_query(query, database=self._database)
            names = []
            for record in records:
                name = record.get("name")
                if isinstance(name, str) and name not in names:
                    names.append(name)
            return names
        except Exception as e:
            logger.warning(f"Failed to list databases: {e}")
            return [self._database]
    
    async def list_relationship_types(self, database: Optional[str] = None) -> List[str]:
        """List all relationship types in the database"""
        try:
            # Try modern syntax first
            query = "SHOW RELATIONSHIP TYPES YIELD relationshipType RETURN relationshipType AS rel_type"
            records, _, _ = await self.execute_query(query, database=database)
        except Exception:
            # Fallback to procedure call
            query = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS rel_type"
            records, _, _ = await self.execute_query(query, database=database)
        
        rel_types = []
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
        """Find a node by its ID"""
        records, _, _ = await self.execute_query(
            _FIND_NODE_BY_ID_QUERY,
            {"id": node_id},
            database
        )
        if not records:
            records, _, _ = await self.execute_query(
                _FALLBACK_FIND_NODE_BY_ID_QUERY,
                {"id": node_id},
                database
            )
        if records:
            node = records[0].get("n")
            if node and matches_project_scope(node, project_id):
                if node.get("framework") == "servlet_jsp":
                    active_records, _, _ = await self.execute_query(
                        "MATCH (s:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id}) "
                        "WHERE s.active_generation = $generation_id RETURN s.id AS id LIMIT 1",
                        {
                            "project_id": node.get("project_id"),
                            "module_id": node.get("module_id"),
                            "generation_id": node.get("generation_id"),
                        },
                        database,
                    )
                    if not active_records:
                        return None
                return node
        return None
    
    async def find_nodes_by_ids(
        self,
        node_ids: List[str],
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find multiple nodes by their IDs"""
        if not node_ids:
            return []
        
        records, _, _ = await self.execute_query(
            _FIND_NODES_BY_IDS_QUERY,
            {"ids": node_ids},
            database
        )
        nodes = [record.get("n") for record in records if record.get("n")]
        found_ids = {str(node.get("id")) for node in nodes if node and node.get("id") is not None}
        unresolved_ids = [node_id for node_id in node_ids if str(node_id) not in found_ids]
        if unresolved_ids:
            fallback_records, _, _ = await self.execute_query(
                _FALLBACK_FIND_NODES_BY_IDS_QUERY,
                {"ids": unresolved_ids},
                database
            )
            nodes.extend(record.get("n") for record in fallback_records if record.get("n"))
        # Filter by project_id in Python (since queries are pre-built UNION queries)
        if project_id is not None:
            nodes = [n for n in nodes if matches_project_scope(n, project_id)]
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
        """Search for functions by name or qualified_name"""
        fulltext_cypher = """
        CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score
        WHERE node:Function
          AND ($project_id IS NULL OR node.project_id_normalized = $project_id_normalized)
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            fulltext_records, _, _ = await self.execute_query(
                fulltext_cypher,
                {"index_name": _FULLTEXT_SYMBOL_TEXT_INDEX, "query": query, "limit": limit, "project_id": project_id},
                database
            )
            fulltext_nodes = [record.get("n") for record in fulltext_records if record.get("n")]
            if fulltext_nodes:
                return fulltext_nodes
        except Exception as exc:
            logger.debug("Fulltext search_functions fallback to CONTAINS: %s", exc)

        cypher = """
        MATCH (n:Function)
        WHERE toLower(n.name) CONTAINS toLower($query)
           OR toLower(coalesce(n.qualified_name, '')) CONTAINS toLower($query)
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database
        )
        return [record.get("n") for record in records if record.get("n")]
    async def search_by_code(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for nodes by code content"""
        fulltext_cypher = """
        CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score
        WHERE ($project_id IS NULL OR node.project_id_normalized = $project_id_normalized)
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            fulltext_records, _, _ = await self.execute_query(
                fulltext_cypher,
                {"index_name": _FULLTEXT_SYMBOL_CODE_INDEX, "query": query, "limit": limit, "project_id": project_id},
                database
            )
            fulltext_nodes = [record.get("n") for record in fulltext_records if record.get("n")]
            if fulltext_nodes:
                return fulltext_nodes
        except Exception as exc:
            logger.debug("Fulltext search_by_code fallback to CONTAINS: %s", exc)

        cypher = """
        MATCH (n)
        WHERE toLower(coalesce(n.code, '')) CONTAINS toLower($query)
           OR toLower(coalesce(n.comment, '')) CONTAINS toLower($query)
           OR toLower(coalesce(n.summary, '')) CONTAINS toLower($query)
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database
        )
        return [record.get("n") for record in records if record.get("n")]

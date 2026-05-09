"""
Neo4j Implementation of Graph Driver

Concrete implementation of the GraphDriver abstraction for Neo4j.
"""

import base64
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from neo4j import GraphDatabase, Driver, Session
import logging

from tools.graph.core.base import GraphDriver, GraphProvider


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
_FULLTEXT_SYMBOL_TEXT_INDEX = "mcp_symbol_text_ft"
_FULLTEXT_SYMBOL_CODE_INDEX = "mcp_symbol_code_ft"


class Neo4jDriver(GraphDriver):
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
        params = parameters or {}
        
        with self._driver.session(database=db) as session:
            result = session.run(query, params)
            records = [record.data() for record in result]
            keys = result.keys()
            summary = result.consume()
            
            return records, keys, summary
    
    async def batch_write_nodes(
        self,
        nodes: List[Dict[str, Any]],
        label: str,
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create nodes using UNWIND
        """
        if not nodes:
            return 0
        
        query = f"""
        UNWIND $nodes AS node
        CREATE (n:{label})
        SET n = node
        RETURN count(n) as count
        """
        
        records, _, _ = await self.execute_query(
            query,
            {"nodes": nodes},
            database
        )
        
        return records[0]["count"] if records else 0
    
    async def batch_write_edges(
        self,
        edges: List[Dict[str, Any]],
        relationship_type: str,
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create relationships using UNWIND
        
        Each edge dict must have 'source_id' and 'target_id' keys
        """
        if not edges:
            return 0
        
        query = f"""
        UNWIND $edges AS edge
        MATCH (source {{id: edge.source_id}})
        MATCH (target {{id: edge.target_id}})
        CREATE (source)-[r:{relationship_type}]->(target)
        SET r = edge.properties
        RETURN count(r) as count
        """
        
        records, _, _ = await self.execute_query(
            query,
            {"edges": edges},
            database
        )
        
        return records[0]["count"] if records else 0
    
    async def verify_connection(self) -> bool:
        """Test the database connection"""
        try:
            query = "RETURN 1 as test"
            records, _, _ = await self.execute_query(query)
            return len(records) > 0 and records[0]["test"] == 1
        except Exception as e:
            logger.error(f"Connection verification failed: {e}")
            return False
    
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
                idx_name = f"{label}_{'_'.join(prop)}_idx"
            else:
                props = f"n.{prop}"
                idx_name = f"{label}_{prop}_idx"
            
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
            except Exception as e:
                logger.warning(f"Failed to create index {idx_name}: {e}")
    
    async def get_node_count(
        self,
        label: Optional[str] = None,
        database: Optional[str] = None,
    ) -> int:
        """Get count of nodes, optionally filtered by label"""
        if label:
            query = f"MATCH (n:{label}) RETURN count(n) as count"
        else:
            query = "MATCH (n) RETURN count(n) as count"
        
        records, _, _ = await self.execute_query(query, database=database)
        return records[0]["count"] if records else 0
    
    async def get_edge_count(
        self,
        relationship_type: Optional[str] = None,
        database: Optional[str] = None,
    ) -> int:
        """Get count of relationships, optionally filtered by type"""
        if relationship_type:
            query = f"MATCH ()-[r:{relationship_type}]->() RETURN count(r) as count"
        else:
            query = "MATCH ()-[r]->() RETURN count(r) as count"
        
        records, _, _ = await self.execute_query(query, database=database)
        return records[0]["count"] if records else 0
    
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
            return records[0].get("n")
        return None
    
    async def find_nodes_by_ids(
        self,
        node_ids: List[str],
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
        return nodes
    
    async def search_functions(
        self,
        query: str,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for functions by name or qualified_name"""
        fulltext_cypher = """
        CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score
        WHERE node:Function
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            fulltext_records, _, _ = await self.execute_query(
                fulltext_cypher,
                {"index_name": _FULLTEXT_SYMBOL_TEXT_INDEX, "query": query, "limit": limit},
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
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit},
            database
        )
        return [record.get("n") for record in records if record.get("n")]
    
    async def search_by_code(
        self,
        query: str,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search for nodes by code content"""
        fulltext_cypher = """
        CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        try:
            fulltext_records, _, _ = await self.execute_query(
                fulltext_cypher,
                {"index_name": _FULLTEXT_SYMBOL_CODE_INDEX, "query": query, "limit": limit},
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
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit},
            database
        )
        return [record.get("n") for record in records if record.get("n")]
    
    async def find_function_paths(
        self,
        start_id: str,
        end_id: str,
        relationship_types: List[str],
        max_depth: int = 8,
        database: Optional[str] = None,
    ) -> List[Any]:
        """Find shortest paths between two functions"""
        rel_pattern = f"[:{'|'.join(relationship_types)}*..{max_depth}]"
        cypher = f"""
        MATCH (a:Function) WHERE a.id = $start
        MATCH (b:Function) WHERE b.id = $end
        AND a.id <> b.id
        MATCH p=shortestPath((a)-{rel_pattern}->(b))
        RETURN p
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"start": start_id, "end": end_id},
            database
        )
        return [record.get("p") for record in records if record.get("p")]
    
    async def query_function_subgraph(
        self,
        function_id: str,
        relationship_types: List[str],
        direction: str = "both",
        max_depth: int = 2,
        database: Optional[str] = None,
    ) -> List[Any]:
        """Query subgraph around a function"""
        rel_pattern = f"[:{'|'.join(relationship_types)}*1..{max_depth}]"
        
        if direction.lower() in {"incoming", "in"}:
            pattern = f"<-{rel_pattern}-"
        elif direction.lower() in {"outgoing", "out"}:
            pattern = f"-{rel_pattern}->"
        else:  # both
            pattern = f"-{rel_pattern}-"
        
        cypher = f"""
        MATCH (f:Function) WHERE f.id = $id
        MATCH p=(f){pattern}(n)
        RETURN p
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"id": function_id},
            database
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
        database: Optional[str] = None,
    ) -> List[Any]:
        """Find paths between modules (file paths)"""
        # Try with specified direction first
        paths = await self._find_module_paths_directed(
            source_modules, target_modules, relationship_types,
            max_depth, limit, direction, database
        )
        
        # If no paths found and direction is not 'both', try bidirectional
        if not paths and direction.lower() not in {"both", "any", "undirected"}:
            paths = await self._find_module_paths_directed(
                source_modules, target_modules, relationship_types,
                max_depth, limit, "both", database
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
        database: Optional[str] = None,
    ) -> List[Any]:
        """Internal helper for directional path finding"""
        rel_types_str = "|".join(relationship_types)
        
        # Build relationship pattern based on direction
        if direction.lower() in {"in", "incoming"}:
            rel_pattern = f"<-[:{rel_types_str}*..{max_depth}]-"
        elif direction.lower() in {"both", "any", "undirected"}:
            rel_pattern = f"-[:{rel_types_str}*..{max_depth}]-"
        else:  # out/outgoing
            rel_pattern = f"-[:{rel_types_str}*..{max_depth}]->"
        
        cypher = f"""
        WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets
        MATCH (s:Function)<-[:CONTAINS]-(sf:File)
        MATCH (t:Function)<-[:CONTAINS]-(tf:File)
        WHERE any(token IN sources WHERE
            toLower(coalesce(s.file_path, '')) CONTAINS token OR
            toLower(coalesce(sf.path, '')) CONTAINS token OR
            toLower(coalesce(sf.file_path, '')) CONTAINS token)
        AND any(token IN targets WHERE
            toLower(coalesce(t.file_path, '')) CONTAINS token OR
            toLower(coalesce(tf.path, '')) CONTAINS token OR
            toLower(coalesce(tf.file_path, '')) CONTAINS token)
        AND s.id <> t.id
        MATCH p=shortestPath((s){rel_pattern}(t))
        RETURN p
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"sources": source_modules, "targets": target_modules, "limit": limit},
            database
        )
        return [record.get("p") for record in records if record.get("p")]
    
    async def list_possible_calls(
        self,
        limit: int = 200,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """List POSSIBLE_CALLS relationships"""
        cypher = """
        MATCH (a:Function)-[r:POSSIBLE_CALLS]->(b:Function)
        WHERE ($project_id IS NULL OR a.project_id = $project_id)
        AND ($project_id IS NULL OR b.project_id = $project_id)
        RETURN a, b, r
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"limit": limit, "project_id": project_id},
            database
        )
        
        nodes = []
        edges = []
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
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List symbols (functions) in files matching path tokens"""
        cypher = """
        WITH [t IN $tokens | toLower(t)] AS tokens
        MATCH (f:Function)<-[:CONTAINS]-(file:File)
        WHERE any(token IN tokens WHERE
            toLower(coalesce(f.file_path, '')) CONTAINS token OR
            toLower(coalesce(file.path, '')) CONTAINS token OR
            toLower(coalesce(file.file_path, '')) CONTAINS token)
        RETURN DISTINCT f
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"tokens": file_paths},
            database
        )
        return [record.get("f") for record in records if record.get("f")]
    
    async def list_functions_by_class(
        self,
        class_names: List[str],
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List functions in classes matching names"""
        cypher = """
        WITH [t IN $tokens | toLower(t)] AS tokens
        MATCH (c:Class)
        WHERE any(token IN tokens WHERE
            toLower(coalesce(c.name, '')) CONTAINS token OR
            toLower(coalesce(c.qualified_name, '')) CONTAINS token)
        MATCH (c)-[:CONTAINS]->(f:Function)
        RETURN DISTINCT f
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"tokens": class_names},
            database
        )
        return [record.get("f") for record in records if record.get("f")]
    
    async def list_functions_by_file(
        self,
        file_path: str,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List functions in a specific file"""
        cypher = """
        MATCH (f:Function)<-[:CONTAINS]-(file:File)
        WHERE toLower(coalesce(f.file_path, '')) CONTAINS toLower($token)
           OR toLower(coalesce(file.path, '')) CONTAINS toLower($token)
           OR toLower(coalesce(file.file_path, '')) CONTAINS toLower($token)
        RETURN DISTINCT f
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"token": file_path},
            database
        )
        return [record.get("f") for record in records if record.get("f")]
    
    async def list_entrypoints(
        self,
        modules: List[str],
        relationship_types: List[str],
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List entrypoint functions called from outside specified modules"""
        rel_pattern = "|".join(relationship_types)
        cypher = f"""
        WITH [t IN $modules | toLower(t)] AS modules
        MATCH (internalFile:File)-[:CONTAINS]->(internalFn:Function)
        WHERE any(token IN modules WHERE
            toLower(coalesce(internalFn.file_path, '')) CONTAINS token OR
            toLower(coalesce(internalFile.path, '')) CONTAINS token OR
            toLower(coalesce(internalFile.file_path, '')) CONTAINS token)
        WITH collect(internalFn.id) AS internalIds, modules
        MATCH (externalFile:File)-[:CONTAINS]->(externalFn:Function)
        WHERE NOT any(token IN modules WHERE
            toLower(coalesce(externalFn.file_path, '')) CONTAINS token OR
            toLower(coalesce(externalFile.path, '')) CONTAINS token OR
            toLower(coalesce(externalFile.file_path, '')) CONTAINS token)
        MATCH (externalFn)-[:{rel_pattern}]->(entryFn:Function)
        WHERE entryFn.id IN internalIds
        RETURN DISTINCT entryFn
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"modules": modules},
            database
        )
        return [record.get("entryFn") for record in records if record.get("entryFn")]

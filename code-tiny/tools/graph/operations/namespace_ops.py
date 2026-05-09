"""
Namespace Node Operations

Handles operations for namespace nodes (C++, C#, etc.)
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class NamespaceNodeOperations:
    """
    Operations for namespace nodes
    
    Manages namespaces in C++, C#, and similar languages
    """
    
    @staticmethod
    async def create_namespace_node(
        driver: GraphDriver,
        namespace_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a namespace node
        
        Args:
            driver: Graph driver instance
            namespace_data: Namespace metadata (id, name, etc.)
            database: Optional database name
            
        Returns:
            Namespace node ID
        """
        query = """
        CREATE (n:Namespace {
            id: $id,
            qualified_name: $qualified_name,
            name: $name,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN n.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            namespace_data,
            database
        )
        
        return records[0]["id"] if records else namespace_data["id"]
    
    @staticmethod
    async def batch_create_namespaces(
        driver: GraphDriver,
        namespaces: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create namespace nodes
        
        Args:
            driver: Graph driver instance
            namespaces: List of namespace data dicts
            database: Optional database name
            
        Returns:
            Number of namespaces created
        """
        if not namespaces:
            return 0
        
        query = """
        UNWIND $rows AS row
        MERGE (n:Namespace {id: row.id})
        SET n.qualified_name = row.qualified_name,
            n.name = row.name,
            n.file_path = row.file_path,
            n.start_line = row.start_line,
            n.end_line = row.end_line,
            n.code = row.code,
            n.comment = row.comment,
            n.summary = row.summary,
            n.note = row.note,
            n.updated_at = datetime()
        RETURN count(n) as count
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"rows": namespaces},
            database
        )
        
        return records[0]["count"] if records else 0
    
    @staticmethod
    async def link_namespace_hierarchy(
        driver: GraphDriver,
        child_id: str,
        parent_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Create parent-child relationship between namespaces
        
        Args:
            driver: Graph driver instance
            child_id: Child namespace ID
            parent_id: Parent namespace ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (child:Namespace {id: $child_id})
        MATCH (parent:Namespace {id: $parent_id})
        MERGE (child)-[r:NESTED_IN]->(parent)
        SET r.created_at = datetime()
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"child_id": child_id, "parent_id": parent_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def link_entity_to_namespace(
        driver: GraphDriver,
        entity_id: str,
        entity_label: str,
        namespace_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Link an entity (class, function, type) to its namespace
        
        Args:
            driver: Graph driver instance
            entity_id: Entity ID
            entity_label: Entity label (Class, Function, Type, etc.)
            namespace_id: Namespace ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = f"""
        MATCH (e:{entity_label} {{id: $entity_id}})
        MATCH (n:Namespace {{id: $namespace_id}})
        MERGE (e)-[r:IN_NAMESPACE]->(n)
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"entity_id": entity_id, "namespace_id": namespace_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def get_namespace_contents(
        driver: GraphDriver,
        namespace_id: str,
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all entities within a namespace
        
        Args:
            driver: Graph driver instance
            namespace_id: Namespace ID
            database: Optional database name
            
        Returns:
            Namespace data with nested contents
        """
        query = """
        MATCH (n:Namespace {id: $namespace_id})
        OPTIONAL MATCH (child:Namespace)-[:NESTED_IN]->(n)
        OPTIONAL MATCH (c:Class)-[:IN_NAMESPACE]->(n)
        OPTIONAL MATCH (f:Function)-[:IN_NAMESPACE]->(n)
        OPTIONAL MATCH (t:Type)-[:IN_NAMESPACE]->(n)
        RETURN 
            n.id as namespace_id,
            n.name as namespace_name,
            n.qualified_name as qualified_name,
            collect(DISTINCT child.id) as child_namespaces,
            collect(DISTINCT c.id) as classes,
            collect(DISTINCT f.id) as functions,
            collect(DISTINCT t.id) as types
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"namespace_id": namespace_id},
            database
        )
        
        return records[0] if records else {}
    
    @staticmethod
    async def find_namespaces_by_pattern(
        driver: GraphDriver,
        pattern: str,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find namespaces by qualified name pattern
        
        Args:
            driver: Graph driver instance
            pattern: Namespace pattern (e.g., 'std::*', 'MyApp::*')
            limit: Maximum results
            database: Optional database name
            
        Returns:
            List of matching namespaces
        """
        query = """
        MATCH (n:Namespace)
        WHERE n.qualified_name =~ $regex_pattern
        RETURN 
            n.id as id,
            n.name as name,
            n.qualified_name as qualified_name,
            n.summary as summary
        ORDER BY n.qualified_name
        LIMIT $limit
        """
        
        # Convert pattern to regex (e.g., 'std::*' -> 'std::.*')
        regex_pattern = pattern.replace("::", "::").replace("*", ".*")
        
        records, _, _ = await driver.execute_query(
            query,
            {"regex_pattern": regex_pattern, "limit": limit},
            database
        )
        
        return records

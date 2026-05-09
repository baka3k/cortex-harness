"""
Type Node Operations

Handles operations for type/typedef nodes (C++, C#, typed languages)
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class TypeNodeOperations:
    """
    Operations for type/typedef nodes
    
    Manages type definitions in statically typed languages
    """
    
    @staticmethod
    async def create_type_node(
        driver: GraphDriver,
        type_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a type node
        
        Args:
            driver: Graph driver instance
            type_data: Type metadata (id, name, kind, etc.)
            database: Optional database name
            
        Returns:
            Type node ID
        """
        query = """
        CREATE (t:Type {
            id: $id,
            qualified_name: $qualified_name,
            name: $name,
            kind: $kind,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN t.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            type_data,
            database
        )
        
        return records[0]["id"] if records else type_data["id"]
    
    @staticmethod
    async def batch_create_types(
        driver: GraphDriver,
        types: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create type nodes
        
        Args:
            driver: Graph driver instance
            types: List of type data dicts
            database: Optional database name
            
        Returns:
            Number of types created
        """
        if not types:
            return 0
        
        query = """
        UNWIND $rows AS row
        MERGE (t:Type {id: row.id})
        SET t.qualified_name = row.qualified_name,
            t.name = row.name,
            t.kind = row.kind,
            t.file_path = row.file_path,
            t.start_line = row.start_line,
            t.end_line = row.end_line,
            t.code = row.code,
            t.comment = row.comment,
            t.summary = row.summary,
            t.note = row.note,
            t.updated_at = datetime()
        RETURN count(t) as count
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"rows": types},
            database
        )
        
        return records[0]["count"] if records else 0
    
    @staticmethod
    async def link_type_usage(
        driver: GraphDriver,
        user_id: str,
        user_label: str,
        type_id: str,
        usage_kind: str = "USES_TYPE",
        properties: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> bool:
        """
        Create type usage relationship
        
        Args:
            driver: Graph driver instance
            user_id: ID of entity using the type
            user_label: Label of entity (Function, Class, etc.)
            type_id: Type ID being used
            usage_kind: Kind of usage (USES_TYPE, POINTER_TO, REFERENCE_TO, etc.)
            properties: Optional relationship properties
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = f"""
        MATCH (user:{user_label} {{id: $user_id}})
        MATCH (t:Type {{id: $type_id}})
        MERGE (user)-[r:{usage_kind}]->(t)
        """
        
        if properties:
            for key in properties.keys():
                query += f"\nSET r.{key} = ${key}"
        
        query += "\nRETURN r"
        
        params = {
            "user_id": user_id,
            "type_id": type_id,
            **(properties or {})
        }
        
        records, _, _ = await driver.execute_query(query, params, database)
        return len(records) > 0
    
    @staticmethod
    async def link_type_alias(
        driver: GraphDriver,
        alias_id: str,
        target_type_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Create alias relationship (typedef, using)
        
        Args:
            driver: Graph driver instance
            alias_id: Alias type ID
            target_type_id: Target type ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (alias:Type {id: $alias_id})
        MATCH (target:Type {id: $target_type_id})
        MERGE (alias)-[r:ALIAS_OF]->(target)
        SET r.created_at = datetime()
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"alias_id": alias_id, "target_type_id": target_type_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def get_type_usages(
        driver: GraphDriver,
        type_id: str,
        limit: int = 100,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find all usages of a type
        
        Args:
            driver: Graph driver instance
            type_id: Type ID
            limit: Maximum results
            database: Optional database name
            
        Returns:
            List of entities using this type
        """
        query = """
        MATCH (entity)-[r:USES_TYPE|POINTER_TO|REFERENCE_TO]->(t:Type {id: $type_id})
        RETURN 
            entity.id as entity_id,
            labels(entity)[0] as entity_label,
            entity.name as entity_name,
            entity.qualified_name as qualified_name,
            type(r) as usage_kind
        ORDER BY entity_label, entity_name
        LIMIT $limit
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"type_id": type_id, "limit": limit},
            database
        )
        
        return records
    
    @staticmethod
    async def find_primitive_types(
        driver: GraphDriver,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find all primitive/built-in types
        
        Args:
            driver: Graph driver instance
            database: Optional database name
            
        Returns:
            List of primitive types
        """
        query = """
        MATCH (t:Type)
        WHERE t.kind = 'primitive' OR t.kind = 'builtin'
        RETURN 
            t.id as id,
            t.name as name,
            t.kind as kind
        ORDER BY t.name
        """
        
        records, _, _ = await driver.execute_query(query, {}, database)
        return records
    
    @staticmethod
    async def resolve_type_chain(
        driver: GraphDriver,
        type_id: str,
        max_depth: int = 10,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Resolve type alias chain (typedef of typedef of...)
        
        Args:
            driver: Graph driver instance
            type_id: Starting type ID
            max_depth: Maximum chain depth
            database: Optional database name
            
        Returns:
            List of types in the alias chain
        """
        query = f"""
        MATCH path = (t:Type {{id: $type_id}})-[:ALIAS_OF*0..{max_depth}]->(final:Type)
        WHERE NOT (final)-[:ALIAS_OF]->()
        RETURN 
            [node in nodes(path) | {{
                id: node.id,
                name: node.name,
                kind: node.kind
            }}] as type_chain
        LIMIT 1
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"type_id": type_id},
            database
        )
        
        if records and records[0].get("type_chain"):
            return records[0]["type_chain"]
        return []

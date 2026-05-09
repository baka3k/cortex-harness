"""
Class Node Operations

Handles operations for class/struct/interface nodes in OOP languages
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class ClassNodeOperations:
    """
    Operations for class/struct/interface nodes
    
    Manages OOP type definitions across languages
    """
    
    @staticmethod
    async def create_class_node(
        driver: GraphDriver,
        class_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a class/struct/interface node
        
        Args:
            driver: Graph driver instance
            class_data: Class metadata (id, name, kind, package, etc.)
            database: Optional database name
            
        Returns:
            Class node ID
        """
        query = """
        CREATE (c:Class {
            id: $id,
            node_type: 'code',
            qualified_name: $qualified_name,
            name: $name,
            kind: $kind,
            package_name: $package_name,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN c.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            class_data,
            database
        )
        
        return records[0]["id"] if records else class_data["id"]
    
    @staticmethod
    async def batch_create_classes(
        driver: GraphDriver,
        classes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create class nodes
        
        Args:
            driver: Graph driver instance
            classes: List of class data dicts
            database: Optional database name
            
        Returns:
            Number of classes created
        """
        if not classes:
            return 0
        
        query = """
        UNWIND $rows AS row
        MERGE (c:Class {id: row.id})
        SET c.qualified_name = row.qualified_name,
            c.node_type = 'code',
            c.name = row.name,
            c.kind = row.kind,
            c.package_name = row.package_name,
            c.file_path = row.file_path,
            c.start_line = row.start_line,
            c.end_line = row.end_line,
            c.code = row.code,
            c.comment = row.comment,
            c.summary = row.summary,
            c.note = row.note,
            c.updated_at = datetime()
        RETURN count(c) as count
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"rows": classes},
            database
        )
        
        return records[0]["count"] if records else 0
    
    @staticmethod
    async def link_class_inheritance(
        driver: GraphDriver,
        child_id: str,
        parent_id: str,
        inheritance_type: str = "EXTENDS",
        database: Optional[str] = None,
    ) -> bool:
        """
        Create inheritance relationship between classes
        
        Args:
            driver: Graph driver instance
            child_id: Child class ID
            parent_id: Parent class/interface ID
            inheritance_type: 'EXTENDS' or 'IMPLEMENTS'
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = f"""
        MATCH (child:Class {{id: $child_id}})
        MATCH (parent:Class {{id: $parent_id}})
        MERGE (child)-[r:{inheritance_type}]->(parent)
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
    async def link_class_to_package(
        driver: GraphDriver,
        class_id: str,
        package_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Link class to its package
        
        Args:
            driver: Graph driver instance
            class_id: Class ID
            package_id: Package ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (c:Class {id: $class_id})
        MATCH (p:Package {id: $package_id})
        MERGE (c)-[r:BELONGS_TO_PACKAGE]->(p)
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"class_id": class_id, "package_id": package_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def link_method_to_class(
        driver: GraphDriver,
        method_id: str,
        class_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Link method/function to its containing class
        
        Args:
            driver: Graph driver instance
            method_id: Function/method ID
            class_id: Class ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (m:Function {id: $method_id})
        MATCH (c:Class {id: $class_id})
        MERGE (m)-[r:BELONGS_TO_CLASS]->(c)
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"method_id": method_id, "class_id": class_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def get_class_hierarchy(
        driver: GraphDriver,
        class_id: str,
        direction: str = "up",
        max_depth: int = 10,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get class inheritance hierarchy
        
        Args:
            driver: Graph driver instance
            class_id: Class ID
            direction: 'up' (parents), 'down' (children), or 'both'
            max_depth: Maximum depth to traverse
            database: Optional database name
            
        Returns:
            List of related classes in hierarchy
        """
        if direction == "up":
            pattern = f"(c:Class {{id: $class_id}})-[:EXTENDS|IMPLEMENTS*1..{max_depth}]->(parent:Class)"
            return_clause = "parent"
        elif direction == "down":
            pattern = f"(child:Class)-[:EXTENDS|IMPLEMENTS*1..{max_depth}]->(c:Class {{id: $class_id}})"
            return_clause = "child"
        else:  # both
            pattern = f"(c:Class {{id: $class_id}})-[:EXTENDS|IMPLEMENTS*1..{max_depth}]-(related:Class)"
            return_clause = "related"
        
        query = f"""
        MATCH {pattern}
        RETURN DISTINCT
            {return_clause}.id as id,
            {return_clause}.name as name,
            {return_clause}.qualified_name as qualified_name,
            {return_clause}.kind as kind
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"class_id": class_id},
            database
        )
        
        return records
    
    @staticmethod
    async def get_class_methods(
        driver: GraphDriver,
        class_id: str,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all methods belonging to a class
        
        Args:
            driver: Graph driver instance
            class_id: Class ID
            database: Optional database name
            
        Returns:
            List of method/function nodes
        """
        query = """
        MATCH (m:Function)-[:BELONGS_TO_CLASS]->(c:Class {id: $class_id})
        RETURN 
            m.id as id,
            m.name as name,
            m.qualified_name as qualified_name,
            m.kind as kind,
            m.arity as arity,
            m.summary as summary
        ORDER BY m.name
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"class_id": class_id},
            database
        )
        
        return records
    
    @staticmethod
    async def find_inner_classes(
        driver: GraphDriver,
        outer_class_id: str,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find inner/nested classes within a class
        
        Args:
            driver: Graph driver instance
            outer_class_id: Outer class ID
            database: Optional database name
            
        Returns:
            List of inner class nodes
        """
        query = """
        MATCH (inner:Class)-[:NESTED_IN]->(outer:Class {id: $outer_class_id})
        RETURN 
            inner.id as id,
            inner.name as name,
            inner.kind as kind
        ORDER BY inner.name
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"outer_class_id": outer_class_id},
            database
        )
        
        return records

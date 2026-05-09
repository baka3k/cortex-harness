"""
Package Node Operations

Handles operations for package/module nodes (Java, Kotlin, Python, etc.)
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class PackageNodeOperations:
    """
    Operations for package/module nodes
    
    Manages packages in Java/Kotlin, modules in Python, namespaces in other languages
    """
    
    @staticmethod
    async def create_package_node(
        driver: GraphDriver,
        package_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a package node
        
        Args:
            driver: Graph driver instance
            package_data: Package metadata (id, name, etc.)
            database: Optional database name
            
        Returns:
            Package node ID
        """
        query = """
        CREATE (p:Package {
            id: $id,
            name: $name,
            start_line: $start_line,
            end_line: $end_line,
            code: $code,
            comment: $comment,
            summary: $summary,
            note: $note,
            created_at: datetime()
        })
        RETURN p.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            package_data,
            database
        )
        
        return records[0]["id"] if records else package_data["id"]
    
    @staticmethod
    async def batch_create_packages(
        driver: GraphDriver,
        packages: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create package nodes
        
        Args:
            driver: Graph driver instance
            packages: List of package data dicts
            database: Optional database name
            
        Returns:
            Number of packages created
        """
        if not packages:
            return 0
        
        query = """
        UNWIND $rows AS row
        MERGE (p:Package {id: row.id})
        SET p.name = row.name,
            p.start_line = row.start_line,
            p.end_line = row.end_line,
            p.code = row.code,
            p.comment = row.comment,
            p.summary = row.summary,
            p.note = row.note,
            p.updated_at = datetime()
        RETURN count(p) as count
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"rows": packages},
            database
        )
        
        return records[0]["count"] if records else 0
    
    @staticmethod
    async def link_file_to_package(
        driver: GraphDriver,
        file_path: str,
        package_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Link a file to its package
        
        Args:
            driver: Graph driver instance
            file_path: File path
            package_id: Package ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (f:File {file_path: $file_path})
        MATCH (p:Package {id: $package_id})
        MERGE (f)-[r:BELONGS_TO_PACKAGE]->(p)
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"file_path": file_path, "package_id": package_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def get_package_contents(
        driver: GraphDriver,
        package_id: str,
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all contents of a package (files, classes, functions)
        
        Args:
            driver: Graph driver instance
            package_id: Package ID
            database: Optional database name
            
        Returns:
            Package data with nested contents
        """
        query = """
        MATCH (p:Package {id: $package_id})
        OPTIONAL MATCH (f:File)-[:BELONGS_TO_PACKAGE]->(p)
        OPTIONAL MATCH (c:Class)-[:BELONGS_TO_PACKAGE]->(p)
        OPTIONAL MATCH (fn:Function)-[:BELONGS_TO_PACKAGE]->(p)
        RETURN 
            p.id as package_id,
            p.name as package_name,
            collect(DISTINCT f.file_path) as files,
            collect(DISTINCT c.id) as classes,
            count(DISTINCT fn) as function_count
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"package_id": package_id},
            database
        )
        
        return records[0] if records else {}
    
    @staticmethod
    async def find_packages_by_prefix(
        driver: GraphDriver,
        prefix: str,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find packages by name prefix (e.g., 'com.example.*')
        
        Args:
            driver: Graph driver instance
            prefix: Package name prefix
            limit: Maximum results
            database: Optional database name
            
        Returns:
            List of matching packages
        """
        query = """
        MATCH (p:Package)
        WHERE p.name STARTS WITH $prefix
        RETURN 
            p.id as id,
            p.name as name,
            p.summary as summary
        ORDER BY p.name
        LIMIT $limit
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"prefix": prefix, "limit": limit},
            database
        )
        
        return records

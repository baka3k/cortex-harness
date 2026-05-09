"""
Function Node Operations

Handles all operations related to function/method nodes in the code graph.
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class FunctionNodeOperations:
    """
    Operations for function/method nodes
    
    Manages creation, updates, and queries for code function nodes
    """
    
    @staticmethod
    async def create_function_node(
        driver: GraphDriver,
        function_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a function node
        
        Args:
            driver: Graph driver instance
            function_data: Function metadata (id, name, code, language, etc.)
            database: Optional database name
            
        Returns:
            Function node ID
        """
        query = """
        CREATE (f:Function {
            id: $id,
            node_type: 'code',
            name: $name,
            qualified_name: $qualified_name,
            code: $code,
            language: $language,
            file_path: $file_path,
            start_line: $start_line,
            end_line: $end_line,
            comment: $comment,
            summary: $summary,
            created_at: datetime()
        })
        RETURN f.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            function_data,
            database
        )
        
        return records[0]["id"] if records else function_data["id"]
    
    @staticmethod
    async def update_function_summary(
        driver: GraphDriver,
        function_id: str,
        summary: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Update function summary (from LLM analysis)
        
        Args:
            driver: Graph driver instance
            function_id: Function node ID
            summary: Generated summary text
            database: Optional database name
            
        Returns:
            True if update successful
        """
        query = """
        MATCH (f:Function {id: $function_id})
        SET f.summary = $summary,
            f.summary_updated_at = datetime()
        RETURN f.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"function_id": function_id, "summary": summary},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def link_function_call(
        driver: GraphDriver,
        caller_id: str,
        callee_id: str,
        call_data: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> bool:
        """
        Create CALLS relationship between functions
        
        Args:
            driver: Graph driver instance
            caller_id: ID of calling function
            callee_id: ID of called function
            call_data: Optional metadata about the call
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (caller:Function {id: $caller_id})
        MATCH (callee:Function {id: $callee_id})
        MERGE (caller)-[r:CALLS]->(callee)
        SET r.count = COALESCE(r.count, 0) + 1,
            r.last_updated = datetime()
        """
        
        if call_data:
            # Add custom properties from call_data
            for key, value in call_data.items():
                query += f"\nSET r.{key} = ${key}"
        
        query += "\nRETURN r"
        
        params = {
            "caller_id": caller_id,
            "callee_id": callee_id,
            **(call_data or {})
        }
        
        records, _, _ = await driver.execute_query(query, params, database)
        return len(records) > 0
    
    @staticmethod
    async def get_function_calls(
        driver: GraphDriver,
        function_id: str,
        direction: str = "outgoing",
        max_depth: int = 1,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get functions called by or calling this function
        
        Args:
            driver: Graph driver instance
            function_id: Function node ID
            direction: 'outgoing' (calls), 'incoming' (called by), or 'both'
            max_depth: Maximum depth to traverse
            database: Optional database name
            
        Returns:
            List of related functions with relationship data
        """
        if direction == "outgoing":
            pattern = f"(f:Function {{id: $function_id}})-[r:CALLS*1..{max_depth}]->(target:Function)"
        elif direction == "incoming":
            pattern = f"(source:Function)-[r:CALLS*1..{max_depth}]->(f:Function {{id: $function_id}})"
        else:  # both
            pattern = f"(f:Function {{id: $function_id}})-[r:CALLS*1..{max_depth}]-(related:Function)"
        
        query = f"""
        MATCH {pattern}
        RETURN DISTINCT 
            f.id as function_id,
            related.id as related_id,
            related.name as related_name,
            related.qualified_name as related_qualified_name,
            length(r) as depth
        ORDER BY depth, related_name
        LIMIT 100
        """
        
        if direction in ["incoming", "both"]:
            query = query.replace("related", "source")
        elif direction == "outgoing":
            query = query.replace("related", "target")
        
        records, _, _ = await driver.execute_query(
            query,
            {"function_id": function_id},
            database
        )
        
        return records
    
    @staticmethod
    async def batch_create_functions(
        driver: GraphDriver,
        functions: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create function nodes
        
        Args:
            driver: Graph driver instance
            functions: List of function data dicts
            database: Optional database name
            
        Returns:
            Number of functions created
        """
        return await driver.batch_write_nodes(
            functions,
            "Function",
            database
        )
    
    @staticmethod
    async def get_functions_without_summary(
        driver: GraphDriver,
        limit: int = 100,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get functions that need summary generation
        
        Args:
            driver: Graph driver instance
            limit: Maximum number of functions to return
            database: Optional database name
            
        Returns:
            List of function nodes without summaries
        """
        query = """
        MATCH (f:Function)
        WHERE f.summary IS NULL OR f.summary = ''
        AND f.code IS NOT NULL AND f.code <> ''
        RETURN 
            f.id as id,
            f.name as name,
            f.qualified_name as qualified_name,
            f.code as code,
            f.language as language,
            f.file_path as file_path
        LIMIT $limit
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"limit": limit},
            database
        )
        
        return records

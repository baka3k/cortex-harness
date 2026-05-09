"""
Cross-Edge Operations

Handles operations for creating and managing cross-references between
different types of nodes (e.g., code -> documentation, code -> infrastructure)
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class CrossEdgeOperations:
    """
    Operations for cross-domain relationships
    
    Manages relationships that connect different entity types in the graph
    """
    
    @staticmethod
    async def link_code_to_document(
        driver: GraphDriver,
        code_id: str,
        document_id: str,
        link_type: str = "IMPLEMENTS_LOGIC",
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> bool:
        """
        Create relationship between code and documentation
        
        Args:
            driver: Graph driver instance
            code_id: Code node ID
            document_id: Document/Paragraph node ID
            link_type: Relationship type
            confidence: Confidence score (0-1)
            metadata: Additional relationship properties
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = f"""
        MATCH (code {{id: $code_id}})
        MATCH (doc {{id: $document_id}})
        MERGE (code)-[r:{link_type}]->(doc)
        SET r.confidence = $confidence,
            r.created_at = datetime()
        """
        
        if metadata:
            for key in metadata.keys():
                query += f"\nSET r.{key} = ${key}"
        
        query += "\nRETURN r"
        
        params = {
            "code_id": code_id,
            "document_id": document_id,
            "confidence": confidence,
            **(metadata or {})
        }
        
        records, _, _ = await driver.execute_query(query, params, database)
        return len(records) > 0
    
    @staticmethod
    async def create_semantic_link(
        driver: GraphDriver,
        source_id: str,
        target_id: str,
        similarity_score: float,
        link_reason: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Create semantic similarity link between nodes
        
        Args:
            driver: Graph driver instance
            source_id: Source node ID
            target_id: Target node ID
            similarity_score: Semantic similarity score
            link_reason: Why these nodes are similar
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (source {id: $source_id})
        MATCH (target {id: $target_id})
        MERGE (source)-[r:SIMILAR_TO]->(target)
        SET r.similarity_score = $similarity_score,
            r.reason = $link_reason,
            r.created_at = datetime()
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {
                "source_id": source_id,
                "target_id": target_id,
                "similarity_score": similarity_score,
                "link_reason": link_reason
            },
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def find_code_without_documentation(
        driver: GraphDriver,
        code_label: str = "Function",
        limit: int = 100,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find code nodes that lack documentation links
        
        Args:
            driver: Graph driver instance
            code_label: Label of code nodes to check
            limit: Maximum results
            database: Optional database name
            
        Returns:
            List of code nodes without documentation
        """
        query = f"""
        MATCH (code:{code_label})
        WHERE NOT (code)-[:DOCUMENTED_BY|IMPLEMENTS_LOGIC]->(:Document)
        AND NOT (code)-[:DOCUMENTED_BY|IMPLEMENTS_LOGIC]->(:Paragraph)
        RETURN 
            code.id as id,
            code.name as name,
            code.qualified_name as qualified_name,
            code.file_path as file_path
        LIMIT $limit
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"limit": limit},
            database
        )
        
        return records
    
    @staticmethod
    async def batch_create_cross_links(
        driver: GraphDriver,
        links: List[Dict[str, Any]],
        relationship_type: str,
        database: Optional[str] = None,
    ) -> int:
        """
        Batch create cross-reference relationships
        
        Args:
            driver: Graph driver instance
            links: List of link data with source_id, target_id, properties
            relationship_type: Type of relationship to create
            database: Optional database name
            
        Returns:
            Number of links created
        """
        query = f"""
        UNWIND $links AS link
        MATCH (source {{id: link.source_id}})
        MATCH (target {{id: link.target_id}})
        MERGE (source)-[r:{relationship_type}]->(target)
        SET r = link.properties
        RETURN count(r) as count
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"links": links},
            database
        )
        
        return records[0]["count"] if records else 0
    
    @staticmethod
    async def get_connected_documentation(
        driver: GraphDriver,
        code_id: str,
        max_depth: int = 2,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all documentation connected to a code node
        
        Args:
            driver: Graph driver instance
            code_id: Code node ID
            max_depth: Maximum traverse depth
            database: Optional database name
            
        Returns:
            List of connected documents/paragraphs
        """
        query = f"""
        MATCH (code {{id: $code_id}})
        MATCH (code)-[r:DOCUMENTED_BY|IMPLEMENTS_LOGIC*1..{max_depth}]->(doc)
        WHERE doc:Document OR doc:Paragraph
        RETURN DISTINCT
            doc.id as id,
            labels(doc)[0] as type,
            doc.title as title,
            doc.content as content,
            length(r) as distance
        ORDER BY distance
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"code_id": code_id},
            database
        )
        
        return records

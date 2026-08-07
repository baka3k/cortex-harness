"""
Cross-Edge Operations

Handles operations for creating and managing cross-references between
different types of nodes (e.g., code -> documentation, code -> infrastructure)
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver
from tools.graph.schema.manifest import validate_cypher_identifier
from tools.graph.writer.query_contract import (
    RelationshipGroup,
    compile_relationship_upsert,
    group_typed_relations,
)


async def _ensure_schema(driver: GraphDriver, database: Optional[str]) -> None:
    ensure = getattr(driver, "ensure_schema", None)
    if callable(ensure):
        await ensure(database=database)


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
        *,
        code_label: str = "Function",
        document_label: str = "Paragraph",
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
        code_node_label = validate_cypher_identifier(code_label, kind="code label")
        document_node_label = validate_cypher_identifier(document_label, kind="document label")
        relationship = validate_cypher_identifier(link_type, kind="relationship type")
        RelationshipGroup(code_node_label, document_node_label, relationship)
        query = f"""
        MATCH (code:{code_node_label} {{id: $code_id}})
        MATCH (doc:{document_node_label} {{id: $document_id}})
        MERGE (code)-[r:{relationship}]->(doc)
        SET r.confidence = $confidence,
            r.created_at = datetime()
        """
        
        if metadata:
            for key in metadata.keys():
                property_name = validate_cypher_identifier(str(key), kind="property")
                query += f"\nSET r.{property_name} = ${property_name}"
        
        query += "\nRETURN r"
        
        params = {
            "code_id": code_id,
            "document_id": document_id,
            "confidence": confidence,
            **(metadata or {})
        }
        await _ensure_schema(driver, database)
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
        *,
        source_label: str,
        target_label: str,
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
        source_node_label = validate_cypher_identifier(source_label, kind="source label")
        target_node_label = validate_cypher_identifier(target_label, kind="target label")
        RelationshipGroup(source_node_label, target_node_label, "SIMILAR_TO")
        query = f"""
        MATCH (source:{source_node_label} {{id: $source_id}})
        MATCH (target:{target_node_label} {{id: $target_id}})
        MERGE (source)-[r:SIMILAR_TO]->(target)
        SET r.similarity_score = $similarity_score,
            r.reason = $link_reason,
            r.created_at = datetime()
        RETURN r
        """
        
        await _ensure_schema(driver, database)
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
        typed_rows = [
            {
                "source_label": link.get("source_label"),
                "target_label": link.get("target_label"),
                "rel_type": relationship_type,
                "source_id": link.get("source_id"),
                "target_id": link.get("target_id"),
                "properties": dict(link.get("properties") or {}),
            }
            for link in links
        ]
        groups = group_typed_relations(typed_rows)
        await _ensure_schema(driver, database)
        total = 0
        for group, rows in groups.items():
            records, _, _ = await driver.execute_query(
                compile_relationship_upsert(group), {"rows": rows}, database
            )
            matched = int(records[0].get("count", 0)) if records else 0
            if matched != len(rows):
                raise RuntimeError(
                    f"cross-link integrity failure for {group.state_key}: "
                    f"expected={len(rows)} matched={matched}"
                )
            total += matched
        return total
    
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

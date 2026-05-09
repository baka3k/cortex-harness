"""
Document Node Operations

Handles operations for document and paragraph nodes in the knowledge graph.
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class DocumentNodeOperations:
    """
    Operations for document and paragraph nodes
    
    Manages documentation, README files, and their relationships to code
    """
    
    @staticmethod
    async def create_document_node(
        driver: GraphDriver,
        document_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a document node
        
        Args:
            driver: Graph driver instance
            document_data: Document metadata (id, title, content, etc.)
            database: Optional database name
            
        Returns:
            Document node ID
        """
        query = """
        CREATE (d:Document {
            id: $id,
            title: $title,
            file_path: $file_path,
            content: $content,
            doc_type: $doc_type,
            created_at: datetime()
        })
        RETURN d.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            document_data,
            database
        )
        
        return records[0]["id"] if records else document_data["id"]
    
    @staticmethod
    async def create_paragraph_node(
        driver: GraphDriver,
        paragraph_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create a paragraph/chunk node
        
        Args:
            driver: Graph driver instance
            paragraph_data: Paragraph data (id, content, embedding, etc.)
            database: Optional database name
            
        Returns:
            Paragraph node ID
        """
        query = """
        CREATE (p:Paragraph {
            id: $id,
            content: $content,
            embedding: $embedding,
            chunk_index: $chunk_index,
            created_at: datetime()
        })
        RETURN p.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            paragraph_data,
            database
        )
        
        return records[0]["id"] if records else paragraph_data["id"]
    
    @staticmethod
    async def link_document_to_paragraph(
        driver: GraphDriver,
        document_id: str,
        paragraph_id: str,
        database: Optional[str] = None,
    ) -> bool:
        """
        Link document to its paragraphs
        
        Args:
            driver: Graph driver instance
            document_id: Document node ID
            paragraph_id: Paragraph node ID
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = """
        MATCH (d:Document {id: $document_id})
        MATCH (p:Paragraph {id: $paragraph_id})
        MERGE (d)-[r:HAS_PARAGRAPH]->(p)
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"document_id": document_id, "paragraph_id": paragraph_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def link_code_to_document(
        driver: GraphDriver,
        code_id: str,
        document_id: str,
        relationship_type: str = "DOCUMENTED_BY",
        metadata: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> bool:
        """
        Link code node to documentation
        
        Args:
            driver: Graph driver instance
            code_id: Code node ID (Function, Class, etc.)
            document_id: Document node ID
            relationship_type: Type of relationship
            metadata: Optional relationship metadata
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = f"""
        MATCH (code {{id: $code_id}})
        MATCH (doc:Document {{id: $document_id}})
        MERGE (code)-[r:{relationship_type}]->(doc)
        """
        
        if metadata:
            for key in metadata.keys():
                query += f"\nSET r.{key} = ${key}"
        
        query += "\nRETURN r"
        
        params = {
            "code_id": code_id,
            "document_id": document_id,
            **(metadata or {})
        }
        
        records, _, _ = await driver.execute_query(query, params, database)
        return len(records) > 0
    
    @staticmethod
    async def find_similar_paragraphs(
        driver: GraphDriver,
        embedding: List[float],
        top_k: int = 5,
        min_similarity: float = 0.7,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find paragraphs similar to given embedding (vector search)
        
        Note: Requires vector index to be created
        
        Args:
            driver: Graph driver instance
            embedding: Query embedding vector
            top_k: Number of results to return
            min_similarity: Minimum cosine similarity threshold
            database: Optional database name
            
        Returns:
            List of similar paragraphs with similarity scores
        """
        # This is a placeholder - actual implementation depends on
        # whether you're using Neo4j vector index or external vector DB
        query = """
        MATCH (p:Paragraph)
        WHERE p.embedding IS NOT NULL
        WITH p, 
             gds.similarity.cosine(p.embedding, $embedding) AS similarity
        WHERE similarity >= $min_similarity
        RETURN 
            p.id as id,
            p.content as content,
            similarity
        ORDER BY similarity DESC
        LIMIT $top_k
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {
                "embedding": embedding,
                "min_similarity": min_similarity,
                "top_k": top_k
            },
            database
        )
        
        return records
    
    @staticmethod
    async def get_document_with_paragraphs(
        driver: GraphDriver,
        document_id: str,
        database: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get document with all its paragraphs
        
        Args:
            driver: Graph driver instance
            document_id: Document node ID
            database: Optional database name
            
        Returns:
            Document data with nested paragraphs
        """
        query = """
        MATCH (d:Document {id: $document_id})
        OPTIONAL MATCH (d)-[:HAS_PARAGRAPH]->(p:Paragraph)
        RETURN 
            d.id as document_id,
            d.title as title,
            d.file_path as file_path,
            d.doc_type as doc_type,
            collect({
                id: p.id,
                content: p.content,
                chunk_index: p.chunk_index
            }) as paragraphs
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"document_id": document_id},
            database
        )
        
        return records[0] if records else {}

"""
Infrastructure Node Operations (Phase 3)

Handles operations for high-level infrastructure nodes created through
graph algorithms (Louvain clustering, community detection, etc.)
"""

from typing import Any, Dict, List, Optional
from tools.graph.core.base import GraphDriver


class InfraNodeOperations:
    """
    Operations for infrastructure/community nodes
    
    Manages creation and maintenance of higher-level organizational nodes
    that group related code components together.
    """
    
    @staticmethod
    async def run_louvain_clustering(
        driver: GraphDriver,
        label: str = "Function",
        relationship: str = "CALLS",
        weight_property: Optional[str] = None,
        min_community_size: int = 2,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run Louvain community detection algorithm
        
        Args:
            driver: Graph driver instance
            label: Node label to cluster
            relationship: Relationship type to use
            weight_property: Optional property for weighted clustering
            min_community_size: Minimum size for a valid community
            database: Optional database name
            
        Returns:
            List of communities with their members
        """
        # Note: Requires GDS library to be installed
        weight_clause = f", relationshipWeightProperty: '{weight_property}'" if weight_property else ""
        
        query = f"""
        CALL gds.louvain.stream({{
            nodeLabels: ['{label}'],
            relationshipTypes: ['{relationship}']
            {weight_clause}
        }})
        YIELD nodeId, communityId
        WITH communityId, collect(gds.util.asNode(nodeId)) as members
        WHERE size(members) >= $min_community_size
        RETURN 
            communityId,
            size(members) as size,
            [m in members | {{
                id: m.id,
                name: m.name
            }}] as members
        ORDER BY size DESC
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"min_community_size": min_community_size},
            database
        )
        
        return records
    
    @staticmethod
    async def create_infra_node(
        driver: GraphDriver,
        infra_data: Dict[str, Any],
        database: Optional[str] = None,
    ) -> str:
        """
        Create an infrastructure node (Module, Package, Layer, etc.)
        
        Args:
            driver: Graph driver instance
            infra_data: Infrastructure node metadata
            database: Optional database name
            
        Returns:
            InfraNode ID
        """
        query = """
        CREATE (i:InfraNode {
            id: $id,
            name: $name,
            type: $type,
            description: $description,
            module_path: $module_path,
            cohesion_score: $cohesion_score,
            coupling_score: $coupling_score,
            status: $status,
            created_at: datetime()
        })
        RETURN i.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            infra_data,
            database
        )
        
        return records[0]["id"] if records else infra_data["id"]
    
    @staticmethod
    async def link_node_to_infra(
        driver: GraphDriver,
        node_id: str,
        infra_id: str,
        relationship_type: str = "BELONGS_TO",
        database: Optional[str] = None,
    ) -> bool:
        """
        Link a code node to its infrastructure container
        
        Args:
            driver: Graph driver instance
            node_id: Code node ID
            infra_id: Infrastructure node ID
            relationship_type: Type of relationship
            database: Optional database name
            
        Returns:
            True if relationship created
        """
        query = f"""
        MATCH (node {{id: $node_id}})
        MATCH (infra:InfraNode {{id: $infra_id}})
        MERGE (node)-[r:{relationship_type}]->(infra)
        RETURN r
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"node_id": node_id, "infra_id": infra_id},
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def update_infra_summary(
        driver: GraphDriver,
        infra_id: str,
        summary: str,
        status: str = "summarized",
        database: Optional[str] = None,
    ) -> bool:
        """
        Update infrastructure node summary (from LLM)
        
        Args:
            driver: Graph driver instance
            infra_id: Infrastructure node ID
            summary: Generated summary
            status: New status
            database: Optional database name
            
        Returns:
            True if update successful
        """
        query = """
        MATCH (i:InfraNode {id: $infra_id})
        SET i.summary = $summary,
            i.status = $status,
            i.summary_updated_at = datetime()
        RETURN i.id as id
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {
                "infra_id": infra_id,
                "summary": summary,
                "status": status
            },
            database
        )
        
        return len(records) > 0
    
    @staticmethod
    async def get_infra_nodes_pending_summary(
        driver: GraphDriver,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get infrastructure nodes that need summary generation
        
        Args:
            driver: Graph driver instance
            limit: Maximum number of nodes to return
            database: Optional database name
            
        Returns:
            List of infra nodes with status 'pending_summary'
        """
        query = """
        MATCH (i:InfraNode)
        WHERE i.status = 'pending_summary'
        OPTIONAL MATCH (i)<-[:BELONGS_TO]-(member)
        RETURN 
            i.id as id,
            i.name as name,
            i.type as type,
            i.module_path as module_path,
            i.cohesion_score as cohesion_score,
            collect({
                id: member.id,
                name: member.name,
                summary: member.summary
            }) as members
        LIMIT $limit
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"limit": limit},
            database
        )
        
        return records
    
    @staticmethod
    async def calculate_module_metrics(
        driver: GraphDriver,
        infra_id: str,
        database: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        Calculate cohesion and coupling metrics for a module
        
        Args:
            driver: Graph driver instance
            infra_id: Infrastructure node ID
            database: Optional database name
            
        Returns:
            Dict with cohesion_score and coupling_score
        """
        query = """
        MATCH (i:InfraNode {id: $infra_id})<-[:BELONGS_TO]-(member)
        
        // Cohesion: internal connections
        OPTIONAL MATCH (member)-[internal:CALLS]->(other)
        WHERE (other)-[:BELONGS_TO]->(i)
        WITH i, member, count(internal) as internal_calls
        
        // Coupling: external connections
        OPTIONAL MATCH (member)-[external:CALLS]->(outside)
        WHERE NOT (outside)-[:BELONGS_TO]->(i)
        WITH i, 
             sum(internal_calls) as total_internal,
             count(external) as total_external,
             count(DISTINCT member) as member_count
        
        RETURN 
            CASE 
                WHEN member_count > 1 
                THEN toFloat(total_internal) / (member_count * (member_count - 1))
                ELSE 0.0 
            END as cohesion_score,
            CASE 
                WHEN total_internal + total_external > 0
                THEN toFloat(total_external) / (total_internal + total_external)
                ELSE 0.0
            END as coupling_score
        """
        
        records, _, _ = await driver.execute_query(
            query,
            {"infra_id": infra_id},
            database
        )
        
        if records:
            return {
                "cohesion_score": records[0]["cohesion_score"],
                "coupling_score": records[0]["coupling_score"]
            }
        return {"cohesion_score": 0.0, "coupling_score": 0.0}

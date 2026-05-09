"""
Abstract Base Classes for Graph Database Operations

Defines the contract that all graph database drivers must implement.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum


class GraphProvider(Enum):
    """Supported graph database providers"""
    NEO4J = "neo4j"
    KUZU = "kuzu"
    FALKORDB = "falkordb"
    NEPTUNE = "neptune"


class QueryExecutor(ABC):
    """Base interface for executing queries against a graph database"""
    
    @abstractmethod
    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        """
        Execute a query and return results
        
        Args:
            query: Cypher or equivalent query string
            parameters: Query parameters
            database: Optional database name
            
        Returns:
            Tuple of (records, header, summary)
        """
        pass
    
    @abstractmethod
    def execute_query_sync(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        """Synchronous version of execute_query"""
        pass


class GraphDriver(QueryExecutor):
    """
    Abstract base class for all graph database drivers
    
    Provides core functionality for connecting to and interacting with graph databases.
    """
    
    @property
    @abstractmethod
    def provider(self) -> GraphProvider:
        """Return the database provider type"""
        pass
    
    @abstractmethod
    def close(self) -> None:
        """Close the database connection"""
        pass
    
    @abstractmethod
    async def batch_write_nodes(
        self,
        nodes: List[Dict[str, Any]],
        label: str,
        database: Optional[str] = None,
    ) -> int:
        """
        Batch write nodes to the database
        
        Args:
            nodes: List of node data dictionaries
            label: Node label
            database: Optional database name
            
        Returns:
            Number of nodes created
        """
        pass
    
    @abstractmethod
    async def batch_write_edges(
        self,
        edges: List[Dict[str, Any]],
        relationship_type: str,
        database: Optional[str] = None,
    ) -> int:
        """
        Batch write edges/relationships to the database
        
        Args:
            edges: List of edge data (must include source_id, target_id)
            relationship_type: Type of relationship
            database: Optional database name
            
        Returns:
            Number of edges created
        """
        pass
    
    @abstractmethod
    async def verify_connection(self) -> bool:
        """
        Verify database connection is alive
        
        Returns:
            True if connection is valid
        """
        pass
    
    @abstractmethod
    async def create_indexes(
        self,
        indexes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> None:
        """
        Create indexes for better query performance
        
        Args:
            indexes: List of index definitions
            database: Optional database name
        """
        pass
    
    @abstractmethod
    async def get_node_count(
        self,
        label: Optional[str] = None,
        database: Optional[str] = None,
    ) -> int:
        """
        Get count of nodes in database
        
        Args:
            label: Optional label to filter by
            database: Optional database name
            
        Returns:
            Node count
        """
        pass
    
    @abstractmethod
    async def get_edge_count(
        self,
        relationship_type: Optional[str] = None,
        database: Optional[str] = None,
    ) -> int:
        """
        Get count of edges in database
        
        Args:
            relationship_type: Optional relationship type to filter by
            database: Optional database name
            
        Returns:
            Edge count
        """
        pass
    
    # High-level query methods (database-agnostic)
    
    @abstractmethod
    async def list_databases(self) -> List[str]:
        """
        List available databases
        
        Returns:
            List of database names
        """
        pass
    
    @abstractmethod
    async def list_relationship_types(self, database: Optional[str] = None) -> List[str]:
        """
        List all relationship types in the database
        
        Args:
            database: Optional database name
            
        Returns:
            List of relationship type names
        """
        pass
    
    @abstractmethod
    async def find_node_by_id(
        self,
        node_id: str,
        database: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Find a node by its ID
        
        Args:
            node_id: Node identifier
            database: Optional database name
            
        Returns:
            Node data or None if not found
        """
        pass
    
    @abstractmethod
    async def find_nodes_by_ids(
        self,
        node_ids: List[str],
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find multiple nodes by their IDs
        
        Args:
            node_ids: List of node identifiers
            database: Optional database name
            
        Returns:
            List of node data dictionaries
        """
        pass
    
    @abstractmethod
    async def search_functions(
        self,
        query: str,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for functions by name or qualified_name
        
        Args:
            query: Search query string
            limit: Maximum number of results
            database: Optional database name
            
        Returns:
            List of matching function nodes
        """
        pass
    
    @abstractmethod
    async def search_by_code(
        self,
        query: str,
        limit: int = 50,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search for nodes by code content
        
        Args:
            query: Search query string
            limit: Maximum number of results
            database: Optional database name
            
        Returns:
            List of matching nodes
        """
        pass
    
    @abstractmethod
    async def find_function_paths(
        self,
        start_id: str,
        end_id: str,
        relationship_types: List[str],
        max_depth: int = 8,
        database: Optional[str] = None,
    ) -> List[Any]:
        """
        Find shortest paths between two functions
        
        Args:
            start_id: Starting function ID
            end_id: Ending function ID
            relationship_types: Types of relationships to traverse
            max_depth: Maximum path depth
            database: Optional database name
            
        Returns:
            List of path objects
        """
        pass
    
    @abstractmethod
    async def query_function_subgraph(
        self,
        function_id: str,
        relationship_types: List[str],
        direction: str = "both",
        max_depth: int = 2,
        database: Optional[str] = None,
    ) -> List[Any]:
        """
        Query subgraph around a function
        
        Args:
            function_id: Central function ID
            relationship_types: Types of relationships to traverse
            direction: 'incoming', 'outgoing', or 'both'
            max_depth: Maximum traversal depth
            database: Optional database name
            
        Returns:
            List of path objects representing the subgraph
        """
        pass
    
    @abstractmethod
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
        """
        Find paths between modules (file paths)
        
        Args:
            source_modules: Source module path tokens
            target_modules: Target module path tokens
            relationship_types: Types of relationships to traverse
            max_depth: Maximum path depth
            limit: Maximum number of paths
            direction: 'out', 'in', or 'both'
            database: Optional database name
            
        Returns:
            List of path objects
        """
        pass
    
    @abstractmethod
    async def list_possible_calls(
        self,
        limit: int = 200,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        List POSSIBLE_CALLS relationships
        
        Args:
            limit: Maximum number of results
            project_id: Optional project filter
            database: Optional database name
            
        Returns:
            Tuple of (nodes, edges)
        """
        pass
    
    @abstractmethod
    async def list_symbols_by_file_path(
        self,
        file_paths: List[str],
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List symbols (functions) in files matching path tokens
        
        Args:
            file_paths: File path tokens to match
            database: Optional database name
            
        Returns:
            List of symbol nodes
        """
        pass
    
    @abstractmethod
    async def list_functions_by_class(
        self,
        class_names: List[str],
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List functions in classes matching names
        
        Args:
            class_names: Class name tokens to match
            database: Optional database name
            
        Returns:
            List of function nodes
        """
        pass
    
    @abstractmethod
    async def list_functions_by_file(
        self,
        file_path: str,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List functions in a specific file
        
        Args:
            file_path: File path token
            database: Optional database name
            
        Returns:
            List of function nodes
        """
        pass
    
    @abstractmethod
    async def list_entrypoints(
        self,
        modules: List[str],
        relationship_types: List[str],
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List entrypoint functions called from outside specified modules
        
        Args:
            modules: Module path tokens
            relationship_types: Types of relationships to consider
            database: Optional database name
            
        Returns:
            List of entrypoint function nodes
        """
        pass


class RecordParser(ABC):
    """
    Abstract base for parsing database records into Python objects
    """
    
    @staticmethod
    @abstractmethod
    def parse_node(record: Dict[str, Any], provider: GraphProvider) -> Dict[str, Any]:
        """Parse a node record into a standardized format"""
        pass
    
    @staticmethod
    @abstractmethod
    def parse_edge(record: Dict[str, Any], provider: GraphProvider) -> Dict[str, Any]:
        """Parse an edge record into a standardized format"""
        pass
    
    @staticmethod
    @abstractmethod
    def parse_path(record: Dict[str, Any], provider: GraphProvider) -> Dict[str, Any]:
        """Parse a path record into a standardized format"""
        pass

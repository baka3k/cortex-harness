"""
Factory for creating graph database drivers

Provides a centralized way to instantiate the appropriate driver
based on configuration.
"""

from typing import Any, Dict, Optional
from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.driver.neo4j_driver import Neo4jDriver


class GraphDriverFactory:
    """
    Factory class for creating graph database drivers
    """
    
    @staticmethod
    async def create_driver(
        provider: GraphProvider,
        config: Optional[Dict[str, Any]] = None,
        *,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ) -> GraphDriver:
        """
        Create a graph driver instance.

        Supports two calling conventions:

        1. Config dict (legacy)::

            driver = await GraphDriverFactory.create_driver(
                GraphProvider.NEO4J,
                {"uri": "bolt://localhost:7687", "user": "neo4j", "password": "pw"}
            )

        2. Flat keyword arguments::

            driver = await GraphDriverFactory.create_driver(
                provider=GraphProvider.NEO4J,
                uri="bolt://localhost:7687",
                user="neo4j",
                password="pw",
            )

        Args:
            provider: The database provider type
            config: Optional configuration dictionary with provider-specific settings
            uri: Neo4j URI (used when config is not provided)
            user: Neo4j username (used when config is not provided)
            password: Neo4j password (used when config is not provided)
            database: Optional database name (used when config is not provided)

        Returns:
            GraphDriver instance

        Raises:
            ValueError: If provider is not supported or required credentials are missing
        """
        # Merge flat kwargs into config dict when config is not supplied directly
        if config is None:
            config = {
                "uri": uri,
                "user": user,
                "password": password,
                "database": database,
            }

        if provider == GraphProvider.NEO4J:
            return Neo4jDriver(
                uri=config["uri"],
                user=config["user"],
                password=config["password"],
                database=config.get("database"),
            )
        elif provider == GraphProvider.KUZU:
            # Future implementation
            raise NotImplementedError("Kuzu driver not yet implemented")
        elif provider == GraphProvider.FALKORDB:
            # Future implementation
            raise NotImplementedError("FalkorDB driver not yet implemented")
        elif provider == GraphProvider.NEPTUNE:
            # Future implementation
            raise NotImplementedError("Neptune driver not yet implemented")
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    @staticmethod
    async def create_from_env(
        provider: GraphProvider,
        env_prefix: str = "NEO4J",
    ) -> GraphDriver:
        """
        Create driver from environment variables

        Args:
            provider: The database provider type
            env_prefix: Prefix for environment variables
                       (e.g., NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)

        Returns:
            GraphDriver instance
        """
        import os

        if provider == GraphProvider.NEO4J:
            config = {
                "uri": os.getenv(f"{env_prefix}_URI", "bolt://localhost:7687"),
                "user": os.getenv(f"{env_prefix}_USER", "neo4j"),
                "password": os.getenv(f"{env_prefix}_PASSWORD", ""),
                "database": os.getenv(f"{env_prefix}_DATABASE"),
            }
            return await GraphDriverFactory.create_driver(provider, config)
        else:
            raise NotImplementedError(f"Environment config not implemented for {provider}")

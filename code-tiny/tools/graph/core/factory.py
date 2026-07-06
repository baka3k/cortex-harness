"""
Factory for creating graph database drivers

Provides a centralized way to instantiate the appropriate driver
based on configuration.
"""

from typing import Any, Dict, Optional
from urllib.parse import urlparse
from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.driver.falkordb_driver import FalkorDBDriver
from tools.graph.driver.neo4j_driver import Neo4jDriver


def _is_falkordb_uri(uri: Optional[str]) -> bool:
    if not uri or "://" not in uri:
        return False
    return urlparse(uri).scheme in {"falkor", "falkors", "redis", "rediss", "unix"}


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
            if _is_falkordb_uri(config.get("uri") or config.get("url")):
                return FalkorDBDriver(
                    uri=config.get("uri") or config.get("url"),
                    user=config.get("user") or config.get("username"),
                    password=config.get("password"),
                    database=config.get("database") or config.get("graph"),
                    graph=config.get("graph"),
                    host=config.get("host"),
                    port=config.get("port"),
                    ssl=bool(config.get("ssl", False)),
                )
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
            return FalkorDBDriver(
                uri=config.get("uri") or config.get("url"),
                user=config.get("user") or config.get("username"),
                password=config.get("password"),
                database=config.get("database") or config.get("graph"),
                graph=config.get("graph"),
                host=config.get("host"),
                port=config.get("port"),
                ssl=bool(config.get("ssl", False)),
            )
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
                       (e.g., NEO4J_URI, NEO4J_USER, NEO4J_PASS)

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
        elif provider == GraphProvider.FALKORDB:
            prefix = env_prefix if env_prefix != "NEO4J" else "FALKORDB"
            config = {
                "uri": os.getenv(f"{prefix}_URI") or os.getenv(f"{prefix}_URL"),
                "host": os.getenv(f"{prefix}_HOST", "localhost"),
                "port": int(os.getenv(f"{prefix}_PORT", "6379")),
                "user": os.getenv(f"{prefix}_USER") or os.getenv(f"{prefix}_USERNAME"),
                "password": os.getenv(f"{prefix}_PASSWORD", ""),
                "database": os.getenv(f"{prefix}_GRAPH") or os.getenv(f"{prefix}_DATABASE", "neo4j"),
                "graph": os.getenv(f"{prefix}_GRAPH"),
                "ssl": os.getenv(f"{prefix}_SSL", "").lower() in {"1", "true", "yes", "on"},
            }
            return await GraphDriverFactory.create_driver(provider, config)
        else:
            raise NotImplementedError(f"Environment config not implemented for {provider}")

"""
Factory for creating graph database drivers

Provides a centralized way to instantiate the appropriate driver
based on configuration.
"""

from typing import Any, Dict, Optional
from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.driver.falkordb_driver import FalkorDBDriver
from tools.graph.driver.neo4j_driver import Neo4jDriver


class GraphDriverFactory:
    """
    Factory class for creating graph database drivers.
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
        path: Optional[Any] = None,
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

        For the docker-free cutover, FalkorDB can also be opened in local mode
        by passing ``path=...`` (or supplying ``config={"path": "..."}``).
        In that mode, ``uri/host/port/user/password/ssl`` are ignored.

        Args:
            provider: The database provider type
            config: Optional configuration dictionary with provider-specific settings
            uri: Neo4j URI (used when config is not provided)
            user: Neo4j user (used when config is not provided)
            password: Neo4j password (used when config is not provided)
            database: Optional database name (used when config is not provided)
            path: Local file path for embedded FalkorDB Lite (FalkorDB provider only)

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
                "path": path,
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
            return FalkorDBDriver(
                uri=config.get("uri") or config.get("url"),
                user=config.get("user") or config.get("username"),
                password=config.get("password"),
                database=config.get("database") or config.get("graph"),
                graph=config.get("graph"),
                host=config.get("host"),
                port=config.get("port"),
                ssl=bool(config.get("ssl", False)),
                path=config.get("path"),
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
        Create driver from environment variables.

        For FalkorDB, the local file path is read from ``FALKORDB_PATH``
        (preferred after the docker-free cutover). ``FALKORDB_URI`` /
        ``FALKORDB_HOST`` / ``FALKORDB_PORT`` are deprecated and ignored when
        a path is present.
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
            path = os.getenv("FALKORDB_PATH")
            config = {
                "uri": os.getenv(f"{prefix}_URI") or os.getenv(f"{prefix}_URL"),
                "host": os.getenv(f"{prefix}_HOST", "localhost"),
                "port": int(os.getenv(f"{prefix}_PORT", "6379")),
                "user": os.getenv(f"{prefix}_USER") or os.getenv(f"{prefix}_USERNAME"),
                "password": os.getenv(f"{prefix}_PASSWORD", ""),
                "database": os.getenv(f"{prefix}_GRAPH") or os.getenv(f"{prefix}_DATABASE", "neo4j"),
                "graph": os.getenv(f"{prefix}_GRAPH"),
                "ssl": os.getenv(f"{prefix}_SSL", "").lower() in {"1", "true", "yes", "on"},
                "path": path,
            }
            return await GraphDriverFactory.create_driver(provider, config)
        else:
            raise NotImplementedError(f"Environment config not implemented for {provider}")
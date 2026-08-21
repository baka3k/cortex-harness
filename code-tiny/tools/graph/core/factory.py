"""
Factory for creating graph database drivers

Provides a centralized way to instantiate the appropriate driver
based on configuration.
"""

from pathlib import Path
from typing import Any, Dict, Optional
from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.driver.falkordb_driver import FalkorDBDriver
from tools.graph.driver.neo4j_driver import Neo4jDriver


class GraphWritesDisabledError(RuntimeError):
    """Driver construction was attempted in an explicitly graphless process."""


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
        from tools.graph.cli import graph_writes_disabled

        if graph_writes_disabled():
            raise GraphWritesDisabledError(
                "graph driver construction is disabled by CORTEX_DISABLE_GRAPH"
            )

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
                instance_id=config.get("instance_id"),
                owner_id=config.get("owner_id"),
                additional_paths=config.get("additional_paths"),
                query_timeout_ms=config.get("query_timeout_ms"),
                _suppress_deprecation=bool(config.get("_suppress_deprecation", False)),
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
            uri = (os.getenv("FALKORDB_URI") or "").strip()
            path = os.getenv("FALKORDB_PATH")
            if not uri and not path:
                from cortex_harness.storage import resolve_storage
                path = str(resolve_storage(Path.cwd()).falkordb_code_path)
            config: Dict[str, Any] = {
                "database": os.getenv(f"{prefix}_GRAPH") or os.getenv(f"{prefix}_DATABASE", "neo4j"),
                "graph": os.getenv(f"{prefix}_GRAPH"),
                "path": path,
                "instance_id": os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
                "owner_id": os.getenv("CORTEX_STORAGE_OWNER", "code"),
            }
            if uri:
                # A remote FalkorDB server (e.g. a local Docker container on
                # platforms without FalkorDBLite) takes precedence over the
                # embedded path.
                config.update(
                    uri=uri,
                    path=None,
                    password=os.getenv("FALKORDB_PASSWORD") or None,
                    ssl=os.getenv("FALKORDB_SSL", "").strip().lower()
                    not in ("", "0", "false", "no"),
                    _suppress_deprecation=True,
                )
            return await GraphDriverFactory.create_driver(provider, config)
        else:
            raise NotImplementedError(f"Environment config not implemented for {provider}")

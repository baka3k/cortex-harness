"""Lease-free read-only graph opener (RT-3 bounded exception).

``link-tiny``'s engine reads both embedded graphs from a single process.
Every existing open path acquires the primary store's exclusive
``StorageLease`` (the ``FalkorDBDriver``/``LadybugDBDriver`` constructors),
which would deadlock against the owning MCP server or a running sync. This
module is the one sanctioned additive opener: it composes existing
lease-free primitives into a uniform read-only handle — no new lease is
acquired anywhere, and no existing call site changes behavior.

* FalkorDBLite (local): ``falkordb_driver._open_local_falkordb(path)`` —
  the same primitive the driver uses for its lease-free sibling fan-out.
* FalkorDB (remote ``uri``): server-side arbitration; nothing to lease.
* LadybugDB (local): ``LadybugDBDriver._open_database(path, read_only=True)``
  — the engine rejects mutations at the database level.

Contract: handles returned by :func:`open_read_only_graph` are for READS
ONLY. Callers (the link engine) must never mutate through them. Pinned by
``tests/test_read_only_opener_no_lease.py`` (zero ``StorageLease.acquire``
calls) mirroring ``tests/test_mcp_sibling_no_lease.py``.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


class ReadOnlyOpenError(RuntimeError):
    """Raised when a store cannot be opened read-only."""


class ReadOnlyGraph:
    """Uniform read handle over one graph in one store."""

    def __init__(
        self,
        *,
        backend: str,
        graph_name: str,
        _client: Any = None,
        _graph: Any = None,
        _ladybug_resources: Optional[tuple] = None,
    ):
        self.backend = backend
        self.graph_name = graph_name
        self._client = _client
        self._graph = _graph
        self._ladybug_resources = _ladybug_resources

    def run(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        params = dict(parameters or {})
        if self.backend == "ladybug":
            return self._run_ladybug(query, params)
        return self._run_falkordb(query, params)

    # -- FalkorDB (local embedded + remote) ---------------------------------

    def _run_falkordb(self, query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        from tools.graph.driver.falkordb_driver import (
            _normalize_falkordb_value,
            _result_key,
        )

        result = self._graph.query(query, params=params)
        keys = [_result_key(item) for item in result.header]
        return [
            {
                key: _normalize_falkordb_value(row[index])
                for index, key in enumerate(keys)
            }
            for row in result.result_set
        ]

    # -- LadybugDB -----------------------------------------------------------

    def _run_ladybug(self, query: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        from tools.graph.driver.ladybug_driver import _normalize_ladybug_value

        _handle, connection = self._ladybug_resources
        result = connection.execute(query, params)
        keys = list(result.get_column_names() or [])
        records: List[Dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            records.append(
                {key: _normalize_ladybug_value(value) for key, value in zip(keys, row)}
            )
        return records

    def close(self) -> None:
        client, resources = self._client, self._ladybug_resources
        self._client = self._graph = self._ladybug_resources = None
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - close is best-effort
                    pass
        if resources is not None:
            _handle, connection = resources
            close = getattr(connection, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass


def _falkordb_remote_client(uri: str, password: Optional[str], ssl: bool) -> Any:  # sensitive-guard:allow
    from falkordb import FalkorDB

    parsed = urlparse(uri if "://" in uri else f"falkor://{uri}")
    host = parsed.hostname or parsed.path
    port = parsed.port or 6379
    # redis-py 8 probes CLIENT MAINT_NOTIFICATIONS during connection setup;
    # OSS/compat servers reject it and the failed handshake poisons every
    # client created after a close() in the same process. The opener builds
    # its own pool with the probe disabled — read paths don't need it.
    try:
        from redis import ConnectionPool
        from redis.maint_notifications import MaintNotificationsConfig

        pool_kwargs: Dict[str, Any] = {
            "host": host,
            "port": port,
            "protocol": 2,
            "decode_responses": True,
            "maint_notifications_config": MaintNotificationsConfig(enabled=False),
        }
        if password:
            pool_kwargs["password"] = password
        if ssl:
            import redis as _redis

            pool_kwargs["connection_class"] = _redis.SSLConnection
        pool = ConnectionPool(**pool_kwargs)
        return FalkorDB(connection_pool=pool)
    except ImportError:  # older redis-py without maint notifications
        return FalkorDB(
            host=host,
            port=port,
            ssl=ssl,
            **({"password": password} if password else {}),
        )


def open_read_only_graph(
    *,
    backend: str = "falkordb",
    graph_name: str,
    path: Optional[str] = None,
    uri: Optional[str] = None,
    password: Optional[str] = None,  # sensitive-guard:allow
    ssl: bool = False,
) -> ReadOnlyGraph:
    """Open one graph read-only without acquiring any ``StorageLease``.

    Local stores take ``path``; the remote FalkorDB backend takes ``uri``.
    Neo4j is not supported by the Phase-1 linker (raise clearly instead of
    guessing a connection).
    """
    normalized_backend = str(backend or "falkordb").strip().lower()
    if not graph_name:
        raise ReadOnlyOpenError("graph_name is required")
    if normalized_backend == "neo4j":
        raise ReadOnlyOpenError(
            "link-tiny Phase 1 does not support the neo4j provider; "
            "register the project with the falkordb or ladybug provider."
        )
    if normalized_backend == "ladybug":
        if not path:
            raise ReadOnlyOpenError("ladybug backend requires path=")
        from tools.graph.driver.ladybug_driver import LadybugDBDriver

        resources = LadybugDBDriver._open_database(
            Path(path).expanduser(), read_only=True
        )
        return ReadOnlyGraph(
            backend="ladybug",
            graph_name=graph_name,
            _ladybug_resources=resources,
        )
    if normalized_backend != "falkordb":
        raise ReadOnlyOpenError(
            f"unsupported graph provider '{backend}'; "
            "expected 'falkordb' or 'ladybug'"
        )
    if uri:
        client = _falkordb_remote_client(uri, password, ssl)
    elif path:
        from tools.graph.driver.falkordb_driver import _open_local_falkordb

        client = _open_local_falkordb(Path(path).expanduser())
    else:
        raise ReadOnlyOpenError("falkordb backend requires path= or uri=")
    graph = client.select_graph(graph_name)
    return ReadOnlyGraph(
        backend="falkordb", graph_name=graph_name, _client=client, _graph=graph
    )


__all__ = [
    "ReadOnlyGraph",
    "ReadOnlyOpenError",
    "open_read_only_graph",
]

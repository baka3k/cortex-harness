"""
FalkorDB implementation of the graph driver interface.

This driver keeps the public GraphDriver contract aligned with the existing
Neo4j driver: query execution returns ``(records, keys, summary)`` where each
record is a dictionary keyed by returned column name.

Local mode (default after Phase 02 of the docker-free cutover):

    FalkorDBDriver(path="/path/to/cortex.rdb", graph="hyper_graph")

opens the embedded FalkorDBLite backend against an ``.rdb`` file. No URI,
host, port, credentials, TLS, Docker, or running Redis/FalkorDB service is
required. Network-style fields (uri, host, port, ssl, user, password) are
still accepted for one release so existing call sites keep compiling, but
they emit a deprecation warning and are ignored when a ``path`` is supplied.
"""

import asyncio
import logging
import os
import re
import threading
import time
from concurrent.futures import Future
import warnings
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.graph.core.base import GraphProvider
from tools.graph.core.cypher_driver import CypherGraphDriver
from tools.common.project_scope import prepare_project_scope_parameters
from cortex_harness.storage.lease import StorageLease
from cortex_harness.storage.admission import BoundedLane, LaneLimits


logger = logging.getLogger(__name__)


class AmbiguousWriteTimeoutError(TimeoutError):
    """A timed-out mutation may have committed and must be reconciled."""


class NativeOperationInFlightError(RuntimeError):
    """A canceled native call is still running and owns the embedded client."""


# Neo4j 5.x subquery-with-importing-variable: CALL (var) { ... }.
# FalkorDB only supports the older CALL { WITH var ... } form (variables
# imported via opening WITH clause). Rewrite the parenthesized form to the
# portable form so a single query works against both backends.
#
# Caveat: this regex matches the opening ``CALL (var) {`` and rewriter inserts
# ``WITH var`` right after the brace. Nested CALL subqueries inside the body
# are left alone — if a query has nested ``CALL (other) {`` braces inside the
# outer subquery, callers should write the query in the portable form.
_CALL_IMPORTING_SUBQUERY_RE = re.compile(
    r"CALL\s+\(([A-Za-z_][A-Za-z0-9_]*)\)\s*\{",
)
_MUTATING_CYPHER_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)


def _normalize_query(query: str) -> str:
    """Rewrite Cypher constructs FalkorDB doesn't understand.

    Rewrites Neo4j 5 ``CALL (var) { ... }`` (importing-variable subquery)
    to ``CALL { WITH var ... }`` which both FalkorDB and Neo4j accept.

    Index creation must go through ``driver.create_indexes()`` — never raw
    ``CREATE INDEX`` Cypher — so each backend uses its native API.
    """
    return _CALL_IMPORTING_SUBQUERY_RE.sub(r"CALL { WITH \1", query)


def _is_retryable_read(query: str) -> bool:
    """Retry only queries that are unambiguously read-only.

    A synchronous embedded call can fail after committing a mutation, so a
    blanket retry can duplicate ingestion effects.  Read retries remain useful
    for transient checkpoint contention.
    """
    first_token = query.lstrip().split(None, 1)[0].upper() if query.strip() else ""
    return first_token in {"MATCH", "OPTIONAL", "UNWIND", "WITH", "RETURN", "SHOW", "EXPLAIN", "PROFILE"} and not bool(
        _MUTATING_CYPHER_RE.search(query)
    )


def _is_timeout_error(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or any(
        token in message for token in ("timed out", "timeout", "query exceeded")
    )


def _cypher_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _prepare_falkordb_query(
    query: str,
    parameters: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    query = _normalize_query(query)
    params = prepare_project_scope_parameters(query, parameters)
    if "datetime()" not in query:
        return query, params

    param_name = "__falkordb_now"
    while param_name in params:
        param_name = f"_{param_name}"

    return query.replace("datetime()", f"${param_name}"), {
        **params,
        param_name: _utc_timestamp(),
    }


def _result_key(header_item: Any) -> str:
    if isinstance(header_item, (list, tuple)) and header_item:
        header_item = next(
            (item for item in header_item if isinstance(item, (str, bytes))),
            header_item[0],
        )
    if isinstance(header_item, bytes):
        return header_item.decode("utf-8")
    return str(header_item)


def _as_properties(value: Any) -> Dict[str, Any]:
    properties = getattr(value, "properties", None)
    if isinstance(properties, Mapping):
        return dict(properties)
    return {}


def _normalize_falkordb_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalize_falkordb_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_falkordb_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_falkordb_value(item) for item in value)

    if hasattr(value, "properties") and hasattr(value, "labels"):
        node = _as_properties(value)
        graph_id = getattr(value, "id", None)
        if graph_id is not None:
            node.setdefault("_graph_id", graph_id)
        return node

    if hasattr(value, "properties") and hasattr(value, "relation"):
        edge = _as_properties(value)
        edge.setdefault("_type", getattr(value, "relation", None))
        edge.setdefault(
            "_start_id",
            _normalize_falkordb_value(getattr(value, "src_node", None)),
        )
        edge.setdefault(
            "_end_id",
            _normalize_falkordb_value(getattr(value, "dest_node", None)),
        )
        return edge

    if callable(getattr(value, "nodes", None)) and callable(getattr(value, "edges", None)):
        return {
            "nodes": [_normalize_falkordb_value(node) for node in value.nodes()],
            "edges": [_normalize_falkordb_value(edge) for edge in value.edges()],
        }

    return value


def _open_local_falkordb(path: Path):
    """Open the embedded FalkorDBLite backend against *path*.

    The PyPI distribution is named ``falkordblite``, but its Python API is
    exposed through ``redislite.falkordb_client``. Keep that packaging detail
    at this boundary so callers only depend on ``FalkorDBDriver``.
    """
    try:
        from redislite.falkordb_client import FalkorDB
    except ImportError as exc:
        raise ImportError(
            "Local FalkorDB backend requires the 'falkordblite' package. "
            "Install dependencies from requirements.txt or pyproject.toml."
        ) from exc

    try:
        socket_timeout = float(os.getenv("FALKORDB_SOCKET_TIMEOUT_SECONDS", "300"))
    except ValueError as exc:
        raise ValueError("FALKORDB_SOCKET_TIMEOUT_SECONDS must be a positive number") from exc
    if socket_timeout <= 0:
        raise ValueError("FALKORDB_SOCKET_TIMEOUT_SECONDS must be a positive number")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return FalkorDB(
        str(path),
        socket_timeout=socket_timeout,
        socket_connect_timeout=min(socket_timeout, 30),
    )


class FalkorDBDriver(CypherGraphDriver):
    """
    FalkorDB graph driver.

    The class inherits provider-neutral high-level Cypher methods and owns its
    provider-specific connection, schema, discovery, full-text, and ID lookup
    behavior.

    Local mode is the supported default after the docker-free cutover. Pass
    ``path=...`` to open an embedded FalkorDBLite backend against an ``.rdb``
    file. URI/host/port/credentials/TLS are deprecated and ignored when a
    path is supplied.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        *,
        graph: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        ssl: bool = False,
        path: Optional[Any] = None,
        **kwargs: Any,
    ):
        self._uri = uri
        self._user = user
        self._password = password
        self._database = graph or database or "hyper_graph"
        self._path: Optional[Path] = Path(path).resolve() if path is not None else None
        self._storage_lease: Optional[StorageLease] = None
        self._additional_clients: List[Any] = []
        # Sibling stores are opened read-only with no application lease; this
        # list keeps the resolved paths for diagnostics / cleanup.
        self._additional_open_paths: List[Path] = []
        self._graph_clients: Dict[str, Any] = {}
        self._query_lane = BoundedLane(
            "falkordb-query", LaneLimits(concurrency=1, max_queue_items=32)
        )
        self._inflight_native_futures: set[Future[Any]] = set()
        self._native_future_lock = threading.Lock()
        self._deferred_close = False
        self._resources_closed = False
        # `_suppress_deprecation` lets the storage factory construct drivers
        # without emitting the per-call deprecation warnings (the factory is
        # the documented entry point for remote mode, so the warning would be
        # noise).
        self._suppress_deprecation = bool(kwargs.pop("_suppress_deprecation", False))
        timeout_value = kwargs.pop("query_timeout_ms", None)
        if timeout_value in {None, ""}:
            timeout_value = os.getenv("FALKORDB_QUERY_TIMEOUT_MS", "120000")
        self._query_timeout_ms = int(timeout_value)
        if self._query_timeout_ms <= 0:
            raise ValueError("FALKORDB_QUERY_TIMEOUT_MS must be a positive integer")

        # Network-style fields are deprecated. We still accept them so call
        # sites keep compiling for one release, but log + warn, and ignore
        # them when an explicit ``path`` was provided.
        if (
            self._path is not None
            and not self._suppress_deprecation
            and (any(v is not None for v in (uri, host, port, user, password)) or ssl)
        ):
            warnings.warn(
                "FalkorDBDriver: network-style arguments (uri/host/port/user/password/ssl) "
                "are deprecated and ignored when 'path' is supplied. Open a network client "
                "explicitly if you need a remote FalkorDB instance.",
                DeprecationWarning,
                stacklevel=2,
            )

        storage_instance_id = str(
            kwargs.pop("instance_id", None)
            or os.getenv("CORTEX_STORAGE_INSTANCE", "default")
        )
        storage_owner_id = str(
            kwargs.pop("owner_id", None)
            or os.getenv("CORTEX_STORAGE_OWNER", "code")
        )

        # Local mode (default): open the embedded FalkorDBLite backend.
        if self._path is not None:
            self._storage_lease = StorageLease(
                self._path,
                instance_id=storage_instance_id,
                owner_id=storage_owner_id,
                backend="falkordb",
            ).acquire()
            try:
                self._client = _open_local_falkordb(self._path)
            except Exception:
                self._storage_lease.release()
                self._storage_lease = None
                raise
            self._register_client_graphs(self._client)
            self._open_additional_local_clients(
                kwargs.pop("additional_paths", ()) or (),
                owner_id=storage_owner_id,
            )
        else:
            # Legacy network fallback. Kept for one release so existing tests
            # that construct a network-style driver without a path keep
            # working, but new code paths must supply ``path``.
            if not self._suppress_deprecation:
                warnings.warn(
                    "FalkorDBDriver opened without 'path'. Network-style usage is deprecated; "
                    "supply FALKORDB_PATH instead and re-run.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            try:
                from falkordb import FalkorDB
                import redis  # noqa: F401  # Verify the FalkorDB client dependency is installed.
            except ImportError as exc:
                raise ImportError(
                    "FalkorDB provider requires the 'falkordb' package. "
                    "Install dependencies from requirements.txt or pyproject.toml."
                ) from exc

            client_kwargs = {k: v for k, v in kwargs.items() if v is not None}
            if user:
                client_kwargs["username"] = user
            if password:
                client_kwargs["password"] = password
            client_kwargs.setdefault("socket_timeout", 120)
            client_kwargs.setdefault("socket_connect_timeout", 10)

            url = uri or client_kwargs.pop("url", None)
            if url and "://" in url:
                from urllib.parse import urlparse

                parsed = urlparse(url)
                if parsed.scheme in {"falkor", "falkors", "redis", "rediss", "unix"}:
                    client_kwargs.setdefault("protocol", 2)
                    self._client = FalkorDB.from_url(url, **client_kwargs)
                else:
                    raise ValueError(
                        "FalkorDB URI must use falkor://, falkors://, redis://, "
                        f"rediss://, or unix://; got {parsed.scheme!r}"
                    )
            else:
                if url and ":" in url and host is None:
                    parsed_host, parsed_port = url.rsplit(":", 1)
                    host = parsed_host or host
                    if parsed_port:
                        port = int(parsed_port)
                self._client = FalkorDB(
                    host=host or "localhost",
                    port=port or 6379,
                    ssl=ssl,
                    **client_kwargs,
                )

        self._graph = self._client.select_graph(self._database)

    def _register_client_graphs(self, client: Any) -> List[str]:
        names: List[str] = []
        graph_clients = getattr(self, "_graph_clients", None)
        if graph_clients is None:
            graph_clients = {}
            self._graph_clients = graph_clients
        try:
            raw_names = client.list_graphs()
        except Exception as exc:
            logger.warning("Failed to list FalkorDB graphs: %s", exc)
            return names
        for name in raw_names:
            if isinstance(name, bytes):
                try:
                    name = name.decode("utf-8")
                except UnicodeDecodeError:
                    logger.warning("Ignoring non-UTF-8 FalkorDB graph name")
                    continue
            if isinstance(name, str) and name:
                names.append(name)
                graph_clients.setdefault(name, client)
        return names

    def _open_additional_local_clients(
        self,
        paths: Any,
        *,
        owner_id: str,
    ) -> None:
        """Open sibling embedded stores for read-only fan-out.

        Sibling stores are opened without acquiring an application lease;
        the writer on the sibling instance owns its own exclusive
        ``StorageLease`` independently of this reader, so concurrent ingests
        on those instances are not blocked. Concurrent writes are
        serialized by falkordblite's append-only AOF + atomic-rename RDB
        rewrite — the reader sees either the previous complete snapshot or
        the new one, never a torn write. The primary file wins when
        duplicate graph names exist.
        """
        if self._path is None:
            return
        primary = self._path.resolve()
        seen = {primary}
        for raw_path in paths:
            candidate = Path(raw_path).expanduser().resolve()
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            try:
                client = _open_local_falkordb(candidate)
            except Exception as exc:
                logger.warning("Skipping unreadable FalkorDB instance %s: %s", candidate, exc)
                continue
            self._additional_open_paths.append(candidate)
            self._additional_clients.append(client)
            self._register_client_graphs(client)

    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.FALKORDB

    @classmethod
    def from_storage_factory(
        cls, factory: Any, graph_name: str
    ) -> "FalkorDBDriver":
        """Create a driver from a :class:`cortex_harness.storage.factory.StorageFactory`.

        Thin wrapper around ``factory.get_falkordb_driver(graph_name)`` so call
        sites that already hold a factory don't need to know whether the
        driver is local or remote. Imported lazily to avoid a hard
        ``cortex_harness`` → ``code-tiny`` cycle.
        """
        return factory.get_falkordb_driver(graph_name)

    @property
    def driver(self) -> Any:
        return self._client

    @property
    def database(self) -> str:
        return self._database

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @property
    def graph(self) -> Any:
        return self._graph

    def session(self, **kwargs: Any) -> None:
        raise NotImplementedError(
            "FalkorDBDriver does not expose Neo4j-style sessions; use execute_query instead."
        )

    def _close_resources(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        try:
            if self._client:
                try:
                    self._client.close()
                except Exception as exc:  # pragma: no cover - best-effort close
                    logger.debug("FalkorDB close() raised: %s", exc)
                logger.info("FalkorDB connection closed")
        finally:
            for client in reversed(self._additional_clients):
                try:
                    client.close()
                except Exception as exc:  # pragma: no cover - best-effort close
                    logger.debug("Additional FalkorDB close() raised: %s", exc)
            self._additional_clients.clear()
            self._additional_open_paths.clear()
            if self._storage_lease is not None:
                self._storage_lease.release()
                self._storage_lease = None

    def _native_future_finished(self, future: Future[Any]) -> None:
        with self._native_future_lock:
            self._inflight_native_futures.discard(future)
            should_close = self._deferred_close and not self._inflight_native_futures
        if should_close:
            self._close_resources()

    async def _run_in_executor(self, operation: Any, *args: Any) -> Any:
        """Run one native call without creating an interpreter-joined worker.

        ``ThreadPoolExecutor`` workers are joined by CPython during process
        shutdown even after ``shutdown(wait=False)``. A wedged embedded query
        could therefore defeat every asyncio deadline at the final exit. The
        bounded query lane already enforces one active call, so a dedicated
        daemon thread gives the same isolation without extending process life.
        """
        native_future: Future[Any] = Future()
        native_future.set_running_or_notify_cancel()
        with self._native_future_lock:
            if any(not future.done() for future in self._inflight_native_futures):
                raise NativeOperationInFlightError(
                    "previous FalkorDB native operation is still in flight; "
                    "close this driver and reconcile the ambiguous result before retrying"
                )
            self._inflight_native_futures.add(native_future)
        native_future.add_done_callback(self._native_future_finished)

        def invoke() -> None:
            try:
                result = operation(*args)
            except BaseException as exc:
                native_future.set_exception(exc)
            else:
                native_future.set_result(result)

        thread = threading.Thread(
            target=invoke,
            name="cortex-falkordb-query",
            daemon=True,
        )
        thread.start()
        wrapped = asyncio.wrap_future(native_future)
        return await asyncio.shield(wrapped)

    def close(self) -> None:
        with self._native_future_lock:
            pending = [
                future for future in self._inflight_native_futures if not future.done()
            ]
            if pending:
                self._deferred_close = True
        if pending:
            logger.warning(
                "FalkorDB close deferred while %d timed/cancelled operation(s) reconcile",
                len(pending),
            )
            return
        self._close_resources()

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        async def run() -> Tuple[List[Dict[str, Any]], List[str], Any]:
            return await self._run_in_executor(
                self.execute_query_sync, query, parameters, database
            )

        return await self._query_lane.run(run)

    def execute_query_sync(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Any]:
        graph = self._graph_for(database)
        query, params = _prepare_falkordb_query(query, parameters)

        # Retry policy:
        # - Read-only queries get a single retry on any failure.
        # - Mutations get retries only for *transient* connection-class
        #   errors (ConnectionError/OSError and redis.client subclasses).
        #   MERGE-based upserts are idempotent so re-issuing a batched
        #   ``MERGE ... SET`` after the socket dropped is safe. Timeouts on
        #   mutations stay ``AmbiguousWriteTimeoutError`` — the commit
        #   outcome is unknown and must be reconciled by the caller, never
        #   silently retried.
        # NOTE: redis.exceptions.ConnectionError is NOT a subclass of
        # Python's builtin ConnectionError — we have to import it explicitly.
        from redis.exceptions import ConnectionError as _RedisConnectionError
        from redis.exceptions import TimeoutError as _RedisTimeoutError
        transient_errors = (
            ConnectionError, OSError,
            _RedisConnectionError, _RedisTimeoutError,
        )
        max_attempts = 4
        result = None
        for attempt in range(max_attempts):
            try:
                result = graph.query(
                    query,
                    params=params,
                    timeout=self._query_timeout_ms,
                )
                break
            except Exception as exc:
                if _is_timeout_error(exc) and not _is_retryable_read(query):
                    raise AmbiguousWriteTimeoutError(
                        "FalkorDB mutation timed out; commit outcome is ambiguous and "
                        "must be reconciled before retry"
                    ) from exc
                is_transient = isinstance(exc, transient_errors)
                # Mutations only retry on transient errors; reads retry on any.
                can_retry = _is_retryable_read(query) or is_transient
                if not can_retry or attempt == max_attempts - 1:
                    raise
                backoff = min(2.0, 0.25 * (2 ** attempt))
                logger.warning(
                    "FalkorDB query failed (attempt %d/%d), retrying in %.2fs: %s",
                    attempt + 1, max_attempts, backoff, exc,
                )
                if backoff:
                    time.sleep(backoff)

        keys = [_result_key(item) for item in result.header]
        records = [
            {
                key: _normalize_falkordb_value(row[index])
                for index, key in enumerate(keys)
            }
            for row in result.result_set
        ]
        return records, keys, result

    def _graph_for(self, database: Optional[str]) -> Any:
        graph_name = database or self._database
        client = self._graph_clients.get(graph_name)
        if client is not None:
            if client is self._client and graph_name == self._database:
                return self._graph
            return client.select_graph(graph_name)
        if graph_name == self._database:
            return self._graph
        return self._client.select_graph(graph_name)

    async def create_indexes(
        self,
        indexes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> None:
        graph = self._graph_for(database)

        async def run_native(operation: Any) -> None:
            async def run() -> None:
                await self._run_in_executor(operation)

            await self._query_lane.run(run)

        for idx in indexes:
            label = idx["label"]
            prop = idx["property"]
            props = prop if isinstance(prop, list) else [prop]
            idx_type = idx.get("type", "range")
            try:
                if idx_type == "fulltext":
                    await run_native(
                        lambda graph=graph, label=label, props=props: graph.create_node_fulltext_index(
                            label, *props
                        )
                    )
                else:
                    await run_native(
                        lambda graph=graph, label=label, props=props: graph.create_node_range_index(
                            label, *props
                        )
                    )
                logger.info("Created FalkorDB %s index on %s(%s)", idx_type, label, ", ".join(props))
            except Exception as exc:
                if "already indexed" in str(exc).lower():
                    logger.debug(
                        "FalkorDB %s index already exists on %s(%s)",
                        idx_type,
                        label,
                        ", ".join(props),
                    )
                else:
                    raise RuntimeError(
                        "Failed to create FalkorDB "
                        f"{idx_type} index on {label}({', '.join(props)}): {exc}"
                    ) from exc

    async def inspect_indexes(
        self,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return FalkorDB index metadata, including operational status."""

        records, _, _ = await self.execute_query(
            "CALL db.indexes()",
            database=database,
        )
        normalized: List[Dict[str, Any]] = []
        for record in records:
            label_value = record.get("label", record.get("labelsOrTypes", ""))
            if isinstance(label_value, (list, tuple)):
                label_value = label_value[0] if label_value else ""
            properties_value = record.get("properties", record.get("property", [])) or []
            properties = (
                [properties_value]
                if isinstance(properties_value, str)
                else list(properties_value)
            )
            type_map = record.get("types") or {}
            if not properties and isinstance(type_map, Mapping):
                properties = [str(item) for item in type_map]
            common_type = str(record.get("index_type", record.get("type", ""))).casefold()
            entity_type = str(
                record.get("entity_type", record.get("entitytype", "node")) or "node"
            ).casefold()
            status = str(record.get("status", record.get("state", "")) or "").upper()

            # FalkorDB reports all indexed attributes for a label in one row.
            # They are independent single-property range indexes, not one
            # composite index, so expose one normalized record per property.
            for prop in properties:
                index_types = [common_type] if common_type else []
                if not index_types and isinstance(type_map, Mapping):
                    values = type_map.get(prop, ())
                    index_types = sorted({
                        str(item).casefold()
                        for item in (
                            values if isinstance(values, (list, tuple, set)) else [values]
                        )
                        if item
                    })
                for index_type in index_types or [""]:
                    if index_type == "btree":
                        index_type = "range"
                    normalized.append(
                        {
                            "label": str(label_value or ""),
                            "properties": [str(prop)],
                            "index_type": index_type,
                            "entity_type": entity_type,
                            "status": status,
                        }
                    )
        return normalized

    async def list_databases(self) -> List[str]:
        normalized_names: List[str] = []
        for client in (
            self._client,
            *getattr(self, "_additional_clients", ()),
        ):
            for name in self._register_client_graphs(client):
                if name not in normalized_names:
                    normalized_names.append(name)
        return normalized_names or [self._database]

    async def list_relationship_types(self, database: Optional[str] = None) -> List[str]:
        records, _, _ = await self.execute_query(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS rel_type",
            database=database,
        )
        rel_types: List[str] = []
        for record in records:
            rel_type = record.get("rel_type")
            if isinstance(rel_type, str):
                rel_upper = rel_type.upper()
                if rel_upper not in rel_types:
                    rel_types.append(rel_upper)
        return rel_types

    async def find_node_by_id(
        self,
        node_id: str,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cypher = """
        MATCH (n)
        WHERE n.id = $id
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT 1
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"id": node_id, "project_id": project_id},
            database,
        )
        node = records[0].get("n") if records else None
        if node and node.get("framework") == "servlet_jsp":
            active_records, _, _ = await self.execute_query(
                "MATCH (s:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id}) "
                "WHERE s.active_generation = $generation_id RETURN s.id AS id LIMIT 1",
                {"project_id": node.get("project_id"), "module_id": node.get("module_id"), "generation_id": node.get("generation_id")},
                database,
            )
            if not active_records:
                return None
        return node

    async def find_nodes_by_ids(
        self,
        node_ids: List[str],
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not node_ids:
            return []
        cypher = """
        MATCH (n)
        WHERE n.id IN $ids
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"ids": node_ids, "project_id": project_id},
            database,
        )
        nodes = [record.get("n") for record in records if record.get("n")]
        servlet_nodes = [n for n in nodes if n.get("framework") == "servlet_jsp"]
        if servlet_nodes:
            active_records, _, _ = await self.execute_query(
                "UNWIND $rows AS row "
                "MATCH (s:ServletJspAnalysisState {project_id: row.project_id, module_id: row.module_id}) "
                "WHERE s.active_generation = row.generation_id RETURN row.id AS id",
                {"rows": servlet_nodes},
                database,
            )
            active_ids = {str(row.get("id")) for row in active_records if row.get("id")}
            nodes = [n for n in nodes if n.get("framework") != "servlet_jsp" or str(n.get("id")) in active_ids]
        return nodes

    async def search_functions(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            return await self._fulltext_node_search("Function", query, limit, project_id, database)
        except Exception as exc:
            logger.debug("FalkorDB fulltext search_functions fallback to CONTAINS: %s", exc)

        cypher = """
        MATCH (n:Function)
        WHERE (
            toLower(n.name) CONTAINS toLower($query)
            OR toLower(coalesce(n.qualified_name, '')) CONTAINS toLower($query)
        )
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database,
        )
        return [record.get("n") for record in records if record.get("n")]

    async def search_by_code(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            return await self._fulltext_node_search("Function", query, limit, project_id, database)
        except Exception as exc:
            logger.debug("FalkorDB fulltext search_by_code fallback to CONTAINS: %s", exc)

        cypher = """
        MATCH (n)
        WHERE (
            toLower(coalesce(n.code, '')) CONTAINS toLower($query)
            OR toLower(coalesce(n.comment, '')) CONTAINS toLower($query)
            OR toLower(coalesce(n.summary, '')) CONTAINS toLower($query)
        )
          AND ($project_id IS NULL OR n.project_id_normalized = $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database,
        )
        return [record.get("n") for record in records if record.get("n")]

    async def _fulltext_node_search(
        self,
        label: str,
        query: str,
        limit: int,
        project_id: Optional[str],
        database: Optional[str],
    ) -> List[Dict[str, Any]]:
        cypher = f"""
        CALL db.idx.fulltext.queryNodes({_cypher_string(label)}, $query) YIELD node, score
        WHERE ($project_id IS NULL OR node.project_id_normalized = $project_id_normalized)
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher,
            {"query": query, "limit": limit, "project_id": project_id},
            database,
        )
        return [record.get("n") for record in records if record.get("n")]

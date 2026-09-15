"""LadybugDB implementation of the graph driver interface (embedded, local-only).

LadybugDB (the maintained successor of KùzuDB, PyPI package ``ladybug``) is an
embedded graph database: one store **file** per named graph, no server, no
named-graph multiplexing inside a single store.  This driver keeps the public
GraphDriver contract aligned with the FalkorDB driver: query execution returns
``(records, keys, summary)`` where each record is a dictionary keyed by
returned column name.

Routing contract (see ``plans/260913-1538-ladybug-graph-provider``):

* Every FalkorDB named graph maps to its own Ladybug store file
  ``<root>/<owner_role>.lbug/<graph-name>``.  ``execute_query(database=...)``
  lazily opens (write intent) or resolves (read) that store.
* ``additional_paths`` are sibling store files opened ``read_only=True``
  without an application lease — Ladybug allows many concurrent readers
  next to one writer.  A primary store wins on duplicate graph names.
* Single-writer safety comes from two layers: Ladybug's own OS file lock and
  the application-level ``StorageLease`` acquired here for the primary store.

Dialect notes (spike on ladybug 0.20.4):

* Static schema: node/rel tables must exist before writes, and properties
  must be declared.  This driver bootstraps ``CODE_GRAPH_SCHEMA`` tables on
  first write-mode open and auto-adds unknown properties via ``ALTER TABLE``
  (gated by ``CORTEX_GRAPH_AUTO_DDL``, default on, logged loudly) so
  analyzer queries written for schemaless FalkorDB keep working without
  silently dropping data.
* ``SET n = <map>`` and ``SET r = <map>`` are not supported, so batch
  writes are rewritten to per-property assignments.
* Range indexes do not exist; ART indexes are the native ordered index and
  FTS indexes come from the FTS extension.  ``create_indexes`` maps
  ``range`` → ART and ``fulltext`` → FTS so manifests stay portable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tools.graph.core.base import GraphProvider
from tools.graph.core.cypher_driver import CypherGraphDriver
from tools.graph.core.errors import (
    AmbiguousWriteTimeoutError,
    NativeOperationInFlightError,
)
from tools.graph.core.query_normalize import (
    is_mutating,
    is_retryable_read,
    normalize_call_importing_subqueries,
    rewrite_datetime_call,
)
from tools.common.project_scope import prepare_project_scope_parameters
from cortex_harness.storage.lease import StorageLease
from cortex_harness.storage.admission import BoundedLane, LaneLimits
from cortex_harness.storage.layout import ladybug_store_file_name


logger = logging.getLogger(__name__)


__all__ = [
    "AmbiguousWriteTimeoutError",
    "LadybugDriver",
    "NativeOperationInFlightError",
    "ladybug_store_file_name",
]


# Ladybug internal keys on node/rel result dicts (spike 0.20.4).
_LADYBUG_INTERNAL_KEYS = frozenset({"_ID", "_LABEL", "_SRC", "_DST"})
_LADYBUG_READ_ONLY_MISSING = "cannot create an empty database under read only mode"

# Auto-DDL error classification (exact Ladybug 0.20 binder wording).
_MISSING_PROPERTY_RE = re.compile(
    r"Cannot find property ([A-Za-z_][A-Za-z0-9_]*) for ([A-Za-z_][A-Za-z0-9_]*)"
)
_MISSING_TABLE_RE = re.compile(r"Table ([A-Za-z_][A-Za-z0-9_]*) does not exist")
# MERGE/CREATE with a rel type whose table does not exist yet surfaces this
# binder wording instead of "Table X does not exist".
_MISSING_REL_BIND_RE = re.compile(
    r"Cannot bind ([A-Za-z_][A-Za-z0-9_]*) as a relationship pattern label"
)
_PROPERTY_EXISTS_RE = re.compile(r"already has property", re.IGNORECASE)
_INDEX_EXISTS_RE = re.compile(r"already exists", re.IGNORECASE)

# Variable -> table resolution inside a query (node and rel patterns).
_REL_VAR_RE_TMPL = r"-\[\s*`?{var}`?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?"
_NODE_VAR_RE_TMPL = r"[(,]\s*`?{var}`?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?"
# Rel usage with optionally-typed endpoints (property maps allowed):
# (s:File {id: $x})-[r:CALLS]->(t:Function {id: $y})
_REL_USAGE_RE = re.compile(
    r"\(\s*`?(\w+)`?\s*:?\s*`?([A-Za-z_][A-Za-z0-9_]*)?`?[^)]*\)-\[[^\]]*?:"
    r"\s*`?([A-Za-z_][A-Za-z0-9_]*)`?[^\]]*\]->\(\s*`?(\w+)`?\s*:?\s*"
    r"`?([A-Za-z_][A-Za-z0-9_]*)?`?[^)]*\)"
)
# All node patterns, used to resolve untyped endpoint variables.
_NODE_PATTERN_RE = re.compile(r"\(\s*`?(\w+)`?\s*:\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")

_AUTO_DDL_ENV = "CORTEX_GRAPH_AUTO_DDL"

# Stores bootstrapped in this process: (resolved path) -> None.  Re-opening a
# store must not replay ~155 DDL statements every time.
_BOOTSTRAPPED_STORES: set[str] = set()


def auto_ddl_enabled() -> bool:
    """``CORTEX_GRAPH_AUTO_DDL`` defaults on; operators may fail it closed."""

    raw = (os.getenv(_AUTO_DDL_ENV) or "").strip().casefold()
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off"}


# First tokens that mutate the store.  ``is_mutating`` covers Cypher DML/DDL;
# Ladybug-only statements (COPY/INSTALL/LOAD) extend the write-intent set so
# introspection reads never silently create stores.
_WRITE_INTENT_TOKENS = frozenset(
    {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "DROP", "ALTER", "REMOVE",
     "FOREACH", "COPY", "INSTALL", "LOAD", "CHECKPOINT", "EXPORT", "IMPORT"}
)


def is_write_intent(query: str) -> bool:
    """True when the query mutates the store (Cypher or native DDL/utility)."""

    first_token = query.lstrip().split(None, 1)[0].upper() if query.strip() else ""
    return first_token in _WRITE_INTENT_TOKENS or is_mutating(query)


def _is_timeout_error(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or any(
        token in message for token in ("timed out", "timeout", "query exceeded", "interrupt")
    )


def _is_database_missing_error(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return _LADYBUG_READ_ONLY_MISSING in message


def _infer_type(value: Any) -> str:
    """Map a Python value onto a Ladybug column type."""

    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, list):
        element = next((item for item in value if item is not None), None)
        return f"{_infer_type(element)}[]" if element is not None else "STRING[]"
    return "STRING"


def _open_local_ladybug(path: Path, *, read_only: bool = False):
    """Open an embedded LadybugDB store file at *path*.

    Ladybug auto-creates the store file (never its parent directories), so
    this boundary creates the parent directory and tightens permissions on
    freshly created stores.  The PyPI package is named ``ladybug``; the
    packaging detail stays here so callers only depend on ``LadybugDriver``.
    """

    try:
        import ladybug
    except ImportError as exc:
        raise ImportError(
            "Local LadybugDB backend requires the 'ladybug' package. "
            "Install dependencies from requirements.txt or pyproject.toml."
        ) from exc

    path = Path(path).expanduser()
    created = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)

    kwargs: Dict[str, Any] = {"read_only": read_only}
    buffer_pool_raw = (os.getenv("LADYBUG_BUFFER_POOL_SIZE") or "").strip()
    if buffer_pool_raw:
        try:
            buffer_pool_size = int(buffer_pool_raw)
        except ValueError as exc:
            raise ValueError("LADYBUG_BUFFER_POOL_SIZE must be a positive integer") from exc
        if buffer_pool_size <= 0:
            raise ValueError("LADYBUG_BUFFER_POOL_SIZE must be a positive integer")
        kwargs["buffer_pool_size"] = buffer_pool_size

    try:
        return ladybug.Database(str(path), **kwargs)
    finally:
        if created and not read_only and os.name == "posix":
            try:
                os.chmod(path, 0o700)
            except OSError:  # pragma: no cover - best-effort hardening
                logger.debug("Could not tighten permissions on %s", path)


def _as_native_id(value: Any) -> Any:
    """Extract a scalar id from a Ladybug ``_ID``/``_SRC``/``_DST`` entry."""

    if isinstance(value, Mapping):
        offset = value.get("offset")
        if offset is not None:
            return offset
    return value


def _normalize_ladybug_value(value: Any) -> Any:
    """Normalize Ladybug result values into the FalkorDB-driver shape.

    Nodes become ``{props..., _label, _id}``, relationships
    ``{props..., _type, _start_id, _end_id}``, and paths
    ``{"nodes": [...], "edges": [...]}`` — matching what MCP consumers
    already receive from :class:`FalkorDBDriver`.
    """

    if isinstance(value, Mapping):
        if "_NODES" in value and "_RELS" in value:
            return {
                "nodes": [_normalize_ladybug_value(node) for node in value["_NODES"]],
                "edges": [_normalize_ladybug_value(rel) for rel in value["_RELS"]],
            }
        label = value.get("_LABEL")
        if label is not None:
            props = {
                str(key): _normalize_ladybug_value(item)
                for key, item in value.items()
                if key not in _LADYBUG_INTERNAL_KEYS
            }
            native_id = _as_native_id(value.get("_ID"))
            if "_SRC" in value:
                props.setdefault("_type", label)
                props.setdefault("_start_id", _as_native_id(value.get("_SRC")))
                props.setdefault("_end_id", _as_native_id(value.get("_DST")))
            else:
                props.setdefault("_label", label)
                props.setdefault("_id", native_id)
                props.setdefault("_graph_id", native_id)
            return props
        return {str(key): _normalize_ladybug_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_ladybug_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_ladybug_value(item) for item in value)
    return value


def _prepare_ladybug_query(
    query: str,
    parameters: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    query = normalize_call_importing_subqueries(query)
    params = prepare_project_scope_parameters(query, parameters)
    # Ladybug has no zero-argument datetime(); timestamp($param) parses the
    # bound ISO-8601 string instead.
    return rewrite_datetime_call(
        query,
        params,
        replacement="timestamp(${param})",
        param_prefix="__ladybug_now",
    )


class LadybugDriver(CypherGraphDriver):
    """Embedded LadybugDB graph driver (local-only).

    Pass ``path=...`` pointing at the primary named-graph store file
    (conventionally ``<root>/<owner_role>.lbug/<graph-name>``).  There is no
    remote mode: uri/user/password/host/port/ssl arguments are rejected.
    """

    def __init__(
        self,
        path: Any,
        *,
        graph: Optional[str] = None,
        instance_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        additional_paths: Any = (),
        query_timeout_ms: Any = None,
        read_only_siblings: bool = True,
        **kwargs: Any,
    ):
        network_kwargs = {
            name: kwargs.pop(name, None)
            for name in ("uri", "user", "password", "host", "port", "url")
        }
        if kwargs.pop("ssl", False) or any(v is not None for v in network_kwargs.values()):
            raise ValueError(
                "LadybugDriver is local-only: uri/user/password/host/port/url/ssl "
                "are not supported. Use falkordb or neo4j for remote graph servers."
            )
        if not read_only_siblings:
            raise ValueError(
                "LadybugDriver only supports read-only sibling stores; a second "
                "writer on one store file is rejected by Ladybug's file lock."
            )

        self._database = graph or "hyper_graph"
        self._path = Path(path).expanduser().resolve()
        self._storage_lease: Optional[StorageLease] = None
        self._query_lane = BoundedLane(
            "ladybug-query", LaneLimits(concurrency=1, max_queue_items=32)
        )
        self._inflight_native_futures: set[Future[Any]] = set()
        self._native_future_lock = threading.Lock()
        self._deferred_close = False
        self._resources_closed = False

        timeout_value = query_timeout_ms
        if timeout_value in {None, ""}:
            timeout_value = os.getenv("LADYBUG_QUERY_TIMEOUT_MS", "120000")
        self._query_timeout_ms = int(timeout_value)
        if self._query_timeout_ms <= 0:
            raise ValueError("LADYBUG_QUERY_TIMEOUT_MS must be a positive integer")

        storage_instance_id = str(
            instance_id or os.getenv("CORTEX_STORAGE_INSTANCE", "default")
        )
        storage_owner_id = str(owner_id or os.getenv("CORTEX_STORAGE_OWNER", "code"))

        # Canonical graph name -> connection; sibling stores keep their own
        # map keyed by store file name so duplicate names cannot shadow the
        # primary unexpectedly.
        self._connections: Dict[str, Any] = {}
        self._sibling_connections: Dict[str, Any] = {}
        self._databases: List[Any] = []
        self._primary_connection: Optional[Any] = None
        self._fts_index_names: Dict[str, Dict[str, str]] = {}

        self._storage_lease = StorageLease(
            self._path,
            instance_id=storage_instance_id,
            owner_id=storage_owner_id,
            backend="ladybug",
        ).acquire()
        try:
            database = _open_local_ladybug(self._path, read_only=False)
            connection = self._new_connection(database)
            self._databases.append(database)
            self._primary_connection = connection
            self._connections[ladybug_store_file_name(self._database)] = connection
            self._configure_timeout(connection)
            self._on_store_opened(connection, store_path=self._path, write_mode=True)
        except Exception:
            if self._storage_lease is not None:
                self._storage_lease.release()
                self._storage_lease = None
            raise
        self._open_additional_stores(additional_paths)

    # ── Store/connection management ─────────────────────────────────────────

    def _new_connection(self, database: Any) -> Any:
        import ladybug

        return ladybug.Connection(database)

    def _configure_timeout(self, connection: Any) -> None:
        setter = getattr(connection, "set_query_timeout", None)
        if setter is None:  # pragma: no cover - older wheels without timeout support
            logger.debug("Ladybug Connection has no set_query_timeout; skipping")
            return
        try:
            setter(self._query_timeout_ms)
        except Exception as exc:  # pragma: no cover - best-effort configuration
            logger.debug("Ladybug set_query_timeout(%s) failed: %s", self._query_timeout_ms, exc)

    def _store_path_for(self, graph_name: str) -> Path:
        return self._path.parent / ladybug_store_file_name(graph_name)

    def _open_store(self, path: Path, *, read_only: bool) -> Any:
        database = _open_local_ladybug(path, read_only=read_only)
        self._databases.append(database)
        connection = self._new_connection(database)
        self._configure_timeout(connection)
        return connection

    def _open_additional_stores(self, paths: Any) -> None:
        """Open sibling store files read-only, without an application lease.

        The writer on the sibling instance owns its own exclusive
        ``StorageLease``; Ladybug's read-only mode tolerates concurrent
        readers beside an active writer.  Primary stores win when duplicate
        graph names exist.
        """

        primary = self._path
        seen = {primary.resolve()}
        for raw_path in paths or ():
            candidate = Path(raw_path).expanduser().resolve()
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            store_name = candidate.name
            if store_name in self._sibling_connections:
                continue
            try:
                connection = self._open_store(candidate, read_only=True)
            except Exception as exc:
                logger.warning("Skipping unreadable LadybugDB store %s: %s", candidate, exc)
                continue
            self._sibling_connections[store_name] = connection

    def _connection_for(self, database: Optional[str], *, query: str = "") -> Any:
        """Resolve the connection for *database*, honoring write intent.

        Read queries against a store that exists nowhere raise a
        ``database does not exist`` error (classified by
        ``provider_contract.is_database_not_found_error``) instead of
        silently creating an empty store file — introspection fan-out
        relies on that classification.  Write-intent queries lazily create
        this instance's own store beside the primary and bootstrap its
        schema; a read-only sibling connection is never reused for writes.
        Connections are keyed by store file name so case variants of a
        graph name share one entry.
        """

        graph_name = database or self._database
        store_name = ladybug_store_file_name(graph_name)

        if graph_name == self._database:
            return self._primary_connection

        if is_write_intent(query):
            cached = self._connections.get(store_name)
            if cached is not None and not self._is_sibling_connection(cached):
                return cached
            path = self._store_path_for(graph_name)
            connection = self._open_store(path, read_only=False)
            self._connections[store_name] = connection
            self._on_store_opened(connection, store_path=path, write_mode=True)
            return connection

        connection = self._connections.get(store_name)
        if connection is not None:
            return connection
        sibling = self._sibling_connections.get(store_name)
        if sibling is not None:
            self._connections[store_name] = sibling
            return sibling

        raise RuntimeError(
            f"database does not exist: {graph_name} (no LadybugDB store at "
            f"{self._store_path_for(graph_name)})"
        )

    def _is_sibling_connection(self, connection: Any) -> bool:
        return any(
            connection is sibling
            for sibling in self._sibling_connections.values()
        )

    def _on_store_opened(
        self, connection: Any, *, store_path: Path, write_mode: bool
    ) -> None:
        """Bootstrap ``CODE_GRAPH_SCHEMA`` tables on first write-mode open.

        Ladybug has a static schema, so a schemaless FalkorDB-style workload
        would fail on its very first ``CREATE (:Function {...})``.  The
        bootstrap creates the manifest's node tables (shared identity/scope
        columns plus the ``id`` primary key) and registered rel tables up
        front; unknown extra properties are added later by the gated
        auto-DDL path.  ``IF NOT EXISTS`` keeps this idempotent, and the
        per-process cache — keyed by the opened store path so lazily routed
        secondary stores bootstrap too — avoids replaying ~155 DDL
        statements on every open.
        """

        if not write_mode:
            return
        cache_key = str(Path(store_path).resolve())
        if cache_key in _BOOTSTRAPPED_STORES:
            return
        _BOOTSTRAPPED_STORES.add(cache_key)
        try:
            self._bootstrap_schema(connection)
        except Exception as exc:
            _BOOTSTRAPPED_STORES.discard(cache_key)
            raise RuntimeError(
                f"LadybugDB schema bootstrap failed for {store_path}: {exc}"
            ) from exc

    def _bootstrap_schema(self, connection: Any) -> None:
        from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA

        # Shared identity/scope columns written by essentially every
        # analyzer payload; pre-declaring them keeps first writes down to a
        # handful of auto-DDL ALTERs instead of one per descriptive property.
        base_columns = (
            "id STRING, name STRING, file_path STRING, path STRING, "
            "qualified_name STRING, project_id STRING, project_id_normalized STRING, "
            "PRIMARY KEY(id)"
        )
        base_props = {
            "id",
            "name",
            "file_path",
            "path",
            "qualified_name",
            "project_id",
            "project_id_normalized",
        }
        # Index property columns must exist before ensure_schema creates the
        # required indexes: CREATE INDEX does not run through the auto-DDL
        # retry, so a missing column there fails the whole schema preflight.
        index_props: Dict[str, set] = {}
        for index in CODE_GRAPH_SCHEMA.indexes:
            index_props.setdefault(index.label, set()).update(index.properties)
        for label in sorted(index_props):
            extra = sorted(index_props[label] - base_props)
            columns = base_columns
            if extra:
                rendered = ", ".join(f"`{prop}` STRING" for prop in extra)
                columns = base_columns.replace(
                    "PRIMARY KEY(id)", f"{rendered}, PRIMARY KEY(id)"
                )
            connection.execute(
                f"CREATE NODE TABLE IF NOT EXISTS `{label}` ({columns})",
                {},
            )
            self._alter_missing_index_columns(connection, label, extra)
        for name, source_labels, target_labels, _required in CODE_GRAPH_SCHEMA.relationship_types:
            pairs = ", ".join(
                f"FROM `{src}` TO `{dst}`"
                for src in source_labels
                for dst in target_labels
            )
            if not pairs:
                continue
            try:
                connection.execute(
                    f"CREATE REL TABLE IF NOT EXISTS `{name}` ({pairs})", {}
                )
            except Exception as exc:
                # A rel table whose endpoints were registered differently by
                # an older bootstrap must not wedge every future open.
                logger.warning(
                    "LadybugDB rel table bootstrap for %s failed: %s", name, exc
                )
        logger.info(
            "LadybugDB schema bootstrap complete: %d node labels, %d registered rel types (%s)",
            len({index.label for index in CODE_GRAPH_SCHEMA.indexes}),
            len(CODE_GRAPH_SCHEMA.relationship_types),
            self._path,
        )

    def _alter_missing_index_columns(
        self, connection: Any, label: str, expected_props: Sequence[str]
    ) -> None:
        """Add manifest index columns missing from an older bootstrap's table.

        Fresh stores declare these columns at CREATE time; stores created by
        earlier bootstraps only carry the base columns, so heal them via the
        same loud ALTER path as auto-DDL instead of failing schema preflight.
        """

        if not expected_props:
            return
        try:
            result = connection.execute(f"CALL TABLE_INFO('{label}') RETURN *", {})
            existing: set = set()
            while result.has_next():
                existing.add(str(result.get_next()[1]))
        except Exception as exc:
            logger.warning(
                "LadybugDB TABLE_INFO(%s) failed; skipping index-column heal: %s", label, exc
            )
            return
        for prop in expected_props:
            if prop in existing:
                continue
            try:
                connection.execute(
                    f"ALTER TABLE `{label}` ADD `{prop}` STRING", {}
                )
                logger.warning(
                    "LadybugDB bootstrap healed missing index column: "
                    "ALTER TABLE `%s` ADD `%s` STRING",
                    label,
                    prop,
                )
            except Exception as exc:
                if "already exists" in str(exc).casefold():
                    continue
                logger.warning(
                    "LadybugDB index-column heal for %s(%s) failed: %s", label, prop, exc
                )

    # ── Introspection helpers ───────────────────────────────────────────────

    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.LADYBUG

    @property
    def driver(self) -> Any:
        return self._primary_connection

    @property
    def database(self) -> str:
        return self._database

    @property
    def path(self) -> Path:
        return self._path

    @property
    def graph(self) -> Any:
        return self._primary_connection

    @classmethod
    def from_storage_factory(cls, factory: Any, graph_name: str) -> "LadybugDriver":
        """Create a driver from a :class:`cortex_harness.storage.factory.StorageFactory`.

        Thin wrapper around ``factory.get_ladybug_driver(graph_name)`` so call
        sites that already hold a factory don't need to know whether the
        driver is local or remote.  Imported lazily to avoid a hard
        ``cortex_harness`` → ``code-tiny`` cycle.
        """
        return factory.get_ladybug_driver(graph_name)

    # ── Native-call execution plumbing ──────────────────────────────────────

    def _close_resources(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        try:
            connections = list(self._connections.values()) + list(
                self._sibling_connections.values()
            )
            if self._primary_connection is not None:
                connections.append(self._primary_connection)
            for connection in connections:
                try:
                    closer = getattr(connection, "close", None)
                    if closer is not None:
                        closer()
                except Exception as exc:  # pragma: no cover - best-effort close
                    logger.debug("Ladybug connection close() raised: %s", exc)
            for database in reversed(self._databases):
                try:
                    closer = getattr(database, "close", None)
                    if closer is not None:
                        closer()
                except Exception as exc:  # pragma: no cover - best-effort close
                    logger.debug("Ladybug close() raised: %s", exc)
            self._databases.clear()
            self._connections.clear()
            self._sibling_connections.clear()
            self._primary_connection = None
        finally:
            logger.info("LadybugDB connection closed")
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
                    "previous LadybugDB native operation is still in flight; "
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
            name="cortex-ladybug-query",
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
                "LadybugDB close deferred while %d timed/cancelled operation(s) reconcile",
                len(pending),
            )
            return
        self._close_resources()

    # ── Query execution ─────────────────────────────────────────────────────

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
        prepared_query, params = _prepare_ladybug_query(query, parameters)
        connection = self._connection_for(database, query=prepared_query)

        # Retry policy mirrors FalkorDBDriver:
        # - Read-only queries get retries on any failure.
        # - Mutations get retries only for transient connection-class errors
        #   (ConnectionError/OSError); timeouts on mutations stay
        #   ``AmbiguousWriteTimeoutError`` — the commit outcome is unknown
        #   and must be reconciled by the caller, never silently retried.
        # Before each retry after a static-schema failure, the gated
        # auto-DDL path may ALTER the store and re-issue the query once.
        transient_errors = (ConnectionError, OSError)
        # Error-retry budget (transient/read failures) is separate from the
        # auto-DDL budget: one write can legitimately reveal several missing
        # properties in sequence, and burning the retry loop on them would
        # leave the query un-executed.
        max_attempts = 4
        auto_ddl_attempted: set[str] = set()
        max_auto_ddl = 64
        result = None
        error_attempts = 0
        while True:
            try:
                result = connection.execute(prepared_query, params)
                break
            except Exception as exc:
                if _is_database_missing_error(exc):
                    raise
                ddl_statement = self._auto_ddl_statement(
                    exc, prepared_query, params, database
                )
                if (
                    ddl_statement is not None
                    and ddl_statement not in auto_ddl_attempted
                    and len(auto_ddl_attempted) < max_auto_ddl
                ):
                    auto_ddl_attempted.add(ddl_statement)
                    logger.warning(
                        "LadybugDB auto-DDL on %s: %s (query: %.200s)",
                        database or self._database,
                        ddl_statement,
                        prepared_query,
                    )
                    try:
                        connection.execute(ddl_statement, {})
                    except Exception as ddl_exc:
                        raise RuntimeError(
                            f"auto-DDL statement failed: {ddl_statement}"
                        ) from exc
                    # Ladybug caches prepared statements per query string;
                    # a cached plan compiled before the DDL would keep
                    # failing with the stale binder error on retry.
                    self._invalidate_query_cache(connection)
                    continue
                if _is_timeout_error(exc) and not is_retryable_read(prepared_query):
                    raise AmbiguousWriteTimeoutError(
                        "LadybugDB mutation timed out; commit outcome is ambiguous and "
                        "must be reconciled before retry"
                    ) from exc
                is_transient = isinstance(exc, transient_errors)
                # Mutations only retry on transient errors; reads retry on any.
                can_retry = is_retryable_read(prepared_query) or is_transient
                if not can_retry or error_attempts >= max_attempts - 1:
                    raise
                backoff = min(2.0, 0.25 * (2 ** error_attempts))
                error_attempts += 1
                logger.warning(
                    "LadybugDB query failed (attempt %d/%d), retrying in %.2fs: %s",
                    error_attempts, max_attempts, backoff, exc,
                )
                if backoff:
                    time.sleep(backoff)

        if result is None:  # pragma: no cover - defensive, loop guarantees set
            raise RuntimeError("LadybugDB query returned no result")
        if isinstance(result, list):
            if len(result) != 1:
                raise ValueError(
                    "LadybugDB multi-statement queries are not supported; "
                    f"got {len(result)} results"
                )
            result = result[0]

        keys = [str(item) for item in result.get_column_names()]
        records: List[Dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            records.append(
                {
                    key: _normalize_ladybug_value(row[index])
                    for index, key in enumerate(keys)
                }
            )
        return records, keys, result

    # ── Auto-DDL (static-schema compatibility) ──────────────────────────────

    @staticmethod
    def _invalidate_query_cache(connection: Any) -> None:
        """Drop Ladybug's implicit prepared-statement cache.

        Plans compiled before a DDL statement stay poisoned (the binder
        rejects the new property/table even though the catalog now has it),
        so any cache the connected wheel exposes must be cleared after
        auto-DDL.  Guarded with getattr so newer/older wheels without this
        private detail still work — they would surface as retry failures
        with the original binder error instead.
        """

        cache = getattr(connection, "_pybind_implicit_prepared_cache", None)
        if isinstance(cache, dict) and cache:
            cache.clear()

    def _auto_ddl_statement(
        self,
        exc: Exception,
        query: str,
        params: Mapping[str, Any],
        database: Optional[str],
    ) -> Optional[str]:
        """Return one ALTER/CREATE statement that unblocks a static-schema
        failure, or ``None`` when the error is not schema-shaped.

        The flag ``CORTEX_GRAPH_AUTO_DDL`` (default on) gates this path; when
        off, schema errors propagate fail-closed.  Every applied statement is
        logged loudly by the caller — silent data loss is the worst case this
        mechanism exists to prevent.
        """

        message = str(exc)
        if not auto_ddl_enabled():
            return None

        missing_property = _MISSING_PROPERTY_RE.search(message)
        if missing_property is not None:
            prop, var = missing_property.group(1), missing_property.group(2)
            table = self._resolve_table_for_var(query, var)
            if table is None:
                return None
            column_type = self._infer_property_type(query, var, prop, params)
            return f"ALTER TABLE `{table}` ADD `{prop}` {column_type}"

        missing_table = _MISSING_TABLE_RE.search(message)
        if missing_table is not None:
            table = missing_table.group(1)
            return self._missing_table_statement(query, table)

        missing_rel = _MISSING_REL_BIND_RE.search(message)
        if missing_rel is not None:
            return self._missing_rel_table_statement(query, missing_rel.group(1))

        return None

    @staticmethod
    def _resolve_table_for_var(query: str, var: str) -> Optional[str]:
        pattern = re.compile(
            _REL_VAR_RE_TMPL.format(var=re.escape(var)), re.IGNORECASE
        )
        match = pattern.search(query)
        if match is not None:
            return match.group(1)
        pattern = re.compile(
            _NODE_VAR_RE_TMPL.format(var=re.escape(var)), re.IGNORECASE
        )
        match = pattern.search(query)
        if match is not None:
            return match.group(1)
        return None

    @staticmethod
    def _infer_property_type(
        query: str,
        var: str,
        prop: str,
        params: Mapping[str, Any],
    ) -> str:
        """Infer a Ladybug column type from the assignment expression."""

        assignment = re.search(
            rf"`?{re.escape(var)}`?\.`?{re.escape(prop)}`?\s*=\s*([^\s,;]+)",
            query,
            re.IGNORECASE,
        )
        if assignment is None:
            return "STRING"
        expression = assignment.group(1)
        if expression.lower().startswith("timestamp("):
            return "TIMESTAMP"
        if expression.startswith("$"):
            value = params.get(expression[1:])
            if value is None:
                return "STRING"
            return _infer_type(value)
        if expression.lower() in {"true", "false"}:
            return "BOOL"
        if expression.startswith("'") or expression.startswith('"'):
            return "STRING"
        if expression.startswith("["):
            return "STRING[]"
        try:
            if "." in expression:
                float(expression)
                return "DOUBLE"
            int(expression)
            return "INT64"
        except ValueError:
            return "STRING"

    def _missing_table_statement(self, query: str, table: str) -> Optional[str]:
        """DDL that unblocks a missing-table binder error, or ``None``.

        Node tables always get the standard identity key.  Rel tables need
        endpoint labels: they come from the manifest registry when registered,
        otherwise from the query's own typed patterns.  Unresolvable
        endpoints fail closed rather than guessing a schema.
        """

        from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA

        manifest_labels = {index.label for index in CODE_GRAPH_SCHEMA.indexes}
        registry = {
            name: (sources, targets)
            for name, sources, targets, _required in CODE_GRAPH_SCHEMA.relationship_types
        }
        if table in registry:
            sources, targets = registry[table]
            pairs = ", ".join(f"FROM `{src}` TO `{dst}`" for src in sources for dst in targets)
            return f"CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"

        pairs = self._resolve_rel_pairs_from_query(query, table)
        if pairs:
            return f"CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
        if table in manifest_labels:
            return (
                f"CREATE NODE TABLE IF NOT EXISTS `{table}` (id STRING, PRIMARY KEY(id))"
            )
        # Unknown label: treat as a node table with the standard identity key
        # (matches analyzer labels that predate the manifest registry).
        return f"CREATE NODE TABLE IF NOT EXISTS `{table}` (id STRING, PRIMARY KEY(id))"

    def _missing_rel_table_statement(self, query: str, table: str) -> Optional[str]:
        """DDL for a missing rel type surfaced by a MERGE/CREATE bind error.

        Unlike the MATCH path Ladybug does not say "Table X does not exist"
        here, and endpoint labels must come from the query itself.  When the
        endpoints cannot be resolved the original error propagates
        (fail-closed): guessing a rel-table schema would silently misshape
        the store.
        """

        from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA

        registry = {
            name: (sources, targets)
            for name, sources, targets, _required in CODE_GRAPH_SCHEMA.relationship_types
        }
        if table in registry:
            sources, targets = registry[table]
            pairs = ", ".join(f"FROM `{src}` TO `{dst}`" for src in sources for dst in targets)
            return f"CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
        pairs = self._resolve_rel_pairs_from_query(query, table)
        if pairs:
            return f"CREATE REL TABLE IF NOT EXISTS `{table}` ({pairs})"
        return None

    @staticmethod
    def _resolve_rel_pairs_from_query(query: str, table: str) -> str:
        """Return ``FROM x TO y, ...`` pairs for *table* found in *query*.

        Endpoint labels may be attached inline (``(f:Function)-[:CALLS]->``)
        or bound to variables typed elsewhere in the query.
        """

        var_labels: Dict[str, str] = {
            var: label for var, label in _NODE_PATTERN_RE.findall(query)
        }
        endpoints: set[tuple[str, str]] = set()
        for match in _REL_USAGE_RE.finditer(query):
            src_var, src_label, rel_type, dst_var, dst_label = match.groups()
            if rel_type.casefold() != table.casefold():
                continue
            source_label = src_label or var_labels.get(src_var, "")
            target_label = dst_label or var_labels.get(dst_var, "")
            if source_label and target_label:
                endpoints.add((source_label, target_label))
        return ", ".join(f"FROM `{src}` TO `{dst}`" for src, dst in sorted(endpoints))

    # ── Batch writes (Ladybug has no ``SET n = <map>``) ─────────────────────

    async def batch_write_nodes(
        self,
        nodes: List[Dict[str, Any]],
        label: str,
        database: Optional[str] = None,
    ) -> int:
        if not nodes:
            return 0

        from tools.graph.schema.manifest import validate_cypher_identifier

        validate_cypher_identifier(label, kind="label")
        property_names = sorted(
            {key for node in nodes for key in node.keys() if key != "id"}
        )
        for prop in property_names:
            validate_cypher_identifier(prop, kind="property")
        assignments = ", ".join(f"n.`{prop}` = node.`{prop}`" for prop in property_names)
        query = f"UNWIND $nodes AS node CREATE (n:{label} {{id: node.id}})"
        if assignments:
            query += f" SET {assignments}"
        query += " RETURN count(n) as count"
        records, _, _ = await self.execute_query(query, {"nodes": nodes}, database)
        return records[0]["count"] if records else 0

    async def batch_write_edges(
        self,
        edges: List[Dict[str, Any]],
        relationship_type: str,
        source_label: str,
        target_label: str,
        database: Optional[str] = None,
    ) -> int:
        if not edges:
            return 0

        from tools.graph.schema.manifest import validate_cypher_identifier

        rel_type = validate_cypher_identifier(relationship_type, kind="relationship type")
        source_node_label = validate_cypher_identifier(source_label, kind="source label")
        target_node_label = validate_cypher_identifier(target_label, kind="target label")

        # Flatten edge payloads: Ladybug rejects ``SET r = <map>`` and nested
        # map member access, so each property becomes a top-level binding.
        property_names = sorted(
            {
                key
                for edge in edges
                for key in (edge.get("properties") or {})
                if key not in {"source_id", "target_id"}
            }
        )
        for prop in property_names:
            validate_cypher_identifier(prop, kind="property")
        rows = []
        for edge in edges:
            row = {
                "source_id": edge.get("source_id"),
                "target_id": edge.get("target_id"),
            }
            for prop in property_names:
                row[prop] = (edge.get("properties") or {}).get(prop)
            rows.append(row)

        assignments = ", ".join(f"r.`{prop}` = edge.`{prop}`" for prop in property_names)
        query = (
            f"UNWIND $edges AS edge "
            f"MATCH (source:{source_node_label} {{id: edge.source_id}}) "
            f"MATCH (target:{target_node_label} {{id: edge.target_id}}) "
            f"MERGE (source)-[r:{rel_type}]->(target)"
        )
        if assignments:
            query += f" SET {assignments}"
        query += " RETURN count(r) as count"
        records, _, _ = await self.execute_query(query, {"edges": rows}, database)
        return records[0]["count"] if records else 0

    # ── Bulk load (COPY FROM fast path) ─────────────────────────────────────

    @staticmethod
    def _ladybug_type_from_dtype(dtype: Any) -> str:
        name = str(dtype).casefold()
        if "int" in name:
            return "INT64"
        if "float" in name:
            return "DOUBLE"
        if "bool" in name:
            return "BOOL"
        if "datetime" in name:
            return "TIMESTAMP"
        return "STRING"

    def _bulk_table_statement(self, table: str, frame: Any) -> str:
        columns = [str(column) for column in frame.columns]
        if "id" not in columns:
            raise ValueError(
                f"bulk_load requires an 'id' column in the frame for table {table!r}"
            )
        definitions = [
            f"`{column}` {self._ladybug_type_from_dtype(frame[column].dtype)}"
            for column in columns
            if column != "id"
        ]
        rendered = ", ".join(["id STRING", *definitions, "PRIMARY KEY(id)"])
        return f"CREATE NODE TABLE IF NOT EXISTS `{table}` ({rendered})"

    def bulk_load(
        self,
        table: str,
        frame: Any,
        *,
        database: Optional[str] = None,
        ignore_errors: bool = True,
    ) -> Dict[str, Any]:
        """Bulk-insert rows via ``COPY <table> FROM <DataFrame>``.

        Semantics (verified on 0.20.4): rows whose primary key already
        exists are **skipped**, never updated — deduplicate by PK and prefer
        ``execute_query`` MERGE upserts for incremental syncs.  Requires
        pandas (or a registered Arrow-backed frame).  The target table is
        created from the frame's dtypes when missing.  Returns the row count
        plus the number of skipped rows reported by ``show_warnings()``.
        """

        from tools.graph.schema.manifest import validate_cypher_identifier

        validate_cypher_identifier(table, kind="table")
        connection = self._connection_for(database, query="COPY")
        self._ensure_fts_free_ddl(connection, self._bulk_table_statement(table, frame))
        options = "(ignore_errors=true)" if ignore_errors else ""
        copy_query = f"COPY `{table}` FROM $frame {options}"
        result = connection.execute(copy_query, {"frame": frame})
        inserted = int(result.get_num_tuples())
        skipped = 0
        try:
            warnings_result = connection.execute("CALL show_warnings() RETURN *", {})
            for row in warnings_result.get_all():
                message = str(row[1]) if len(row) > 1 else ""
                if "duplicated primary key" in message.casefold():
                    skipped += 1
        except Exception as exc:  # pragma: no cover - best-effort accounting
            logger.debug("LadybugDB show_warnings failed: %s", exc)
        return {"inserted": inserted, "skipped": skipped}

    def _ensure_fts_free_ddl(self, connection: Any, statement: str) -> None:
        """Run one DDL statement idempotently, ignoring already-exists."""

        try:
            connection.execute(statement, {})
        except Exception as exc:
            if not _INDEX_EXISTS_RE.search(str(exc)) and not _PROPERTY_EXISTS_RE.search(
                str(exc)
            ):
                raise
        self._invalidate_query_cache(connection)

    # ── Introspection API ───────────────────────────────────────────────────

    async def list_databases(self) -> List[str]:
        """Enumerate named graphs: the primary plus sibling store files."""

        names: List[str] = [self._database]
        for store_name in self._sibling_connections:
            if store_name not in names:
                names.append(store_name)
        return names

    async def list_relationship_types(self, database: Optional[str] = None) -> List[str]:
        records, _, _ = await self.execute_query(
            "CALL show_tables() RETURN *",
            database=database,
        )
        rel_types: List[str] = []
        for record in records:
            table_type = str(record.get("type", "")).upper()
            name = record.get("name")
            if table_type == "REL" and isinstance(name, str) and name:
                rel_upper = name.upper()
                if rel_upper not in rel_types:
                    rel_types.append(rel_upper)
        return rel_types

    async def list_labels(self, database: Optional[str] = None) -> List[str]:
        """List node table (label) names via ``show_tables()``."""

        records, _, _ = await self.execute_query(
            "CALL show_tables() RETURN *",
            database=database,
        )
        labels: List[str] = []
        for record in records:
            table_type = str(record.get("type", "")).upper()
            name = record.get("name")
            if table_type == "NODE" and isinstance(name, str) and name:
                if name not in labels:
                    labels.append(name)
        return labels

    # ── Index / full-text management ────────────────────────────────────────

    async def create_indexes(
        self,
        indexes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> None:
        """Create ART indexes for ``range`` entries and FTS indexes for
        ``fulltext`` entries.

        Ladybug 0.20 has no generic secondary HASH indexes (primary key
        excepted), so ``range`` requests become ART indexes — the native
        ordered index.  The auto-created ``_PK`` HASH index already covers
        ``id`` lookups; creating an ART index on ``id`` would be redundant
        but harmless, so manifests may request either way.
        """

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
                        lambda label=label, props=props: self._create_fts_index_sync(
                            label, props, database=database
                        )
                    )
                else:
                    await run_native(
                        lambda label=label, props=props: self._create_art_index_sync(
                            label, props, database=database
                        )
                    )
                logger.info(
                    "Created LadybugDB %s index on %s(%s)", idx_type, label, ", ".join(props)
                )
            except Exception as exc:
                if _INDEX_EXISTS_RE.search(str(exc)):
                    logger.debug(
                        "LadybugDB %s index already exists on %s(%s)",
                        idx_type,
                        label,
                        ", ".join(props),
                    )
                else:
                    raise RuntimeError(
                        "Failed to create LadybugDB "
                        f"{idx_type} index on {label}({', '.join(props)}): {exc}"
                    ) from exc

    def _create_art_index_sync(self, label: str, props: List[str], *, database: Optional[str]) -> None:
        from tools.graph.schema.manifest import validate_cypher_identifier

        validate_cypher_identifier(label, kind="label")
        for prop in props:
            validate_cypher_identifier(prop, kind="property")
        connection = self._connection_for(database, query="CREATE INDEX")
        index_name = self._index_name(label, props)
        columns = ", ".join(f"t.`{prop}`" for prop in props)
        connection.execute(
            f"CREATE ART INDEX `{index_name}` FOR (t:`{label}`) ON ({columns})",
            {},
        )

    def _ensure_fts_extension(self, connection: Any) -> None:
        connection.execute("INSTALL FTS", {})
        connection.execute("LOAD EXTENSION FTS", {})

    def _create_fts_index_sync(self, label: str, props: List[str], *, database: Optional[str]) -> None:
        from tools.graph.schema.manifest import validate_cypher_identifier

        validate_cypher_identifier(label, kind="label")
        for prop in props:
            validate_cypher_identifier(prop, kind="property")
        connection = self._connection_for(database, query="CREATE INDEX")
        index_name = self._index_name(label, props)
        prop_list = ", ".join(f"'{prop}'" for prop in props)
        self._ensure_fts_extension(connection)
        connection.execute(
            f"CALL CREATE_FTS_INDEX('{label}', '{index_name}', [{prop_list}])",
            {},
        )
        self._fts_index_names.setdefault(database or self._database, {})[label] = index_name

    async def _fulltext_node_search(
        self,
        label: str,
        query: str,
        limit: int,
        project_id: Optional[str],
        database: Optional[str],
    ) -> List[Dict[str, Any]]:
        fts_index = await self._resolve_fts_index_name(label, database)
        cypher = f"""
        CALL QUERY_FTS_INDEX('{label}', '{fts_index}', $query)
        YIELD node, score
        WHERE ($project_id IS NULL OR node.project_id_normalized STARTS WITH $project_id_normalized)
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

    async def _resolve_fts_index_name(self, label: str, database: Optional[str]) -> str:
        """Return the FTS index name for *label*, discovering it from the
        catalog when this process did not create it."""

        known = self._fts_index_names.get(database or self._database, {})
        if label in known:
            return known[label]
        records, _, _ = await self.execute_query(
            "CALL show_indexes() RETURN *",
            database=database,
        )
        for record in records:
            if (
                str(record.get("table_name", "")) == label
                and str(record.get("index_type", "")).casefold() == "fts"
            ):
                name = str(record.get("index_name", ""))
                if name:
                    self._fts_index_names.setdefault(database or self._database, {})[label] = name
                    return name
        raise RuntimeError(
            f"no LadybugDB FTS index exists for label {label}; "
            "create one via driver.create_indexes()"
        )

    async def fulltext_query_nodes(
        self,
        *,
        index_name: str,
        labels: List[str],
        query: str,
        project_id: Optional[str] = None,
        limit: int = 50,
        extra_where: str = "",
        extra_params: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Ladybug native full-text query (FTS extension).

        ``index_name`` is the FalkorDB-style index identifier; Ladybug
        instead resolves the FTS index created for the first label in
        ``labels`` (FTS indexes are per-table here).  Property-only
        predicates in ``extra_where`` are honored; Cypher label predicates
        are unnecessary because the index already scopes one table.
        """

        if not labels:
            raise ValueError("ladybug fulltext_query_nodes requires a label")
        fts_index = await self._resolve_fts_index_name(labels[0], database)
        predicates = [
            "($project_id IS NULL OR node.project_id_normalized STARTS WITH $project_id_normalized)"
        ]
        if extra_where:
            predicates.append(f"({extra_where})")
        where_clause = " AND ".join(predicates)
        cypher = f"""
        CALL QUERY_FTS_INDEX('{labels[0]}', '{fts_index}', $query)
        YIELD node, score
        WHERE {where_clause}
        RETURN node AS n
        ORDER BY score DESC
        LIMIT $limit
        """
        params: Dict[str, Any] = {"query": query, "project_id": project_id, "limit": int(limit)}
        params.update(extra_params or {})
        records, _, _ = await self.execute_query(cypher, params, database)
        return [record.get("n") for record in records if record.get("n")]

    @staticmethod
    def _index_name(label: str, props: List[str]) -> str:
        raw = f"{label}_{'_'.join(props)}".lower()
        sanitized = re.sub(r"[^a-z0-9_]", "_", raw)
        return f"idx_{sanitized}"

    async def inspect_indexes(
        self,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return normalized index metadata for schema readiness verification.

        Rows come from ``CALL show_indexes() RETURN *``: the auto-created
        ``_PK`` HASH index maps onto ``range`` over the primary key, ART
        rows map onto ``range``, and FTS rows map onto ``fulltext`` — the
        vocabulary ``schema.preflight`` already expects.
        """

        records, _, _ = await self.execute_query(
            "CALL show_indexes() RETURN *",
            database=database,
        )
        normalized: List[Dict[str, Any]] = []
        for record in records:
            table_name = str(record.get("table_name", ""))
            index_name = str(record.get("index_name", ""))
            index_type = str(record.get("index_type", "")).casefold()
            properties_value = record.get("property_names") or []
            properties = (
                [properties_value]
                if isinstance(properties_value, str)
                else [str(item) for item in properties_value]
            )
            if index_type == "fts":
                mapped_type = "fulltext"
            elif index_type == "art":
                mapped_type = "range"
            elif index_name == "_PK":
                mapped_type = "range"
            else:
                mapped_type = index_type
            for prop in properties:
                normalized.append(
                    {
                        "label": table_name,
                        "properties": [prop],
                        "index_type": mapped_type,
                        "entity_type": "node",
                        "status": "ONLINE",
                    }
                )
        return normalized

"""LadybugDB implementation of the graph driver interface.

LadybugDB (``pip install ladybug``, the archived KuzuDB successor) is an
embedded columnar graph database.  The driver opens one catalog per
``.lbdb`` file in-process — no server, Docker, or credentials are required —
and adapts the portable Cypher written by the shared writer layer to the
LadybugDB dialect.

Dialect adaptation (verified in the Phase 0 spike — see
``docs/plans/260916-ladybugdb-graph-provider/phase-00-spike-report.md``):

* schema-first: node/rel tables are created from
  ``tools.graph.schema.ladybug_schema`` before the first mutation, with a
  JSON ``_properties`` spill column for heterogeneous writer payloads;
* ``SET n += row`` / ``SET r += coalesce(row.props, {})`` / ``SET p += $map``
  are rewritten to explicit typed-column SETs plus the spill column;
* ``CALL { WITH row MATCH ... RETURN v LIMIT 1 }`` cardinality guards are
  flattened to plain ``MATCH`` (unmatched rows simply drop the MERGE);
* ``FOREACH (x IN xs | DETACH DELETE x)`` becomes ``UNWIND ... DETACH DELETE``;
* ``datetime()`` is substituted with an ISO-string parameter;
* ``[t IN $list | toLower(t)]`` list comprehensions are replaced by the
  parameter itself, lowercased client-side;
* parameters named after reserved words (``$end``) are renamed;
* ``labels(n)[0]`` becomes ``label(n)``.

The standard project-scope predicate
``($project_id IS NULL OR n.project_id_normalized STARTS WITH ...)``
executes natively — LadybugDB supports ``STARTS WITH`` and parameters, so
query rules R1-R6 keep byte-identical Cypher.
"""

import asyncio
import json
import logging
import os
import re
import threading
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.graph.core.base import GraphProvider
from tools.graph.core.cypher_driver import CypherGraphDriver
from tools.common.project_scope import prepare_project_scope_parameters
from tools.graph.schema import ladybug_schema
from tools.graph.schema.ladybug_schema import SPILL_PROPERTY
from tools.graph.schema.manifest import validate_cypher_identifier
from cortex_harness.storage.lease import StorageLease
from cortex_harness.storage.admission import BoundedLane, LaneLimits


logger = logging.getLogger(__name__)


class NativeOperationInFlightError(RuntimeError):
    """A canceled native call is still running and owns the embedded client."""


# Parameters named after Cypher reserved words fail to parse (spike finding:
# ``$end`` breaks the whole statement), so they are renamed transparently.
_RESERVED_PARAM_WORDS = frozenset(
    {
        "and", "as", "asc", "ascending", "by", "call", "case", "contains",
        "create", "delete", "desc", "descending", "detach", "distinct",
        "else", "end", "exists", "foreach", "in", "is", "limit", "match",
        "merge", "none", "not", "optional", "or", "order", "remove",
        "return", "set", "skip", "start", "then", "union", "unwind", "when",
        "where", "with", "yield",
    }
)

_CALL_GUARD_RE = re.compile(
    r"CALL\s*\{\s*WITH\s+(\w+)\s+MATCH\s+(?P<body>.*?)\s+RETURN\s+\w+\s+LIMIT\s+1\s*\}",
    re.DOTALL,
)
_LIST_COMPREHENSION_RE = re.compile(
    r"\[\s*(?P<item>\w+)\s+IN\s+\$(?P<param>\w+)\s*\|\s*toLower\(\s*(?P=item)\s*\)\s*\]"
)
_SET_MERGE_PROPS_RE = re.compile(
    r"SET\s+(?P<var>\w+)\s*\+=\s*(?:coalesce\(\s*)?(?P<row_var>\w+)\.(?P<field>\w+)\s*(?:,\s*\{\}\s*\))?"
)
_SET_MERGE_PARAM_RE = re.compile(r"SET\s+(?P<var>\w+)\s*\+=\s*\$(?P<param>\w+)")
_SET_MERGE_VAR_RE = re.compile(
    r"SET\s+(?P<var>\w+)\s*\+=\s*(?P<row_var>\w+)(?![\w.])"
)
_FOREACH_DETACH_RE = re.compile(
    r"FOREACH\s*\(\s*(?P<item>\w+)\s+IN\s+(?P<source>\w+)\s*\|\s*DETACH\s+DELETE\s+(?P=item)\s*\)"
)
# Canonical journal/writer cleanup idiom:
#   WITH [keep, ] collect([DISTINCT] VAR) AS LIST
#   FOREACH (item IN LIST | DETACH DELETE item)
#   RETURN size(LIST) AS count   |   RETURN count(*) AS count
# LadybugDB cannot DELETE an UNWIND-bound variable, so the idiom is folded
# back into a direct ``DETACH DELETE VAR`` on the MATCH-bound variable.
_FOREACH_COLLECT_RE = re.compile(
    r"WITH\s+(?:(?P<keeps>(?:\w+\s*,\s*)+))?collect\(\s*(?:DISTINCT\s+)?(?P<var>\w+)\s*\)\s+AS\s+(?P<list>\w+)\s*"
    r"FOREACH\s*\(\s*(?P<item>\w+)\s+IN\s+(?P=list)\s*\|\s*DETACH\s+DELETE\s+(?P=item)\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_LABELS_INDEX_RE = re.compile(r"\blabels\((?P<var>\w+)\)\s*\[\s*0\s*\]")
# openCypher label tests inside WHERE — LadybugDB has no ``n:Label`` predicate
# syntax, so they become label() comparisons. Only the OR-chain and
# ``label AND expr`` forms are rewritten; bare ``(n:Label)`` stays untouched
# because it is also the node-pattern syntax of MATCH/MERGE.
_LABEL_TEST_CHAIN_RE = re.compile(
    r"\(\s*(?P<var>\w+)\s*:\s*(?P<first>[A-Za-z_]\w*)"
    r"(?P<rest>(?:\s+OR\s+\w+\s*:\s*[A-Za-z_]\w*)+)\s*\)"
)
_LABEL_TEST_AND_RE = re.compile(
    r"\(\s*(?P<var>\w+)\s*:\s*(?P<label>[A-Za-z_]\w*)\s+AND\s+(?P<body>[^)]+)\)"
)
_PARAM_RE = re.compile(r"\$(\w+)")
_UNWIND_RE = re.compile(r"UNWIND\s+\$(\w+)\s+AS\s+(\w+)")
_VAR_LABEL_RE = re.compile(r"\(\s*(\w+)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")
_REL_VAR_RE = re.compile(r"\[\s*(?:(\w+)\s*)?:\s*([A-Za-z_][A-Za-z0-9_]*)[^\]]*\]")
_MERGE_NODE_RE = re.compile(r"(?:MERGE|CREATE)\s*\(\s*(?:\w+)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")
_MERGE_EDGE_RE = re.compile(
    r"MERGE\s*\(\s*(\w+)[^)]*\)\s*-\s*\[\s*\w*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)[^\]]*\]\s*->\s*\(\s*(\w+)"
)
_MUTATING_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|ALTER|FOREACH)\b", re.IGNORECASE
)
_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'")


def _rewrite_foreach_collect_detach(query: str) -> str:
    """Fold ``WITH collect(x) AS l FOREACH (i IN l | DETACH DELETE i)`` back
    into a direct ``DETACH DELETE x`` on the MATCH-bound variable.

    Also repairs the trailing RETURN: ``size(l)`` becomes ``count(x)`` and,
    when the WITH kept grouping variables (``WITH row, collect(...)``),
    ``count(*)`` becomes ``count(DISTINCT row)`` so the returned count keeps
    counting source rows rather than row×node pairs.
    """

    matches = list(_FOREACH_COLLECT_RE.finditer(query))
    if not matches:
        return query
    for match in reversed(matches):
        var = match.group("var")
        list_name = match.group("list")
        keeps = [
            keep.strip()
            for keep in (match.group("keeps") or "").split(",")
            if keep.strip()
        ]
        replacement = f"DETACH DELETE {var}"
        tail = query[match.end():]
        tail = tail.replace(f"size({list_name})", f"count({var})", 1)
        if keeps:
            tail = tail.replace("count(*)", f"count(DISTINCT {keeps[0]})", 1)
        query = query[: match.start()] + replacement + tail
    return query


def _rewrite_label_tests(query: str) -> str:
    """Convert openCypher label-test predicates to label() comparisons."""

    def _chain(match: re.Match) -> str:
        labels = [match.group("first")] + [
            label
            for _var, label in re.findall(
                r"(\w+)\s*:\s*([A-Za-z_]\w*)", match.group("rest")
            )
        ]
        quoted = ", ".join(f"'{label}'" for label in labels)
        return f"label({match.group('var')}) IN [{quoted}]"

    def _with_and(match: re.Match) -> str:
        return (
            f"(label({match.group('var')}) = '{match.group('label')}' "
            f"AND {match.group('body')})"
        )

    def mapper(code: str) -> str:
        code = _LABEL_TEST_CHAIN_RE.sub(_chain, code)
        return _LABEL_TEST_AND_RE.sub(_with_and, code)

    return _map_code_segments(query, mapper)


_SET_LABEL_ITEM_LEAD_RE = re.compile(r"\bSET\s+\w+\s*:\s*[A-Za-z_]\w*\s*,\s*")
_SET_LABEL_ITEM_TAIL_RE = re.compile(r",\s*\w+\s*:\s*[A-Za-z_]\w*(?=\s*,|$)", re.MULTILINE)
# Neo4j admin/dictionary introspection → Kuzu catalog equivalents.
_SHOW_REL_TYPES_CALL_RE = re.compile(
    r"CALL\s+db\.relationshipTypes\(\)\s+YIELD\s+relationshipType\s+"
    r"RETURN\s+relationshipType\s+AS\s+(\w+)",
    re.IGNORECASE,
)
_SHOW_REL_TYPES_SHOW_RE = re.compile(
    r"SHOW\s+RELATIONSHIP\s+TYPES\s+YIELD\s+relationshipType\s+"
    r"RETURN\s+relationshipType\s+AS\s+(\w+)",
    re.IGNORECASE,
)
_SHOW_NODE_LABELS_RE = re.compile(
    r"SHOW\s+NODE\s+LABELS\s+YIELD\s+label\s+RETURN\s+label",
    re.IGNORECASE,
)
_SHOW_DATABASES_RE = re.compile(r"SHOW\s+DATABASES", re.IGNORECASE)
# Kuzu has no filter comprehension: ``[t IN $xs WHERE expr]`` becomes
# ``list_filter($xs, t -> expr)``.
_LIST_COMPREHENSION_WHERE_RE = re.compile(
    r"\[\s*(?P<item>\w+)\s+IN\s+\$(?P<param>\w+)\s+WHERE\s+(?P<body>[^\]]+?)\s*\]"
)


def _rewrite_admin_introspection(query: str) -> str:
    """Map Neo4j SHOW/dictionary procedures onto Kuzu catalog queries."""

    def _rel_types(match: re.Match) -> str:
        alias = match.group(1)
        return f"MATCH ()-[__r]->() RETURN DISTINCT label(__r) AS {alias}"

    query = _SHOW_REL_TYPES_CALL_RE.sub(_rel_types, query)
    query = _SHOW_REL_TYPES_SHOW_RE.sub(_rel_types, query)
    query = _SHOW_NODE_LABELS_RE.sub(
        "MATCH (__n) RETURN DISTINCT label(__n) AS label", query
    )
    query = _SHOW_DATABASES_RE.sub(
        "CALL SHOW_TABLES() RETURN DISTINCT `database name` AS name", query
    )
    return query


def _rewrite_filter_comprehensions(query: str) -> str:
    """Convert ``[t IN $xs WHERE cond]`` to ``list_filter($xs, t -> cond)``."""

    def repl(match: re.Match) -> str:
        return (
            f"list_filter(${match.group('param')}, "
            f"{match.group('item')} -> {match.group('body')})"
        )

    return _LIST_COMPREHENSION_WHERE_RE.sub(repl, query)


def _strip_set_label_items(query: str) -> str:
    """Drop ``SET var:Label`` label-assignment items.

    Kuzu has no label assignment in SET; writers use them as idempotent
    no-ops after a labeled MERGE (``SET p:Package, p.name = ...``). Property
    assignments in the same SET clause are preserved.
    """

    query = _SET_LABEL_ITEM_LEAD_RE.sub("SET ", query)
    return _SET_LABEL_ITEM_TAIL_RE.sub("", query)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _split_literals(query: str) -> List[Tuple[bool, str]]:
    """Split a query into (is_literal, text) segments, literals untouched."""

    segments: List[Tuple[bool, str]] = []
    position = 0
    for match in _LITERAL_RE.finditer(query):
        if match.start() > position:
            segments.append((False, query[position:match.start()]))
        segments.append((True, match.group(0)))
        position = match.end()
    if position < len(query):
        segments.append((False, query[position:]))
    return segments


def _map_code_segments(query: str, mapper) -> str:
    return "".join(text if literal else mapper(text) for literal, text in _split_literals(query))


def _coerce_column(column: str, value: Any) -> Any:
    """Coerce a Python value to the declared LadybugDB column type."""

    if value is None:
        return None
    kind = ladybug_schema.column_type(column)
    if kind == "STRING[]":
        if isinstance(value, str):
            text = value.strip()
            if text[:1] == "[":
                try:
                    value = json.loads(text)
                except ValueError:
                    return []
            else:
                return [value] if text else []
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return [str(value)]
    if kind == "STRING":
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, default=str)
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return str(value)
        return str(value)
    if kind == "INT64":
        return int(value)
    if kind == "DOUBLE":
        return float(value)
    if kind == "BOOL":
        return bool(value)
    return value


def _cast_row_ref(row_var: str, column: str) -> str:
    """Reference ``row_var.column`` with a cast for non-STRING columns.

    ``None`` values inside a struct parameter are typed as STRING by the
    binder, so ``SET n.end_line = row.end_line`` fails against an INT64
    column; an explicit CAST keeps NULL NULL and accepts the real types.
    """

    kind = ladybug_schema.column_type(column)
    ref = f"{row_var}.`{column}`"
    if kind == "STRING":
        return ref
    return f"CAST({ref} AS {kind})"


def _json_columns_for(label: Optional[str]) -> frozenset[str]:
    # JSON round-trip columns are decided by property name, not label.
    from tools.graph.schema.ladybug_schema import _JSON_PROPERTIES

    return _JSON_PROPERTIES


def _decode_json_column(key: str, value: Any) -> Any:
    if (
        key in _json_columns_for(None)
        and isinstance(value, str)
        and value[:1] in ("[", "{")
    ):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _normalize_ladybug_value(value: Any) -> Any:
    """Map LadybugDB result values to FalkorDB-compatible record shapes."""

    if isinstance(value, list):
        return [_normalize_ladybug_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_ladybug_value(item) for item in value)
    if not isinstance(value, dict):
        return value

    if "_NODES" in value and "_RELS" in value:
        return {
            "nodes": [_normalize_ladybug_value(node) for node in value["_NODES"]],
            "edges": [_normalize_ladybug_value(rel) for rel in value["_RELS"]],
        }
    if "_SRC" in value and "_DST" in value:
        rel: Dict[str, Any] = {
            "_type": value.get("_LABEL"),
            "_start_id": value.get("_SRC"),
            "_end_id": value.get("_DST"),
        }
        for key, item in value.items():
            if key in {"_SRC", "_DST", "_ID", "_LABEL"}:
                continue
            if key == SPILL_PROPERTY:
                spill = _parse_spill(item)
                for spill_key, spill_value in spill.items():
                    rel.setdefault(spill_key, _decode_json_column(spill_key, spill_value))
            else:
                rel[key] = _decode_json_column(key, item)
        return rel
    if "_ID" in value and "_LABEL" in value:
        node: Dict[str, Any] = {}
        for key, item in value.items():
            if key in {"_ID", "_LABEL"}:
                continue
            if key == SPILL_PROPERTY:
                spill = _parse_spill(item)
                for spill_key, spill_value in spill.items():
                    node.setdefault(spill_key, _decode_json_column(spill_key, spill_value))
            else:
                node[key] = _decode_json_column(key, item)
        return node

    return {
        key: _normalize_ladybug_value(item) if isinstance(item, (dict, list, tuple)) else item
        for key, item in value.items()
    }


def _parse_spill(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _lower_param(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_lower_param(item) for item in value]
    if isinstance(value, str):
        return value.lower()
    return value


def _unify_row_shapes(rows: List[Any]) -> None:
    """Give every dict in a batch the same keys (missing -> None).

    LadybugDB binds a list of dicts as ``LIST(STRUCT(...))`` and rejects
    heterogeneous shapes inside one parameter, while the schema-less writer
    layer happily emits optional keys per row.
    """

    dict_rows = [row for row in rows if isinstance(row, dict)]
    if len(dict_rows) < 2:
        return
    keys: List[str] = []
    for row in dict_rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    for row in dict_rows:
        for key in keys:
            row.setdefault(key, None)


def _safe_coerce(field: str, value: Any) -> Any:
    """Coerce to the column type, downgrading unconvertible values to NULL.

    Writers occasionally feed enum-ish strings into numeric columns
    (``confidence: 'high'``); failing the whole batch would lose every row,
    so the offending value alone is dropped.
    """

    if value is None:
        return None
    kind = ladybug_schema.column_type(field)
    if kind in {"INT64", "DOUBLE", "BOOL"}:
        try:
            return _coerce_column(field, value)
        except (TypeError, ValueError):
            return None
    return _coerce_column(field, value)


class LadybugDBDriver(CypherGraphDriver):
    """Embedded LadybugDB graph driver (one catalog per ``.lbdb`` file)."""

    def __init__(
        self,
        path: Optional[Any] = None,
        database: Optional[str] = None,
        *,
        instance_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        additional_paths: Any = (),
        **kwargs: Any,
    ):
        if path is None:
            raise ValueError(
                "LadybugDB is embedded-only: pass path=... (or set LADYBUG_PATH)."
            )
        self._database = database or "hyper_graph"
        self._path = Path(path).expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_lease: Optional[StorageLease] = StorageLease(
            self._path,
            instance_id=str(instance_id or os.getenv("CORTEX_STORAGE_INSTANCE", "default")),
            owner_id=str(owner_id or os.getenv("CORTEX_STORAGE_OWNER", "code")),
            backend="ladybug",
        ).acquire()
        self._additional_connections: Dict[str, Any] = {}
        self._additional_open_paths: List[Path] = []
        self._query_lane = BoundedLane(
            "ladybug-query", LaneLimits(concurrency=1, max_queue_items=32)
        )
        self._inflight_native_futures: set[Future[Any]] = set()
        self._native_future_lock = threading.Lock()
        self._deferred_close = False
        self._resources_closed = False
        # RLock: schema self-healing acquires the lock from inside the
        # execute critical section on the same thread.
        self._execute_lock = threading.RLock()
        self._ensured_node_tables: set[str] = set()
        self._ensured_rel_pairs: Dict[str, set[Tuple[str, str]]] = {}
        self._universal_columns_cache: Optional[frozenset] = None
        self._catalog_aligned_once: bool = False
        self._virtual_indexes: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
        self._prepared_cache_warned = False

        try:
            self._database_handle, self._conn = self._open_database(self._path, read_only=False)
            self._set_query_timeout()
        except Exception:
            self._storage_lease.release()
            self._storage_lease = None
            raise
        self._open_additional_connections(additional_paths or ())

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    @staticmethod
    def _open_database(path: Path, *, read_only: bool) -> Tuple[Any, Any]:
        try:
            import ladybug as _ladybug
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "LadybugDB provider requires the 'ladybug' package. "
                "Install it with: pip install 'cortex-harness[ladybug]' "
                "(or pip install ladybug==0.20.4)."
            ) from exc
        try:
            handle = _ladybug.Database(str(path), read_only=read_only)
        except RuntimeError as exc:
            # A hard-killed process can leave a partially written WAL record.
            # Replay once more tolerating the corrupt tail: intact records are
            # applied and the WAL is truncated, instead of bricking the
            # database until someone deletes files by hand.
            if not read_only and "wal" in str(exc).lower():
                logger.warning(
                    "LadybugDB WAL replay failed for %s (%s); retrying with "
                    "tolerant replay to recover committed records",
                    path,
                    exc,
                )
                handle = _ladybug.Database(
                    str(path), read_only=False, throw_on_wal_replay_failure=False
                )
            else:
                raise
        return handle, _ladybug.Connection(handle)

    def _set_query_timeout(self) -> None:
        try:
            timeout_ms = int(os.getenv("LADYBUG_QUERY_TIMEOUT_MS", "120000"))
        except ValueError as exc:
            raise ValueError("LADYBUG_QUERY_TIMEOUT_MS must be a positive integer") from exc
        if timeout_ms <= 0:
            raise ValueError("LADYBUG_QUERY_TIMEOUT_MS must be a positive integer")
        setter = getattr(self._conn, "set_query_timeout", None)
        if callable(setter):
            setter(timeout_ms)

    def _open_additional_connections(self, paths: Any) -> None:
        """Open sibling instance catalogs read-only for cross-instance fan-out."""

        seen = {self._path}
        for raw_path in paths:
            candidate = Path(raw_path).expanduser().resolve()
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            name = self._sibling_name(candidate)
            if name in self._additional_connections or name == self._database:
                continue
            try:
                handle, conn = self._open_database(candidate, read_only=True)
            except Exception as exc:
                logger.warning("Skipping unreadable LadybugDB instance %s: %s", candidate, exc)
                continue
            self._additional_open_paths.append(candidate)
            self._additional_connections[name] = conn

    @staticmethod
    def _sibling_name(path: Path) -> str:
        # <data-root>/v1/instances/<instance>/ladybug/<owner>/data.lbdb
        try:
            owner = path.parent.name
            instance = path.parent.parent.parent.name
            return f"{instance}:{owner}"
        except Exception:
            return path.stem

    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.LADYBUG

    @property
    def driver(self) -> Any:
        return self._database_handle

    @property
    def database(self) -> str:
        return self._database

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def session(self, **kwargs: Any) -> None:
        raise NotImplementedError(
            "LadybugDBDriver does not expose Neo4j-style sessions; use execute_query instead."
        )

    def _close_resources(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        try:
            for conn in self._additional_connections.values():
                try:
                    conn.close()
                except Exception as exc:  # pragma: no cover - best-effort close
                    logger.debug("Additional LadybugDB close() raised: %s", exc)
            self._additional_connections.clear()
            self._additional_open_paths.clear()
            try:
                self._conn.close()
            except Exception as exc:  # pragma: no cover - best-effort close
                logger.debug("LadybugDB connection close() raised: %s", exc)
            try:
                self._database_handle.close()
            except Exception as exc:  # pragma: no cover - best-effort close
                logger.debug("LadybugDB database close() raised: %s", exc)
            logger.info("LadybugDB connection closed")
        finally:
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
        """Run one native call on a daemon thread (see FalkorDBDriver note)."""

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
            except BaseException as exc:  # noqa: BLE001 - forwarded to the future
                native_future.set_exception(exc)
            else:
                native_future.set_result(result)

        thread = threading.Thread(target=invoke, name="cortex-ladybug-query", daemon=True)
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

    # ------------------------------------------------------------------ #
    # Query preparation (dialect rewrites)
    # ------------------------------------------------------------------ #

    def _top_up_unwind_row_fields(
        self, query: str, params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """Ensure every ``row.<field>`` referenced by the query exists on every
        row of the matching UNWIND parameter.

        Struct binding rejects field access (``Invalid struct field name``)
        when a referenced field is absent from the bound struct, even when the
        SET target tolerates NULL. Writers routinely reference optional
        fields (JVM rows never carry ``scope_name``), so missing references
        are filled with NULL before binding.
        """

        unwind_map = {var: param for param, var in _UNWIND_RE.findall(query)}
        if not unwind_map:
            return query, params
        fields_by_var: Dict[str, set] = {}

        def _collect_fields(code: str) -> str:
            for match in re.finditer(r"\b(\w+)\.(\w+)\b", code):
                var, field = match.group(1), match.group(2)
                if var in unwind_map:
                    fields_by_var.setdefault(var, set()).add(field)
            return code

        _map_code_segments(query, _collect_fields)
        for row_var, fields in fields_by_var.items():
            rows = params.get(unwind_map[row_var])
            if not isinstance(rows, list):
                continue
            dict_rows = [row for row in rows if isinstance(row, dict)]
            if not dict_rows:
                continue
            # Canonical field order: every existing key plus every referenced
            # field. Struct conversion also requires identical field ORDER
            # across rows, not just identical fields.
            ordered_fields: List[str] = []
            for row in dict_rows:
                for key in row:
                    if key not in ordered_fields:
                        ordered_fields.append(key)
            for field in sorted(fields):
                if field not in ordered_fields:
                    ordered_fields.append(field)
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    rows[index] = {
                        field: _safe_coerce(field, row.get(field))
                        for field in ordered_fields
                    }
        return query, params

    def _cast_unwind_refs(self, query: str) -> str:
        """Rewrite ``row.<field>`` references with backticks and CASTs.

        Backticks keep keyword-named fields (``row.group``) parseable, and
        typed CASTs keep NULL struct fields (bound as STRING) assignable to
        INT64/DOUBLE/BOOL columns.
        """

        unwind_vars = {var for _, var in _UNWIND_RE.findall(query)}
        if not unwind_vars:
            return query

        def _cast_refs(code: str) -> str:
            def _replace(match: re.Match) -> str:
                var, field = match.group(1), match.group(2)
                if var not in unwind_vars:
                    return match.group(0)
                kind = ladybug_schema.column_type(field)
                if kind == "STRING":
                    return f"{var}.`{field}`"
                if re.search(r"CAST\(\s*$", code[: match.start()], re.IGNORECASE):
                    return match.group(0)
                return f"CAST({var}.`{field}` AS {kind})"

            return re.sub(r"\b(\w+)\.(\w+)\b", _replace, code)

        return _map_code_segments(query, _cast_refs)

    def _live_universal_columns(self) -> frozenset:
        """Columns present on EVERY live node table (generic ``(n)`` scope).

        Generic-node property access binds against the intersection of all
        existing tables; a property outside that set cannot be referenced at
        all. Cached until the next DDL invalidation.
        """

        if self._universal_columns_cache is not None:
            return self._universal_columns_cache
        universal: Optional[set] = None
        for label in self._list_node_table_names(self._conn):
            cols = self._existing_node_columns_on(self._conn, label)
            if not cols:
                continue
            universal = set(cols) if universal is None else (universal & cols)
        self._universal_columns_cache = frozenset(universal or ())
        return self._universal_columns_cache

    def _null_out_nonuniversal_props(self, query: str) -> str:
        """Neutralize ``n.<prop>`` references that cannot bind on a generic
        node variable.

        A generic ``MATCH (n)`` requires every referenced property to exist
        on every node table. In plain expressions such properties become
        NULL (predicates degrade to no-match); inside a SET clause the whole
        assignment item is dropped, because ``SET NULL = ...`` is invalid
        syntax and the property cannot be persisted anyway. Labeled
        variables and UNWIND row structs are left untouched (their bindings
        are typed and already validated).
        """

        var_labels = dict(_VAR_LABEL_RE.findall(query))
        unwind_vars = {var for _, var in _UNWIND_RE.findall(query)}
        rel_vars = {var for var, _rel in _REL_VAR_RE.findall(query)}
        universal = self._live_universal_columns()
        drop_props: set = set()
        null_props: set = set()
        clause = ""
        clauses = ("WHERE", "SET", "RETURN", "WITH", "MATCH", "MERGE", "CREATE",
                   "ORDER", "LIMIT", "UNWIND", "DELETE", "REMOVE")

        def _classify(code: str) -> str:
            nonlocal clause
            for match in re.finditer(
                r"(\bSET\b|\bWHERE\b)|(\w+)\.(\w+)(?=\s*=)|(\w+)\.(\w+)\b", code
            ):
                if match.group(1):
                    clause = match.group(1).upper()
                    continue
                if match.group(2):
                    var, prop = match.group(2), match.group(3)
                    is_lhs = True
                else:
                    var, prop = match.group(4), match.group(5)
                    is_lhs = False
                if (
                    prop in universal
                    or prop == SPILL_PROPERTY
                    or var in unwind_vars
                    or var in rel_vars
                    or var in var_labels
                ):
                    continue
                if is_lhs and clause == "SET":
                    drop_props.add((var, prop))
                else:
                    null_props.add((var, prop))
            return code

        _map_code_segments(query, _classify)
        if not drop_props and not null_props:
            return query

        if drop_props:
            # Replace each dropped SET item with a no-op self-assignment on
            # the universal spill column. Removing items outright is fragile
            # (trailing items have no comma and the list must never end up
            # empty), while ``n._properties = n._properties`` is always
            # valid and changes nothing.
            for var, prop in drop_props:
                query = re.sub(
                    rf"{var}\.{prop}\s*=\s*[^,\n]+",
                    f"{var}.{SPILL_PROPERTY} = {var}.{SPILL_PROPERTY}",
                    query,
                )

        if not null_props:
            return query

        def _replace(code: str) -> str:
            def repl(match: re.Match) -> str:
                var, prop = match.group(1), match.group(2)
                if (var, prop) in null_props:
                    return "NULL"
                return match.group(0)

            return re.sub(r"\b(\w+)\.(\w+)\b", repl, code)

        return _map_code_segments(query, _replace)

    def _prepare_ladybug_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]],
    ) -> Tuple[str, Dict[str, Any]]:
        params = prepare_project_scope_parameters(query, self._preserve_row_normalized(parameters))
        params = self._restore_row_normalized(params)
        # The temp marker must never leak into caller-owned row dicts that
        # callers may journal, retry, or forward to another provider.
        self._cleanup_preserved_markers(parameters)

        query, params = self._rename_reserved_params(query, params)
        query, params = self._lower_comprehension_params(query, params)
        query, params = self._rewrite_composite_merge_keys(query, params)
        query = _CALL_GUARD_RE.sub(r"MATCH \g<body>", query)
        query, params = self._rewrite_set_merge(query, params)
        query, params = self._top_up_unwind_row_fields(query, params)
        query = self._cast_unwind_refs(query)
        query = _rewrite_foreach_collect_detach(query)
        query = _FOREACH_DETACH_RE.sub(r"UNWIND \g<source> AS \g<item> DETACH DELETE \g<item>", query)
        query = _LABELS_INDEX_RE.sub(r"label(\g<var>)", query)
        query = _rewrite_label_tests(query)
        query = _strip_set_label_items(query)
        query = self._null_out_nonuniversal_props(query)
        query = _rewrite_admin_introspection(query)
        query = _rewrite_filter_comprehensions(query)

        if "datetime()" in query:
            param_name = "__ladybug_now"
            while param_name in params:
                param_name = f"_{param_name}"
            query = query.replace("datetime()", f"${param_name}")
            params = {**params, param_name: _utc_timestamp()}
        if "timestamp()" in query:
            # Neo4j-style timestamp() is not a zero-argument function here; the
            # schema models created_at columns as STRING, so bind the UTC wall
            # clock the way the datetime() rewrite above does.
            param_name = "__ladybug_ts"
            while param_name in params:
                param_name = f"_{param_name}"
            query = query.replace("timestamp()", f"${param_name}")
            params = {**params, param_name: _utc_timestamp()}
        return query, params

    @staticmethod
    def _preserve_row_normalized(
        parameters: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Snapshot writer-supplied ``project_id_normalized`` row values.

        ``prepare_project_scope_parameters`` treats the field as derived and
        strips it from nested dicts whose ``project_id`` is absent *or
        None/blank*, but LadybugDB CREATE/SET statements reference it as a
        typed column, so the original value must survive the enrichment pass.
        """

        from tools.common.project_scope import normalize_project_id

        params = dict(parameters or {})
        for value in params.values():
            if not isinstance(value, list):
                continue
            for row in value:
                if (
                    isinstance(row, dict)
                    and "project_id_normalized" in row
                    and normalize_project_id(row.get("project_id")) is None
                ):
                    row["__lb_preserved_normalized"] = row["project_id_normalized"]
        return params

    @staticmethod
    def _restore_row_normalized(params: Dict[str, Any]) -> Dict[str, Any]:
        for value in params.values():
            if not isinstance(value, list):
                continue
            for row in value:
                if not isinstance(row, dict):
                    continue
                preserved = row.pop("__lb_preserved_normalized", None)
                if preserved is not None and "project_id_normalized" not in row:
                    row["project_id_normalized"] = preserved
        return params

    @staticmethod
    def _cleanup_preserved_markers(parameters: Optional[Dict[str, Any]]) -> None:
        """Remove the temp marker from the caller's original row dicts."""

        if not parameters:
            return
        for value in parameters.values():
            if not isinstance(value, list):
                continue
            for row in value:
                if isinstance(row, dict):
                    row.pop("__lb_preserved_normalized", None)

    def _rewrite_composite_merge_keys(
        self, query: str, params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """Inject synthetic ``__pk`` values into composite-key MERGE patterns.

        Labels whose writer merge key spans several properties (doc-tiny
        ``Paragraph {source_id, paragraph_id}``) get a synthetic single-column
        PK; the MERGE property map gains a ``__pk`` entry computed from the
        referenced fields, for both scalar-parameter and UNWIND-row forms.
        """

        for label, fields in ladybug_schema._COMPOSITE_MERGE_KEYS.items():
            pattern = re.compile(
                rf"MERGE \(\s*(\w+)\s*:\s*{label}\s*\{{\s*([^}}]*?)\s*\}}\s*\)"
            )
            matches = list(pattern.finditer(query))
            if not matches:
                continue
            unwind_map = {
                var: param for param, var in _UNWIND_RE.findall(query)
            }
            field_names = list(fields)
            rebuilt = []
            for index, match in enumerate(matches):
                var, body = match.group(1), match.group(2)
                if not all(name in body for name in field_names):
                    continue
                if "row." in body:
                    row_var = re.search(r"(\w+)\.", body.split(",")[0])
                    row_var_name = row_var.group(1) if row_var else None
                    row_param = unwind_map.get(row_var_name or "")
                    rows = params.get(row_param) if row_param else None
                    if isinstance(rows, list):
                        for row in rows:
                            if isinstance(row, dict):
                                row["__pk"] = ladybug_schema.merge_key_composite_value(
                                    label, row
                                ) or row.get("__pk")
                    injected = f"{body}, __pk: {row_var_name}.__pk"
                else:
                    value = ladybug_schema.merge_key_composite_value(
                        label, {name: params.get(name) for name in field_names}
                    )
                    if value is None:
                        continue
                    pk_param = f"__cmp_pk_{index}"
                    params[pk_param] = value
                    injected = f"{body}, __pk: ${pk_param}"
                rebuilt.append((match, injected))
            for match, injected in reversed(rebuilt):
                query = (
                    query[: match.start(2)] + injected + query[match.end(2):]
                )
        return query, params

    @staticmethod
    def _rename_reserved_params(
        query: str, params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        renames: Dict[str, str] = {
            name: f"__lb_{name}"
            for name in set(_PARAM_RE.findall(query))
            if name.lower() in _RESERVED_PARAM_WORDS
        }
        if not renames:
            return query, params

        def mapper(segment: str) -> str:
            for old, new in renames.items():
                segment = re.sub(rf"\${re.escape(old)}\b", f"${new}", segment)
            return segment

        query = _map_code_segments(query, mapper)
        rebuilt: Dict[str, Any] = {}
        for key, value in params.items():
            rebuilt[renames.get(key, key)] = value
        return query, rebuilt

    @staticmethod
    def _lower_comprehension_params(
        query: str, params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        lowered: List[str] = []

        def replace(match: re.Match) -> str:
            lowered.append(match.group("param"))
            return f"${match.group('param')}"

        query = _LIST_COMPREHENSION_RE.sub(replace, query)
        for name in lowered:
            if name in params:
                params = {**params, name: _lower_param(params[name])}
        return query, params

    def _rewrite_set_merge(
        self, query: str, params: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        unwind_map = {
            var: param for param, var in _UNWIND_RE.findall(query)
        }
        var_labels = dict(_VAR_LABEL_RE.findall(query))
        rel_vars = {
            var: rel for var, rel in _REL_VAR_RE.findall(query)
        }

        # 1) SET r += coalesce(row.<field>, {})
        def replace_props(match: re.Match) -> str:
            row_var, field = match.group("row_var"), match.group("field")
            spill_key = f"__{field}_spill"
            param = unwind_map.get(row_var)
            if param and isinstance(params.get(param), list):
                for row in params[param]:
                    if isinstance(row, dict):
                        row[spill_key] = json.dumps(row.get(field) or {}, default=str)
                _unify_row_shapes(params[param])
            # The pattern consumes the trailing whitespace before the next
            # clause; restore a separator so RETURN is not glued on.
            return (
                f"SET {match.group('var')}.{SPILL_PROPERTY} = {row_var}.{spill_key}\n"
            )

        query = _SET_MERGE_PROPS_RE.sub(replace_props, query)

        # 2) SET p += $param
        def replace_param(match: re.Match) -> str:
            var, param = match.group("var"), match.group("param")
            spill_param = f"__spill_{param}"
            if param in params and spill_param not in params:
                params[spill_param] = json.dumps(params[param] or {}, default=str)
            return f"SET {var}.{SPILL_PROPERTY} = ${spill_param}"

        query = _SET_MERGE_PARAM_RE.sub(replace_param, query)

        # 3) SET n += row
        def replace_var(match: re.Match) -> str:
            var, row_var = match.group("var"), match.group("row_var")
            param = unwind_map.get(row_var)
            rows = params.get(param) if param else None
            if var in rel_vars or not isinstance(rows, list):
                # Relationship target: emit typed assignments for the shared
                # rel columns (project scope/routing must stay queryable) and
                # spill the rest of the payload.
                columns = set(ladybug_schema.CORE_REL_COLUMNS)
                if var in rel_vars:
                    known_pairs, known_cols = ladybug_schema.rel_spec(rel_vars[var])
                    if known_pairs:
                        columns |= set(known_cols)
                assignments = [
                    f"{var}.`{column}` = {_cast_row_ref(row_var, column)}"
                    for column in sorted(columns)
                ]
                assignments.append(f"{var}.{SPILL_PROPERTY} = {row_var}.__spill")
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            spill: Dict[str, Any] = {
                                key: value
                                for key, value in row.items()
                                if key not in columns
                                and key not in {"__spill", "props", "properties"}
                            }
                            # Flatten the writer's nested property payload the
                            # way ``SET r += row`` would on a schema-less store.
                            for nested_key in ("props", "properties"):
                                nested = row.get(nested_key)
                                if isinstance(nested, dict):
                                    spill.update(nested)
                            row["__spill"] = json.dumps(spill, default=str)
                            for column in columns:
                                row[column] = _safe_coerce(column, row.get(column))
                    _unify_row_shapes(rows)
                return f"SET {', '.join(assignments)}"
            label = var_labels.get(var)
            if label is None:
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            row["__spill"] = json.dumps(row, default=str)
                    _unify_row_shapes(rows)
                return f"SET {var}.{SPILL_PROPERTY} = {row_var}.__spill"
            self._ensure_node_table(label)
            _, columns = ladybug_schema.label_columns(label)
            pk, _ = ladybug_schema.node_spec(label)
            assignments = [
                f"{var}.`{column}` = {_cast_row_ref(row_var, column)}"
                for column in sorted(columns)
            ]
            assignments.append(f"{var}.{SPILL_PROPERTY} = {row_var}.__spill")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        row["__spill"] = json.dumps(
                            {
                                key: value
                                for key, value in row.items()
                                if key not in columns
                                and key != "__spill"
                                and key != pk
                            },
                            default=str,
                        )
                        # Missing struct fields are binder errors (not NULL),
                        # so every referenced column must exist on every row.
                        for column in columns:
                            row[column] = _safe_coerce(column, row.get(column))
                _unify_row_shapes(rows)
            return f"SET {', '.join(assignments)}"

        query = _SET_MERGE_VAR_RE.sub(replace_var, query)
        return query, params

    # ------------------------------------------------------------------ #
    # Schema management
    # ------------------------------------------------------------------ #

    def _run_catalog(self, conn: Any, query: str, params: Optional[Dict[str, Any]] = None):
        return conn.execute(query, params or {})

    def _invalidate_prepared_cache(self) -> None:
        """Drop cached prepared statements after catalog-changing DDL.

        LadybugDB caches prepared statements by (query, param signature).
        Statements bound before a DDL keep referencing the old catalog — and
        a failed prepare leaves a permanently failing entry — so every DDL
        must invalidate the cache or healed queries keep erroring.
        """

        self._universal_columns_cache = None

        cache = getattr(self._conn, "_pybind_implicit_prepared_cache", None)
        if cache is None:
            if not self._prepared_cache_warned:
                self._prepared_cache_warned = True
                logger.warning(
                    "LadybugDB prepared-statement cache attribute not found; "
                    "stale statements after DDL may cause spurious binder errors"
                )
            return
        lock = getattr(self._conn, "_prepared_cache_lock", None)
        try:
            if lock is not None:
                with lock:
                    cache.clear()
            else:
                cache.clear()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("LadybugDB prepared-cache invalidation failed: %s", exc)

    def _existing_node_columns(self, label: str) -> Optional[set]:
        """Return the live column names of an existing node table, or None."""

        return self._existing_node_columns_on(self._conn, label)

    @staticmethod
    def _existing_node_columns_on(conn: Any, label: str) -> Optional[set]:
        try:
            result = conn.execute(f"CALL TABLE_INFO('{label}') RETURN *")
            rows = result.get_all()
        except Exception as exc:  # noqa: BLE001 - probe only
            logger.debug("LadybugDB TABLE_INFO probe failed for %s: %s", label, exc)
            return None
        columns: set = set()
        for row in rows:
            # TABLE_INFO rows are (property_id, name, type, default, is_pk).
            if len(row) >= 2 and row[1]:
                columns.add(str(row[1]))
        return columns

    @staticmethod
    def _live_column_types_on(conn: Any, label: str) -> Optional[Dict[str, str]]:
        try:
            result = conn.execute(f"CALL TABLE_INFO('{label}') RETURN *")
            rows = result.get_all()
        except Exception as exc:  # noqa: BLE001 - probe only
            logger.debug("LadybugDB TABLE_INFO type probe failed for %s: %s", label, exc)
            return None
        types: Dict[str, str] = {}
        for row in rows:
            # TABLE_INFO rows are (property_id, name, type, default, is_pk).
            if len(row) >= 3 and row[1]:
                types[str(row[1])] = str(row[2]).upper()
        return types

    @staticmethod
    def _list_node_table_names(conn: Any) -> List[str]:
        """Return every node table name in the connected catalog."""

        try:
            result = conn.execute("CALL SHOW_TABLES() RETURN *")
            rows = result.get_all()
        except Exception as exc:  # noqa: BLE001 - probe only
            logger.debug("LadybugDB SHOW_TABLES probe failed: %s", exc)
            return []
        names: List[str] = []
        for row in rows:
            # SHOW_TABLES rows are (id, name, type, database name, comment).
            if len(row) >= 3 and str(row[2]).upper() == "NODE" and row[1]:
                names.append(str(row[1]))
        return names

    def _delete_project_nodes_by_paths_sync(
        self,
        conn: Any,
        project_id: str,
        paths: List[str],
    ) -> int:
        """Per-label replacement for the schema-less
        ``MATCH (n) WHERE n.file_path IN ...`` cleanup idiom.

        LadybugDB property access is table-typed and anonymous nodes cannot
        carry arbitrary properties, so the cleanup fans out over the live
        node tables and deletes rows whose file-ish column matches.
        """

        total = 0
        for label in self._list_node_table_names(conn):
            try:
                existing = self._existing_node_columns_on(conn, label)
            except Exception:  # noqa: BLE001 - keep other labels
                continue
            if not existing or "project_id" not in existing:
                continue
            match_columns = [
                column for column in ("file_path", "path") if column in existing
            ]
            if label == "File" and "id" in existing:
                match_columns.append("id")
            if not match_columns:
                continue
            conditions = " OR ".join(f"n.`{c}` IN $paths" for c in match_columns)
            query = (
                f"MATCH (n:`{label}`) "
                "WHERE n.`project_id` = $project_id "
                f"AND ({conditions}) "
                "DETACH DELETE n RETURN count(n) AS count"
            )
            try:
                result = conn.execute(
                    query, {"project_id": project_id, "paths": paths}
                )
                rows = result.get_all()
                total += int(rows[0][0]) if rows and rows[0] else 0
            except Exception as exc:  # noqa: BLE001 - keep other labels
                logger.warning(
                    "LadybugDB path cleanup failed for %s: %s", label, exc
                )
        return total

    def _prune_orphan_unknown_functions_sync(self, conn: Any) -> int:
        query = (
            "MATCH (u:UnknownFunction) "
            "WHERE NOT ()-[:UNKNOWN_CALL]->(u) "
            "DETACH DELETE u RETURN count(u) AS count"
        )
        try:
            result = conn.execute(query)
            rows = result.get_all()
            return int(rows[0][0]) if rows and rows[0] else 0
        except Exception as exc:  # noqa: BLE001 - table may not exist
            logger.debug("LadybugDB unknown-function prune skipped: %s", exc)
            return 0

    def _cleanup_topology_owned_sync(
        self,
        conn: Any,
        project_id_normalized: str,
        paths: Optional[List[str]],
    ) -> int:
        """Per-label replacement for the topology writers' schema-less
        ``MATCH (node) WHERE node.topology_owned ...`` cleanup."""

        total = 0
        for label in self._list_node_table_names(conn):
            existing = self._existing_node_columns_on(conn, label)
            if not existing or "topology_owned" not in existing:
                continue
            if "project_id_normalized" not in existing:
                continue
            conditions = []
            if paths:
                for column in ("file_path", "path", "id"):
                    if column in existing:
                        conditions.append(f"n.`{column}` IN $paths")
                if not conditions:
                    # No queryable file column (module_path lives in the
                    # spill payload); nothing this label can match on.
                    continue
            core_where = (
                "n.`project_id_normalized` = $project_id_normalized "
                "AND n.`topology_owned` = true"
            )
            where = (
                f"{core_where} AND ({' OR '.join(conditions)})"
                if conditions
                else core_where
            )
            query = (
                f"MATCH (n:`{label}`) WHERE {where} "
                "DETACH DELETE n RETURN count(n) AS count"
            )
            params: Dict[str, Any] = {
                "project_id_normalized": project_id_normalized
            }
            if paths:
                params["paths"] = paths
            try:
                result = conn.execute(query, params)
                rows = result.get_all()
                total += int(rows[0][0]) if rows and rows[0] else 0
            except Exception as exc:  # noqa: BLE001 - keep other labels
                logger.warning(
                    "LadybugDB topology cleanup failed for %s: %s", label, exc
                )
        return total

    async def delete_nodes_by_paths(
        self,
        *,
        project_id: str,
        file_paths: List[str],
        database: Optional[str] = None,
    ) -> int:
        """Delete every project node whose file-ish column matches
        ``file_paths``; returns the number of deleted nodes."""

        conn = self._connection_for(database)
        paths = [str(path).replace("\\", "/") for path in file_paths if path]
        if not paths:
            return 0

        async def run() -> int:
            return await self._run_in_executor(
                self._delete_project_nodes_by_paths_sync, conn, project_id, paths
            )

        return await self._query_lane.run(run)

    async def prune_orphan_unknown_functions(
        self, database: Optional[str] = None
    ) -> int:
        """Delete UnknownFunction nodes with no incoming UNKNOWN_CALL."""

        conn = self._connection_for(database)

        async def run() -> int:
            return await self._run_in_executor(
                self._prune_orphan_unknown_functions_sync, conn
            )

        return await self._query_lane.run(run)

    async def cleanup_topology_owned(
        self,
        project_id_normalized: str,
        *,
        paths: Optional[List[str]] = None,
        database: Optional[str] = None,
    ) -> int:
        """Delete topology-owned nodes for a project, optionally scoped to
        the given file paths."""

        conn = self._connection_for(database)
        normalized = [
            str(path).replace("\\", "/").strip("/") for path in (paths or []) if path
        ]

        async def run() -> int:
            return await self._run_in_executor(
                self._cleanup_topology_owned_sync,
                conn,
                project_id_normalized,
                normalized or None,
            )

        return await self._query_lane.run(run)

    def _align_node_table_columns(self, label: str) -> None:
        """ALTER TABLE ADD columns that joined the spec after the table was
        first created.

        ``CREATE NODE TABLE IF NOT EXISTS`` cannot evolve an existing table,
        so a database created by an older build keeps its old shape forever
        without this reconciliation. Only additive changes are made; columns
        present in the live table but absent from the spec are preserved.

        Every node table is also topped up to the core column baseline so
        generic ``(n)`` queries can reference core properties (``id``,
        ``file_path``, ``note``, ...) on any label — the schema-less MCP
        fallback queries depend on that.
        """

        pk, columns = ladybug_schema.node_spec(label)
        wanted = list(columns)
        for core in ladybug_schema.CORE_NODE_COLUMNS:
            if core not in wanted:
                wanted.append(core)
        if pk and pk not in wanted and pk != "id":
            wanted.append(pk)
        existing = self._existing_node_columns(label)
        if existing is None:
            return
        live_types = self._live_column_types_on(self._conn, label) or {}
        for column in wanted:
            wanted_type = ladybug_schema.column_type(column)
            live_type = live_types.get(column)
            if column in existing:
                if live_type and live_type != wanted_type:
                    # A column whose declared type changed (e.g. STRING →
                    # BOOL) cannot be ALTERed in place; drop and re-add it.
                    # Only metadata columns are affected — writers re-stamp
                    # them on the next run.
                    try:
                        self._conn.execute(
                            f"ALTER TABLE `{label}` DROP `{column}`"
                        )
                    except Exception as exc:  # noqa: BLE001 - keep syncing
                        logger.warning(
                            "LadybugDB column retype drop failed for %s.%s: %s",
                            label, column, exc,
                        )
                        continue
                    logger.info(
                        "LadybugDB column retyped: %s.%s %s -> %s",
                        label, column, live_type, wanted_type,
                    )
                else:
                    continue
            try:
                self._conn.execute(
                    "ALTER TABLE `{label}` ADD `{column}` {type}".format(
                        label=label,
                        column=column,
                        type=wanted_type,
                    )
                )
                logger.info("LadybugDB column healed: %s.%s", label, column)
            except Exception as exc:  # noqa: BLE001 - keep syncing the rest
                logger.warning(
                    "LadybugDB column heal failed for %s.%s: %s", label, column, exc
                )

    def _ensure_node_table(self, label: str) -> None:
        if label in self._ensured_node_tables:
            return
        ddl = ladybug_schema.compile_node_ddl(label)
        with self._execute_lock:
            self._conn.execute(ddl)
            self._align_node_table_columns(label)
        self._invalidate_prepared_cache()
        self._ensured_node_tables.add(label)
        logger.debug("LadybugDB node table ensured: %s", label)

    def _align_rel_table_columns(self, rel_type: str, conn: Any) -> None:
        """ALTER REL TABLE ADD columns missing from an older catalog."""

        try:
            _, columns = ladybug_schema.rel_spec(rel_type)
        except Exception:  # noqa: BLE001 - unknown rel types self-heal elsewhere
            return
        existing = self._existing_node_columns_on(conn, rel_type)
        if not existing:
            return
        for column in columns:
            if column in existing:
                continue
            try:
                conn.execute(
                    "ALTER TABLE `{rel}` ADD `{column}` {type}".format(
                        rel=rel_type,
                        column=column,
                        type=ladybug_schema.column_type(column),
                    )
                )
                logger.info("LadybugDB rel column healed: %s.%s", rel_type, column)
            except Exception as exc:  # noqa: BLE001 - keep syncing the rest
                logger.warning(
                    "LadybugDB rel column heal failed for %s.%s: %s",
                    rel_type,
                    column,
                    exc,
                )

    def _ensure_rel_table(self, rel_type: str, source: str, target: str) -> None:
        requested = (source, target)
        pairs = self._ensured_rel_pairs.get(rel_type)
        if pairs and requested in pairs:
            return
        known, columns = ladybug_schema.rel_spec(rel_type)
        endpoints = known or (requested,)
        for endpoint_source, endpoint_target in endpoints:
            # Rel DDL fails when an endpoint node table is missing.
            self._ensure_node_table(endpoint_source)
            self._ensure_node_table(endpoint_target)
        ddl = ladybug_schema.compile_rel_ddl(rel_type, endpoints)
        with self._execute_lock:
            self._conn.execute(ddl)
            self._align_rel_table_columns(rel_type, self._conn)
            # ``CREATE ... IF NOT EXISTS`` cannot extend a rel table created by
            # an older catalog (or with fewer endpoints) with pairs that
            # joined later; ALTER them in. Duplicates are the common case and
            # are skipped quietly.
            self._ensure_node_table(requested[0])
            self._ensure_node_table(requested[1])
            try:
                self._conn.execute(
                    f"ALTER TABLE `{rel_type}` ADD FROM `{requested[0]}` "
                    f"TO `{requested[1]}`"
                )
                logger.info(
                    "LadybugDB rel endpoint healed: %s %s->%s",
                    rel_type,
                    requested[0],
                    requested[1],
                )
            except Exception as exc:  # noqa: BLE001 - duplicates expected
                logger.debug(
                    "LadybugDB rel endpoint ALTER skipped for %s %s->%s: %s",
                    rel_type,
                    requested[0],
                    requested[1],
                    exc,
                )
            endpoints = endpoints + (requested,)
        self._invalidate_prepared_cache()
        self._ensured_rel_pairs.setdefault(rel_type, set()).update(endpoints)
        logger.debug(
            "LadybugDB rel table ensured: %s (%d endpoint pairs)", rel_type, len(endpoints)
        )

    def _ensure_schema_for_query(self, query: str) -> None:
        for label in _MERGE_NODE_RE.findall(query):
            try:
                self._ensure_node_table(label)
            except Exception as exc:  # noqa: BLE001 - surface after the loop
                raise RuntimeError(
                    f"Failed to create LadybugDB node table {label!r}: {exc}"
                ) from exc
        var_labels = dict(_VAR_LABEL_RE.findall(query))
        for source_var, rel_type, target_var in _MERGE_EDGE_RE.findall(query):
            source = var_labels.get(source_var)
            target = var_labels.get(target_var)
            if not source or not target:
                continue
            try:
                self._ensure_rel_table(rel_type, source, target)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"Failed to create LadybugDB rel table {rel_type!r}"
                    f" ({source}->{target}): {exc}"
                ) from exc

    def _self_heal_missing_table(self, query: str, exc: Exception) -> bool:
        """Create a missing table named by the binder error, then retry once."""

        message = str(exc)
        match = re.search(r"Table (\w+) does not exist", message)
        if not match:
            return False
        table = validate_cypher_identifier(match.group(1), kind="table")
        if table in self._ensured_node_tables:
            return False
        labels = {label for _, label in _VAR_LABEL_RE.findall(query)}
        rel_types = {rel for _, rel in _REL_VAR_RE.findall(query)}
        try:
            if table in rel_types:
                # Known rel types carry their full endpoint map; otherwise
                # infer the pair from a MERGE pattern in the query.
                known_pairs, _ = ladybug_schema.rel_spec(table)
                if known_pairs:
                    first_source, first_target = known_pairs[0]
                    self._ensure_rel_table(table, first_source, first_target)
                    self._ensured_rel_pairs.setdefault(table, set()).update(known_pairs)
                    return True
                var_labels = dict(_VAR_LABEL_RE.findall(query))
                for source_var, rel_type, target_var in _MERGE_EDGE_RE.findall(query):
                    if rel_type != table:
                        continue
                    source = var_labels.get(source_var)
                    target = var_labels.get(target_var)
                    if source and target:
                        self._ensure_rel_table(table, source, target)
                        return True
                return False
            if table in labels:
                self._ensure_node_table(table)
                return True
        except Exception as heal_exc:  # noqa: BLE001
            logger.warning("LadybugDB self-heal failed for %s: %s", table, heal_exc)
        return False
        return False

    # ------------------------------------------------------------------ #
    # Query execution
    # ------------------------------------------------------------------ #

    def _connection_for(self, database: Optional[str]) -> Any:
        if database and database != self._database:
            conn = self._additional_connections.get(database)
            if conn is not None:
                return conn
        return self._conn

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
        conn = self._connection_for(database)
        prepared_query, params = self._prepare_ladybug_query(query, parameters)
        # Schema DDL (preflight and self-heal) only ever targets the primary
        # writable catalog; read-only sibling catalogs must not trigger DDL
        # against the primary while their own query stays doomed.
        primary = conn is self._conn
        if primary and not self._catalog_aligned_once:
            # Read-only processes (MCP query servers) never hit the mutating
            # path below, yet typed columns added by newer specs must exist
            # before any label-qualified property access can bind.
            self._catalog_aligned_once = True
            for label in self._list_node_table_names(self._conn):
                try:
                    self._align_node_table_columns(label)
                except Exception as exc:  # noqa: BLE001 - best-effort
                    logger.debug("LadybugDB startup align failed for %s: %s", label, exc)
            # Relationship tables referenced by read-side MCP queries (e.g.
            # HANDLED_BY) may never appear in a write path; create the known
            # ones — with their full endpoint maps — so their endpoints exist
            # before the first query binds.
            for rel_type, (known_pairs, _cols) in ladybug_schema._REL_SPECS.items():
                if not known_pairs:
                    continue
                try:
                    for source, target in known_pairs:
                        self._ensure_rel_table(rel_type, source, target)
                except Exception as exc:  # noqa: BLE001 - best-effort
                    logger.debug(
                        "LadybugDB startup rel ensure failed for %s: %s", rel_type, exc
                    )
            self._invalidate_prepared_cache()
        if primary and _MUTATING_RE.search(prepared_query):
            self._ensure_schema_for_query(prepared_query)

        with self._execute_lock:
            for attempt in range(3):
                try:
                    result = conn.execute(prepared_query, params or {})
                    break
                except Exception as exc:
                    # A fresh catalog reports missing node tables before
                    # missing rel tables, so allow chained self-heal retries.
                    if (
                        primary
                        and attempt < 2
                        and self._self_heal_missing_table(prepared_query, exc)
                    ):
                        continue
                    raise

        keys = list(result.get_column_names() or [])
        records: List[Dict[str, Any]] = []
        try:
            while result.has_next():
                row = result.get_next()
                records.append(
                    {key: _normalize_ladybug_value(value) for key, value in zip(keys, row)}
                )
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeError(f"Failed to read LadybugDB result: {exc}") from exc
        return records, keys, result

    # ------------------------------------------------------------------ #
    # Batch writes (schema-aware overrides of the portable base)
    # ------------------------------------------------------------------ #

    async def batch_write_nodes(
        self,
        nodes: List[Dict[str, Any]],
        label: str,
        database: Optional[str] = None,
    ) -> int:
        if not nodes:
            return 0
        label = validate_cypher_identifier(label, kind="node label")
        self._ensure_node_table(label)
        pk, columns = ladybug_schema.label_columns(label)
        identity = pk or "id"
        property_map = [
            f"`{column}`: {_cast_row_ref('row', column)}" for column in sorted(columns)
        ]
        property_map.insert(0, f"`{identity}`: row.{identity}")
        property_map.append(f"`{SPILL_PROPERTY}`: row.__spill")
        prepared_rows = []
        uniform_keys = sorted(columns | {identity}) + ["__spill"]
        for node in nodes:
            row: Dict[str, Any] = {}
            for column in uniform_keys:
                if column == "__spill":
                    continue
                row[column] = _coerce_column(column, node.get(column))
            row["__spill"] = json.dumps(
                {
                    key: value
                    for key, value in node.items()
                    if key not in columns and key != identity
                },
                default=str,
            )
            prepared_rows.append(row)
        query = (
            f"UNWIND $rows AS row CREATE (n:{label} {{{', '.join(property_map)}}}) "
            "RETURN count(n) AS count"
        )
        records, _, _ = await self.execute_query(query, {"rows": prepared_rows}, database)
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
        from tools.graph.writer.query_contract import RelationshipGroup

        rel_type = validate_cypher_identifier(relationship_type, kind="relationship type")
        source_node_label = validate_cypher_identifier(source_label, kind="source label")
        target_node_label = validate_cypher_identifier(target_label, kind="target label")
        RelationshipGroup(source_node_label, target_node_label, rel_type)
        self._ensure_rel_table(rel_type, source_node_label, target_node_label)
        prepared_rows = []
        for edge in edges:
            row = dict(edge)
            properties = row.get("properties") or {}
            row["__spill"] = json.dumps(properties, default=str)
            prepared_rows.append(row)
        query = f"""
        UNWIND $rows AS row
        MATCH (source:{source_node_label} {{id: row.source_id}})
        MATCH (target:{target_node_label} {{id: row.target_id}})
        MERGE (source)-[r:{rel_type}]->(target)
        SET r.{SPILL_PROPERTY} = row.__spill
        RETURN count(r) as count
        """
        records, _, _ = await self.execute_query(query, {"rows": prepared_rows}, database)
        return records[0]["count"] if records else 0

    # ------------------------------------------------------------------ #
    # Schema preflight contract (ensure_schema integration)
    # ------------------------------------------------------------------ #

    async def create_indexes(
        self,
        indexes: List[Dict[str, Any]],
        database: Optional[str] = None,
    ) -> None:
        for idx in indexes:
            label = idx["label"]
            self._ensure_node_table(label)
            prop = idx.get("property")
            props = prop if isinstance(prop, list) else [prop]
            for item in props:
                if item:
                    self._virtual_indexes[(label, tuple([str(item)]))] = {
                        "label": label,
                        "properties": [str(item)],
                        "index_type": "range",
                        "entity_type": "node",
                        "status": "ONLINE",
                    }

    async def inspect_indexes(
        self,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        try:
            with self._execute_lock:
                result = self._conn.execute("CALL show_tables() RETURN *")
                names: List[str] = []
                while result.has_next():
                    row = result.get_next()
                    if len(row) >= 3 and str(row[2]).upper() == "NODE":
                        names.append(str(row[1]))
                for name in names:
                    info = self._conn.execute(f"CALL table_info('{name}') RETURN *")
                    pk: Optional[str] = None
                    while info.has_next():
                        col = info.get_next()
                        if len(col) >= 5 and bool(col[4]):
                            pk = str(col[1])
                            break
                    if pk:
                        normalized.append(
                            {
                                "label": name,
                                "properties": [pk],
                                "index_type": "range",
                                "entity_type": "node",
                                "status": "ONLINE",
                            }
                        )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("LadybugDB index inspection failed: %s", exc)
        normalized.extend(self._virtual_indexes.values())
        return normalized

    async def list_databases(self) -> List[str]:
        return [self._database, *self._additional_connections.keys()]

    async def list_relationship_types(self, database: Optional[str] = None) -> List[str]:
        records, _, _ = await self.execute_query(
            "MATCH ()-[r]->() RETURN DISTINCT label(r) AS rel_type",
            database=database,
        )
        rel_types: List[str] = []
        for record in records:
            rel_type = record.get("rel_type")
            if isinstance(rel_type, str):
                upper = rel_type.upper()
                if upper not in rel_types:
                    rel_types.append(upper)
        return rel_types

    # ------------------------------------------------------------------ #
    # Lookups and search (no fulltext — CONTAINS fallback only)
    # ------------------------------------------------------------------ #

    async def find_node_by_id(
        self,
        node_id: str,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cypher = """
        MATCH (n)
        WHERE n.id = $id
          AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)
        RETURN n
        LIMIT 1
        """
        records, _, _ = await self.execute_query(
            cypher, {"id": node_id, "project_id": project_id}, database
        )
        node = records[0].get("n") if records else None
        if node and node.get("framework") == "servlet_jsp":
            active_records, _, _ = await self.execute_query(
                "MATCH (s:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id}) "
                "WHERE s.active_generation = $generation_id RETURN s.id AS id LIMIT 1",
                {
                    "project_id": node.get("project_id"),
                    "module_id": node.get("module_id"),
                    "generation_id": node.get("generation_id"),
                },
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
          AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)
        RETURN n
        """
        records, _, _ = await self.execute_query(
            cypher, {"ids": node_ids, "project_id": project_id}, database
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
            nodes = [
                n
                for n in nodes
                if n.get("framework") != "servlet_jsp" or str(n.get("id")) in active_ids
            ]
        return nodes

    async def search_functions(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cypher = """
        MATCH (n:Function)
        WHERE (
            toLower(n.name) CONTAINS toLower($query)
            OR toLower(coalesce(n.qualified_name, '')) CONTAINS toLower($query)
        )
          AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher, {"query": query, "limit": limit, "project_id": project_id}, database
        )
        return [record.get("n") for record in records if record.get("n")]

    async def search_by_code(
        self,
        query: str,
        limit: int = 50,
        project_id: Optional[str] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cypher = """
        MATCH (n)
        WHERE (
            toLower(coalesce(n.code, '')) CONTAINS toLower($query)
            OR toLower(coalesce(n.comment, '')) CONTAINS toLower($query)
            OR toLower(coalesce(n.summary, '')) CONTAINS toLower($query)
        )
          AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)
        RETURN n
        LIMIT $limit
        """
        records, _, _ = await self.execute_query(
            cypher, {"query": query, "limit": limit, "project_id": project_id}, database
        )
        return [record.get("n") for record in records if record.get("n")]


__all__ = ["LadybugDBDriver", "NativeOperationInFlightError"]

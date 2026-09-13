"""Tests for the LadybugDB driver (fake-backed, no ladybug install required).

Pattern mirrors ``tests/test_falkordb_driver_local.py``: the ``ladybug``
module is replaced by fakes so the driver's lease/lane/retry/auto-DDL
behavior is testable on any machine.  Integration tests that exercise the
real wheel are marked ``@pytest.mark.ladybug`` and skip when the package is
absent.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.core.provider_contract import is_database_not_found_error  # noqa: E402
from tools.graph.driver import ladybug_driver as ladybug_driver_module  # noqa: E402
from tools.graph.driver.ladybug_driver import (  # noqa: E402
    AmbiguousWriteTimeoutError,
    LadybugDriver,
    auto_ddl_enabled,
)
from cortex_harness.storage.layout import ladybug_store_file_name  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes emulating the ladybug module surface used by the driver
# ---------------------------------------------------------------------------


class FakeQueryResult:
    def __init__(self, columns: List[str], rows: List[List[Any]]):
        self._columns = columns
        self._rows = rows
        self._cursor = 0

    def get_column_names(self) -> List[str]:
        return list(self._columns)

    def has_next(self) -> bool:
        return self._cursor < len(self._rows)

    def get_next(self) -> List[Any]:
        row = self._rows[self._cursor]
        self._cursor += 1
        return row

    def get_num_tuples(self) -> int:
        return len(self._rows)

    def get_all(self) -> List[List[Any]]:
        return [list(row) for row in self._rows]


class FakeConnection:
    """Scripted Ladybug Connection.

    ``script`` maps a predicate(query) -> list of outcomes consumed in order;
    an outcome may be a FakeQueryResult or an exception. The default response
    is an empty result. Executed statements accumulate in ``statements``.
    """

    def __init__(self, database: "FakeDatabase"):
        self.database = database
        self.statements: List[Tuple[str, Dict[str, Any]]] = []
        self.timeouts: List[int] = []
        self.script: List[Tuple[Any, Any]] = []

    def set_query_timeout(self, timeout_ms: int) -> None:
        self.timeouts.append(timeout_ms)

    def execute(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> FakeQueryResult:
        self.statements.append((query, dict(parameters or {})))
        for index, (predicate, outcome) in enumerate(self.script):
            if predicate(query):
                self.script.pop(index)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
        return FakeQueryResult(["ok"], [[1]])


class FakeDatabase:
    instances: List["FakeDatabase"] = []

    def __init__(self, database_path: str, *, read_only: bool = False, **kwargs: Any):
        self.path = Path(database_path)
        self.read_only = read_only
        self.kwargs = kwargs
        self.closed = False
        # A real Ladybug store is a single file; emulate that.
        self.path.touch(exist_ok=True)
        FakeDatabase.instances.append(self)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_ladybug(tmp_path: Path, monkeypatch):
    """Install a fake ``ladybug`` module and reset driver globals per test."""

    module = types.ModuleType("ladybug")
    module.Database = FakeDatabase
    module.Connection = FakeConnection
    FakeDatabase.instances = []
    ladybug_driver_module._BOOTSTRAPPED_STORES.clear()
    monkeypatch.setenv("LADYBUG_QUERY_TIMEOUT_MS", "5000")
    monkeypatch.delenv("LADYBUG_BUFFER_POOL_SIZE", raising=False)
    monkeypatch.delenv("CORTEX_GRAPH_AUTO_DDL", raising=False)
    with patch.dict(sys.modules, {"ladybug": module}):
        yield module
    ladybug_driver_module._BOOTSTRAPPED_STORES.clear()


def _make_driver(tmp_path: Path, graph: str = "hyper_graph", **kwargs: Any) -> LadybugDriver:
    store = tmp_path / "code.lbug" / ladybug_store_file_name(graph)
    return LadybugDriver(store, graph=graph, **kwargs)


def _primary_connection(driver: LadybugDriver) -> FakeConnection:
    return driver._primary_connection


# ---------------------------------------------------------------------------
# Constructor contract
# ---------------------------------------------------------------------------


def test_local_only_rejects_network_arguments(tmp_path: Path, fake_ladybug) -> None:
    with pytest.raises(ValueError, match="local-only"):
        _make_driver(tmp_path, uri="redis://example:6379")


def test_rejects_writable_siblings(tmp_path: Path, fake_ladybug) -> None:
    with pytest.raises(ValueError, match="read-only"):
        _make_driver(tmp_path / "g", read_only_siblings=False)


def test_constructor_opens_store_and_acquires_lease(tmp_path: Path, fake_ladybug) -> None:
    store_parent = tmp_path / "code.lbug"
    driver = _make_driver(tmp_path)
    try:
        assert driver.provider.value == "ladybug"
        assert driver.path.exists()  # Ladybug store is a file
        assert len(FakeDatabase.instances) == 1
        assert not FakeDatabase.instances[0].read_only
        lock_file = store_parent / ".hyper_graph.cortex-owner.lock"
        assert lock_file.exists()
        # Query timeout propagated to the connection.
        assert _primary_connection(driver).timeouts == [5000]
    finally:
        driver.close()
    assert FakeDatabase.instances[0].closed


def test_invalid_graph_name_fails_closed(tmp_path: Path, fake_ladybug) -> None:
    with pytest.raises(ValueError, match="store file name"):
        _make_driver(tmp_path, graph="../escape")


def test_query_timeout_env_override(tmp_path: Path, fake_ladybug, monkeypatch) -> None:
    monkeypatch.setenv("LADYBUG_QUERY_TIMEOUT_MS", "7777")
    driver = _make_driver(tmp_path)
    try:
        assert _primary_connection(driver).timeouts == [7777]
    finally:
        driver.close()
    with pytest.raises(ValueError):
        LadybugDriver(
            tmp_path / "code.lbug" / "g",
            query_timeout_ms=-1,
        )


def test_buffer_pool_size_env(tmp_path: Path, fake_ladybug, monkeypatch) -> None:
    monkeypatch.setenv("LADYBUG_BUFFER_POOL_SIZE", "1048576")
    _make_driver(tmp_path)
    assert FakeDatabase.instances[-1].kwargs.get("buffer_pool_size") == 1048576


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------


def test_query_round_trip(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "MATCH" in q,
                FakeQueryResult(["n"], [[{"_ID": {"offset": 3, "table": 0}, "_LABEL": "Function", "id": "f1"}]]),
            )
        )
        records, keys, summary = driver.execute_query_sync("MATCH (n:Function) RETURN n LIMIT 1")
        assert keys == ["n"]
        assert records[0]["n"]["_label"] == "Function"
        assert records[0]["n"]["id"] == "f1"
        assert records[0]["n"]["_id"] == 3
        assert summary is not None
    finally:
        driver.close()


def test_datetime_rewrite_uses_timestamp_wrapper(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        driver.execute_query_sync("MATCH (n) SET n.updated_at = datetime()")
        executed = _primary_connection(driver).statements[-1]
        query, params = executed
        assert "datetime()" not in query
        assert "timestamp($__ladybug_now)" in query
        assert params["__ladybug_now"].endswith("Z")
    finally:
        driver.close()


def test_call_importing_subquery_rewritten(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        driver.execute_query_sync("CALL (x) { RETURN x AS v }")
        query = _primary_connection(driver).statements[-1][0]
        assert "CALL { WITH x" in query
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Retry / timeout contract
# ---------------------------------------------------------------------------


def test_read_retries_then_succeeds(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (lambda q: "MATCH" in q, OSError("transient checkpoint contention"))
        )
        connection.script.append(
            (lambda q: "MATCH" in q, FakeQueryResult(["count"], [[5]]))
        )
        records, _, _ = driver.execute_query_sync("MATCH (n) RETURN count(n) AS count")
        assert records == [{"count": 5}]
    finally:
        driver.close()


def test_mutation_timeout_is_ambiguous(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (lambda q: "MERGE" in q, RuntimeError("query timed out"))
        )
        with pytest.raises(AmbiguousWriteTimeoutError, match="ambiguous"):
            driver.execute_query_sync("MERGE (n:Function {id: 'x'})")
        # Exactly one mutation attempt: ambiguous timeouts are never retried.
        mutation_calls = [s for s in connection.statements if "MERGE" in s[0]]
        assert len(mutation_calls) == 1
    finally:
        driver.close()


def test_mutation_not_retried_on_schema_error_without_auto_ddl(
    tmp_path: Path, fake_ladybug, monkeypatch
) -> None:
    monkeypatch.setenv("CORTEX_GRAPH_AUTO_DDL", "0")
    assert not auto_ddl_enabled()
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "SET n.unknown_prop" in q,
                RuntimeError("Binder exception: Cannot find property unknown_prop for n."),
            )
        )
        with pytest.raises(RuntimeError, match="unknown_prop"):
            driver.execute_query_sync("MATCH (n:Function) SET n.unknown_prop = $v", {"v": "x"})
        assert not any("ALTER TABLE" in s[0] for s in connection.statements)
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Auto-DDL
# ---------------------------------------------------------------------------


def test_auto_ddl_alters_missing_property_and_retries(
    tmp_path: Path, fake_ladybug, caplog
) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "SET n.email" in q,
                RuntimeError("Binder exception: Cannot find property email for n."),
            )
        )
        with caplog.at_level(logging.WARNING, logger="tools.graph.driver.ladybug_driver"):
            records, _, _ = driver.execute_query_sync(
                "MATCH (n:Function) SET n.email = $v RETURN n.id AS id",
                {"v": "a@example.com"},
            )
        assert auto_ddl_enabled()
        alter_calls = [s for s in connection.statements if "ALTER TABLE" in s[0]]
        assert alter_calls == [("ALTER TABLE `Function` ADD `email` STRING", {})]
        # The mutation retried after the ALTER.
        assert len([s for s in connection.statements if "SET n.email" in s[0]]) == 2
        assert any("auto-DDL" in record.message for record in caplog.records)
    finally:
        driver.close()


def test_auto_ddl_infers_types(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "SET n.score" in q,
                RuntimeError("Binder exception: Cannot find property score for n."),
            )
        )
        driver.execute_query_sync(
            "MATCH (n:Function) SET n.score = $v", {"v": 1.5}
        )
        alter_calls = [s[0] for s in connection.statements if "ALTER TABLE" in s[0]]
        assert alter_calls == ["ALTER TABLE `Function` ADD `score` DOUBLE"]
    finally:
        driver.close()


def test_auto_ddl_infers_timestamp_from_datetime_rewrite(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        # The datetime() rewrite runs before auto-DDL sees the query.
        connection.script.append(
            (
                lambda q: "SET n.updated_at" in q,
                RuntimeError("Binder exception: Cannot find property updated_at for n."),
            )
        )
        driver.execute_query_sync("MATCH (n:Function) SET n.updated_at = datetime()")
        alter_calls = [s[0] for s in connection.statements if "ALTER TABLE" in s[0]]
        assert alter_calls == ["ALTER TABLE `Function` ADD `updated_at` TIMESTAMP"]
    finally:
        driver.close()


def test_auto_ddl_creates_missing_node_table(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "CREATE (n:Widget" in q,
                RuntimeError("Binder exception: Table Widget does not exist."),
            )
        )
        driver.execute_query_sync("CREATE (n:Widget {id: 'w1'})")
        ddl = [s[0] for s in connection.statements if "CREATE NODE TABLE" in s[0]]
        assert "CREATE NODE TABLE IF NOT EXISTS `Widget` (id STRING, PRIMARY KEY(id))" in ddl
    finally:
        driver.close()


def test_auto_ddl_creates_missing_rel_table_from_query(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "-[:LINKS_TO]" in q,
                RuntimeError("Binder exception: Table LINKS_TO does not exist."),
            )
        )
        driver.execute_query_sync(
            "MATCH (a:Function), (b:Function) CREATE (a)-[:LINKS_TO]->(b)"
        )
        ddl = [s[0] for s in connection.statements if "CREATE REL TABLE" in s[0]]
        assert (
            "CREATE REL TABLE IF NOT EXISTS `LINKS_TO` (FROM `Function` TO `Function`)" in ddl
        )
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Named-graph routing (one store file per graph)
# ---------------------------------------------------------------------------


def test_read_on_missing_store_raises_database_not_found(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="database does not exist") as exc_info:
            driver.execute_query_sync("MATCH (n) RETURN n", database="ghost_graph")
        assert is_database_not_found_error(exc_info.value)
        # No store file was created by the read.
        assert not (tmp_path / "code.lbug" / "ghost_graph").exists()
    finally:
        driver.close()


def test_write_creates_secondary_store_beside_primary(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        driver.execute_query_sync(
            "CREATE (n:Function {id: 'x'})", database="doc_graph"
        )
        secondary = tmp_path / "code.lbug" / "doc_graph"
        assert secondary.exists()
        # Routing caches the new connection for subsequent queries.
        assert driver._connections["doc_graph"] is not None
    finally:
        driver.close()


def test_primary_and_secondary_stores_are_isolated(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        primary_conn = _primary_connection(driver)
        primary_conn.script.append(
            (
                lambda q: "hyper" in str(q),
                FakeQueryResult(["marker"], [["primary-row"]]),
            )
        )
        primary_records, _, _ = driver.execute_query_sync(
            "MATCH (n) RETURN 'hyper' AS marker"
        )
        driver.execute_query_sync("CREATE (n:Function {id: 'x'})", database="doc_graph")
        secondary_conn = driver._connections["doc_graph"]
        secondary_conn.script.append(
            (
                lambda q: "marker" in q,
                FakeQueryResult(["marker"], [["secondary-row"]]),
            )
        )
        secondary_records, _, _ = driver.execute_query_sync(
            "MATCH (n) RETURN 'doc' AS marker", database="doc_graph"
        )
        assert primary_records[0]["marker"] == "primary-row"
        assert secondary_records[0]["marker"] == "secondary-row"
        assert secondary_conn is not primary_conn
    finally:
        driver.close()


def test_sibling_stores_open_read_only_without_lease(tmp_path: Path, fake_ladybug) -> None:
    # Primary writer owns the lease for its own store.
    writer = _make_driver(tmp_path, graph="hyper_graph")
    try:
        sibling_store = tmp_path / "code.lbug" / "doc_graph"
        sibling_store.touch()
        # The reader's primary is a different store (as in the MCP fan-out:
        # one instance's primary plus every other instance as siblings).
        reader = _make_driver(tmp_path, graph="reader_main", additional_paths=[sibling_store])
        try:
            reader.execute_query_sync("MATCH (n) RETURN n", database="doc_graph")
            # Sibling store was opened read-only, without a lease.
            assert FakeDatabase.instances[-1].read_only
            assert reader._connections["doc_graph"] is reader._sibling_connections["doc_graph"]
            # The primary of the writer was never touched by the reader.
            assert "hyper_graph" not in reader._connections
        finally:
            reader.close()
        # Primary writer's lease still held (reader never touched it).
        assert writer._storage_lease is not None
    finally:
        writer.close()


def test_introspection_read_on_missing_store_does_not_create_it(
    tmp_path: Path, fake_ladybug
) -> None:
    driver = _make_driver(tmp_path)
    try:
        # CALL-classified reads (e.g. show_tables fan-out) are read intent:
        # they must surface "database does not exist", never create a store.
        with pytest.raises(RuntimeError, match="database does not exist"):
            driver.execute_query_sync(
                "CALL show_tables() RETURN *", database="ghost_graph"
            )
        assert not (tmp_path / "code.lbug" / "ghost_graph").exists()
    finally:
        driver.close()


def test_write_on_sibling_graph_opens_local_store(tmp_path: Path, fake_ladybug) -> None:
    (tmp_path / "code.lbug").mkdir(parents=True)
    sibling_store = tmp_path / "code.lbug" / "doc_graph"
    sibling_store.touch()
    driver = _make_driver(
        tmp_path, graph="reader_main", additional_paths=[sibling_store]
    )
    try:
        # The sibling read-only connection must not serve writes: the driver
        # opens this instance's own doc_graph store instead.
        driver.execute_query_sync("CREATE (n:Function {id: 'x'})", database="doc_graph")
        connection = driver._connections["doc_graph"]
        assert not driver._is_sibling_connection(connection)
    finally:
        driver.close()


def test_secondary_store_receives_schema_bootstrap(tmp_path: Path, fake_ladybug) -> None:
    from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA

    driver = _make_driver(tmp_path)
    try:
        driver.execute_query_sync(
            "CREATE (n:Function {id: 'x'})", database="doc_graph"
        )
        secondary = driver._connections["doc_graph"]
        statements = [q for q, _ in secondary.statements]
        bootstrap = {q for q in statements if "CREATE NODE TABLE IF NOT EXISTS" in q}
        expected_labels = {index.label for index in CODE_GRAPH_SCHEMA.indexes}
        for label in expected_labels:
            assert any(f"CREATE NODE TABLE IF NOT EXISTS `{label}`" in q for q in bootstrap)
    finally:
        driver.close()


def test_siblings_missing_files_are_skipped(tmp_path: Path, fake_ladybug) -> None:
    driver = LadybugDriver(
        tmp_path / "code.lbug" / "hyper_graph",
        graph="hyper_graph",
        additional_paths=[tmp_path / "code.lbug" / "not_there"],
    )
    try:
        assert driver._sibling_connections == {}
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalization_node_rel_path(tmp_path: Path, fake_ladybug) -> None:
    from tools.graph.driver.ladybug_driver import _normalize_ladybug_value

    node = {
        "_ID": {"offset": 1, "table": 0},
        "_LABEL": "Function",
        "id": "f1",
        "name": "run",
    }
    normalized_node = _normalize_ladybug_value(node)
    assert normalized_node == {
        "id": "f1",
        "name": "run",
        "_label": "Function",
        "_id": 1,
        "_graph_id": 1,
    }

    rel = {
        "_SRC": {"offset": 1, "table": 0},
        "_DST": {"offset": 2, "table": 0},
        "_LABEL": "CALLS",
        "_ID": {"offset": 0, "table": 5},
        "weight": 2.0,
    }
    normalized_rel = _normalize_ladybug_value(rel)
    assert normalized_rel["_type"] == "CALLS"
    assert normalized_rel["_start_id"] == 1
    assert normalized_rel["_end_id"] == 2
    assert normalized_rel["weight"] == 2.0
    assert "_label" not in normalized_rel

    path = {"_NODES": [node], "_RELS": [rel]}
    normalized_path = _normalize_ladybug_value(path)
    assert normalized_path["nodes"][0]["_label"] == "Function"
    assert normalized_path["edges"][0]["_type"] == "CALLS"


# ---------------------------------------------------------------------------
# Indexes / introspection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_indexes_maps_range_to_art(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        await driver.create_indexes(
            [{"label": "Function", "property": "id", "type": "range"}]
        )
        executed = [q for q, _ in _primary_connection(driver).statements]
        assert any("CREATE ART INDEX" in q and "`Function`" in q for q in executed)
    finally:
        driver.close()


@pytest.mark.asyncio
async def test_create_indexes_tolerates_already_exists(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        _primary_connection(driver).script.append(
            (
                lambda q: "CREATE ART INDEX" in q,
                RuntimeError("Binder exception: idx_function_id already exists in catalog."),
            )
        )
        await driver.create_indexes(
            [{"label": "Function", "property": "id", "type": "range"}]
        )
    finally:
        driver.close()


@pytest.mark.asyncio
async def test_inspect_indexes_normalizes_pk_art_and_fts(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "show_indexes" in q,
                FakeQueryResult(
                    ["table_name", "index_name", "index_type", "property_names", "extension_loaded", "index_definition"],
                    [
                        ["Function", "_PK", "HASH", ["id"], True, ""],
                        ["Function", "idx_function_name", "ART", ["name"], True, ""],
                        ["Function", "idx_function_name_fts", "FTS", ["name"], True, ""],
                    ],
                ),
            )
        )
        records = await driver.inspect_indexes()
        range_entries = {
            (r["label"], r["properties"][0]): r for r in records if r["index_type"] == "range"
        }
        assert range_entries[("Function", "id")]["status"] == "ONLINE"
        assert range_entries[("Function", "name")]["index_type"] == "range"
        fts = [r for r in records if r["index_type"] == "fulltext"]
        assert fts and fts[0]["label"] == "Function"
        assert fts[0]["properties"] == ["name"]
        # Every reported index is ONLINE for preflight readiness.
        assert all(r["status"] == "ONLINE" for r in records)
    finally:
        driver.close()


@pytest.mark.asyncio
async def test_list_relationship_types_and_labels(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)

        def _tables_result() -> FakeQueryResult:
            # Fresh instance per call: FakeQueryResult consumes its rows.
            return FakeQueryResult(
                ["id", "name", "type", "database name", "comment"],
                [
                    [0, "Function", "NODE", "main(graph)", ""],
                    [1, "CALLS", "REL", "main(graph)", ""],
                    [2, "contains", "REL", "main(graph)", ""],
                ],
            )

        connection.script.append((lambda q: "show_tables" in q, _tables_result()))
        rel_types = await driver.list_relationship_types()
        assert rel_types == ["CALLS", "CONTAINS"]
        connection.script.append((lambda q: "show_tables" in q, _tables_result()))
        labels = await driver.list_labels()
        assert labels == ["Function"]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def test_bootstrap_creates_manifest_tables_once(tmp_path: Path, fake_ladybug) -> None:
    from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA

    driver = _make_driver(tmp_path)
    try:
        statements = [q for q, _ in _primary_connection(driver).statements]
        node_tables = {q for q in statements if "CREATE NODE TABLE IF NOT EXISTS" in q}
        expected_labels = {index.label for index in CODE_GRAPH_SCHEMA.indexes}
        for label in expected_labels:
            assert (
                f"CREATE NODE TABLE IF NOT EXISTS `{label}` (id STRING, name STRING, "
                "file_path STRING, path STRING, qualified_name STRING, project_id STRING, "
                "project_id_normalized STRING, PRIMARY KEY(id))"
            ) in node_tables
        rel_tables = {q for q in statements if "CREATE REL TABLE IF NOT EXISTS" in q}
        for name, sources, targets, _required in CODE_GRAPH_SCHEMA.relationship_types:
            assert any(f"CREATE REL TABLE IF NOT EXISTS `{name}`" in q for q in rel_tables)
    finally:
        driver.close()

    statement_count = len(_primary_connection(driver).statements) if driver._primary_connection else 0
    # Re-open the same store: the per-process cache skips the bootstrap DDL.
    driver2 = LadybugDriver(
        tmp_path / "code.lbug" / "hyper_graph", graph="hyper_graph"
    )
    try:
        assert _primary_connection(driver2).statements == []
    finally:
        driver2.close()
    assert statement_count >= 0


# ---------------------------------------------------------------------------
# Bulk load
# ---------------------------------------------------------------------------


def test_bulk_load_counts_skipped_rows(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)
    try:
        connection = _primary_connection(driver)
        connection.script.append(
            (
                lambda q: "COPY" in q,
                # get_num_tuples() counts rows, mirroring the real COPY result.
                FakeQueryResult(["Count"], [[1], [2], [3]]),
            )
        )
        connection.script.append(
            (
                lambda q: "show_warnings" in q,
                FakeQueryResult(
                    ["query_id", "message", "file_path", "line_number", "skipped_line_or_record"],
                    [
                        [2, "Found duplicated primary key value p1, which violates the uniqueness constraint of the primary key column.", "", 0, ""],
                        [3, "Found duplicated primary key value p2, which violates the uniqueness constraint of the primary key column.", "", 0, ""],
                        [4, "some other warning", "", 0, ""],
                    ],
                ),
            )
        )
        frame = _StubFrame()
        result = driver.bulk_load("Function", frame)
        assert result == {"inserted": 3, "skipped": 2}
        copy_statements = [q for q, _ in connection.statements if "COPY" in q]
        assert copy_statements == ["COPY `Function` FROM $frame (ignore_errors=true)"]
        create_statements = [q for q, _ in connection.statements if "CREATE NODE TABLE" in q and "`Function`" in q]
        # The bootstrap already created `Function` with the shared base
        # columns; bulk_load's IF NOT EXISTS from the frame is the last one.
        assert create_statements[-1] == (
            "CREATE NODE TABLE IF NOT EXISTS `Function` (id STRING, `name` STRING, PRIMARY KEY(id))"
        )
    finally:
        driver.close()


class _StubFrame:
    """Minimal DataFrame stand-in: columns + per-column dtype access."""

    columns = ["id", "name"]

    def __getitem__(self, key: str):
        class _Column:
            dtype = "object"

        return _Column()


# ---------------------------------------------------------------------------
# Close semantics
# ---------------------------------------------------------------------------


def test_deferred_close_while_native_operation_in_flight(tmp_path: Path, fake_ladybug) -> None:
    driver = _make_driver(tmp_path)

    started = threading.Event()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()

        def slow_operation() -> None:
            # Plain blocking function: native calls are synchronous work
            # executed on the daemon thread, never coroutine objects.
            started.set()
            time.sleep(0.2)

        future = asyncio.ensure_future(driver._run_in_executor(slow_operation))
        await loop.run_in_executor(None, started.wait)
        driver.close()  # must defer, not close underneath the native call
        assert driver._deferred_close
        await future
        # Completion callback performs the deferred close.
        assert driver._resources_closed

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Optional integration tests against the real wheel
# ---------------------------------------------------------------------------


def _real_ladybug():
    try:
        import ladybug  # noqa: F401
    except ImportError:
        pytest.skip("ladybug package not installed")
    return ladybug


@pytest.mark.ladybug
def test_integration_round_trip(tmp_path: Path, monkeypatch) -> None:
    _real_ladybug()
    monkeypatch.setenv("LADYBUG_QUERY_TIMEOUT_MS", "30000")
    store = tmp_path / "integration.lbug"
    driver = LadybugDriver(store, graph="hyper_graph")
    try:
        records, keys, _ = driver.execute_query_sync(
            "UNWIND $nodes AS node CREATE (n:Function {id: node.id}) SET n.name = node.name "
            "RETURN count(n) AS count",
            {"nodes": [{"id": "a", "name": "alpha"}, {"id": "b", "name": "beta"}]},
        )
        assert records[0]["count"] == 2

        records, _, _ = driver.execute_query_sync(
            "MATCH (n:Function) RETURN n ORDER BY n.id"
        )
        assert [r["n"]["id"] for r in records] == ["a", "b"]
        assert records[0]["n"]["_label"] == "Function"

        # MERGE upsert parity
        driver.execute_query_sync(
            "MERGE (n:Function {id: 'a'}) SET n.name = 'alpha2'"
        )
        records, _, _ = driver.execute_query_sync(
            "MATCH (n:Function {id: 'a'}) RETURN n.name AS name"
        )
        assert records[0]["name"] == "alpha2"

        rel_types = asyncio.run(driver.list_relationship_types())
        assert "CALLS" not in rel_types  # empty store has no rel tables yet
    finally:
        driver.close()


@pytest.mark.ladybug
def test_integration_multi_graph_isolation(tmp_path: Path, monkeypatch) -> None:
    _real_ladybug()
    monkeypatch.setenv("LADYBUG_QUERY_TIMEOUT_MS", "30000")
    store = tmp_path / "code.lbug" / "hyper_graph"
    driver = LadybugDriver(store, graph="hyper_graph")
    try:
        driver.execute_query_sync(
            "CREATE (n:Function {id: 'only-primary'})", database="hyper_graph"
        )
        driver.execute_query_sync(
            "CREATE (n:Function {id: 'only-secondary'})", database="doc_graph"
        )
        primary_ids = [
            r["n"]["id"]
            for r in driver.execute_query_sync("MATCH (n:Function) RETURN n")[0]
        ]
        secondary_ids = [
            r["n"]["id"]
            for r in driver.execute_query_sync(
                "MATCH (n:Function) RETURN n", database="doc_graph"
            )[0]
        ]
        assert primary_ids == ["only-primary"]
        assert secondary_ids == ["only-secondary"]
    finally:
        driver.close()

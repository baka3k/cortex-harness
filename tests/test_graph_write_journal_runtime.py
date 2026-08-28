from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.journal import (  # noqa: E402
    BatchStatus,
    JournalError,
    RunStatus,
    SQLiteJournal,
    configure_journal_env,
    finalize_journal_from_env,
    install_required_write_guard,
)
from tools.graph.journal.consumer import resume_journal  # noqa: E402
from tools.graph.journal.executor import compile_persisted_mutation  # noqa: E402
from tools.graph.journal.guard import journaled_mutation  # noqa: E402
from tools.graph.journal.operation import (  # noqa: E402
    GraphWriteOperation,
    operation_for_custom_query,
)
from tools.graph.journal.retry import classify_error, retry_at  # noqa: E402
from tools.graph.journal.runtime import GraphWriteJournalRuntime  # noqa: E402
from tools.graph.schema import (  # noqa: E402
    CODE_GRAPH_SCHEMA,
    GraphSchemaManifest,
)
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


class _Driver:
    provider = "falkordb"

    def __init__(self, config=None) -> None:
        self.journal_config = config
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        count = 1 if "GraphWriteReceipt" in query else len(values.get("rows", []))
        return ([{"count": count}], [], None)


class _MissingReadbackDriver(_Driver):
    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        count = 0 if "GraphWriteReceipt" in query else len(values.get("rows", []))
        return ([{"count": count}], [], None)


class _ConsumerDriver(_Driver):
    def __init__(self) -> None:
        super().__init__()
        self.receipts: set[str] = set()

    async def ensure_schema(self, manifest=None, database=None):
        self.calls.append(("SCHEMA_PREFLIGHT", {"database": database}))
        return SimpleNamespace(
            fingerprint=(manifest or CODE_GRAPH_SCHEMA).fingerprint
        )

    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        if "source_matches" in query and "target_matches" in query:
            return ([], [], None)
        job_id = values.get("job_id")
        if query.lstrip().startswith("MATCH (receipt:GraphWriteReceipt"):
            return ([{"count": int(job_id in self.receipts)}], [], None)
        mutation_job_id = values.get("__journal_job_id")
        if mutation_job_id:
            self.receipts.add(str(mutation_job_id))
        return ([{"count": len(values.get("rows", []))}], [], None)


class _ReadbackUnavailableDriver(_ConsumerDriver):
    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        if query.lstrip().startswith("MATCH (receipt:GraphWriteReceipt"):
            raise ConnectionError("receipt store temporarily unavailable")
        return ([{"count": len(values.get("rows", []))}], [], None)


class _DuplicateReceiptDriver(_ConsumerDriver):
    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        if query.lstrip().startswith("MATCH (receipt:GraphWriteReceipt"):
            return ([{"count": 2}], [], None)
        return ([{"count": len(values.get("rows", []))}], [], None)


def _config(tmp_path: Path, parser: str = "python"):
    env: dict[str, str] = {}
    config = configure_journal_env(
        env,
        root=tmp_path / "source",
        project_id="demo",
        parser=parser,
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=f"falkordb:{tmp_path}/code.rdb:demo",
        cache_dir=tmp_path / "cache",
        generation="attempt-1",
    )
    return env, config


def test_retry_taxonomy_and_backoff_are_bounded_and_injectable() -> None:
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    deadline = retry_at(
        2,
        now=now,
        base_seconds=2,
        max_seconds=10,
        jitter=lambda _low, high: high,
    )
    assert deadline == now + timedelta(seconds=5)
    assert retry_at(
        10,
        now=now,
        base_seconds=2,
        max_seconds=10,
        jitter=lambda _low, high: high,
    ) == now + timedelta(seconds=10)
    assert classify_error(ConnectionError("offline")).value == "transient"
    assert classify_error(ValueError("bad contract")).value == "terminal"


@pytest.mark.asyncio
async def test_required_writer_enqueues_before_mutation_and_parent_drains(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path)
    writer = LanguageCodeWriter(_Driver(config), batch_size=10)
    observed: list[BatchStatus] = []

    async def write(batch):
        counts = writer._journal_runtime.journal.status_counts(  # type: ignore[union-attr]
            writer._journal_runtime.run.run_id  # type: ignore[union-attr]
        )
        assert counts[BatchStatus.LEASED] == 1
        observed.append(BatchStatus.LEASED)
        return len(batch)

    assert await writer.write_batches("files", [{"id": "a"}], write) == 1
    assert observed == [BatchStatus.LEASED]
    writer.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


@pytest.mark.asyncio
async def test_specialized_node_batch_uses_the_trusted_replay_compiler(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path, parser="cplus")
    driver = _ConsumerDriver()
    driver.journal_config = config
    writer = LanguageCodeWriter(driver, batch_size=10)

    assert await writer.write_node_properties_batch(
        "SqlStatement",
        "SqlStatement",
        [
            {
                "id": "sql-1",
                "name": "SELECT",
                "node_type": "code",
                "project_id": "demo",
            }
        ],
    ) == 1
    await writer.close_node_production_and_drain_edges()
    writer.close_journal()

    mutation = next(query for query, _parameters in driver.calls if "SqlStatement" in query)
    assert "MERGE (n:SqlStatement {id: row.id})" in mutation
    assert "SET n += row, n.updated_at = datetime()" in mutation
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


@pytest.mark.asyncio
async def test_specialized_node_batch_rejects_unindexed_identity(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path, parser="cplus")
    driver = _Driver(config)
    writer = LanguageCodeWriter(driver)

    with pytest.raises(ValueError, match="has no required 'id' identity index"):
        await writer.write_node_properties_batch(
            "custom",
            "UnregisteredNode",
            [{"id": "unsafe"}],
        )
    assert driver.calls == []
    writer.close_journal()


@pytest.mark.asyncio
async def test_done_batch_is_skipped_on_identical_outer_replay(tmp_path: Path) -> None:
    env, config = _config(tmp_path)
    calls = 0

    async def write(batch):
        nonlocal calls
        calls += 1
        return len(batch)

    first = LanguageCodeWriter(_Driver(config))
    assert await first.write_batches("files", [{"id": "a"}], write) == 1
    first.close_journal()

    second = LanguageCodeWriter(_Driver(config))
    assert await second.write_batches("files", [{"id": "a"}], write) == 1
    second.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED
    assert calls == 1


@pytest.mark.asyncio
async def test_new_generation_rebuilds_identical_source_snapshot(tmp_path: Path) -> None:
    env, config = _config(tmp_path)
    calls = 0

    async def write(batch):
        nonlocal calls
        calls += 1
        return len(batch)

    first = LanguageCodeWriter(_Driver(config))
    await first.write_batches("files", [{"id": "a"}], write)
    first.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED

    second_env = dict(env)
    second_config = configure_journal_env(
        second_env,
        root=tmp_path / "source",
        project_id="demo",
        parser="python",
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=f"falkordb:{tmp_path}/code.rdb:demo",
        cache_dir=tmp_path / "cache",
        generation="attempt-2",
    )
    second = LanguageCodeWriter(_Driver(second_config))
    await second.write_batches("files", [{"id": "a"}], write)
    second.close_journal()
    assert finalize_journal_from_env(second_env) is RunStatus.DRAINED
    assert calls == 2


@pytest.mark.asyncio
async def test_ambiguous_failure_is_reconciled_by_idempotent_outer_replay(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path)
    attempts = 0

    async def write(batch):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("lost acknowledgement")
        return len(batch)

    first = LanguageCodeWriter(_Driver(config))
    with pytest.raises(ConnectionError, match="lost acknowledgement"):
        await first.write_batches("files", [{"id": "a"}], write)
    first.close_journal()

    second = LanguageCodeWriter(_MissingReadbackDriver(config))
    assert await second.write_batches("files", [{"id": "a"}], write) == 1
    second.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED
    assert attempts == 2


@pytest.mark.asyncio
async def test_readback_acks_ambiguous_effect_without_replaying_mutation(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path)
    attempts = 0

    async def write(batch):
        nonlocal attempts
        attempts += 1
        raise ConnectionError("commit response lost")

    first = LanguageCodeWriter(_Driver(config))
    with pytest.raises(ConnectionError, match="commit response lost"):
        await first.write_batches("files", [{"id": "a"}], write)
    first.close_journal()

    # The recording driver's readback reports that the node exists. The
    # recovered batch is ACKed without submitting the mutation a second time.
    second = LanguageCodeWriter(_Driver(config))
    assert await second.write_batches("files", [{"id": "a"}], write) == 1
    second.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED
    assert attempts == 1


def test_required_parent_gate_rejects_missing_analyzer_journal(tmp_path: Path) -> None:
    env, _config_value = _config(tmp_path)
    with pytest.raises(JournalError, match="was not created"):
        finalize_journal_from_env(env)


def test_schema_v1_migrates_persisted_operation_contract(tmp_path: Path) -> None:
    _env, config = _config(tmp_path)
    with SQLiteJournal(config.path):
        pass
    connection = sqlite3.connect(config.path)
    connection.execute("ALTER TABLE batches DROP COLUMN operation_json")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    with SQLiteJournal(config.path) as journal:
        version = journal._connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {
            row[1]
            for row in journal._connection.execute("PRAGMA table_info(batches)")
        }
    assert version == 3
    assert "operation_json" in columns


def test_schema_v1_rejects_active_batch_without_operation_descriptor(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.close()
    connection = sqlite3.connect(config.path)
    connection.execute("ALTER TABLE batches DROP COLUMN operation_json")
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    with pytest.raises(JournalError, match="active batches"):
        SQLiteJournal(config.path)


@pytest.mark.asyncio
async def test_autonomous_consumer_replays_artifact_without_producer_closure(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(
        label="files",
        rows=[{"id": "a.py"}, {"id": "b.py"}],
        sequence=0,
    )
    assert ticket.execute is True
    runtime.close()

    driver = _ConsumerDriver()
    assert await resume_journal(config, driver) == 2
    mutations = [
        query
        for query, _parameters in driver.calls
        if "MERGE (n:File" in query
    ]
    assert len(mutations) == 1
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


@pytest.mark.asyncio
async def test_autonomous_consumer_preflights_before_receipt_readback_or_mutation(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.close()

    driver = _ConsumerDriver()
    await resume_journal(config, driver)

    assert driver.calls[0][0] == "SCHEMA_PREFLIGHT"
    graph_queries = [query for query, _parameters in driver.calls[1:]]
    assert graph_queries
    assert graph_queries[0].startswith("MATCH (receipt:GraphWriteReceipt")
    assert any("MERGE (n:File" in query for query in graph_queries)


@pytest.mark.asyncio
async def test_autonomous_consumer_rejects_driver_without_schema_preflight(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.close()
    driver = _Driver(config)

    with pytest.raises(JournalError, match="does not implement required schema preflight"):
        await resume_journal(config, driver)

    assert driver.calls == []
    with SQLiteJournal(config.path) as journal:
        batch = journal.get_batch(ticket.batch.job_id)
        assert batch is not None
        assert batch.status is BatchStatus.LEASED


@pytest.mark.asyncio
async def test_autonomous_consumer_acks_exact_receipt_without_replay(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.close()

    driver = _ConsumerDriver()
    driver.receipts.add(ticket.batch.job_id)
    assert await resume_journal(config, driver) == 1
    assert not any("MERGE (n:File" in query for query, _ in driver.calls)
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


@pytest.mark.asyncio
async def test_transient_receipt_failure_stays_reconciling_without_mutation_retry(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.mark_ambiguous(ticket)
    runtime.close()

    driver = _ReadbackUnavailableDriver()
    with pytest.raises(ConnectionError, match="temporarily unavailable"):
        await resume_journal(config, driver)

    assert not any("MERGE (n:File" in query for query, _ in driver.calls)
    with SQLiteJournal(config.path) as journal:
        recovered = journal.get_batch(ticket.batch.job_id)
        assert recovered is not None
        assert recovered.status is BatchStatus.RECONCILING
        assert recovered.fencing_token is None
        assert recovered.next_attempt_at is not None
        assert journal.claim_batch(run_id_value=ticket.batch.run_id) is None


@pytest.mark.asyncio
async def test_inline_recovery_backs_off_transient_receipt_failure(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.mark_ambiguous(ticket)
    runtime.close()

    driver = _ReadbackUnavailableDriver()
    driver.journal_config = config
    writer = LanguageCodeWriter(driver)
    with pytest.raises(ConnectionError, match="temporarily unavailable"):
        await writer.write_batches("files", [{"id": "a.py"}], lambda _rows: None)
    writer.close_journal()

    assert not any("MERGE (n:File" in query for query, _ in driver.calls)
    with SQLiteJournal(config.path) as journal:
        recovered = journal.get_batch(ticket.batch.job_id)
        assert recovered is not None
        assert recovered.status is BatchStatus.RECONCILING
        assert recovered.fencing_token is None
        assert recovered.next_attempt_at is not None


@pytest.mark.asyncio
async def test_invalid_receipt_cardinality_blocks_without_mutation_retry(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.mark_ambiguous(ticket)
    runtime.close()

    driver = _DuplicateReceiptDriver()
    with pytest.raises(JournalError, match="must be zero or one"):
        await resume_journal(config, driver)

    assert not any("MERGE (n:File" in query for query, _ in driver.calls)
    with SQLiteJournal(config.path) as journal:
        blocked = journal.get_batch(ticket.batch.job_id)
        assert blocked is not None
        assert blocked.status is BatchStatus.BLOCKED


@pytest.mark.asyncio
async def test_inline_recovery_blocks_invalid_receipt_cardinality(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.mark_ambiguous(ticket)
    runtime.close()

    driver = _DuplicateReceiptDriver()
    driver.journal_config = config
    writer = LanguageCodeWriter(driver)
    with pytest.raises(JournalError, match="no safe readback"):
        await writer.write_batches("files", [{"id": "a.py"}], lambda _rows: None)
    writer.close_journal()

    assert not any("MERGE (n:File" in query for query, _ in driver.calls)
    with SQLiteJournal(config.path) as journal:
        blocked = journal.get_batch(ticket.batch.job_id)
        assert blocked is not None
        assert blocked.status is BatchStatus.BLOCKED


@pytest.mark.asyncio
async def test_compatible_draining_run_resumes_across_generation_attempt(
    tmp_path: Path,
) -> None:
    env, first_config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(first_config)
    ticket = runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.close()
    with SQLiteJournal(first_config.path) as journal:
        assert (
            journal.close_run_production(ticket.batch.run_id).status
            is RunStatus.DRAINING
        )

    next_env: dict[str, str] = {}
    resumed_config = configure_journal_env(
        next_env,
        root=tmp_path / "source",
        project_id="demo",
        parser="python",
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=f"falkordb:{tmp_path}/code.rdb:demo",
        cache_dir=tmp_path / "cache",
        generation="attempt-2",
    )
    assert resumed_config.metadata.generation == first_config.metadata.generation

    driver = _ConsumerDriver()
    await resume_journal(resumed_config, driver)
    with SQLiteJournal(resumed_config.path) as journal:
        assert journal.get_run(ticket.batch.run_id).status is RunStatus.DRAINED  # type: ignore[union-attr]

    callbacks = 0

    async def should_not_replay(_batch):
        nonlocal callbacks
        callbacks += 1
        return 1

    writer = LanguageCodeWriter(_Driver(resumed_config))
    assert await writer.write_batches(
        "files", [{"id": "a.py"}], should_not_replay
    ) == 1
    writer.close_journal()
    assert finalize_journal_from_env(next_env) is RunStatus.DRAINED
    assert callbacks == 0


@pytest.mark.asyncio
async def test_atomic_receipt_round_trip_on_embedded_falkordb(tmp_path: Path) -> None:
    pytest.importorskip("redislite.falkordb_client")
    from tools.graph.driver.falkordb_driver import FalkorDBDriver

    driver = FalkorDBDriver(
        path=tmp_path / "graph.rdb", graph="journal_test", owner_id="journal-test"
    )
    receipt_indexes = tuple(
        index
        for index in CODE_GRAPH_SCHEMA.indexes
        if index.label == "GraphWriteReceipt"
    )
    receipt_manifest = GraphSchemaManifest("journal_receipt", 1, receipt_indexes)
    result = await driver.ensure_schema(
        receipt_manifest, database="journal_test", timeout_seconds=20
    )
    assert result.verified_count == 1
    install_required_write_guard(driver)
    try:
        with journaled_mutation("job-1", "graph-write/v1/nodes/files"):
            records, _, _ = await driver.execute_query(
                "UNWIND $rows AS row "
                "MERGE (n:File {id: row.id}) "
                "SET n += row RETURN count(n) AS count",
                {
                    "rows": [{"id": "a.py"}, {"id": "b.py"}],
                },
                "journal_test",
            )
        receipt, _, _ = await driver.execute_query(
            "MATCH (r:GraphWriteReceipt {id: $id}) "
            "RETURN r.row_count AS count",
            {"id": "job-1"},
            "journal_test",
        )
        receipt_plan = str(
            driver.graph.explain(
                "MATCH (r:GraphWriteReceipt {id: $id}) "
                "RETURN r.row_count AS count",
                params={"id": "job-1"},
            )
        )
    finally:
        driver.close()
    assert records == [{"count": 2}]
    assert receipt == [{"count": 2}]
    assert "Node By Index Scan | (r:GraphWriteReceipt)" in receipt_plan
    assert "All Node Scan" not in receipt_plan


def test_receipt_write_records_retention_evidence_without_unsafe_age_cleanup() -> None:
    from tools.graph.journal.guard import _receipt_query

    query = _receipt_query(
        "UNWIND $rows AS row MERGE (n:File {id: row.id}) "
        "RETURN count(n) AS count",
        returns_count=True,
    )

    assert "receipt.applied_at = datetime()" in query
    assert "DELETE receipt" not in query
    assert "DETACH DELETE receipt" not in query


@pytest.mark.asyncio
async def test_no_return_mutation_counts_surviving_rows_and_binds_operation_key() -> None:
    driver = _ConsumerDriver()
    install_required_write_guard(driver)
    with journaled_mutation("job-2", "graph-write/v1/custom/calls-api"):
        records, _, _ = await driver.execute_query(
            "UNWIND $rows AS row "
            "MATCH (source:Function {id: row.source_id}) "
            "MATCH (target:ApiCall {id: row.target_id}) "
            "MERGE (source)-[:CALLS_API]->(target)",
            {"rows": [{"source_id": "f", "target_id": "api"}]},
        )
    query, parameters = driver.calls[-1]
    assert "WITH count(*) AS __journal_count" in query
    assert parameters["__journal_operation_key"] == (
        "graph-write/v1/custom/calls-api"
    )
    assert records == [{"count": 1}]


def test_unsupported_custom_shape_is_rejected_before_durable_enqueue(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(config)
    operation = operation_for_custom_query(
        "custom:fixed-property",
        "UNWIND $rows AS row "
        "MERGE (n:Resource {id: row.id}) "
        "SET n.name = row.name, n.node_type = 'code'",
    )
    try:
        with pytest.raises(JournalError, match="no trusted recovery compiler"):
            runtime.prepare(
                label=operation.label,
                rows=[{"id": "r1", "name": "R"}],
                sequence=0,
                operation=operation,
            )
        assert runtime.journal.status_counts(runtime.run.run_id)[BatchStatus.PENDING] == 0
    finally:
        runtime.close()


def test_config_reuses_only_compatible_open_generation(tmp_path: Path) -> None:
    env, first = _config(tmp_path)
    runtime = GraphWriteJournalRuntime(first)
    runtime.prepare(label="files", rows=[{"id": "a.py"}], sequence=0)
    runtime.close()

    next_env: dict[str, str] = {}
    resumed = configure_journal_env(
        next_env,
        root=tmp_path / "source",
        project_id="demo",
        parser="python",
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=f"falkordb:{tmp_path}/code.rdb:demo",
        cache_dir=tmp_path / "cache",
        generation="attempt-2",
    )
    assert resumed.metadata.generation == "attempt-1"


@pytest.mark.asyncio
async def test_legacy_schema_fingerprint_is_rejected_then_quarantined(
    tmp_path: Path,
) -> None:
    env, current_config = _config(tmp_path)
    legacy_metadata = replace(
        current_config.metadata,
        schema_fingerprint="shared-graph-schema-v1",
    )
    legacy_config = replace(current_config, metadata=legacy_metadata)
    legacy_runtime = GraphWriteJournalRuntime(legacy_config)
    legacy_runtime.prepare(label="files", rows=[{"id": "legacy.py"}], sequence=0)
    legacy_run_id = legacy_runtime.run.run_id
    legacy_runtime.close()

    legacy_driver = _ConsumerDriver()
    with pytest.raises(JournalError, match="schema fingerprint is incompatible"):
        await resume_journal(legacy_config, legacy_driver)
    assert legacy_driver.calls == []

    next_env: dict[str, str] = {}
    replacement_config = configure_journal_env(
        next_env,
        root=tmp_path / "source",
        project_id="demo",
        parser="python",
        source_revision="revision-1",
        source_snapshot="snapshot-1",
        physical_target=f"falkordb:{tmp_path}/code.rdb:demo",
        cache_dir=tmp_path / "cache",
        generation="replacement",
    )
    assert replacement_config.metadata.schema_fingerprint == CODE_GRAPH_SCHEMA.fingerprint
    replacement_runtime = GraphWriteJournalRuntime(replacement_config)
    replacement_runtime.close()

    with SQLiteJournal(replacement_config.path) as journal:
        legacy_run = journal.get_run(legacy_run_id)
        assert legacy_run is not None
        assert legacy_run.status is RunStatus.QUARANTINED


def test_trusted_replay_preserves_nested_property_projection() -> None:
    operation = operation_for_custom_query(
        "cobol:CobolProgram",
        "UNWIND $rows AS row "
        "MERGE (n:CobolProgram {id: row.id}) SET n += row.properties",
    )
    query, _ = compile_persisted_mutation(
        operation, [{"id": "P1", "properties": {"name": "P1"}}]
    )
    assert "SET n += coalesce(row.properties, {})" in query


@pytest.mark.asyncio
async def test_process_kill_after_lease_resumes_exact_batch(tmp_path: Path) -> None:
    env, config = _config(tmp_path)
    process_env = dict(os.environ)
    process_env.update(env)
    process_env["PYTHONPATH"] = str(CODE_TINY)
    script = r"""
import asyncio
import os
from tools.graph.journal.config import journal_config_from_env
from tools.graph.writer.language_writer import LanguageCodeWriter

class Driver:
    provider = "falkordb"
    def __init__(self, config):
        self.journal_config = config

async def main():
    writer = LanguageCodeWriter(Driver(journal_config_from_env()))
    async def crash(batch):
        os._exit(17)
    await writer.write_batches("files", [{"id": "crash.py"}], crash)

asyncio.run(main())
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script], env=process_env, check=False
    )
    assert crashed.returncode == 17

    executions = 0

    async def write(batch):
        nonlocal executions
        executions += 1
        return len(batch)

    recovered = LanguageCodeWriter(_MissingReadbackDriver(config))
    assert await recovered.write_batches(
        "files", [{"id": "crash.py"}], write
    ) == 1
    recovered.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED
    assert executions == 1


@pytest.mark.asyncio
async def test_relationship_waits_on_both_501_file_endpoint_barriers(
    tmp_path: Path,
) -> None:
    _env, config = _config(tmp_path, parser="cplus")
    writer = LanguageCodeWriter(_Driver(config), batch_size=500)

    async def write_nodes(batch):
        return len(batch)

    files = [
        {
            "id": f"file-{index}",
            "project_id": "fixture-project",
            "project_id_normalized": "fixture-project",
        }
        for index in range(501)
    ]
    assert await writer.write_batches("files", files, write_nodes) == 501
    assert await writer.write_relations_typed(
        [
            {
                "source_label": "File",
                "source_id": "file-0",
                "target_label": "File",
                "target_id": "file-500",
                "rel_type": "INCLUDES",
                "properties": {},
            }
        ],
        project_id="fixture-project",
    ) == 1

    connection = writer._journal_runtime.journal._connection  # type: ignore[union-attr]
    row = connection.execute(
        "SELECT required_barriers_json FROM batches WHERE phase = 'relationships'"
    ).fetchone()
    required = json.loads(row["required_barriers_json"])
    assert len(required) == 4
    assert "audit:endpoints" in required
    assert "phase:nodes" in required
    statuses = connection.execute(
        "SELECT name, status FROM barriers WHERE name IN (?, ?, ?, ?) ORDER BY name",
        required,
    ).fetchall()
    status_by_name = {item["name"]: item["status"] for item in statuses}
    assert status_by_name["phase:nodes"] == "open"
    assert set(status_by_name.values()) == {"open", "drained"}
    writer.close_journal()


@pytest.mark.asyncio
async def test_cplus_node_first_stages_edges_until_global_node_drain(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path, parser="cplus")
    assert config.metadata.query_shape_version == "language-writer-node-first-v1"
    assert config.metadata.operation_versions["node-first"] == 1
    driver = _ConsumerDriver()
    driver.journal_config = config
    writer = LanguageCodeWriter(driver, batch_size=10)

    assert await writer.write_node_properties_batch(
        "files", "File", [{"id": "a.c", "repo": "Demo/project"}]
    ) == 1
    calls_before_edge = len(driver.calls)
    assert await writer.write_repo_file_edges(
        [{"id": "a.c", "repo": "Demo/project"}]
    ) == 1
    assert len(driver.calls) == calls_before_edge

    runtime = writer._journal_runtime
    assert runtime is not None
    barrier = runtime.journal.get_barrier(runtime.run.run_id, "phase:nodes")
    assert barrier is not None and barrier.status.value == "open"
    assert runtime.journal.status_counts(runtime.run.run_id)[BatchStatus.PENDING] == 1

    assert await writer.close_node_production_and_drain_edges() == 1
    node_index = next(
        index for index, (query, _parameters) in enumerate(driver.calls)
        if "MERGE (n:File" in query
    )
    edge_index = next(
        index for index, (query, _parameters) in enumerate(driver.calls)
        if "MATCH (repository:Repository" in query
    )
    assert node_index < edge_index
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


@pytest.mark.asyncio
async def test_serialization_failure_prevents_graph_mutation(tmp_path: Path) -> None:
    _env, config = _config(tmp_path)
    writer = LanguageCodeWriter(_Driver(config))
    mutated = False

    async def write(batch):
        nonlocal mutated
        mutated = True
        return len(batch)

    with pytest.raises((JournalError, TypeError, ValueError)):
        await writer.write_batches("files", [{"invalid": object()}], write)
    writer.close_journal()
    assert mutated is False


@pytest.mark.asyncio
async def test_count_mismatch_blocks_clean_publication(tmp_path: Path) -> None:
    env, config = _config(tmp_path)
    writer = LanguageCodeWriter(_Driver(config))

    async def short_write(batch):
        return 0

    with pytest.raises(JournalError, match="count mismatch"):
        await writer.write_batches("files", [{"id": "a"}], short_write)
    run_id = writer._journal_runtime.run.run_id  # type: ignore[union-attr]
    writer.close_journal()
    with SQLiteJournal(config.path) as journal:
        assert journal.status_counts(run_id)[BatchStatus.BLOCKED] == 1
    with pytest.raises(JournalError):
        finalize_journal_from_env(env)


@pytest.mark.asyncio
async def test_calls_are_aggregated_and_use_absolute_replay_safe_count(
    tmp_path: Path,
) -> None:
    driver = _Driver()
    writer = LanguageCodeWriter(driver)
    calls = [
        {
            "caller_id": "a",
            "callee_id": "b",
            "call_type": "direct",
            "project_id": "demo",
            "project_id_normalized": "demo",
        },
        {
            "caller_id": "a",
            "callee_id": "b",
            "call_type": "direct",
            "project_id": "demo",
            "project_id_normalized": "demo",
        },
    ]

    assert await writer.write_calls(calls) == 1
    query, parameters = driver.calls[0]
    assert "SET r.count                = row.count" in query
    assert "r.count = COALESCE" not in query
    assert parameters["rows"][0]["count"] == 2


@pytest.mark.asyncio
async def test_required_guard_rejects_direct_mutation_but_allows_writer(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path)
    driver = _Driver(config)
    install_required_write_guard(driver)
    with pytest.raises(JournalError, match="bypassed"):
        await driver.execute_query("MERGE (n:File {id: 'x'})")

    writer = LanguageCodeWriter(driver)
    assert await writer.write_files(
        [
            {
                "id": "x",
                "path": "x",
                "start_line": 1,
                "end_line": 1,
                "code": "",
                "comment": "",
                "summary": "",
                "note": "",
                "project_id": "demo",
                "project_id_normalized": "demo",
                "project_name": "demo",
                "language": "python",
                "repo": "demo",
                "build_system": "none",
            }
        ]
    ) == 1
    writer.close_journal()
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


@pytest.mark.asyncio
async def test_required_incremental_cleanup_is_journaled_node_work(
    tmp_path: Path,
) -> None:
    env, config = _config(tmp_path, parser="cplus")
    driver = _ConsumerDriver()
    driver.journal_config = config
    install_required_write_guard(driver)
    writer = LanguageCodeWriter(driver)

    assert await writer.cleanup_incremental_files(
        project_id="demo",
        file_paths=["src/old.pc", "src/old.pc"],
    ) == {"file_cleanup_jobs": 16, "orphan_cleanup_jobs": 1}

    mutation_queries = [query for query, _ in driver.calls if "DETACH DELETE" in query]
    assert len(mutation_queries) == 17
    assert all("OPTIONAL MATCH (n)" not in query for query in mutation_queries)
    assert any(
        "u.project_id = row.project_id" in query for query in mutation_queries
    )
    runtime = writer._journal_runtime
    assert runtime is not None
    assert runtime.journal.status_counts(runtime.run.run_id)[BatchStatus.DONE] == 17
    assert await writer.close_node_production_and_drain_edges() == 0
    assert finalize_journal_from_env(env) is RunStatus.DRAINED


def test_incremental_cleanup_compiler_uses_one_replay_row_per_job() -> None:
    operation = GraphWriteOperation.for_incremental_cleanup(
        "cplus:incremental_file_cleanup:File",
        reconciliation="file_cleanup",
        node_label="File",
    )
    query, parameters = compile_persisted_mutation(
        operation,
        [{"project_id": "demo", "paths": ["src/old.pc"]}],
    )

    assert operation.phase.value == "nodes"
    assert operation.version == 2
    assert "OPTIONAL MATCH (n:File)" in query
    assert "DETACH DELETE node" in query
    assert query.endswith("RETURN count(*) AS count")
    assert parameters["rows"][0]["paths"] == ["src/old.pc"]


@pytest.mark.parametrize(
    ("reconciliation", "row", "node_label"),
    [
        ("file_cleanup", {"project_id": "demo", "paths": ["old.pc"]}, "File"),
        (
            "orphan_unknown_cleanup",
            {"scope": "demo"},
            "UnknownFunction",
        ),
    ],
)
def test_legacy_v1_incremental_cleanup_replay_fails_closed(
    reconciliation: str,
    row: dict,
    node_label: str,
) -> None:
    current = GraphWriteOperation.for_incremental_cleanup(
        f"cplus:{reconciliation}",
        reconciliation=reconciliation,
        node_label=node_label,
    )
    legacy = replace(current, version=1)

    with pytest.raises(JournalError, match="no trusted replay compiler"):
        compile_persisted_mutation(legacy, [row])

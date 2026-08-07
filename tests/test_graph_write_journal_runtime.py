from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

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
from tools.graph.journal.runtime import GraphWriteJournalRuntime  # noqa: E402
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

    async def execute_query(self, query, parameters=None, database=None):
        values = dict(parameters or {})
        self.calls.append((query, values))
        job_id = values.get("job_id")
        if query.lstrip().startswith("MATCH (receipt:GraphWriteReceipt"):
            return ([{"count": int(job_id in self.receipts)}], [], None)
        mutation_job_id = values.get("__journal_job_id")
        if mutation_job_id:
            self.receipts.add(str(mutation_job_id))
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
    assert version == 2
    assert "operation_json" in columns


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
async def test_atomic_receipt_round_trip_on_embedded_falkordb(tmp_path: Path) -> None:
    pytest.importorskip("redislite.falkordb_client")
    from tools.graph.driver.falkordb_driver import FalkorDBDriver

    driver = FalkorDBDriver(
        path=tmp_path / "graph.rdb", graph="journal_test", owner_id="journal-test"
    )
    install_required_write_guard(driver)
    try:
        from tools.graph.journal.guard import journaled_mutation

        with journaled_mutation("job-1"):
            records, _, _ = await driver.execute_query(
                "UNWIND $rows AS row "
                "MERGE (n:File {id: row.id}) "
                "SET n += row RETURN count(n) AS count",
                {
                    "rows": [{"id": "a.py"}, {"id": "b.py"}],
                    "__journal_operation_key": "files",
                },
                "journal_test",
            )
        receipt, _, _ = await driver.execute_query(
            "MATCH (r:GraphWriteReceipt {id: $id}) "
            "RETURN r.row_count AS count",
            {"id": "job-1"},
            "journal_test",
        )
    finally:
        driver.close()
    assert records == [{"count": 2}]
    assert receipt == [{"count": 2}]


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

    files = [{"id": f"file-{index}"} for index in range(501)]
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
        ]
    ) == 1

    connection = writer._journal_runtime.journal._connection  # type: ignore[union-attr]
    row = connection.execute(
        "SELECT required_barriers_json FROM batches WHERE phase = 'relationships'"
    ).fetchone()
    required = json.loads(row["required_barriers_json"])
    assert len(required) == 2
    statuses = connection.execute(
        "SELECT status FROM barriers WHERE name IN (?, ?) ORDER BY name", required
    ).fetchall()
    assert [item["status"] for item in statuses] == ["drained", "drained"]
    writer.close_journal()


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

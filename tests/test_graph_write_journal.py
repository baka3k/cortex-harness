from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.journal import (  # noqa: E402
    JOURNAL_SCHEMA_VERSION,
    ArtifactRef,
    BarrierStatus,
    BatchSpec,
    BatchStatus,
    JournalError,
    JournalLimits,
    OperationPhase,
    RetryClass,
    RunMetadata,
    RunStatus,
    SQLiteJournal,
    StaleFenceError,
    TerminalErrorCode,
    deterministic_job_id,
    run_fingerprint,
)
from tools.graph.journal import artifacts as artifact_module  # noqa: E402
from tools.graph.journal.sqlite_store import inspect_journal  # noqa: E402


def _limits(**overrides: int) -> JournalLimits:
    values = {
        "max_batches_per_run": 20,
        "max_payload_bytes_per_run": 1024 * 1024,
        "max_artifact_bytes": 256 * 1024,
        "max_journal_bytes": 8 * 1024 * 1024,
        "min_free_bytes": 1,
        "retention_seconds": 60,
        "busy_timeout_ms": 250,
        "wal_autocheckpoint_pages": 32,
    }
    values.update(overrides)
    return JournalLimits(**values)


def _metadata(**overrides: object) -> RunMetadata:
    values: dict[str, object] = {
        "project_id": "demo",
        "scope_id": "scope-1",
        "source_revision": "rev-1",
        "source_snapshot": "snapshot-1",
        "physical_target": "falkordb:/data/code.rdb",
        "generation": "generation-1",
        "parser": "python",
        "parser_version": "3",
        "schema_fingerprint": "schema-1",
        "query_shape_version": "queries-1",
        "operation_versions": {"node_upsert": 1},
    }
    values.update(overrides)
    return RunMetadata(**values)  # type: ignore[arg-type]


def _journal(tmp_path: Path, **kwargs: object) -> SQLiteJournal:
    return SQLiteJournal(
        tmp_path / "journal" / "scope.sqlite3",
        limits=_limits(),
        **kwargs,  # type: ignore[arg-type]
    )


def _enqueue(
    journal: SQLiteJournal,
    run_id: str,
    *,
    phase: OperationPhase = OperationPhase.NODES,
    operation_key: str = "node_upsert:v1",
    sequence: int = 0,
    required_barriers: tuple[str, ...] = (),
    produced_barriers: tuple[str, ...] = (),
    max_attempts: int = 5,
):
    artifact = journal.create_artifact(run_id, [{"id": sequence, "name": "sample"}])
    return journal.enqueue_batch(
        run_id,
        BatchSpec(
            phase=phase,
            operation_key=operation_key,
            sequence=sequence,
            artifact=artifact,
            expected_count=1,
            required_barriers=required_barriers,
            produced_barriers=produced_barriers,
            max_attempts=max_attempts,
        ),
    )


def test_identity_is_canonical_and_scoped_to_every_compatibility_input() -> None:
    mutable_versions = {"b": 2, "a": 1}
    first = _metadata(operation_versions=mutable_versions)
    reordered = _metadata(operation_versions={"a": 1, "b": 2})
    changed_target = replace(first, generation="generation-2")

    mutable_versions["a"] = 99
    assert run_fingerprint(first) == run_fingerprint(reordered)
    assert run_fingerprint(first) != run_fingerprint(changed_target)
    assert deterministic_job_id(
        run_fingerprint_value=run_fingerprint(first),
        phase=OperationPhase.NODES,
        operation_key="upsert:v1",
        sequence=4,
        payload_sha256="a" * 64,
    ) == deterministic_job_id(
        run_fingerprint_value=run_fingerprint(first),
        phase="nodes",
        operation_key="upsert:v1",
        sequence=4,
        payload_sha256="a" * 64,
    )
    with pytest.raises(TypeError):
        first.operation_versions["a"] = 2  # type: ignore[index]
    spec = BatchSpec(
        OperationPhase.NODES,
        "upsert:v1",
        0,
        artifact=ArtifactRef("a" * 64, "run/file.jsonl", 1, 1),
        expected_count=1,
        required_barriers=["b", "a", "a"],
    )
    assert spec.required_barriers == ("a", "b")
    with pytest.raises(ValueError, match="same barrier"):
        replace(spec, produced_barriers=("a",))


def test_open_configures_wal_full_sync_foreign_keys_and_version(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        connection = sqlite3.connect(journal.path)
        try:
            assert (
                connection.execute("PRAGMA journal_mode").fetchone()[0].casefold()
                == "wal"
            )
            assert (
                connection.execute("PRAGMA user_version").fetchone()[0]
                == JOURNAL_SCHEMA_VERSION
            )
            assert journal._connection.execute("PRAGMA synchronous").fetchone()[0] >= 2
            assert journal._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            connection.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission contract")
def test_database_and_artifacts_are_owner_only(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        artifact = journal.create_artifact(run.run_id, [{"secret": "source-derived"}])
        artifact_path = journal.artifacts.path_for(artifact)

        assert stat.S_IMODE(journal.path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o400
        assert stat.S_IMODE(artifact_path.parent.stat().st_mode) == 0o700


def test_canonical_artifact_and_enqueue_are_deterministic_and_deduplicated(
    tmp_path: Path,
) -> None:
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        first_ref = journal.create_artifact(run.run_id, [{"b": 2, "a": 1}])
        second_ref = journal.create_artifact(run.run_id, [{"a": 1, "b": 2}])
        assert first_ref == second_ref

        spec = BatchSpec(OperationPhase.NODES, "node_upsert:v1", 0, first_ref, 1)
        first = journal.enqueue_batch(run.run_id, spec)
        duplicate = journal.enqueue_batch(run.run_id, spec)

        assert duplicate.job_id == first.job_id
        assert journal.status_counts(run.run_id)[BatchStatus.PENDING] == 1
        ref_count = journal._connection.execute(
            "SELECT ref_count FROM artifacts WHERE run_id = ? AND sha256 = ?",
            (run.run_id, first_ref.sha256),
        ).fetchone()[0]
        assert ref_count == 1


def test_artifact_writes_stream_with_one_placement_probe_and_clean_failed_temps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits = _limits(max_artifact_bytes=32)
    with SQLiteJournal(tmp_path / "journal.sqlite3", limits=limits) as journal:
        run = journal.open_run(_metadata())

        def unexpected_probe(_path: Path) -> str:
            raise AssertionError(
                "filesystem placement must be validated once per store"
            )

        monkeypatch.setattr(artifact_module, "_filesystem_type", unexpected_probe)
        first = journal.create_artifact(
            run.run_id, ({"id": value} for value in range(2))
        )
        assert journal.artifacts.verify(first).exists()
        with pytest.raises(JournalError) as oversized:
            journal.create_artifact(run.run_id, ({"value": "x" * 64} for _ in range(2)))
        assert oversized.value.code is TerminalErrorCode.ADMISSION_REJECTED
        run_dir = journal.artifacts.root / run.run_id
        assert not list(run_dir.glob("*.tmp"))


def test_barrier_blocks_relationship_until_producer_is_closed_and_acked(
    tmp_path: Path,
) -> None:
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        producer = _enqueue(
            journal,
            run.run_id,
            sequence=0,
            produced_barriers=("nodes:File",),
        )
        relation = _enqueue(
            journal,
            run.run_id,
            phase=OperationPhase.RELATIONSHIPS,
            operation_key="relationship_upsert:v1",
            sequence=1,
            required_barriers=("nodes:File",),
        )

        closed = journal.close_barrier(run.run_id, "nodes:File")
        assert closed.status is BarrierStatus.PRODUCED
        leased_producer = journal.claim_batch(run_id_value=run.run_id)
        assert leased_producer is not None and leased_producer.job_id == producer.job_id
        journal.ack_batch(leased_producer.job_id, leased_producer.fencing_token or "")
        assert (
            journal.get_barrier(run.run_id, "nodes:File").status
            is BarrierStatus.DRAINED
        )  # type: ignore[union-attr]
        with pytest.raises(JournalError) as late_producer:
            _enqueue(
                journal,
                run.run_id,
                sequence=2,
                produced_barriers=("nodes:File",),
            )
        assert late_producer.value.code is TerminalErrorCode.INVALID_TRANSITION
        leased_relation = journal.claim_batch(run_id_value=run.run_id)
        assert leased_relation is not None and leased_relation.job_id == relation.job_id


def test_expired_lease_is_recovered_and_old_fence_cannot_ack(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 7, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return current[0]

    journal = _journal(tmp_path, clock=clock)
    run = journal.open_run(_metadata())
    batch = _enqueue(journal, run.run_id)
    first_lease = journal.claim_batch(run_id_value=run.run_id, lease_seconds=1)
    assert first_lease is not None
    current[0] += timedelta(seconds=2)
    with pytest.raises(StaleFenceError):
        journal.ack_batch(batch.job_id, first_lease.fencing_token or "")
    journal.close()

    with _journal(tmp_path, clock=clock) as reopened:
        recovered = reopened.get_batch(batch.job_id)
        assert recovered is not None
        assert recovered.status is BatchStatus.RECONCILING
        assert recovered.fencing_token is None
        assert reopened.claim_batch(run_id_value=run.run_id) is None
        second_lease = reopened.claim_reconciling(
            run_id_value=run.run_id, lease_seconds=5
        )
        assert second_lease is not None
        assert second_lease.fencing_token != first_lease.fencing_token
        with pytest.raises(StaleFenceError):
            reopened.ack_batch(batch.job_id, first_lease.fencing_token or "")
        assert (
            reopened.ack_batch(batch.job_id, second_lease.fencing_token or "").status
            is BatchStatus.DONE
        )


def test_expired_reconciliation_is_reclaimed_for_readback_not_execution(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 8, 7, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return current[0]

    journal = _journal(tmp_path, clock=clock)
    run = journal.open_run(_metadata())
    batch = _enqueue(journal, run.run_id)
    lease = journal.claim_batch(run_id_value=run.run_id, lease_seconds=1)
    assert lease is not None
    journal.mark_reconciling(lease.job_id, lease.fencing_token or "")
    current[0] += timedelta(seconds=2)
    journal.close()

    with _journal(tmp_path, clock=clock) as reopened:
        recovered = reopened.get_batch(batch.job_id)
        assert recovered is not None and recovered.status is BatchStatus.RECONCILING
        assert recovered.fencing_token is None
        assert reopened.claim_batch(run_id_value=run.run_id) is None
        reconciliation = reopened.claim_reconciling(run_id_value=run.run_id)
        assert reconciliation is not None
        assert (
            reopened.ack_batch(
                reconciliation.job_id, reconciliation.fencing_token or ""
            ).status
            is BatchStatus.DONE
        )


def test_reconciliation_backoff_never_makes_ambiguous_work_executable(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 8, 7, tzinfo=timezone.utc)]

    with _journal(tmp_path, clock=lambda: current[0]) as journal:
        run = journal.open_run(_metadata())
        batch = _enqueue(journal, run.run_id)
        lease = journal.claim_batch(run_id_value=run.run_id)
        assert lease is not None
        reconciliation = journal.mark_reconciling(
            batch.job_id, lease.fencing_token or ""
        )

        retry = journal.schedule_reconciliation_retry(
            batch.job_id,
            reconciliation.fencing_token or "",
            retry_at=current[0] + timedelta(seconds=5),
            error_code=TerminalErrorCode.INVALID_TRANSITION,
        )
        assert retry.status is BatchStatus.RECONCILING
        assert retry.fencing_token is None
        assert journal.claim_batch(run_id_value=run.run_id) is None
        assert journal.claim_reconciling(run_id_value=run.run_id) is None

        current[0] += timedelta(seconds=5)
        retried_readback = journal.claim_reconciling(run_id_value=run.run_id)
        assert retried_readback is not None
        assert retried_readback.job_id == batch.job_id


def test_retry_exhaustion_dead_letters_batch_and_run(tmp_path: Path) -> None:
    current = datetime(2026, 8, 7, tzinfo=timezone.utc)
    with _journal(tmp_path, clock=lambda: current) as journal:
        run = journal.open_run(_metadata())
        _enqueue(journal, run.run_id, max_attempts=1)
        lease = journal.claim_batch(run_id_value=run.run_id)
        assert lease is not None
        result = journal.schedule_retry(
            lease.job_id,
            lease.fencing_token or "",
            retry_at=current + timedelta(seconds=1),
            retry_class=RetryClass.TRANSIENT,
            error_code=TerminalErrorCode.INVALID_CONTRACT,
        )
        assert result.status is BatchStatus.DEAD_LETTER
        assert result.error_code is TerminalErrorCode.MAX_ATTEMPTS
        assert journal.get_run(run.run_id).status is RunStatus.DEAD_LETTERED  # type: ignore[union-attr]


def test_incompatible_active_run_is_quarantined_not_replayed(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        first = journal.open_run(_metadata())
        batch = _enqueue(journal, first.run_id)
        lease = journal.claim_batch(run_id_value=first.run_id)
        assert lease is not None
        second = journal.open_run(_metadata(source_snapshot="snapshot-2"))

        assert first.run_id != second.run_id
        assert journal.get_run(first.run_id).status is RunStatus.QUARANTINED  # type: ignore[union-attr]
        assert journal.get_batch(batch.job_id).status is BatchStatus.BLOCKED  # type: ignore[union-attr]
        with pytest.raises(StaleFenceError):
            journal.ack_batch(batch.job_id, lease.fencing_token or "")
        assert journal.get_run(second.run_id).status is RunStatus.OPEN  # type: ignore[union-attr]


def test_reopen_fails_closed_when_artifact_hash_does_not_match(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    run = journal.open_run(_metadata())
    batch = _enqueue(journal, run.run_id)
    artifact_path = journal.artifacts.path_for(batch.artifact)
    journal.close()
    os.chmod(artifact_path, 0o600)
    artifact_path.write_bytes(b'{"tampered":true}\n')

    with pytest.raises(JournalError) as error:
        _journal(tmp_path)
    assert error.value.code is TerminalErrorCode.ARTIFACT_HASH_MISMATCH


def test_unknown_newer_schema_and_corrupt_database_fail_closed(tmp_path: Path) -> None:
    schema_path = tmp_path / "newer" / "journal.sqlite3"
    schema_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(schema_path)
    connection.execute(f"PRAGMA user_version = {JOURNAL_SCHEMA_VERSION + 1}")
    connection.close()
    with pytest.raises(JournalError) as newer:
        SQLiteJournal(schema_path, limits=_limits())
    assert newer.value.code is TerminalErrorCode.INCOMPATIBLE_SCHEMA

    corrupt_path = tmp_path / "corrupt" / "journal.sqlite3"
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_bytes(b"not-a-sqlite-database")
    with pytest.raises(JournalError) as corrupt:
        SQLiteJournal(corrupt_path, limits=_limits())
    assert corrupt.value.code is TerminalErrorCode.JOURNAL_CORRUPT


def test_admission_limits_reject_before_batch_commit(tmp_path: Path) -> None:
    limits = _limits(max_batches_per_run=1)
    with SQLiteJournal(tmp_path / "journal.sqlite3", limits=limits) as journal:
        run = journal.open_run(_metadata())
        _enqueue(journal, run.run_id, sequence=0)
        with pytest.raises(JournalError) as error:
            _enqueue(journal, run.run_id, sequence=1)
        assert error.value.code is TerminalErrorCode.ADMISSION_REJECTED
        assert journal.status_counts(run.run_id)[BatchStatus.PENDING] == 1


def test_disk_headroom_and_artifact_permission_failures_are_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    free = shutil.disk_usage(tmp_path).free
    with SQLiteJournal(
        tmp_path / "disk" / "journal.sqlite3",
        limits=_limits(min_free_bytes=free + 1),
    ) as journal:
        with pytest.raises(JournalError) as disk:
            journal.open_run(_metadata())
        assert disk.value.code is TerminalErrorCode.DISK_FULL

    with _journal(tmp_path / "permission") as journal:
        run = journal.open_run(_metadata())

        def deny_open(*args: object, **kwargs: object) -> int:
            raise PermissionError("denied")

        monkeypatch.setattr(artifact_module.os, "open", deny_open)
        with pytest.raises(JournalError) as permission:
            journal.create_artifact(run.run_id, [{"secret": "value"}])
        assert permission.value.code is TerminalErrorCode.PERMISSION_DENIED


def test_relative_and_symlinked_placements_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(JournalError) as relative:
        SQLiteJournal(Path("relative.sqlite3"), limits=_limits())
    assert relative.value.code is TerminalErrorCode.UNSAFE_PLACEMENT

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(JournalError) as symlinked:
        SQLiteJournal(alias / "journal.sqlite3", limits=_limits())
    assert symlinked.value.code is TerminalErrorCode.UNSAFE_PLACEMENT

    with _journal(tmp_path / "contained") as journal:
        with pytest.raises(JournalError) as escaped:
            journal.artifacts.write_jsonl("..", [])
        assert escaped.value.code is TerminalErrorCode.UNSAFE_PLACEMENT


@pytest.mark.parametrize("filesystem_type", [None, "nfs"])
def test_unknown_or_network_filesystem_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filesystem_type: str | None,
) -> None:
    monkeypatch.setattr(
        artifact_module, "_filesystem_type", lambda _path: filesystem_type
    )
    with pytest.raises(JournalError) as placement:
        SQLiteJournal(tmp_path / "journal.sqlite3", limits=_limits())
    assert placement.value.code is TerminalErrorCode.UNSAFE_PLACEMENT


def test_enqueue_rechecks_run_state_inside_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _journal(tmp_path)
    second = _journal(tmp_path)
    try:
        run = first.open_run(_metadata())
        artifact = first.create_artifact(run.run_id, [{"id": 1}])
        original_verify = first.artifacts.verify

        def close_then_verify(ref):
            second.close_run_production(run.run_id)
            return original_verify(ref)

        monkeypatch.setattr(first.artifacts, "verify", close_then_verify)
        with pytest.raises(JournalError) as closed:
            first.enqueue_batch(
                run.run_id,
                BatchSpec(OperationPhase.NODES, "node_upsert:v1", 0, artifact, 1),
            )
        assert closed.value.code is TerminalErrorCode.INVALID_TRANSITION
        assert first.status_counts(run.run_id)[BatchStatus.PENDING] == 0
    finally:
        first.close()
        second.close()


def test_aged_orphan_cleanup_requires_ownership_and_preserves_references(
    tmp_path: Path,
) -> None:
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        referenced = _enqueue(journal, run.run_id)
        orphan = journal.create_artifact(run.run_id, [{"orphan": True}])
        orphan_path = journal.artifacts.path_for(orphan)
        os.utime(orphan_path, (0, 0))

        with pytest.raises(JournalError, match="ownership lock"):
            journal.cleanup_orphan_artifacts(older_than_seconds=1)
        assert (
            journal.cleanup_orphan_artifacts(
                ownership_confirmed=True, older_than_seconds=1
            )
            == 1
        )
        assert not orphan_path.exists()
        assert journal.artifacts.path_for(referenced.artifact).exists()


def test_terminal_run_purge_obeys_retention_and_removes_exact_artifacts(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 8, 7, tzinfo=timezone.utc)]
    limits = _limits(retention_seconds=1)
    with SQLiteJournal(
        tmp_path / "journal.sqlite3",
        limits=limits,
        clock=lambda: current[0],
    ) as journal:
        run = journal.open_run(_metadata())
        batch = _enqueue(journal, run.run_id)
        lease = journal.claim_batch(run_id_value=run.run_id)
        assert lease is not None
        journal.ack_batch(lease.job_id, lease.fencing_token or "")
        assert journal.close_run_production(run.run_id).status is RunStatus.DRAINED
        with pytest.raises(JournalError, match="retention"):
            journal.purge_run(run.run_id, ownership_confirmed=True)
        current[0] += timedelta(seconds=2)
        assert journal.purge_run(run.run_id, ownership_confirmed=True) == 1
        assert journal.get_run(run.run_id) is None
        assert not journal.artifacts.path_for(batch.artifact).exists()


def test_terminal_run_purge_retries_after_artifact_directory_fsync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = datetime(2026, 8, 7, tzinfo=timezone.utc)
    path = tmp_path / "journal.sqlite3"
    limits = _limits(retention_seconds=1)
    journal = SQLiteJournal(path, limits=limits, clock=lambda: current)
    run = journal.open_run(_metadata())
    batch = _enqueue(journal, run.run_id)
    lease = journal.claim_batch(run_id_value=run.run_id)
    assert lease is not None
    journal.ack_batch(lease.job_id, lease.fencing_token or "")
    assert journal.close_run_production(run.run_id).status is RunStatus.DRAINED

    original_fsync_directory = artifact_module._fsync_directory

    def fail_directory_fsync(_path: Path) -> None:
        raise OSError("simulated directory fsync failure")

    monkeypatch.setattr(artifact_module, "_fsync_directory", fail_directory_fsync)
    with pytest.raises(JournalError, match="cannot remove journal artifact"):
        journal.purge_run(
            run.run_id,
            ownership_confirmed=True,
            now=current + timedelta(seconds=2),
        )
    assert journal.get_run(run.run_id) is not None
    assert journal.status_summary(run.run_id)["next_action"] == "retry_purge"
    assert not journal.artifacts.path_for(batch.artifact).exists()
    journal.close()

    # The durable purge marker suppresses normal payload verification for this
    # terminal run, so a new process can retry after unlink succeeded but its
    # directory fsync reported failure.
    with SQLiteJournal(path, limits=limits, clock=lambda: current) as reopened:
        assert reopened.get_run(run.run_id) is not None
        assert inspect_journal(path)[0]["next_action"] == "retry_purge"
        monkeypatch.setattr(
            artifact_module, "_fsync_directory", original_fsync_directory
        )
        assert (
            reopened.purge_run(
                run.run_id,
                ownership_confirmed=True,
                now=current + timedelta(seconds=2),
            )
            == 1
        )
        assert reopened.get_run(run.run_id) is None


def test_events_contain_identifiers_and_counters_but_not_payloads(
    tmp_path: Path,
) -> None:
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        batch = _enqueue(journal, run.run_id)
        events = journal.list_events(run.run_id)

        assert [event["event_type"] for event in events] == [
            "run_opened",
            "batch_enqueued",
        ]
        assert events[-1]["job_id"] == batch.job_id
        assert events[-1]["counters"]["rows"] == 1
        assert events[-1]["parser"] == "python"
        assert events[-1]["phase"] == "nodes"
        assert events[-1]["operation"] == "node_upsert:v1"
        assert events[-1]["pending"] == 1
        assert "sample" not in repr(events)


def test_payload_free_status_reports_resume_age_bytes_and_next_action(
    tmp_path: Path,
) -> None:
    current = [datetime(2026, 8, 7, tzinfo=timezone.utc)]
    path = tmp_path / "journal.sqlite3"
    with SQLiteJournal(path, limits=_limits(), clock=lambda: current[0]) as journal:
        run = journal.open_run(_metadata())
        journal.open_run(_metadata())
        _enqueue(journal, run.run_id)
        current[0] += timedelta(seconds=7)
        summary = journal.status_summary(run.run_id)

        assert summary["resumed"] is True
        assert summary["oldest_unfinished_age_seconds"] == 7
        assert summary["artifact_bytes"] > 0
        assert summary["journal_bytes"] > 0
        assert summary["next_action"] == "resume_consumer"

    inspected = inspect_journal(path)
    assert len(inspected) == 1
    assert inspected[0]["run_id"] == run.run_id
    assert inspected[0]["resumed"] is True
    assert inspected[0]["pending"] == 1


def test_v3_node_manifest_tracks_typed_identity_conservation_and_audit_seal(
    tmp_path: Path,
) -> None:
    operation = {
        "label": "functions",
        "phase": "nodes",
        "version": 1,
        "reconciliation": "node_identity",
        "node_label": "Function",
        "identity_property": "id",
        "row_identity_property": "id",
        "mutation_kind": "merge",
    }
    rows = [{"id": 7, "name": "same"}, {"id": 7, "name": "same"}]
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        artifact = journal.create_artifact(run.run_id, rows)
        batch = journal.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.NODES,
                "graph-write/v1/nodes/functions",
                0,
                artifact,
                2,
                operation=operation,
            ),
        )

        manifests = journal._connection.execute(
            """
            SELECT identity_type, identity_json, disposition, acked, graph_verified
            FROM node_manifest WHERE job_id = ? ORDER BY row_ordinal
            """,
            (batch.job_id,),
        ).fetchall()
        assert [row["disposition"] for row in manifests] == [
            "staged_unique",
            "declared_duplicate",
        ]
        assert {(row["identity_type"], row["identity_json"]) for row in manifests} == {
            ("integer", "7")
        }
        assert journal.list_open_producers(run.run_id) == ["functions"]

        lease = journal.claim_batch(run_id_value=run.run_id)
        assert lease is not None
        journal.ack_batch(lease.job_id, lease.fencing_token or "")
        summary = journal.conservation_summary(run.run_id)
        assert summary["node"] == {
            "emitted": 2,
            "staged_unique": 1,
            "declared_duplicate": 1,
            "conflict": 0,
            "rejected": 0,
            "acked": 1,
            "graph_verified": 1,
            "conserved": True,
        }
        assert journal.complete_producers(run.run_id) == 1
        audit = journal.seal_endpoint_audit(
            run.run_id,
            summary["node_manifest_digest"],
            receipt_count=1,
        )
        assert audit == "sealed"
        assert journal.endpoint_audit_status(run.run_id) == audit


def test_v3_edge_manifest_persists_both_typed_endpoints(tmp_path: Path) -> None:
    operation = {
        "label": "relations:owns",
        "phase": "relationships",
        "version": 1,
        "reconciliation": "typed_relationship",
    }
    row = {
        "source_label": "Project",
        "source_id": "demo",
        "target_label": "File",
        "target_id": 9,
        "rel_type": "OWNS",
        "project_id_normalized": "demo",
        "properties": {},
    }
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        artifact = journal.create_artifact(run.run_id, [row])
        batch = journal.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.RELATIONSHIPS,
                "graph-write/v1/relationships/relations:owns",
                0,
                artifact,
                1,
                operation=operation,
            ),
        )

        endpoints = journal._connection.execute(
            """
            SELECT role, node_label, identity_property, identity_type, identity_json
            FROM edge_endpoint WHERE edge_manifest_id =
              (SELECT manifest_id FROM edge_manifest WHERE job_id = ?)
            ORDER BY role
            """,
            (batch.job_id,),
        ).fetchall()
        assert [tuple(endpoint) for endpoint in endpoints] == [
            ("source", "Project", "id", "string", '"demo"'),
            ("target", "File", "id", "integer", "9"),
        ]


def test_v3_external_type_observations_are_declared_duplicates(tmp_path: Path) -> None:
    operation = {
        "label": "types",
        "phase": "nodes",
        "version": 1,
        "reconciliation": "node_identity",
        "node_label": "Type",
        "identity_property": "id",
        "row_identity_property": "id",
        "mutation_kind": "merge",
    }
    rows = [
        {
            "id": "AppError",
            "kind": "external",
            "code": "AppError",
            "project_id": "demo",
            "file_path": path,
        }
        for path in ("src/a.pc", "src/b.pc")
    ]
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        artifact = journal.create_artifact(run.run_id, rows)
        batch = journal.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.NODES,
                "graph-write/v1/nodes/types",
                0,
                artifact,
                2,
                operation=operation,
            ),
        )
        dispositions = journal._connection.execute(
            "SELECT disposition FROM node_manifest WHERE job_id = ? "
            "ORDER BY row_ordinal",
            (batch.job_id,),
        ).fetchall()
        assert [row["disposition"] for row in dispositions] == [
            "staged_unique",
            "declared_duplicate",
        ]


def test_v3_typed_edge_ignores_internal_grouping_ordinal_for_dedup(
    tmp_path: Path,
) -> None:
    operation = {
        "label": "relations:Function:POINTER_TO:Type",
        "phase": "relationships",
        "version": 1,
        "reconciliation": "typed_relationship",
    }
    base_row = {
        "source_label": "Function",
        "source_id": "function-1",
        "target_label": "Type",
        "target_id": "char",
        "rel_type": "POINTER_TO",
        "project_id_normalized": "demo",
        "properties": {"kind": "pointer"},
    }
    rows = [
        {**base_row, "_contract_row_position": position}
        for position in (3, 9)
    ]
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        artifact = journal.create_artifact(run.run_id, rows)
        batch = journal.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.RELATIONSHIPS,
                "graph-write/v1/relationships/relations:Function:POINTER_TO:Type",
                0,
                artifact,
                2,
                operation=operation,
            ),
        )
        dispositions = journal._connection.execute(
            "SELECT disposition FROM edge_manifest WHERE job_id = ? "
            "ORDER BY row_ordinal",
            (batch.job_id,),
        ).fetchall()
        assert [row["disposition"] for row in dispositions] == [
            "staged_unique",
            "declared_duplicate",
        ]


def test_v3_conflicting_node_payload_is_durable_and_blocks_run(tmp_path: Path) -> None:
    operation = {
        "label": "functions",
        "phase": "nodes",
        "version": 1,
        "reconciliation": "node_identity",
        "node_label": "Function",
        "identity_property": "id",
        "row_identity_property": "id",
        "mutation_kind": "merge",
    }
    with _journal(tmp_path) as journal:
        run = journal.open_run(_metadata())
        first = journal.create_artifact(run.run_id, [{"id": "f", "name": "first"}])
        journal.enqueue_batch(
            run.run_id,
            BatchSpec(OperationPhase.NODES, "functions", 0, first, 1, operation=operation),
        )
        second = journal.create_artifact(run.run_id, [{"id": "f", "name": "changed"}])
        with pytest.raises(JournalError) as conflict:
            journal.enqueue_batch(
                run.run_id,
                BatchSpec(
                    OperationPhase.NODES,
                    "functions",
                    1,
                    second,
                    1,
                    operation=operation,
                ),
            )
        assert conflict.value.code is TerminalErrorCode.INVALID_CONTRACT
        assert conflict.value.details["node_conflict"] == 1
        assert journal.get_run(run.run_id).status is RunStatus.BLOCKED  # type: ignore[union-attr]
        assert [
            row[0]
            for row in journal._connection.execute(
                "SELECT disposition FROM node_manifest ORDER BY created_at, rowid"
            )
        ] == ["staged_unique", "conflict"]
        assert journal.status_counts(run.run_id)[BatchStatus.BLOCKED] == 1


def test_v2_migration_adds_v3_ledgers_but_refuses_active_batches(
    tmp_path: Path,
) -> None:
    path = tmp_path / "completed.sqlite3"
    with SQLiteJournal(path, limits=_limits()):
        pass
    connection = sqlite3.connect(path)
    for table in (
        "endpoint_audit",
        "edge_endpoint",
        "edge_manifest",
        "node_manifest",
        "producer_completion",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()

    with SQLiteJournal(path, limits=_limits()) as migrated:
        assert migrated._connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert {
            row[0]
            for row in migrated._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }.issuperset(
            {
                "producer_completion",
                "node_manifest",
                "edge_manifest",
                "edge_endpoint",
                "endpoint_audit",
            }
        )

    active_path = tmp_path / "active.sqlite3"
    with SQLiteJournal(active_path, limits=_limits()) as journal:
        run = journal.open_run(_metadata())
        _enqueue(journal, run.run_id)
    connection = sqlite3.connect(active_path)
    for table in (
        "endpoint_audit",
        "edge_endpoint",
        "edge_manifest",
        "node_manifest",
        "producer_completion",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("PRAGMA user_version = 2")
    connection.commit()
    connection.close()
    with pytest.raises(JournalError) as incompatible:
        SQLiteJournal(active_path, limits=_limits())
    assert incompatible.value.code is TerminalErrorCode.INCOMPATIBLE_SCHEMA

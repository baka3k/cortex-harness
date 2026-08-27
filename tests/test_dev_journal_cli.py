from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex_harness.dev import cli
from tools.common.sync_scope import scan_scope_id
from tools.graph.journal import (
    BatchSpec,
    JournalError,
    JournalLimits,
    OperationPhase,
    RunMetadata,
    SQLiteJournal,
    TerminalErrorCode,
)
from tools.graph.journal.artifacts import ArtifactStore


def _limits() -> JournalLimits:
    return JournalLimits(
        max_batches_per_run=20,
        max_payload_bytes_per_run=1024 * 1024,
        max_artifact_bytes=256 * 1024,
        max_journal_bytes=8 * 1024 * 1024,
        min_free_bytes=1,
        retention_seconds=1,
        busy_timeout_ms=250,
        wal_autocheckpoint_pages=32,
    )


def _drained_journal(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    scope = scan_scope_id("demo", str(root))
    path = tmp_path / "cache" / "graph-write-journal" / scope / "python.sqlite3"
    metadata = RunMetadata(
        project_id="demo",
        scope_id=scope,
        source_revision="rev",
        source_snapshot="snapshot",
        physical_target="target",
        generation="generation",
        parser="python",
        parser_version="1",
        schema_fingerprint="schema",
        query_shape_version="query",
    )
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    with SQLiteJournal(path, limits=_limits(), clock=lambda: old) as database:
        run = database.open_run(metadata)
        artifact = database.create_artifact(run.run_id, [{"id": "sample"}])
        database.enqueue_batch(
            run.run_id,
            BatchSpec(
                OperationPhase.NODES,
                "node_upsert:v1",
                0,
                artifact,
                1,
            ),
        )
        lease = database.claim_batch(run_id_value=run.run_id)
        assert lease is not None
        database.ack_batch(lease.job_id, lease.fencing_token or "")
        database.close_run_production(run.run_id)
    return root, path, run.run_id


def test_journal_status_is_payload_free_and_machine_readable(tmp_path: Path) -> None:
    _root, path, run_id = _drained_journal(tmp_path)

    result = CliRunner().invoke(
        cli, ["journal", "status", "--journal-path", str(path), "--json-output"]
    )

    assert result.exit_code == 0, result.output
    assert run_id in result.output
    assert '"status": "drained"' in result.output
    assert '"next_action": "none"' in result.output


def test_journal_purge_requires_exact_scope_and_removes_one_terminal_run(
    tmp_path: Path,
) -> None:
    root, path, run_id = _drained_journal(tmp_path)
    runner = CliRunner()

    wrong = runner.invoke(
        cli,
        [
            "journal", "purge", "--journal-path", str(path), "--run-id", run_id,
            "--project-id", "wrong", "--root", str(root),
        ],
    )
    assert wrong.exit_code != 0
    assert "exact project/root scope" in wrong.output

    result = runner.invoke(
        cli,
        [
            "journal", "purge", "--journal-path", str(path), "--run-id", run_id,
            "--project-id", "demo", "--root", str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"event_type": "journal_purged"' in result.output
    with SQLiteJournal(path, limits=_limits()) as database:
        assert database.get_run(run_id) is None


def test_journal_purge_failure_retains_run_for_cli_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, path, run_id = _drained_journal(tmp_path)
    runner = CliRunner()
    original_remove = ArtifactStore.remove

    def fail_remove(_store, _ref) -> None:
        raise JournalError(
            TerminalErrorCode.PERMISSION_DENIED,
            "simulated artifact unlink failure",
        )

    monkeypatch.setattr(ArtifactStore, "remove", fail_remove)
    arguments = [
        "journal", "purge", "--journal-path", str(path), "--run-id", run_id,
        "--project-id", "demo", "--root", str(root),
    ]
    failed = runner.invoke(cli, arguments)
    assert failed.exit_code != 0
    assert "simulated artifact unlink failure" in failed.output
    with SQLiteJournal(path, limits=_limits()) as database:
        assert database.get_run(run_id) is not None
        assert database.status_summary(run_id)["next_action"] == "retry_purge"

    monkeypatch.setattr(ArtifactStore, "remove", original_remove)
    retried = runner.invoke(cli, arguments)
    assert retried.exit_code == 0, retried.output
    assert '"event_type": "journal_purged"' in retried.output
    with SQLiteJournal(path, limits=_limits()) as database:
        assert database.get_run(run_id) is None

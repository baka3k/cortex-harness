from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from cortex_harness.dev import cli
from tools.common.sync_scope import scan_scope_id
from tools.graph.journal import JournalLimits, RunMetadata, SQLiteJournal


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

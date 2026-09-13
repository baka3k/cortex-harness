#!/usr/bin/env python3
"""Ghi kịch bản journal (producer/consumer loop, low-level path) bằng
implementation Python THẬT (`SQLiteJournal`, clock cố định) để Rust replay.

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/gen_journal_scenario.py

Output: rust/crates/cortex-graph-core/tests/fixtures/journal_scenario.json
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys_path = REPO / "code-tiny"
import sys

sys.path.insert(0, str(sys_path))

from tools.graph.journal import sqlite_store  # noqa: E402
from tools.graph.journal.artifacts import ArtifactStore  # noqa: E402
from tools.graph.journal.identity import run_fingerprint, run_id  # noqa: E402
from tools.graph.journal.models import (  # noqa: E402
    BatchSpec,
    OperationPhase,
    RunMetadata,
)

FIXED = dt.datetime(2026, 9, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
FIXED_EPOCH = FIXED.timestamp()

FIXTURES = REPO / "rust" / "crates" / "cortex-graph-core" / "tests" / "fixtures"


def enumify(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value"):
        return value.value
    return str(value)


def metadata_dict(meta: RunMetadata) -> dict:
    return meta.to_dict()


def run_to_dict(record) -> dict:
    return {
        "run_id": record.run_id,
        "fingerprint": record.fingerprint,
        "metadata": metadata_dict(record.metadata),
        "status": record.status.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "retention_until": record.retention_until,
        "error_code": record.error_code.value if record.error_code else None,
    }


def batch_to_dict(record) -> dict:
    return {
        "job_id": record.job_id,
        "run_id": record.run_id,
        "phase": record.phase.value,
        "operation_key": record.operation_key,
        "sequence": record.sequence,
        "artifact": artifact_to_dict(record.artifact),
        "expected_count": record.expected_count,
        "status": record.status.value,
        "attempt": record.attempt,
        "max_attempts": record.max_attempts,
        "fencing_token": record.fencing_token,
        "lease_until": record.lease_until,
        "next_attempt_at": record.next_attempt_at,
        "required_barriers": list(record.required_barriers),
        "produced_barriers": list(record.produced_barriers),
        "retry_class": record.retry_class.value if record.retry_class else None,
        "error_code": record.error_code.value if record.error_code else None,
        "operation": dict(record.operation),
    }


def barrier_to_dict(record) -> dict:
    data = dataclasses.asdict(record)
    data["status"] = record.status.value
    return data


def artifact_to_dict(ref) -> dict:
    return dataclasses.asdict(ref)


def record_result(ops, name, callable_fn, *args, **kwargs):
    try:
        result = callable_fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        code = code.value if code is not None else type(exc).__name__
        ops.append({"op": name, "error": {"code": code}})
        return None
    serializer = {
        "open_run": run_to_dict,
        "create_artifact": artifact_to_dict,
        "enqueue_batch": batch_to_dict,
        "open_barrier": barrier_to_dict,
        "close_barrier": barrier_to_dict,
        "get_barrier": barrier_to_dict,
        "renew_lease": batch_to_dict,
        "ack_batch": batch_to_dict,
        "claim_batch": lambda row: batch_to_dict(row) if row else None,
        "schedule_retry": batch_to_dict,
        "mark_reconciling": batch_to_dict,
        "block_batch": batch_to_dict,
        "list_runs": lambda rows: [run_to_dict(row) for row in rows],
        "find_resumable_run": lambda row: run_to_dict(row) if row else None,
        "complete_producers": lambda count: count,
        "inspect": lambda rows: rows,
    }[name]
    ops.append({"op": name, "result": serializer(result)})
    return result


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = (tmp_path / "scenario.sqlite").resolve()
        artifact_root = (tmp_path / "artifacts").resolve()

        ops: list[dict] = []

        journal = SQLiteJournalWithArtifacts(db_path, artifact_root)

        meta_a = RunMetadata(
            project_id="demo", scope_id="demo", source_revision="rev-1",
            source_snapshot="snap-1", physical_target="local", generation="gen-1",
            parser="python", parser_version="1.0", schema_fingerprint="sfp-1",
            query_shape_version="qv-1",
        )
        meta_b = RunMetadata(
            project_id="demo", scope_id="demo", source_revision="rev-1",
            source_snapshot="snap-1", physical_target="local", generation="gen-1",
            parser="java", parser_version="1.0", schema_fingerprint="sfp-1",
            query_shape_version="qv-1",
        )

        run_a = record_result(ops, "open_run", journal.open_run, meta_a)
        record_result(ops, "open_run", journal.open_run, meta_a)  # resume path
        record_result(ops, "open_run", journal.open_run, meta_b)
        record_result(ops, "find_resumable_run", journal.find_resumable_run, meta_a)
        record_result(ops, "list_runs", journal.list_runs)

        rows = [
            {"id": "row-1", "value": "payment", "score": 1},
            {"id": "row-2", "value": "auth", "score": 2},
        ]
        artifact = record_result(ops, "create_artifact", journal.create_artifact, run_a.run_id, rows)

        record_result(ops, "open_barrier", journal.open_barrier, run_a.run_id, "barrier-tables")
        spec1 = BatchSpec(
            phase=OperationPhase.NODES, operation_key="node.upsert", sequence=0,
            artifact=artifact, expected_count=len(rows),
            produced_barriers=["barrier-tables"], operation={},
        )
        batch1 = record_result(ops, "enqueue_batch", journal.enqueue_batch, run_a.run_id, spec1)
        record_result(ops, "enqueue_batch", journal.enqueue_batch, run_a.run_id, spec1)  # idempotent
        spec2 = BatchSpec(
            phase=OperationPhase.CALLS, operation_key="call.upsert", sequence=1,
            artifact=artifact, expected_count=len(rows),
            required_barriers=["barrier-tables"], operation={},
        )
        batch2 = record_result(ops, "enqueue_batch", journal.enqueue_batch, run_a.run_id, spec2)
        record_result(ops, "get_barrier", journal.get_barrier, run_a.run_id, "barrier-tables")
        record_result(ops, "close_barrier", journal.close_barrier, run_a.run_id, "barrier-tables")

        claimed = record_result(ops, "claim_batch", journal.claim_batch, run_a.run_id, 60)
        assert claimed is not None and claimed.job_id == batch1.job_id
        record_result(ops, "renew_lease", journal.renew_lease,
                      batch1.job_id, claimed.fencing_token, 60)
        record_result(ops, "ack_batch", journal.ack_batch,
                      batch1.job_id, claimed.fencing_token, 120)

        # spec2 chưa claim được (barrier chưa drained lúc trước); claim lại sau ack
        claimed2 = record_result(ops, "claim_batch", journal.claim_batch, run_a.run_id, 60)
        assert claimed2 is not None and claimed2.job_id == batch2.job_id
        record_result(ops, "ack_batch", journal.ack_batch,
                      batch2.job_id, claimed2.fencing_token, None)

        record_result(ops, "claim_batch", journal.claim_batch, run_a.run_id, 60)  # None

        record_result(ops, "complete_producers", journal.complete_producers, run_a.run_id)
        # enqueue sau complete → invalid_transition error
        record_result(ops, "enqueue_batch", journal.enqueue_batch, run_a.run_id, spec1)

        # run khác parser không đụng run_a; exhausted retry qua schedule_retry
        fake_token = "0" * 32
        record_result(ops, "renew_lease", journal.renew_lease, batch1.job_id, fake_token, 60)

        summary = journal.inspect()
        ops.append({"op": "inspect", "result": summary})

        payload = {
            "generator": "scripts/rust_parity/gen_journal_scenario.py",
            "now_epoch": FIXED_EPOCH,
            "metadata_a": metadata_dict(meta_a),
            "metadata_b": metadata_dict(meta_b),
            "artifact_rows": rows,
            "ops": ops,
        }
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path = FIXTURES / "journal_scenario.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)} ({len(ops)} ops)")


class SQLiteJournalWithArtifacts:
    """SQLiteJournal + ArtifactStore thật, clock cố định cho determinism."""

    def __init__(self, db_path: Path, artifact_root: Path) -> None:
        self.journal = sqlite_store.SQLiteJournal(db_path, artifact_root=artifact_root, clock=lambda: FIXED)
        self.artifacts_store = ArtifactStore(artifact_root, max_artifact_bytes=256 * 1024 * 1024,
                                             min_free_bytes=0)

    def open_run(self, meta: RunMetadata) -> object:
        return self.journal.open_run(meta)

    def find_resumable_run(self, meta: RunMetadata):
        return self.journal.find_resumable_run(meta)

    def list_runs(self):
        return self.journal.list_runs()

    def create_artifact(self, run_id_value: str, rows):
        return self.journal.create_artifact(run_id_value, rows)

    def open_barrier(self, run_id_value: str, name: str):
        return self.journal.open_barrier(run_id_value, name)

    def close_barrier(self, run_id_value: str, name: str):
        return self.journal.close_barrier(run_id_value, name)

    def get_barrier(self, run_id_value: str, name: str):
        return self.journal.get_barrier(run_id_value, name)

    def enqueue_batch(self, run_id_value: str, spec: BatchSpec):
        return self.journal.enqueue_batch(run_id_value, spec)

    def claim_batch(self, run_id_value: str, lease_seconds: int):
        return self.journal.claim_batch(run_id_value=run_id_value, lease_seconds=lease_seconds)

    def renew_lease(self, job_id: str, token: str, lease_seconds: int):
        return self.journal.renew_lease(job_id, token, lease_seconds=lease_seconds)

    def ack_batch(self, job_id: str, token: str, elapsed_ms):
        return self.journal.ack_batch(job_id, token, elapsed_ms=elapsed_ms)

    def complete_producers(self, run_id_value: str):
        return self.journal.complete_producers(run_id_value)

    def inspect(self):
        return sqlite_store.inspect_journal(self.journal.path)


if __name__ == "__main__":
    main()

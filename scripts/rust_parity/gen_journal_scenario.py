#!/usr/bin/env python3
"""Ghi kịch bản journal (producer/consumer loop, low-level path) bằng
implementation Python THẬT (`SQLiteJournal`, clock cố định) để Rust replay.

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/gen_journal_scenario.py
    # kèm emit store Python + JSONL capture-format vào 1 thư mục:
    .venv/bin/python scripts/rust_parity/gen_journal_scenario.py --emit-dir /tmp/journal-regression

Output:
- rust/crates/cortex-graph-core/tests/fixtures/journal_scenario.json
  (ops kèm `args` — Rust test serde bỏ qua field lạ nên vẫn tương thích)
- rust/crates/cortex-graph-core/tests/fixtures/journal_scenario.jsonl
  (capture format Track B: header + 1 dòng/op + key prefix `_`)
- với --emit-dir: <dir>/python_store.sqlite3 (store Python FIXED-clock)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import shutil
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
    JOURNAL_SCHEMA_VERSION,
    JournalLimits,
    BatchSpec,
    OperationPhase,
    RunMetadata,
)
from tools.graph.journal.shadow import spec_to_dict  # noqa: E402

FIXED = dt.datetime(2026, 9, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
FIXED_EPOCH = FIXED.timestamp()

FIXTURES = REPO / "rust" / "crates" / "cortex-graph-core" / "tests" / "fixtures"

# Thứ tự positional args của từng op — khớp `SQLiteJournal` (cho JSONL).
_ARG_NAMES = {
    "open_run": ("metadata",),
    "find_resumable_run": ("metadata",),
    "list_runs": (),
    "create_artifact": ("run_id", "rows"),
    "open_barrier": ("run_id", "name"),
    "close_barrier": ("run_id", "name"),
    "get_barrier": ("run_id", "name"),
    "enqueue_batch": ("run_id", "spec"),
    "claim_batch": ("run_id", "lease_seconds"),
    "renew_lease": ("job_id", "fencing_token", "lease_seconds"),
    "ack_batch": ("job_id", "fencing_token", "elapsed_ms"),
    "complete_producers": ("run_id",),
    "inspect": (),
    # Phase 02 — manifest staging + reconciling + conservation
    "mark_reconciling": ("job_id", "fencing_token"),
    "claim_reconciling": ("run_id", "lease_seconds"),
    "schedule_reconciliation_retry": ("job_id", "fencing_token", "retry_at", "error_code"),
    "recover_run_leases_as_ambiguous": ("run_id",),
    "conservation_summary": ("run_id",),
    "seal_endpoint_audit": ("run_id", "manifest_digest", "receipt_count", "audited_rows"),
    "endpoint_audit_status": ("run_id",),
}


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


def _serialize_arg(value):
    if isinstance(value, RunMetadata):
        return metadata_dict(value)
    if isinstance(value, BatchSpec):
        return spec_to_dict(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_serialize_arg(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize_arg(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return value.value
    return repr(value)


def args_dict(name, args, kwargs):
    names = _ARG_NAMES.get(name)
    if names is None:
        return {"_positional": [repr(argument) for argument in args]}
    serialized = {}
    for index, arg_name in enumerate(names):
        if index < len(args):
            serialized[arg_name] = _serialize_arg(args[index])
        elif arg_name in kwargs:
            serialized[arg_name] = _serialize_arg(kwargs[arg_name])
        else:
            defaults = {
                "lease_seconds": 60,
                "elapsed_ms": None,
            }
            if arg_name in defaults:
                serialized[arg_name] = defaults[arg_name]
    return serialized


def record_result(ops, name, callable_fn, *args, **kwargs):
    entry = {"op": name, "args": args_dict(name, args, kwargs)}
    try:
        result = callable_fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        code = code.value if code is not None else type(exc).__name__
        entry["error"] = {"code": code}
        ops.append(entry)
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
        # Phase 02
        "claim_reconciling": lambda row: batch_to_dict(row) if row else None,
        "schedule_reconciliation_retry": batch_to_dict,
        "recover_run_leases_as_ambiguous": lambda count: count,
        "conservation_summary": lambda summary: summary,
        "seal_endpoint_audit": lambda status: status,
        "endpoint_audit_status": lambda status: status,
    }[name]
    entry["result"] = serializer(result)
    ops.append(entry)
    return result


def to_capture_jsonl(journal_config: dict, ops: list[dict], path: Path) -> None:
    """Đổi ops → capture format Track B: header + 1 dòng/op, key prefix `_`."""

    lines = [
        json.dumps(
            {
                "_header": True,
                "schema_version": JOURNAL_SCHEMA_VERSION,
                "journal_config": journal_config,
                "now_epoch": FIXED_EPOCH,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    ]
    for seq, op in enumerate(ops):
        entry = dict(op)
        entry["_seq"] = seq
        entry["_captured_at"] = FIXED.isoformat(timespec="microseconds")
        entry["_captured_at_epoch"] = FIXED_EPOCH
        lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--emit-dir",
        default=None,
        help="copy store Python (FIXED clock) ra thư mục này cho diff regression",
    )
    arguments = parser.parse_args()
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
        run_b = record_result(ops, "open_run", journal.open_run, meta_b)
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

        # ── Phase 02: manifest staging + reconciling + conservation ──
        meta_c = RunMetadata(
            project_id="demo", scope_id="demo", source_revision="rev-2",
            source_snapshot="snap-2", physical_target="local", generation="gen-1",
            parser="cplus", parser_version="1.0", schema_fingerprint="sfp-1",
            query_shape_version="qv-1",
        )
        run_c = record_result(ops, "open_run", journal.open_run, meta_c)

        # Run B (java, đã open): batch dirty — dup + conflict + rejected trong
        # 1 batch node_identity → batch BLOCKED + run BLOCKED sau commit.
        rows_b = [
            {"id": "fn-1", "comment": "a"},
            {"id": "fn-1", "comment": "a"},
            {"id": "fn-1", "comment": "b"},
            {"comment": "missing id"},
        ]
        artifact_b = record_result(
            ops, "create_artifact", journal.create_artifact, run_b.run_id, rows_b
        )
        spec_dirty = BatchSpec(
            phase=OperationPhase.NODES, operation_key="node.upsert", sequence=0,
            artifact=artifact_b, expected_count=len(rows_b),
            operation={
                "reconciliation": "node_identity", "node_label": "Function",
                "identity_property": "id", "mutation_kind": "merge",
                "producer_id": "producer-dirty",
            },
        )
        record_result(ops, "enqueue_batch", journal.enqueue_batch, run_b.run_id, spec_dirty)
        record_result(ops, "complete_producers", journal.complete_producers, run_b.run_id)
        record_result(
            ops, "recover_run_leases_as_ambiguous",
            journal.recover_run_leases_as_ambiguous, run_b.run_id,
        )

        # Run C (cplus): sạch — node dups + call_edge dups, đủ vòng reconcile
        # rồi seal endpoint audit.
        nodes_a = [
            {"id": "fn-1", "comment": "clean-a"},
            {"id": "fn-2", "comment": "clean-b"},
            {"id": "fn-1", "comment": "clean-a"},
        ]
        artifact_c1 = record_result(
            ops, "create_artifact", journal.create_artifact, run_c.run_id, nodes_a
        )
        spec_node = BatchSpec(
            phase=OperationPhase.NODES, operation_key="node.upsert", sequence=0,
            artifact=artifact_c1, expected_count=len(nodes_a),
            operation={
                "reconciliation": "node_identity", "node_label": "Function",
                "identity_property": "id", "mutation_kind": "merge",
                "producer_id": "producer-clean",
            },
        )
        batch_c1 = record_result(
            ops, "enqueue_batch", journal.enqueue_batch, run_c.run_id, spec_node
        )

        edges_a = [
            {"caller_id": "fn-1", "callee_id": "fn-2"},
            {"caller_id": "fn-1", "callee_id": "fn-2"},
            {"caller_id": "fn-2", "callee_id": "fn-1"},
        ]
        artifact_c2 = record_result(
            ops, "create_artifact", journal.create_artifact, run_c.run_id, edges_a
        )
        spec_edge = BatchSpec(
            phase=OperationPhase.CALLS, operation_key="call.upsert", sequence=1,
            artifact=artifact_c2, expected_count=len(edges_a),
            operation={
                "reconciliation": "call_edge", "producer_id": "producer-clean",
            },
        )
        batch_c2 = record_result(
            ops, "enqueue_batch", journal.enqueue_batch, run_c.run_id, spec_edge
        )

        record_result(
            ops, "conservation_summary", journal.conservation_summary, run_c.run_id
        )

        claimed_c = record_result(ops, "claim_batch", journal.claim_batch, run_c.run_id, 60)
        assert claimed_c is not None and claimed_c.job_id == batch_c1.job_id
        record_result(
            ops, "mark_reconciling", journal.mark_reconciling,
            batch_c1.job_id, claimed_c.fencing_token,
        )
        record_result(
            ops, "schedule_reconciliation_retry", journal.schedule_reconciliation_retry,
            batch_c1.job_id, claimed_c.fencing_token,
            FIXED,  # clock cố định → retry claimable ngay ở tick kế tiếp
            sqlite_store.TerminalErrorCode.INVALID_CONTRACT,
        )
        fenced = record_result(
            ops, "claim_reconciling", journal.claim_reconciling, run_c.run_id, 60
        )
        assert fenced is not None and fenced.job_id == batch_c1.job_id
        record_result(
            ops, "ack_batch", journal.ack_batch,
            batch_c1.job_id, fenced.fencing_token, 100,
        )
        claimed_c2 = record_result(ops, "claim_batch", journal.claim_batch, run_c.run_id, 60)
        assert claimed_c2 is not None and claimed_c2.job_id == batch_c2.job_id
        record_result(
            ops, "ack_batch", journal.ack_batch,
            batch_c2.job_id, claimed_c2.fencing_token, None,
        )

        record_result(ops, "complete_producers", journal.complete_producers, run_c.run_id)
        record_result(
            ops, "conservation_summary", journal.conservation_summary, run_c.run_id
        )
        record_result(
            ops, "seal_endpoint_audit", journal.seal_endpoint_audit,
            run_c.run_id, None, None, None,
        )
        # seal lần 2 với digest khác → immutable error
        record_result(
            ops, "seal_endpoint_audit", journal.seal_endpoint_audit,
            run_c.run_id, "deadbeef", None, None,
        )
        record_result(
            ops, "endpoint_audit_status", journal.endpoint_audit_status, run_c.run_id
        )
        record_result(
            ops, "recover_run_leases_as_ambiguous",
            journal.recover_run_leases_as_ambiguous, run_c.run_id,
        )

        summary = journal.inspect()
        ops.append({"op": "inspect", "result": summary})

        payload = {
            "generator": "scripts/rust_parity/gen_journal_scenario.py",
            "now_epoch": FIXED_EPOCH,
            "metadata_a": metadata_dict(meta_a),
            "metadata_b": metadata_dict(meta_b),
            "metadata_c": metadata_dict(meta_c),
            "artifact_rows": rows,
            "artifact_rows_b": rows_b,
            "artifact_rows_c1": nodes_a,
            "artifact_rows_c2": edges_a,
            "ops": ops,
        }
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path = FIXTURES / "journal_scenario.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)} ({len(ops)} ops)")

        journal_config = {
            "mode": "required",
            "path": "scenario.sqlite3",
            "metadata": metadata_dict(meta_a),
            "limits": dataclasses.asdict(JournalLimits()),
        }
        jsonl_path = FIXTURES / "journal_scenario.jsonl"
        to_capture_jsonl(journal_config, ops, jsonl_path)
        print(f"wrote {jsonl_path.relative_to(REPO)} ({len(ops)} ops)")

        # Đóng store (WAL checkpointed khi close) rồi copy cho diff regression.
        journal.close()
        if arguments.emit_dir:
            emit_dir = Path(arguments.emit_dir).resolve()
            emit_dir.mkdir(parents=True, exist_ok=True)
            store_copy = emit_dir / "python_store.sqlite3"
            shutil.copyfile(db_path, store_copy)
            print(f"emitted {store_copy} (Python store, FIXED clock)")


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

    def mark_reconciling(self, job_id: str, token: str):
        return self.journal.mark_reconciling(job_id, token)

    def claim_reconciling(self, run_id_value: str, lease_seconds: int):
        return self.journal.claim_reconciling(run_id_value=run_id_value, lease_seconds=lease_seconds)

    def schedule_reconciliation_retry(self, job_id: str, token: str, retry_at, error_code):
        return self.journal.schedule_reconciliation_retry(
            job_id, token, retry_at=retry_at, error_code=error_code
        )

    def recover_run_leases_as_ambiguous(self, run_id_value: str):
        return self.journal.recover_run_leases_as_ambiguous(run_id_value)

    def conservation_summary(self, run_id_value: str):
        return self.journal.conservation_summary(run_id_value)

    def seal_endpoint_audit(self, run_id_value: str, manifest_digest, receipt_count, audited_rows):
        return self.journal.seal_endpoint_audit(
            run_id_value, manifest_digest, receipt_count, audited_rows=audited_rows
        )

    def endpoint_audit_status(self, run_id_value: str):
        return self.journal.endpoint_audit_status(run_id_value)

    def inspect(self):
        return sqlite_store.inspect_journal(self.journal.path)

    def close(self):
        self.journal.close()


if __name__ == "__main__":
    main()

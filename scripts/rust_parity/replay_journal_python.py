#!/usr/bin/env python3
"""Replay JSONL shadow capture (Track B) qua `SQLiteJournal` Python.

Mirror của bin Rust `replay_journal` (cortex-graph-driver) — cùng op
vocabulary, cùng ràng buộc:
- Header line (`_header` + `schema_version`) được verify; key prefix `_` bỏ qua.
- `fencing_token` của op phụ thuộc dùng token do store đang replay sinh ra
  (track theo job_id); token trong capture chỉ là fallback.
- Payload `operation` của `BatchSpec` bị strip (manifest staging chưa ported
  sang Rust) — để DB-state so được với Rust store (xem phase-B2/B3).
- Op lỗi trong capture: verify store raise ĐÚNG code đã ghi.

Dùng cho:
- Regression B2: replay fixture (clock cố định qua `_captured_at_epoch`)
  → store Python để diff với Rust store.
- Benchmark B3: đo ops/s, MB-ghi/s trên cùng op-stream với Rust.

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/replay_journal_python.py \
        --input capture.jsonl --output store.sqlite3 [--bench]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.graph.journal import sqlite_store  # noqa: E402
from tools.graph.journal.models import (  # noqa: E402
    ArtifactRef,
    BatchSpec,
    JournalError,
    JournalLimits,
    JOURNAL_SCHEMA_VERSION,
    OperationPhase,
    RetryClass,
    RunMetadata,
    TerminalErrorCode,
)
from tools.graph.journal.sqlite_store import SQLiteJournal  # noqa: E402

# Ops chỉ-đọc / no-op trên store replay mới (khớp nhóm skip của bin Rust).
READONLY_OPS = frozenset(
    {
        "get_run", "list_runs", "find_resumable_run", "get_batch", "get_barrier",
        "list_open_producers", "list_batches", "status_counts", "status_summary",
        "list_events", "inspect", "conservation_summary", "endpoint_audit_status",
        "recover_expired_leases", "recover_run_leases_as_ambiguous", "close",
    }
)
# Ops ghi có thật nhưng chưa hỗ trợ replay (khớp bin Rust).
UNSUPPORTED_OPS = frozenset({"claim_reconciling_job", "schedule_reconciliation_retry"})


def _enum(value: Any, enum_type: type) -> Any:
    return enum_type(value) if value is not None else None


class Replay:
    """Replay từng op JSONL vào `SQLiteJournal`, track token/run như bin Rust."""

    def __init__(self, journal: SQLiteJournal, *, skip_unsupported: bool = True) -> None:
        self.journal = journal
        self.skip_unsupported = skip_unsupported
        self.tokens: dict[str, str] = {}
        self.run_by_job: dict[str, str] = {}
        self.last_run: str | None = None
        self.skipped_readonly = 0
        self.skipped_unsupported = 0

    # -- helpers ----------------------------------------------------------
    def _token(self, job_id: str, fallback: Any) -> str:
        return self.tokens.get(job_id) or (fallback or "")

    def _track(self, record: Any) -> None:
        if record.fencing_token:
            self.tokens[record.job_id] = record.fencing_token
        else:
            self.tokens.pop(record.job_id, None)
        self.run_by_job[record.job_id] = record.run_id
        self.last_run = record.run_id

    # -- dispatch ---------------------------------------------------------
    def apply(self, op: str, args: dict[str, Any]) -> None:
        if op == "open_run":
            metadata = RunMetadata(**args["metadata"])
            record = self.journal.open_run(
                metadata,
                expected_items=int(args.get("expected_items") or 0),
                expected_bytes=int(args.get("expected_bytes") or 0),
            )
            self.last_run = record.run_id
        elif op == "create_artifact":
            self.journal.create_artifact(args["run_id"], args.get("rows") or [])
        elif op == "enqueue_batch":
            spec_data = dict(args["spec"])
            # Manifest staging chưa ported — strip payload (khớp bin Rust).
            spec_data["operation"] = {}
            spec_data["phase"] = OperationPhase(spec_data["phase"])
            spec_data["artifact"] = ArtifactRef(**spec_data["artifact"])
            spec_data["required_barriers"] = list(spec_data.get("required_barriers") or ())
            spec_data["produced_barriers"] = list(spec_data.get("produced_barriers") or ())
            record = self.journal.enqueue_batch(args["run_id"], BatchSpec(**spec_data))
            self._track(record)
        elif op == "claim_batch":
            record = self.journal.claim_batch(
                run_id_value=args.get("run_id"),
                lease_seconds=int(args.get("lease_seconds") or 60),
            )
            if record is not None:
                self._track(record)
        elif op == "claim_job":
            job_id = args["job_id"]
            record = self.journal.claim_job(
                job_id, lease_seconds=int(args.get("lease_seconds") or 300)
            )
            if record is not None:
                if record.job_id != job_id:
                    print(
                        f"warn: capture claim_job({job_id}) nhưng replay claim được "
                        f"{record.job_id} — thứ tự stream lệch",
                        file=sys.stderr,
                    )
                self._track(record)
        elif op == "ack_batch":
            job_id = args["job_id"]
            record = self.journal.ack_batch(
                job_id,
                self._token(job_id, args.get("fencing_token")),
                elapsed_ms=args.get("elapsed_ms"),
            )
            self._track(record)
        elif op == "renew_lease":
            job_id = args["job_id"]
            record = self.journal.renew_lease(
                job_id,
                self._token(job_id, args.get("fencing_token")),
                lease_seconds=int(args.get("lease_seconds") or 60),
            )
            self._track(record)
        elif op == "mark_reconciling":
            job_id = args["job_id"]
            record = self.journal.mark_reconciling(
                job_id,
                self._token(job_id, args.get("fencing_token")),
                error_code=_enum(args.get("error_code"), TerminalErrorCode),
            )
            self._track(record)
        elif op == "schedule_retry":
            job_id = args["job_id"]
            record = self.journal.schedule_retry(
                job_id,
                self._token(job_id, args.get("fencing_token")),
                retry_at=dt.datetime.fromtimestamp(args["retry_at_epoch"], tz=dt.timezone.utc),
                retry_class=RetryClass(args["retry_class"]),
                error_code=TerminalErrorCode(args["error_code"]),
            )
            self._track(record)
        elif op == "block_batch":
            job_id = args["job_id"]
            record = self.journal.block_batch(
                job_id,
                self._token(job_id, args.get("fencing_token")),
                retry_class=RetryClass(args["retry_class"]),
                error_code=TerminalErrorCode(args["error_code"]),
            )
            self._track(record)
        elif op == "open_barrier":
            self.journal.open_barrier(args["run_id"], args["name"])
        elif op == "close_barrier":
            self.journal.close_barrier(args["run_id"], args["name"])
        elif op == "complete_producers":
            self.journal.complete_producers(args["run_id"])
        elif op in READONLY_OPS:
            self.skipped_readonly += 1
        elif op in UNSUPPORTED_OPS:
            if not self.skip_unsupported:
                raise ValueError(f"op {op} chưa hỗ trợ replay (skip_unsupported=False)")
            self.skipped_unsupported += 1
        else:
            raise ValueError(f"op {op} không thuộc vocabulary replay")


def _read_capture(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header: dict[str, Any] | None = None
    ops: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if value.get("_header"):
            header = value
            continue
        ops.append(value)
    if header is None:
        raise ValueError(f"capture {path} thiếu header line")
    if header.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version {header.get('schema_version')} không khớp crate "
            f"({JOURNAL_SCHEMA_VERSION}) — format drift"
        )
    return header, ops


def replay_capture(
    input_path: str | Path,
    output_path: str | Path,
    *,
    artifact_root: str | Path | None = None,
    skip_unsupported: bool = True,
) -> dict[str, Any]:
    """Replay full capture → SQLite store; trả stats (ops, elapsed, bytes)."""

    input_path = Path(input_path)
    output_path = Path(output_path).resolve()
    header, ops = _read_capture(input_path)

    for stale in (
        output_path,
        Path(f"{output_path}-wal"),
        Path(f"{output_path}-shm"),
    ):
        stale.unlink(missing_ok=True)
    defaulted_root = artifact_root is None
    root = (
        Path(f"{output_path}.artifacts")
        if artifact_root is None
        else Path(artifact_root)
    )
    if defaulted_root:
        import shutil

        shutil.rmtree(root, ignore_errors=True)

    # Clock điều khiển được: `_captured_at_epoch` của op đang replay
    # (fallback now_epoch header) — khớp cơ chế clock của bin Rust.
    current_epoch = {"value": float(header.get("now_epoch") or time.time())}
    limits_data = (header.get("journal_config") or {}).get("limits") or {}
    limits = JournalLimits(**limits_data) if limits_data else JournalLimits()

    def clock() -> dt.datetime:
        return dt.datetime.fromtimestamp(current_epoch["value"], tz=dt.timezone.utc)

    journal = SQLiteJournal(output_path, artifact_root=root, limits=limits, clock=clock)
    replay = Replay(journal, skip_unsupported=skip_unsupported)
    started = time.perf_counter()
    try:
        for op in ops:
            if "_captured_at_epoch" in op:
                current_epoch["value"] = float(op["_captured_at_epoch"])
            name = op["op"]
            args = op.get("args") or {}
            expected_error = (op.get("error") or {}).get("code")
            try:
                replay.apply(name, args)
            except JournalError as exc:
                if expected_error is None:
                    raise ValueError(f"op {name}: replay lỗi nhưng capture thành công: {exc}") from exc
                if exc.code.value != expected_error:
                    raise ValueError(
                        f"op {name}: capture error `{expected_error}` nhưng replay lỗi "
                        f"`{exc.code.value}`"
                    ) from exc
                continue
            if expected_error is not None:
                raise ValueError(
                    f"op {name}: capture ghi error `{expected_error}` nhưng replay thành công"
                )
    finally:
        journal.close()
    elapsed = time.perf_counter() - started

    return {
        "ops": len(ops),
        "elapsed_s": elapsed,
        "input_bytes": input_path.stat().st_size,
        "skipped_readonly": replay.skipped_readonly,
        "skipped_unsupported": replay.skipped_unsupported,
        "output": str(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay JSONL capture qua SQLiteJournal Python")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--bench", action="store_true")
    arguments = parser.parse_args()
    stats = replay_capture(
        arguments.input,
        arguments.output,
        artifact_root=arguments.artifact_root,
    )
    if arguments.bench:
        elapsed = stats["elapsed_s"]
        mib = stats["input_bytes"] / (1024 * 1024)
        print(
            f"bench: ops={stats['ops']} elapsed_s={elapsed:.3f} "
            f"ops_per_s={stats['ops'] / elapsed:.0f} "
            f"input_bytes={stats['input_bytes']} mib_per_s={mib / elapsed:.2f} "
            f"skipped_readonly={stats['skipped_readonly']} "
            f"skipped_unsupported={stats['skipped_unsupported']}"
        )
    else:
        print(
            f"replayed {stats['ops']} ops → {stats['output']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

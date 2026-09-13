#!/usr/bin/env python3
"""Sinh golden fixtures phase 05 (schema manifest + journal).

- manifest_golden.json: fingerprint/driver_indexes của CODE_GRAPH_SCHEMA thật.
- journal_golden.json + journal_python.sqlite: DB tạo bằng `SQLiteJournal` thật
  (schema v3), insert rows mẫu qua sqlite3, expected = `inspect_journal` thật
  (clock monkeypatch về mốc cố định).

Chạy từ repo root:
    uv run --no-project python scripts/rust_parity/gen_phase05_fixtures.py
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.graph.journal import sqlite_store  # noqa: E402
from tools.graph.journal.sqlite_store import SQLiteJournal  # noqa: E402
from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA  # noqa: E402

FIXTURES = REPO / "rust" / "crates" / "cortex-graph-core" / "tests" / "fixtures"
FIXED = dt.datetime(2026, 9, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def iso(hour: int, minute: int = 0) -> str:
    return FIXED.replace(hour=hour, minute=minute).isoformat(timespec="microseconds")


def gen_manifest() -> None:
    payload = {
        "generator": "scripts/rust_parity/gen_phase05_fixtures.py",
        "fingerprint": CODE_GRAPH_SCHEMA.fingerprint,
        "name": CODE_GRAPH_SCHEMA.name,
        "version": CODE_GRAPH_SCHEMA.version,
        "index_count": len(CODE_GRAPH_SCHEMA.indexes),
        "driver_indexes": CODE_GRAPH_SCHEMA.driver_indexes(),
        "has_identity": {
            "File/id": CODE_GRAPH_SCHEMA.has_identity_index("File", "id"),
            "File/path": CODE_GRAPH_SCHEMA.has_identity_index("File", "path"),
            "ApiEndpoint/symbol_id": CODE_GRAPH_SCHEMA.has_identity_index(
                "ApiEndpoint", "symbol_id"
            ),
            "SemanticCoverage/tu_key": CODE_GRAPH_SCHEMA.has_identity_index(
                "SemanticCoverage", "tu_key"
            ),
            "SemanticCoverage/id": CODE_GRAPH_SCHEMA.has_identity_index(
                "SemanticCoverage", "id"
            ),
        },
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / "manifest_golden.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO)} (fingerprint={payload['fingerprint']})")


RUN_COLUMNS = [
    "run_id", "fingerprint", "project_id", "scope_id", "physical_target",
    "parser", "metadata_json", "status", "created_at", "updated_at",
    "retention_until", "error_code", "error_detail",
]
ARTIFACT_COLUMNS = [
    "run_id", "sha256", "relative_path", "byte_count", "row_count",
    "ref_count", "created_at", "last_referenced_at",
]
BATCH_COLUMNS = [
    "job_id", "run_id", "phase", "operation_key", "sequence",
    "artifact_sha256", "artifact_path", "payload_bytes", "row_count",
    "expected_count", "status", "attempt", "max_attempts", "fencing_token",
    "lease_until", "next_attempt_at", "required_barriers_json",
    "produced_barriers_json", "operation_json", "retry_class", "error_code",
    "error_detail", "created_at", "updated_at",
]
EVENT_COLUMNS = [
    "run_id", "job_id", "event_type", "counters_json", "attempt",
    "elapsed_ms", "error_code", "created_at",
]

ROWS = {
    "runs": [
        {
            "run_id": "run-open-0001", "fingerprint": "fp-open",
            "project_id": "demo", "scope_id": "demo", "physical_target": "local",
            "parser": "python",
            "metadata_json": json.dumps({"parser": "python", "project_id": "demo", "scope_id": "demo", "source_revision": "rev-1", "source_snapshot": "snap-1", "physical_target": "local", "generation": "gen-1", "parser_version": "1.0", "schema_fingerprint": "sfp-1", "query_shape_version": "qv-1"}),
            "status": "open", "created_at": iso(10, 0), "updated_at": iso(11, 30),
            "retention_until": iso(23, 59), "error_code": None, "error_detail": None,
        },
        {
            "run_id": "run-drained-01", "fingerprint": "fp-drained",
            "project_id": "demo", "scope_id": "demo", "physical_target": "local",
            "parser": "java",
            "metadata_json": json.dumps({"parser": "java", "project_id": "demo", "scope_id": "demo", "source_revision": "rev-2", "source_snapshot": "snap-2", "physical_target": "local", "generation": "gen-1", "parser_version": "1.0", "schema_fingerprint": "sfp-2", "query_shape_version": "qv-1"}),
            "status": "drained", "created_at": iso(10, 0), "updated_at": iso(11, 0),
            "retention_until": iso(23, 59),
            "error_code": "invalid_contract", "error_detail": "fixture detail",
        },
    ],
    "artifacts": [
        {"run_id": "run-open-0001", "sha256": "a" * 64,
         "relative_path": "aa/aaaa", "byte_count": 4096, "row_count": 20,
         "ref_count": 2, "created_at": iso(10, 5), "last_referenced_at": iso(10, 5)},
        {"run_id": "run-open-0001", "sha256": "b" * 64,
         "relative_path": "bb/bbbb", "byte_count": 1024, "row_count": 10,
         "ref_count": 2, "created_at": iso(10, 6), "last_referenced_at": iso(10, 6)},
        {"run_id": "run-drained-01", "sha256": "c" * 64,
         "relative_path": "cc/cccc", "byte_count": 2048, "row_count": 7,
         "ref_count": 1, "created_at": iso(10, 25), "last_referenced_at": iso(10, 25)},
    ],
    "batches": [
        {"job_id": "job-done-0001", "run_id": "run-open-0001", "phase": "nodes",
         "operation_key": "node.upsert", "sequence": 0, "artifact_sha256": "a" * 64,
         "artifact_path": "aa/aaaa", "payload_bytes": 1000, "row_count": 10,
         "expected_count": 10, "status": "done", "attempt": 1, "max_attempts": 3,
         "fencing_token": "fence-1", "lease_until": None, "next_attempt_at": None,
         "required_barriers_json": "[]", "produced_barriers_json": "[]",
         "operation_json": "{}", "retry_class": None, "error_code": None,
         "error_detail": None, "created_at": iso(10, 5), "updated_at": iso(10, 40)},
        {"job_id": "job-pend-0002", "run_id": "run-open-0001", "phase": "nodes",
         "operation_key": "node.upsert", "sequence": 1, "artifact_sha256": "b" * 64,
         "artifact_path": "bb/bbbb", "payload_bytes": 500, "row_count": 5,
         "expected_count": 5, "status": "pending", "attempt": 0, "max_attempts": 3,
         "fencing_token": None, "lease_until": None, "next_attempt_at": None,
         "required_barriers_json": "[]", "produced_barriers_json": "[]",
         "operation_json": "{}", "retry_class": None, "error_code": None,
         "error_detail": None, "created_at": iso(10, 10), "updated_at": iso(10, 10)},
        {"job_id": "job-lease-0003", "run_id": "run-open-0001", "phase": "calls",
         "operation_key": "call.upsert", "sequence": 0, "artifact_sha256": "b" * 64,
         "artifact_path": "bb/bbbb", "payload_bytes": 300, "row_count": 3,
         "expected_count": 3, "status": "leased", "attempt": 1, "max_attempts": 3,
         "fencing_token": "fence-3", "lease_until": iso(12, 30),
         "next_attempt_at": None,
         "required_barriers_json": "[]", "produced_barriers_json": "[]",
         "operation_json": "{}", "retry_class": None, "error_code": None,
         "error_detail": None, "created_at": iso(10, 15), "updated_at": iso(10, 45)},
        {"job_id": "job-retry-0004", "run_id": "run-open-0001", "phase": "relationships",
         "operation_key": "rel.upsert", "sequence": 0, "artifact_sha256": "b" * 64,
         "artifact_path": "bb/bbbb", "payload_bytes": 200, "row_count": 2,
         "expected_count": 2, "status": "retry_wait", "attempt": 2, "max_attempts": 3,
         "fencing_token": None, "lease_until": None, "next_attempt_at": iso(13, 0),
         "required_barriers_json": "[]", "produced_barriers_json": "[]",
         "operation_json": "{}", "retry_class": "transient", "error_code": "stale_fence",
         "error_detail": "fixture retry", "created_at": iso(10, 20),
         "updated_at": iso(10, 50)},
        {"job_id": "job-done-0005", "run_id": "run-drained-01", "phase": "nodes",
         "operation_key": "node.upsert", "sequence": 0, "artifact_sha256": "c" * 64,
         "artifact_path": "cc/cccc", "payload_bytes": 700, "row_count": 7,
         "expected_count": 7, "status": "done", "attempt": 1, "max_attempts": 3,
         "fencing_token": "fence-5", "lease_until": None, "next_attempt_at": None,
         "required_barriers_json": "[]", "produced_barriers_json": "[]",
         "operation_json": "{}", "retry_class": None, "error_code": None,
         "error_detail": None, "created_at": iso(10, 25), "updated_at": iso(10, 55)},
    ],
    "events": [
        {"run_id": "run-open-0001", "job_id": None, "event_type": "run_opened",
         "counters_json": "{}", "attempt": None, "elapsed_ms": None,
         "error_code": None, "created_at": iso(10, 0)},
        {"run_id": "run-open-0001", "job_id": None, "event_type": "run_resumed",
         "counters_json": "{}", "attempt": None, "elapsed_ms": None,
         "error_code": None, "created_at": iso(11, 30)},
        {"run_id": "run-drained-01", "job_id": None,
         "event_type": "run_purge_started", "counters_json": "{}",
         "attempt": None, "elapsed_ms": None, "error_code": None,
         "created_at": iso(11, 10)},
    ],
}


def insert_rows(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        for table, columns in [
            ("runs", RUN_COLUMNS),
            ("artifacts", ARTIFACT_COLUMNS),
            ("batches", BATCH_COLUMNS),
            ("events", EVENT_COLUMNS),
        ]:
            placeholders = ", ".join("?" for _ in columns)
            column_list = ", ".join(columns)
            conn.executemany(
                f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
                [tuple(row[column] for column in columns) for row in ROWS[table]],
            )
        conn.commit()
    finally:
        conn.close()


def gen_journal() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    # ensure_safe_local_directory từ chối path traverse symlink (macOS /tmp,
    # /Users alias...) — resolve trước.
    db_path = (FIXTURES / "journal_python.sqlite").resolve(strict=True) \
        if (FIXTURES / "journal_python.sqlite").exists() \
        else (FIXTURES / "journal_python.sqlite").resolve()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()

    artifact_root = (REPO / "rust" / "target" / "phase05-artifacts").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    journal = SQLiteJournal(db_path, artifact_root=artifact_root)
    journal._connection.close()
    insert_rows(db_path)

    # Monkeypatch clock cho inspect deterministic.
    original_now = sqlite_store._utc_now
    sqlite_store._utc_now = lambda: FIXED
    try:
        expected = sqlite_store.inspect_journal(db_path)
    finally:
        sqlite_store._utc_now = original_now

    payload = {
        "generator": "scripts/rust_parity/gen_phase05_fixtures.py",
        "schema_version": sqlite_store.JOURNAL_SCHEMA_VERSION,
        "now_epoch": FIXED.timestamp(),
        "ddl_statements": list(sqlite_store._schema_statements()),
        "row_columns": {
            "runs": RUN_COLUMNS,
            "artifacts": ARTIFACT_COLUMNS,
            "batches": BATCH_COLUMNS,
            "events": EVENT_COLUMNS,
        },
        "rows": ROWS,
        "expected": expected,
    }
    path = FIXTURES / "journal_golden.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO)} ({len(expected)} runs)")


if __name__ == "__main__":
    gen_manifest()
    gen_journal()

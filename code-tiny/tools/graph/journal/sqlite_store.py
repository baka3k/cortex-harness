"""Transactional SQLite WAL repository for graph-write journal state."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator

from .artifacts import ArtifactStore, ensure_safe_local_directory
from .identity import canonical_json, deterministic_job_id, run_fingerprint, run_id
from .models import (
    JOURNAL_SCHEMA_VERSION,
    ArtifactRef,
    BarrierRecord,
    BarrierStatus,
    BatchRecord,
    BatchSpec,
    BatchStatus,
    JournalError,
    JournalLimits,
    OperationPhase,
    RetryClass,
    RunMetadata,
    RunRecord,
    RunStatus,
    StaleFenceError,
    TerminalErrorCode,
)


_ACTIVE_RUN_STATUSES = (RunStatus.OPEN.value, RunStatus.DRAINING.value)
_TERMINAL_RUN_STATUSES = {
    RunStatus.BLOCKED,
    RunStatus.DRAINED,
    RunStatus.DEAD_LETTERED,
    RunStatus.QUARANTINED,
    RunStatus.FAILED,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _enum_values(enum_type: type[Any]) -> str:
    return ",".join(f"'{item.value}'" for item in enum_type)


def _schema_statements() -> tuple[str, ...]:
    return (
        f"""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            physical_target TEXT NOT NULL,
            parser TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({_enum_values(RunStatus)})),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            retention_until TEXT NOT NULL,
            error_code TEXT,
            error_detail TEXT
        ) STRICT
        """,
        """
        CREATE TABLE artifacts (
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            sha256 TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            ref_count INTEGER NOT NULL CHECK (ref_count >= 0),
            created_at TEXT NOT NULL,
            last_referenced_at TEXT NOT NULL,
            PRIMARY KEY (run_id, sha256)
        ) STRICT
        """,
        f"""
        CREATE TABLE batches (
            job_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            phase TEXT NOT NULL CHECK (phase IN ({_enum_values(OperationPhase)})),
            operation_key TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            artifact_sha256 TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            expected_count INTEGER NOT NULL CHECK (expected_count >= 0),
            status TEXT NOT NULL CHECK (status IN ({_enum_values(BatchStatus)})),
            attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
            fencing_token TEXT,
            lease_until TEXT,
            next_attempt_at TEXT,
            required_barriers_json TEXT NOT NULL,
            produced_barriers_json TEXT NOT NULL,
            operation_json TEXT NOT NULL,
            retry_class TEXT,
            error_code TEXT,
            error_detail TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (run_id, phase, operation_key, sequence, artifact_sha256),
            FOREIGN KEY (run_id, artifact_sha256)
                REFERENCES artifacts(run_id, sha256) ON DELETE RESTRICT
        ) STRICT
        """,
        f"""
        CREATE TABLE barriers (
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({_enum_values(BarrierStatus)})),
            produced_count INTEGER NOT NULL DEFAULT 0 CHECK (produced_count >= 0),
            drained_count INTEGER NOT NULL DEFAULT 0 CHECK (drained_count >= 0),
            closed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, name),
            CHECK (drained_count <= produced_count)
        ) STRICT
        """,
        """
        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            job_id TEXT,
            event_type TEXT NOT NULL,
            counters_json TEXT NOT NULL,
            attempt INTEGER,
            elapsed_ms INTEGER,
            error_code TEXT,
            created_at TEXT NOT NULL
        ) STRICT
        """,
        "CREATE INDEX batches_claim_idx ON batches(status, next_attempt_at, sequence, created_at)",
        "CREATE INDEX batches_run_status_idx ON batches(run_id, status)",
        "CREATE INDEX events_run_idx ON events(run_id, event_id)",
        "CREATE INDEX runs_scope_idx ON runs(scope_id, physical_target, parser, status)",
    )


class SQLiteJournal:
    """A single-file journal with immutable artifact payloads."""

    def __init__(
        self,
        path: Path,
        *,
        artifact_root: Path | None = None,
        limits: JournalLimits | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        requested = Path(path).expanduser()
        if not requested.is_absolute():
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "journal path must be absolute",
                details={"path": str(requested)},
            )
        safe_parent = ensure_safe_local_directory(requested.parent)
        self.path = safe_parent / requested.name
        self.limits = limits or JournalLimits()
        self._clock = clock
        self._lock = threading.RLock()
        if self.path.is_symlink():
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                "journal database must not be a symlink",
                details={"path": str(self.path)},
            )
        self._precreate_database()
        root = artifact_root or self.path.parent / "artifacts"
        self.artifacts = ArtifactStore(
            root,
            max_artifact_bytes=self.limits.max_artifact_bytes,
            min_free_bytes=self.limits.min_free_bytes,
        )
        try:
            self._connection = sqlite3.connect(
                self.path,
                isolation_level=None,
                check_same_thread=False,
                timeout=self.limits.busy_timeout_ms / 1000,
            )
            self._connection.row_factory = sqlite3.Row
            self._configure()
            self._migrate()
            self._validate_database()
            self.recover_expired_leases()
            self.verify_referenced_artifacts()
        except JournalError:
            self._close_after_failed_open()
            raise
        except sqlite3.DatabaseError as exc:
            self._close_after_failed_open()
            raise self._translate_sqlite_error(
                exc, "cannot open graph-write journal"
            ) from exc
        self._protect_sqlite_files()

    def _precreate_database(self) -> None:
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(descriptor)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            if isinstance(exc, PermissionError):
                raise JournalError(
                    TerminalErrorCode.PERMISSION_DENIED,
                    "journal database is not writable",
                ) from exc
            if getattr(exc, "errno", None) in {28, 122}:
                raise JournalError(
                    TerminalErrorCode.DISK_FULL, "cannot allocate journal database"
                ) from exc
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT, f"cannot create journal: {exc}"
            ) from exc

    def _close_after_failed_open(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _configure(self) -> None:
        connection = self._connection
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.limits.busy_timeout_ms}")
        mode = str(
            connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        ).casefold()
        if mode != "wal":
            raise JournalError(
                TerminalErrorCode.UNSAFE_PLACEMENT,
                f"journal placement refused WAL mode (received {mode})",
            )
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute(
            f"PRAGMA wal_autocheckpoint = {self.limits.wal_autocheckpoint_pages}"
        )
        connection.execute(
            f"PRAGMA journal_size_limit = {self.limits.max_journal_bytes}"
        )
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        current_pages = int(connection.execute("PRAGMA page_count").fetchone()[0])
        configured_pages = max(1, self.limits.max_journal_bytes // page_size)
        if current_pages > configured_pages:
            raise JournalError(
                TerminalErrorCode.ADMISSION_REJECTED,
                "journal database already exceeds the configured size limit",
            )
        connection.execute(f"PRAGMA max_page_count = {configured_pages}")

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > JOURNAL_SCHEMA_VERSION:
            raise JournalError(
                TerminalErrorCode.INCOMPATIBLE_SCHEMA,
                f"journal schema {version} is newer than supported schema {JOURNAL_SCHEMA_VERSION}",
            )
        if version == JOURNAL_SCHEMA_VERSION:
            return
        if version == 0:
            with self._transaction():
                for statement in _schema_statements():
                    self._connection.execute(statement)
                self._connection.execute(
                    f"PRAGMA user_version = {JOURNAL_SCHEMA_VERSION}"
                )
            return
        if version == 1:
            with self._transaction():
                active = self._connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE status != ?",
                    (BatchStatus.DONE.value,),
                ).fetchone()
                if active is not None and int(active[0]) > 0:
                    raise JournalError(
                        TerminalErrorCode.INCOMPATIBLE_SCHEMA,
                        "schema v1 has active batches without replay descriptors",
                    )
                self._connection.execute(
                    "ALTER TABLE batches ADD COLUMN operation_json "
                    "TEXT NOT NULL DEFAULT '{}'"
                )
                self._connection.execute(
                    f"PRAGMA user_version = {JOURNAL_SCHEMA_VERSION}"
                )
            return
        raise JournalError(
            TerminalErrorCode.INCOMPATIBLE_SCHEMA,
            f"journal schema {version} cannot be migrated",
        )

    def _validate_database(self) -> None:
        rows = self._connection.execute("PRAGMA quick_check").fetchall()
        results = [str(row[0]) for row in rows]
        if results != ["ok"]:
            raise JournalError(
                TerminalErrorCode.JOURNAL_CORRUPT,
                "journal integrity check failed",
                details={"integrity": results[:10]},
            )
        foreign_keys = int(
            self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
        )
        synchronous = int(self._connection.execute("PRAGMA synchronous").fetchone()[0])
        if foreign_keys != 1 or synchronous < 2:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "journal durability pragmas are not active",
            )

    def _protect_sqlite_files(self) -> None:
        try:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{self.path}{suffix}")
                if candidate.exists():
                    os.chmod(candidate, 0o600)
        except PermissionError as exc:
            raise JournalError(
                TerminalErrorCode.PERMISSION_DENIED,
                "cannot protect journal database files",
            ) from exc

    @staticmethod
    def _translate_sqlite_error(
        exc: sqlite3.DatabaseError, message: str
    ) -> JournalError:
        detail = str(exc).casefold()
        if "database or disk is full" in detail or "disk full" in detail:
            code = TerminalErrorCode.DISK_FULL
        elif "readonly" in detail or "permission" in detail:
            code = TerminalErrorCode.PERMISSION_DENIED
        elif "malformed" in detail or "not a database" in detail or "corrupt" in detail:
            code = TerminalErrorCode.JOURNAL_CORRUPT
        else:
            code = TerminalErrorCode.INVALID_CONTRACT
        return JournalError(code, f"{message}: {exc}")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield
                self._connection.execute("COMMIT")
                self._protect_sqlite_files()
            except sqlite3.DatabaseError as exc:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise self._translate_sqlite_error(
                    exc, "journal transaction failed"
                ) from exc
            except Exception:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def _now(self) -> datetime:
        value = self._clock()
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _admit(self, expected_items: int, expected_bytes: int) -> None:
        if expected_items < 0 or expected_bytes < 0:
            raise ValueError("expected admission values must be non-negative")
        if expected_items > self.limits.max_batches_per_run:
            raise JournalError(
                TerminalErrorCode.ADMISSION_REJECTED, "run item limit exceeded"
            )
        if expected_bytes > self.limits.max_payload_bytes_per_run:
            raise JournalError(
                TerminalErrorCode.ADMISSION_REJECTED, "run byte limit exceeded"
            )
        free = shutil.disk_usage(self.path.parent).free
        if free - expected_bytes < self.limits.min_free_bytes:
            raise JournalError(
                TerminalErrorCode.DISK_FULL,
                "run admission would violate disk headroom",
            )

    def _add_event(
        self,
        *,
        run_id_value: str,
        event_type: str,
        job_id: str | None = None,
        counters: dict[str, int] | None = None,
        attempt: int | None = None,
        elapsed_ms: int | None = None,
        error_code: TerminalErrorCode | None = None,
        now: str,
    ) -> None:
        event_counters = dict(counters or {})
        event_counters.setdefault(
            "pending",
            int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE run_id = ? AND status != ?",
                    (run_id_value, BatchStatus.DONE.value),
                ).fetchone()[0]
            ),
        )
        self._connection.execute(
            """
            INSERT INTO events(
                run_id, job_id, event_type, counters_json, attempt,
                elapsed_ms, error_code, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id_value,
                job_id,
                event_type,
                canonical_json(event_counters).decode("utf-8"),
                attempt,
                elapsed_ms,
                error_code.value if error_code else None,
                now,
            ),
        )

    def open_run(
        self,
        metadata: RunMetadata,
        *,
        expected_items: int = 0,
        expected_bytes: int = 0,
    ) -> RunRecord:
        self._admit(expected_items, expected_bytes)
        fingerprint = run_fingerprint(metadata)
        run_id_value = run_id(metadata)
        now_value = self._now()
        now = _iso(now_value)
        retention_until = _iso(
            now_value + timedelta(seconds=self.limits.retention_seconds)
        )
        metadata_json = canonical_json(metadata.to_dict()).decode("utf-8")
        with self._transaction():
            existing = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            if existing is not None:
                if existing["metadata_json"] != metadata_json:
                    raise JournalError(
                        TerminalErrorCode.INCOMPATIBLE_SCHEMA,
                        "run identity collision has incompatible metadata",
                    )
                self._add_event(
                    run_id_value=run_id_value,
                    event_type="run_resumed",
                    now=now,
                )
                return self._run_from_row(existing)
            incompatible = self._connection.execute(
                """
                SELECT run_id FROM runs
                WHERE project_id = ? AND scope_id = ?
                  AND physical_target = ? AND parser = ?
                  AND status IN (?, ?)
                """,
                (
                    metadata.project_id,
                    metadata.scope_id,
                    metadata.physical_target,
                    metadata.parser,
                    *_ACTIVE_RUN_STATUSES,
                ),
            ).fetchall()
            for row in incompatible:
                incompatible_retention = _iso(
                    now_value + timedelta(seconds=self.limits.retention_seconds)
                )
                self._connection.execute(
                    """
                    UPDATE runs SET status = ?, updated_at = ?, retention_until = ?,
                        error_code = ?, error_detail = ?
                    WHERE run_id = ?
                    """,
                    (
                        RunStatus.QUARANTINED.value,
                        now,
                        incompatible_retention,
                        TerminalErrorCode.INCOMPATIBLE_SCHEMA.value,
                        "superseded by an incompatible source/target/schema/query fingerprint",
                        row["run_id"],
                    ),
                )
                self._add_event(
                    run_id_value=row["run_id"],
                    event_type="run_quarantined",
                    error_code=TerminalErrorCode.INCOMPATIBLE_SCHEMA,
                    now=now,
                )
                self._connection.execute(
                    """
                    UPDATE batches
                    SET status = ?, fencing_token = NULL, lease_until = NULL,
                        retry_class = ?, error_code = ?, updated_at = ?
                    WHERE run_id = ? AND status != ?
                    """,
                    (
                        BatchStatus.BLOCKED.value,
                        RetryClass.INCOMPATIBLE.value,
                        TerminalErrorCode.INCOMPATIBLE_SCHEMA.value,
                        now,
                        row["run_id"],
                        BatchStatus.DONE.value,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO runs(
                    run_id, fingerprint, project_id, scope_id, physical_target,
                    parser, metadata_json, status, created_at, updated_at,
                    retention_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id_value,
                    fingerprint,
                    metadata.project_id,
                    metadata.scope_id,
                    metadata.physical_target,
                    metadata.parser,
                    metadata_json,
                    RunStatus.OPEN.value,
                    now,
                    now,
                    retention_until,
                ),
            )
            self._add_event(run_id_value=run_id_value, event_type="run_opened", now=now)
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            return self._run_from_row(row)

    def get_run(self, run_id_value: str) -> RunRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def find_resumable_run(self, metadata: RunMetadata) -> RunRecord | None:
        """Return the newest open run with the same non-generation contract."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM runs
                WHERE project_id = ? AND scope_id = ? AND physical_target = ?
                  AND parser = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (
                    metadata.project_id,
                    metadata.scope_id,
                    metadata.physical_target,
                    metadata.parser,
                    RunStatus.OPEN.value,
                ),
            ).fetchall()
        expected = metadata.to_dict()
        expected.pop("generation", None)
        for row in rows:
            candidate = self._run_from_row(row)
            actual = candidate.metadata.to_dict()
            actual.pop("generation", None)
            if actual == expected:
                return candidate
        return None

    def quarantine_legacy_targets(
        self,
        metadata: RunMetadata,
        physical_targets: Iterable[str],
    ) -> int:
        """Fence active runs that use a superseded target-identity format."""

        targets = tuple(
            sorted(
                {
                    str(target).strip()
                    for target in physical_targets
                    if str(target).strip() and str(target).strip() != metadata.physical_target
                }
            )
        )
        if not targets:
            return 0
        placeholders = ", ".join("?" for _ in targets)
        now_value = self._now()
        now = _iso(now_value)
        retention_until = _iso(
            now_value + timedelta(seconds=self.limits.retention_seconds)
        )
        with self._transaction():
            rows = self._connection.execute(
                f"""
                SELECT run_id FROM runs
                WHERE project_id = ? AND scope_id = ? AND parser = ?
                  AND physical_target IN ({placeholders})
                  AND status IN (?, ?)
                """,
                (
                    metadata.project_id,
                    metadata.scope_id,
                    metadata.parser,
                    *targets,
                    *_ACTIVE_RUN_STATUSES,
                ),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    """
                    UPDATE runs SET status = ?, updated_at = ?, retention_until = ?,
                        error_code = ?, error_detail = ?
                    WHERE run_id = ?
                    """,
                    (
                        RunStatus.QUARANTINED.value,
                        now,
                        retention_until,
                        TerminalErrorCode.INCOMPATIBLE_SCHEMA.value,
                        "superseded by the credential-free effective-target identity contract",
                        row["run_id"],
                    ),
                )
                self._add_event(
                    run_id_value=row["run_id"],
                    event_type="run_quarantined",
                    error_code=TerminalErrorCode.INCOMPATIBLE_SCHEMA,
                    now=now,
                )
                self._connection.execute(
                    """
                    UPDATE batches
                    SET status = ?, fencing_token = NULL, lease_until = NULL,
                        retry_class = ?, error_code = ?, updated_at = ?
                    WHERE run_id = ? AND status != ?
                    """,
                    (
                        BatchStatus.BLOCKED.value,
                        RetryClass.INCOMPATIBLE.value,
                        TerminalErrorCode.INCOMPATIBLE_SCHEMA.value,
                        now,
                        row["run_id"],
                        BatchStatus.DONE.value,
                    ),
                )
        return len(rows)

    def create_artifact(self, run_id_value: str, rows: Iterable[Any]) -> ArtifactRef:
        run = self.get_run(run_id_value)
        if run is None or run.status is not RunStatus.OPEN:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "artifacts may only be created for an open run",
            )
        return self.artifacts.write_jsonl(run_id_value, rows)

    def enqueue_batch(self, run_id_value: str, spec: BatchSpec) -> BatchRecord:
        run = self.get_run(run_id_value)
        if run is None:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "batch run does not exist",
            )
        artifact_parts = PurePosixPath(spec.artifact.relative_path).parts
        expected_parts = (run_id_value, f"{spec.artifact.sha256}.jsonl")
        if artifact_parts != expected_parts:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "artifact does not belong to the target run",
            )
        self.artifacts.verify(spec.artifact)
        job_id = deterministic_job_id(
            run_fingerprint_value=run.fingerprint,
            phase=spec.phase,
            operation_key=spec.operation_key,
            sequence=spec.sequence,
            payload_sha256=spec.artifact.sha256,
        )
        now = _iso(self._now())
        with self._transaction():
            duplicate = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            if duplicate is not None:
                return self._batch_from_row(duplicate)
            current_run = self._connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            if current_run is None or current_run["status"] != RunStatus.OPEN.value:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "batches may only be enqueued into an open run",
                )
            totals = self._connection.execute(
                "SELECT COUNT(*) AS items, COALESCE(SUM(payload_bytes), 0) AS bytes FROM batches WHERE run_id = ?",
                (run_id_value,),
            ).fetchone()
            self._admit(
                int(totals["items"]) + 1,
                int(totals["bytes"]) + spec.artifact.byte_count,
            )
            existing_artifact = self._connection.execute(
                "SELECT * FROM artifacts WHERE run_id = ? AND sha256 = ?",
                (run_id_value, spec.artifact.sha256),
            ).fetchone()
            if existing_artifact is None:
                self._connection.execute(
                    """
                    INSERT INTO artifacts(
                        run_id, sha256, relative_path, byte_count, row_count,
                        ref_count, created_at, last_referenced_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        run_id_value,
                        spec.artifact.sha256,
                        spec.artifact.relative_path,
                        spec.artifact.byte_count,
                        spec.artifact.row_count,
                        now,
                        now,
                    ),
                )
            else:
                if (
                    existing_artifact["relative_path"] != spec.artifact.relative_path
                    or int(existing_artifact["byte_count"]) != spec.artifact.byte_count
                    or int(existing_artifact["row_count"]) != spec.artifact.row_count
                ):
                    raise JournalError(
                        TerminalErrorCode.ARTIFACT_HASH_MISMATCH,
                        "artifact metadata conflicts with its content hash",
                    )
                self._connection.execute(
                    """
                    UPDATE artifacts
                    SET ref_count = ref_count + 1, last_referenced_at = ?
                    WHERE run_id = ? AND sha256 = ?
                    """,
                    (now, run_id_value, spec.artifact.sha256),
                )
            required = tuple(sorted(set(spec.required_barriers)))
            produced = tuple(sorted(set(spec.produced_barriers)))
            self._connection.execute(
                """
                INSERT INTO batches(
                    job_id, run_id, phase, operation_key, sequence,
                    artifact_sha256, artifact_path, payload_bytes, row_count,
                    expected_count, status, max_attempts,
                    required_barriers_json, produced_barriers_json,
                    operation_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    run_id_value,
                    spec.phase.value,
                    spec.operation_key,
                    spec.sequence,
                    spec.artifact.sha256,
                    spec.artifact.relative_path,
                    spec.artifact.byte_count,
                    spec.artifact.row_count,
                    spec.expected_count,
                    BatchStatus.PENDING.value,
                    spec.max_attempts,
                    json.dumps(required, separators=(",", ":")),
                    json.dumps(produced, separators=(",", ":")),
                    canonical_json(dict(spec.operation)).decode("utf-8"),
                    now,
                    now,
                ),
            )
            for barrier_name in produced:
                barrier = self._connection.execute(
                    "SELECT status FROM barriers WHERE run_id = ? AND name = ?",
                    (run_id_value, barrier_name),
                ).fetchone()
                if barrier is None:
                    self._connection.execute(
                        """
                        INSERT INTO barriers(
                            run_id, name, status, produced_count, drained_count, updated_at
                        ) VALUES (?, ?, ?, 1, 0, ?)
                        """,
                        (run_id_value, barrier_name, BarrierStatus.OPEN.value, now),
                    )
                elif barrier["status"] == BarrierStatus.OPEN.value:
                    self._connection.execute(
                        """
                        UPDATE barriers SET produced_count = produced_count + 1, updated_at = ?
                        WHERE run_id = ? AND name = ?
                        """,
                        (now, run_id_value, barrier_name),
                    )
                else:
                    raise JournalError(
                        TerminalErrorCode.INVALID_TRANSITION,
                        f"cannot enqueue producer after barrier {barrier_name} is closed",
                    )
            self._add_event(
                run_id_value=run_id_value,
                job_id=job_id,
                event_type="batch_enqueued",
                counters={
                    "bytes": spec.artifact.byte_count,
                    "rows": spec.artifact.row_count,
                },
                now=now,
            )
            row = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._batch_from_row(row)

    def open_barrier(self, run_id_value: str, name: str) -> BarrierRecord:
        if not name.strip():
            raise ValueError("barrier name must not be empty")
        now = _iso(self._now())
        with self._transaction():
            run = self._connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            if run is None or run["status"] != RunStatus.OPEN.value:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "barriers may only be opened for an open run",
                )
            self._connection.execute(
                """
                INSERT INTO barriers(run_id, name, status, produced_count, drained_count, updated_at)
                VALUES (?, ?, ?, 0, 0, ?)
                ON CONFLICT(run_id, name) DO NOTHING
                """,
                (run_id_value, name, BarrierStatus.OPEN.value, now),
            )
            row = self._connection.execute(
                "SELECT * FROM barriers WHERE run_id = ? AND name = ?",
                (run_id_value, name),
            ).fetchone()
            return self._barrier_from_row(row)

    def close_barrier(self, run_id_value: str, name: str) -> BarrierRecord:
        now = _iso(self._now())
        with self._transaction():
            row = self._connection.execute(
                "SELECT * FROM barriers WHERE run_id = ? AND name = ?",
                (run_id_value, name),
            ).fetchone()
            if row is None:
                raise JournalError(
                    TerminalErrorCode.INVALID_CONTRACT, "barrier does not exist"
                )
            if row["status"] == BarrierStatus.DRAINED.value:
                return self._barrier_from_row(row)
            run = self._connection.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            if run is None or run["status"] != RunStatus.OPEN.value:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "barriers may only be closed for an open run",
                )
            status = (
                BarrierStatus.DRAINED
                if int(row["drained_count"]) == int(row["produced_count"])
                else BarrierStatus.PRODUCED
            )
            self._connection.execute(
                """
                UPDATE barriers SET status = ?, closed_at = ?, updated_at = ?
                WHERE run_id = ? AND name = ?
                """,
                (status.value, now, now, run_id_value, name),
            )
            if status is BarrierStatus.DRAINED:
                self._add_event(
                    run_id_value=run_id_value,
                    event_type="barrier_reached",
                    counters={"produced": int(row["produced_count"])},
                    now=now,
                )
            updated = self._connection.execute(
                "SELECT * FROM barriers WHERE run_id = ? AND name = ?",
                (run_id_value, name),
            ).fetchone()
            return self._barrier_from_row(updated)

    def get_barrier(self, run_id_value: str, name: str) -> BarrierRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM barriers WHERE run_id = ? AND name = ?",
                (run_id_value, name),
            ).fetchone()
        return self._barrier_from_row(row) if row is not None else None

    def claim_batch(
        self,
        *,
        run_id_value: str | None = None,
        lease_seconds: int = 60,
    ) -> BatchRecord | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value = self._now()
        now = _iso(now_value)
        lease_until = _iso(now_value + timedelta(seconds=lease_seconds))
        with self._transaction():
            self._recover_expired_leases_locked(now)
            query = """
                SELECT b.* FROM batches b
                JOIN runs r ON r.run_id = b.run_id
                WHERE b.status IN (?, ?)
                  AND (b.next_attempt_at IS NULL OR b.next_attempt_at <= ?)
                  AND r.status IN (?, ?)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM json_each(b.required_barriers_json) required
                      LEFT JOIN barriers barrier
                        ON barrier.run_id = b.run_id
                       AND barrier.name = required.value
                      WHERE barrier.status IS NULL OR barrier.status != ?
                  )
            """
            params: list[Any] = [
                BatchStatus.PENDING.value,
                BatchStatus.RETRY_WAIT.value,
                now,
                *_ACTIVE_RUN_STATUSES,
                BarrierStatus.DRAINED.value,
            ]
            if run_id_value is not None:
                query += " AND b.run_id = ?"
                params.append(run_id_value)
            query += " ORDER BY b.sequence, b.created_at, b.job_id LIMIT 1"
            row = self._connection.execute(query, params).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(16)
            result = self._connection.execute(
                """
                UPDATE batches
                SET status = ?, attempt = attempt + 1, fencing_token = ?,
                    lease_until = ?, next_attempt_at = NULL, updated_at = ?
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (
                    BatchStatus.LEASED.value,
                    token,
                    lease_until,
                    now,
                    row["job_id"],
                    BatchStatus.PENDING.value,
                    BatchStatus.RETRY_WAIT.value,
                ),
            )
            if result.rowcount != 1:
                return None
            claimed = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            self._add_event(
                run_id_value=claimed["run_id"],
                job_id=claimed["job_id"],
                event_type="batch_leased",
                attempt=int(claimed["attempt"]),
                now=now,
            )
            return self._batch_from_row(claimed)

    def claim_job(self, job_id: str, *, lease_seconds: int = 300) -> BatchRecord | None:
        """Fence one exact executable batch without consuming adjacent work."""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value = self._now()
        now = _iso(now_value)
        lease_until = _iso(now_value + timedelta(seconds=lease_seconds))
        with self._transaction():
            self._recover_expired_leases_locked(now)
            row = self._connection.execute(
                """
                SELECT b.* FROM batches b
                JOIN runs r ON r.run_id = b.run_id
                WHERE b.job_id = ?
                  AND b.status IN (?, ?)
                  AND (b.next_attempt_at IS NULL OR b.next_attempt_at <= ?)
                  AND r.status IN (?, ?)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM json_each(b.required_barriers_json) required
                      LEFT JOIN barriers barrier
                        ON barrier.run_id = b.run_id
                       AND barrier.name = required.value
                      WHERE barrier.status IS NULL OR barrier.status != ?
                  )
                """,
                (
                    job_id,
                    BatchStatus.PENDING.value,
                    BatchStatus.RETRY_WAIT.value,
                    now,
                    *_ACTIVE_RUN_STATUSES,
                    BarrierStatus.DRAINED.value,
                ),
            ).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(16)
            result = self._connection.execute(
                """
                UPDATE batches
                SET status = ?, attempt = attempt + 1, fencing_token = ?,
                    lease_until = ?, next_attempt_at = NULL, updated_at = ?
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (
                    BatchStatus.LEASED.value,
                    token,
                    lease_until,
                    now,
                    job_id,
                    BatchStatus.PENDING.value,
                    BatchStatus.RETRY_WAIT.value,
                ),
            )
            if result.rowcount != 1:
                return None
            claimed = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._add_event(
                run_id_value=claimed["run_id"],
                job_id=job_id,
                event_type="batch_leased",
                attempt=int(claimed["attempt"]),
                now=now,
            )
            return self._batch_from_row(claimed)

    def claim_reconciling(
        self,
        *,
        run_id_value: str | None = None,
        lease_seconds: int = 60,
    ) -> BatchRecord | None:
        """Fence one ambiguous batch for readback without making it executable."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value = self._now()
        now = _iso(now_value)
        lease_until = _iso(now_value + timedelta(seconds=lease_seconds))
        with self._transaction():
            self._recover_expired_leases_locked(now)
            query = """
                SELECT b.* FROM batches b
                JOIN runs r ON r.run_id = b.run_id
                WHERE b.status = ? AND b.fencing_token IS NULL
                  AND r.status IN (?, ?)
            """
            params: list[Any] = [BatchStatus.RECONCILING.value, *_ACTIVE_RUN_STATUSES]
            if run_id_value is not None:
                query += " AND b.run_id = ?"
                params.append(run_id_value)
            query += " ORDER BY b.sequence, b.created_at, b.job_id LIMIT 1"
            row = self._connection.execute(query, params).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(16)
            result = self._connection.execute(
                """
                UPDATE batches SET fencing_token = ?, lease_until = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND fencing_token IS NULL
                """,
                (
                    token,
                    lease_until,
                    now,
                    row["job_id"],
                    BatchStatus.RECONCILING.value,
                ),
            )
            if result.rowcount != 1:
                return None
            claimed = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
            self._add_event(
                run_id_value=claimed["run_id"],
                job_id=claimed["job_id"],
                event_type="batch_reconciliation_leased",
                attempt=int(claimed["attempt"]),
                now=now,
            )
            return self._batch_from_row(claimed)

    def claim_reconciling_job(
        self, job_id: str, *, lease_seconds: int = 300
    ) -> BatchRecord | None:
        """Fence one exact ambiguous batch for deterministic reconciliation."""

        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value = self._now()
        now = _iso(now_value)
        lease_until = _iso(now_value + timedelta(seconds=lease_seconds))
        with self._transaction():
            self._recover_expired_leases_locked(now)
            row = self._connection.execute(
                """
                SELECT b.* FROM batches b
                JOIN runs r ON r.run_id = b.run_id
                WHERE b.job_id = ? AND b.status = ?
                  AND b.fencing_token IS NULL
                  AND r.status IN (?, ?)
                """,
                (job_id, BatchStatus.RECONCILING.value, *_ACTIVE_RUN_STATUSES),
            ).fetchone()
            if row is None:
                return None
            token = secrets.token_hex(16)
            result = self._connection.execute(
                """
                UPDATE batches SET fencing_token = ?, lease_until = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND fencing_token IS NULL
                """,
                (
                    token,
                    lease_until,
                    now,
                    job_id,
                    BatchStatus.RECONCILING.value,
                ),
            )
            if result.rowcount != 1:
                return None
            claimed = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            self._add_event(
                run_id_value=claimed["run_id"],
                job_id=job_id,
                event_type="batch_reconciliation_leased",
                attempt=int(claimed["attempt"]),
                now=now,
            )
            return self._batch_from_row(claimed)

    def renew_lease(
        self, job_id: str, fencing_token: str, *, lease_seconds: int = 60
    ) -> BatchRecord:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_value = self._now()
        now = _iso(now_value)
        lease_until = _iso(now_value + timedelta(seconds=lease_seconds))
        with self._transaction():
            result = self._connection.execute(
                """
                UPDATE batches SET lease_until = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND fencing_token = ? AND lease_until > ?
                """,
                (
                    lease_until,
                    now,
                    job_id,
                    BatchStatus.LEASED.value,
                    fencing_token,
                    now,
                ),
            )
            if result.rowcount != 1:
                raise StaleFenceError(job_id)
            row = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._batch_from_row(row)

    def ack_batch(
        self,
        job_id: str,
        fencing_token: str,
        *,
        elapsed_ms: int | None = None,
    ) -> BatchRecord:
        now = _iso(self._now())
        with self._transaction():
            row = self._owned_transition(job_id, fencing_token, now)
            self._connection.execute(
                """
                UPDATE batches SET status = ?, fencing_token = NULL, lease_until = NULL,
                    retry_class = NULL, error_code = NULL, error_detail = NULL, updated_at = ?
                WHERE job_id = ? AND status IN (?, ?) AND fencing_token = ?
                """,
                (
                    BatchStatus.DONE.value,
                    now,
                    job_id,
                    BatchStatus.LEASED.value,
                    BatchStatus.RECONCILING.value,
                    fencing_token,
                ),
            )
            for name in json.loads(row["produced_barriers_json"]):
                barrier = self._connection.execute(
                    "SELECT * FROM barriers WHERE run_id = ? AND name = ?",
                    (row["run_id"], name),
                ).fetchone()
                if barrier is None:
                    raise JournalError(
                        TerminalErrorCode.INVALID_CONTRACT,
                        "produced barrier is missing",
                    )
                drained_count = int(barrier["drained_count"]) + 1
                status = BarrierStatus(barrier["status"])
                if barrier["closed_at"] is not None and drained_count == int(
                    barrier["produced_count"]
                ):
                    status = BarrierStatus.DRAINED
                self._connection.execute(
                    """
                    UPDATE barriers SET drained_count = ?, status = ?, updated_at = ?
                    WHERE run_id = ? AND name = ?
                    """,
                    (drained_count, status.value, now, row["run_id"], name),
                )
                if status is BarrierStatus.DRAINED:
                    self._add_event(
                        run_id_value=row["run_id"],
                        event_type="barrier_reached",
                        counters={"produced": int(barrier["produced_count"])},
                        now=now,
                    )
            self._add_event(
                run_id_value=row["run_id"],
                job_id=job_id,
                event_type="batch_acked",
                counters={"rows": int(row["row_count"])},
                attempt=int(row["attempt"]),
                elapsed_ms=elapsed_ms,
                now=now,
            )
            self._refresh_run_status_locked(row["run_id"], now)
            updated = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._batch_from_row(updated)

    def mark_reconciling(
        self,
        job_id: str,
        fencing_token: str,
        *,
        error_code: TerminalErrorCode | None = None,
    ) -> BatchRecord:
        now = _iso(self._now())
        with self._transaction():
            row = self._owned_lease(job_id, fencing_token, now)
            self._connection.execute(
                """
                UPDATE batches SET status = ?, retry_class = ?, error_code = ?, updated_at = ?
                WHERE job_id = ? AND status = ? AND fencing_token = ?
                """,
                (
                    BatchStatus.RECONCILING.value,
                    RetryClass.AMBIGUOUS.value,
                    error_code.value if error_code else None,
                    now,
                    job_id,
                    BatchStatus.LEASED.value,
                    fencing_token,
                ),
            )
            self._add_event(
                run_id_value=row["run_id"],
                job_id=job_id,
                event_type="batch_reconciling",
                attempt=int(row["attempt"]),
                error_code=error_code,
                now=now,
            )
            updated = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._batch_from_row(updated)

    def schedule_retry(
        self,
        job_id: str,
        fencing_token: str,
        *,
        retry_at: datetime,
        retry_class: RetryClass,
        error_code: TerminalErrorCode,
    ) -> BatchRecord:
        now = _iso(self._now())
        with self._transaction():
            row = self._owned_transition(job_id, fencing_token, now)
            exhausted = int(row["attempt"]) >= int(row["max_attempts"])
            status = BatchStatus.DEAD_LETTER if exhausted else BatchStatus.RETRY_WAIT
            terminal_code = TerminalErrorCode.MAX_ATTEMPTS if exhausted else error_code
            self._connection.execute(
                """
                UPDATE batches SET status = ?, fencing_token = NULL, lease_until = NULL,
                    next_attempt_at = ?, retry_class = ?, error_code = ?, updated_at = ?
                WHERE job_id = ? AND fencing_token = ? AND status IN (?, ?)
                """,
                (
                    status.value,
                    None if exhausted else _iso(retry_at),
                    retry_class.value,
                    terminal_code.value,
                    now,
                    job_id,
                    fencing_token,
                    BatchStatus.LEASED.value,
                    BatchStatus.RECONCILING.value,
                ),
            )
            self._add_event(
                run_id_value=row["run_id"],
                job_id=job_id,
                event_type="batch_dead_lettered" if exhausted else "retry_scheduled",
                attempt=int(row["attempt"]),
                error_code=terminal_code,
                now=now,
            )
            if exhausted:
                self._set_run_error_locked(
                    row["run_id"], RunStatus.DEAD_LETTERED, terminal_code, now
                )
            updated = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._batch_from_row(updated)

    def block_batch(
        self,
        job_id: str,
        fencing_token: str,
        *,
        retry_class: RetryClass,
        error_code: TerminalErrorCode,
    ) -> BatchRecord:
        now = _iso(self._now())
        with self._transaction():
            row = self._owned_transition(job_id, fencing_token, now)
            self._connection.execute(
                """
                UPDATE batches SET status = ?, fencing_token = NULL, lease_until = NULL,
                    retry_class = ?, error_code = ?, updated_at = ?
                WHERE job_id = ? AND fencing_token = ? AND status IN (?, ?)
                """,
                (
                    BatchStatus.BLOCKED.value,
                    retry_class.value,
                    error_code.value,
                    now,
                    job_id,
                    fencing_token,
                    BatchStatus.LEASED.value,
                    BatchStatus.RECONCILING.value,
                ),
            )
            self._add_event(
                run_id_value=row["run_id"],
                job_id=job_id,
                event_type="batch_blocked",
                attempt=int(row["attempt"]),
                error_code=error_code,
                now=now,
            )
            self._set_run_error_locked(
                row["run_id"], RunStatus.BLOCKED, error_code, now
            )
            updated = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._batch_from_row(updated)

    def _owned_lease(self, job_id: str, fencing_token: str, now: str) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT * FROM batches
            WHERE job_id = ? AND status = ? AND fencing_token = ? AND lease_until > ?
            """,
            (job_id, BatchStatus.LEASED.value, fencing_token, now),
        ).fetchone()
        if row is None:
            raise StaleFenceError(job_id)
        return row

    def _owned_transition(
        self, job_id: str, fencing_token: str, now: str
    ) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT * FROM batches
            WHERE job_id = ? AND status IN (?, ?) AND fencing_token = ? AND lease_until > ?
            """,
            (
                job_id,
                BatchStatus.LEASED.value,
                BatchStatus.RECONCILING.value,
                fencing_token,
                now,
            ),
        ).fetchone()
        if row is None:
            raise StaleFenceError(job_id)
        return row

    def close_run_production(self, run_id_value: str) -> RunRecord:
        now = _iso(self._now())
        with self._transaction():
            result = self._connection.execute(
                """
                UPDATE runs SET status = ?, updated_at = ?
                WHERE run_id = ? AND status = ?
                """,
                (RunStatus.DRAINING.value, now, run_id_value, RunStatus.OPEN.value),
            )
            if result.rowcount != 1:
                row = self._connection.execute(
                    "SELECT status FROM runs WHERE run_id = ?", (run_id_value,)
                ).fetchone()
                if row is None or RunStatus(row["status"]) not in {
                    RunStatus.DRAINING,
                    RunStatus.DRAINED,
                }:
                    raise JournalError(
                        TerminalErrorCode.INVALID_TRANSITION,
                        "run cannot close production",
                    )
            self._refresh_run_status_locked(run_id_value, now)
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            return self._run_from_row(row)

    def _refresh_run_status_locked(self, run_id_value: str, now: str) -> None:
        run = self._connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id_value,)
        ).fetchone()
        if run is None or run["status"] != RunStatus.DRAINING.value:
            return
        unfinished = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM batches WHERE run_id = ? AND status != ?",
                (run_id_value, BatchStatus.DONE.value),
            ).fetchone()[0]
        )
        undrained = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM barriers WHERE run_id = ? AND status != ?",
                (run_id_value, BarrierStatus.DRAINED.value),
            ).fetchone()[0]
        )
        if unfinished == 0 and undrained == 0:
            retention_until = _iso(
                _parse_datetime(now) + timedelta(seconds=self.limits.retention_seconds)
            )
            self._connection.execute(
                """
                UPDATE runs SET status = ?, updated_at = ?, retention_until = ?
                WHERE run_id = ?
                """,
                (RunStatus.DRAINED.value, now, retention_until, run_id_value),
            )
            self._add_event(
                run_id_value=run_id_value, event_type="queue_drained", now=now
            )

    def _set_run_error_locked(
        self,
        run_id_value: str,
        status: RunStatus,
        error_code: TerminalErrorCode,
        now: str,
    ) -> None:
        retention_until = _iso(
            _parse_datetime(now) + timedelta(seconds=self.limits.retention_seconds)
        )
        self._connection.execute(
            """
            UPDATE runs SET status = ?, error_code = ?, updated_at = ?, retention_until = ?
            WHERE run_id = ?
            """,
            (status.value, error_code.value, now, retention_until, run_id_value),
        )

    def recover_expired_leases(self) -> int:
        now = _iso(self._now())
        with self._transaction():
            return self._recover_expired_leases_locked(now)

    def recover_run_leases_as_ambiguous(self, run_id_value: str) -> int:
        """Take over leases left by a previous process for the same locked run.

        The orchestrators serialize a project scope. A lease still present when a
        new analyzer process opens the identical run therefore has no live owner,
        but its graph outcome is unknown and must be reconciled before replay.
        """

        now = _iso(self._now())
        with self._transaction():
            rows = self._connection.execute(
                """
                SELECT job_id, attempt FROM batches
                WHERE run_id = ? AND status = ?
                """,
                (run_id_value, BatchStatus.LEASED.value),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    """
                    UPDATE batches
                    SET status = ?, fencing_token = NULL, lease_until = NULL,
                        retry_class = ?, updated_at = ?
                    WHERE job_id = ? AND status = ?
                    """,
                    (
                        BatchStatus.RECONCILING.value,
                        RetryClass.AMBIGUOUS.value,
                        now,
                        row["job_id"],
                        BatchStatus.LEASED.value,
                    ),
                )
                self._add_event(
                    run_id_value=run_id_value,
                    job_id=row["job_id"],
                    event_type="lease_recovered_as_ambiguous",
                    attempt=int(row["attempt"]),
                    now=now,
                )
            reconciling = self._connection.execute(
                """
                SELECT job_id, attempt FROM batches
                WHERE run_id = ? AND status = ? AND fencing_token IS NOT NULL
                """,
                (run_id_value, BatchStatus.RECONCILING.value),
            ).fetchall()
            for row in reconciling:
                self._connection.execute(
                    """
                    UPDATE batches SET fencing_token = NULL, lease_until = NULL,
                        updated_at = ?
                    WHERE job_id = ? AND status = ?
                    """,
                    (now, row["job_id"], BatchStatus.RECONCILING.value),
                )
                self._add_event(
                    run_id_value=run_id_value,
                    job_id=row["job_id"],
                    event_type="reconciliation_ownership_recovered",
                    attempt=int(row["attempt"]),
                    now=now,
                )
            return len(rows) + len(reconciling)

    def _recover_expired_leases_locked(self, now: str) -> int:
        rows = self._connection.execute(
            """
            SELECT job_id, run_id, attempt FROM batches
            WHERE status = ? AND lease_until IS NOT NULL AND lease_until <= ?
            """,
            (BatchStatus.LEASED.value, now),
        ).fetchall()
        for row in rows:
            self._connection.execute(
                """
                UPDATE batches SET status = ?, fencing_token = NULL, lease_until = NULL, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    BatchStatus.PENDING.value,
                    now,
                    row["job_id"],
                    BatchStatus.LEASED.value,
                ),
            )
            self._add_event(
                run_id_value=row["run_id"],
                job_id=row["job_id"],
                event_type="lease_recovered",
                attempt=int(row["attempt"]),
                now=now,
            )
        reconciling = self._connection.execute(
            """
            SELECT job_id, run_id, attempt FROM batches
            WHERE status = ? AND fencing_token IS NOT NULL
              AND lease_until IS NOT NULL AND lease_until <= ?
            """,
            (BatchStatus.RECONCILING.value, now),
        ).fetchall()
        for row in reconciling:
            self._connection.execute(
                """
                UPDATE batches SET fencing_token = NULL, lease_until = NULL, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (now, row["job_id"], BatchStatus.RECONCILING.value),
            )
            self._add_event(
                run_id_value=row["run_id"],
                job_id=row["job_id"],
                event_type="reconciliation_recovered",
                attempt=int(row["attempt"]),
                now=now,
            )
        return len(rows) + len(reconciling)

    def verify_referenced_artifacts(self) -> None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT sha256, relative_path, byte_count, row_count FROM artifacts WHERE ref_count > 0"
            ).fetchall()
        for row in rows:
            self.artifacts.verify(
                ArtifactRef(
                    sha256=row["sha256"],
                    relative_path=row["relative_path"],
                    byte_count=int(row["byte_count"]),
                    row_count=int(row["row_count"]),
                )
            )

    def get_batch(self, job_id: str) -> BatchRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM batches WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._batch_from_row(row) if row is not None else None

    def status_counts(self, run_id_value: str) -> dict[BatchStatus, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM batches WHERE run_id = ? GROUP BY status",
                (run_id_value,),
            ).fetchall()
        counts = {status: 0 for status in BatchStatus}
        counts.update({BatchStatus(row["status"]): int(row["count"]) for row in rows})
        return counts

    def status_summary(self, run_id_value: str) -> dict[str, Any]:
        """Return payload-free operational counters for CLI and sync summaries."""

        run = self.get_run(run_id_value)
        if run is None:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "journal run does not exist",
            )
        counts = self.status_counts(run_id_value)
        with self._lock:
            totals = self._connection.execute(
                """
                SELECT COUNT(*) AS produced,
                       COALESCE(SUM(payload_bytes), 0) AS payload_bytes,
                       COALESCE(SUM(row_count), 0) AS rows
                FROM batches WHERE run_id = ?
                """,
                (run_id_value,),
            ).fetchone()
            oldest = self._connection.execute(
                """
                SELECT MIN(created_at) AS oldest_at FROM batches
                WHERE run_id = ? AND status != ?
                """,
                (run_id_value, BatchStatus.DONE.value),
            ).fetchone()
            resumed = bool(
                self._connection.execute(
                    "SELECT 1 FROM events WHERE run_id = ? AND event_type = ? LIMIT 1",
                    (run_id_value, "run_resumed"),
                ).fetchone()
            )
            artifact_bytes = int(
                self._connection.execute(
                    "SELECT COALESCE(SUM(byte_count), 0) FROM artifacts WHERE run_id = ?",
                    (run_id_value,),
                ).fetchone()[0]
            )
        oldest_at = oldest["oldest_at"]
        oldest_age_seconds = (
            max(0.0, (self._now() - _parse_datetime(oldest_at)).total_seconds())
            if oldest_at
            else None
        )
        if run.status is RunStatus.DRAINED:
            next_action = "none"
        elif run.status in {RunStatus.BLOCKED, RunStatus.DEAD_LETTERED}:
            next_action = "inspect_error_and_acknowledge_or_purge_after_retention"
        elif run.status is RunStatus.QUARANTINED:
            next_action = "inspect_incompatible_fingerprint"
        elif counts[BatchStatus.RECONCILING]:
            next_action = "reconcile_ambiguous_batches"
        elif counts[BatchStatus.RETRY_WAIT]:
            next_action = "wait_for_retry"
        elif counts[BatchStatus.LEASED]:
            next_action = "wait_for_active_consumer"
        elif counts[BatchStatus.PENDING]:
            next_action = "resume_consumer"
        else:
            next_action = "close_production"
        journal_bytes = sum(
            candidate.stat().st_size
            for suffix in ("", "-wal", "-shm")
            if (candidate := Path(f"{self.path}{suffix}")).is_file()
        )
        return {
            "run_id": run.run_id,
            "status": run.status.value,
            "resumed": resumed,
            "parser": run.metadata.parser,
            "produced": int(totals["produced"]),
            "acked": counts[BatchStatus.DONE],
            "pending": counts[BatchStatus.PENDING],
            "leased": counts[BatchStatus.LEASED],
            "retrying": counts[BatchStatus.RETRY_WAIT],
            "reconciling": counts[BatchStatus.RECONCILING],
            "blocked": counts[BatchStatus.BLOCKED],
            "dead_letter": counts[BatchStatus.DEAD_LETTER],
            "rows": int(totals["rows"]),
            "payload_bytes": int(totals["payload_bytes"]),
            "artifact_bytes": artifact_bytes,
            "journal_bytes": journal_bytes,
            "oldest_unfinished_at": oldest_at,
            "oldest_unfinished_age_seconds": oldest_age_seconds,
            "next_action": next_action,
            "error_code": run.error_code.value if run.error_code else None,
        }

    def list_events(self, run_id_value: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT e.event_id, e.run_id, e.job_id, e.event_type,
                       e.counters_json, e.attempt, e.elapsed_ms, e.error_code,
                       e.created_at, r.parser, b.phase, b.operation_key,
                       b.row_count
                FROM events AS e
                JOIN runs AS r ON r.run_id = e.run_id
                LEFT JOIN batches AS b ON b.job_id = e.job_id
                WHERE e.run_id = ? ORDER BY e.event_id
                """,
                (run_id_value,),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "job_id": row["job_id"],
                "event_type": row["event_type"],
                "parser": row["parser"],
                "phase": row["phase"] or "run",
                "operation": row["operation_key"] or row["event_type"],
                "rows": int(row["row_count"] or 0),
                "pending": int(json.loads(row["counters_json"]).get("pending", 0)),
                "counters": json.loads(row["counters_json"]),
                "attempt": row["attempt"],
                "elapsed_ms": row["elapsed_ms"],
                "error_code": row["error_code"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def checkpoint(self, *, truncate: bool = False) -> tuple[int, int, int]:
        mode = "TRUNCATE" if truncate else "PASSIVE"
        with self._lock:
            row = self._connection.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
            self._protect_sqlite_files()
        return int(row[0]), int(row[1]), int(row[2])

    def purge_run(
        self,
        run_id_value: str,
        *,
        ownership_confirmed: bool = False,
        now: datetime | None = None,
    ) -> int:
        if not ownership_confirmed:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "journal purge requires the caller to hold the exact-scope ownership lock",
            )
        now_value = now or self._now()
        refs: list[ArtifactRef]
        with self._transaction():
            run = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id_value,)
            ).fetchone()
            if run is None:
                return 0
            status = RunStatus(run["status"])
            if status not in _TERMINAL_RUN_STATUSES:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "active journal runs cannot be purged",
                )
            if _parse_datetime(run["retention_until"]) > now_value:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "journal retention period has not elapsed",
                )
            fenced = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM batches WHERE run_id = ? AND fencing_token IS NOT NULL",
                    (run_id_value,),
                ).fetchone()[0]
            )
            if fenced:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    "fenced journal work cannot be purged",
                )
            rows = self._connection.execute(
                "SELECT sha256, relative_path, byte_count, row_count FROM artifacts WHERE run_id = ?",
                (run_id_value,),
            ).fetchall()
            refs = [
                ArtifactRef(
                    row["sha256"],
                    row["relative_path"],
                    int(row["byte_count"]),
                    int(row["row_count"]),
                )
                for row in rows
            ]
            self._connection.execute(
                "DELETE FROM runs WHERE run_id = ?", (run_id_value,)
            )
        for ref in refs:
            self.artifacts.remove(ref)
        return len(refs)

    def cleanup_orphan_artifacts(
        self,
        *,
        ownership_confirmed: bool = False,
        older_than_seconds: int = 3600,
    ) -> int:
        """Remove only aged files with no committed artifact reference."""
        if not ownership_confirmed:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "artifact cleanup requires the exact-scope ownership lock",
            )
        if older_than_seconds <= 0:
            raise ValueError("older_than_seconds must be positive")
        cutoff = self._now().timestamp() - older_than_seconds
        with self._lock:
            referenced = {
                row["relative_path"]
                for row in self._connection.execute(
                    "SELECT relative_path FROM artifacts"
                ).fetchall()
            }
        removed = 0
        for run_directory in self.artifacts.root.iterdir():
            if not run_directory.is_dir() or run_directory.is_symlink():
                continue
            for candidate in run_directory.iterdir():
                if candidate.is_symlink():
                    candidate.unlink()
                    removed += 1
                    continue
                if not candidate.is_file() or candidate.stat().st_mtime > cutoff:
                    continue
                relative_path = candidate.relative_to(self.artifacts.root).as_posix()
                if relative_path in referenced:
                    continue
                self.artifacts.remove(
                    ArtifactRef(
                        sha256=candidate.stem,
                        relative_path=relative_path,
                        byte_count=candidate.stat().st_size,
                        row_count=0,
                    )
                )
                removed += 1
        return removed

    def close(self) -> None:
        with self._lock:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
                self._connection = None  # type: ignore[assignment]

    def __enter__(self) -> "SQLiteJournal":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        metadata = RunMetadata(**json.loads(row["metadata_json"]))
        return RunRecord(
            run_id=row["run_id"],
            fingerprint=row["fingerprint"],
            metadata=metadata,
            status=RunStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            retention_until=row["retention_until"],
            error_code=TerminalErrorCode(row["error_code"])
            if row["error_code"]
            else None,
        )

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> BatchRecord:
        return BatchRecord(
            job_id=row["job_id"],
            run_id=row["run_id"],
            phase=OperationPhase(row["phase"]),
            operation_key=row["operation_key"],
            sequence=int(row["sequence"]),
            artifact=ArtifactRef(
                sha256=row["artifact_sha256"],
                relative_path=row["artifact_path"],
                byte_count=int(row["payload_bytes"]),
                row_count=int(row["row_count"]),
            ),
            expected_count=int(row["expected_count"]),
            status=BatchStatus(row["status"]),
            attempt=int(row["attempt"]),
            max_attempts=int(row["max_attempts"]),
            fencing_token=row["fencing_token"],
            lease_until=row["lease_until"],
            next_attempt_at=row["next_attempt_at"],
            required_barriers=tuple(json.loads(row["required_barriers_json"])),
            produced_barriers=tuple(json.loads(row["produced_barriers_json"])),
            retry_class=RetryClass(row["retry_class"]) if row["retry_class"] else None,
            error_code=TerminalErrorCode(row["error_code"])
            if row["error_code"]
            else None,
            operation=json.loads(row["operation_json"]),
        )

    @staticmethod
    def _barrier_from_row(row: sqlite3.Row) -> BarrierRecord:
        return BarrierRecord(
            run_id=row["run_id"],
            name=row["name"],
            status=BarrierStatus(row["status"]),
            produced_count=int(row["produced_count"]),
            drained_count=int(row["drained_count"]),
            closed_at=row["closed_at"],
        )

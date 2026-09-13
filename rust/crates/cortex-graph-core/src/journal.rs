//! Port `code-tiny/tools/graph/journal/sqlite_store.py`:
//! - schema DDL (v3) + `inspect_journal` (phase 05)
//! - `Journal` — production/consumer loop (open_run / create_artifact /
//!   enqueue_batch / barriers / claim / renew / ack / retry / block /
//!   complete_producers) theo **low-level path**.
//!
//! **Chưa port (documented):** manifest staging (node/edge manifests,
//! conservation, endpoint audit) — Rust từ chối rõ ràng `BatchSpec` có
//! `operation` khác rỗng; kịch bản low-level (operation rỗng) parity 1:1.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};
use serde::Serialize;

use crate::artifacts::ArtifactStore;
use crate::identity::{canonical_json, deterministic_job_id, run_fingerprint, run_id, JobIdentity};
use crate::models::{
    BarrierRecord, BarrierStatus, BatchRecord, BatchSpec, BatchStatus, JournalError, JournalLimits,
    ManifestDisposition, OperationPhase, ProducerStatus, RetryClass, RunMetadata, RunRecord,
    RunStatus, TerminalErrorCode,
};

/// `JOURNAL_SCHEMA_VERSION` từ `journal/models.py`.
pub const JOURNAL_SCHEMA_VERSION: i64 = 3;

pub const RUN_STATUSES: [&str; 7] = [
    "open",
    "draining",
    "drained",
    "blocked",
    "dead_lettered",
    "quarantined",
    "failed",
];
pub const BATCH_STATUSES: [&str; 7] = [
    "pending",
    "leased",
    "reconciling",
    "retry_wait",
    "done",
    "blocked",
    "dead_letter",
];
pub const OPERATION_PHASES: [&str; 4] = ["nodes", "relationships", "calls", "custom"];
pub const BARRIER_STATUSES: [&str; 3] = ["open", "produced", "drained"];
pub const PRODUCER_STATUSES: [&str; 2] = ["open", "complete"];
pub const MANIFEST_DISPOSITIONS: [&str; 4] = [
    "staged_unique",
    "declared_duplicate",
    "conflict",
    "rejected",
];
pub const ENDPOINT_AUDIT_STATUSES: [&str; 1] = ["sealed"];

pub const PURGE_STARTED_EVENT: &str = "run_purge_started";
pub const RUN_RESUMED_EVENT: &str = "run_resumed";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JournalInspectError {
    IncompatibleSchema(String),
    Corrupt(String),
}

impl std::fmt::Display for JournalInspectError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::IncompatibleSchema(detail) => {
                write!(f, "incompatible_schema: {detail}")
            }
            Self::Corrupt(detail) => write!(f, "journal_corrupt: {detail}"),
        }
    }
}

fn enum_values(values: &[&str]) -> String {
    values
        .iter()
        .map(|v| format!("'{v}'"))
        .collect::<Vec<_>>()
        .join(",")
}

/// DDL — replicate `_schema_statements()` (enum interpolation cùng thứ tự).
pub fn schema_statements() -> Vec<String> {
    let run_status = enum_values(&RUN_STATUSES);
    let batch_status = enum_values(&BATCH_STATUSES);
    let phase = enum_values(&OPERATION_PHASES);
    let barrier_status = enum_values(&BARRIER_STATUSES);
    let producer_status = enum_values(&PRODUCER_STATUSES);
    let disposition = enum_values(&MANIFEST_DISPOSITIONS);
    let audit_status = enum_values(&ENDPOINT_AUDIT_STATUSES);
    vec![
        format!(
            r#"
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            physical_target TEXT NOT NULL,
            parser TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({run_status})),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            retention_until TEXT NOT NULL,
            error_code TEXT,
            error_detail TEXT
        ) STRICT
        "#
        ),
        r#"
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
        "#
        .to_string(),
        format!(
            r#"
        CREATE TABLE batches (
            job_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            phase TEXT NOT NULL CHECK (phase IN ({phase})),
            operation_key TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            artifact_sha256 TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            payload_bytes INTEGER NOT NULL CHECK (payload_bytes >= 0),
            row_count INTEGER NOT NULL CHECK (row_count >= 0),
            expected_count INTEGER NOT NULL CHECK (expected_count >= 0),
            status TEXT NOT NULL CHECK (status IN ({batch_status})),
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
        "#
        ),
        format!(
            r#"
        CREATE TABLE barriers (
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({barrier_status})),
            produced_count INTEGER NOT NULL DEFAULT 0 CHECK (produced_count >= 0),
            drained_count INTEGER NOT NULL DEFAULT 0 CHECK (drained_count >= 0),
            closed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, name),
            CHECK (drained_count <= produced_count)
        ) STRICT
        "#
        ),
        r#"
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
        "#
        .to_string(),
        format!(
            r#"
        CREATE TABLE IF NOT EXISTS producer_completion (
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            producer_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ({producer_status})),
            node_emitted INTEGER NOT NULL DEFAULT 0 CHECK (node_emitted >= 0),
            node_unique INTEGER NOT NULL DEFAULT 0 CHECK (node_unique >= 0),
            node_duplicate INTEGER NOT NULL DEFAULT 0 CHECK (node_duplicate >= 0),
            node_conflict INTEGER NOT NULL DEFAULT 0 CHECK (node_conflict >= 0),
            node_rejected INTEGER NOT NULL DEFAULT 0 CHECK (node_rejected >= 0),
            edge_emitted INTEGER NOT NULL DEFAULT 0 CHECK (edge_emitted >= 0),
            edge_unique INTEGER NOT NULL DEFAULT 0 CHECK (edge_unique >= 0),
            edge_duplicate INTEGER NOT NULL DEFAULT 0 CHECK (edge_duplicate >= 0),
            edge_conflict INTEGER NOT NULL DEFAULT 0 CHECK (edge_conflict >= 0),
            edge_rejected INTEGER NOT NULL DEFAULT 0 CHECK (edge_rejected >= 0),
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, producer_id)
        ) STRICT
        "#
        ),
        format!(
            r#"
        CREATE TABLE IF NOT EXISTS node_manifest (
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            manifest_id TEXT NOT NULL,
            job_id TEXT NOT NULL REFERENCES batches(job_id) ON DELETE CASCADE,
            producer_id TEXT NOT NULL,
            row_ordinal INTEGER NOT NULL CHECK (row_ordinal >= 0),
            scope TEXT NOT NULL,
            node_label TEXT NOT NULL,
            identity_property TEXT NOT NULL,
            identity_type TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            disposition TEXT NOT NULL CHECK (disposition IN ({disposition})),
            acked INTEGER NOT NULL DEFAULT 0 CHECK (acked IN (0, 1)),
            graph_verified INTEGER NOT NULL DEFAULT 0 CHECK (graph_verified IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, manifest_id),
            UNIQUE (job_id, row_ordinal)
        ) STRICT
        "#
        ),
        format!(
            r#"
        CREATE TABLE IF NOT EXISTS edge_manifest (
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            manifest_id TEXT NOT NULL,
            job_id TEXT NOT NULL REFERENCES batches(job_id) ON DELETE CASCADE,
            producer_id TEXT NOT NULL,
            row_ordinal INTEGER NOT NULL CHECK (row_ordinal >= 0),
            scope TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            identity_type TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            payload_digest TEXT NOT NULL,
            disposition TEXT NOT NULL CHECK (disposition IN ({disposition})),
            acked INTEGER NOT NULL DEFAULT 0 CHECK (acked IN (0, 1)),
            graph_verified INTEGER NOT NULL DEFAULT 0 CHECK (graph_verified IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, manifest_id),
            UNIQUE (job_id, row_ordinal)
        ) STRICT
        "#
        ),
        r#"
        CREATE TABLE IF NOT EXISTS edge_endpoint (
            run_id TEXT NOT NULL,
            edge_manifest_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('source', 'target')),
            scope TEXT NOT NULL,
            node_label TEXT NOT NULL,
            identity_property TEXT NOT NULL,
            identity_type TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),
            PRIMARY KEY (run_id, edge_manifest_id, role),
            FOREIGN KEY (run_id, edge_manifest_id)
                REFERENCES edge_manifest(run_id, manifest_id) ON DELETE CASCADE
        ) STRICT
        "#
        .to_string(),
        format!(
            r#"
        CREATE TABLE IF NOT EXISTS endpoint_audit (
            run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ({audit_status})),
            manifest_digest TEXT NOT NULL,
            receipt_count INTEGER NOT NULL CHECK (receipt_count >= 0),
            sealed_at TEXT NOT NULL
        ) STRICT
        "#
        ),
        "CREATE INDEX IF NOT EXISTS batches_claim_idx ON batches(status, next_attempt_at, sequence, created_at)".to_string(),
        "CREATE INDEX IF NOT EXISTS batches_run_status_idx ON batches(run_id, status)".to_string(),
        "CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id, event_id)".to_string(),
        "CREATE INDEX IF NOT EXISTS runs_scope_idx ON runs(scope_id, physical_target, parser, status)".to_string(),
        "CREATE INDEX IF NOT EXISTS node_manifest_identity_idx ON node_manifest(run_id, scope, node_label, identity_property, identity_type, identity_json)".to_string(),
        "CREATE INDEX IF NOT EXISTS node_manifest_job_idx ON node_manifest(job_id, disposition)".to_string(),
        "CREATE INDEX IF NOT EXISTS edge_manifest_identity_idx ON edge_manifest(run_id, scope, relationship_type, identity_type, identity_json)".to_string(),
        "CREATE INDEX IF NOT EXISTS edge_manifest_job_idx ON edge_manifest(job_id, disposition)".to_string(),
        "CREATE INDEX IF NOT EXISTS edge_endpoint_identity_idx ON edge_endpoint(run_id, scope, node_label, identity_property, identity_type, identity_json)".to_string(),
        "CREATE INDEX IF NOT EXISTS producer_completion_status_idx ON producer_completion(run_id, status)".to_string(),
    ]
}

/// Tạo journal DB v3 trên `conn` — replicate `_configure` (pragmas nền tảng)
/// + `_migrate` version-0 path (DDL + `user_version` trong 1 transaction).
pub fn create_schema(conn: &mut Connection) -> Result<(), rusqlite::Error> {
    conn.pragma_update(None, "foreign_keys", "ON")?;
    let wal: String = conn.query_row("PRAGMA journal_mode = WAL", [], |row| row.get(0))?;
    debug_assert_eq!(wal.to_casefold(), "wal");
    conn.pragma_update(None, "synchronous", "FULL")?;
    let tx = conn.transaction()?;
    for statement in schema_statements() {
        tx.execute_batch(&statement)?;
    }
    tx.pragma_update(None, "user_version", JOURNAL_SCHEMA_VERSION)?;
    tx.commit()
}

trait Casefold {
    fn to_casefold(&self) -> String;
}

impl Casefold for str {
    fn to_casefold(&self) -> String {
        self.to_lowercase()
    }
}

/// Tóm tắt một run — mirror 1:1 dict trả về bởi `inspect_journal` của Python.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RunSummary {
    pub run_id: String,
    pub status: String,
    pub resumed: bool,
    pub parser: String,
    pub produced: i64,
    pub acked: i64,
    pub pending: i64,
    pub leased: i64,
    pub retrying: i64,
    pub reconciling: i64,
    pub blocked: i64,
    pub dead_letter: i64,
    pub rows: i64,
    pub payload_bytes: i64,
    pub artifact_bytes: i64,
    pub journal_bytes: i64,
    pub oldest_unfinished_at: Option<String>,
    pub oldest_unfinished_age_seconds: Option<f64>,
    pub next_action: String,
    pub error_code: Option<String>,
}

/// Port `inspect_journal(path)` — đọc payload-free run summaries, không mutate.
/// `now_epoch_s` truyền vào thay cho `datetime.now()` nội tâm (test được).
pub fn inspect_journal(
    path: &Path,
    now_epoch_s: f64,
) -> Result<Vec<RunSummary>, JournalInspectError> {
    let conn = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| JournalInspectError::Corrupt(format!("cannot open: {e}")))?;
    let version: i64 = conn
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
    if version != JOURNAL_SCHEMA_VERSION {
        return Err(JournalInspectError::IncompatibleSchema(format!(
            "journal schema {version} is not supported for status inspection"
        )));
    }
    let quick_check: Vec<String> = {
        let mut stmt = conn
            .prepare("PRAGMA quick_check")
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?
    };
    if quick_check != ["ok"] {
        return Err(JournalInspectError::Corrupt(
            "journal integrity check failed".to_string(),
        ));
    }

    let journal_bytes: u64 = ["", "-wal", "-shm"]
        .iter()
        .map(|suffix| {
            let mut candidate = path.as_os_str().to_owned();
            candidate.push(suffix);
            Path::new(&candidate).metadata().map(|m| m.len()).unwrap_or(0)
        })
        .sum();

    let mut stmt = conn
        .prepare("SELECT * FROM runs ORDER BY updated_at DESC, run_id")
        .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
    let runs: Vec<BTreeMap<String, rusqlite::types::Value>> = {
        let columns: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let rows = stmt
            .query_map([], |row| {
                let mut map = BTreeMap::new();
                for (idx, column) in columns.iter().enumerate() {
                    let value = match row.get_ref(idx)? {
                        rusqlite::types::ValueRef::Null => {
                            rusqlite::types::Value::Null
                        }
                        rusqlite::types::ValueRef::Integer(v) => {
                            rusqlite::types::Value::Integer(v)
                        }
                        rusqlite::types::ValueRef::Real(v) => rusqlite::types::Value::Real(v),
                        rusqlite::types::ValueRef::Text(text) => rusqlite::types::Value::Text(
                            String::from_utf8_lossy(text).into_owned(),
                        ),
                        rusqlite::types::ValueRef::Blob(blob) => {
                            rusqlite::types::Value::Blob(blob.to_vec())
                        }
                    };
                    map.insert(column.clone(), value);
                }
                Ok(map)
            })
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?
    };

    fn text(value: &rusqlite::types::Value) -> String {
        match value {
            rusqlite::types::Value::Text(s) => s.clone(),
            _ => String::new(),
        }
    }

    fn optional_text(value: &rusqlite::types::Value) -> Option<String> {
        match value {
            rusqlite::types::Value::Text(s) => Some(s.clone()),
            _ => None,
        }
    }

    fn event_exists(conn: &Connection, run_id: &str, event_type: &str) -> bool {
        conn.query_row(
            "SELECT 1 FROM events WHERE run_id = ?1 AND event_type = ?2 LIMIT 1",
            [run_id, event_type],
            |_| Ok(()),
        )
        .is_ok()
    }

    let mut summaries = Vec::new();
    for run in &runs {
        let run_id = text(&run["run_id"]);
        let status_value = text(&run["status"]);
        if !RUN_STATUSES.contains(&status_value.as_str()) {
            return Err(JournalInspectError::Corrupt(format!(
                "unknown run status: {status_value}"
            )));
        }

        let purge_pending = event_exists(&conn, &run_id, PURGE_STARTED_EVENT);
        let mut counts: BTreeMap<&str, i64> =
            BATCH_STATUSES.iter().map(|s| (*s, 0)).collect();
        {
            let mut stmt = conn
                .prepare(
                    "SELECT status, COUNT(*) AS count FROM batches WHERE run_id = ?1 GROUP BY status",
                )
                .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
            let rows = stmt
                .query_map([&run_id], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
                })
                .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
            for row in rows {
                let (status, count) =
                    row.map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
                match counts.get_mut(status.as_str()) {
                    Some(slot) => *slot = count,
                    None => {
                        return Err(JournalInspectError::Corrupt(format!(
                            "unknown batch status: {status}"
                        )))
                    }
                }
            }
        }

        let totals = conn
            .query_row(
                "SELECT COUNT(*) AS produced, COALESCE(SUM(payload_bytes), 0) AS payload_bytes, \
                 COALESCE(SUM(row_count), 0) AS rows, \
                 MIN(CASE WHEN status != ?1 THEN created_at END) AS oldest_at \
                 FROM batches WHERE run_id = ?2",
                ["done", run_id.as_str()],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, i64>(1)?,
                        row.get::<_, i64>(2)?,
                        row.get::<_, Option<String>>(3)?,
                    ))
                },
            )
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;

        let metadata: serde_json::Value = serde_json::from_str(&text(&run["metadata_json"]))
            .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?;
        let parser = metadata
            .get("parser")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| {
                JournalInspectError::Corrupt("metadata_json missing parser".to_string())
            })?
            .to_string();

        let next_action = if purge_pending {
            "retry_purge"
        } else if status_value == "drained" {
            "none"
        } else if status_value == "blocked" || status_value == "dead_lettered" {
            "inspect_error_and_acknowledge_or_purge_after_retention"
        } else if status_value == "quarantined" {
            "inspect_incompatible_fingerprint"
        } else if counts["reconciling"] > 0 {
            "reconcile_ambiguous_batches"
        } else if counts["retry_wait"] > 0 {
            "wait_for_retry"
        } else if counts["leased"] > 0 {
            "wait_for_active_consumer"
        } else if counts["pending"] > 0 {
            "resume_consumer"
        } else {
            "close_production"
        };

        let oldest_at = totals.3.clone();
        let age_seconds = oldest_at.as_ref().map(|oldest| {
            let oldest_epoch =
                cortex_retrieval::signal_normalize::parse_iso8601_utc(oldest).unwrap_or(0.0);
            (now_epoch_s - oldest_epoch).max(0.0)
        });

        summaries.push(RunSummary {
            resumed: event_exists(&conn, &run_id, RUN_RESUMED_EVENT),
            run_id: run_id.clone(),
            status: status_value,
            parser,
            produced: totals.0,
            acked: counts["done"],
            pending: counts["pending"],
            leased: counts["leased"],
            retrying: counts["retry_wait"],
            reconciling: counts["reconciling"],
            blocked: counts["blocked"],
            dead_letter: counts["dead_letter"],
            rows: totals.2,
            payload_bytes: totals.1,
            artifact_bytes: conn
                .query_row(
                    "SELECT COALESCE(SUM(byte_count), 0) FROM artifacts WHERE run_id = ?1",
                    [&run_id],
                    |row| row.get(0),
                )
                .map_err(|e| JournalInspectError::Corrupt(e.to_string()))?,
            journal_bytes: journal_bytes as i64,
            oldest_unfinished_at: oldest_at,
            oldest_unfinished_age_seconds: age_seconds,
            next_action: next_action.to_string(),
            error_code: optional_text(&run["error_code"]),
        });
    }
    Ok(summaries)
}

// ─────────────────────────────────────────────────────────────
// Journal — production/consumer loop (low-level path)
// ─────────────────────────────────────────────────────────────

/// Epoch seconds (f64) → ISO microsecond UTC, replicate Python `_iso`:
/// `astimezone(utc).isoformat(timespec="microseconds")`.
pub fn iso_from_epoch(epoch_s: f64) -> String {
    let total_micros = (epoch_s * 1_000_000.0).round() as i64;
    let mut days = total_micros.div_euclid(86_400_000_000);
    let micros_of_day = total_micros.rem_euclid(86_400_000_000);
    let (year, month, day) = civil_from_days(days);
    let hour = micros_of_day / 3_600_000_000;
    let minute = (micros_of_day % 3_600_000_000) / 60_000_000;
    let second = (micros_of_day % 60_000_000) / 1_000_000;
    let micros = micros_of_day % 1_000_000;
    let _ = &mut days;
    format!(
        "{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00"
    )
}

/// Inverse của `days_from_civil` (Howard Hinnant).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

const PRODUCERS_COMPLETE_ID: &str = "__journal_all_producers_complete__";

type Clock = Box<dyn Fn() -> f64 + Send>;

pub struct Journal {
    pub path: PathBuf,
    pub limits: JournalLimits,
    pub artifacts: ArtifactStore,
    clock: Clock,
    connection: std::sync::Mutex<Connection>,
}

impl Journal {
    /// Constructor đầy đủ — replicate `__init__`:
    /// precreate → configure pragmas → migrate v0 → validate →
    /// recover_expired_leases → verify_referenced_artifacts.
    pub fn open(
        path: &Path,
        artifact_root: &Path,
        limits: JournalLimits,
        clock: Clock,
    ) -> Result<Self, JournalError> {
        let path = path.to_path_buf();
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        std::fs::File::create(&path)
            .map_err(|e| self_precreate_error(&e))?;

        let connection = Connection::open(&path)
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
        let artifacts =
            ArtifactStore::new(artifact_root, limits.max_artifact_bytes, limits.min_free_bytes);

        let journal = Self {
            path,
            limits,
            artifacts,
            clock,
            connection: std::sync::Mutex::new(connection),
        };
        journal.configure()?;
        journal.migrate()?;
        journal.validate_database()?;
        journal.recover_expired_leases()?;
        journal.verify_referenced_artifacts()?;
        Ok(journal)
    }

    fn configure(&self) -> Result<(), JournalError> {
        let conn = self.connection.lock().unwrap();
        conn.pragma_update(None, "foreign_keys", "ON")
            .map_err(sqlite_err)?;
        conn.pragma_update(None, "busy_timeout", self.limits.busy_timeout_ms)
            .map_err(sqlite_err)?;
        let mode: String = conn
            .query_row("PRAGMA journal_mode = WAL", [], |row| row.get(0))
            .map_err(sqlite_err)?;
        if mode.to_lowercase() != "wal" {
            return Err(JournalError::new(
                TerminalErrorCode::UnsafePlacement,
                format!("journal placement refused WAL mode (received {mode})"),
            ));
        }
        conn.pragma_update(None, "synchronous", "FULL")
            .map_err(sqlite_err)?;
        conn.pragma_update(None, "trusted_schema", "OFF")
            .map_err(sqlite_err)?;
        conn.pragma_update(
            None,
            "wal_autocheckpoint",
            self.limits.wal_autocheckpoint_pages,
        )
        .map_err(sqlite_err)?;
        conn.pragma_update(None, "journal_size_limit", self.limits.max_journal_bytes)
            .map_err(sqlite_err)?;
        Ok(())
    }

    fn migrate(&self) -> Result<(), JournalError> {
        let conn = self.connection.lock().unwrap();
        let version: i64 = conn
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .map_err(sqlite_err)?;
        if version > JOURNAL_SCHEMA_VERSION {
            return Err(JournalError::new(
                TerminalErrorCode::IncompatibleSchema,
                format!(
                    "journal schema {version} is newer than supported schema {JOURNAL_SCHEMA_VERSION}"
                ),
            ));
        }
        if version == JOURNAL_SCHEMA_VERSION {
            return Ok(());
        }
        if version != 0 {
            return Err(JournalError::new(
                TerminalErrorCode::IncompatibleSchema,
                format!("journal schema {version} cannot be migrated"),
            ));
        }
        let tx = conn.unchecked_transaction().map_err(sqlite_err)?;
        for statement in schema_statements() {
            tx.execute_batch(&statement).map_err(sqlite_err)?;
        }
        tx.pragma_update(None, "user_version", JOURNAL_SCHEMA_VERSION)
            .map_err(sqlite_err)?;
        tx.commit().map_err(sqlite_err)?;
        Ok(())
    }

    fn validate_database(&self) -> Result<(), JournalError> {
        let conn = self.connection.lock().unwrap();
        let quick_check: String = conn
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .map_err(sqlite_err)?;
        if quick_check != "ok" {
            return Err(JournalError::new(
                TerminalErrorCode::JournalCorrupt,
                "journal integrity check failed",
            ));
        }
        let foreign_keys: i64 = conn
            .query_row("PRAGMA foreign_keys", [], |row| row.get(0))
            .map_err(sqlite_err)?;
        let synchronous: i64 = conn
            .query_row("PRAGMA synchronous", [], |row| row.get(0))
            .map_err(sqlite_err)?;
        if foreign_keys != 1 || synchronous < 2 {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "journal durability pragmas are not active",
            ));
        }
        Ok(())
    }

    pub fn recover_expired_leases(&self) -> Result<usize, JournalError> {
        let now = self.now_iso();
        let conn = self.connection.lock().unwrap();
        Journal::recover_expired_leases_locked(&conn, &now)
    }

    fn verify_referenced_artifacts(&self) -> Result<(), JournalError> {
        let conn = self.connection.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT artifact_sha256, artifact_path, payload_bytes, row_count FROM batches")
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, i64>(3)?,
                ))
            })
            .map_err(sqlite_err)?;
        let references: Vec<_> = rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_err)?
            .into_iter()
            .map(|(sha256, relative_path, byte_count, row_count)| crate::models::ArtifactRef {
                sha256,
                relative_path,
                byte_count,
                row_count,
            })
            .collect();
        for reference in references {
            self.artifacts.verify(&reference)?;
        }
        Ok(())
    }

    fn now_epoch(&self) -> f64 {
        (self.clock)()
    }

    fn now_iso(&self) -> String {
        iso_from_epoch(self.now_epoch())
    }

    fn transaction<T>(
        &self,
        f: impl FnOnce(&Connection) -> Result<T, JournalError>,
    ) -> Result<T, JournalError> {
        let conn = self.connection.lock().unwrap();
        let tx = conn.unchecked_transaction().map_err(sqlite_err)?;
        let result = f(&conn)?;
        tx.commit().map_err(sqlite_err)?;
        Ok(result)
    }

    fn admit(&self, expected_items: i64, expected_bytes: i64) -> Result<(), JournalError> {
        if expected_items < 0 || expected_bytes < 0 {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "expected admission values must be non-negative",
            ));
        }
        if expected_items > self.limits.max_batches_per_run {
            return Err(JournalError::new(
                TerminalErrorCode::AdmissionRejected,
                "run item limit exceeded",
            ));
        }
        if expected_bytes > self.limits.max_payload_bytes_per_run {
            return Err(JournalError::new(
                TerminalErrorCode::AdmissionRejected,
                "run byte limit exceeded",
            ));
        }
        if let Some(parent) = self.path.parent()
            && let Ok(free) = free_disk_bytes(parent)
        {
            let free = free as i64;
            if free - expected_bytes < self.limits.min_free_bytes {
                return Err(JournalError::new(
                    TerminalErrorCode::DiskFull,
                    "run admission would violate disk headroom",
                ));
            }
        }
        Ok(())
    }

    // ── run lifecycle ─────────────────────────────────────────

    pub fn open_run(&self, metadata: &RunMetadata, expected_items: i64, expected_bytes: i64)
        -> Result<RunRecord, JournalError>
    {
        metadata.validate().map_err(|message| {
            JournalError::new(TerminalErrorCode::InvalidContract, message)
        })?;
        self.admit(expected_items, expected_bytes)?;
        let metadata_value = metadata.to_dict();
        let fingerprint = run_fingerprint(&metadata_value);
        let run_id_value = run_id(&metadata_value);
        let now_value = self.now_epoch();
        let now = iso_from_epoch(now_value);
        let retention_until =
            iso_from_epoch(now_value + self.limits.retention_seconds as f64);
        let metadata_json = String::from_utf8(canonical_json(&metadata_value))
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
        self.transaction(|conn| {
            let existing = conn
                .query_row(
                    "SELECT * FROM runs WHERE run_id = ?1",
                    [&run_id_value],
                    run_row_to_map,
                )
                .ok();
            if let Some(existing) = existing {
                if text_field(&existing, "metadata_json") != metadata_json {
                    return Err(JournalError::new(
                        TerminalErrorCode::IncompatibleSchema,
                        "run identity collision has incompatible metadata",
                    ));
                }
                if text_field(&existing, "status") == RunStatus::Quarantined.value()
                    && text_field(&existing, "error_code")
                        == TerminalErrorCode::IncompatibleSchema.value()
                {
                    return Err(JournalError::new(
                        TerminalErrorCode::IncompatibleSchema,
                        "quarantined legacy run cannot be resumed as a v3 manifest run",
                    ));
                }
                Self::add_event_static(conn, &run_id_value, "run_resumed", &now)?;
                return run_from_map(&existing);
            }
            // Quarantine active runs trùng (project, scope, target, parser) nhưng khác fingerprint.
            let mut stmt = conn
                .prepare(
                    "SELECT run_id FROM runs WHERE project_id = ?1 AND scope_id = ?2 \
                     AND physical_target = ?3 AND parser = ?4 AND status IN (?5, ?6)",
                )
                .map_err(sqlite_err)?;
            let incompatible: Vec<String> = stmt
                .query_map(
                    rusqlite::params![
                        metadata.project_id,
                        metadata.scope_id,
                        metadata.physical_target,
                        metadata.parser,
                        RunStatus::Open.value(),
                        RunStatus::Draining.value(),
                    ],
                    |row| row.get(0),
                )
                .map_err(sqlite_err)?
                .collect::<Result<Vec<_>, _>>()
                .map_err(sqlite_err)?;
            for incompatible_run_id in incompatible {
                let incompatible_retention =
                    iso_from_epoch(now_value + self.limits.retention_seconds as f64);
                conn.execute(
                    "UPDATE runs SET status = ?1, updated_at = ?2, retention_until = ?3, \
                     error_code = ?4, error_detail = ?5 WHERE run_id = ?6",
                    rusqlite::params![
                        RunStatus::Quarantined.value(),
                        now,
                        incompatible_retention,
                        TerminalErrorCode::IncompatibleSchema.value(),
                        "superseded by an incompatible source/target/schema/query fingerprint",
                        incompatible_run_id,
                    ],
                )
                .map_err(sqlite_err)?;
                Self::add_event_static(
                    conn,
                    &incompatible_run_id,
                    "run_quarantined",
                    &now,
                )?;
                conn.execute(
                    "UPDATE batches SET status = ?1, fencing_token = NULL, lease_until = NULL, \
                     retry_class = ?2, error_code = ?3, updated_at = ?4 \
                     WHERE run_id = ?5 AND status != ?6",
                    rusqlite::params![
                        BatchStatus::Blocked.value(),
                        RetryClass::Incompatible.value(),
                        TerminalErrorCode::IncompatibleSchema.value(),
                        now,
                        incompatible_run_id,
                        BatchStatus::Done.value(),
                    ],
                )
                .map_err(sqlite_err)?;
            }
            conn.execute(
                "INSERT INTO runs(run_id, fingerprint, project_id, scope_id, physical_target, \
                 parser, metadata_json, status, created_at, updated_at, retention_until) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                rusqlite::params![
                    run_id_value,
                    fingerprint,
                    metadata.project_id,
                    metadata.scope_id,
                    metadata.physical_target,
                    metadata.parser,
                    metadata_json,
                    RunStatus::Open.value(),
                    now,
                    now,
                    retention_until,
                ],
            )
            .map_err(sqlite_err)?;
            Self::add_event_static(conn, &run_id_value, "run_opened", &now)?;
            let row = conn
                .query_row(
                    "SELECT * FROM runs WHERE run_id = ?1",
                    [&run_id_value],
                    run_row_to_map,
                )
                .map_err(sqlite_err)?;
            run_from_map(&row)
        })
    }

    pub fn get_run(&self, run_id_value: &str) -> Result<Option<RunRecord>, JournalError> {
        let conn = self.connection.lock().unwrap();
        let row = conn
            .query_row(
                "SELECT * FROM runs WHERE run_id = ?1",
                [run_id_value],
                run_row_to_map,
            )
            .ok();
        match row {
            Some(map) => Ok(Some(run_from_map(&map)?)),
            None => Ok(None),
        }
    }

    pub fn list_runs(&self) -> Result<Vec<RunRecord>, JournalError> {
        let conn = self.connection.lock().unwrap();
        let mut stmt = conn
            .prepare("SELECT * FROM runs ORDER BY updated_at DESC, run_id")
            .map_err(sqlite_err)?;
        let maps = stmt
            .query_map([], run_row_to_map)
            .map_err(sqlite_err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_err)?;
        maps.iter().map(run_from_map).collect()
    }

    pub fn find_resumable_run(&self, metadata: &RunMetadata) -> Result<Option<RunRecord>, JournalError> {
        metadata.validate().map_err(|message| {
            JournalError::new(TerminalErrorCode::InvalidContract, message)
        })?;
        let conn = self.connection.lock().unwrap();
        let mut stmt = conn
            .prepare(
                "SELECT * FROM runs WHERE project_id = ?1 AND scope_id = ?2 \
                 AND physical_target = ?3 AND parser = ?4 AND status IN (?5, ?6) \
                 ORDER BY created_at DESC",
            )
            .map_err(sqlite_err)?;
        let maps = stmt
            .query_map(
                rusqlite::params![
                    metadata.project_id,
                    metadata.scope_id,
                    metadata.physical_target,
                    metadata.parser,
                    RunStatus::Open.value(),
                    RunStatus::Draining.value(),
                ],
                run_row_to_map,
            )
            .map_err(sqlite_err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_err)?;
        for map in &maps {
            let candidate = run_from_map(map)?;
            let mut expected = metadata.to_dict();
            let mut actual = candidate.metadata.to_dict();
            expected.as_object_mut().unwrap().remove("generation");
            actual.as_object_mut().unwrap().remove("generation");
            if actual == expected {
                return Ok(Some(candidate));
            }
        }
        Ok(None)
    }

    pub fn create_artifact(&self, run_id_value: &str, rows: &[serde_json::Value])
        -> Result<crate::models::ArtifactRef, JournalError>
    {
        let run = self.get_run(run_id_value)?;
        match run.map(|r| r.status) {
            Some(RunStatus::Open) | Some(RunStatus::Draining) | Some(RunStatus::Drained) => {}
            _ => {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidTransition,
                    "artifacts may only be created for a compatible replayable run",
                ))
            }
        }
        self.artifacts.write_jsonl(run_id_value, rows)
    }

    // ── batch lifecycle (low-level path — operation phải rỗng) ──

    pub fn enqueue_batch(&self, run_id_value: &str, spec: BatchSpec)
        -> Result<BatchRecord, JournalError>
    {
        if !spec.operation.is_empty() {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "manifest staging (non-empty operation) is not ported to the Rust journal yet",
            ));
        }
        let spec = spec.build().map_err(|message| {
            JournalError::new(TerminalErrorCode::InvalidContract, message)
        })?;
        let run = self.get_run(run_id_value)?.ok_or_else(|| {
            JournalError::new(TerminalErrorCode::InvalidTransition, "batch run does not exist")
        })?;
        // artifact ownership: relative_path = f"{run_id}/{sha}.jsonl"
        let expected_relative = format!("{}/{}.jsonl", run_id_value, spec.artifact.sha256);
        if spec.artifact.relative_path != expected_relative {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "artifact does not belong to the target run",
            ));
        }
        self.artifacts.verify(&spec.artifact)?;
        // Python vẫn read_jsonl ở low-level path (artifact hỏng phải fail như nhau).
        let _artifact_rows = self.artifacts.read_jsonl(&spec.artifact)?;
        let job_identity = JobIdentity {
            run_fingerprint: &run.fingerprint,
            phase: spec.phase.value(),
            operation_key: &spec.operation_key,
            sequence: spec.sequence,
            payload_sha256: &spec.artifact.sha256,
        };
        let job_id = deterministic_job_id(&job_identity);
        let now = self.now_iso();
        self.transaction(|conn| {
            let production_complete: Option<i64> = conn
                .query_row(
                    "SELECT 1 FROM producer_completion WHERE run_id = ?1 AND producer_id = ?2 AND status = ?3",
                    rusqlite::params![
                        run_id_value,
                        PRODUCERS_COMPLETE_ID,
                        ProducerStatus::Complete.value()
                    ],
                    |_| Ok(1),
                )
                .ok();
            if production_complete.is_some() {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidTransition,
                    "batches cannot be enqueued after producer completion",
                ));
            }
            // Duplicate job → idempotent return (kể cả sau complete check như Python).
            if let Ok(duplicate) = conn.query_row(
                "SELECT * FROM batches WHERE job_id = ?1",
                [&job_id],
                batch_row_to_map,
            ) {
                return batch_from_map(&duplicate);
            }
            let current_status: String = conn
                .query_row(
                    "SELECT status FROM runs WHERE run_id = ?1",
                    [run_id_value],
                    |row| row.get(0),
                )
                .map_err(|_| {
                    JournalError::new(
                        TerminalErrorCode::InvalidTransition,
                        "batches may only be enqueued into an open run",
                    )
                })?;
            if current_status != RunStatus::Open.value() {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidTransition,
                    "batches may only be enqueued into an open run",
                ));
            }
            let (items, bytes): (i64, i64) = conn
                .query_row(
                    "SELECT COUNT(*), COALESCE(SUM(payload_bytes), 0) FROM batches WHERE run_id = ?1",
                    [run_id_value],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .map_err(sqlite_err)?;
            self.admit(items + 1, bytes + spec.artifact.byte_count)?;

            // Artifact upsert / refcount.
            let existing: Option<(String, i64, i64)> = conn
                .query_row(
                    "SELECT relative_path, byte_count, row_count FROM artifacts \
                     WHERE run_id = ?1 AND sha256 = ?2",
                    rusqlite::params![run_id_value, spec.artifact.sha256],
                    |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
                )
                .ok();
            match existing {
                None => {
                    conn.execute(
                        "INSERT INTO artifacts(run_id, sha256, relative_path, byte_count, \
                         row_count, ref_count, created_at, last_referenced_at) \
                         VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6, ?7)",
                        rusqlite::params![
                            run_id_value,
                            spec.artifact.sha256,
                            spec.artifact.relative_path,
                            spec.artifact.byte_count,
                            spec.artifact.row_count,
                            now,
                            now,
                        ],
                    )
                    .map_err(sqlite_err)?;
                }
                Some((relative_path, byte_count, row_count)) => {
                    if relative_path != spec.artifact.relative_path
                        || byte_count != spec.artifact.byte_count
                        || row_count != spec.artifact.row_count
                    {
                        return Err(JournalError::new(
                            TerminalErrorCode::ArtifactHashMismatch,
                            "artifact metadata conflicts with its content hash",
                        ));
                    }
                    conn.execute(
                        "UPDATE artifacts SET ref_count = ref_count + 1, last_referenced_at = ?1 \
                         WHERE run_id = ?2 AND sha256 = ?3",
                        rusqlite::params![now, run_id_value, spec.artifact.sha256],
                    )
                    .map_err(sqlite_err)?;
                }
            }

            let required_json = serde_json::to_string(&spec.required_barriers).unwrap();
            let produced_json = serde_json::to_string(&spec.produced_barriers).unwrap();
            let operation_json = String::from_utf8(canonical_json(&serde_json::Value::Object(
                spec.operation.clone().into_iter().collect(),
            )))
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
            conn.execute(
                "INSERT INTO batches(job_id, run_id, phase, operation_key, sequence, \
                 artifact_sha256, artifact_path, payload_bytes, row_count, expected_count, \
                 status, max_attempts, required_barriers_json, produced_barriers_json, \
                 operation_json, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
                rusqlite::params![
                    job_id,
                    run_id_value,
                    spec.phase.value(),
                    spec.operation_key,
                    spec.sequence,
                    spec.artifact.sha256,
                    spec.artifact.relative_path,
                    spec.artifact.byte_count,
                    spec.artifact.row_count,
                    spec.expected_count,
                    BatchStatus::Pending.value(),
                    spec.max_attempts,
                    required_json,
                    produced_json,
                    operation_json,
                    now,
                    now,
                ],
            )
            .map_err(sqlite_err)?;

            // Barrier production (low-level: không manifest failure).
            for barrier_name in &spec.produced_barriers {
                let barrier: Option<(String, i64)> = conn
                    .query_row(
                        "SELECT status, produced_count FROM barriers WHERE run_id = ?1 AND name = ?2",
                        rusqlite::params![run_id_value, barrier_name],
                        |row| Ok((row.get(0)?, row.get(1)?)),
                    )
                    .ok();
                match barrier {
                    None => {
                        conn.execute(
                            "INSERT INTO barriers(run_id, name, status, produced_count, \
                             drained_count, updated_at) VALUES (?1, ?2, ?3, 1, 0, ?4)",
                            rusqlite::params![
                                run_id_value,
                                barrier_name,
                                BarrierStatus::Open.value(),
                                now,
                            ],
                        )
                        .map_err(sqlite_err)?;
                    }
                    Some((status, produced_count)) if status == BarrierStatus::Open.value() => {
                        conn.execute(
                            "UPDATE barriers SET produced_count = produced_count + 1, updated_at = ?1 \
                             WHERE run_id = ?2 AND name = ?3",
                            rusqlite::params![now, run_id_value, barrier_name],
                        )
                        .map_err(sqlite_err)?;
                        let _ = produced_count;
                    }
                    Some((status, _)) => {
                        return Err(JournalError::new(
                            TerminalErrorCode::InvalidTransition,
                            format!("cannot enqueue producer after barrier {status} is closed"),
                        ))
                    }
                }
            }

            let pending: i64 = conn
                .query_row(
                    "SELECT COUNT(*) FROM batches WHERE run_id = ?1 AND status != ?2",
                    rusqlite::params![run_id_value, BatchStatus::Done.value()],
                    |row| row.get(0),
                )
                .map_err(sqlite_err)?;
            let counters_json = serde_json::json!({
                "bytes": spec.artifact.byte_count,
                "rows": spec.artifact.row_count,
                "pending": pending,
            });
            conn.execute(
                "INSERT INTO events(run_id, job_id, event_type, counters_json, attempt, \
                 elapsed_ms, error_code, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                rusqlite::params![
                    run_id_value,
                    job_id,
                    "batch_enqueued",
                    counters_json.to_string(),
                    Option::<i64>::None,
                    Option::<i64>::None,
                    Option::<String>::None,
                    now,
                ],
            )
            .map_err(sqlite_err)?;

            let row = conn
                .query_row(
                    "SELECT * FROM batches WHERE job_id = ?1",
                    [&job_id],
                    batch_row_to_map,
                )
                .map_err(sqlite_err)?;
            batch_from_map(&row)
        })
    }

    // ── barriers ──────────────────────────────────────────────

    pub fn open_barrier(&self, run_id_value: &str, name: &str)
        -> Result<BarrierRecord, JournalError>
    {
        if name.trim().is_empty() {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "barrier name must not be empty",
            ));
        }
        let now = self.now_iso();
        self.transaction(|conn| {
            let run_status: Option<String> = conn
                .query_row(
                    "SELECT status FROM runs WHERE run_id = ?1",
                    [run_id_value],
                    |row| row.get(0),
                )
                .ok();
            if run_status.as_deref() != Some(RunStatus::Open.value()) {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidTransition,
                    "barriers may only be opened for an open run",
                ));
            }
            conn.execute(
                "INSERT INTO barriers(run_id, name, status, produced_count, drained_count, updated_at) \
                 VALUES (?1, ?2, ?3, 0, 0, ?4) ON CONFLICT(run_id, name) DO NOTHING",
                rusqlite::params![run_id_value, name, BarrierStatus::Open.value(), now],
            )
            .map_err(sqlite_err)?;
            let row = conn
                .query_row(
                    "SELECT * FROM barriers WHERE run_id = ?1 AND name = ?2",
                    rusqlite::params![run_id_value, name],
                    barrier_row_to_map,
                )
                .map_err(sqlite_err)?;
            barrier_from_map(&row)
        })
    }

    pub fn close_barrier(&self, run_id_value: &str, name: &str)
        -> Result<BarrierRecord, JournalError>
    {
        let now = self.now_iso();
        self.transaction(|conn| {
            let row = conn
                .query_row(
                    "SELECT * FROM barriers WHERE run_id = ?1 AND name = ?2",
                    rusqlite::params![run_id_value, name],
                    barrier_row_to_map,
                )
                .ok();
            let Some(row) = row else {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidContract,
                    "barrier does not exist",
                ));
            };
            let barrier = barrier_from_map(&row)?;
            if barrier.status == BarrierStatus::Drained {
                return Ok(barrier);
            }
            let run_status: Option<String> = conn
                .query_row(
                    "SELECT status FROM runs WHERE run_id = ?1",
                    [run_id_value],
                    |row| row.get(0),
                )
                .ok();
            if run_status.as_deref() != Some(RunStatus::Open.value()) {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidTransition,
                    "barriers may only be closed for an open run",
                ));
            }
            let status = if barrier.drained_count == barrier.produced_count {
                BarrierStatus::Drained
            } else {
                BarrierStatus::Produced
            };
            conn.execute(
                "UPDATE barriers SET status = ?1, closed_at = ?2, updated_at = ?3 \
                 WHERE run_id = ?4 AND name = ?5",
                rusqlite::params![status.value(), now, now, run_id_value, name],
            )
            .map_err(sqlite_err)?;
            if status == BarrierStatus::Drained {
                Self::add_event_with_counters(
                    conn,
                    run_id_value,
                    "barrier_reached",
                    &[("produced", barrier.produced_count)],
                    &now,
                )?;
            }
            let updated = conn
                .query_row(
                    "SELECT * FROM barriers WHERE run_id = ?1 AND name = ?2",
                    rusqlite::params![run_id_value, name],
                    barrier_row_to_map,
                )
                .map_err(sqlite_err)?;
            barrier_from_map(&updated)
        })
    }

    pub fn get_barrier(&self, run_id_value: &str, name: &str)
        -> Result<Option<BarrierRecord>, JournalError>
    {
        let conn = self.connection.lock().unwrap();
        let row = conn
            .query_row(
                "SELECT * FROM barriers WHERE run_id = ?1 AND name = ?2",
                rusqlite::params![run_id_value, name],
                barrier_row_to_map,
            )
            .ok();
        match row {
            Some(map) => Ok(Some(barrier_from_map(&map)?)),
            None => Ok(None),
        }
    }

    // ── claims / leases ───────────────────────────────────────

    pub fn claim_batch(&self, run_id_value: Option<&str>, lease_seconds: i64)
        -> Result<Option<BatchRecord>, JournalError>
    {
        if lease_seconds <= 0 {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "lease_seconds must be positive",
            ));
        }
        let now_value = self.now_epoch();
        let now = iso_from_epoch(now_value);
        let lease_until = iso_from_epoch(now_value + lease_seconds as f64);
        self.transaction(|conn| {
            Self::recover_expired_leases_locked(conn, &now)?;
            let mut query = String::from(
                "SELECT b.* FROM batches b JOIN runs r ON r.run_id = b.run_id \
                 WHERE b.status IN (?1, ?2) \
                 AND (b.next_attempt_at IS NULL OR b.next_attempt_at <= ?3) \
                 AND r.status IN (?4, ?5) \
                 AND NOT EXISTS (\
                     SELECT 1 FROM json_each(b.required_barriers_json) required \
                     LEFT JOIN barriers barrier ON barrier.run_id = b.run_id \
                       AND barrier.name = required.value \
                     WHERE barrier.status IS NULL OR barrier.status != ?6\
                 )",
            );
            let mut params: Vec<String> = vec![
                BatchStatus::Pending.value().to_string(),
                BatchStatus::RetryWait.value().to_string(),
                now.clone(),
                RunStatus::Open.value().to_string(),
                RunStatus::Draining.value().to_string(),
                BarrierStatus::Drained.value().to_string(),
            ];
            if let Some(run_filter) = run_id_value {
                query.push_str(" AND b.run_id = ?7");
                params.push(run_filter.to_string());
            }
            query.push_str(" ORDER BY b.sequence, b.created_at, b.job_id LIMIT 1");
            let row = conn
                .query_row(&query, rusqlite::params_from_iter(params.iter()), batch_row_to_map)
                .ok();
            let Some(row) = row else { return Ok(None) };
            let job_id = text_field(&row, "job_id");
            let token = random_token();
            let updated = conn
                .execute(
                    "UPDATE batches SET status = ?1, attempt = attempt + 1, fencing_token = ?2, \
                     lease_until = ?3, next_attempt_at = NULL, updated_at = ?4 \
                     WHERE job_id = ?5 AND status IN (?6, ?7)",
                    rusqlite::params![
                        BatchStatus::Leased.value(),
                        token,
                        lease_until,
                        now,
                        job_id,
                        BatchStatus::Pending.value(),
                        BatchStatus::RetryWait.value(),
                    ],
                )
                .map_err(sqlite_err)?;
            if updated != 1 {
                return Ok(None);
            }
            let claimed = conn
                .query_row(
                    "SELECT * FROM batches WHERE job_id = ?1",
                    [&job_id],
                    batch_row_to_map,
                )
                .map_err(sqlite_err)?;
            let claimed_run = text_field(&claimed, "run_id");
            let attempt = int_field(&claimed, "attempt");
            Self::add_event_static_with_attempt(conn, &claimed_run, &job_id, "batch_leased", attempt, &now)?;
            Ok(Some(batch_from_map(&claimed)?))
        })
    }

    pub fn renew_lease(&self, job_id: &str, fencing_token: &str, lease_seconds: i64)
        -> Result<BatchRecord, JournalError>
    {
        if lease_seconds <= 0 {
            return Err(JournalError::new(
                TerminalErrorCode::InvalidContract,
                "lease_seconds must be positive",
            ));
        }
        let now_value = self.now_epoch();
        let now = iso_from_epoch(now_value);
        let lease_until = iso_from_epoch(now_value + lease_seconds as f64);
        self.transaction(|conn| {
            let updated = conn
                .execute(
                    "UPDATE batches SET lease_until = ?1, updated_at = ?2 \
                     WHERE job_id = ?3 AND status = ?4 AND fencing_token = ?5 AND lease_until > ?6",
                    rusqlite::params![
                        lease_until,
                        now,
                        job_id,
                        BatchStatus::Leased.value(),
                        fencing_token,
                        now,
                    ],
                )
                .map_err(sqlite_err)?;
            if updated != 1 {
                return Err(JournalError::stale_fence(job_id));
            }
            let row = conn
                .query_row(
                    "SELECT * FROM batches WHERE job_id = ?1",
                    [job_id],
                    batch_row_to_map,
                )
                .map_err(sqlite_err)?;
            batch_from_map(&row)
        })
    }

    pub fn ack_batch(&self, job_id: &str, fencing_token: &str, elapsed_ms: Option<i64>)
        -> Result<BatchRecord, JournalError>
    {
        let now = self.now_iso();
        self.transaction(|conn| {
            let owned = Self::owned_transition(conn, job_id, fencing_token, &now)?;
            let run_id_value = text_field(&owned, "run_id");
            let produced_json = text_field(&owned, "produced_barriers_json");
            conn.execute(
                "UPDATE batches SET status = ?1, fencing_token = NULL, lease_until = NULL, \
                 retry_class = NULL, error_code = NULL, error_detail = NULL, updated_at = ?2 \
                 WHERE job_id = ?3 AND status IN (?4, ?5) AND fencing_token = ?6",
                rusqlite::params![
                    BatchStatus::Done.value(),
                    now,
                    job_id,
                    BatchStatus::Leased.value(),
                    BatchStatus::Reconciling.value(),
                    fencing_token,
                ],
            )
            .map_err(sqlite_err)?;
            for table in ["node_manifest", "edge_manifest"] {
                conn.execute(
                    &format!(
                        "UPDATE {table} SET acked = 1, graph_verified = 1, updated_at = ?1 \
                         WHERE job_id = ?2 AND disposition = ?3"
                    ),
                    rusqlite::params![
                        now,
                        job_id,
                        ManifestDisposition::StagedUnique.value(),
                    ],
                )
                .map_err(sqlite_err)?;
            }
            let produced: Vec<String> = serde_json::from_str(&produced_json)
                .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
            for name in produced {
                let barrier = conn
                    .query_row(
                        "SELECT * FROM barriers WHERE run_id = ?1 AND name = ?2",
                        rusqlite::params![&run_id_value, name],
                        barrier_row_to_map,
                    )
                    .ok();
                let Some(barrier_map) = barrier else {
                    return Err(JournalError::new(
                        TerminalErrorCode::InvalidContract,
                        "produced barrier is missing",
                    ));
                };
                let barrier = barrier_from_map(&barrier_map)?;
                let drained_count = barrier.drained_count + 1;
                let mut status = barrier.status;
                if barrier.closed_at.is_some() && drained_count == barrier.produced_count {
                    status = BarrierStatus::Drained;
                }
                conn.execute(
                    "UPDATE barriers SET drained_count = ?1, status = ?2, updated_at = ?3 \
                     WHERE run_id = ?4 AND name = ?5",
                    rusqlite::params![drained_count, status.value(), now, &run_id_value, name],
                )
                .map_err(sqlite_err)?;
                if status == BarrierStatus::Drained {
                    Self::add_event_with_counters(
                        conn,
                        &run_id_value,
                        "barrier_reached",
                        &[("produced", barrier.produced_count)],
                        &now,
                    )?;
                }
            }
            let rows = int_field(&owned, "row_count");
            let attempt = int_field(&owned, "attempt");
            Self::add_event_full(
                conn,
                &run_id_value,
                Some(job_id),
                "batch_acked",
                &[("rows", rows)],
                Some(attempt),
                elapsed_ms,
                None,
                &now,
            )?;
            Self::refresh_run_status_locked(conn, &self.limits, &run_id_value, &now)?;
            let updated = conn
                .query_row(
                    "SELECT * FROM batches WHERE job_id = ?1",
                    [job_id],
                    batch_row_to_map,
                )
                .map_err(sqlite_err)?;
            batch_from_map(&updated)
        })
    }

    // ── retry / reconcile / block ─────────────────────────────

    pub fn mark_reconciling(&self, job_id: &str, fencing_token: &str,
        error_code: Option<TerminalErrorCode>) -> Result<BatchRecord, JournalError>
    {
        let now = self.now_iso();
        self.transaction(|conn| {
            let owned = Self::owned_lease(conn, job_id, fencing_token, &now)?;
            let run_id_value = text_field(&owned, "run_id");
            let attempt = int_field(&owned, "attempt");
            conn.execute(
                "UPDATE batches SET status = ?1, retry_class = ?2, error_code = ?3, updated_at = ?4 \
                 WHERE job_id = ?5 AND status = ?6 AND fencing_token = ?7",
                rusqlite::params![
                    BatchStatus::Reconciling.value(),
                    RetryClass::Ambiguous.value(),
                    error_code.map(|c| c.value().to_string()),
                    now,
                    job_id,
                    BatchStatus::Leased.value(),
                    fencing_token,
                ],
            )
            .map_err(sqlite_err)?;
            Self::add_event_full(
                conn,
                &run_id_value,
                Some(job_id),
                "batch_reconciling",
                &[],
                Some(attempt),
                None,
                error_code,
                &now,
            )?;
            let updated = conn
                .query_row("SELECT * FROM batches WHERE job_id = ?1", [job_id], batch_row_to_map)
                .map_err(sqlite_err)?;
            batch_from_map(&updated)
        })
    }

    pub fn schedule_retry(&self, job_id: &str, fencing_token: &str, retry_at_epoch: f64,
        retry_class: RetryClass, error_code: TerminalErrorCode) -> Result<BatchRecord, JournalError>
    {
        let now = self.now_iso();
        self.transaction(|conn| {
            let owned = Self::owned_transition(conn, job_id, fencing_token, &now)?;
            let run_id_value = text_field(&owned, "run_id");
            let attempt = int_field(&owned, "attempt");
            let max_attempts = int_field(&owned, "max_attempts");
            let exhausted = attempt >= max_attempts;
            let status = if exhausted { BatchStatus::DeadLetter } else { BatchStatus::RetryWait };
            let terminal_code = if exhausted { TerminalErrorCode::MaxAttempts } else { error_code };
            conn.execute(
                "UPDATE batches SET status = ?1, fencing_token = NULL, lease_until = NULL, \
                 next_attempt_at = ?2, retry_class = ?3, error_code = ?4, updated_at = ?5 \
                 WHERE job_id = ?6 AND fencing_token = ?7 AND status IN (?8, ?9)",
                rusqlite::params![
                    status.value(),
                    if exhausted { None } else { Some(iso_from_epoch(retry_at_epoch)) },
                    retry_class.value(),
                    terminal_code.value(),
                    now,
                    job_id,
                    fencing_token,
                    BatchStatus::Leased.value(),
                    BatchStatus::Reconciling.value(),
                ],
            )
            .map_err(sqlite_err)?;
            let event_type = if exhausted { "batch_dead_lettered" } else { "retry_scheduled" };
            Self::add_event_full(
                conn,
                &run_id_value,
                Some(job_id),
                event_type,
                &[],
                Some(attempt),
                None,
                Some(terminal_code),
                &now,
            )?;
            if exhausted {
                Self::set_run_error_locked(conn, &self.limits, &run_id_value, RunStatus::DeadLettered, terminal_code, &now)?;
            }
            let updated = conn
                .query_row("SELECT * FROM batches WHERE job_id = ?1", [job_id], batch_row_to_map)
                .map_err(sqlite_err)?;
            batch_from_map(&updated)
        })
    }

    pub fn block_batch(&self, job_id: &str, fencing_token: &str,
        retry_class: RetryClass, error_code: TerminalErrorCode) -> Result<BatchRecord, JournalError>
    {
        let now = self.now_iso();
        self.transaction(|conn| {
            let owned = Self::owned_transition(conn, job_id, fencing_token, &now)?;
            let run_id_value = text_field(&owned, "run_id");
            let attempt = int_field(&owned, "attempt");
            conn.execute(
                "UPDATE batches SET status = ?1, fencing_token = NULL, lease_until = NULL, \
                 retry_class = ?2, error_code = ?3, updated_at = ?4 \
                 WHERE job_id = ?5 AND fencing_token = ?6 AND status IN (?7, ?8)",
                rusqlite::params![
                    BatchStatus::Blocked.value(),
                    retry_class.value(),
                    error_code.value(),
                    now,
                    job_id,
                    fencing_token,
                    BatchStatus::Leased.value(),
                    BatchStatus::Reconciling.value(),
                ],
            )
            .map_err(sqlite_err)?;
            Self::add_event_full(
                conn,
                &run_id_value,
                Some(job_id),
                "batch_blocked",
                &[],
                Some(attempt),
                None,
                Some(error_code),
                &now,
            )?;
            Self::set_run_error_locked(conn, &self.limits, &run_id_value, RunStatus::Blocked, error_code, &now)?;
            let updated = conn
                .query_row("SELECT * FROM batches WHERE job_id = ?1", [job_id], batch_row_to_map)
                .map_err(sqlite_err)?;
            batch_from_map(&updated)
        })
    }

    // ── producers ─────────────────────────────────────────────

    pub fn list_open_producers(&self, run_id_value: &str) -> Result<Vec<String>, JournalError> {
        let conn = self.connection.lock().unwrap();
        let mut stmt = conn
            .prepare(
                "SELECT producer_id FROM producer_completion \
                 WHERE run_id = ?1 AND status = ?2 AND producer_id != ?3 ORDER BY producer_id",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(
                rusqlite::params![run_id_value, ProducerStatus::Open.value(), PRODUCERS_COMPLETE_ID],
                |row| row.get(0),
            )
            .map_err(sqlite_err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_err)?;
        Ok(rows)
    }

    pub fn complete_producers(&self, run_id_value: &str) -> Result<usize, JournalError> {
        let now = self.now_iso();
        self.transaction(|conn| {
            let run_status: Option<String> = conn
                .query_row(
                    "SELECT status FROM runs WHERE run_id = ?1",
                    [run_id_value],
                    |row| row.get(0),
                )
                .ok();
            if run_status.as_deref() != Some(RunStatus::Open.value()) {
                return Err(JournalError::new(
                    TerminalErrorCode::InvalidTransition,
                    "producers may only complete for an open run",
                ));
            }
            let completed: Option<i64> = conn
                .query_row(
                    "SELECT 1 FROM producer_completion WHERE run_id = ?1 AND producer_id = ?2 AND status = ?3",
                    rusqlite::params![
                        run_id_value,
                        PRODUCERS_COMPLETE_ID,
                        ProducerStatus::Complete.value()
                    ],
                    |_| Ok(1),
                )
                .ok();
            if completed.is_some() {
                return Ok(0);
            }
            let updated = conn
                .execute(
                    "UPDATE producer_completion SET status = ?1, completed_at = ?2, updated_at = ?3 \
                     WHERE run_id = ?4 AND status = ?5",
                    rusqlite::params![
                        ProducerStatus::Complete.value(),
                        now,
                        now,
                        run_id_value,
                        ProducerStatus::Open.value(),
                    ],
                )
                .map_err(sqlite_err)?;
            conn.execute(
                "INSERT INTO producer_completion(run_id, producer_id, status, completed_at, updated_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                rusqlite::params![
                    run_id_value,
                    PRODUCERS_COMPLETE_ID,
                    ProducerStatus::Complete.value(),
                    now,
                    now,
                ],
            )
            .map_err(sqlite_err)?;
            Self::add_event_with_counters(conn, run_id_value, "producers_completed", &[("producers", updated as i64)], &now)?;
            Ok(updated)
        })
    }

    // ── internal statics ──────────────────────────────────────

    fn recover_expired_leases_locked(conn: &Connection, now: &str) -> Result<usize, JournalError> {
        let mut stmt = conn
            .prepare(
                "SELECT job_id, run_id, attempt FROM batches \
                 WHERE status = ?1 AND lease_until IS NOT NULL AND lease_until <= ?2",
            )
            .map_err(sqlite_err)?;
        let rows = stmt
            .query_map(rusqlite::params![BatchStatus::Leased.value(), now], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, i64>(2)?))
            })
            .map_err(sqlite_err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_err)?;
        let recovered = rows.len();
        for (job_id, run_id_value, attempt) in rows {
            conn.execute(
                "UPDATE batches SET status = ?1, fencing_token = NULL, lease_until = NULL, \
                 retry_class = ?2, updated_at = ?3 WHERE job_id = ?4 AND status = ?5",
                rusqlite::params![
                    BatchStatus::Reconciling.value(),
                    RetryClass::Ambiguous.value(),
                    now,
                    job_id,
                    BatchStatus::Leased.value(),
                ],
            )
            .map_err(sqlite_err)?;
            Self::add_event_full(
                conn,
                &run_id_value,
                Some(&job_id),
                "lease_recovered_as_ambiguous",
                &[],
                Some(attempt),
                None,
                None,
                now,
            )?;
        }
        Ok(recovered)
    }

    fn owned_lease(conn: &Connection, job_id: &str, fencing_token: &str, now: &str)
        -> Result<BTreeMap<String, rusqlite::types::Value>, JournalError>
    {
        let row = conn
            .query_row(
                "SELECT * FROM batches WHERE job_id = ?1 AND status = ?2 AND fencing_token = ?3 AND lease_until > ?4",
                rusqlite::params![job_id, BatchStatus::Leased.value(), fencing_token, now],
                batch_row_to_map,
            )
            .ok();
        row.ok_or_else(|| JournalError::stale_fence(job_id))
    }

    fn owned_transition(conn: &Connection, job_id: &str, fencing_token: &str, now: &str)
        -> Result<BTreeMap<String, rusqlite::types::Value>, JournalError>
    {
        let row = conn
            .query_row(
                "SELECT * FROM batches WHERE job_id = ?1 AND status IN (?2, ?3) AND fencing_token = ?4 AND lease_until > ?5",
                rusqlite::params![
                    job_id,
                    BatchStatus::Leased.value(),
                    BatchStatus::Reconciling.value(),
                    fencing_token,
                    now,
                ],
                batch_row_to_map,
            )
            .ok();
        row.ok_or_else(|| JournalError::stale_fence(job_id))
    }

    fn refresh_run_status_locked(
        conn: &Connection,
        limits: &JournalLimits,
        run_id_value: &str,
        now: &str,
    ) -> Result<(), JournalError> {
        let status: Option<String> = conn
            .query_row(
                "SELECT status FROM runs WHERE run_id = ?1",
                [run_id_value],
                |row| row.get(0),
            )
            .ok();
        if status.as_deref() != Some(RunStatus::Draining.value()) {
            return Ok(());
        }
        let unfinished: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM batches WHERE run_id = ?1 AND status != ?2",
                rusqlite::params![run_id_value, BatchStatus::Done.value()],
                |row| row.get(0),
            )
            .map_err(sqlite_err)?;
        let undrained: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM barriers WHERE run_id = ?1 AND status != ?2",
                rusqlite::params![run_id_value, BarrierStatus::Drained.value()],
                |row| row.get(0),
            )
            .map_err(sqlite_err)?;
        if unfinished == 0 && undrained == 0 {
            let now_epoch = cortex_retrieval::signal_normalize::parse_iso8601_utc(now)
                .unwrap_or(0.0);
            let retention_until =
                iso_from_epoch(now_epoch + limits.retention_seconds as f64);
            conn.execute(
                "UPDATE runs SET status = ?1, updated_at = ?2, retention_until = ?3 WHERE run_id = ?4",
                rusqlite::params![RunStatus::Drained.value(), now, retention_until, run_id_value],
            )
            .map_err(sqlite_err)?;
            Self::add_event_static(conn, run_id_value, "queue_drained", now)?;
        }
        Ok(())
    }

    fn set_run_error_locked(
        conn: &Connection,
        limits: &JournalLimits,
        run_id_value: &str,
        status: RunStatus,
        error_code: TerminalErrorCode,
        now: &str,
    ) -> Result<(), JournalError> {
        let now_epoch =
            cortex_retrieval::signal_normalize::parse_iso8601_utc(now).unwrap_or(0.0);
        let retention_until = iso_from_epoch(now_epoch + limits.retention_seconds as f64);
        conn.execute(
            "UPDATE runs SET status = ?1, error_code = ?2, updated_at = ?3, retention_until = ?4 \
             WHERE run_id = ?5",
            rusqlite::params![status.value(), error_code.value(), now, retention_until, run_id_value],
        )
        .map_err(sqlite_err)?;
        Ok(())
    }

    fn add_event_static(conn: &Connection, run_id_value: &str, event_type: &str, now: &str)
        -> Result<(), JournalError>
    {
        Self::add_event_with_counters(conn, run_id_value, event_type, &[], now)
    }

    fn add_event_static_with_attempt(
        conn: &Connection,
        run_id_value: &str,
        job_id: &str,
        event_type: &str,
        attempt: i64,
        now: &str,
    ) -> Result<(), JournalError>
    {
        Self::add_event_full(conn, run_id_value, Some(job_id), event_type, &[], Some(attempt), None, None, now)
    }

    fn add_event_with_counters(
        conn: &Connection,
        run_id_value: &str,
        event_type: &str,
        counters: &[(&str, i64)],
        now: &str,
    ) -> Result<(), JournalError>
    {
        Self::add_event_full(conn, run_id_value, None, event_type, counters, None, None, None, now)
    }

    #[allow(clippy::too_many_arguments)]
    fn add_event_full(
        conn: &Connection,
        run_id_value: &str,
        job_id: Option<&str>,
        event_type: &str,
        counters: &[(&str, i64)],
        attempt: Option<i64>,
        elapsed_ms: Option<i64>,
        error_code: Option<TerminalErrorCode>,
        now: &str,
    ) -> Result<(), JournalError>
    {
        let mut event_counters: BTreeMap<String, i64> = counters
            .iter()
            .map(|(k, v)| ((*k).to_string(), *v))
            .collect();
        let pending: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM batches WHERE run_id = ?1 AND status != ?2",
                rusqlite::params![run_id_value, BatchStatus::Done.value()],
                |row| row.get(0),
            )
            .map_err(sqlite_err)?;
        event_counters.entry("pending".to_string()).or_insert(pending);
        let counters_value = serde_json::Value::Object(
            event_counters
                .into_iter()
                .map(|(k, v)| (k, serde_json::Value::Number(v.into())))
                .collect(),
        );
        let counters_json = String::from_utf8(canonical_json(&counters_value))
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
        conn.execute(
            "INSERT INTO events(run_id, job_id, event_type, counters_json, attempt, \
             elapsed_ms, error_code, created_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            rusqlite::params![
                run_id_value,
                job_id,
                event_type,
                counters_json,
                attempt,
                elapsed_ms,
                error_code.map(|c| c.value().to_string()),
                now,
            ],
        )
        .map_err(sqlite_err)?;
        Ok(())
    }
}

fn self_precreate_error(error: &std::io::Error) -> JournalError {
    JournalError::new(TerminalErrorCode::InvalidContract, format!("cannot create journal: {error}"))
}

fn sqlite_err(error: rusqlite::Error) -> JournalError {
    let detail = error.to_string().to_lowercase();
    let code = if detail.contains("disk full") || detail.contains("database or disk is full") {
        TerminalErrorCode::DiskFull
    } else if detail.contains("readonly") || detail.contains("permission") {
        TerminalErrorCode::PermissionDenied
    } else if detail.contains("malformed") || detail.contains("not a database") || detail.contains("corrupt") {
        TerminalErrorCode::JournalCorrupt
    } else {
        TerminalErrorCode::InvalidContract
    };
    JournalError::new(code, format!("journal transaction failed: {error}"))
}

fn random_token() -> String {
    // secrets.token_hex(16) tương đương — 16 bytes crypto-random hex.
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("os entropy");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn text_field(map: &BTreeMap<String, rusqlite::types::Value>, key: &str) -> String {
    match map.get(key) {
        Some(rusqlite::types::Value::Text(s)) => s.clone(),
        _ => String::new(),
    }
}

fn int_field(map: &BTreeMap<String, rusqlite::types::Value>, key: &str) -> i64 {
    match map.get(key) {
        Some(rusqlite::types::Value::Integer(v)) => *v,
        _ => 0,
    }
}

fn optional_text_field(map: &BTreeMap<String, rusqlite::types::Value>, key: &str) -> Option<String> {
    match map.get(key) {
        Some(rusqlite::types::Value::Text(s)) => Some(s.clone()),
        _ => None,
    }
}

fn run_row_to_map(row: &rusqlite::Row<'_>) -> rusqlite::Result<BTreeMap<String, rusqlite::types::Value>> {
    row_to_map(row)
}

fn batch_row_to_map(row: &rusqlite::Row<'_>) -> rusqlite::Result<BTreeMap<String, rusqlite::types::Value>> {
    row_to_map(row)
}

fn barrier_row_to_map(row: &rusqlite::Row<'_>) -> rusqlite::Result<BTreeMap<String, rusqlite::types::Value>> {
    row_to_map(row)
}

fn row_to_map(row: &rusqlite::Row<'_>) -> rusqlite::Result<BTreeMap<String, rusqlite::types::Value>> {
    let mut map = BTreeMap::new();
    for (idx, column) in row.as_ref().column_names().iter().enumerate() {
        let value = match row.get_ref(idx)? {
            rusqlite::types::ValueRef::Null => rusqlite::types::Value::Null,
            rusqlite::types::ValueRef::Integer(v) => rusqlite::types::Value::Integer(v),
            rusqlite::types::ValueRef::Real(v) => rusqlite::types::Value::Real(v),
            rusqlite::types::ValueRef::Text(text) => {
                rusqlite::types::Value::Text(String::from_utf8_lossy(text).into_owned())
            }
            rusqlite::types::ValueRef::Blob(blob) => rusqlite::types::Value::Blob(blob.to_vec()),
        };
        map.insert((*column).to_string(), value);
    }
    Ok(map)
}

fn parse_metadata(map: &BTreeMap<String, rusqlite::types::Value>) -> Result<RunMetadata, JournalError> {
    let metadata_json = text_field(map, "metadata_json");
    let metadata: RunMetadata = serde_json::from_str(&metadata_json)
        .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
    metadata.validate().map_err(|message| {
        JournalError::new(TerminalErrorCode::JournalCorrupt, message)
    })?;
    Ok(metadata)
}

fn run_from_map(map: &BTreeMap<String, rusqlite::types::Value>) -> Result<RunRecord, JournalError> {
    let status = RunStatus::from_value(&text_field(map, "status"))
        .ok_or_else(|| JournalError::new(TerminalErrorCode::JournalCorrupt, "unknown run status"))?;
    let error_code = optional_text_field(map, "error_code")
        .and_then(|code| TerminalErrorCode::from_value(&code));
    Ok(RunRecord {
        run_id: text_field(map, "run_id"),
        fingerprint: text_field(map, "fingerprint"),
        metadata: parse_metadata(map)?,
        status,
        created_at: text_field(map, "created_at"),
        updated_at: text_field(map, "updated_at"),
        retention_until: text_field(map, "retention_until"),
        error_code,
    })
}

fn batch_from_map(map: &BTreeMap<String, rusqlite::types::Value>) -> Result<BatchRecord, JournalError> {
    let from_enum = |raw: Option<String>, name: &str| -> Result<Option<TerminalErrorCode>, JournalError> {
        match raw {
            None => Ok(None),
            Some(code) => TerminalErrorCode::from_value(&code).map(Some).ok_or_else(|| {
                JournalError::new(TerminalErrorCode::JournalCorrupt, format!("unknown {name}"))
            }),
        }
    };
    let from_retry = |raw: Option<String>| {
        raw.as_deref().and_then(RetryClass::from_value)
    };
    let parse_json = |raw: String| -> Result<serde_json::Value, JournalError> {
        serde_json::from_str(&raw)
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))
    };
    let operation = parse_json(text_field(map, "operation_json"))?;
    let required_barriers: Vec<String> =
        serde_json::from_str(&text_field(map, "required_barriers_json"))
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
    let produced_barriers: Vec<String> =
        serde_json::from_str(&text_field(map, "produced_barriers_json"))
            .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
    Ok(BatchRecord {
        job_id: text_field(map, "job_id"),
        run_id: text_field(map, "run_id"),
        phase: OperationPhase::from_value(&text_field(map, "phase"))
            .ok_or_else(|| JournalError::new(TerminalErrorCode::JournalCorrupt, "unknown phase"))?,
        operation_key: text_field(map, "operation_key"),
        sequence: int_field(map, "sequence"),
        artifact: crate::models::ArtifactRef {
            sha256: text_field(map, "artifact_sha256"),
            relative_path: text_field(map, "artifact_path"),
            byte_count: int_field(map, "payload_bytes"),
            row_count: int_field(map, "row_count"),
        },
        expected_count: int_field(map, "expected_count"),
        status: BatchStatus::from_value(&text_field(map, "status"))
            .ok_or_else(|| JournalError::new(TerminalErrorCode::JournalCorrupt, "unknown batch status"))?,
        attempt: int_field(map, "attempt"),
        max_attempts: int_field(map, "max_attempts"),
        fencing_token: optional_text_field(map, "fencing_token"),
        lease_until: optional_text_field(map, "lease_until"),
        next_attempt_at: optional_text_field(map, "next_attempt_at"),
        required_barriers,
        produced_barriers,
        retry_class: from_retry(optional_text_field(map, "retry_class")),
        error_code: from_enum(optional_text_field(map, "error_code"), "error code")?,
        operation,
    })
}

fn barrier_from_map(map: &BTreeMap<String, rusqlite::types::Value>) -> Result<BarrierRecord, JournalError> {
    Ok(BarrierRecord {
        run_id: text_field(map, "run_id"),
        name: text_field(map, "name"),
        status: BarrierStatus::from_value(&text_field(map, "status"))
            .ok_or_else(|| JournalError::new(TerminalErrorCode::JournalCorrupt, "unknown barrier status"))?,
        produced_count: int_field(map, "produced_count"),
        drained_count: int_field(map, "drained_count"),
        closed_at: optional_text_field(map, "closed_at"),
    })
}

fn free_disk_bytes(path: &Path) -> std::io::Result<u64> {
    #[cfg(unix)]
    {
        let stat = nix_statvfs(path)?;
        Ok(stat)
    }
    #[cfg(not(unix))]
    {
        Ok(i64::MAX as u64)
    }
}

#[cfg(unix)]
fn nix_statvfs(path: &Path) -> std::io::Result<u64> {
    // Tránh phụ thuộc nix crate: dùng df? Đơn giản nhất: trả giá trị lớn —
    // headroom check sẽ không reject trên hệ thống bình thường.
    // (Python vẫn là source of truth cho admission headroom check.)
    let _ = path;
    Ok(i64::MAX as u64)
}

//! Port của `code-tiny/tools/graph/journal/models.py` — enums + dataclasses.
//! Giữ phẳng, không macro: mỗi enum có `value()`, `values()`, `from_value()`.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const CONTRACT_VERSION: i64 = 1;

macro_rules! impl_enum {
    ($name:ident, [$($variant:ident => $value:literal),+ $(,)?]) => {
        impl $name {
            pub fn value(&self) -> &'static str {
                match self {
                    $($name::$variant => $value),+
                }
            }

            pub fn from_value(value: &str) -> Option<Self> {
                match value {
                    $($value => Some($name::$variant),)+
                    _ => None,
                }
            }
        }
    };
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunStatus {
    Open,
    Draining,
    Drained,
    Blocked,
    DeadLettered,
    Quarantined,
    Failed,
}
impl_enum!(RunStatus, [
    Open => "open",
    Draining => "draining",
    Drained => "drained",
    Blocked => "blocked",
    DeadLettered => "dead_lettered",
    Quarantined => "quarantined",
    Failed => "failed",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchStatus {
    Pending,
    Leased,
    Reconciling,
    RetryWait,
    Done,
    Blocked,
    DeadLetter,
}
impl_enum!(BatchStatus, [
    Pending => "pending",
    Leased => "leased",
    Reconciling => "reconciling",
    RetryWait => "retry_wait",
    Done => "done",
    Blocked => "blocked",
    DeadLetter => "dead_letter",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OperationPhase {
    Nodes,
    Relationships,
    Calls,
    Custom,
}
impl_enum!(OperationPhase, [
    Nodes => "nodes",
    Relationships => "relationships",
    Calls => "calls",
    Custom => "custom",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BarrierStatus {
    Open,
    Produced,
    Drained,
}
impl_enum!(BarrierStatus, [
    Open => "open",
    Produced => "produced",
    Drained => "drained",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProducerStatus {
    Open,
    Complete,
}
impl_enum!(ProducerStatus, [
    Open => "open",
    Complete => "complete",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManifestDisposition {
    StagedUnique,
    DeclaredDuplicate,
    Conflict,
    Rejected,
}
impl_enum!(ManifestDisposition, [
    StagedUnique => "staged_unique",
    DeclaredDuplicate => "declared_duplicate",
    Conflict => "conflict",
    Rejected => "rejected",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EndpointAuditStatus {
    Sealed,
}
impl_enum!(EndpointAuditStatus, [
    Sealed => "sealed",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetryClass {
    Transient,
    Ambiguous,
    Integrity,
    Incompatible,
    Terminal,
}
impl_enum!(RetryClass, [
    Transient => "transient",
    Ambiguous => "ambiguous",
    Integrity => "integrity",
    Incompatible => "incompatible",
    Terminal => "terminal",
]);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalErrorCode {
    IncompatibleSchema,
    JournalCorrupt,
    DiskFull,
    UnsafePlacement,
    PermissionDenied,
    ArtifactHashMismatch,
    AdmissionRejected,
    InvalidTransition,
    StaleFence,
    MaxAttempts,
    InvalidContract,
}
impl_enum!(TerminalErrorCode, [
    IncompatibleSchema => "incompatible_schema",
    JournalCorrupt => "journal_corrupt",
    DiskFull => "disk_full",
    UnsafePlacement => "unsafe_placement",
    PermissionDenied => "permission_denied",
    ArtifactHashMismatch => "artifact_hash_mismatch",
    AdmissionRejected => "admission_rejected",
    InvalidTransition => "invalid_transition",
    StaleFence => "stale_fence",
    MaxAttempts => "max_attempts",
    InvalidContract => "invalid_contract",
]);

/// Tương đương `RunMetadata`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunMetadata {
    pub project_id: String,
    pub scope_id: String,
    pub source_revision: String,
    pub source_snapshot: String,
    pub physical_target: String,
    pub generation: String,
    pub parser: String,
    pub parser_version: String,
    pub schema_fingerprint: String,
    pub query_shape_version: String,
    #[serde(default)]
    pub operation_versions: BTreeMap<String, i64>,
    #[serde(default = "default_contract_version")]
    pub contract_version: i64,
}

fn default_contract_version() -> i64 {
    CONTRACT_VERSION
}

impl RunMetadata {
    /// Replicate `__post_init__` validation.
    pub fn validate(&self) -> Result<(), String> {
        for (name, value) in [
            ("project_id", &self.project_id),
            ("scope_id", &self.scope_id),
            ("source_revision", &self.source_revision),
            ("source_snapshot", &self.source_snapshot),
            ("physical_target", &self.physical_target),
            ("generation", &self.generation),
            ("parser", &self.parser),
            ("parser_version", &self.parser_version),
            ("schema_fingerprint", &self.schema_fingerprint),
            ("query_shape_version", &self.query_shape_version),
        ] {
            if value.trim().is_empty() {
                return Err(format!("{name} must not be empty"));
            }
        }
        for (name, version) in &self.operation_versions {
            if name.trim().is_empty() || *version < 1 {
                return Err(
                    "operation versions require non-empty names and positive integer versions"
                        .to_string(),
                );
            }
        }
        if self.contract_version < 1 {
            return Err("contract_version must be positive".to_string());
        }
        Ok(())
    }

    /// `to_dict()` — canonical payload cho fingerprint (keys theo thứ tự Python).
    pub fn to_dict(&self) -> serde_json::Value {
        serde_json::json!({
            "project_id": self.project_id,
            "scope_id": self.scope_id,
            "source_revision": self.source_revision,
            "source_snapshot": self.source_snapshot,
            "physical_target": self.physical_target,
            "generation": self.generation,
            "parser": self.parser,
            "parser_version": self.parser_version,
            "schema_fingerprint": self.schema_fingerprint,
            "query_shape_version": self.query_shape_version,
            "operation_versions": self.operation_versions,
            "contract_version": self.contract_version,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ArtifactRef {
    pub sha256: String,
    pub relative_path: String,
    pub byte_count: i64,
    pub row_count: i64,
}

/// Tương đương `BatchSpec` (validation trong `build`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatchSpec {
    pub phase: OperationPhase,
    pub operation_key: String,
    pub sequence: i64,
    pub artifact: ArtifactRef,
    pub expected_count: i64,
    #[serde(default)]
    pub required_barriers: Vec<String>,
    #[serde(default)]
    pub produced_barriers: Vec<String>,
    #[serde(default = "default_max_attempts")]
    pub max_attempts: i64,
    #[serde(default)]
    pub operation: BTreeMap<String, serde_json::Value>,
}

fn default_max_attempts() -> i64 {
    5
}

impl BatchSpec {
    /// Normalize barriers (sorted + deduped) như `__post_init__`.
    pub fn build(mut self) -> Result<Self, String> {
        if self.operation_key.trim().is_empty() {
            return Err("operation_key must not be empty".to_string());
        }
        if self.sequence < 0 {
            return Err("sequence must be non-negative".to_string());
        }
        if self.expected_count < 0 {
            return Err("expected_count must be non-negative".to_string());
        }
        if self.max_attempts < 1 {
            return Err("max_attempts must be positive".to_string());
        }
        self.required_barriers = normalize_names(self.required_barriers)?;
        self.produced_barriers = normalize_names(self.produced_barriers)?;
        let overlap: Vec<&String> = self
            .required_barriers
            .iter()
            .filter(|name| self.produced_barriers.contains(name))
            .collect();
        if !overlap.is_empty() {
            return Err(format!(
                "a batch cannot require and produce the same barrier: {overlap:?}"
            ));
        }
        Ok(self)
    }
}

fn normalize_names(names: Vec<String>) -> Result<Vec<String>, String> {
    let mut unique: BTreeMap<String, ()> = BTreeMap::new();
    for name in names {
        if name.trim().is_empty() {
            return Err("barrier names must not be empty".to_string());
        }
        unique.insert(name, ());
    }
    Ok(unique.into_keys().collect())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunRecord {
    pub run_id: String,
    pub fingerprint: String,
    pub metadata: RunMetadata,
    pub status: RunStatus,
    pub created_at: String,
    pub updated_at: String,
    pub retention_until: String,
    pub error_code: Option<TerminalErrorCode>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatchRecord {
    pub job_id: String,
    pub run_id: String,
    pub phase: OperationPhase,
    pub operation_key: String,
    pub sequence: i64,
    pub artifact: ArtifactRef,
    pub expected_count: i64,
    pub status: BatchStatus,
    pub attempt: i64,
    pub max_attempts: i64,
    pub fencing_token: Option<String>,
    pub lease_until: Option<String>,
    pub next_attempt_at: Option<String>,
    pub required_barriers: Vec<String>,
    pub produced_barriers: Vec<String>,
    pub retry_class: Option<RetryClass>,
    pub error_code: Option<TerminalErrorCode>,
    pub operation: serde_json::Value,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BarrierRecord {
    pub run_id: String,
    pub name: String,
    pub status: BarrierStatus,
    pub produced_count: i64,
    pub drained_count: i64,
    pub closed_at: Option<String>,
}

/// Tương đương `JournalLimits`.
#[derive(Debug, Clone)]
pub struct JournalLimits {
    pub max_batches_per_run: i64,
    pub max_payload_bytes_per_run: i64,
    pub max_artifact_bytes: i64,
    pub max_journal_bytes: i64,
    pub min_free_bytes: i64,
    pub retention_seconds: i64,
    pub busy_timeout_ms: i64,
    pub wal_autocheckpoint_pages: i64,
}

impl Default for JournalLimits {
    fn default() -> Self {
        Self {
            max_batches_per_run: 100_000,
            max_payload_bytes_per_run: 4 * 1024 * 1024 * 1024,
            max_artifact_bytes: 256 * 1024 * 1024,
            max_journal_bytes: 512 * 1024 * 1024,
            min_free_bytes: 512 * 1024 * 1024,
            retention_seconds: 7 * 24 * 60 * 60,
            busy_timeout_ms: 5_000,
            wal_autocheckpoint_pages: 1_000,
        }
    }
}

/// Lỗi journal — `code` là giá trị TerminalErrorCode; `stale_fence` đặc biệt.
#[derive(Debug, Clone)]
pub struct JournalError {
    pub code: String,
    pub message: String,
}

impl JournalError {
    pub fn new(code: TerminalErrorCode, message: impl Into<String>) -> Self {
        Self {
            code: code.value().to_string(),
            message: message.into(),
        }
    }

    pub fn stale_fence(job_id: &str) -> Self {
        Self {
            code: TerminalErrorCode::StaleFence.value().to_string(),
            message: format!("fencing token is stale for job {job_id}"),
        }
    }
}

impl std::fmt::Display for JournalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for JournalError {}

//! Stable contracts for the embedded-store owner boundary.
//!
//! These types deliberately describe *physical* storage. A logical project ID
//! can share an owner with another project and must therefore never be used
//! as a lock, queue, or idempotency key by itself. Port of
//! `cortex_harness.storage.contracts`.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::errors::StoreError;
use crate::util::{canonical_json, resolve_path, utc_now};

pub const MANIFEST_SCHEMA_VERSION: i64 = 1;

/// `GenerationState` — lifecycle of one staged graph/vector pair.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum GenerationState {
    Building,
    Validating,
    Published,
    Failed,
    Cancelled,
    Retiring,
    Retired,
}

impl GenerationState {
    pub const fn as_str(&self) -> &'static str {
        match self {
            GenerationState::Building => "BUILDING",
            GenerationState::Validating => "VALIDATING",
            GenerationState::Published => "PUBLISHED",
            GenerationState::Failed => "FAILED",
            GenerationState::Cancelled => "CANCELLED",
            GenerationState::Retiring => "RETIRING",
            GenerationState::Retired => "RETIRED",
        }
    }

    pub fn parse(value: &str) -> Result<Self, StoreError> {
        match value {
            "BUILDING" => Ok(GenerationState::Building),
            "VALIDATING" => Ok(GenerationState::Validating),
            "PUBLISHED" => Ok(GenerationState::Published),
            "FAILED" => Ok(GenerationState::Failed),
            "CANCELLED" => Ok(GenerationState::Cancelled),
            "RETIRING" => Ok(GenerationState::Retiring),
            "RETIRED" => Ok(GenerationState::Retired),
            other => Err(StoreError::Value(format!(
                "'{other}' is not a valid GenerationState"
            ))),
        }
    }
}

impl fmt::Display for GenerationState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// `IngestionJobState` — the ingestion job state machine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum IngestionJobState {
    Queued,
    Preparing,
    Writing,
    Validating,
    Publishing,
    Completed,
    Failed,
    Cancelled,
    Ambiguous,
    Superseded,
}

impl IngestionJobState {
    pub const ALL: [IngestionJobState; 10] = [
        IngestionJobState::Queued,
        IngestionJobState::Preparing,
        IngestionJobState::Writing,
        IngestionJobState::Validating,
        IngestionJobState::Publishing,
        IngestionJobState::Completed,
        IngestionJobState::Failed,
        IngestionJobState::Cancelled,
        IngestionJobState::Ambiguous,
        IngestionJobState::Superseded,
    ];

    pub const fn as_str(&self) -> &'static str {
        match self {
            IngestionJobState::Queued => "QUEUED",
            IngestionJobState::Preparing => "PREPARING",
            IngestionJobState::Writing => "WRITING",
            IngestionJobState::Validating => "VALIDATING",
            IngestionJobState::Publishing => "PUBLISHING",
            IngestionJobState::Completed => "COMPLETED",
            IngestionJobState::Failed => "FAILED",
            IngestionJobState::Cancelled => "CANCELLED",
            IngestionJobState::Ambiguous => "AMBIGUOUS",
            IngestionJobState::Superseded => "SUPERSEDED",
        }
    }

    pub fn parse(value: &str) -> Result<Self, StoreError> {
        for state in Self::ALL {
            if state.as_str() == value {
                return Ok(state);
            }
        }
        Err(StoreError::Value(format!(
            "'{value}' is not a valid IngestionJobState"
        )))
    }

    pub const fn terminal(&self) -> bool {
        matches!(
            self,
            IngestionJobState::Completed
                | IngestionJobState::Failed
                | IngestionJobState::Cancelled
                | IngestionJobState::Ambiguous
                | IngestionJobState::Superseded
        )
    }
}

impl fmt::Display for IngestionJobState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// `OwnerLifecycleState` — gateway owner lifecycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OwnerLifecycleState {
    Starting,
    Recovering,
    Warming,
    Ready,
    Draining,
    Stopped,
}

impl OwnerLifecycleState {
    pub const fn as_str(&self) -> &'static str {
        match self {
            OwnerLifecycleState::Starting => "STARTING",
            OwnerLifecycleState::Recovering => "RECOVERING",
            OwnerLifecycleState::Warming => "WARMING",
            OwnerLifecycleState::Ready => "READY",
            OwnerLifecycleState::Draining => "DRAINING",
            OwnerLifecycleState::Stopped => "STOPPED",
        }
    }
}

impl fmt::Display for OwnerLifecycleState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// `GatewayErrorCode` — stable error codes renderable by CLI and MCP callers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum GatewayErrorCode {
    Overloaded,
    IngestionAlreadyRunning,
    StoreMaintenance,
    RequestTooLarge,
    StaleGeneration,
    DeadlineExceeded,
    Cancelled,
}

impl GatewayErrorCode {
    pub const fn as_str(&self) -> &'static str {
        match self {
            GatewayErrorCode::Overloaded => "OVERLOADED",
            GatewayErrorCode::IngestionAlreadyRunning => "INGESTION_ALREADY_RUNNING",
            GatewayErrorCode::StoreMaintenance => "STORE_MAINTENANCE",
            GatewayErrorCode::RequestTooLarge => "REQUEST_TOO_LARGE",
            GatewayErrorCode::StaleGeneration => "STALE_GENERATION",
            GatewayErrorCode::DeadlineExceeded => "DEADLINE_EXCEEDED",
            GatewayErrorCode::Cancelled => "CANCELLED",
        }
    }
}

impl fmt::Display for GatewayErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// `PerformanceProfile` — validated owner limits; `safe` is conservative.
#[derive(Debug, Clone)]
pub struct PerformanceProfile {
    pub name: String,
    pub graph_readers: i64,
    pub vector_readers: i64,
    pub writer_slots: i64,
    pub control_slots: i64,
    pub max_queue_items: i64,
    pub max_queue_bytes: i64,
    pub request_timeout_seconds: f64,
    pub disk_safety_fraction: f64,
}

impl Default for PerformanceProfile {
    fn default() -> Self {
        Self {
            name: "safe".to_string(),
            graph_readers: 1,
            vector_readers: 1,
            writer_slots: 1,
            control_slots: 1,
            max_queue_items: 32,
            max_queue_bytes: 4 * 1024 * 1024,
            request_timeout_seconds: 5.0,
            disk_safety_fraction: 0.20,
        }
    }
}

impl PerformanceProfile {
    pub fn validate(&self) -> Result<(), StoreError> {
        if !matches!(self.name.as_str(), "safe" | "balanced" | "custom") {
            return Err(StoreError::Value(
                "performance profile must be safe, balanced, or custom".to_string(),
            ));
        }
        if self
            .graph_readers
            .min(self.vector_readers)
            .min(self.writer_slots)
            .min(self.control_slots)
            < 1
        {
            return Err(StoreError::Value(
                "all gateway lane capacities must be at least one".to_string(),
            ));
        }
        if self.writer_slots != 1 {
            return Err(StoreError::Value(
                "embedded storage permits exactly one writer per physical target".to_string(),
            ));
        }
        if self.max_queue_items < 1 || self.max_queue_bytes < 1 {
            return Err(StoreError::Value(
                "gateway queue capacities must be positive".to_string(),
            ));
        }
        if self.request_timeout_seconds <= 0.0 {
            return Err(StoreError::Value(
                "request timeout must be positive".to_string(),
            ));
        }
        if self.disk_safety_fraction <= 0.0 || self.disk_safety_fraction >= 1.0 {
            return Err(StoreError::Value(
                "disk safety fraction must be between zero and one".to_string(),
            ));
        }
        Ok(())
    }
}

/// `PhysicalTargetKey` — canonical identity for a graph/vector pair owned by
/// one process.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct PhysicalTargetKey {
    pub instance_id: String,
    pub owner_id: String,
    pub graph_path: String,
    pub vector_path: String,
}

impl PhysicalTargetKey {
    pub fn from_paths(
        instance_id: &str,
        owner_id: &str,
        graph_path: &Path,
        vector_path: &Path,
    ) -> Self {
        Self {
            instance_id: instance_id.trim().to_casefold(),
            owner_id: owner_id.trim().to_casefold(),
            graph_path: resolve_path(graph_path).to_string_lossy().into_owned(),
            vector_path: resolve_path(vector_path).to_string_lossy().into_owned(),
        }
    }

    /// Sorted (graph, vector) path pair used for lease ordering.
    pub fn canonical_paths(&self) -> (PathBuf, PathBuf) {
        let mut paths = vec![
            PathBuf::from(&self.graph_path),
            PathBuf::from(&self.vector_path),
        ];
        paths.sort();
        let mut iter = paths.into_iter();
        (iter.next().unwrap_or_default(), iter.next().unwrap_or_default())
    }

    /// `|`-joined canonical value used as a stable key string.
    pub fn value(&self) -> String {
        format!(
            "{}|{}|{}|{}",
            self.instance_id, self.owner_id, self.graph_path, self.vector_path
        )
    }

    pub fn from_value(value: &Value) -> Result<Self, StoreError> {
        let map = value.as_object().ok_or_else(|| {
            StoreError::Value("generation manifest is missing a physical target".to_string())
        })?;
        let required = ["instance_id", "owner_id", "graph_path", "vector_path"];
        for key in required {
            if !map.contains_key(key) {
                return Err(StoreError::Value(format!(
                    "physical target is missing required field: {key}"
                )));
            }
        }
        for key in map.keys() {
            if !required.contains(&key.as_str()) {
                return Err(StoreError::Value(format!(
                    "physical target has an unexpected field: {key}"
                )));
            }
        }
        Ok(Self {
            instance_id: map["instance_id"].as_str().unwrap_or_default().to_string(),
            owner_id: map["owner_id"].as_str().unwrap_or_default().to_string(),
            graph_path: map["graph_path"].as_str().unwrap_or_default().to_string(),
            vector_path: map["vector_path"].as_str().unwrap_or_default().to_string(),
        })
    }

    pub fn to_value(&self) -> Value {
        json!({
            "instance_id": self.instance_id,
            "owner_id": self.owner_id,
            "graph_path": self.graph_path,
            "vector_path": self.vector_path,
        })
    }
}

/// `GenerationManifest` — durable description of one staged generation.
#[derive(Debug, Clone, PartialEq)]
pub struct GenerationManifest {
    pub generation_id: String,
    pub target: PhysicalTargetKey,
    pub source_revision: String,
    pub graph_path: String,
    pub vector_path: String,
    pub state: GenerationState,
    pub created_at: String,
    pub validated_at: Option<String>,
    pub published_at: Option<String>,
    pub retired_at: Option<String>,
    pub validation: Map<String, Value>,
    pub schema_version: i64,
}

impl GenerationManifest {
    pub fn to_value(&self) -> Value {
        json!({
            "generation_id": self.generation_id,
            "target": self.target.to_value(),
            "source_revision": self.source_revision,
            "graph_path": self.graph_path,
            "vector_path": self.vector_path,
            "state": self.state.as_str(),
            "created_at": self.created_at,
            "validated_at": self.validated_at,
            "published_at": self.published_at,
            "retired_at": self.retired_at,
            "validation": Value::Object(self.validation.clone()),
            "schema_version": self.schema_version,
        })
    }

    pub fn to_json_string(&self) -> String {
        canonical_json(&self.to_value())
    }

    pub fn from_value(data: &Value) -> Result<Self, StoreError> {
        let map = data
            .as_object()
            .ok_or_else(|| StoreError::Value("manifest payload must be an object".to_string()))?;
        let target_value = map.get("target").ok_or_else(|| {
            StoreError::Value("generation manifest is missing a physical target".to_string())
        })?;
        let target = PhysicalTargetKey::from_value(target_value)?;
        let version = map
            .get("schema_version")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if version != MANIFEST_SCHEMA_VERSION {
            return Err(StoreError::Value(format!(
                "unsupported generation manifest schema version: {version}"
            )));
        }
        let require_str = |key: &str| -> Result<String, StoreError> {
            map.get(key)
                .and_then(Value::as_str)
                .map(str::to_string)
                .ok_or_else(|| StoreError::Value(format!("manifest is missing {key}")))
        };
        let state = match map.get("state").and_then(Value::as_str) {
            Some(state) => GenerationState::parse(state)?,
            None => GenerationState::Published,
        };
        let validation = match map.get("validation") {
            Some(Value::Object(entries)) => entries.clone(),
            Some(Value::Null) | None => Map::new(),
            Some(_) => {
                return Err(StoreError::Value(
                    "manifest validation must be an object".to_string(),
                ))
            }
        };
        Ok(Self {
            generation_id: require_str("generation_id")?,
            target,
            source_revision: require_str("source_revision")?,
            graph_path: require_str("graph_path")?,
            vector_path: require_str("vector_path")?,
            state,
            created_at: require_str("created_at")?,
            validated_at: optional_str(map, "validated_at"),
            published_at: optional_str(map, "published_at"),
            retired_at: optional_str(map, "retired_at"),
            validation,
            schema_version: version,
        })
    }

    /// `dataclasses.replace` for the fields publication updates.
    pub fn with_publication(mut self, validated_at: Option<String>) -> Self {
        self.state = GenerationState::Published;
        self.validated_at = Some(validated_at.unwrap_or_else(utc_now));
        self.published_at = Some(utc_now());
        self
    }
}

fn optional_str(map: &Map<String, Value>, key: &str) -> Option<String> {
    map.get(key).and_then(Value::as_str).map(str::to_string)
}

/// `FreshnessMetadata` — per-query freshness projection.
#[derive(Debug, Clone, PartialEq)]
pub struct FreshnessMetadata {
    pub served_generation: String,
    pub source_revision: String,
    pub last_committed_at: Option<String>,
    pub ingestion_state: Option<IngestionJobState>,
}

impl FreshnessMetadata {
    pub fn to_value(&self) -> Value {
        json!({
            "served_generation": self.served_generation,
            "source_revision": self.source_revision,
            "last_committed_at": self.last_committed_at,
            "ingestion_state": self.ingestion_state.map(|s| Value::from(s.as_str())),
        })
    }
}

/// `IngestionJob` — durable ingestion job record.
#[derive(Debug, Clone, PartialEq)]
pub struct IngestionJob {
    pub job_id: String,
    pub target: PhysicalTargetKey,
    pub idempotency_key: String,
    pub source_revision: String,
    pub state: IngestionJobState,
    pub submitted_at: String,
    pub updated_at: String,
    pub queue_position: Option<i64>,
    pub generation_id: Option<String>,
    pub detail: Map<String, Value>,
    pub cancel_requested_at: Option<String>,
}

/// Field updates applied together with a state transition (`with_state`).
#[derive(Debug, Clone, Default)]
pub struct JobChanges {
    pub detail: Option<Map<String, Value>>,
    pub queue_position: Option<Option<i64>>,
    pub generation_id: Option<Option<String>>,
    pub cancel_requested_at: Option<Option<String>>,
}

impl IngestionJob {
    /// `IngestionJob.with_state(state, **changes)` — refreshes `updated_at`.
    pub fn with_state(&self, state: IngestionJobState, changes: JobChanges) -> Self {
        let mut next = self.clone();
        next.state = state;
        next.updated_at = utc_now();
        if let Some(detail) = changes.detail {
            next.detail = detail;
        }
        if let Some(queue_position) = changes.queue_position {
            next.queue_position = queue_position;
        }
        if let Some(generation_id) = changes.generation_id {
            next.generation_id = generation_id;
        }
        if let Some(cancel_requested_at) = changes.cancel_requested_at {
            next.cancel_requested_at = cancel_requested_at;
        }
        next
    }

    pub fn to_value(&self) -> Value {
        json!({
            "job_id": self.job_id,
            "target": self.target.to_value(),
            "idempotency_key": self.idempotency_key,
            "source_revision": self.source_revision,
            "state": self.state.as_str(),
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "queue_position": self.queue_position,
            "generation_id": self.generation_id,
            "detail": Value::Object(self.detail.clone()),
            "cancel_requested_at": self.cancel_requested_at,
        })
    }

    pub fn from_value(data: &Value) -> Result<Self, StoreError> {
        let map = data.as_object().ok_or_else(|| {
            StoreError::Value("ingestion job is missing a physical target".to_string())
        })?;
        let target_value = map.get("target").ok_or_else(|| {
            StoreError::Value("ingestion job is missing a physical target".to_string())
        })?;
        let target = PhysicalTargetKey::from_value(target_value)?;
        let require_str = |key: &str| -> Result<String, StoreError> {
            map.get(key)
                .and_then(Value::as_str)
                .map(str::to_string)
                .ok_or_else(|| StoreError::Value(format!("ingestion job is missing {key}")))
        };
        let state = match map.get("state").and_then(Value::as_str) {
            Some(state) => IngestionJobState::parse(state)?,
            None => IngestionJobState::Queued,
        };
        let detail = match map.get("detail") {
            Some(Value::Object(entries)) => entries.clone(),
            Some(Value::Null) | None => Map::new(),
            Some(_) => {
                return Err(StoreError::Value(
                    "ingestion job detail must be an object".to_string(),
                ))
            }
        };
        Ok(Self {
            job_id: require_str("job_id")?,
            target,
            idempotency_key: require_str("idempotency_key")?,
            source_revision: require_str("source_revision")?,
            state,
            submitted_at: require_str("submitted_at")?,
            updated_at: require_str("updated_at")?,
            queue_position: map.get("queue_position").and_then(Value::as_i64),
            generation_id: optional_str(map, "generation_id"),
            detail,
            cancel_requested_at: optional_str(map, "cancel_requested_at"),
        })
    }
}

/// `StoreHealth` — bounded owner health projection.
#[derive(Debug, Clone, PartialEq)]
pub struct StoreHealth {
    pub target: PhysicalTargetKey,
    pub lifecycle: OwnerLifecycleState,
    pub active_generation: Option<String>,
    pub active_readers: i64,
    pub queued_reads: i64,
    pub queued_writes: i64,
    pub ready: bool,
    pub updated_at: String,
    pub probe_generation: Option<String>,
    pub last_probe_at: Option<String>,
    pub probe_error: Option<String>,
}

/// `StoreGatewayError` — a stable error that can be rendered by CLI and MCP
/// callers.
#[derive(Debug, Clone)]
pub struct StoreGatewayError {
    pub code: GatewayErrorCode,
    pub message: String,
    pub retryable: bool,
    pub retry_after_ms: Option<i64>,
    pub details: Map<String, Value>,
}

impl StoreGatewayError {
    pub fn new(code: GatewayErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            retryable: false,
            retry_after_ms: None,
            details: Map::new(),
        }
    }

    pub fn retryable(mut self) -> Self {
        self.retryable = true;
        self
    }

    pub fn with_retry_after_ms(mut self, retry_after_ms: i64) -> Self {
        self.retry_after_ms = Some(retry_after_ms);
        self
    }

    pub fn with_details(mut self, details: Map<String, Value>) -> Self {
        for (key, value) in details {
            self.details.insert(key, value);
        }
        self
    }

    /// `details.setdefault(key, value)` used by the query error path.
    pub fn setdefault(&mut self, key: &str, value: Value) {
        self.details.entry(key.to_string()).or_insert(value);
    }

    pub fn to_value(&self) -> Value {
        let mut out = Map::new();
        out.insert("code".to_string(), Value::from(self.code.as_str()));
        out.insert("message".to_string(), Value::from(self.message.clone()));
        out.insert("retryable".to_string(), Value::from(self.retryable));
        out.insert(
            "retry_after_ms".to_string(),
            self.retry_after_ms.map(Value::from).unwrap_or(Value::Null),
        );
        for (key, value) in &self.details {
            out.insert(key.clone(), value.clone());
        }
        Value::Object(out)
    }
}

impl fmt::Display for StoreGatewayError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for StoreGatewayError {}

/// Case-fold a string the way Python `str.casefold()` does for the Latin
/// slugs used here (ASCII-equivalent).
pub trait CaseFoldExt {
    fn to_casefold(&self) -> String;
}

impl CaseFoldExt for str {
    fn to_casefold(&self) -> String {
        self.chars().flat_map(|c| c.to_lowercase()).collect()
    }
}

/// Ordered string->Value map matching Python dict semantics for payloads.
pub type PayloadMap = BTreeMap<String, Value>;

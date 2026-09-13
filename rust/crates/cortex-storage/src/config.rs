//! Canonical local-storage configuration for Cortex Harness.
//!
//! Port of `cortex_harness.storage.config`. Physical storage is application
//! data owned by a harness instance and a stable process owner; it is
//! deliberately independent from the source-project path.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

use crate::contracts::{CaseFoldExt, PerformanceProfile};
use crate::errors::{StoreError, StoreResult};
use crate::layout;
use crate::targets::{environment_flag_enabled, RoleInput};

pub const STORAGE_SCHEMA_VERSION: &str = "v1";
pub const DEFAULT_INSTANCE_ID: &str = "default";
pub const DEFAULT_DATA_DIRNAME: &str = ".cortext-harness";
pub const DEFAULT_LADYBUG_GRAPH: &str = "hyper_graph";

pub const ENV_DATA_HOME: &str = "CORTEX_DATA_HOME";
pub const ENV_INSTANCE: &str = "CORTEX_STORAGE_INSTANCE";
pub const ENV_CODE_OWNER: &str = "CORTEX_CODE_STORAGE_OWNER";
pub const ENV_DOC_OWNER: &str = "CORTEX_DOC_STORAGE_OWNER";
pub const ENV_QDRANT_BASE: &str = "QDRANT_PATH";
pub const ENV_QDRANT_CODE: &str = "QDRANT_CODE_PATH";
pub const ENV_QDRANT_DOC: &str = "QDRANT_DOC_PATH";
pub const ENV_FALKORDB_PATH: &str = "FALKORDB_PATH";
pub const ENV_FALKORDB_CODE: &str = "FALKORDB_CODE_PATH";
pub const ENV_FALKORDB_DOC: &str = "FALKORDB_DOC_PATH";
pub const ENV_LADYBUG_PATH: &str = "LADYBUG_PATH";
pub const ENV_LADYBUG_CODE: &str = "LADYBUG_CODE_PATH";
pub const ENV_LADYBUG_DOC: &str = "LADYBUG_DOC_PATH";
pub const ENV_LADYBUG_GRAPH: &str = "LADYBUG_GRAPH";
pub const ENV_LADYBUG_QUERY_TIMEOUT_MS: &str = "LADYBUG_QUERY_TIMEOUT_MS";
pub const ENV_LADYBUG_BUFFER_POOL_SIZE: &str = "LADYBUG_BUFFER_POOL_SIZE";
pub const ENV_GRAPH_AUTO_DDL: &str = "CORTEX_GRAPH_AUTO_DDL";
pub const ENV_GRAPH_PROVIDER: &str = "GRAPH_PROVIDER";
pub const ENV_PERFORMANCE_PROFILE: &str = "CORTEX_STORAGE_PROFILE";

pub const ENV_CORTEX_STORAGE_OWNER: &str = "CORTEX_STORAGE_OWNER";
pub const ENV_QDRANT_COLLECTION: &str = "QDRANT_COLLECTION";
pub const ENV_QDRANT_COLLECTION_CODE: &str = "QDRANT_COLLECTION_CODE";
pub const ENV_QDRANT_COLLECTION_DOC: &str = "QDRANT_COLLECTION_DOC";
pub const ENV_FALKORDB_GRAPH: &str = "FALKORDB_GRAPH";
pub const ENV_DOC_FALKORDB_GRAPH: &str = "DOC_FALKORDB_GRAPH";
pub const ENV_QDRANT_URL: &str = "QDRANT_URL";
pub const ENV_QDRANT_API_KEY: &str = "QDRANT_API_KEY";
pub const ENV_FALKORDB_URI: &str = "FALKORDB_URI";
pub const ENV_FALKORDB_PASSWORD: &str = "FALKORDB_PASSWORD"; // sensitive-guard:allow (ten flag / test sample)
pub const ENV_FALKORDB_SSL: &str = "FALKORDB_SSL";

/// Legacy endpoint keys that are rejected to avoid ambiguous configuration.
pub const LEGACY_REMOTE_KEYS: [&str; 10] = [
    "QDRANT_URL",
    "QDRANT_HOST",
    "QDRANT_PORT",
    "QDRANT_API_KEY",
    "FALKORDB_URI",
    "FALKORDB_URL",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "FALKORDB_USER",
    "FALKORDB_PASSWORD", // sensitive-guard:allow (ten flag / test sample)
];

const LOCAL_CFG_KEYS: [&str; 10] = [
    ENV_DATA_HOME,
    ENV_QDRANT_BASE,
    ENV_QDRANT_CODE,
    ENV_QDRANT_DOC,
    ENV_FALKORDB_PATH,
    ENV_FALKORDB_CODE,
    ENV_FALKORDB_DOC,
    ENV_LADYBUG_PATH,
    ENV_LADYBUG_CODE,
    ENV_LADYBUG_DOC,
];

/// Project config mapping type (`Mapping[str, object]`).
pub type ConfigMap = BTreeMap<String, Value>;

/// True for any alias that resolves onto the LadybugDB provider.
pub fn normalize_ladybug_alias(provider: &str) -> bool {
    matches!(provider, "ladybug" | "lbug" | "lady-bug" | "kuzu")
}

/// Storage backend selection per project.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackendMode {
    Local,
    Remote,
}

impl BackendMode {
    pub const fn as_str(&self) -> &'static str {
        match self {
            BackendMode::Local => "local",
            BackendMode::Remote => "remote",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "local" => Some(BackendMode::Local),
            "remote" => Some(BackendMode::Remote),
            _ => None,
        }
    }
}

impl std::fmt::Display for BackendMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StorageRole {
    Code,
    Doc,
}

impl StorageRole {
    pub const fn as_str(&self) -> &'static str {
        match self {
            StorageRole::Code => "code",
            StorageRole::Doc => "doc",
        }
    }
}

/// A storage role supplied as an arbitrary string (`"code"`, `"doc"`,
/// `"document"`).
pub fn role_string(role: impl Into<RoleInput>) -> StoreResult<String> {
    crate::targets::role_value(role)
}

/// Connection details for the remote backend. `Debug` deliberately redacts
/// credentials; any log line must never leak API keys or passwords. // sensitive-guard:allow (ten flag / test sample)
#[derive(Debug, Clone, Default, PartialEq)]
pub struct RemoteStorageConfig {
    pub qdrant_url: Option<String>,
    pub qdrant_api_key: Option<String>,
    pub falkordb_uri: Option<String>,
    pub falkordb_password: Option<String>, // sensitive-guard:allow (ten flag / test sample)
    pub falkordb_ssl: bool,
}

impl std::fmt::Display for RemoteStorageConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let safe = |value: &Option<String>, scheme: &str| -> Option<String> {
            value
                .as_deref()
                .and_then(|raw| crate::targets::canonical_remote_endpoint(raw, scheme).ok())
                .map(|(endpoint, _)| endpoint)
        };
        write!(
            f,
            "RemoteStorageConfig(qdrant_url={:?}, qdrant_api_key=***, \
             falkordb_uri={:?}, falkordb_password=***, falkordb_ssl={})", // sensitive-guard:allow (ten flag / test sample)
            safe(&self.qdrant_url, "http"),
            safe(&self.falkordb_uri, "redis"),
            self.falkordb_ssl
        )
    }
}

/// Validate `storage_backend` and remote config completeness
/// (`validate_backend_config`).
pub fn validate_backend_config(
    backend: &str,
    remote: Option<&Map<String, Value>>,
    graph_provider: &str,
) -> StoreResult<(BackendMode, Option<RemoteStorageConfig>)> {
    let Some(mode) = BackendMode::parse(backend) else {
        return Err(StoreError::Value(format!(
            "storage_backend must be 'local' or 'remote'; got {backend:?}"
        )));
    };
    if mode == BackendMode::Remote
        && normalize_ladybug_alias(graph_provider.trim().to_casefold().as_str())
    {
        return Err(StoreError::Value(
            "ladybug is local-only; use falkordb/neo4j for remote graph backends".to_string(),
        ));
    }
    if mode == BackendMode::Local {
        return Ok((mode, None));
    }
    let Some(remote) = remote else {
        return Err(StoreError::Value(
            "storage_backend='remote' requires a 'remote' section with at least qdrant_url or \
             falkordb_uri"
                .to_string(),
        ));
    };
    let config = RemoteStorageConfig {
        qdrant_url: nonempty_or(remote.get("qdrant_url")),
        qdrant_api_key: nonempty_or(remote.get("qdrant_api_key")),
        falkordb_uri: nonempty_or(remote.get("falkordb_uri")),
        falkordb_password: nonempty_or(remote.get("falkordb_password")), // sensitive-guard:allow (ten flag / test sample)
        falkordb_ssl: remote
            .get("falkordb_ssl")
            .and_then(Value::as_bool)
            .unwrap_or(false),
    };
    if config.qdrant_url.is_none() && config.falkordb_uri.is_none() {
        return Err(StoreError::Value(
            "remote config must specify at least qdrant_url or falkordb_uri".to_string(),
        ));
    }
    Ok((mode, Some(config)))
}

/// Return `None` for empty/whitespace strings, otherwise the trimmed string.
fn nonempty_or(value: Option<&Value>) -> Option<String> {
    let rendered = match value? {
        Value::String(text) => text.clone(),
        Value::Null => return None,
        other => other.to_string(),
    };
    let trimmed = rendered.trim().to_string();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed)
    }
}

fn config_nonempty(config: &ConfigMap, key: &str) -> Option<String> {
    nonempty_or(config.get(key))
}

/// First non-empty string among the candidates (`_select`).
fn select(values: &[Option<String>]) -> Option<String> {
    values.iter().flatten().find(|value| !value.is_empty()).cloned()
}

/// Validate an instance/owner identifier (`validate_storage_identity`).
pub fn validate_storage_identity(value: Option<&str>, field_name: &str) -> StoreResult<String> {
    let candidate = value.unwrap_or("").trim().to_casefold();
    let bytes = candidate.as_bytes();
    // ^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$ — 1..=64 characters.
    let valid = {
        let body_ok = |slice: &[u8]| {
            slice
                .iter()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-' || *b == b'_')
        };
        match bytes.len() {
            0 => false,
            1 => bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit(),
            len @ 2..=64 => {
                (bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit())
                    && (bytes[len - 1].is_ascii_lowercase() || bytes[len - 1].is_ascii_digit())
                    && body_ok(&bytes[1..len - 1])
            }
            _ => false,
        }
    };
    if !valid {
        // The Python error message is not an f-string for the `{value!r}`
        // tail; the literal text is reproduced for byte parity.
        return Err(StoreError::Value(format!(
            "{field_name} must be a stable lowercase slug containing only letters, digits, \
             '-' or '_' (1-64 characters); got {{value!r}}"
        )));
    }
    Ok(candidate)
}

/// The centralized per-account data root (`default_data_home`).
pub fn default_data_home() -> PathBuf {
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"));
    home.join(DEFAULT_DATA_DIRNAME)
}

/// Resolve the owner performance profile (`resolve_performance_profile`).
pub fn resolve_performance_profile(
    config: Option<&ConfigMap>,
    profile: Option<&str>,
) -> StoreResult<PerformanceProfile> {
    let empty = ConfigMap::new();
    let cfg = config.unwrap_or(&empty);
    let selected = select(&[
        profile.map(str::to_string),
        config_nonempty(cfg, ENV_PERFORMANCE_PROFILE),
        std::env::var(ENV_PERFORMANCE_PROFILE).ok(),
        Some("safe".to_string()),
    ])
    .unwrap_or_default()
    .to_casefold();
    if !matches!(selected.as_str(), "safe" | "balanced" | "custom") {
        return Err(StoreError::Value(
            "CORTEX_STORAGE_PROFILE must be safe, balanced, or custom".to_string(),
        ));
    }
    let mut values = PerformanceProfile {
        name: selected.clone(),
        ..PerformanceProfile::default()
    };
    if selected == "custom" {
        let field_map: [(&str, &str); 8] = [
            ("graph_readers", "CORTEX_STORAGE_GRAPH_READERS"),
            ("vector_readers", "CORTEX_STORAGE_VECTOR_READERS"),
            ("writer_slots", "CORTEX_STORAGE_WRITER_SLOTS"),
            ("control_slots", "CORTEX_STORAGE_CONTROL_SLOTS"),
            ("max_queue_items", "CORTEX_STORAGE_MAX_QUEUE_ITEMS"),
            ("max_queue_bytes", "CORTEX_STORAGE_MAX_QUEUE_BYTES"),
            (
                "request_timeout_seconds",
                "CORTEX_STORAGE_REQUEST_TIMEOUT_SECONDS",
            ),
            (
                "disk_safety_fraction",
                "CORTEX_STORAGE_DISK_SAFETY_FRACTION",
            ),
        ];
        let mut parsed: Map<String, Value> = Map::new();
        for (field_name, key) in field_map {
            let raw = select(&[
                config_nonempty(cfg, key),
                std::env::var(key).ok(),
            ]);
            let Some(raw) = raw else { continue };
            let is_float = matches!(field_name, "request_timeout_seconds" | "disk_safety_fraction");
            let parsed_value = if is_float {
                raw.parse::<f64>().map(Value::from).map_err(|_| ())
            } else {
                raw.parse::<i64>().map(Value::from).map_err(|_| ())
            };
            match parsed_value {
                Ok(value) => {
                    parsed.insert(field_name.to_string(), value);
                }
                Err(()) => {
                    return Err(StoreError::Value(format!("{key} is invalid: {raw:?}")));
                }
            }
        }
        apply_profile_overrides(&mut values, &parsed)?;
    }
    values.validate()?;
    Ok(values)
}

fn apply_profile_overrides(
    profile: &mut PerformanceProfile,
    parsed: &Map<String, Value>,
) -> StoreResult<()> {
    if let Some(value) = parsed.get("graph_readers").and_then(Value::as_i64) {
        profile.graph_readers = value;
    }
    if let Some(value) = parsed.get("vector_readers").and_then(Value::as_i64) {
        profile.vector_readers = value;
    }
    if let Some(value) = parsed.get("writer_slots").and_then(Value::as_i64) {
        profile.writer_slots = value;
    }
    if let Some(value) = parsed.get("control_slots").and_then(Value::as_i64) {
        profile.control_slots = value;
    }
    if let Some(value) = parsed.get("max_queue_items").and_then(Value::as_i64) {
        profile.max_queue_items = value;
    }
    if let Some(value) = parsed.get("max_queue_bytes").and_then(Value::as_i64) {
        profile.max_queue_bytes = value;
    }
    if let Some(value) = parsed
        .get("request_timeout_seconds")
        .and_then(Value::as_f64)
    {
        profile.request_timeout_seconds = value;
    }
    if let Some(value) = parsed.get("disk_safety_fraction").and_then(Value::as_f64) {
        profile.disk_safety_fraction = value;
    }
    Ok(())
}

/// Fully resolved storage paths (`ResolvedStorage`).
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedStorage {
    pub project_root: PathBuf,
    pub qdrant_base: PathBuf,
    pub qdrant_code_path: PathBuf,
    pub qdrant_doc_path: PathBuf,
    pub falkordb_path: PathBuf,
    pub code_graph: Option<String>,
    pub doc_graph: Option<String>,
    pub code_collection: Option<String>,
    pub doc_collection: Option<String>,
    pub data_root: PathBuf,
    pub schema_version: String,
    pub instance_id: String,
    pub code_owner_id: String,
    pub doc_owner_id: String,
    pub instance_root: PathBuf,
    pub falkordb_code_path: PathBuf,
    pub falkordb_doc_path: PathBuf,
    pub ladybug_code_path: PathBuf,
    pub ladybug_doc_path: PathBuf,
    pub manifest_path: PathBuf,
    pub backups_path: PathBuf,
    pub path_provenance: String,
    pub backend_mode: BackendMode,
    pub remote: Option<RemoteStorageConfig>,
    pub legacy_keys: Vec<String>,
}

impl ResolvedStorage {
    /// The original five-path compatibility shape (`__post_init__` derives
    /// the remaining fields from it).
    #[allow(clippy::too_many_arguments)]
    pub fn from_core(
        project_root: &Path,
        qdrant_base: &Path,
        qdrant_code_path: &Path,
        qdrant_doc_path: &Path,
        falkordb_path: &Path,
    ) -> Self {
        let data_root = qdrant_base
            .parent()
            .unwrap_or_else(|| Path::new("/"))
            .to_path_buf();
        let instance_root = data_root.clone();
        let falkor_code = falkordb_path.to_path_buf();
        let falkor_doc = falkordb_path
            .parent()
            .and_then(Path::parent)
            .map(|base| base.join("doc").join("data.rdb"))
            .unwrap_or_else(|| PathBuf::from("doc/data.rdb"));
        let ladybug_code = derive_ladybug_path(&instance_root, "code");
        let ladybug_doc = derive_ladybug_path(&instance_root, "doc");
        let instance_root_value = instance_root.clone();
        Self {
            project_root: project_root.to_path_buf(),
            qdrant_base: qdrant_base.to_path_buf(),
            qdrant_code_path: qdrant_code_path.to_path_buf(),
            qdrant_doc_path: qdrant_doc_path.to_path_buf(),
            falkordb_path: falkordb_path.to_path_buf(),
            code_graph: None,
            doc_graph: None,
            code_collection: None,
            doc_collection: None,
            data_root,
            schema_version: STORAGE_SCHEMA_VERSION.to_string(),
            instance_id: DEFAULT_INSTANCE_ID.to_string(),
            code_owner_id: StorageRole::Code.as_str().to_string(),
            doc_owner_id: StorageRole::Doc.as_str().to_string(),
            instance_root,
            falkordb_code_path: falkor_code,
            falkordb_doc_path: falkor_doc,
            ladybug_code_path: ladybug_code,
            ladybug_doc_path: ladybug_doc,
            manifest_path: instance_root_value.clone().join("manifest.json"),
            backups_path: instance_root_value.join("backups"),
            path_provenance: "derived-default".to_string(),
            backend_mode: BackendMode::Local,
            remote: None,
            legacy_keys: Vec::new(),
        }
    }

    pub fn has_legacy_keys(&self) -> bool {
        !self.legacy_keys.is_empty()
    }

    pub fn qdrant_path(&self) -> &Path {
        &self.qdrant_code_path
    }

    fn role_slugs(role: &str) -> StoreResult<String> {
        crate::targets::role_value(RoleInput::Str(role.to_string()))
    }

    pub fn path_for_role(&self, role: &str) -> StoreResult<&Path> {
        match Self::role_slugs(role)?.as_str() {
            "code" => Ok(&self.qdrant_code_path),
            "doc" => Ok(&self.qdrant_doc_path),
            _ => Err(StoreError::Value(format!(
                "Unknown storage role: {role:?}"
            ))),
        }
    }

    pub fn falkordb_path_for_role(&self, role: &str) -> StoreResult<PathBuf> {
        match Self::role_slugs(role)?.as_str() {
            "code" => Ok(self.falkordb_code_path.clone()),
            "doc" => Ok(self.falkordb_doc_path.clone()),
            _ => Err(StoreError::Value(format!(
                "Unknown storage role: {role:?}"
            ))),
        }
    }

    pub fn ladybug_path_for_role(&self, role: &str) -> StoreResult<PathBuf> {
        match Self::role_slugs(role)?.as_str() {
            "code" => Ok(self.ladybug_code_path.clone()),
            "doc" => Ok(self.ladybug_doc_path.clone()),
            _ => Err(StoreError::Value(format!(
                "Unknown storage role: {role:?}"
            ))),
        }
    }

    pub fn ensure_directories(&self) -> std::io::Result<()> {
        std::fs::create_dir_all(&self.qdrant_code_path)?;
        std::fs::create_dir_all(&self.qdrant_doc_path)?;
        std::fs::create_dir_all(
            self.falkordb_code_path
                .parent()
                .unwrap_or_else(|| Path::new("/")),
        )?;
        std::fs::create_dir_all(
            self.falkordb_doc_path
                .parent()
                .unwrap_or_else(|| Path::new("/")),
        )?;
        std::fs::create_dir_all(
            self.ladybug_code_path
                .parent()
                .unwrap_or_else(|| Path::new("/")),
        )?;
        std::fs::create_dir_all(
            self.ladybug_doc_path
                .parent()
                .unwrap_or_else(|| Path::new("/")),
        )?;
        std::fs::create_dir_all(&self.backups_path)?;
        Ok(())
    }
}

/// Default LadybugDB store for an owner's primary graph
/// (`ResolvedStorage._derive_ladybug_path`).
pub fn derive_ladybug_path(instance_root: &Path, owner_id: &str) -> PathBuf {
    layout::derive_ladybug_store_path(
        instance_root.join("ladybug").join(owner_id),
        owner_id,
    )
}

/// Optional CLI-level overrides for `resolve_storage`.
#[derive(Debug, Clone, Default)]
pub struct ResolveOverrides {
    pub data_home: Option<String>,
    pub instance_id: Option<String>,
    pub code_owner_id: Option<String>,
    pub doc_owner_id: Option<String>,
    pub qdrant_base: Option<String>,
    pub qdrant_code_path: Option<String>,
    pub qdrant_doc_path: Option<String>,
    pub falkordb_path: Option<String>,
    pub falkordb_code_path: Option<String>,
    pub falkordb_doc_path: Option<String>,
    pub ladybug_path: Option<String>,
    pub ladybug_code_path: Option<String>,
    pub ladybug_doc_path: Option<String>,
    pub graph_provider: Option<String>,
    pub code_graph: Option<String>,
    pub doc_graph: Option<String>,
    pub code_collection: Option<String>,
    pub doc_collection: Option<String>,
}

fn resolve_override(value: &str, project_root: &Path) -> PathBuf {
    let expanded = crate::util::expanduser(Path::new(value));
    if expanded.is_absolute() {
        crate::util::resolve_path(&expanded)
    } else {
        crate::util::resolve_path(&project_root.join(expanded))
    }
}

fn legacy_keys(config: &ConfigMap) -> Vec<String> {
    LEGACY_REMOTE_KEYS
        .iter()
        .filter(|key| config_nonempty(config, key).is_some())
        .map(|key| (*key).to_string())
        .collect()
}

/// Resolve local paths using CLI > config > environment > derived default
/// (`resolve_storage`).
pub fn resolve_storage(
    project_root: &Path,
    config: Option<&ConfigMap>,
    overrides: &ResolveOverrides,
) -> StoreResult<ResolvedStorage> {
    let root = crate::util::resolve_path(project_root);
    let empty = ConfigMap::new();
    let cfg = config.unwrap_or(&empty);
    let legacy = legacy_keys(cfg);
    let backend_raw = config_nonempty(cfg, "storage_backend").unwrap_or_else(|| "local".to_string());
    let mut provider_raw = config_nonempty(cfg, "graph_provider");
    if provider_raw.is_none() {
        provider_raw = overrides.graph_provider.clone();
    }
    let remote_section = cfg.get("remote").and_then(Value::as_object);
    let (backend_mode, remote_config) =
        validate_backend_config(&backend_raw, remote_section, provider_raw.as_deref().unwrap_or("falkordb"))?;

    let local_keys_present = LOCAL_CFG_KEYS
        .iter()
        .any(|key| config_nonempty(cfg, key).is_some());
    let mode = if local_keys_present {
        "mixed local/remote"
    } else {
        "remote-only"
    };
    if backend_mode == BackendMode::Remote && !legacy.is_empty() {
        return Err(StoreError::Value(format!(
            "{mode} database configuration is unsupported for the local runtime: {}. Remove \
             endpoint/credential fields or move them to the 'remote' section of your project \
             config.",
            legacy.join(", ")
        )));
    }
    if !legacy.is_empty() {
        return Err(StoreError::Value(format!(
            "{mode} database configuration is unsupported for the local runtime: {}. Export or \
             re-ingest remote data, remove endpoint/credential fields, then configure \
             CORTEX_DATA_HOME or owner-specific local paths.",
            legacy.join(", ")
        )));
    }

    let data_raw = select(&[
        overrides.data_home.clone(),
        config_nonempty(cfg, ENV_DATA_HOME),
        std::env::var(ENV_DATA_HOME).ok(),
    ]);
    let (data_root, provenance) = match data_raw {
        Some(raw) => {
            let expanded = crate::util::expanduser(Path::new(&raw));
            if expanded.is_absolute() {
                (crate::util::resolve_path(&expanded), "explicit-absolute-override")
            } else {
                // Relative names are anchored under the account data home so
                // siblings stay co-located (never trapped inside source trees).
                (
                    crate::util::resolve_path(&default_data_home().join(expanded)),
                    "explicit-relative-anchored-to-home",
                )
            }
        }
        None => (crate::util::resolve_path(&default_data_home()), "account-home-default"),
    };

    let instance = validate_storage_identity(
        select(&[
            overrides.instance_id.clone(),
            config_nonempty(cfg, ENV_INSTANCE),
            std::env::var(ENV_INSTANCE).ok(),
            Some(DEFAULT_INSTANCE_ID.to_string()),
        ])
        .as_deref(),
        "instance_id",
    )?;
    let code_owner = validate_storage_identity(
        select(&[
            overrides.code_owner_id.clone(),
            config_nonempty(cfg, ENV_CODE_OWNER),
            std::env::var(ENV_CODE_OWNER).ok(),
            Some(StorageRole::Code.as_str().to_string()),
        ])
        .as_deref(),
        "code_owner_id",
    )?;
    let doc_owner = validate_storage_identity(
        select(&[
            overrides.doc_owner_id.clone(),
            config_nonempty(cfg, ENV_DOC_OWNER),
            std::env::var(ENV_DOC_OWNER).ok(),
            Some(StorageRole::Doc.as_str().to_string()),
        ])
        .as_deref(),
        "doc_owner_id",
    )?;
    if code_owner == doc_owner {
        return Err(StoreError::Value(
            "code_owner_id and doc_owner_id must be distinct".to_string(),
        ));
    }

    let instance_root = data_root
        .join(STORAGE_SCHEMA_VERSION)
        .join("instances")
        .join(&instance);
    let q_base_raw = select(&[
        overrides.qdrant_base.clone(),
        config_nonempty(cfg, ENV_QDRANT_BASE),
        std::env::var(ENV_QDRANT_BASE).ok(),
    ]);
    let q_base = match &q_base_raw {
        Some(raw) => resolve_override(raw, &root),
        None => instance_root.join("qdrant"),
    };
    let q_code_raw = select(&[
        overrides.qdrant_code_path.clone(),
        config_nonempty(cfg, ENV_QDRANT_CODE),
        std::env::var(ENV_QDRANT_CODE).ok(),
    ]);
    let q_doc_raw = select(&[
        overrides.qdrant_doc_path.clone(),
        config_nonempty(cfg, ENV_QDRANT_DOC),
        std::env::var(ENV_QDRANT_DOC).ok(),
    ]);
    let q_code = match &q_code_raw {
        Some(raw) => resolve_override(raw, &root),
        None => q_base.join(&code_owner),
    };
    let q_doc = match &q_doc_raw {
        Some(raw) => resolve_override(raw, &root),
        None => q_base.join(&doc_owner),
    };

    let shared_falkor = select(&[
        overrides.falkordb_path.clone(),
        config_nonempty(cfg, ENV_FALKORDB_PATH),
        std::env::var(ENV_FALKORDB_PATH).ok(),
    ]);
    let f_code_raw = select(&[
        overrides.falkordb_code_path.clone(),
        config_nonempty(cfg, ENV_FALKORDB_CODE),
        std::env::var(ENV_FALKORDB_CODE).ok(),
        shared_falkor.clone(),
    ]);
    let f_doc_raw = select(&[
        overrides.falkordb_doc_path.clone(),
        config_nonempty(cfg, ENV_FALKORDB_DOC),
        std::env::var(ENV_FALKORDB_DOC).ok(),
    ]);
    let f_code = match &f_code_raw {
        Some(raw) => resolve_override(raw, &root),
        None => instance_root.join("falkordb").join(&code_owner).join("data.rdb"),
    };
    let f_doc = match &f_doc_raw {
        Some(raw) => resolve_override(raw, &root),
        None => instance_root.join("falkordb").join(&doc_owner).join("data.rdb"),
    };

    let shared_ladybug = select(&[
        overrides.ladybug_path.clone(),
        config_nonempty(cfg, ENV_LADYBUG_PATH),
        std::env::var(ENV_LADYBUG_PATH).ok(),
    ]);
    let lb_code_raw = select(&[
        overrides.ladybug_code_path.clone(),
        config_nonempty(cfg, ENV_LADYBUG_CODE),
        std::env::var(ENV_LADYBUG_CODE).ok(),
        shared_ladybug.clone(),
    ]);
    let lb_doc_raw = select(&[
        overrides.ladybug_doc_path.clone(),
        config_nonempty(cfg, ENV_LADYBUG_DOC),
        std::env::var(ENV_LADYBUG_DOC).ok(),
    ]);
    let lb_code = match &lb_code_raw {
        Some(raw) => resolve_override(raw, &root),
        None => derive_ladybug_path(&instance_root, &code_owner),
    };
    let lb_doc = match &lb_doc_raw {
        Some(raw) => resolve_override(raw, &root),
        None => derive_ladybug_path(&instance_root, &doc_owner),
    };
    let manifest_path_value = instance_root.join("manifest.json");
    let backups_path_value = instance_root.join("backups");

    Ok(ResolvedStorage {
        project_root: root,
        qdrant_base: q_base,
        qdrant_code_path: q_code,
        qdrant_doc_path: q_doc,
        falkordb_path: f_code.clone(),
        code_graph: overrides.code_graph.clone(),
        doc_graph: overrides.doc_graph.clone(),
        code_collection: overrides.code_collection.clone(),
        doc_collection: overrides.doc_collection.clone(),
        data_root,
        schema_version: STORAGE_SCHEMA_VERSION.to_string(),
        instance_id: instance,
        code_owner_id: code_owner,
        doc_owner_id: doc_owner,
        instance_root,
        falkordb_code_path: f_code,
        falkordb_doc_path: f_doc,
        ladybug_code_path: lb_code,
        ladybug_doc_path: lb_doc,
        manifest_path: manifest_path_value,
        backups_path: backups_path_value,
        path_provenance: provenance.to_string(),
        backend_mode,
        remote: remote_config,
        legacy_keys: legacy,
    })
}

/// Build the child-process environment overlay (`storage_overlay`).
///
/// Returns an ordered (env key, value) map; `None` values represent keys the
/// Python version would delete from an inherited environment.
#[allow(clippy::too_many_arguments)]
pub fn storage_overlay(
    resolved: &ResolvedStorage,
    owner: &str,
    graph_provider: &str,
    code_collection: Option<&str>,
    doc_collection: Option<&str>,
    code_graph: Option<&str>,
    doc_graph: Option<&str>,
) -> StoreResult<BTreeMap<String, String>> {
    let selected_raw = crate::targets::role_value(RoleInput::Str(owner.to_string()))?;
    let selected = selected_raw.as_str();
    let mut provider = graph_provider.trim().to_casefold();
    if matches!(provider.as_str(), "kuzu" | "lbug" | "lady-bug") {
        provider = "ladybug".to_string();
    }
    let mut overlay: BTreeMap<String, String> = BTreeMap::new();
    overlay.insert(ENV_DATA_HOME.to_string(), resolved.data_root.to_string_lossy().into_owned());
    overlay.insert(ENV_INSTANCE.to_string(), resolved.instance_id.clone());
    overlay.insert(ENV_CODE_OWNER.to_string(), resolved.code_owner_id.clone());
    overlay.insert(ENV_DOC_OWNER.to_string(), resolved.doc_owner_id.clone());
    overlay.insert(ENV_QDRANT_BASE.to_string(), resolved.qdrant_base.to_string_lossy().into_owned());
    overlay.insert(ENV_QDRANT_CODE.to_string(), resolved.qdrant_code_path.to_string_lossy().into_owned());
    overlay.insert(ENV_QDRANT_DOC.to_string(), resolved.qdrant_doc_path.to_string_lossy().into_owned());
    overlay.insert(ENV_FALKORDB_CODE.to_string(), resolved.falkordb_code_path.to_string_lossy().into_owned());
    overlay.insert(ENV_FALKORDB_DOC.to_string(), resolved.falkordb_doc_path.to_string_lossy().into_owned());
    overlay.insert(
        ENV_FALKORDB_PATH.to_string(),
        resolved
            .falkordb_path_for_role(selected)?
            .to_string_lossy()
            .into_owned(),
    );
    overlay.insert(ENV_LADYBUG_CODE.to_string(), resolved.ladybug_code_path.to_string_lossy().into_owned());
    overlay.insert(ENV_LADYBUG_DOC.to_string(), resolved.ladybug_doc_path.to_string_lossy().into_owned());
    overlay.insert(ENV_CORTEX_STORAGE_OWNER.to_string(), selected.to_string());

    if provider == "ladybug" {
        // Ladybug is embedded-only: point the active store at the resolved
        // Ladybug file and drop the embedded-FalkorDB path override.
        overlay.remove(ENV_FALKORDB_PATH);
        overlay.insert(
            ENV_LADYBUG_PATH.to_string(),
            resolved
                .ladybug_path_for_role(selected)?
                .to_string_lossy()
                .into_owned(),
        );
        overlay.insert(ENV_GRAPH_PROVIDER.to_string(), "ladybug".to_string());
    }
    let force_local =
        environment_flag_enabled(std::env::var("CORTEX_STORAGE_BACKEND_FORCE_LOCAL").ok().as_deref());
    if resolved.backend_mode == BackendMode::Remote && !force_local
        && let Some(remote) = &resolved.remote {
            if let Some(qdrant_url) = &remote.qdrant_url {
                overlay.insert(ENV_QDRANT_URL.to_string(), qdrant_url.clone());
                if let Some(api_key) = &remote.qdrant_api_key {
                    overlay.insert(ENV_QDRANT_API_KEY.to_string(), api_key.clone());
                }
                // Analyzer --qdrant-url defaults read QDRANT_CODE_PATH;
                // override with the remote URL so embedding targets it.
                overlay.insert(ENV_QDRANT_CODE.to_string(), qdrant_url.clone());
                overlay.insert(ENV_QDRANT_DOC.to_string(), qdrant_url.clone());
            }
            if let Some(falkordb_uri) = &remote.falkordb_uri {
                overlay.insert(ENV_FALKORDB_URI.to_string(), falkordb_uri.clone());
                overlay.remove(ENV_LADYBUG_PATH);
                overlay.remove(ENV_FALKORDB_PATH);
                overlay.remove(ENV_FALKORDB_CODE);
                overlay.remove(ENV_FALKORDB_DOC);
                if let Some(password) = &remote.falkordb_password { // sensitive-guard:allow (ten flag / test sample)
                    overlay.insert(ENV_FALKORDB_PASSWORD.to_string(), password.clone()); // sensitive-guard:allow (ten flag / test sample)
                }
                if remote.falkordb_ssl {
                    overlay.insert(ENV_FALKORDB_SSL.to_string(), "1".to_string());
                }
            }
        }
    if let Some(collection) = code_collection.or(resolved.code_collection.as_deref()) {
        overlay.insert(ENV_QDRANT_COLLECTION.to_string(), collection.to_string());
        overlay.insert(
            ENV_QDRANT_COLLECTION_CODE.to_string(),
            collection.to_string(),
        );
    }
    if let Some(collection) = doc_collection.or(resolved.doc_collection.as_deref()) {
        overlay.insert(ENV_QDRANT_COLLECTION_DOC.to_string(), collection.to_string());
    }
    if let Some(graph) = code_graph.or(resolved.code_graph.as_deref()) {
        overlay.insert(ENV_FALKORDB_GRAPH.to_string(), graph.to_string());
    }
    if let Some(graph) = doc_graph.or(resolved.doc_graph.as_deref()) {
        overlay.insert(ENV_DOC_FALKORDB_GRAPH.to_string(), graph.to_string());
        if selected == "doc" {
            overlay.insert(ENV_FALKORDB_GRAPH.to_string(), graph.to_string());
        }
    }

    // Propagate resolved, credential-free descriptors for journal setup.
    let mut graph_name = overlay.get(ENV_FALKORDB_GRAPH).cloned();
    if provider == "ladybug" {
        graph_name = overlay
            .get(ENV_LADYBUG_GRAPH)
            .cloned()
            .or_else(|| overlay.get(ENV_FALKORDB_GRAPH).cloned());
    }
    let collection_name = if selected == "doc" {
        overlay.get(ENV_QDRANT_COLLECTION_DOC).cloned()
    } else {
        overlay.get(ENV_QDRANT_COLLECTION).cloned()
    };
    if let (Some(graph_name), Some(collection_name)) = (graph_name, collection_name)
        && matches!(
            provider.as_str(),
            "falkor" | "falkordb" | "ladybug" | "local" | "embedded"
        ) {
            let factory = crate::factory::StorageFactory::new(
                resolved.backend_mode,
                resolved,
                resolved.remote.clone(),
                "unbound",
                &provider,
                code_graph.map(str::to_string),
                doc_graph.map(str::to_string),
                code_collection.map(str::to_string),
                doc_collection.map(str::to_string),
            )?;
            let topology = factory.effective_topology(
                Some(&graph_name),
                Some(&collection_name),
                selected,
                "unbound",
                None,
            )?;
            overlay.insert(
                crate::targets::ENV_EFFECTIVE_GRAPH_TARGET.to_string(),
                topology.graph.canonical_json(),
            );
            overlay.insert(
                crate::targets::ENV_EFFECTIVE_GRAPH_FINGERPRINT.to_string(),
                topology.graph_fingerprint(),
            );
            overlay.insert(
                crate::targets::ENV_EFFECTIVE_VECTOR_TARGET.to_string(),
                topology.vector.canonical_json(),
            );
            overlay.insert(
                crate::targets::ENV_EFFECTIVE_VECTOR_FINGERPRINT.to_string(),
                topology.vector_fingerprint(),
            );
            overlay.insert(
                crate::targets::ENV_EFFECTIVE_TOPOLOGY.to_string(),
                topology.canonical_json(),
            );
            overlay.insert(
                crate::targets::ENV_EFFECTIVE_TOPOLOGY_FINGERPRINT.to_string(),
                topology.fingerprint(),
            );
        }
    Ok(overlay)
}

/// `env_to_config` — an ordered (key, value) list into a config map.
pub fn env_to_config(env: &[(String, Value)]) -> ConfigMap {
    env.iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

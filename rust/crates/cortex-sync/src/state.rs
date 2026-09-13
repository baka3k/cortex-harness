//! Port of `tools/common/incremental_sync_state.py` — the v2 sync state
//! document shared across runs (the incremental baseline).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use serde_json::json;

pub const STATE_SCHEMA_VERSION: i64 = 2;

#[derive(Debug, Clone, Default)]
pub struct IncrementalSyncState {
    pub project_id: String,
    pub root: String,
    pub schema_version: i64,
    pub last_good_sha: String,
    pub dirty: bool,
    pub last_error: String,
    pub last_run_before: String,
    pub last_run_after: String,
    pub updated_at: String,
    pub snapshot_id: String,
    pub inventory_path: String,
    pub dirty_inventory_paths: Vec<String>,
    pub repositories: BTreeMap<String, BTreeMap<String, serde_json::Value>>,
    pub working_tree_paths: Vec<String>,
    pub filter_version: i64,
    pub migration_required: bool,
    pub migrated_from: Option<i64>,
}

impl IncrementalSyncState {
    fn fresh(project_id: &str, root: &str) -> IncrementalSyncState {
        IncrementalSyncState {
            project_id: project_id.to_string(),
            root: crate::util::path_to_string(&crate::util::realpath(root)),
            schema_version: STATE_SCHEMA_VERSION,
            filter_version: 1,
            ..Default::default()
        }
    }

    /// `from_dict` — includes legacy schema migration detection.
    pub fn from_dict(data: &serde_json::Value, project_id: &str, root: &str) -> IncrementalSyncState {
        let raw_schema = data.get("schema_version").and_then(|v| v.as_i64());
        let mut migrated_from: Option<i64> = None;
        let mut migration_required = false;
        match raw_schema {
            None => {
                migrated_from = Some(1);
                migration_required = !data.as_object().map(|m| m.is_empty()).unwrap_or(true);
            }
            Some(version) if version != STATE_SCHEMA_VERSION => {
                migrated_from = Some(version);
                migration_required = true;
            }
            _ => {}
        }
        let string_at = |key: &str| {
            data.get(key)
                .map(|v| match v {
                    serde_json::Value::String(text) => text.clone(),
                    serde_json::Value::Null => String::new(),
                    other => other.to_string(),
                })
                .unwrap_or_default()
        };
        let state = IncrementalSyncState {
            project_id: project_id.to_string(),
            root: crate::util::path_to_string(&crate::util::realpath(root)),
            schema_version: STATE_SCHEMA_VERSION,
            last_good_sha: string_at("last_good_sha"),
            dirty: data.get("dirty").and_then(|v| v.as_bool()).unwrap_or(false),
            last_error: string_at("last_error"),
            last_run_before: string_at("last_run_before"),
            last_run_after: string_at("last_run_after"),
            updated_at: string_at("updated_at"),
            snapshot_id: string_at("snapshot_id"),
            inventory_path: string_at("inventory_path"),
            dirty_inventory_paths: data
                .get("dirty_inventory_paths")
                .and_then(|v| v.as_array())
                .map(|items| items.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
                .unwrap_or_default(),
            repositories: data
                .get("repositories")
                .and_then(|v| v.as_object())
                .map(|map| {
                    map.iter()
                        .filter_map(|(key, value)| {
                            let inner = value.as_object()?;
                            Some((
                                key.clone(),
                                inner
                                    .iter()
                                    .map(|(ik, iv)| (ik.clone(), iv.clone()))
                                    .collect::<BTreeMap<_, _>>(),
                            ))
                        })
                        .collect()
                })
                .unwrap_or_default(),
            working_tree_paths: data
                .get("working_tree_paths")
                .and_then(|v| v.as_array())
                .map(|items| items.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
                .unwrap_or_default(),
            filter_version: data.get("filter_version").and_then(|v| v.as_i64()).unwrap_or(1),
            migration_required: migration_required
                || data
                    .get("migration_required")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
            migrated_from: migrated_from.or_else(|| {
                data.get("migrated_from").and_then(|v| v.as_i64())
            }),
        };
        state
    }

    /// `to_dict`.
    pub fn to_dict(&self) -> serde_json::Value {
        let dirty_sorted: Vec<String> = {
            let set: BTreeSet<&String> = self.dirty_inventory_paths.iter().collect();
            set.into_iter().cloned().collect()
        };
        let worktree_sorted: Vec<String> = {
            let set: BTreeSet<&String> = self.working_tree_paths.iter().collect();
            set.into_iter().cloned().collect()
        };
        json!({
            "schema_version": STATE_SCHEMA_VERSION,
            "project_id": self.project_id,
            "root": self.root,
            "last_good_sha": self.last_good_sha,
            "dirty": self.dirty,
            "last_error": self.last_error,
            "last_run_before": self.last_run_before,
            "last_run_after": self.last_run_after,
            "updated_at": self.updated_at,
            "snapshot_id": self.snapshot_id,
            "inventory_path": self.inventory_path,
            "dirty_inventory_paths": dirty_sorted,
            "repositories": self.repositories,
            "working_tree_paths": worktree_sorted,
            "filter_version": self.filter_version,
            "migration_required": self.migration_required,
            "migrated_from": self.migrated_from,
        })
    }
}

/// `load_sync_state`.
pub fn load_sync_state(path: &Path, project_id: &str, root: &str) -> IncrementalSyncState {
    if !path.exists() {
        return IncrementalSyncState::fresh(project_id, root);
    }
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let data: serde_json::Value = if text.trim().is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::from_str(&text).unwrap_or(serde_json::Value::Null)
    };
    if !data.is_object() {
        return IncrementalSyncState::fresh(project_id, root);
    }
    IncrementalSyncState::from_dict(&data, project_id, root)
}

/// `save_sync_state` — atomic replace with `.tmp` suffix.
pub fn save_sync_state(path: &Path, state: &IncrementalSyncState) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let temp = path.with_extension("json.tmp");
    let text = format!("{}\n", serde_json::to_string_pretty(&state.to_dict()).unwrap());
    std::fs::write(&temp, text)?;
    std::fs::rename(&temp, path)
}

/// `backup_legacy_state`.
pub fn backup_legacy_state(path: &Path, state: &IncrementalSyncState) -> String {
    if !state.migration_required {
        return String::new();
    }
    let suffix = state.migrated_from.unwrap_or(1);
    let original = path.to_string_lossy().to_string();
    let backup = pathbuf_from(format!("{original}.v{suffix}.bak"));
    if path.exists() && !backup.exists() {
        if let Some(parent) = backup.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let temp = pathbuf_from(format!("{original}.v{suffix}.bak.tmp"));
        if std::fs::copy(path, &temp).is_ok() {
            std::fs::rename(&temp, &backup).ok();
        }
    }
    backup.to_string_lossy().to_string()
}

fn pathbuf_from(value: String) -> std::path::PathBuf {
    std::path::PathBuf::from(value)
}

/// `mark_dirty`.
#[allow(clippy::too_many_arguments)]
pub fn mark_dirty(
    path: &Path,
    state: &mut IncrementalSyncState,
    error: &str,
    before_sha: &str,
    after_sha: &str,
    dirty_inventory_path: Option<&str>,
) -> std::io::Result<()> {
    state.dirty = true;
    state.last_error = error.to_string();
    state.last_run_before = before_sha.to_string();
    state.last_run_after = after_sha.to_string();
    if let Some(inventory) = dirty_inventory_path {
        let mut set: BTreeSet<String> = state.dirty_inventory_paths.iter().cloned().collect();
        set.insert(inventory.to_string());
        state.dirty_inventory_paths = set.into_iter().collect();
    }
    state.updated_at = crate::util::now_iso_micros();
    save_sync_state(path, state)
}

/// `mark_clean`.
#[allow(clippy::too_many_arguments)]
pub fn mark_clean(
    path: &Path,
    state: &mut IncrementalSyncState,
    last_good_sha: &str,
    before_sha: &str,
    after_sha: &str,
    snapshot_id: Option<&str>,
    inventory_path: Option<&str>,
    repositories: Option<BTreeMap<String, BTreeMap<String, serde_json::Value>>>,
    working_tree_paths: Option<Vec<String>>,
    filter_version: i64,
) -> std::io::Result<()> {
    state.dirty = false;
    state.dirty_inventory_paths = vec![];
    state.last_error = String::new();
    state.last_good_sha = last_good_sha.to_string();
    state.last_run_before = before_sha.to_string();
    state.last_run_after = after_sha.to_string();
    if let Some(snapshot) = snapshot_id {
        state.snapshot_id = snapshot.to_string();
    }
    if let Some(inventory) = inventory_path {
        state.inventory_path = inventory.to_string();
    }
    if let Some(repos) = repositories {
        state.repositories = repos;
    }
    if let Some(paths) = working_tree_paths {
        state.working_tree_paths = paths;
    }
    state.filter_version = filter_version;
    state.migration_required = false;
    state.updated_at = crate::util::now_iso_micros();
    save_sync_state(path, state)?;
    Ok(())
}


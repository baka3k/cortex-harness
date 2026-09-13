//! Versioned instance-layout and manifest helpers.
//!
//! Port of `cortex_harness.storage.layout`: the `~/.cortext-harness/v1/
//! instances/<instance>/` tree and Ladybug store-file naming.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::config::ResolvedStorage;
use crate::errors::{StoreError, StoreResult};
use crate::util::utc_now_seconds;

const WINDOWS_RESERVED_DEVICE_NAMES: [&str; 22] = [
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
    "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];
pub const LADYBUG_STORE_SUFFIX: &str = ".lbug";

fn has_forbidden_name_characters(name: &str) -> bool {
    name.chars()
        .any(|c| !(c.is_ascii_alphanumeric() || c == '_' || c == '-' || c == '.'))
}

/// Sanitized store file name for one named graph (`ladybug_store_file_name`).
///
/// A graph name becomes a store **file** name (Ladybug keeps an entire
/// database in one file). Names that cannot map cleanly fail closed.
pub fn ladybug_store_file_name(graph_name: &str) -> StoreResult<String> {
    let name = graph_name.trim();
    if name.is_empty() {
        return Err(StoreError::Value(
            "Ladybug graph name must not be empty".to_string(),
        ));
    }
    if has_forbidden_name_characters(name) {
        return Err(StoreError::Value(format!(
            "Ladybug graph name {graph_name:?} must only contain letters, digits, '_', '-', or \
             '.' (it becomes a store file name)"
        )));
    }
    let mut cleaned = name.to_lowercase();
    while cleaned.ends_with('.') {
        cleaned.pop();
    }
    if cleaned.is_empty() || cleaned == "." || cleaned == ".." {
        return Err(StoreError::Value(format!(
            "Ladybug graph name {graph_name:?} is not a valid store name"
        )));
    }
    let stem = cleaned.split_once('.').map_or(cleaned.as_str(), |(stem, _)| stem);
    if WINDOWS_RESERVED_DEVICE_NAMES.contains(&stem.to_uppercase().as_str()) {
        return Err(StoreError::Value(format!(
            "Ladybug graph name {graph_name:?} collides with a Windows reserved device name and \
             cannot be used as a store file name"
        )));
    }
    Ok(cleaned)
}

/// Directory holding every Ladybug store of one owner.
pub fn ladybug_owner_store_dir(root: impl AsRef<Path>, owner_role: &str) -> PathBuf {
    root.as_ref()
        .join(format!("{owner_role}{LADYBUG_STORE_SUFFIX}"))
}

/// Canonical LadybugDB store file for one named graph (`ladybug_graph_path`).
pub fn ladybug_graph_path(
    root: impl AsRef<Path>,
    owner_role: &str,
    graph_name: &str,
) -> StoreResult<PathBuf> {
    Ok(ladybug_owner_store_dir(root, owner_role)
        .join(ladybug_store_file_name(graph_name)?))
}

/// Derivation used by `ResolvedStorage`; the default graph name is always a
/// valid store name, so this is infallible in practice.
pub(crate) fn derive_ladybug_store_path(root: impl AsRef<Path>, owner_role: &str) -> PathBuf {
    let file = ladybug_store_file_name(crate::config::DEFAULT_LADYBUG_GRAPH)
        .unwrap_or_else(|_| crate::config::DEFAULT_LADYBUG_GRAPH.to_string());
    ladybug_owner_store_dir(root, owner_role).join(file)
}

/// Instance manifest payload (`manifest_payload`).
pub fn manifest_payload(resolved: &ResolvedStorage, created_at: Option<&str>) -> Value {
    let mut owners: BTreeMap<String, Value> = BTreeMap::new();
    owners.insert(
        resolved.code_owner_id.clone(),
        json!({
            "qdrant_path": resolved.qdrant_code_path.to_string_lossy(),
            "falkordb_path": resolved.falkordb_code_path.to_string_lossy(),
        }),
    );
    owners.insert(
        resolved.doc_owner_id.clone(),
        json!({
            "qdrant_path": resolved.qdrant_doc_path.to_string_lossy(),
            "falkordb_path": resolved.falkordb_doc_path.to_string_lossy(),
        }),
    );
    json!({
        "schema_version": resolved.schema_version,
        "instance_id": resolved.instance_id,
        "created_at": created_at.map(str::to_string).unwrap_or_else(utc_now_seconds),
        "path_provenance": resolved.path_provenance,
        "owners": Value::Object(owners.into_iter().collect::<Map<String, Value>>()),
    })
}

/// Load the on-disk instance manifest, if present (`load_manifest`).
pub fn load_manifest(resolved: &ResolvedStorage) -> StoreResult<Option<Value>> {
    let path = &resolved.manifest_path;
    if !path.is_file() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(path)
        .map_err(|err| StoreError::Value(format!("cannot read storage manifest: {err}")))?;
    let payload: Value = serde_json::from_str(&text).map_err(|err| {
        StoreError::Value(format!("Invalid storage manifest at {path:?}: {err}"))
    })?;
    if !payload.is_object() {
        return Err(StoreError::Value(format!(
            "Invalid storage manifest at {}: expected object",
            path.display()
        )));
    }
    Ok(Some(payload))
}

fn manifest_drift(message: &str, resolved: &ResolvedStorage) -> StoreError {
    StoreError::Value(format!(
        "{message} at {}. Refusing to reinterpret existing embedded storage. Restore the \
         matching CORTEX_STORAGE_INSTANCE/owner/path configuration, or run \
         'storage-migrate-layout' to move data into the new layout.",
        resolved.manifest_path.display()
    ))
}

fn validate_manifest_layout(existing: &Value, resolved: &ResolvedStorage) -> StoreResult<()> {
    let Some(owners) = existing.get("owners").and_then(Value::as_object) else {
        return Err(manifest_drift(
            &format!(
                "Storage owner identity drift: manifest owners {:?} do not match configured \
                 owners {:?}",
                Value::Null,
                sorted(&[resolved.code_owner_id.clone(), resolved.doc_owner_id.clone()])
            ),
            resolved,
        ));
    };
    let mut expected_owner_names = vec![resolved.code_owner_id.clone(), resolved.doc_owner_id.clone()];
    expected_owner_names.sort();
    let mut actual_owner_names: Vec<&String> = owners.keys().collect();
    actual_owner_names.sort();
    if actual_owner_names.len() != expected_owner_names.len()
        || actual_owner_names
            .iter()
            .zip(&expected_owner_names)
            .any(|(actual, expected)| actual.as_str() != expected.as_str())
    {
        let actual_json: Vec<String> =
            actual_owner_names.iter().map(|name| name.to_string()).collect();
        return Err(manifest_drift(
            &format!(
                "Storage owner identity drift: manifest owners {:?} do not match configured \
                 owners {:?}",
                actual_json, expected_owner_names
            ),
            resolved,
        ));
    }
    let expected_paths = [
        (
            resolved.code_owner_id.clone(),
            resolved.qdrant_code_path.clone(),
            resolved.falkordb_code_path.clone(),
        ),
        (
            resolved.doc_owner_id.clone(),
            resolved.qdrant_doc_path.clone(),
            resolved.falkordb_doc_path.clone(),
        ),
    ];
    for (owner, qdrant_expected, falkordb_expected) in expected_paths {
        let Some(configured) = owners.get(&owner).and_then(Value::as_object) else {
            return Err(manifest_drift(
                &format!(
                    "Storage owner identity drift: manifest entry for {owner:?} is invalid"
                ),
                resolved,
            ));
        };
        let expected_fields = [
            ("qdrant_path", qdrant_expected),
            ("falkordb_path", falkordb_expected),
        ];
        for (field, expected_path) in expected_fields {
            let Some(raw_path) = configured.get(field).and_then(Value::as_str) else {
                return Err(manifest_drift(
                    &format!(
                        "Storage path drift: manifest {owner}.{field} is missing or invalid"
                    ),
                    resolved,
                ));
            };
            if raw_path.trim().is_empty() {
                return Err(manifest_drift(
                    &format!(
                        "Storage path drift: manifest {owner}.{field} is missing or invalid"
                    ),
                    resolved,
                ));
            }
            let manifest_path = crate::util::expanduser(Path::new(raw_path));
            let canonical_path = crate::util::resolve_path(&manifest_path);
            if !manifest_path.is_absolute()
                || manifest_path.to_string_lossy() != canonical_path.to_string_lossy()
                || canonical_path != expected_path
            {
                return Err(manifest_drift(
                    &format!(
                        "Storage path drift: manifest {owner}.{field}={raw_path:?} does not match \
                         configured canonical path {:?}",
                        expected_path.to_string_lossy()
                    ),
                    resolved,
                ));
            }
        }
    }
    Ok(())
}

fn sorted(values: &[String]) -> Vec<String> {
    let mut sorted = values.to_vec();
    sorted.sort();
    sorted
}

/// Create the instance tree and idempotent manifest (`ensure_layout`).
///
/// Existing manifests are validated rather than overwritten, preventing an
/// instance directory from being silently reinterpreted under a new schema
/// or identity.
pub fn ensure_layout(resolved: &ResolvedStorage) -> StoreResult<Value> {
    let Some(existing) = load_manifest(resolved)? else {
        resolved
            .ensure_directories()
            .map_err(StoreError::Io)?;
        let payload = manifest_payload(resolved, None);
        let path = &resolved.manifest_path;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(StoreError::Io)?;
        }
        // json.dumps(payload, indent=2, sort_keys=True) + "\n"
        let mut text = crate::util::default_dumps_sorted_pretty(&payload);
        text.push('\n');
        let temporary = crate::util::with_suffix(path, ".json.tmp");
        std::fs::write(&temporary, text).map_err(StoreError::Io)?;
        std::fs::rename(&temporary, path).map_err(StoreError::Io)?;
        return Ok(payload);
    };
    if existing.get("schema_version").and_then(Value::as_str) != Some(resolved.schema_version.as_str())
    {
        return Err(manifest_drift(
            &format!(
                "Storage schema drift: manifest version {:?} does not match configured version \
                 {:?}",
                existing.get("schema_version"),
                resolved.schema_version
            ),
            resolved,
        ));
    }
    if existing.get("instance_id").and_then(Value::as_str) != Some(resolved.instance_id.as_str()) {
        return Err(manifest_drift(
            &format!(
                "Storage instance drift: manifest instance {:?} does not match configured \
                 instance {:?}",
                existing.get("instance_id"),
                resolved.instance_id
            ),
            resolved,
        ));
    }
    validate_manifest_layout(&existing, resolved)?;
    resolved
        .ensure_directories()
        .map_err(StoreError::Io)?;
    Ok(existing)
}

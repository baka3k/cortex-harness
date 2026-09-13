//! Port of `tools/common/source_inventory.py` — content fingerprints,
//! snapshot ids, generation files, and diffing.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use serde_json::json;

use crate::util;

pub const INVENTORY_SCHEMA_VERSION: i64 = 1;

#[derive(Debug, Clone, PartialEq)]
pub struct InventoryEntry {
    pub size: i64,
    pub mtime_ns: i128,
    pub sha256: String,
    pub repository_scope: String,
}

#[derive(Debug, Clone)]
pub struct SourceInventory {
    pub entries: BTreeMap<String, InventoryEntry>,
    pub snapshot_id: String,
    pub schema_version: i64,
    pub filter_version: i64,
}

/// `SourceChangedError` equivalent.
#[derive(Debug)]
pub struct SourceChanged(pub String);

impl std::fmt::Display for SourceChanged {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

fn to_posix(path: &str) -> String {
    let normalized = path.replace('\\', "/");
    normalized
        .strip_prefix("./")
        .map(str::to_string)
        .unwrap_or(normalized)
}

fn mtime_ns(metadata: &std::fs::Metadata) -> i128 {
    metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos() as i128)
        .unwrap_or(0)
}

/// `_fingerprint` — reuse the previous entry when size+mtime match and the
/// path is not force-hashed; otherwise hash the file and re-stat.
fn fingerprint(
    full_path: &Path,
    previous: Option<&InventoryEntry>,
    force_hash: bool,
) -> Result<InventoryEntry, SourceChanged> {
    let before = std::fs::metadata(full_path).map_err(|error| SourceChanged(error.to_string()))?;
    let before_mtime = mtime_ns(&before);
    if let Some(previous) = previous {
        if !force_hash && previous.size == before.len() as i64 && previous.mtime_ns == before_mtime {
            return Ok(previous.clone());
        }
    }
    let sha256 = util::sha256_of_file(full_path).map_err(|error| SourceChanged(error.to_string()))?;
    let after = std::fs::metadata(full_path).map_err(|error| SourceChanged(error.to_string()))?;
    let after_mtime = mtime_ns(&after);
    if before.len() != after.len() || before_mtime != after_mtime {
        return Err(SourceChanged(format!("source changed while hashing: {}", full_path.to_string_lossy())));
    }
    Ok(InventoryEntry {
        size: after.len() as i64,
        mtime_ns: after_mtime,
        sha256,
        repository_scope: ".".to_string(),
    })
}

fn entry_dict(entry: &InventoryEntry) -> serde_json::Value {
    json!({
        "size": entry.size,
        "mtime_ns": entry.mtime_ns as i64,
        "sha256": entry.sha256,
        "repository_scope": entry.repository_scope,
    })
}

/// `_snapshot_id` — sha256 over canonical JSON of sorted entries.
fn snapshot_id(entries: &BTreeMap<String, InventoryEntry>, filter_version: i64) -> String {
    let canonical = json!({
        "filter_version": filter_version,
        "entries": entries.iter().map(|(path, entry)| (path.clone(), entry_dict(entry))).collect::<serde_json::Map<String, serde_json::Value>>(),
    });
    util::sha256_hex(util::canonical_json_string(&canonical).as_bytes())
}

/// `capture_source_inventory`.
pub fn capture_source_inventory(
    root: &Path,
    paths: &BTreeSet<String>,
    previous: Option<&SourceInventory>,
    force_hash_paths: &BTreeSet<String>,
) -> Result<SourceInventory, SourceChanged> {
    let root_path = util::realpath(&util::path_to_string(root));
    let entries_result: Result<Vec<Option<(String, InventoryEntry)>>, SourceChanged> =
        paths.iter().map(|raw_path| -> Result<Option<(String, InventoryEntry)>, SourceChanged> {
            let normalized = to_posix(raw_path);
            if normalized.is_empty() {
                return Ok(None);
            }
            let relative = Path::new(&normalized);
            if relative.is_absolute() || relative.components().any(|c| c == std::path::Component::ParentDir) {
                return Ok(None);
            }
            let full_path = root_path.join(relative);
            if !full_path.is_file() {
                return Ok(None);
            }
            let prior = previous.and_then(|prev| prev.entries.get(&normalized));
            let entry = fingerprint(&full_path, prior, force_hash_paths.contains(&normalized))?;
            Ok(Some((normalized, entry)))
        }).collect();
    let entries: BTreeMap<String, InventoryEntry> = entries_result?.into_iter().flatten().collect();
    Ok(SourceInventory {
        snapshot_id: snapshot_id(&entries, INVENTORY_SCHEMA_VERSION),
        entries,
        schema_version: INVENTORY_SCHEMA_VERSION,
        filter_version: 1,
    })
}

/// `diff_source_inventories`.
pub fn diff_source_inventories(
    before: Option<&SourceInventory>,
    after: &SourceInventory,
) -> (BTreeSet<String>, BTreeSet<String>) {
    let empty = BTreeMap::new();
    let old_entries = before.map(|b| &b.entries).unwrap_or(&empty);
    let changed: BTreeSet<String> = after
        .entries
        .iter()
        .filter(|(path, entry)| {
            old_entries
                .get(*path)
                .map(|old| old.sha256 != entry.sha256)
                .unwrap_or(true)
        })
        .map(|(path, _)| path.clone())
        .collect();
    let deleted: BTreeSet<String> = old_entries
        .keys()
        .filter(|path| !after.entries.contains_key(*path))
        .cloned()
        .collect();
    (changed, deleted)
}

/// `preserve_inventory_prefixes`.
pub fn preserve_inventory_prefixes(
    current: SourceInventory,
    previous: Option<&SourceInventory>,
    prefixes: &BTreeSet<String>,
) -> SourceInventory {
    let previous = match previous {
        Some(previous) => previous,
        None => return current,
    };
    let normalized: Vec<String> = prefixes
        .iter()
        .filter(|p| !p.is_empty())
        .map(|p| format!("{}/", p.trim_end_matches('/')))
        .collect();
    if normalized.is_empty() {
        return current;
    }
    let mut entries = current.entries.clone();
    for (path, entry) in &previous.entries {
        if normalized.iter().any(|prefix| path.starts_with(prefix.as_str())) {
            entries.insert(path.clone(), entry.clone());
        }
    }
    SourceInventory {
        snapshot_id: snapshot_id(&entries, current.filter_version),
        entries,
        schema_version: current.schema_version,
        filter_version: current.filter_version,
    }
}

fn payload(inventory: &SourceInventory) -> serde_json::Value {
    json!({
        "schema_version": inventory.schema_version,
        "filter_version": inventory.filter_version,
        "snapshot_id": inventory.snapshot_id,
        "entries": inventory.entries.iter().map(|(path, entry)| (path.clone(), entry_dict(entry))).collect::<serde_json::Map<String, serde_json::Value>>(),
    })
}

/// `write_inventory_generation` — `<cache>/inventories/<snapshot>.json`.
pub fn write_inventory_generation(cache_dir: &Path, inventory: &SourceInventory) -> std::io::Result<String> {
    let directory = cache_dir.join("inventories");
    std::fs::create_dir_all(&directory)?;
    let target = directory.join(format!("{}.json", inventory.snapshot_id));
    let serialized = format!("{}\n", serde_json::to_string_pretty(&payload(inventory)).unwrap());
    if target.exists() {
        let existing = std::fs::read_to_string(&target)?;
        if existing != serialized {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("inventory generation collision: {}", target.to_string_lossy()),
            ));
        }
        return Ok(target.to_string_lossy().to_string());
    }
    let temp = target.with_extension(format!(
        "json.{}.{}.tmp",
        std::process::id(),
        util::uuid4_hex()
    ));
    std::fs::write(&temp, &serialized)?;
    match std::fs::rename(&temp, &target) {
        Ok(()) => {}
        Err(error) => {
            let _ = std::fs::remove_file(&temp);
            return Err(error);
        }
    }
    Ok(target.to_string_lossy().to_string())
}

/// `load_inventory_generation`.
pub fn load_inventory_generation(path: &Path) -> Result<SourceInventory, String> {
    let text = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
    let data: serde_json::Value = serde_json::from_str(&text).map_err(|error| error.to_string())?;
    let mut entries = BTreeMap::new();
    if let Some(map) = data.get("entries").and_then(|value| value.as_object()) {
        for (name, value) in map {
            entries.insert(
                to_posix(name),
                InventoryEntry {
                    size: value.get("size").and_then(|v| v.as_i64()).unwrap_or_default(),
                    mtime_ns: value.get("mtime_ns").and_then(|v| v.as_i64()).unwrap_or_default() as i128,
                    sha256: value
                        .get("sha256")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    repository_scope: value
                        .get("repository_scope")
                        .and_then(|v| v.as_str())
                        .unwrap_or(".")
                        .to_string(),
                },
            );
        }
    }
    let inventory = SourceInventory {
        snapshot_id: data
            .get("snapshot_id")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string(),
        entries,
        schema_version: data.get("schema_version").and_then(|v| v.as_i64()).unwrap_or(INVENTORY_SCHEMA_VERSION),
        filter_version: data.get("filter_version").and_then(|v| v.as_i64()).unwrap_or(1),
    };
    if inventory.snapshot_id != snapshot_id(&inventory.entries, inventory.filter_version) {
        return Err(format!("inventory checksum mismatch: {}", path.to_string_lossy()));
    }
    Ok(inventory)
}

/// `validate_inventory_unchanged` — re-hash selected paths and compare.
pub fn validate_inventory_unchanged(
    root: &Path,
    inventory: &SourceInventory,
    paths: &BTreeSet<String>,
) -> Result<(), SourceChanged> {
    let selected: BTreeSet<String> = paths.iter().map(|p| to_posix(p)).collect();
    let current = capture_source_inventory(root, &selected, None, &selected)?;
    let mismatch: Vec<String> = selected
        .iter()
        .filter(|path| inventory.entries.get(*path) != current.entries.get(*path))
        .cloned()
        .collect();
    if !mismatch.is_empty() {
        let head: Vec<String> = mismatch.iter().take(10).cloned().collect();
        return Err(SourceChanged(format!(
            "source changed during scan: {}",
            head.join(", ")
        )));
    }
    Ok(())
}

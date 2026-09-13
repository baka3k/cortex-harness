//! Non-destructive migration from the repository-local storage layout.
//!
//! Port of `cortex_harness.storage.migration`: copy legacy stores without
//! deleting or overwriting their sources, verify by digest, and fence the
//! result with a migration marker.
//!
//! Divergence: `_reopen_inventory` in Python reopens migrated stores with
//! qdrant-client / redislite / ladybug to list their graphs or collections.
//! Those embedded engines are not linkable here, so the Rust port reports an
//! empty inventory for `copied`/`verified-noop` items (dry-run behavior is
//! identical to Python).

use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::config::ResolvedStorage;
use crate::errors::{StoreError, StoreResult};
use crate::lease::StorageLease;
use crate::layout::ensure_layout;

/// One migration outcome row (`MigrationItem`).
#[derive(Debug, Clone, PartialEq)]
pub struct MigrationItem {
    pub source: PathBuf,
    pub target: PathBuf,
    pub action: String,
    pub digest: Option<String>,
    pub inventory: Vec<String>,
}

fn tree_digest(path: &Path) -> StoreResult<String> {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    let meta = std::fs::symlink_metadata(path).map_err(StoreError::Io)?;
    if meta.is_file() {
        let mut file = std::fs::File::open(path).map_err(StoreError::Io)?;
        let mut buffer = [0u8; 1024 * 1024];
        loop {
            let read = file.read(&mut buffer).map_err(StoreError::Io)?;
            if read == 0 {
                break;
            }
            hasher.update(&buffer[..read]);
        }
    } else if meta.is_dir() {
        let mut entries: Vec<PathBuf> = Vec::new();
        collect_files(path, path, &mut entries)?;
        entries.sort();
        for entry in entries {
            let relative = entry
                .strip_prefix(path)
                .map_err(|_| StoreError::Value("entry outside source tree".to_string()))?;
            hasher.update(relative.to_string_lossy().as_bytes());
            let mut file = std::fs::File::open(&entry).map_err(StoreError::Io)?;
            let mut buffer = [0u8; 1024 * 1024];
            loop {
                let read = file.read(&mut buffer).map_err(StoreError::Io)?;
                if read == 0 {
                    break;
                }
                hasher.update(&buffer[..read]);
            }
        }
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn collect_files(root: &Path, current: &Path, entries: &mut Vec<PathBuf>) -> StoreResult<()> {
    for entry in std::fs::read_dir(current).map_err(StoreError::Io)? {
        let entry = entry.map_err(StoreError::Io)?.path();
        let meta = std::fs::symlink_metadata(&entry).map_err(StoreError::Io)?;
        if meta.is_dir() {
            collect_files(root, &entry, entries)?;
        } else if meta.is_file() {
            entries.push(entry);
        }
    }
    let _ = root;
    Ok(())
}

fn has_content(path: &Path) -> bool {
    match std::fs::symlink_metadata(path) {
        Err(_) => false,
        Ok(meta) if meta.is_file() => true,
        Ok(meta) if meta.is_dir() => std::fs::read_dir(path)
            .map(|mut entries| entries.next().is_some())
            .unwrap_or(false),
        Ok(_) => false,
    }
}

fn marker_path(target: &Path) -> PathBuf {
    let name = target
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    target
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(format!(".{name}.cortex-migration.json"))
}

fn load_marker(target: &Path) -> Option<Value> {
    let path = marker_path(target);
    let text = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str(&text).ok()
}

fn write_marker(target: &Path, source: &Path, digest: &str, inventory: &[String]) -> StoreResult<()> {
    let payload = json!({
        "source": source.to_string_lossy(),
        "source_sha256": digest,
        "target": target.to_string_lossy(),
        "inventory": inventory,
    });
    let mut text = crate::util::default_dumps_sorted_pretty(&payload);
    text.push('\n');
    std::fs::write(marker_path(target), text).map_err(StoreError::Io)?;
    Ok(())
}

fn copy_tree(source: &Path, target: &Path) -> StoreResult<()> {
    std::fs::create_dir_all(target).map_err(StoreError::Io)?;
    for entry in std::fs::read_dir(source).map_err(StoreError::Io)? {
        let entry = entry.map_err(StoreError::Io)?.path();
        let destination = target.join(entry.file_name().unwrap_or_default());
        let meta = std::fs::symlink_metadata(&entry).map_err(StoreError::Io)?;
        if meta.is_dir() {
            copy_tree(&entry, &destination)?;
        } else {
            std::fs::copy(&entry, &destination).map_err(StoreError::Io)?;
        }
    }
    Ok(())
}

/// Copy legacy stores without deleting or overwriting their sources
/// (`migrate_legacy_layout`).
///
/// Existing targets with the same digest are reported as verified no-ops;
/// divergent targets fail rather than merging two embedded databases.
pub fn migrate_legacy_layout(
    resolved: &ResolvedStorage,
    legacy_root: &Path,
    dry_run: bool,
) -> StoreResult<Vec<MigrationItem>> {
    let legacy_root = crate::util::resolve_path(legacy_root);
    let pairs: Vec<(PathBuf, PathBuf, String, String)> = vec![
        (
            legacy_root.join("local_qdrant_db").join("code"),
            resolved.qdrant_code_path.clone(),
            resolved.code_owner_id.clone(),
            "qdrant".to_string(),
        ),
        (
            legacy_root.join("local_qdrant_db").join("doc"),
            resolved.qdrant_doc_path.clone(),
            resolved.doc_owner_id.clone(),
            "qdrant".to_string(),
        ),
        (
            legacy_root.join("local_falkordb_db").join("cortex.rdb"),
            resolved.falkordb_code_path.clone(),
            resolved.code_owner_id.clone(),
            "falkordb".to_string(),
        ),
        // The legacy file could contain both code and document graphs; copy
        // it to each new owner so scoped verification can retire extras.
        (
            legacy_root.join("local_falkordb_db").join("cortex.rdb"),
            resolved.falkordb_doc_path.clone(),
            resolved.doc_owner_id.clone(),
            "falkordb".to_string(),
        ),
    ];
    let mut report: Vec<MigrationItem> = Vec::new();
    for (source, target, owner, backend) in pairs {
        if !source.exists() {
            continue;
        }
        // Hold both ends continuously so parallel migration attempts fail
        // before hashing.
        let mut source_lease;
        let mut target_lease;
        if dry_run {
            source_lease = None;
            target_lease = None;
        } else {
            source_lease = Some(
                StorageLease::new(
                    &source,
                    &resolved.instance_id,
                    &format!("legacy-{owner}"),
                    &format!("legacy-{backend}"),
                )
                .acquire()
                .map_err(|(_, conflict)| StoreError::Runtime(conflict.message))?,
            );
            target_lease = Some(
                StorageLease::new(&target, &resolved.instance_id, &owner, &backend)
                    .acquire()
                    .map_err(|(_, conflict)| StoreError::Runtime(conflict.message))?,
            );
        }
        let outcome: StoreResult<()> = (|| {
            let source_digest = tree_digest(&source)?;
            if has_content(&target) {
                let marker = load_marker(&target);
                let marker_matches = marker
                    .as_ref()
                    .and_then(|payload| payload.get("source_sha256"))
                    .and_then(Value::as_str)
                    .is_some_and(|digest| digest == source_digest);
                if !marker_matches {
                    let target_digest = tree_digest(&target)?;
                    if target_digest != source_digest {
                        return Err(StoreError::Io(std::io::Error::other(format!(
                            "Migration target exists with different content: {}",
                            target.display()
                        ))));
                    }
                }
                report.push(MigrationItem {
                    source: source.clone(),
                    target: target.clone(),
                    action: "verified-noop".to_string(),
                    digest: Some(source_digest),
                    inventory: Vec::new(),
                });
                return Ok(());
            }
            report.push(MigrationItem {
                source: source.clone(),
                target: target.clone(),
                action: if dry_run { "would-copy" } else { "copied" }.to_string(),
                digest: Some(source_digest.clone()),
                inventory: Vec::new(),
            });
            if dry_run {
                return Ok(());
            }
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent).map_err(StoreError::Io)?;
            }
            let source_is_dir = std::fs::symlink_metadata(&source)
                .map(|meta| meta.is_dir())
                .unwrap_or(false);
            if source_is_dir {
                if target.exists() {
                    return Err(StoreError::Io(std::io::Error::other(format!(
                        "Migration target already exists: {}",
                        target.display()
                    ))));
                }
                copy_tree(&source, &target)?;
            } else {
                std::fs::copy(&source, &target).map_err(StoreError::Io)?;
            }
            if tree_digest(&target)? != source_digest {
                return Err(StoreError::Io(std::io::Error::other(format!(
                    "Migration verification failed for {}",
                    target.display()
                ))));
            }
            // Inventory reopen is out of crate scope (embedded engines live
            // in the Python layer); see module docs.
            let inventory: BTreeSet<String> = BTreeSet::new();
            write_marker(
                &target,
                &source,
                &source_digest,
                &inventory.into_iter().collect::<Vec<_>>(),
            )?;
            if let Some(item) = report.last_mut() {
                item.action = "copied".to_string();
                item.inventory = Vec::new();
            }
            Ok(())
        })();
        if let Some(lease) = target_lease.as_mut() {
            lease.release();
        }
        if let Some(lease) = source_lease.as_mut() {
            lease.release();
        }
        outcome?;
    }
    if !dry_run {
        ensure_layout(resolved)?;
    }
    Ok(report)
}

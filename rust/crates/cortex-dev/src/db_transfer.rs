//! Native `.cortexdb` export/import — port of `cortex_harness/db_transfer.py`
//! (phase-03). The gzipped-tar bundle format is unchanged: `manifest.json`
//! plus the physical storage lanes, so archives produced by either side are
//! readable by both. Tar/gzip goes through the platform `tar` CLI (bsdtar on
//! macOS / GNU tar on Linux / tar.exe on Windows) — a system tool, not a
//! Python dependency.

use crate::env::{code_qdrant_collection, resolve_start_config};
use cortex_storage::config::{resolve_storage, ConfigMap, ResolveOverrides};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub const BUNDLE_SUFFIX: &str = ".cortexdb";
pub const BUNDLE_SCHEMA: &str = "cortex-db-bundle@1";
pub const MANIFEST_NAME: &str = "manifest.json";

pub struct DbTransferError(pub String);

impl std::fmt::Display for DbTransferError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

type Result_<T> = std::result::Result<T, DbTransferError>;

fn err<T>(message: impl Into<String>) -> Result_<T> {
    Err(DbTransferError(message.into()))
}

/// dev.py `_load_project_config` — nearest `dev.json` walking up.
fn load_project_config(project_root: &Path) -> Result_<(Value, PathBuf)> {
    let (root, config_path) = resolve_start_config(project_root, project_root);
    if !config_path.is_file() {
        return err(format!(
            "No active dev.json found at {}. Run 'dev init' first.",
            config_path.display()
        ));
    }
    let text = std::fs::read_to_string(&config_path)
        .map_err(|e| DbTransferError(format!("Invalid dev.json at {}: {e}", config_path.display())))?;
    match serde_json::from_str::<Value>(&text) {
        Ok(v) if v.is_object() => Ok((v, root)),
        _ => err(format!(
            "Invalid dev.json at {}: expected object",
            config_path.display()
        )),
    }
}

fn str_or_empty(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// dev.py `_resolve_targets`.
fn resolve_targets(cfg: &Value) -> Result_<BTreeMap<String, String>> {
    let project = cfg.get("project").cloned().unwrap_or(json!({}));
    let project_id = ["code", "name"]
        .iter()
        .find_map(|k| {
            let v = str_or_empty(project.get(k));
            if v.is_empty() {
                None
            } else {
                Some(v)
            }
        })
        .unwrap_or_default()
        .trim()
        .to_string();
    if project_id.is_empty() {
        return err("dev.json is missing 'project.code' — cannot derive project_id");
    }
    let env_str = |section: &str, key: &str| -> String {
        str_or_empty(
            cfg.get(section)
                .and_then(|s| s.get("env"))
                .and_then(|e| e.get(key)),
        )
    };
    let mut targets = BTreeMap::new();
    targets.insert("project_id".to_string(), project_id.clone());
    targets.insert(
        "code_graph".to_string(),
        {
            let v = env_str("code", "FALKORDB_GRAPH");
            if v.is_empty() {
                project_id.clone()
            } else {
                v
            }
        }
        .trim()
        .to_string(),
    );
    targets.insert(
        "doc_graph".to_string(),
        {
            let v = env_str("doc", "FALKORDB_GRAPH");
            if v.is_empty() {
                format!("{project_id}_doc")
            } else {
                v
            }
        }
        .trim()
        .to_string(),
    );
    targets.insert(
        "code_collection".to_string(),
        {
            let v = env_str("code", "QDRANT_COLLECTION");
            if !v.is_empty() {
                v
            } else {
                code_qdrant_collection(
                    cfg.get("code").and_then(|c| c.get("env")).unwrap_or(&Value::Null),
                    &project,
                )
            }
        }
        .trim()
        .to_string(),
    );
    targets.insert(
        "doc_collection".to_string(),
        {
            let v = env_str("doc", "QDRANT_COLLECTION_DOC");
            if !v.is_empty() {
                v
            } else {
                let v = env_str("doc", "QDRANT_COLLECTION");
                if !v.is_empty() {
                    v
                } else {
                    format!("{project_id}_doc")
                }
            }
        }
        .trim()
        .to_string(),
    );
    Ok(targets)
}

/// dev.py `_resolve_local_storage` — resolve local storage, reject remote.
fn resolve_local_storage(project_root: &Path, cfg: &Value, targets: &BTreeMap<String, String>) -> Result_<cortex_storage::config::ResolvedStorage> {
    let backend_raw = str_or_empty(cfg.get("storage_backend"));
    let backend = if backend_raw.is_empty() { "local".to_string() } else { backend_raw };
    if backend.trim().to_lowercase() != "local" {
        return err(format!(
            "db-transfer only supports storage_backend='local'; the active config is '{}'. \
             Switch to local embedded storage or export directly from the machine that owns \
             the local files.",
            backend
        ));
    }
    let code_env = cfg.get("code").and_then(|c| c.get("env")).cloned().unwrap_or(json!({}));
    let resolve_config: ConfigMap = code_env
        .as_object()
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    resolve_storage(
        project_root,
        Some(&resolve_config),
        &ResolveOverrides {
            code_graph: targets.get("code_graph").cloned(),
            doc_graph: targets.get("doc_graph").cloned(),
            code_collection: targets.get("code_collection").cloned(),
            doc_collection: targets.get("doc_collection").cloned(),
            ..Default::default()
        },
    )
    .map_err(|e| DbTransferError(e.to_string()))
}

fn sha256_file(path: &Path) -> String {
    use sha2::{Digest, Sha256};
    let Ok(data) = std::fs::read(path) else {
        return String::new();
    };
    let mut hasher = Sha256::new();
    hasher.update(&data);
    hasher.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Recursively copy `src` into `dst`; returns bytes copied.
fn copy_tree(src: &Path, dst: &Path) -> std::io::Result<u64> {
    if !src.exists() {
        return Ok(0);
    }
    std::fs::create_dir_all(dst)?;
    let mut total = 0u64;
    let mut stack = vec![src.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in std::fs::read_dir(&dir)?.flatten() {
            let target = dst.join(entry.path().strip_prefix(src).unwrap_or(&entry.path()));
            if entry.file_type()?.is_dir() {
                std::fs::create_dir_all(&target)?;
                stack.push(entry.path());
            } else {
                if let Some(parent) = target.parent() {
                    std::fs::create_dir_all(parent)?;
                }
                std::fs::copy(entry.path(), &target)?;
                total += entry.metadata()?.len();
            }
        }
    }
    Ok(total)
}

fn copy_file(src: &Path, dst: &Path) -> Result_<u64> {
    if !src.exists() {
        return Ok(0);
    }
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent).map_err(|e| DbTransferError(e.to_string()))?;
    }
    std::fs::copy(src, dst).map_err(|e| DbTransferError(e.to_string()))
}

fn timestamp() -> String {
    crate::util::local_strftime(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64,
        "%Y%m%dT%H%M%SZ",
    )
}

fn tempfile_dir(project_root: &Path) -> PathBuf {
    let candidate = project_root.join(".cache").join("db-transfer");
    let _ = std::fs::create_dir_all(&candidate);
    candidate
}

fn maybe_load_source_manifest(resolved: &cortex_storage::config::ResolvedStorage) -> Value {
    match cortex_storage::layout::load_manifest(resolved) {
        Ok(Some(payload)) => payload,
        _ => Value::Null,
    }
}

fn resolve_output_path(output: Option<&str>, project_id: &str) -> PathBuf {
    if let Some(output) = output {
        let expanded = match output.strip_prefix("~/") {
            Some(rest) => std::env::var("HOME")
                .map(|home| PathBuf::from(home).join(rest))
                .unwrap_or_else(|_| PathBuf::from(output)),
            None => PathBuf::from(output),
        };
        let path = if expanded.extension().is_none() {
            // Python `path.with_suffix('.cortexdb')` on a suffix-less path.
            let os_text = expanded.to_string_lossy().to_string();
            PathBuf::from(format!("{os_text}{BUNDLE_SUFFIX}"))
        } else {
            expanded
        };
        return if path.is_absolute() {
            crate::env::abspath(&path)
        } else {
            std::env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(path)
        };
    }
    std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("outputs")
        .join("db")
        .join(format!("{project_id}-{}{BUNDLE_SUFFIX}", timestamp()))
}

/// `with_suffix(".cortexdb")` keeps a stem (Python replaces a MISSING suffix
/// only); with_suffix on names that already end in .cortexdb is a no-op.
fn format_count(n: u64) -> String {
    // Python `{n:,}` grouping.
    let text = n.to_string();
    let mut out = String::new();
    for (i, c) in text.chars().enumerate() {
        if i > 0 && (text.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// dev.py `export_project`.
pub fn export_project(
    project_dir: &Path,
    output: Option<&str>,
    project_id_override: Option<&str>,
    role: &str,
) -> Result_<()> {
    let (cfg, resolved_root) = load_project_config(project_dir)?;
    let mut targets = resolve_targets(&cfg)?;
    if let Some(over) = project_id_override.filter(|s| !s.is_empty()) {
        targets.insert("project_id".to_string(), over.to_string());
    }
    let resolved = resolve_local_storage(&resolved_root, &cfg, &targets)?;
    if !matches!(role, "code" | "doc" | "both") {
        return err(format!("role must be code|doc|both; got {role:?}"));
    }
    let project_id = targets.get("project_id").cloned().unwrap_or_default();
    let staging = tempfile_dir(&resolved_root).join(format!(".cortexdb-export-{project_id}-{}", timestamp()));
    std::fs::create_dir_all(&staging).map_err(|e| DbTransferError(e.to_string()))?;

    let mut entries = json!({"qdrant": {}, "falkordb": {}});

    let mut lane_plan: Vec<(&str, &Path, &Path, String, String)> = Vec::new();
    if matches!(role, "code" | "both") {
        lane_plan.push((
            "code",
            resolved.qdrant_code_path.as_path(),
            resolved.falkordb_code_path.as_path(),
            targets.get("code_collection").cloned().unwrap_or_default(),
            targets.get("code_graph").cloned().unwrap_or_default(),
        ));
    }
    if matches!(role, "doc" | "both") {
        lane_plan.push((
            "doc",
            resolved.qdrant_doc_path.as_path(),
            resolved.falkordb_doc_path.as_path(),
            targets.get("doc_collection").cloned().unwrap_or_default(),
            targets.get("doc_graph").cloned().unwrap_or_default(),
        ));
    }

    for (lane_name, qdrant_path, falkordb_path, collection, graph) in lane_plan {
        if !qdrant_path.exists() {
            eprintln!("[warn] Qdrant lane {lane_name} missing at {}; skipping", qdrant_path.display());
        } else {
            let dst_qdrant = staging.join("qdrant").join(lane_name);
            let bytes_copied =
                copy_tree(qdrant_path, &dst_qdrant).map_err(|e| DbTransferError(e.to_string()))?;
            entries["qdrant"][lane_name] = json!({
                "source_path": qdrant_path.to_string_lossy(),
                "archive_path": format!("qdrant/{lane_name}"),
                "collection": collection,
                "bytes": bytes_copied,
            });
        }
        if !falkordb_path.exists() {
            eprintln!("[warn] FalkorDB lane {lane_name} missing at {}; skipping", falkordb_path.display());
        } else {
            let dst_falkor = staging.join("falkordb").join(format!("{lane_name}.rdb"));
            let bytes_copied = copy_file(falkordb_path, &dst_falkor)?;
            entries["falkordb"][lane_name] = json!({
                "source_path": falkordb_path.to_string_lossy(),
                "archive_path": format!("falkordb/{lane_name}.rdb"),
                "graph": graph,
                "bytes": bytes_copied,
                "sha256": sha256_file(falkordb_path),
            });
        }
    }

    if entries["qdrant"].as_object().map(|o| o.is_empty()).unwrap_or(true)
        && entries["falkordb"].as_object().map(|o| o.is_empty()).unwrap_or(true)
    {
        let _ = std::fs::remove_dir_all(&staging);
        return err(format!(
            "No local storage files found for project {project_id:?}. Run ingestion first."
        ));
    }

    let manifest = json!({
        "schema": BUNDLE_SCHEMA,
        "project_id": project_id,
        "schema_version": resolved.schema_version,
        "instance_id": resolved.instance_id,
        "code_owner_id": resolved.code_owner_id,
        "doc_owner_id": resolved.doc_owner_id,
        "targets": {
            "code_graph": targets.get("code_graph"),
            "doc_graph": targets.get("doc_graph"),
            "code_collection": targets.get("code_collection"),
            "doc_collection": targets.get("doc_collection"),
        },
        "entries": entries,
        "created_at": crate::util::iso_utc_now(),
        "source_manifest": maybe_load_source_manifest(&resolved),
    });
    let manifest_path = staging.join(MANIFEST_NAME);
    let mut sorted_manifest = manifest.clone();
    sort_json_in_place(&mut sorted_manifest);
    std::fs::write(
        &manifest_path,
        serde_json::to_string_pretty(&sorted_manifest).unwrap_or_default() + "\n",
    )
    .map_err(|e| DbTransferError(e.to_string()))?;

    let archive_path = resolve_output_path(output, &project_id);
    if let Some(parent) = archive_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    run_tar(&[
        "-czf".into(),
        archive_path.to_string_lossy().to_string(),
        "-C".into(),
        staging.to_string_lossy().to_string(),
        ".".into(),
    ])?;
    let _ = std::fs::remove_dir_all(&staging);

    let size_bytes = std::fs::metadata(&archive_path).map(|m| m.len()).unwrap_or(0);
    crate::util::echo(&format!(
        "[ok] Exported project_id='{project_id}' -> {} ({} bytes)",
        archive_path.display(),
        format_count(size_bytes)
    ));
    Ok(())
}

/// dev.py `import_project`.
pub fn import_project(project_dir: &Path, archive: &Path, overwrite: bool, role: &str) -> Result_<()> {
    let archive_path = crate::env::abspath(archive);
    if !archive_path.is_file() {
        return err(format!("Archive not found: {}", archive_path.display()));
    }
    if !matches!(role, "code" | "doc" | "both") {
        return err(format!("role must be code|doc|both; got {role:?}"));
    }
    let staging = tempfile_dir(project_dir).join(format!(".cortexdb-import-{}", timestamp()));
    std::fs::create_dir_all(&staging).map_err(|e| DbTransferError(e.to_string()))?;
    let result = (|| -> Result_<(String, Value)> {
        for member in safe_member_names(&archive_path)? {
            if member.trim().is_empty() {
                continue;
            }
            safe_member_name(&member)?;
        }
        run_tar(&[
            "-xzf".into(),
            archive_path.to_string_lossy().to_string(),
            "-C".into(),
            staging.to_string_lossy().to_string(),
        ])
        .or_else(|_| {
            run_tar(&[
                "-xf".into(),
                archive_path.to_string_lossy().to_string(),
                "-C".into(),
                staging.to_string_lossy().to_string(),
            ])
        })?;

        let manifest_path = staging.join(MANIFEST_NAME);
        if !manifest_path.is_file() {
            return err(format!(
                "Archive {} is missing {MANIFEST_NAME}",
                archive_path.display()
            ));
        }
        let manifest: Value = serde_json::from_str(
            &std::fs::read_to_string(&manifest_path).map_err(|e| DbTransferError(e.to_string()))?,
        )
        .map_err(|_| DbTransferError("Bundle manifest must be a JSON object".to_string()))?;
        if manifest.get("schema").and_then(|s| s.as_str()) != Some(BUNDLE_SCHEMA) {
            return err(format!(
                "Unsupported bundle schema {}; expected {BUNDLE_SCHEMA:?}",
                manifest
                    .get("schema")
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| "None".to_string())
            ));
        }

        let (cfg, resolved_root) = load_project_config(project_dir)?;
        let mut targets = resolve_targets(&cfg)?;
        if let Some(pid) = manifest.get("project_id").and_then(|v| v.as_str()) {
            targets.insert("project_id".to_string(), pid.to_string());
        }
        let resolved = resolve_local_storage(&resolved_root, &cfg, &targets)?;

        let mut restored = json!({"qdrant": {}, "falkordb": {}, "backup": Value::Null});
        let mut lane_plan: Vec<(&str, &Path, &Path)> = Vec::new();
        if matches!(role, "code" | "both") {
            lane_plan.push(("code", resolved.qdrant_code_path.as_path(), resolved.falkordb_code_path.as_path()));
        }
        if matches!(role, "doc" | "both") {
            lane_plan.push(("doc", resolved.qdrant_doc_path.as_path(), resolved.falkordb_doc_path.as_path()));
        }
        for (lane_name, qdrant_dest, falkordb_dest) in lane_plan {
            let src_qdrant = staging.join("qdrant").join(lane_name);
            if src_qdrant.exists() {
                let backup = backup_if_needed(qdrant_dest, &resolved.backups_path, overwrite)?;
                if let Some(backup) = backup
                    && restored["backup"].is_null()
                {
                    restored["backup"] =
                        json!(backup.parent().map(|p| p.to_string_lossy().to_string()));
                }
                if qdrant_dest.exists() {
                    std::fs::remove_dir_all(qdrant_dest).map_err(|e| DbTransferError(e.to_string()))?;
                }
                copy_tree(&src_qdrant, qdrant_dest).map_err(|e| DbTransferError(e.to_string()))?;
                restored["qdrant"][lane_name] = json!(qdrant_dest.to_string_lossy());
            }
            let src_falkor = staging.join("falkordb").join(format!("{lane_name}.rdb"));
            if src_falkor.exists() {
                let backup = backup_if_needed(falkordb_dest, &resolved.backups_path, overwrite)?;
                if let Some(backup) = backup
                    && restored["backup"].is_null()
                {
                    restored["backup"] =
                        json!(backup.parent().map(|p| p.to_string_lossy().to_string()));
                }
                if falkordb_dest.exists() {
                    std::fs::remove_file(falkordb_dest).map_err(|e| DbTransferError(e.to_string()))?;
                }
                copy_file(&src_falkor, falkordb_dest)?;
                restored["falkordb"][lane_name] = json!(falkordb_dest.to_string_lossy());
            }
        }
        if restored["qdrant"].as_object().map(|o| o.is_empty()).unwrap_or(true)
            && restored["falkordb"].as_object().map(|o| o.is_empty()).unwrap_or(true)
        {
            return err(format!(
                "Archive contains no storage entries for role {role:?} (project_id={})",
                manifest
                    .get("project_id")
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "None".to_string())
            ));
        }
        let manifest_project_id = manifest
            .get("project_id")
            .map(|v| match v {
                Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_else(|| "None".to_string());
        Ok((manifest_project_id, restored))
    })();
    let _ = std::fs::remove_dir_all(&staging);
    let (manifest_project_id, restored) = result?;

    let lanes: Vec<String> = restored["qdrant"]
        .as_object()
        .map(|o| o.keys().cloned().collect::<Vec<String>>())
        .unwrap_or_default()
        .into_iter()
        .chain(
            restored["falkordb"]
                .as_object()
                .map(|o| o.keys().cloned().collect::<Vec<String>>())
                .unwrap_or_default(),
        )
        .collect();
    crate::util::echo(&format!(
        "[ok] Imported project_id={} from {} (lanes={})",
        python_repr(&manifest_project_id),
        archive_path.display(),
        python_lane_list(&lanes),
    ));
    if let Some(backup) = restored["backup"].as_str().filter(|s| !s.is_empty()) {
        crate::util::echo(&format!("[info] Previous data backed up to {backup}"));
    }
    Ok(())
}

fn python_lane_list(lanes: &[String]) -> String {
    let inner: Vec<String> = lanes.iter().map(|l| format!("'{l}'")).collect();
    format!("[{}]", inner.join(", "))
}

fn python_repr(value: &str) -> String {
    format!("{value:?}")
}

fn backup_if_needed(target: &Path, backups_root: &Path, overwrite: bool) -> Result_<Option<PathBuf>> {
    let skip = if target.is_dir() {
        !target.exists()
            || std::fs::read_dir(target)
                .map(|mut d| d.next().is_none())
                .unwrap_or(true)
    } else {
        !target.exists()
    };
    if skip {
        return Ok(None);
    }
    if !overwrite {
        return err(format!(
            "Destination {} already contains data. Re-run with OVERWRITE=1 to back it up and replace it.",
            target.display()
        ));
    }
    std::fs::create_dir_all(backups_root).map_err(|e| DbTransferError(e.to_string()))?;
    let backup_path = backups_root.join(format!(
        "{}.{}.bak",
        target
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
        timestamp()
    ));
    std::fs::rename(target, &backup_path).map_err(|e| DbTransferError(e.to_string()))?;
    Ok(Some(backup_path))
}

/// List archive members with type info (`tar -tv`). Equivalent of Python's
/// `tarfile.filter="data"` gate: members that are not regular files or
/// directories (symlinks, hardlinks, devices, fifos) are refused before any
/// extraction happens, so a crafted bundle cannot write through a link.
fn safe_member_names(archive: &Path) -> Result_<Vec<String>> {
    let output = std::process::Command::new("tar")
        .args(["-tvzf", &archive.to_string_lossy()])
        .output()
        .map_err(|e| DbTransferError(e.to_string()))?;
    let listing = if output.status.success() {
        String::from_utf8_lossy(&output.stdout).to_string()
    } else {
        let output = std::process::Command::new("tar")
            .args(["-tvf", &archive.to_string_lossy()])
            .output()
            .map_err(|e| DbTransferError(e.to_string()))?;
        String::from_utf8_lossy(&output.stdout).to_string()
    };
    Ok(listing.lines().map(|l| l.to_string()).collect())
}

/// The `-tv` listing carries no reliable portable field layout (bsdtar vs GNU
/// tar differ), so only the load-bearing, format-independent check lives here:
/// the member TYPE. Path safety relies on that gate plus tar's own extraction
/// sanitisation (bsdtar/GNU tar refuse `..` escapes and strip absolute paths),
/// mirroring the reach of Python's `filter="data"` for the bundles this
/// project produces.
fn safe_member_name(listing_line: &str) -> Result_<()> {
    let entry = listing_line.split(" -> ").next().unwrap_or(listing_line);
    let type_char = entry.trim_start().chars().next().unwrap_or('-');
    if !matches!(type_char, '-' | 'd') {
        return err(format!(
            "Unsafe archive member (not a regular file or directory): {listing_line:?}"
        ));
    }
    Ok(())
}

fn run_tar(args: &[String]) -> Result_<()> {
    let status = std::process::Command::new("tar")
        .args(args)
        // bsdtar on macOS: COPYFILE_DISABLE=1 keeps AppleDouble (._*) members
        // out of archives so bundles match the Python tarfile layout.
        .env("COPYFILE_DISABLE", "1")
        .stdout(std::process::Stdio::null())
        .status()
        .map_err(|e| DbTransferError(format!("failed to run tar: {e}")))?;
    if status.success() {
        Ok(())
    } else {
        err(format!("tar exited with status {}", status.code().unwrap_or(1)))
    }
}

/// Recursively sort manifest object keys (`json.dumps(..., sort_keys=True)`).
fn sort_json_in_place(value: &mut Value) {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<String> = map.keys().cloned().collect();
            keys.sort();
            let mut rebuilt = serde_json::Map::new();
            for key in keys {
                let mut item = map.remove(&key).unwrap_or(Value::Null);
                sort_json_in_place(&mut item);
                rebuilt.insert(key, item);
            }
            *map = rebuilt;
        }
        Value::Array(items) => {
            for item in items.iter_mut() {
                sort_json_in_place(item);
            }
        }
        _ => {}
    }
}

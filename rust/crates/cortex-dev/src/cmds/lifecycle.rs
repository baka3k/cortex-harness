//! Lifecycle commands — phase-05 port of `scripts/mcp-lifecycle.py`, action
//! by action (no big-bang). Actions still shimmed to the Python script are
//! listed in `SHIMMED_ACTIONS` with the reason (phase-05 report); each native
//! port replaces its shim here.
//!
//! Native: help, build (new semantics: cargo workspace + native ensure-ort),
//! install/uninstall (D1 launcher), storage-init/layout/migrate-layout/
//! backup/stop, ensure-ort.
//! Shimmed: doctor, start, stop, infra-up, infra-down.

use crate::parser::Matches;
use crate::util::repo_root;
use crate::util::{echo, echo_err, fail};
use cortex_storage::config::{resolve_storage, ConfigMap, ResolvedStorage};
use cortex_storage::lease::StorageLease;
use cortex_storage::layout::{ensure_layout, load_manifest};
use cortex_storage::migration::migrate_legacy_layout;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;

const USAGE: &str = "Usage (equivalent forms):
  make build       | dev build       Create/sync virtualenvs and Python dependencies.
  make install     | dev install     Run build and install the global dev command.
  make uninstall   | dev uninstall   Remove the global dev command.
  make infra-up    | dev infra-up    Initialize local storage for all registered
                                      projects and probe remote backends (pass
                                      INFRA_ARGS=\"--provision\" to provision).
  make infra-down  | dev infra-down  Deprecated: stop legacy Docker containers if
                                      present and close cached remote clients
                                      (local file-backed storage persists).
  make storage-layout               Show instance paths, manifest, and current leases.
  make storage-init                 Create the canonical instance tree and manifest.
  make storage-migrate-layout       Dry-run legacy repository-local migration.
  make storage-backup               Create a verified owner backup (OWNER=code|doc).
  make doctor      | dev doctor      Check local storage and list every running Cortex MCP.
                                      Also reports active code/doc sync workers and
                                      remote-backend reachability.
  make sync code stop                Stop code sync workers and descendants.
  make sync doc stop                 Stop document sync workers and descendants.
  make start       | dev start       Load the nearest project dev.json and open both MCPs.
  make stop        | dev stop        Stop MCP terminals/processes started by start.

Rust workspace (rust/) — parity-first port of the graph core + retrieval brain:
  make rust-build                    cargo build --release for the whole workspace.
  make rust-test                     cargo test against committed golden fixtures.
  make rust-clippy                   cargo clippy -D warnings (mandatory port gate).
  make rust-check                    clippy + test (run before every cutover).
  make rust-pyo3                     Build the PyO3 extension (cortex-retrieval-py)
                                      and replay the Python↔Rust parity suite.
  make rust-fixtures                 Regenerate golden fixtures from the Python
                                      reference in scripts/rust_parity/ (commit diffs).
  make rust-clean                    cargo clean.

Graph providers (GRAPH_PROVIDER / CODE_GRAPH_PROVIDER / DOC_GRAPH_PROVIDER):
  falkordb  embedded FalkorDBLite (POSIX default), or remote via FALKORDB_URI.
  ladybug   embedded LadybugDB, local-only (Windows default; aliases
            lbug | lady-bug | kuzu; LADYBUG_GRAPH default hyper_graph).
  neo4j     remote Neo4j.

Retrieval env (CORTEX_BM25_AUTO):
  auto-BM25 keyword boost on search seeds (default on; 0 = off, rollback).

Parameterized MCP instances:
  dev start --server code --name shop --project SHOP --port 8790
  dev start --name shop --project SHOP --code-port 8790 --doc-port 8791
  dev stop --name shop
  make start START_ARGS=\"--server code --name shop --project SHOP --port 8790\"
  make stop STOP_ARGS=\"--name shop\"

Default MCP ports (occupied ports are advanced automatically):
  code-tiny  http://127.0.0.1:8788/mcp
  doc-tiny   http://127.0.0.1:8789/mcp

Default local storage:
  data root     ~/.cortext-harness/v1/instances/default
  qdrant        <data-root>/qdrant/{code,doc}
  falkordb      <data-root>/falkordb/{code,doc}/data.rdb
  ladybug       <data-root>/ladybug/{code,doc}/<owner>.lbug/<graph>
";

// ---------------------------------------------------------------------------
// Python shim (actions not ported yet)
// ---------------------------------------------------------------------------

/// dev.py `_run_lifecycle` (POSIX branch): spawn the lifecycle script with the
/// harness Python. cwd stays at the caller for start/doctor, else repo root.
fn run_lifecycle(action: &str, arguments: &[String]) {
    let root = repo_root();
    let lifecycle = root.join("scripts").join("mcp-lifecycle.py");
    if !lifecycle.is_file() {
        fail(&format!("Lifecycle script not found: {}", lifecycle.display()));
    }
    let caller_directory = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let cwd = if action == "start" || action == "doctor" {
        caller_directory
    } else {
        root.clone()
    };

    let mut command = Command::new(crate::util::harness_python(&root));
    command.arg(&lifecycle).arg(action).args(arguments).current_dir(&cwd);
    let status = command.status();
    let code = match status {
        Ok(s) => s.code().unwrap_or(1),
        Err(e) => {
            echo_err(&format!("[error] failed to run lifecycle: {}", e));
            1
        }
    };
    if code != 0 {
        std::process::exit(code);
    }
}

// ---------------------------------------------------------------------------
// Native action dispatchers (called from main.rs)
// ---------------------------------------------------------------------------

pub fn lifecycle_help() {
    print!("{USAGE}");
    let _ = std::io::stdout().flush();
}

pub fn build() {
    // Phase-05 semantics change (plan-sanctioned): `build` no longer
    // bootstraps a Python venv — it builds the Rust workspace and provisions
    // the ONNX Runtime dylib natively.
    let root = repo_root().join("rust");
    echo("[build] cargo build --release --workspace");
    let mut command = Command::new("cargo");
    command.args(["build", "--release", "--workspace"]).current_dir(&root);
    // macOS PyO3 extension (cortex-retrieval-py) links python symbols at
    // import time — the same RUST_LINK_ENV the Makefile passes.
    #[cfg(target_os = "macos")]
    {
        let existing = std::env::var("RUSTFLAGS").unwrap_or_default();
        command.env(
            "RUSTFLAGS",
            format!("{existing} -C link-arg=-undefined -C link-arg=dynamic_lookup"),
        );
    }
    let status = command.status();
    match status {
        Ok(s) if s.success() => {}
        Ok(s) => {
            echo_err(&format!("[error] cargo build exited with {}", s.code().unwrap_or(1)));
            std::process::exit(1);
        }
        Err(e) => {
            echo_err(&format!("[error] cargo: {e}"));
            std::process::exit(1);
        }
    }
    echo("[build] Dependency sync complete (cargo).");
    echo("[build] Ensuring ONNX Runtime dylib for cortex-embed");
    let code = crate::ensure_ort::run(false, false);
    if code != 0 {
        std::process::exit(code);
    }
}

/// D1 binary resolution (frozen contract): `CORTEX_DEV_BIN` env → installed
/// prefix `<prefix>/bin/cortex-dev[.exe]` → repo `rust/target/release/`.
pub fn resolve_dev_binary() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("CORTEX_DEV_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    let exe_suffix = if cfg!(windows) { ".exe" } else { "" };
    if let Ok(home) = std::env::var("HOME") {
        let installed = PathBuf::from(home).join(".local").join("bin").join(format!("cortex-dev{exe_suffix}"));
        if installed.is_file() {
            return Some(installed);
        }
    }
    let repo_release = repo_root()
        .join("rust")
        .join("target")
        .join("release")
        .join(format!("cortex-dev{exe_suffix}"));
    if repo_release.is_file() {
        return Some(repo_release);
    }
    None
}

pub fn install() {
    build();
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() {
        echo_err("[error] HOME is not set; cannot choose a user-local install directory.");
        std::process::exit(1);
    }
    let bin_dir = PathBuf::from(&home).join(".local").join("bin");
    let _ = std::fs::create_dir_all(&bin_dir);

    let Some(binary) = resolve_dev_binary() else {
        echo_err(
            "[error] cortex-dev binary not found after build (rust/target/release). \
             Build with: cargo build --release -p cortex-dev, or set CORTEX_DEV_BIN.",
        );
        std::process::exit(1);
    };
    // Install the binary itself into the user prefix (D1 step 2 target).
    let installed = bin_dir.join("cortex-dev");
    if std::fs::copy(&binary, &installed).is_ok() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&installed, std::fs::Permissions::from_mode(0o755));
        }
        echo(&format!("[install] Installed binary: {}", installed.display()));
    }

    // `dev` launcher implementing the D1 resolution order.
    let root = repo_root().to_string_lossy().to_string();
    let target = bin_dir.join("dev");
    let launcher = format!(
        "#!/usr/bin/env bash\n\
         set -euo pipefail\n\
         if [ -n \"${{CORTEX_DEV_BIN:-}}\" ] && [ -x \"$CORTEX_DEV_BIN\" ]; then exec \"$CORTEX_DEV_BIN\" \"$@\"; fi\n\
         if [ -x \"$HOME/.local/bin/cortex-dev\" ]; then exec \"$HOME/.local/bin/cortex-dev\" \"$@\"; fi\n\
         if [ -x \"{root}/rust/target/release/cortex-dev\" ]; then exec \"{root}/rust/target/release/cortex-dev\" \"$@\"; fi\n\
         echo '[error] cortex-dev binary not found. Install it (make install) or set CORTEX_DEV_BIN.' >&2\n\
         exit 1\n"
    );
    if std::fs::write(&target, launcher).is_err() {
        echo_err(&format!("[error] cannot write {}", target.display()));
        std::process::exit(1);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o755));
    }
    echo(&format!("[install] Installed dev command: {}", target.display()));
    let path_entries: Vec<PathBuf> = std::env::var("PATH")
        .unwrap_or_default()
        .split(':')
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .collect();
    if !path_entries.contains(&bin_dir) {
        echo(&format!(
            "[install] Add this to your shell profile if needed: export PATH=\"{}:$PATH\"",
            bin_dir.display()
        ));
    }
}

pub fn uninstall() {
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() {
        echo_err("[error] HOME is not set; cannot choose a user-local install directory.");
        std::process::exit(1);
    }
    let bin_dir = PathBuf::from(&home).join(".local").join("bin");
    for name in ["dev", "cortex-dev"] {
        let target = bin_dir.join(name);
        if target.exists() {
            let _ = std::fs::remove_file(&target);
            echo(&format!("[uninstall] Removed {name} command: {}", target.display()));
        } else {
            echo(&format!("[uninstall] {name} command was not installed at: {}", target.display()));
        }
    }
    echo("[uninstall] User PATH was left unchanged.");
}

pub fn infra_up(m: &Matches) {
    let mut arguments = Vec::new();
    if m.flag("--provision") {
        arguments.push("--provision".to_string());
    }
    run_lifecycle("infra-up", &arguments);
}

pub fn infra_down() {
    run_lifecycle("infra-down", &[]);
}

// ---------------------------------------------------------------------------
// Storage ops (native via cortex-storage)
// ---------------------------------------------------------------------------

/// `mcp_runtime_config.resolve_active_storage` port: active config → flatten
/// code/doc local-storage keys (conflict-checked) → process-env overrides →
/// `resolve_storage` on the repo root.
fn resolve_active_storage() -> Result<ResolvedStorage, String> {
    const LOCAL_KEYS: [&str; 10] = [
        "CORTEX_DATA_HOME",
        "CORTEX_STORAGE_INSTANCE",
        "CORTEX_CODE_STORAGE_OWNER",
        "CORTEX_DOC_STORAGE_OWNER",
        "QDRANT_PATH",
        "QDRANT_CODE_PATH",
        "QDRANT_DOC_PATH",
        "FALKORDB_PATH",
        "FALKORDB_CODE_PATH",
        "FALKORDB_DOC_PATH",
    ];
    let root = repo_root();
    let (config, config_path) = crate::env::active_config_path(Some(&root))
        .map(|path| {
            let text = std::fs::read_to_string(&path).unwrap_or_default();
            let value: Value = serde_json::from_str(&text).unwrap_or(json!({}));
            (value, Some(path))
        })
        .unwrap_or((json!({}), None));

    let mut flat: BTreeMap<String, String> = BTreeMap::new();
    for section in ["code", "doc"] {
        let env = config
            .get(section)
            .and_then(|s| s.get("env"))
            .and_then(|e| e.as_object())
            .cloned()
            .unwrap_or_default();
        for key in LOCAL_KEYS {
            let value = env
                .get(key)
                .map(|v| match v {
                    Value::String(s) => s.trim().to_string(),
                    other => other.to_string(),
                })
                .unwrap_or_default();
            if value.is_empty() {
                continue;
            }
            if let Some(previous) = flat.get(key) {
                if previous != &value {
                    let shown = config_path
                        .as_ref()
                        .map(|p| p.display().to_string())
                        .unwrap_or_default();
                    return Err(format!(
                        "Active config {shown} has conflicting {key} values in code/doc sections"
                    ));
                }
            }
            flat.insert(key.to_string(), value);
        }
    }

    let mut resolve_config: ConfigMap = config
        .as_object()
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    for (key, value) in flat {
        resolve_config.insert(key, Value::String(value));
    }
    for key in LOCAL_KEYS {
        if let Ok(value) = std::env::var(key) {
            let value = value.trim().to_string();
            if !value.is_empty() {
                resolve_config.insert(key.to_string(), Value::String(value));
            }
        }
    }
    resolve_storage(&root, Some(&resolve_config), &Default::default()).map_err(|e| e.to_string())
}

pub fn storage_init() {
    match storage_init_impl() {
        Ok(()) => {}
        Err(message) => {
            echo_err(&format!("[error] {message}"));
            std::process::exit(1);
        }
    }
}

fn storage_init_impl() -> Result<(), String> {
    let resolved = resolve_active_storage()?;
    ensure_layout(&resolved).map_err(|e| e.to_string())?;
    echo(&format!("[storage-init] data root     : {}", resolved.data_root.display()));
    echo(&format!("[storage-init] instance      : {}", resolved.instance_id));
    echo(&format!("[storage-init] Qdrant code   : {}", resolved.qdrant_code_path.display()));
    echo(&format!("[storage-init] Qdrant doc    : {}", resolved.qdrant_doc_path.display()));
    echo(&format!("[storage-init] FalkorDB code : {}", resolved.falkordb_code_path.display()));
    echo(&format!("[storage-init] FalkorDB doc  : {}", resolved.falkordb_doc_path.display()));
    echo(&format!("[storage-init] manifest      : {}", resolved.manifest_path.display()));
    Ok(())
}

pub fn storage_layout() {
    let outcome = (|| -> Result<(), String> {
        let resolved = resolve_active_storage()?;
        let mut leases: BTreeMap<String, Value> = BTreeMap::new();
        for (owner, backend, target) in [
            (&resolved.code_owner_id, "qdrant", resolved.qdrant_code_path.clone()),
            (&resolved.doc_owner_id, "qdrant", resolved.qdrant_doc_path.clone()),
            (&resolved.code_owner_id, "falkordb", resolved.falkordb_code_path.clone()),
            (&resolved.doc_owner_id, "falkordb", resolved.falkordb_doc_path.clone()),
        ] {
            let lock_path = target.parent().unwrap_or(Path::new(".")).join(format!(
                ".{}.cortex-owner.lock",
                target
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_default()
            ));
            let holder = match std::fs::read_to_string(&lock_path) {
                Ok(raw) if !raw.trim().is_empty() => {
                    serde_json::from_str::<Value>(raw.trim()).unwrap_or(Value::String("unreadable".into()))
                }
                _ => Value::Null,
            };
            leases.insert(format!("{owner}:{backend}"), holder);
        }
        let manifest = load_manifest(&resolved)
            .ok()
            .flatten()
            .unwrap_or(Value::Null);
        let mut qdrant = serde_json::Map::new();
        qdrant.insert(resolved.code_owner_id.clone(), json!(resolved.qdrant_code_path.to_string_lossy()));
        qdrant.insert(resolved.doc_owner_id.clone(), json!(resolved.qdrant_doc_path.to_string_lossy()));
        let mut falkordb = serde_json::Map::new();
        falkordb.insert(resolved.code_owner_id.clone(), json!(resolved.falkordb_code_path.to_string_lossy()));
        falkordb.insert(resolved.doc_owner_id.clone(), json!(resolved.falkordb_doc_path.to_string_lossy()));
        let summary = json!({
            "schema_version": resolved.schema_version,
            "instance_id": resolved.instance_id,
            "data_root": resolved.data_root.to_string_lossy(),
            "instance_root": resolved.instance_root.to_string_lossy(),
            "qdrant": Value::Object(qdrant),
            "falkordb": Value::Object(falkordb),
            "manifest": manifest,
            "leases": leases,
        });
        let mut sorted = summary;
        sort_json_in_place(&mut sorted);
        println!("{}", serde_json::to_string_pretty(&sorted).unwrap_or_default());
        Ok(())
    })();
    if let Err(message) = outcome {
        echo_err(&format!("[error] {message}"));
        std::process::exit(1);
    }
}

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

pub fn storage_migrate_layout(m: &Matches) {
    let legacy_root = m.value_or("--legacy-root", "");
    let legacy_root = if legacy_root.is_empty() {
        repo_root().to_string_lossy().to_string()
    } else {
        legacy_root.to_string()
    };
    let apply = m.flag("--apply");
    let outcome = (|| -> Result<(), String> {
        let resolved = resolve_active_storage()?;
        let report = migrate_legacy_layout(&resolved, Path::new(&legacy_root), !apply)
            .map_err(|e| e.to_string())?;
        let mode = if apply { "apply" } else { "dry-run" };
        echo(&format!("[storage-migrate-layout] mode: {mode}"));
        if report.is_empty() {
            let resolved_root = crate::env::abspath(Path::new(&legacy_root));
            echo(&format!(
                "[storage-migrate-layout] no legacy stores found under {}",
                resolved_root.display()
            ));
        }
        for item in &report {
            echo(&format!(
                "[storage-migrate-layout] {}: {} -> {} sha256={}",
                item.action,
                item.source.display(),
                item.target.display(),
                item.digest.clone().unwrap_or_default()
            ));
        }
        Ok(())
    })();
    if let Err(message) = outcome {
        echo_err(&format!("[error] {message}"));
        std::process::exit(1);
    }
}

pub fn storage_backup(m: &Matches) {
    let owner = m.value_or("--owner", "code");
    let outcome = (|| -> Result<(), String> {
        let resolved = resolve_active_storage()?;
        ensure_layout(&resolved).map_err(|e| e.to_string())?;
        let owner = owner.trim().to_lowercase();
        if owner != resolved.code_owner_id && owner != resolved.doc_owner_id {
            return Err(format!(
                "Unknown storage owner {owner:?}; choose {:?} or {:?}.",
                resolved.code_owner_id, resolved.doc_owner_id
            ));
        }
        let is_code = owner == resolved.code_owner_id;
        let qdrant_source = if is_code { resolved.qdrant_code_path.clone() } else { resolved.qdrant_doc_path.clone() };
        let falkor_source = if is_code { resolved.falkordb_code_path.clone() } else { resolved.falkordb_doc_path.clone() };

        let mut qdrant_lease = StorageLease::new(&qdrant_source, &resolved.instance_id, &owner, "qdrant");
        let falkor_lease = StorageLease::new(&falkor_source, &resolved.instance_id, &owner, "falkordb");
        qdrant_lease = qdrant_lease.acquire().map_err(|(_, e)| e.to_string())?;
        let mut falkor_lease = match falkor_lease.acquire() {
            Ok(lease) => lease,
            Err((mut lease, error)) => {
                lease.release();
                return Err(error.to_string());
            }
        };

        let timestamp = crate::util::local_strftime(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs() as i64,
            "%Y%m%dT%H%M%S",
        );
        // Python stamps `%f` microseconds; reproduce with sub-second digits.
        let micros = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .subsec_micros();
        let destination = resolved.backups_path.join(format!("{timestamp}.{micros:06}Z"));
        let mut records: Vec<Value> = Vec::new();
        for (backend, source, target) in [
            ("qdrant", qdrant_source.clone(), destination.join("qdrant").join(&owner)),
            ("falkordb", falkor_source.clone(), destination.join("falkordb").join(&owner).join("data.rdb")),
        ] {
            if !source.exists() {
                continue;
            }
            if let Some(parent) = target.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let copy = if source.is_dir() {
                copy_tree(&source, &target)
            } else {
                std::fs::copy(&source, &target).map(|_| ())
            };
            if copy.is_err() {
                return Err(format!("Backup copy failed for {}", source.display()));
            }
            let source_digest = path_digest(&source).map_err(|e| e.to_string())?;
            if path_digest(&target).map_err(|e| e.to_string())? != source_digest {
                return Err(format!("Backup verification failed for {}", source.display()));
            }
            records.push(json!({
                "backend": backend,
                "source": source.to_string_lossy(),
                "target": target.to_string_lossy(),
                "sha256": source_digest,
            }));
        }
        let manifest = json!({
            "schema_version": resolved.schema_version,
            "instance_id": resolved.instance_id,
            "owner_id": owner,
            "created_at": crate::util::iso_utc_now(),
            "items": records,
        });
        let mut sorted = manifest;
        sort_json_in_place(&mut sorted);
        std::fs::create_dir_all(&destination).map_err(|e| e.to_string())?;
        std::fs::write(
            destination.join("manifest.json"),
            serde_json::to_string_pretty(&sorted).unwrap_or_default() + "\n",
        )
        .map_err(|e| e.to_string())?;
        falkor_lease.release();
        qdrant_lease.release();
        echo(&format!("[storage-backup] verified backup: {}", destination.display()));
        Ok(())
    })();
    if let Err(message) = outcome {
        echo_err(&format!("[error] {message}"));
        std::process::exit(1);
    }
}

fn copy_tree(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
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
            }
        }
    }
    Ok(())
}

/// `_path_digest` — sha256 over (relative path bytes + file contents).
fn path_digest(path: &Path) -> std::result::Result<String, String> {
    use sha2::Digest as _;
    let mut hasher = sha2::Sha256::new();
    let mut feed = |bytes: &[u8], contents: &Path| -> Result<(), String> {
        hasher_update(&mut hasher, bytes);
        let mut file = std::fs::File::open(contents).map_err(|e| e.to_string())?;
        let mut buffer = [0u8; 1024 * 1024];
        loop {
            let read = std::io::Read::read(&mut file, &mut buffer).map_err(|e| e.to_string())?;
            if read == 0 {
                break;
            }
            hasher_update(&mut hasher, &buffer[..read]);
        }
        Ok(())
    };
    if path.is_file() {
        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        feed(name.as_bytes(), path)?;
    } else {
        let mut entries: Vec<PathBuf> = Vec::new();
        collect_files(path, path, &mut entries);
        entries.sort();
        for entry in entries {
            let relative = entry
                .strip_prefix(path)
                .map_err(|_| "entry outside source tree".to_string())?;
            feed(relative.to_string_lossy().as_bytes(), &entry)?;
        }
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}

// Generic-free helper: the closure above borrows hasher; this avoids fighting
// the borrow checker with a closure capturing `&mut` across calls.
fn hasher_update(hasher: &mut sha2::Sha256, bytes: &[u8]) {
    use sha2::Digest as _;
    hasher.update(bytes);
}

fn collect_files(base: &Path, root: &Path, out: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(base).into_iter().flatten().flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_files(&path, root, out);
        } else {
            out.push(path);
        }
    }
}

pub fn storage_stop() {
    echo("[storage-stop] Local storage has no lifecycle to stop.");
}

pub fn start(m: &Matches) {
    let values: [(&str, Option<String>); 14] = [
        ("--name", m.value("--name").map(String::from)),
        ("--project", m.value("--project").map(String::from)),
        ("--database", m.value("--database").map(String::from)),
        ("--code-database", m.value("--code-database").map(String::from)),
        ("--doc-database", m.value("--doc-database").map(String::from)),
        ("--port", m.value("--port").map(String::from)),
        ("--code-port", m.value("--code-port").map(String::from)),
        ("--doc-port", m.value("--doc-port").map(String::from)),
        ("--host", m.value("--host").map(String::from)),
        ("--path", m.value("--path").map(String::from)),
        ("--provider", m.value("--provider").map(String::from)),
        ("--collection", m.value("--collection").map(String::from)),
        ("--code-collection", m.value("--code-collection").map(String::from)),
        ("--doc-collection", m.value("--doc-collection").map(String::from)),
    ];
    let mut arguments: Vec<String> = values
        .into_iter()
        .filter_map(|(option, value)| value.map(|v| vec![option.to_string(), v]))
        .flatten()
        .collect();
    let server = m.value_or("--server", "all");
    if server != "all" {
        arguments.splice(0..0, ["--server".to_string(), server]);
    }
    run_lifecycle("start", &arguments);
}

pub fn stop(m: &Matches) {
    match m.value("--name") {
        Some(name) => run_lifecycle("stop", &["--name".to_string(), name.to_string()]),
        None => run_lifecycle("stop", &[]),
    }
}

pub fn doctor() {
    run_lifecycle("doctor", &[]);
}

pub fn ensure_ort(m: &Matches) {
    let force = m.flag("--force");
    let print_path = m.flag("--print-path");
    let code = crate::ensure_ort::run(force, print_path);
    if code != 0 {
        std::process::exit(code);
    }
}

/// `dev mcp-gates` — native replica of the gate printer.
pub fn mcp_gates() {
    let legacy_pause = std::env::var("CORTEX_MCP_PAUSE_BY_INSTANCE")
        .map(|v| v.trim() == "0")
        .unwrap_or(false);
    let storage_instance = std::env::var("CORTEX_STORAGE_INSTANCE")
        .map(|v| v.trim().to_string())
        .unwrap_or_default();
    let storage_instance = if storage_instance.is_empty() {
        "default".to_string()
    } else {
        storage_instance
    };
    echo("Per-instance MCP isolation gates");
    echo(&"─".repeat(40));
    echo(&format!(
        "  CORTEX_STORAGE_INSTANCE        = {}",
        storage_instance
    ));
    echo(&format!(
        "  CORTEX_MCP_PAUSE_BY_INSTANCE    = {}",
        if legacy_pause {
            "0 (legacy: pause by pattern)"
        } else {
            "unset (pause by instance on)"
        }
    ));
}

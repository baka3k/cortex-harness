//! Lifecycle commands — phase-05 port of `scripts/mcp-lifecycle.py`, action
//! by action (no big-bang). Actions still shimmed to the Python script are
//! listed in `SHIMMED_ACTIONS` with the reason (phase-05 report); each native
//! port replaces its shim here.
//!
//! Native: help, build (new semantics: cargo workspace + native ensure-ort),
//! install/uninstall (D1 launcher), storage-init/layout/migrate-layout/
//! backup/stop, ensure-ort, start, stop (phase-05b).
//! Shimmed: doctor, infra-up, infra-down.

use crate::parser::Matches;
use crate::util::repo_root;
use crate::util::{echo, echo_err, fail};
use crate::mcp_state;
use cortex_storage::config::{resolve_storage, ConfigMap, ResolvedStorage};
use cortex_storage::lease::StorageLease;
use cortex_storage::layout::{ensure_layout, load_manifest};
use cortex_storage::migration::migrate_legacy_layout;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
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

pub fn install() {
    build();
    let home = std::env::var("HOME").unwrap_or_default();
    if home.is_empty() {
        echo_err("[error] HOME is not set; cannot choose a user-local install directory.");
        std::process::exit(1);
    }
    let bin_dir = PathBuf::from(&home).join(".local").join("bin");
    let _ = std::fs::create_dir_all(&bin_dir);

    // Install the binary itself into the user prefix (D1 step 2 target).
    // The source must never resolve to the destination itself: fs::copy onto
    // itself truncates the file to zero bytes while still returning Ok. The
    // D1 installed-prefix step is also skipped — the fresh repo build always
    // wins over whatever the prefix already holds.
    let installed = bin_dir.join("cortex-dev");
    let exe_suffix = if cfg!(windows) { ".exe" } else { "" };
    let mut sources: Vec<PathBuf> = Vec::new();
    if let Ok(explicit) = std::env::var("CORTEX_DEV_BIN") {
        sources.push(PathBuf::from(explicit));
    }
    sources.push(
        repo_root()
            .join("rust")
            .join("target")
            .join("release")
            .join(format!("cortex-dev{exe_suffix}")),
    );
    let Some(binary) = sources.into_iter().find(|p| p.is_file() && *p != installed) else {
        echo_err(
            "[error] cortex-dev binary not found after build (rust/target/release). \
             Build with: cargo build --release -p cortex-dev, or set CORTEX_DEV_BIN.",
        );
        std::process::exit(1);
    };
    let source_len = std::fs::metadata(&binary).map(|m| m.len()).unwrap_or(0);
    // Never rewrite the destination inode in place: macOS caches the code
    // signature per vnode, so an in-place overwrite makes every later exec
    // of the installed binary die with SIGKILL even though the file itself
    // is valid. Stage + rename swaps in a fresh inode (atomic replace).
    let staged = bin_dir.join(format!(".cortex-dev.staged.{}", std::process::id()));
    let _ = std::fs::remove_file(&staged);
    let copied_ok = match std::fs::copy(&binary, &staged) {
        Ok(n) => n > 0 && n == source_len,
        Err(_) => false,
    };
    if !copied_ok {
        let _ = std::fs::remove_file(&staged);
        echo_err(&format!(
            "[error] cannot copy {} -> {}",
            binary.display(),
            installed.display()
        ));
        std::process::exit(1);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&staged, std::fs::Permissions::from_mode(0o755));
    }
    if std::fs::rename(&staged, &installed).is_err() {
        let _ = std::fs::remove_file(&staged);
        echo_err(&format!(
            "[error] cannot move staged binary into place at {}",
            installed.display()
        ));
        std::process::exit(1);
    }
    echo(&format!("[install] Installed binary: {}", installed.display()));

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

// ---------------------------------------------------------------------------
// start / stop — phase-05b native port of `_invoke_start` / `_invoke_stop`
// ---------------------------------------------------------------------------

/// dev.py `SERVERS` — the lifecycle launcher runs `<svc>/mcp.sh`, which is a
/// different surface from `dev mcp start`'s `SERVICES` (direct entry script +
/// rust/python backend switch).
#[derive(Debug)]
struct LifecycleServer {
    name: &'static str,
    work_dir: PathBuf,
    script: PathBuf,
    port: u16,
}

fn lifecycle_servers() -> Vec<LifecycleServer> {
    let root = repo_root();
    ["code-tiny", "doc-tiny"]
        .iter()
        .map(|name| LifecycleServer {
            name,
            work_dir: root.join(name),
            script: root.join(name).join("mcp.sh"),
            port: if *name == "code-tiny" { 8788 } else { 8789 },
        })
        .collect()
}

/// dev.py `start_options` namespace.
struct StartOptions {
    server: String,
    name: Option<String>,
    project: Option<String>,
    database: Option<String>,
    code_database: Option<String>,
    doc_database: Option<String>,
    port: Option<u16>,
    code_port: Option<u16>,
    doc_port: Option<u16>,
    host: String,
    path: String,
    provider: Option<String>,
    collection: Option<String>,
    code_collection: Option<String>,
    doc_collection: Option<String>,
}

/// argparse treats `""` as absent for every `or`-chain default.
fn text(value: Option<&str>) -> Option<String> {
    value.filter(|v| !v.is_empty()).map(|v| v.to_string())
}

fn parse_port(option: &str, value: Option<&str>) -> Option<u16> {
    let raw = text(value)?;
    match raw.parse::<u16>() {
        Ok(port) if (1..=65535).contains(&port) => Some(port),
        _ => usage_error(&format!("{option} must be between 1 and 65535")),
    }
}

/// argparse `parser.error` equivalent — usage exit code 2.
fn usage_error(message: &str) -> ! {
    echo_err(&format!("Error: {message}"));
    echo_err("Try 'dev start --help' for help.");
    std::process::exit(2);
}

/// Runtime errors surface as `[error] …` on stdout with exit 1, exactly like
/// `mcp-lifecycle.py`'s `main()` handler.
fn runtime_error(message: &str) -> ! {
    echo(&format!("[error] {message}"));
    std::process::exit(1);
}

impl StartOptions {
    fn from_matches(m: &Matches) -> StartOptions {
        let mut path = m.value_or("--path", "/mcp");
        if !path.starts_with('/') {
            path = format!("/{path}");
        }
        let server = m.value_or("--server", "all");
        if server == "code" && m.value("--doc-port").is_some() {
            usage_error("--doc-port cannot be used with --server code");
        }
        if server == "doc" && m.value("--code-port").is_some() {
            usage_error("--code-port cannot be used with --server doc");
        }
        StartOptions {
            server,
            name: text(m.value("--name")),
            project: text(m.value("--project")),
            database: text(m.value("--database")),
            code_database: text(m.value("--code-database")),
            doc_database: text(m.value("--doc-database")),
            port: parse_port("--port", m.value("--port")),
            code_port: parse_port("--code-port", m.value("--code-port")),
            doc_port: parse_port("--doc-port", m.value("--doc-port")),
            host: m.value_or("--host", "127.0.0.1"),
            path,
            provider: text(m.value("--provider")),
            collection: text(m.value("--collection")),
            code_collection: text(m.value("--code-collection")),
            doc_collection: text(m.value("--doc-collection")),
        }
    }
}

fn string_env(payload: &serde_json::Map<String, Value>) -> BTreeMap<String, String> {
    payload
        .iter()
        .map(|(key, value)| {
            let text = match value {
                Value::String(text) => text.clone(),
                other => other.to_string(),
            };
            (key.clone(), text)
        })
        .collect()
}

/// dev.py `_selected_servers`.
fn selected_servers(options: &StartOptions) -> Result<Vec<LifecycleServer>, String> {
    let mut selected: Vec<LifecycleServer> = lifecycle_servers()
        .into_iter()
        .filter(|server| options.server == "all" || server.name.starts_with(&options.server))
        .collect();
    if options.port.is_some() && options.server == "all" {
        return Err(
            "--port requires --server code or --server doc; use --code-port/--doc-port for both."
                .to_string(),
        );
    }
    if let Some(port) = options.port
        && let Some(first) = selected.first_mut()
    {
        first.port = port;
    }
    for server in &mut selected {
        if server.name == "code-tiny"
            && let Some(port) = options.code_port
        {
            if options.port.is_some() {
                return Err("Use either --port or --code-port, not both.".to_string());
            }
            server.port = port;
        }
        if server.name == "doc-tiny"
            && let Some(port) = options.doc_port
        {
            if options.port.is_some() {
                return Err("Use either --port or --doc-port, not both.".to_string());
            }
            server.port = port;
        }
    }
    let ports: Vec<u16> = selected.iter().map(|server| server.port).collect();
    if ports.iter().collect::<BTreeSet<_>>().len() != ports.len() {
        return Err("Each selected MCP server must use a different port.".to_string());
    }
    Ok(selected)
}

/// dev.py `_default_graph_env_exports` — the last-resort defaults for a launch
/// with no harness config (the generated `.active.env` wins when present).
fn default_graph_env_exports(server_name: &str) -> String {
    let scoped_provider = if server_name == "doc-tiny" {
        "DOC_GRAPH_PROVIDER"
    } else {
        "CODE_GRAPH_PROVIDER"
    };
    format!(
        "# Default local graph backend for make start.\n\
         export GRAPH_PROVIDER=\"${{GRAPH_PROVIDER:-{default}}}\"\n\
         export {scoped_provider}=\"${{{scoped_provider}:-${{GRAPH_PROVIDER}}}}\"\n\
         export FALKORDB_GRAPH=\"${{FALKORDB_GRAPH:-hyper_graph}}\"\n",
        default = "falkordb"
    )
}

/// dev.py `_runtime_overrides`. The graph key follows the active provider, so a
/// `--database` on a ladybug launch lands on `LADYBUG_GRAPH` (the Python shim
/// wrote `NEO4J_DB` for anything that was not falkordb).
fn runtime_overrides(
    options: &StartOptions,
    server_name: &str,
    instance: &str,
    multiple_servers: bool,
    environment: &serde_json::Map<String, Value>,
) -> Result<Vec<(String, String)>, String> {
    let is_code = server_name == "code-tiny";
    let scoped_provider = if is_code {
        "CODE_GRAPH_PROVIDER"
    } else {
        "DOC_GRAPH_PROVIDER"
    };
    let database = {
        let specific = if is_code {
            options.code_database.clone()
        } else {
            options.doc_database.clone()
        };
        specific.or_else(|| options.database.clone()).or_else(|| options.project.clone())
    };
    let collection = {
        let specific = if is_code {
            options.code_collection.clone()
        } else {
            options.doc_collection.clone()
        };
        specific.or_else(|| options.collection.clone()).or_else(|| options.project.clone())
    };
    let mcp_name = if multiple_servers {
        format!("{instance}-{}", if is_code { "code" } else { "doc" })
    } else {
        instance.to_string()
    };
    let mut overrides: Vec<(String, String)> = vec![
        ("CORTEX_MCP_NAME".to_string(), mcp_name),
        (
            "CORTEX_STORAGE_INSTANCE".to_string(),
            instance.to_lowercase().replace('.', "-"),
        ),
        (
            "CORTEX_STORAGE_OWNER".to_string(),
            if is_code { "code" } else { "doc" }.to_string(),
        ),
    ];
    if let Some(project) = &options.project {
        for key in ["PROJECT_ID", "CORTEX_STORAGE_PROJECT_ID", "PROJECT_NAME"] {
            overrides.push((key.to_string(), project.clone()));
        }
    }
    let mut provider_environment = environment.clone();
    if let Some(provider) = &options.provider {
        provider_environment.insert("GRAPH_PROVIDER".to_string(), Value::String(provider.clone()));
        provider_environment
            .insert(scoped_provider.to_string(), Value::String(provider.clone()));
    }
    let provider = crate::config::graph_provider(&Value::Object(provider_environment), scoped_provider)?;
    if let Some(database) = database {
        overrides.push((
            mcp_state::graph_key(&provider).to_string(),
            database,
        ));
    }
    if let Some(collection) = collection {
        overrides.push((
            if is_code {
                "QDRANT_COLLECTION"
            } else {
                "QDRANT_COLLECTION_DOC"
            }
            .to_string(),
            collection,
        ));
    }
    if let Some(provider_value) = &options.provider {
        overrides.push(("GRAPH_PROVIDER".to_string(), provider_value.clone()));
        overrides.push((scoped_provider.to_string(), provider_value.clone()));
    }
    Ok(overrides)
}

/// dev.py `_terminal_command` — macOS opens a Terminal.app window, POSIX
/// desktops fall back to a terminal emulator, and the wrapper reports its own
/// pid so the record survives the `exec bash mcp.sh` hand-over.
fn terminal_command(wrapper: &Path) -> Result<Vec<String>, String> {
    let wrapper_path = wrapper.to_string_lossy().to_string();
    #[cfg(target_os = "macos")]
    {
        let Some(osascript) = which("osascript") else {
            return Err(
                "osascript was not found; cannot open macOS Terminal windows.".to_string(),
            );
        };
        let quoted = mcp_state::shlex_quote(&wrapper_path);
        let script = format!(
            "tell application \"Terminal\" to do script {}",
            serde_json::to_string(&quoted).unwrap_or_else(|_| format!("\"{quoted}\""))
        );
        Ok(vec![
            osascript.to_string_lossy().to_string(),
            "-e".to_string(),
            script,
        ])
    }
    #[cfg(not(target_os = "macos"))]
    {
        for name in ["gnome-terminal", "x-terminal-emulator", "xterm"] {
            let Some(executable) = which(name) else {
                continue;
            };
            let arguments: Vec<&str> = if name == "gnome-terminal" {
                vec!["--", "bash", wrapper_path.as_str()]
            } else {
                vec!["-e", "bash", wrapper_path.as_str()]
            };
            let mut command = vec![executable.to_string_lossy().to_string()];
            command.extend(arguments.iter().map(|value| (*value).to_string()));
            return Ok(command);
        }
        Err(
            "No supported terminal emulator found (gnome-terminal, x-terminal-emulator, or xterm)."
                .to_string(),
        )
    }
}

fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var("PATH").ok()?;
    for directory in std::env::split_paths(&path) {
        let candidate = directory.join(name);
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

#[cfg(unix)]
fn is_executable(candidate: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    match std::fs::metadata(candidate) {
        Ok(metadata) => {
            metadata.is_file() && metadata.permissions().mode() & 0o111 != 0
        }
        Err(_) => false,
    }
}

#[cfg(not(unix))]
fn is_executable(candidate: &Path) -> bool {
    candidate.is_file()
}

fn bare_start_options() -> StartOptions {
    StartOptions {
        server: "all".to_string(),
        name: None,
        project: None,
        database: None,
        code_database: None,
        doc_database: None,
        port: None,
        code_port: None,
        doc_port: None,
        host: "127.0.0.1".to_string(),
        path: "/mcp".to_string(),
        provider: None,
        collection: None,
        code_collection: None,
        doc_collection: None,
    }
}

fn invoke_start(options: Option<StartOptions>) -> Result<(), String> {
    // dev.py distinguishes a bare `start` (reuse running servers, auto-advance
    // ports) from any parameterized invocation (stop-then-start, hard port
    // conflict) by whether argv carried options at all.
    let custom = options.is_some();
    let options = options.unwrap_or_else(bare_start_options);

    let directory = mcp_state::state_dir();
    std::fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    let caller = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let (config_root, config_path) = crate::env::resolve_start_config(&caller, &repo_root());
    if !config_path.is_file() {
        return Err(format!("MCP config not found: {}", config_path.display()));
    }

    let servers = selected_servers(&options)?;
    let multiple_servers = servers.len() > 1;
    let mut runtime_environments: BTreeMap<String, serde_json::Map<String, Value>> = BTreeMap::new();
    for server in &servers {
        runtime_environments.insert(
            server.name.to_string(),
            crate::env::mcp_env_from_config(&config_root, server.name),
        );
    }

    let (instance, mut records) = if custom {
        let requested = options
            .name
            .clone()
            .or_else(|| options.project.clone())
            .or_else(|| options.database.clone())
            .unwrap_or_default();
        let instance = mcp_state::validate_instance_name(&requested)?;
        mcp_state::stop(Some(instance.as_str()));
        (instance, mcp_state::read_records())
    } else {
        let first = runtime_environments
            .get(servers[0].name)
            .cloned()
            .unwrap_or_default();
        let scoped = if servers[0].name == "doc-tiny" {
            "DOC_GRAPH_PROVIDER"
        } else {
            "CODE_GRAPH_PROVIDER"
        };
        let provider = crate::config::graph_provider(&Value::Object(first.clone()), scoped)
            .unwrap_or_else(|_| "falkordb".to_string());
        (
            mcp_state::config_instance(
                &string_env(&first),
                mcp_state::graph_key(&provider),
            )?,
            mcp_state::read_records(),
        )
    };

    let processes = crate::procinfo::process_table();
    let mut reserved: BTreeSet<u16> = BTreeSet::new();

    for server in servers {
        let mut runtime_env = runtime_environments
            .remove(server.name)
            .unwrap_or_default();
        if custom {
            for (key, value) in runtime_overrides(
                &options,
                server.name,
                &instance,
                multiple_servers,
                &runtime_env,
            )? {
                runtime_env.insert(key, Value::String(value));
            }
        }
        let script = server.script.clone();
        if !script.is_file() {
            return Err(format!("MCP script not found: {}", script.display()));
        }
        let scoped_provider = if server.name == "doc-tiny" {
            "DOC_GRAPH_PROVIDER"
        } else {
            "CODE_GRAPH_PROVIDER"
        };
        let provider =
            crate::env::isolate_graph_provider_environment(&mut runtime_env, scoped_provider);
        let graph = runtime_env
            .get(mcp_state::graph_key(&provider))
            .map(|value| match value {
                Value::String(text) => text.clone(),
                other => other.to_string(),
            })
            .unwrap_or_default();

        if !custom {
            let reused = records.iter().find(|record| {
                record
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    == server.name
                    && record
                        .get("graph")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        == graph
                    && mcp_state::record_is_live(record, &processes)
            });
            if let Some(existing) = reused {
                let host = existing
                    .get("host")
                    .and_then(Value::as_str)
                    .unwrap_or(options.host.as_str());
                let port = existing.get("port").and_then(Value::as_i64).unwrap_or(0);
                echo(&format!(
                    "[start] Reusing {instance}/{} on {host}:{port} (graph={graph})",
                    server.name
                ));
                continue;
            }
        }

        let port = if custom {
            if mcp_state::tcp_port_open(&options.host, server.port) {
                return Err(format!(
                    "Port already in use: {}:{}",
                    options.host, server.port
                ));
            }
            server.port
        } else {
            mcp_state::next_available_port(&options.host, server.port, &mut reserved)?
        };

        let (wrapper, pid_path, runtime_env_path, _) =
            mcp_state::state_paths(&instance, server.name);
        let exports = mcp_state::format_bash_exports(&string_env(&runtime_env))?;
        // Python appends the trailing newline only for a non-empty overlay.
        let exports = if runtime_env.is_empty() {
            exports
        } else {
            format!("{exports}\n")
        };
        mcp_state::write_active_env(&runtime_env_path, &exports)
            .map_err(|error| error.to_string())?;

        let activate = repo_root()
            .join(".venv")
            .join("bin")
            .join("activate")
            .to_string_lossy()
            .to_string();
        let activate = mcp_state::shlex_quote(&activate);
        let mut body = String::from("#!/usr/bin/env bash\nset -euo pipefail\n");
        body.push_str(&format!(
            "printf '%s' \"$$\" > {}\n",
            mcp_state::shlex_quote(&pid_path.to_string_lossy())
        ));
        body.push_str(&format!("if [ -f {activate} ]; then\n  source {activate}\nfi\n"));
        body.push_str(&default_graph_env_exports(server.name));
        body.push_str(&format!(
            "export CORTEX_HARNESS_ENV_FILE={}\n",
            mcp_state::shlex_quote(&runtime_env_path.to_string_lossy())
        ));
        body.push_str(&format!(
            "cd {}\n",
            mcp_state::shlex_quote(&server.work_dir.to_string_lossy())
        ));
        body.push_str(&format!(
            "exec bash {} --host {} --port {} --path {}\n",
            mcp_state::shlex_quote(&script.to_string_lossy()),
            mcp_state::shlex_quote(&options.host),
            port,
            mcp_state::shlex_quote(&options.path),
        ));
        mcp_state::write_executable(&wrapper, &body).map_err(|error| error.to_string())?;
        let _ = std::fs::remove_file(&pid_path);

        let command = terminal_command(&wrapper)?;
        let status = Command::new(&command[0])
            .args(&command[1..])
            .current_dir(&caller)
            .status()
            .map_err(|error| error.to_string())?;
        if !status.success() {
            return Err(format!(
                "Terminal launch failed ({} exited {}).",
                command[0],
                status.code().unwrap_or(1)
            ));
        }

        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !pid_path.is_file() && std::time::Instant::now() < deadline {
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        if !pid_path.is_file() {
            return Err(format!(
                "Terminal opened, but {} did not report its process ID.",
                server.name
            ));
        }
        let pid_text = std::fs::read_to_string(&pid_path).map_err(|error| error.to_string())?;
        let pid: i64 = pid_text
            .trim()
            .parse()
            .map_err(|_| format!("{} reported an invalid pid.", server.name))?;

        records.push(json!({
            "name": server.name,
            "instance": instance,
            "pid": pid,
            "script": script.to_string_lossy(),
            "port": port,
            "host": options.host,
            "path": options.path,
            "endpoint": format!("http://{}:{}{}", options.host, port, options.path),
            "graph": graph,
            "config_path": config_path.to_string_lossy(),
            "runtime_env_path": runtime_env_path.to_string_lossy(),
            "project_id": string_env(&runtime_env)
                .get("PROJECT_ID")
                .cloned()
                .unwrap_or_default(),
        }));
        echo(&format!(
            "[start] Started {instance}/{} in terminal PID {pid} on {port} (graph={graph})",
            server.name
        ));
    }

    mcp_state::write_records(&records);
    echo("[start] MCP terminals opened. Logs are visible in their own windows.");
    Ok(())
}

pub fn start(m: &Matches) {
    let options = if m.opts.is_empty() {
        None
    } else {
        Some(StartOptions::from_matches(m))
    };
    if let Err(error) = invoke_start(options) {
        runtime_error(&error);
    }
}

pub fn stop(m: &Matches) {
    let instance = match text(m.value("--name")) {
        Some(name) => match mcp_state::validate_instance_name(&name) {
            Ok(validated) => Some(validated),
            Err(error) => runtime_error(&error),
        },
        None => None,
    };
    mcp_state::stop(instance.as_deref());
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

#[cfg(test)]
mod tests {
    use super::*;

    fn options() -> StartOptions {
        bare_start_options()
    }

    fn graph_env(provider: &str) -> serde_json::Map<String, Value> {
        let mut env = serde_json::Map::new();
        env.insert("GRAPH_PROVIDER".to_string(), Value::String(provider.to_string()));
        env.insert(
            "CODE_GRAPH_PROVIDER".to_string(),
            Value::String(provider.to_string()),
        );
        env.insert("PROJECT_ID".to_string(), Value::String("cortext".to_string()));
        env
    }

    #[test]
    fn selected_servers_keeps_the_python_validation_messages() {
        let mut both = options();
        both.port = Some(8790);
        assert_eq!(
            selected_servers(&both).unwrap_err(),
            "--port requires --server code or --server doc; use --code-port/--doc-port for both."
        );

        let mut code_only = options();
        code_only.server = "code".to_string();
        code_only.port = Some(8790);
        code_only.code_port = Some(8791);
        assert_eq!(
            selected_servers(&code_only).unwrap_err(),
            "Use either --port or --code-port, not both."
        );

        let mut colliding = options();
        colliding.code_port = Some(8789);
        assert_eq!(
            selected_servers(&colliding).unwrap_err(),
            "Each selected MCP server must use a different port."
        );

        let servers = selected_servers(&options()).unwrap();
        assert_eq!(servers.len(), 2);
        assert_eq!((servers[0].port, servers[1].port), (8788, 8789));

        let mut doc_only = options();
        doc_only.server = "doc".to_string();
        doc_only.doc_port = Some(9001);
        let servers = selected_servers(&doc_only).unwrap();
        assert_eq!(servers.len(), 1);
        assert_eq!((servers[0].name, servers[0].port), ("doc-tiny", 9001));
    }

    #[test]
    fn database_override_follows_the_active_graph_provider() {
        let mut start = options();
        start.database = Some("shop-db".to_string());

        let ladybug =
            runtime_overrides(&start, "code-tiny", "shop", false, &graph_env("ladybug")).unwrap();
        assert!(ladybug.contains(&("LADYBUG_GRAPH".to_string(), "shop-db".to_string())));
        assert!(!ladybug.iter().any(|(key, _)| key == "NEO4J_DB"));

        let falkor =
            runtime_overrides(&start, "code-tiny", "shop", false, &graph_env("falkordb")).unwrap();
        assert!(falkor.contains(&("FALKORDB_GRAPH".to_string(), "shop-db".to_string())));

        let neo =
            runtime_overrides(&start, "code-tiny", "shop", false, &graph_env("neo4j")).unwrap();
        assert!(neo.contains(&("NEO4J_DB".to_string(), "shop-db".to_string())));

        // An unsupported provider fails closed instead of picking a default.
        assert!(runtime_overrides(&start, "code-tiny", "shop", false, &graph_env("redisgraph"))
            .is_err());
    }

    #[test]
    fn overrides_match_the_python_key_and_value_defaults() {
        let mut start = options();
        start.project = Some("SHOP".to_string());
        start.collection = Some("shop-vec".to_string());
        let overrides =
            runtime_overrides(&start, "doc-tiny", "a.b", true, &graph_env("ladybug")).unwrap();
        let map: BTreeMap<String, String> = overrides.into_iter().collect();
        // Multiple servers suffix the MCP name; the instance dot becomes a dash.
        assert_eq!(map["CORTEX_MCP_NAME"], "a.b-doc");
        assert_eq!(map["CORTEX_STORAGE_INSTANCE"], "a-b");
        assert_eq!(map["CORTEX_STORAGE_OWNER"], "doc");
        assert_eq!(map["PROJECT_ID"], "SHOP");
        assert_eq!(map["PROJECT_NAME"], "SHOP");
        assert_eq!(map["QDRANT_COLLECTION_DOC"], "shop-vec");
        assert!(!map.contains_key("QDRANT_COLLECTION"));
    }

    #[test]
    fn wrapper_graph_defaults_stay_byte_compatible() {
        assert_eq!(
            default_graph_env_exports("code-tiny"),
            "# Default local graph backend for make start.\n\
             export GRAPH_PROVIDER=\"${GRAPH_PROVIDER:-falkordb}\"\n\
             export CODE_GRAPH_PROVIDER=\"${CODE_GRAPH_PROVIDER:-${GRAPH_PROVIDER}}\"\n\
             export FALKORDB_GRAPH=\"${FALKORDB_GRAPH:-hyper_graph}\"\n"
        );
        assert!(default_graph_env_exports("doc-tiny").contains("export DOC_GRAPH_PROVIDER="));
    }

    #[test]
    fn instance_name_derives_from_the_provider_graph_key() {
        let mut env = graph_env("ladybug");
        env.insert("PROJECT_ID".to_string(), Value::String(String::new()));
        env.insert("LADYBUG_GRAPH".to_string(), Value::String("shop_graph".to_string()));
        let map = string_env(&env);
        assert_eq!(
            mcp_state::config_instance(&map, mcp_state::graph_key("ladybug")).unwrap(),
            "shop_graph"
        );
        // The same env seen through the falkordb key has no graph value to use.
        assert_eq!(
            mcp_state::config_instance(&map, mcp_state::graph_key("falkordb")).unwrap(),
            "cortext"
        );
    }
}

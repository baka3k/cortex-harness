//! `dev sync code` — orchestrator shell around the native `cortex-sync`
//! binary (phase-04 wire: the Python orchestrator spawn is retired;
//! analyzer selection is the cortex-sync registry's job), plus the native
//! scan-root selection, retry loop, state files, and summary printer.
//! `dev sync doc` lives in `cmds/docsync.rs` (forced-Python fallback).
//! The per-process storage environment comes from the native env layer.

use super::docsync::sha256_hex_hex;
use crate::config::{graph_provider, ignore_folders, load_active_config, save_config};
use crate::parser::Matches;
use crate::util::{echo, echo_err, fail, fnmatch, local_strftime, prompt};
use serde_json::{json, Value};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;


/// Languages served by the cortex-sync analyzer registry (phase-04: analyzer
/// selection lives in cortex-sync; only the `sync code all` banner needs the
/// names here).
const SYNC_CODE_ALL_LANGS: &[&str] = &[
    "cobol", "dart", "delphi", "go", "perl", "shell", "jp1", "kotlin", "java", "ts", "js",
    "php", "sql", "plsql", "cplus", "csharp", "python", "rust", "swift", "vbnet", "vb6",
    "vba", "vbscript", "android_java", "android_kotlin", "android_mixed", "spring",
    "servlet_jsp", "mybatis", "struts", "flutter", "aspnet_framework", "aspnet_core",
    "fastapi_django", "express_js", "laravel", "database_sql", "database_plsql",
];

// ---------------------------------------------------------------------------
// Scan-root helpers (native ports)
// ---------------------------------------------------------------------------

/// dev.py `_dedupe_scan_roots`: drop canonical duplicates and descendants of
/// already-selected roots.
pub fn dedupe_scan_roots(folders: &[String], project_path: &Path) -> Vec<String> {
    let mut selected: Vec<(String, PathBuf)> = Vec::new();
    for folder in folders {
        let candidate = absolute_or_join(folder, project_path);
        let canonical = super::init::resolve_path(&candidate);
        if selected
            .iter()
            .any(|(_, existing)| canonical == *existing || canonical.starts_with(existing))
        {
            continue;
        }
        selected.retain(|(_, existing)| !existing.starts_with(&canonical));
        selected.push((folder.clone(), canonical));
    }
    selected.into_iter().map(|(raw, _)| raw).collect()
}

/// dev.py `_validate_selected_scan_roots`: reject a nested configured root.
pub fn validate_selected_scan_roots(
    selected: &[String],
    configured: &[String],
    project_path: &Path,
) {
    let configured_canonical: Vec<(String, PathBuf)> = configured
        .iter()
        .map(|folder| {
            let candidate = absolute_or_join(folder, project_path);
            (folder.clone(), super::init::resolve_path(&candidate))
        })
        .collect();
    for folder in selected {
        let candidate = absolute_or_join(folder, project_path);
        let canonical = super::init::resolve_path(&candidate);
        let ancestors: Vec<&String> = configured_canonical
            .iter()
            .filter(|(_, root)| *root != canonical && canonical.starts_with(root))
            .map(|(raw, _)| raw)
            .collect();
        if !ancestors.is_empty() {
            fail(&format!(
                "nested_scan_root_changes_identity_namespace: selected={:?} configured_ancestor={:?}; select the configured ancestor root instead",
                folder, ancestors[0]
            ));
        }
    }
}

/// dev.py `_warn_scan_roots_matching_ignores`.
pub fn warn_scan_roots_matching_ignores(selected: &[String], extra_ignores: &[String]) {
    if extra_ignores.is_empty() {
        return;
    }
    for folder in selected {
        let name = Path::new(folder)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        if match_ignore(&name, extra_ignores) {
            echo_err(&format!(
                "[warn] Scan root '{}' matches the configured ignore list \
                 (ignore.folders) but was selected explicitly — syncing it anyway.",
                folder
            ));
        }
    }
}

/// dev.py `_match_ignore` (exact name or fnmatch glob).
fn match_ignore(name: &str, patterns: &[String]) -> bool {
    patterns.iter().any(|p| p == name) || fnmatch(name, patterns)
}

pub(super) fn absolute_or_join(folder: &str, project_path: &Path) -> PathBuf {
    let p = Path::new(folder);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        project_path.join(p)
    }
}

/// dev.py `_select_folders_interactive`.
pub(super) fn select_folders_interactive(folders: &[String]) -> Vec<String> {
    echo("\nConfigured source folders:");
    for (i, f) in folders.iter().enumerate() {
        echo(&format!("  [{:2}] {}", i + 1, f));
    }
    echo("  [ 0] All folders");

    let raw = prompt("\nSelect (comma-separated numbers, 0 = all)", "0");
    let raw = raw.trim();
    if raw == "0" {
        return folders.to_vec();
    }

    let mut selected = Vec::new();
    for part in raw.split(',') {
        let part = part.trim();
        match part.parse::<usize>() {
            Ok(idx) if idx >= 1 && idx <= folders.len() => {
                selected.push(folders[idx - 1].clone());
            }
            _ => echo(&format!("  [warn] Invalid input '{}', skipping", part)),
        }
    }
    selected
}

// ---------------------------------------------------------------------------
// Graph args
// ---------------------------------------------------------------------------

/// dev.py `_neo4j_args_code` (naming kept for grep-parity with dev.py).
pub fn neo4j_args_code(env: &Value) -> Vec<String> {
    let provider = match graph_provider(env, "CODE_GRAPH_PROVIDER") {
        Ok(p) => p,
        Err(e) => crate::util::error_exit(&e),
    };
    let mut args = vec!["--graph-provider".to_string(), provider.clone()];
    match provider.as_str() {
        "falkordb" => {
            if let Some(uri) = env.get("FALKORDB_URI").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--falkordb-uri".to_string());
                args.push(uri.to_string());
                if let Some(pw) = env.get("FALKORDB_PASSWORD").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                    args.push("--falkordb-password".to_string());
                args.push(pw.to_string());
                }
                if env.get("FALKORDB_SSL").map(truthy).unwrap_or(false) {
                    args.push("--falkordb-ssl".to_string());
                }
            } else if let Some(path) = env.get("FALKORDB_PATH").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--falkordb-path".to_string());
                args.push(path.to_string());
            }
            args.push("--falkordb-graph".to_string());
            args.push(
                env.get("FALKORDB_GRAPH")
                    .and_then(|v| v.as_str())
                    .unwrap_or("hyper_graph")
                    .to_string(),
            );
        }
        "ladybug" => {
            if let Some(path) = env.get("LADYBUG_PATH").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--ladybug-path".to_string());
                args.push(path.to_string());
            }
            args.push("--ladybug-graph".to_string());
            args.push(
                env.get("LADYBUG_GRAPH")
                    .and_then(|v| v.as_str())
                    .unwrap_or("hyper_graph")
                    .to_string(),
            );
        }
        _ => {
            args.push("--neo4j-uri".to_string());
            args.push(
                env.get("NEO4J_URI")
                    .and_then(|v| v.as_str())
                    .unwrap_or("bolt://localhost:7687")
                    .to_string(),
            );
            args.push("--neo4j-user".to_string());
            args.push(
                env.get("NEO4J_USER")
                    .and_then(|v| v.as_str())
                    .unwrap_or("neo4j")
                    .to_string(),
            );
            args.push("--neo4j-password".to_string());
            args.push(
                env.get("NEO4J_PASS")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            );
            if let Some(db) = env.get("NEO4J_DB").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--neo4j-db".to_string());
                args.push(db.to_string());
            }
        }
    }
    args
}

/// dev.py `_env_to_neo4j_args` (doc-side graph args).
pub(super) fn env_to_neo4j_args(env: &Value) -> Vec<String> {
    let provider = match graph_provider(env, "DOC_GRAPH_PROVIDER") {
        Ok(p) => p,
        Err(e) => crate::util::error_exit(&e),
    };
    let mut args = vec!["--graph-provider".to_string(), provider.clone()];
    match provider.as_str() {
        "falkordb" => {
            if let Some(uri) = env.get("FALKORDB_URI").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--falkordb-uri".to_string());
                args.push(uri.to_string());
                if let Some(pw) = env.get("FALKORDB_PASSWORD").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                    args.push("--falkordb-password".to_string());
                args.push(pw.to_string());
                }
                if env.get("FALKORDB_SSL").map(truthy).unwrap_or(false) {
                    args.push("--falkordb-ssl".to_string());
                }
            } else if let Some(path) = env.get("FALKORDB_PATH").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--falkordb-path".to_string());
                args.push(path.to_string());
            }
            args.push("--falkordb-graph".to_string());
            args.push(
                env.get("FALKORDB_GRAPH")
                    .and_then(|v| v.as_str())
                    .unwrap_or("hyper_graph")
                    .to_string(),
            );
        }
        "ladybug" => {
            if let Some(path) = env.get("LADYBUG_PATH").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
                args.push("--ladybug-path".to_string());
                args.push(path.to_string());
            }
            args.push("--ladybug-graph".to_string());
            args.push(
                env.get("LADYBUG_GRAPH")
                    .and_then(|v| v.as_str())
                    .unwrap_or("hyper_graph")
                    .to_string(),
            );
        }
        _ => {
            args.push("--neo4j-uri".to_string());
            args.push(
                env.get("NEO4J_URI")
                    .and_then(|v| v.as_str())
                    .unwrap_or("bolt://localhost:7687")
                    .to_string(),
            );
            args.push("--neo4j-user".to_string());
            args.push(
                env.get("NEO4J_USER")
                    .and_then(|v| v.as_str())
                    .unwrap_or("neo4j")
                    .to_string(),
            );
            args.push("--neo4j-pass".to_string());
            args.push(
                env.get("NEO4J_PASS")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
            );
        }
    }
    args
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::String(s) => matches!(s.as_str(), "1" | "true" | "yes" | "on" | "True"),
        Value::Number(n) => n.as_i64() == Some(1),
        _ => false,
    }
}

// ---------------------------------------------------------------------------
// Retry runner (dev.py `_run_with_retry`)
// ---------------------------------------------------------------------------

pub struct RetryOptions<'a> {
    pub max_retries: u32,
    pub dry_run: bool,
    pub env: Option<&'a Value>,
    pub non_retryable_exit_codes: &'a [i32],
    pub result_path: Option<&'a Path>,
}

pub fn run_with_retry(cmd: &[String], opts: &RetryOptions) -> i32 {
    echo(&format!("  $ {}", cmd.join(" ")));
    if opts.dry_run {
        echo("  [dry-run] skipped");
        return 0;
    }

    let mut process_env: Vec<(String, String)> = std::env::vars().collect();
    if let Some(env) = opts.env {
        if let Some(map) = env.as_object() {
            for (k, v) in map {
                let value = match v {
                    Value::String(s) => s.clone(),
                    Value::Null => continue,
                    other => other.to_string(),
                };
                replace_or_push(&mut process_env, k, value);
            }
        }
        let run_id = uuid_hex();
        replace_or_push(&mut process_env, "CORTEX_RUN_ID", run_id.clone());
        set_default(&mut process_env, "CORTEX_CORRELATION_ID", run_id);
    }

    for attempt in 1..=opts.max_retries {
        if let Some(result_path) = opts.result_path {
            let _ = std::fs::remove_file(result_path);
        }

        // Required-journal lanes: replay pending batches before each attempt
        // (forced-Python consumer — see journalx::recover_required_lane).
        if let Some(rc) = crate::journalx::recover_required_lane(&process_env) {
            echo_err(&format!("  [error] graph journal recovery exited {}", rc));
            return rc;
        }

        let mut command = Command::new(&cmd[0]);
        command.args(&cmd[1..]);
        if !process_env.is_empty() {
            command.envs(process_env.iter().map(|(k, v)| (k.as_str(), v.as_str())));
        }
        let rc = match command.status() {
            Ok(s) => s.code().unwrap_or(1),
            Err(e) => {
                echo_err(&format!("  [error] failed to launch {}: {}", cmd[0], e));
                return 1;
            }
        };
        if rc == 0 {
            return 0;
        }

        let mut typed_result: Option<Value> = None;
        if let Some(result_path) = opts.result_path {
            let text = std::fs::read_to_string(result_path).unwrap_or_default();
            match serde_json::from_str::<Value>(&text) {
                Ok(v) if v.is_object() => {
                    typed_result = Some(v);
                }
                _ => {
                    echo_err(&format!(
                        "  [failure] code=child_result_missing phase=finished summary=child exited {} without a valid result artifact artifact={} action=inspect child logs (JSONDecodeError)",
                        rc,
                        result_path.display()
                    ));
                    return rc;
                }
            }
            let result = typed_result.as_ref().unwrap();
            let expected_run_id = process_env
                .iter()
                .find(|(k, _)| k == "CORTEX_RUN_ID")
                .map(|(_, v)| v.clone())
                .unwrap_or_default();
            let actual_run = result.get("run_id").and_then(|v| v.as_str()).unwrap_or("");
            if !expected_run_id.is_empty() && actual_run != expected_run_id {
                echo_err(&format!(
                    "  [failure] code=child_result_identity_mismatch phase=finished summary=result run {} does not match {} artifact={} action=discard the incompatible artifact and rerun",
                    actual_run,
                    expected_run_id,
                    result_path.display()
                ));
                return rc;
            }
            render_failure(result);
            let outcome = result.get("outcome").and_then(|v| v.as_str()).unwrap_or("");
            let should_retry = outcome == "failed_retryable";
            if outcome == "ambiguous" || !should_retry {
                return rc;
            }
        } else if opts.non_retryable_exit_codes.contains(&rc) {
            return rc;
        }

        if attempt < opts.max_retries {
            let wait: u64 = typed_result
                .as_ref()
                .and_then(|r| r.get("retry_after_seconds").and_then(|v| v.as_f64()))
                .map(|f| f as u64)
                .unwrap_or(2u64.pow(attempt));
            echo(&format!(
                "  [retry {}/{}] exit={}, retrying in {}s…",
                attempt,
                opts.max_retries - 1,
                rc,
                wait
            ));
            std::thread::sleep(std::time::Duration::from_secs(wait));
        }
    }
    unreachable!("retry loop returns earlier")
}

fn render_failure(result: &Value) {
    let outcome = result.get("outcome").and_then(|v| v.as_str()).unwrap_or("");
    let phase = result.get("phase").and_then(|v| v.as_str()).unwrap_or("");
    let run_id = result.get("run_id").and_then(|v| v.as_str()).unwrap_or("");
    match result.get("failure") {
        Some(failure) if failure.is_object() => {
            let code = failure.get("code").and_then(|v| v.as_str()).unwrap_or("");
            let fphase = failure.get("phase").and_then(|v| v.as_str()).unwrap_or(phase);
            let frun = failure.get("run_id").and_then(|v| v.as_str()).unwrap_or(run_id);
            let summary = failure.get("summary").and_then(|v| v.as_str()).unwrap_or("");
            let artifact = failure
                .get("artifacts")
                .or_else(|| failure.get("artifact_references"))
                .and_then(|a| a.as_array())
                .and_then(|a| a.first())
                .and_then(|a| a.get("path"))
                .and_then(|p| p.as_str())
                .unwrap_or("");
            echo_err(&format!(
                "  [failure] code={} phase={} run={} summary={} artifact={} action={}",
                code,
                fphase,
                frun,
                summary,
                artifact,
                failure.get("safe_action").and_then(|v| v.as_str()).unwrap_or("")
            ));
        }
        _ => {
            echo_err(&format!(
                "  [failure] outcome={} phase={} run={}",
                outcome, phase, run_id
            ));
        }
    }
}

fn replace_or_push(env: &mut Vec<(String, String)>, key: &str, value: String) {
    match env.iter_mut().find(|(k, _)| k == key) {
        Some(slot) => slot.1 = value,
        None => env.push((key.to_string(), value)),
    }
}

fn set_default(env: &mut Vec<(String, String)>, key: &str, value: String) {
    if !env.iter().any(|(k, _)| k == key) {
        env.push((key.to_string(), value));
    }
}

/// Random hex uuid4-style identifier (32 hex chars, like `uuid4().hex`).
pub fn uuid_hex() -> String {
    let mut buf = [0u8; 16];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = f.read_exact(&mut buf);
    } else {
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        buf[..16].copy_from_slice(&seed.to_le_bytes());
    }
    buf[6] = (buf[6] & 0x0f) | 0x40;
    buf[8] = (buf[8] & 0x3f) | 0x80;
    buf.iter().map(|b| format!("{:02x}", b)).collect()
}

// ---------------------------------------------------------------------------
// Code sync summary path
// ---------------------------------------------------------------------------

fn code_sync_summary_path(folder_path: &Path, project_id: &str, cache_dir: Option<&str>) -> PathBuf {
    let realpath = super::init::resolve_path(folder_path);
    let identity = format!("{}\0{}", project_id, realpath.to_string_lossy());
    let token = sha256_hex_hex(identity.as_bytes());
    let cache_root = match cache_dir.filter(|s| !s.trim().is_empty()) {
        Some(dir) => {
            let expanded = if let Some(rest) = dir.strip_prefix("~/") {
                std::env::var("HOME").map(PathBuf::from).map(|h| h.join(rest)).unwrap_or(PathBuf::from(dir))
            } else {
                PathBuf::from(dir)
            };
            super::init::resolve_path(&expanded)
        }
        None => folder_path.join(".cache"),
    };
    let invocation = format!("{}_{}", std::process::id(), uuid_hex());
    cache_root
        .join("incremental_sync_summaries")
        .join(format!("dev_{}_{}.json", token, invocation))
}

/// Locate the `cortex-sync` binary (phase-04 wire): env override → repo
/// release target → debug target. Missing binary is a hard error with the
/// build hint (no Python fallback — `cortex-sync` IS the orchestrator now).
fn cortex_sync_binary() -> PathBuf {
    if let Ok(explicit) = std::env::var("CORTEX_SYNC_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return path;
        }
    }
    let root = crate::util::repo_root();
    for candidate in [
        root.join("rust").join("target").join("release").join("cortex-sync"),
        root.join("rust").join("target").join("debug").join("cortex-sync"),
    ] {
        if candidate.is_file() {
            return candidate;
        }
    }
    crate::util::fail(
        "cortex-sync binary not found (looked in $CORTEX_SYNC_BIN, rust/target/{release,debug}). \
         Build it with: cargo build --release -p cortex-sync",
    );
}

// ---------------------------------------------------------------------------
// Sync lifecycle (lock + MCP pause) — Python-layer backed
// ---------------------------------------------------------------------------

const MCP_SERVICES: &[(&str, &str, &str)] = &[
    ("code", "code-tiny", "unified_mcp.py"),
    ("doc", "doc-tiny", "mcp_graph_rag.py"),
];

fn storage_instance_of(process_env: &Value) -> String {
    process_env
        .get("CORTEX_STORAGE_INSTANCE")
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            std::env::var("CORTEX_STORAGE_INSTANCE")
                .ok()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
        })
        .unwrap_or_else(|| "default".to_string())
}

/// Guard replicating `_sync_lifecycle`: exclusive run lock + embedded-store
/// MCP pause. Returns the guard handle; drop it to restore the MCP.
pub struct SyncLifecycle {
    owner: String,
    lock: Option<std::fs::File>,
    restart_mcp: bool,
    service_name: String,
    project_path: PathBuf,
}

impl Drop for SyncLifecycle {
    fn drop(&mut self) {
        if self.restart_mcp {
            let extra_env = crate::env::mcp_env_from_config(&self.project_path, &self.service_name);
            let result = crate::cmds::mcp::mcp_start_one(&self.service_name, &extra_env);
            match result.status.as_str() {
                "started" => {
                    echo(&format!(
                        "[sync] {} MCP restarted (pid={})",
                        capitalize(&self.owner),
                        result.pid.map(|p| p.to_string()).unwrap_or_else(|| "?".to_string())
                    ));
                }
                _ => {
                    let reason = if result.reason.is_empty() { "unknown error" } else { &result.reason };
                    echo_err(&format!(
                        "[sync] WARNING: {} MCP was paused but could not be restarted: {}",
                        self.owner, reason
                    ));
                }
            }
        }
        // Releasing the lock closes the fd; the pid line stays, as in dev.py.
        drop(self.lock.take());
    }
}

/// Guard replicating `_sync_lifecycle`: exclusive run lock + embedded-store
/// MCP pause. Returns the guard handle; drop it to restore the MCP.
pub fn sync_lifecycle(
    owner: &str,
    process_env: &Value,
    project_path: &Path,
    enabled: bool,
    take_lock: bool,
) -> SyncLifecycle {
    let mut guard = SyncLifecycle {
        owner: owner.to_string(),
        lock: None,
        restart_mcp: false,
        service_name: String::new(),
        project_path: project_path.to_path_buf(),
    };
    if !enabled {
        return guard;
    }

    // ── pause the embedded-store MCP (dev.py `_pause_mcp_for_sync`) ────
    let scoped_key = if owner == "code" {
        "CODE_GRAPH_PROVIDER"
    } else {
        "DOC_GRAPH_PROVIDER"
    };
    let provider = graph_provider(process_env, scoped_key).unwrap_or_default();
    let falkordb_path = process_env
        .get("FALKORDB_PATH")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if provider == "falkordb" && !falkordb_path.is_empty() {
        let service = MCP_SERVICES
            .iter()
            .find(|(o, _, _)| *o == owner)
            .expect("known owner");
        let db_path = PathBuf::from(&falkordb_path);
        let instance_id = storage_instance_of(process_env);
        let was_running = !mcp_pids(service.2, Some(&instance_id)).is_empty();
        let orphan_running = !embedded_falkordb_pids(&db_path).is_empty();

        if was_running || orphan_running {
            if was_running {
                echo(&format!(
                    "[sync] Pausing {} MCP (instance={}) to acquire the embedded FalkorDB lease",
                    owner, instance_id
                ));
                crate::procinfo::mcp_stop_pattern(service.2, Some(&instance_id));
            } else {
                echo("[sync] Stopping orphaned embedded FalkorDB before sync");
            }
            let stopped = crate::procinfo::stop_embedded_falkordb(&db_path, 5.0);
            if !stopped.is_empty() {
                echo(&format!(
                    "[sync] stopped {} embedded FalkorDB process(es)",
                    stopped.len()
                ));
            }
            if !mcp_pids(service.2, Some(&instance_id)).is_empty() {
                fail(&format!(
                    "Could not stop the {} MCP process; sync was not started. Stop the MCP server manually and retry.",
                    owner
                ));
            }
            if !embedded_falkordb_pids(&db_path).is_empty() {
                fail("Could not stop the embedded FalkorDB process; sync was not started. Stop the local FalkorDB process manually and retry.");
            }
            guard.restart_mcp = true;
            guard.service_name = service.1.to_string();
        }
    }

    // ── exclusive run lock (dev.py `_sync_process_scope`) ───────────────
    if !take_lock {
        return guard;
    }
    let lock_dir = project_path.join(".cortext-harness").join("sync-state");
    let _ = std::fs::create_dir_all(&lock_dir);
    let lock_path = lock_dir.join(format!("sync-{}.lock", owner));
    let handle = std::fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(&lock_path);
    match handle {
        Ok(file) => {
            match file.try_lock() {
                Ok(()) => {
                    {
                        let mut f = &file;
                        let _ = f.write_all(std::process::id().to_string().as_bytes());
                        let _ = f.flush();
                    }
                    guard.lock = Some(file);
                    stop_sync_workers(owner, process_env, "cleared previous run", false);
                }
                Err(_) => {
                    let holder = std::fs::read_to_string(&lock_path)
                        .map(|s| s.trim().to_string())
                        .ok()
                        .filter(|s| !s.is_empty())
                        .unwrap_or_else(|| "unknown".to_string());
                    fail(&format!(
                        "Another 'dev sync {owner}' run is active (pid={holder}). Wait for it to finish, or run 'dev sync {owner} stop' to clear a stuck run."
                    ));
                }
            }
        }
        Err(e) => {
            echo_err(&format!("[error] cannot open sync lock: {}", e));
            std::process::exit(1);
        }
    }
    guard
}

pub fn mcp_pids(pattern: &str, instance_id: Option<&str>) -> Vec<i64> {
    crate::cmds::mcp::mcp_pids(pattern, instance_id)
}

pub fn embedded_falkordb_pids(db_path: &Path) -> Vec<i64> {
    crate::procinfo::embedded_falkordb_pids(db_path, None)
}

/// dev.py `_stop_sync_workers`.
/// dev.py `_stop_sync_workers` — native `stop_sync_processes` + embedded
/// store sweep (the `stop_sync_workers` bridge-op assembly, now in-process).
fn stop_sync_workers(owner: &str, process_env: &Value, prefix: &str, include_launchers: bool) {
    let report = crate::procinfo::stop_sync_processes(
        owner,
        &crate::util::repo_root(),
        &[],
        5.0,
        include_launchers,
    );
    if !report.matched.is_empty() {
        echo(&format!(
            "[sync] {}: owner={} matched={} terminated={} forced={}",
            prefix,
            owner,
            report.matched.len(),
            report.terminated.len(),
            report.forced.len(),
        ));
    }
    if !report.remaining.is_empty() {
        let remaining: Vec<String> = report.remaining.iter().map(|p| p.to_string()).collect();
        fail(&format!(
            "Could not stop {} sync process(es): {}",
            owner,
            remaining.join(", ")
        ));
    }
    let falkordb_path = process_env
        .get("FALKORDB_PATH")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if falkordb_path.is_empty() {
        return;
    }
    let db_path = PathBuf::from(&falkordb_path);
    let embedded_stopped = crate::procinfo::stop_embedded_falkordb(&db_path, 5.0);
    if !embedded_stopped.is_empty() {
        echo(&format!(
            "[sync] {}: stopped {} embedded FalkorDB process(es)",
            prefix,
            embedded_stopped.len()
        ));
    }
    let embedded_remaining = crate::procinfo::embedded_falkordb_pids(&db_path, None);
    if !embedded_remaining.is_empty() {
        let remaining: Vec<String> = embedded_remaining.iter().map(|p| p.to_string()).collect();
        fail(&format!(
            "Could not stop embedded FalkorDB process(es): {}",
            remaining.join(", ")
        ));
    }
}

pub(super) fn stop_sync_command(owner: &str, project_path: &Path, process_env: &Value) {
    // _stop_sync_command wraps the worker stop in the MCP pause cycle; the
    // pause guard restores the MCP on drop.
    let lifecycle = sync_lifecycle(owner, process_env, project_path, true, false);
    stop_sync_workers(owner, process_env, "explicit stop", true);
    drop(lifecycle);
    echo(&format!("[sync] {} sync stop complete", owner));
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) => c.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

// ---------------------------------------------------------------------------
// sync code
// ---------------------------------------------------------------------------

pub fn sync_code(m: &Matches) {
    let project_dir = m.value_or("--project-dir", ".");
    let _preview = m.flag("--preview");
    let verbose = m.flag("--verbose"); // defaults on via FlagPair
    let dry_run = m.flag("--dry-run");
    let full_scan = m.flag("--full-scan");
    let sync_mode = m.value_or("--sync-mode", "both");
    let change_detection = m.value_or("--change-detection", "hybrid");
    let parsers = m.value_or("--parsers", "auto");
    let lock_timeout_seconds = m.value_or("--lock-timeout-seconds", "10.0");
    let reconcile = m.flag("--reconcile");
    let submodules = m.value_or("--submodules", "recursive");
    let parse_quality = m.value_or("--parse-quality", "report");
    let parse_quality_max_files = m.value_or("--parse-quality-max-files", "500");
    let parse_quality_wall_seconds = m.value_or("--parse-quality-wall-seconds", "900");
    let workers_default = default_workers().to_string();
    let parse_quality_workers = m.value_or("--parse-quality-workers", &workers_default);

    if sync_mode != "both" && !full_scan {
        echo_err("Error: --sync-mode graph/embedding requires --full-scan");
        std::process::exit(2);
    }

    let project_path = super::init::resolve_path(Path::new(&project_dir));
    let (cfg, _) = load_active_config(&project_path);
    let code_cfg = cfg.get("code").cloned().unwrap_or(json!({}));
    let env = code_cfg.get("env").cloned().unwrap_or(json!({}));
    let process_env = crate::env::code_env(&project_path);
    let process_env = with_extra_ignores(&cfg, &process_env);
    let project = cfg.get("project").cloned().unwrap_or(json!({}));
    let folders = crate::config::source_folders(code_cfg.get("source").unwrap_or(&json!({})));

    if folders.is_empty() {
        echo("[warn] No source folders configured. Run 'dev init' or 'dev sync code add'.");
        return;
    }

    let selected = dedupe_scan_roots(&select_folders_interactive(&folders), &project_path);
    validate_selected_scan_roots(&selected, &folders, &project_path);
    warn_scan_roots_matching_ignores(&selected, &ignore_folders(&cfg));
    if selected.is_empty() {
        echo("[info] No folders selected.");
        return;
    }

    let mut summaries: Vec<Value> = Vec::new();
    let total_start = std::time::Instant::now();

    let guard = sync_lifecycle("code", &process_env, &project_path, !dry_run, true);
    for folder in &selected {
        let folder_path = absolute_or_join(folder, &project_path);
        if !folder_path.exists() {
            echo(&format!("\n[warn] Folder not found: {} — skipping", folder));
            continue;
        }
        let project_id = project
            .get("code")
            .map(|v| v.to_string().trim_matches('"').to_string())
            .or_else(|| project.get("name").map(|v| v.to_string().trim_matches('"').to_string()))
            .unwrap_or_else(|| "project".to_string());
        let child_summary_path = code_sync_summary_path(
            &folder_path,
            &project_id,
            process_env.get("QDRANT_CACHE_DIR").and_then(|v| v.as_str()),
        );
        let mut cmd = vec![
            cortex_sync_binary().to_string_lossy().to_string(),
            "--python-bin".to_string(),
            crate::env::sync_python_bin(),
            "--root".to_string(),
            folder_path.to_string_lossy().to_string(),
            "--project-id".to_string(),
            project_id.clone(),
            "--project-name".to_string(),
            project.get("name").and_then(|v| v.as_str()).unwrap_or("project").to_string(),
            "--embed-model".to_string(),
            env.get("EMBEDDING_MODEL")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or("jinaai/jina-embeddings-v3")
                .to_string(),
            "--sync-mode".to_string(),
            sync_mode.clone(),
            "--change-detection".to_string(),
            change_detection.clone(),
            "--parsers".to_string(),
            parsers.clone(),
            "--lock-timeout-seconds".to_string(),
            lock_timeout_seconds.clone(),
            "--submodules".to_string(),
            submodules.clone(),
            "--summary-path".to_string(),
            child_summary_path.to_string_lossy().to_string(),
            "--parse-quality".to_string(),
            parse_quality.clone(),
            "--parse-quality-max-files".to_string(),
            parse_quality_max_files.clone(),
            "--parse-quality-wall-seconds".to_string(),
            parse_quality_wall_seconds.clone(),
            "--parse-quality-workers".to_string(),
            parse_quality_workers.clone(),
        ];
        cmd.extend(neo4j_args_code(&process_env));
        if full_scan {
            cmd.push("--full-scan".to_string());
        }
        if reconcile {
            cmd.push("--reconcile".to_string());
        }
        if dry_run {
            cmd.push("--verbose".to_string());
            echo(&format!("\n[dry-run] {}", cmd.join(" ")));
            summaries.push(json!({"folder": folder, "status": "dry_run"}));
            continue;
        }
        if verbose {
            cmd.push("--verbose".to_string());
        }

        let start = std::time::Instant::now();
        let opts = RetryOptions {
            max_retries: 3,
            dry_run: false,
            env: Some(&process_env),
            non_retryable_exit_codes: &[2],
            result_path: Some(child_summary_path.as_path()),
        };
        let rc = run_with_retry(&cmd, &opts);
        let elapsed = start.elapsed().as_secs_f64();
        let child_summary = read_json_file(&child_summary_path);
        summaries.push(json!({
            "folder": folder,
            "status": if rc == 0 { "ok" } else { "error" },
            "elapsed": elapsed,
            "exit_code": rc,
            "outcome": child_summary.get("outcome").cloned().unwrap_or(json!("unknown")),
            "change_sources": child_summary.get("change_sources").cloned().unwrap_or(json!({})),
            "coverage_warnings": child_summary.get("coverage_warnings").cloned().unwrap_or(json!([])),
            "parse_quality": child_summary.get("parse_quality").cloned().unwrap_or(json!({})),
            "journal": child_summary.get("journal").cloned().unwrap_or(json!({})),
        }));
    }
    drop(guard);
    print_summary(&summaries, total_start.elapsed().as_secs_f64());
}

pub fn sync_code_all(m: &Matches) {
    let project_dir = m.value_or("--project-dir", ".");
    let project_path = super::init::resolve_path(Path::new(&project_dir));
    let (cfg, _) = load_active_config(&project_path);
    let code_cfg = cfg.get("code").cloned().unwrap_or(json!({}));
    let env = code_cfg.get("env").cloned().unwrap_or(json!({}));
    let process_env = crate::env::code_env(&project_path);
    let process_env = with_extra_ignores(&cfg, &process_env);
    let project = cfg.get("project").cloned().unwrap_or(json!({}));
    let folders = dedupe_scan_roots(
        &crate::config::source_folders(code_cfg.get("source").unwrap_or(&json!({}))),
        &project_path,
    );

    if folders.is_empty() {
        echo("[warn] No source folders configured. Run 'dev init' or 'dev sync code add'.");
        return;
    }

    let extra_ignores = ignore_folders(&cfg);
    warn_scan_roots_matching_ignores(&folders, &extra_ignores);

    echo(&format!(
        "\n[sync-code all]  folders={}  analyzers={}",
        folders.len(),
        SYNC_CODE_ALL_LANGS.len()
    ));
    echo(&format!("  tools: {}", SYNC_CODE_ALL_LANGS.join(", ")));

    let dry_run = m.flag("--dry-run");
    let verbose = m.flag("--verbose");
    let full_scan = m.flag("--full-scan");
    let reconcile = m.flag("--reconcile");
    let sync_mode = m.value_or("--sync-mode", "both");
    let change_detection = m.value_or("--change-detection", "hybrid");
    let lock_timeout_seconds = m.value_or("--lock-timeout-seconds", "10.0");
    let submodules = m.value_or("--submodules", "recursive");
    let parse_quality = m.value_or("--parse-quality", "report");
    let parse_quality_max_files = m.value_or("--parse-quality-max-files", "500");
    let parse_quality_wall_seconds = m.value_or("--parse-quality-wall-seconds", "900");
    let workers_default = default_workers().to_string();
    let parse_quality_workers = m.value_or("--parse-quality-workers", &workers_default);

    let mut summaries: Vec<Value> = Vec::new();
    let total_start = std::time::Instant::now();

    let guard = sync_lifecycle("code", &process_env, &project_path, !dry_run, true);
    for folder in &folders {
        let folder_path = absolute_or_join(folder, &project_path);
        if !folder_path.exists() {
            echo(&format!("[warn] Folder not found: {} — skipping", folder));
            continue;
        }
        let project_id = project
            .get("code")
            .map(|v| v.to_string().trim_matches('"').to_string())
            .or_else(|| project.get("name").map(|v| v.to_string().trim_matches('"').to_string()))
            .unwrap_or_else(|| "project".to_string());
        let child_summary_path = code_sync_summary_path(
            &folder_path,
            &project_id,
            process_env.get("QDRANT_CACHE_DIR").and_then(|v| v.as_str()),
        );
        let mut cmd = vec![
            cortex_sync_binary().to_string_lossy().to_string(),
            "--python-bin".to_string(),
            crate::env::sync_python_bin(),
            "--root".to_string(),
            folder_path.to_string_lossy().to_string(),
            "--project-id".to_string(),
            project_id.clone(),
            "--project-name".to_string(),
            project.get("name").and_then(|v| v.as_str()).unwrap_or("project").to_string(),
            "--embed-model".to_string(),
            env.get("EMBEDDING_MODEL")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .unwrap_or("jinaai/jina-embeddings-v3")
                .to_string(),
            "--sync-mode".to_string(),
            sync_mode.clone(),
            "--change-detection".to_string(),
            change_detection.clone(),
            "--parsers".to_string(),
            "auto".to_string(),
            "--lock-timeout-seconds".to_string(),
            lock_timeout_seconds.clone(),
            "--submodules".to_string(),
            submodules.clone(),
            "--summary-path".to_string(),
            child_summary_path.to_string_lossy().to_string(),
            "--parse-quality".to_string(),
            parse_quality.clone(),
            "--parse-quality-max-files".to_string(),
            parse_quality_max_files.clone(),
            "--parse-quality-wall-seconds".to_string(),
            parse_quality_wall_seconds.clone(),
            "--parse-quality-workers".to_string(),
            parse_quality_workers.clone(),
        ];
        cmd.extend(neo4j_args_code(&process_env));
        if full_scan {
            cmd.push("--full-scan".to_string());
        }
        if reconcile {
            cmd.push("--reconcile".to_string());
        }
        if dry_run {
            cmd.push("--verbose".to_string());
            echo(&format!("[dry-run] {}", cmd.join(" ")));
            summaries.push(json!({"folder": folder, "status": "dry_run"}));
            continue;
        }
        if verbose {
            cmd.push("--verbose".to_string());
        }

        let start = std::time::Instant::now();
        let opts = RetryOptions {
            max_retries: 3,
            dry_run: false,
            env: Some(&process_env),
            non_retryable_exit_codes: &[2],
            result_path: Some(child_summary_path.as_path()),
        };
        let rc = run_with_retry(&cmd, &opts);
        let elapsed = start.elapsed().as_secs_f64();
        let child_summary = read_json_file(&child_summary_path);
        summaries.push(json!({
            "folder": folder,
            "status": if rc == 0 { "ok" } else { "error" },
            "elapsed": elapsed,
            "exit_code": rc,
            "outcome": child_summary.get("outcome").cloned().unwrap_or(json!("unknown")),
            "change_sources": child_summary.get("change_sources").cloned().unwrap_or(json!({})),
            "coverage_warnings": child_summary.get("coverage_warnings").cloned().unwrap_or(json!([])),
            "parse_quality": child_summary.get("parse_quality").cloned().unwrap_or(json!({})),
            "journal": child_summary.get("journal").cloned().unwrap_or(json!({})),
        }));
    }
    drop(guard);
    print_summary(&summaries, total_start.elapsed().as_secs_f64());
}

pub fn sync_code_stop(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let process_env = crate::env::code_env(&project_path);
    stop_sync_command("code", &project_path, &process_env);
}

// ---------------------------------------------------------------------------
// sync doc
// ---------------------------------------------------------------------------



// ---------------------------------------------------------------------------
// Summary printer (dev.py `_print_summary`)
// ---------------------------------------------------------------------------

pub(super) fn print_summary(summaries: &[Value], total_elapsed: f64) {
    let eq = "═".repeat(52);
    let dash = "─".repeat(52);
    echo(&format!("\n{}", eq));
    echo(&format!(
        "  Sync Summary  {}",
        local_strftime(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs() as i64,
            "%Y-%m-%d %H:%M:%S"
        )
    ));
    echo(&dash);
    for s in summaries {
        let status = s.get("status").and_then(|v| v.as_str()).unwrap_or("?");
        let folder = s.get("folder").and_then(|v| v.as_str()).unwrap_or("?");
        let mode = s.get("mode").and_then(|v| v.as_str()).unwrap_or("");
        let outcome = s.get("outcome").and_then(|v| v.as_str()).unwrap_or("");
        let elapsed = s.get("elapsed").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let reason = s.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        let icon = match status {
            "ok" => "✓",
            "skipped" => "↷",
            "cancelled" => "–",
            "error" => "✗",
            _ => "?",
        };
        let mut parts = vec![format!("  {} {}", icon, folder)];
        if !mode.is_empty() {
            parts.push(format!("[{}]", mode));
        }
        if !outcome.is_empty() && outcome != "unknown" {
            parts.push(format!("[{}]", outcome));
        }
        if elapsed != 0.0 {
            parts.push(format!("{:.1}s", elapsed));
        }
        if !reason.is_empty() {
            parts.push(format!("({})", reason));
        }
        echo(&parts.join("  "));
        let quality = s.get("parse_quality");
        if let Some(artifact) = quality
            .as_ref()
            .and_then(|q| q.get("artifact"))
            .and_then(|a| a.as_str())
            .filter(|a| !a.is_empty())
        {
            let aggregates = quality
                .as_ref()
                .and_then(|q| q.get("aggregates"))
                .cloned()
                .unwrap_or(json!({}));
            echo(&format!(
                "    parse quality: files={} error_nodes={} missing_nodes={} artifact={}",
                aggregates.get("file_count").and_then(|v| v.as_i64()).unwrap_or(0),
                aggregates.get("error_node_total").and_then(|v| v.as_i64()).unwrap_or(0),
                aggregates.get("missing_node_total").and_then(|v| v.as_i64()).unwrap_or(0),
                artifact
            ));
        }
        let journal = s.get("journal");
        let run_count = journal
            .as_ref()
            .and_then(|j| j.get("run_count"))
            .and_then(|v| v.as_i64())
            .unwrap_or(0);
        if run_count != 0 {
            let j = journal.cloned().unwrap_or(json!({}));
            let resumed = match j.get("resumed") {
                Some(Value::Bool(b)) => b.to_string(),
                _ => "false".to_string(),
            };
            echo(&format!(
                "    journal: runs={} resumed={} produced={} acked={} pending={} \
                 retrying={} reconciling={} blocked={} bytes={} next={}",
                j.get("run_count").and_then(|v| v.as_i64()).unwrap_or(0),
                resumed,
                j.get("produced").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("acked").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("pending").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("retrying").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("reconciling").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("blocked").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("artifact_bytes").and_then(|v| v.as_i64()).unwrap_or(0),
                j.get("next_action").and_then(|v| v.as_str()).unwrap_or("none"),
            ));
        }
    }
    let ok = summaries.iter().filter(|s| s.get("status").and_then(|v| v.as_str()) == Some("ok")).count();
    let skipped = summaries
        .iter()
        .filter(|s| {
            let st = s.get("status").and_then(|v| v.as_str()).unwrap_or("");
            st == "skipped" || st == "cancelled"
        })
        .count();
    let errors = summaries
        .iter()
        .filter(|s| s.get("status").and_then(|v| v.as_str()) == Some("error"))
        .count();
    echo(&dash);
    echo(&format!(
        "  {} ok  {} skipped  {} errors  total {:.1}s",
        ok,
        skipped,
        errors,
        total_elapsed
    ));
    echo(&eq);
}

// ---------------------------------------------------------------------------
// sync add (code + doc)
// ---------------------------------------------------------------------------

pub fn sync_code_add(m: &Matches) {
    sync_add(m, "code");
}

pub fn sync_doc_add(m: &Matches) {
    sync_add(m, "doc");
}

fn sync_add(m: &Matches, section: &str) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let (mut cfg, cfg_path) = load_active_config(&project_path);

    let source = cfg
        .get(section)
        .and_then(|s| s.get("source"))
        .cloned()
        .unwrap_or(json!({}));
    let mut existing_projects = crate::config::source_projects(&source);

    let label = if section == "code" { "code" } else { "doc" };
    let dashes = "─".repeat(3);
    echo(&format!(
        "\n─── Add {} project  (current: {}) {}",
        label,
        existing_projects.len(),
        dashes
    ));
    for (i, p) in existing_projects.iter().enumerate() {
        let git = p.get("git").and_then(|g| g.as_str()).unwrap_or("");
        let folders: Vec<String> = p
            .get("folder")
            .and_then(|f| f.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default();
        echo(&format!(
            "  [{}] {}  folders={:?}",
            i + 1,
            if git.is_empty() { "(local)" } else { git },
            folders
        ));
    }
    echo("");

    let mut git_url = m.value("--git").map(String::from);
    if git_url.is_none() {
        git_url = Some(prompt("  Git URL (blank = local)", ""));
    }
    let mut folders: Vec<String> = m.positionals().to_vec();
    if folders.is_empty() {
        let label_prompt = if section == "doc" {
            "  Doc folders (comma-separated)"
        } else {
            "  Source folders (comma-separated)"
        };
        let raw = prompt(label_prompt, "");
        folders = raw.split(',').map(|f| f.trim().to_string()).filter(|f| !f.is_empty()).collect();
    }

    if folders.is_empty() {
        echo_err("[error] At least one folder is required.");
        std::process::exit(1);
    }

    let new_project = json!({"git": git_url.unwrap_or_default(), "folder": folders});
    existing_projects.push(new_project);

    let cfg_obj = cfg.as_object_mut().expect("config is an object");
    let section_obj = cfg_obj
        .entry(section.to_string())
        .or_insert_with(|| json!({}));
    let source_obj = section_obj
        .as_object_mut()
        .expect("section object")
        .entry("source".to_string())
        .or_insert_with(|| json!({}));
    {
        let src = source_obj.as_object_mut().expect("source object");
        src.insert("projects".to_string(), Value::Array(existing_projects.clone()));
        src.remove("git");
        src.remove("folder");
    }

    save_config(&cfg, &cfg_path);
    let git_label = existing_projects
        .last()
        .and_then(|p| p.get("git"))
        .and_then(|g| g.as_str())
        .unwrap_or("");
    let total_folders = crate::config::source_folders(&json!({"projects": existing_projects})).len();
    echo(&format!(
        "\n[ok] Added project #{}: {}  {:?}",
        existing_projects.len(),
        if git_label.is_empty() { "(local)" } else { git_label },
        existing_projects
            .last()
            .and_then(|p| p.get("folder"))
            .and_then(|f| f.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect::<Vec<_>>())
            .unwrap_or_default()
    ));
    echo(&format!(
        "     Total {} projects: {}  ({} folders)",
        label,
        existing_projects.len(),
        total_folders
    ));
}

// ---------------------------------------------------------------------------

fn read_json_file(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}))
}

/// dev.py `_sync_extra_ignores` — export CORTEX_EXTRA_IGNORE_DIRS to children.
pub(super) fn with_extra_ignores(cfg: &Value, process_env: &Value) -> Value {
    let mut env = process_env.clone();
    let extra = ignore_folders(cfg);
    if !extra.is_empty() {
        let mut sorted = extra.clone();
        sorted.sort();
        if let Some(obj) = env.as_object_mut() {
            obj.insert("CORTEX_EXTRA_IGNORE_DIRS".to_string(), json!(sorted.join(",")));
        }
    }
    env
}

/// click `max(1, min(4, (os.cpu_count() or 2) // 2))`.
pub fn default_workers() -> i64 {
    let cpus = std::thread::available_parallelism()
        .map(|n| n.get() as i64)
        .unwrap_or(2);
    (cpus / 2).clamp(1, 4)
}

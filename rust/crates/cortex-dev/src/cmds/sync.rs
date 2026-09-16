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
            "collections": collections_from_child_summary(&child_summary),
            "journal_paths": journal_paths_from_child_summary(&child_summary),
            "state_path": first_string(&child_summary, &["scope", "cache_dir"]),
        }));
    }
    drop(guard);
    let dest = lane_destinations(&process_env, "code", &sync_mode);
    print_summary(&summaries, total_start.elapsed().as_secs_f64(), &dest);
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
            "collections": collections_from_child_summary(&child_summary),
            "journal_paths": journal_paths_from_child_summary(&child_summary),
            "state_path": first_string(&child_summary, &["scope", "cache_dir"]),
        }));
    }
    drop(guard);
    let dest = lane_destinations(&process_env, "code", &sync_mode);
    print_summary(&summaries, total_start.elapsed().as_secs_f64(), &dest);
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
// Storage destinations
// ---------------------------------------------------------------------------

/// Where one lane writes its data. Resolved from the per-process environment
/// that was actually handed to the children, so it reports the effective
/// target rather than what the config file happens to say.
#[derive(Debug, Clone, PartialEq)]
pub struct Destination {
    pub backend: String,
    /// `local file` / `local dir` (embedded store on disk), `local server`
    /// (a server on this host) or `remote`.
    pub scope: String,
    pub location: String,
    pub namespace: String,
    pub tls: bool,
    /// A credential was supplied for this endpoint. The value is never shown.
    pub auth: bool,
}

/// The write targets of one sync run, plus the lanes a `--sync-mode` subset
/// deliberately left alone.
#[derive(Debug, Clone, Default)]
pub struct LaneDestinations {
    pub instance: String,
    pub graph: Option<Destination>,
    pub vectors: Option<Destination>,
    pub graph_skipped: bool,
    pub vectors_skipped: bool,
}

/// Engine name as users see it in config and docs.
pub fn backend_label(backend: &str) -> String {
    match backend {
        "falkordb" => "FalkorDB".to_string(),
        other => {
            let mut chars = other.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        }
    }
}

fn env_text(env: &Value, key: &str) -> String {
    match env.get(key) {
        Some(Value::String(s)) => s.trim().to_string(),
        None | Some(Value::Null) => String::new(),
        Some(other) => other.to_string().trim_matches('"').trim().to_string(),
    }
}

fn first_text(env: &Value, keys: &[&str]) -> String {
    keys.iter()
        .map(|k| env_text(env, k))
        .find(|v| !v.is_empty())
        .unwrap_or_default()
}

/// `local server` for a loopback endpoint, `remote` otherwise.
fn endpoint_scope(url: &str) -> String {
    if crate::config::is_local(url) {
        "local server".to_string()
    } else {
        "remote".to_string()
    }
}

/// The graph write target for one lane (`code` | `doc`).
pub fn graph_destination(env: &Value, lane: &str) -> Option<Destination> {
    let scoped = if lane == "code" {
        "CODE_GRAPH_PROVIDER"
    } else {
        "DOC_GRAPH_PROVIDER"
    };
    let provider = graph_provider(env, scoped).ok()?;
    let (location, scope, tls) = match provider.as_str() {
        "falkordb" => {
            let uri = env_text(env, "FALKORDB_URI");
            if uri.is_empty() {
                let path = first_text(env, &["FALKORDB_PATH"]);
                if path.is_empty() {
                    return None;
                }
                (path, "local file".to_string(), false)
            } else {
                let tls = uri.starts_with("rediss://") || env.get("FALKORDB_SSL").map(truthy).unwrap_or(false);
                (uri.clone(), endpoint_scope(&uri), tls)
            }
        }
        "ladybug" => {
            let path = env_text(env, "LADYBUG_PATH");
            if path.is_empty() {
                return None;
            }
            (path, "local file".to_string(), false)
        }
        _ => {
            let uri = env_text(env, "NEO4J_URI");
            let uri = if uri.is_empty() {
                "bolt://localhost:7687".to_string()
            } else {
                uri
            };
            let tls = uri.starts_with("bolt+ssc") || uri.starts_with("neo4js://");
            let scope = endpoint_scope(&uri);
            (uri, scope, tls)
        }
    };
    let namespace = match provider.as_str() {
        "ladybug" => first_text(env, &["LADYBUG_GRAPH", "FALKORDB_GRAPH"]),
        "falkordb" => first_text(env, &["FALKORDB_GRAPH", "LADYBUG_GRAPH"]),
        _ => first_text(env, &["NEO4J_DB"]),
    };
    // The graph-name providers fall back to the engine default the children
    // are given (`neo4j_args_code`), Neo4j addresses databases by name only.
    let namespace = if namespace.is_empty() && provider != "neo4j" {
        "hyper_graph".to_string()
    } else {
        namespace
    };
    let auth = !first_text(env, &["NEO4J_PASS", "FALKORDB_PASSWORD"]).is_empty();
    Some(Destination {
        backend: provider,
        scope,
        location,
        namespace,
        tls,
        auth,
    })
}

/// The vector write target for one lane (`code` | `doc`). A local embedded
/// Qdrant store is a directory; a server backend is an endpoint. The lane path
/// key is what the children actually receive (`cortex-sync` reads
/// `QDRANT_CODE_PATH`, the doc ingestor `--qdrant-path`), and the storage
/// overlay rewrites it to the remote URL, so it is preferred over `QDRANT_URL`.
pub fn vector_destination(env: &Value, lane: &str) -> Option<Destination> {
    let path_key = if lane == "code" {
        "QDRANT_CODE_PATH"
    } else {
        "QDRANT_DOC_PATH"
    };
    let location = first_text(env, &[path_key, "QDRANT_URL"]);
    if location.is_empty() {
        return None;
    }
    let (scope, tls) = if location.contains("://") {
        (
            endpoint_scope(&location),
            location.starts_with("https://"),
        )
    } else {
        ("local dir".to_string(), false)
    };
    let namespace = if lane == "code" {
        first_text(env, &["QDRANT_COLLECTION_CODE", "QDRANT_COLLECTION"])
    } else {
        first_text(env, &["QDRANT_COLLECTION_DOC", "QDRANT_COLLECTION"])
    };
    let auth = !env_text(env, "QDRANT_API_KEY").is_empty();
    Some(Destination {
        backend: "qdrant".to_string(),
        scope,
        location,
        namespace,
        tls,
        auth,
    })
}

/// Both targets for a lane, with the halves `--sync-mode` excluded marked as
/// skipped rather than hidden.
pub fn lane_destinations(env: &Value, lane: &str, sync_mode: &str) -> LaneDestinations {
    LaneDestinations {
        instance: env_text(env, "CORTEX_STORAGE_INSTANCE"),
        graph: graph_destination(env, lane),
        vectors: vector_destination(env, lane),
        graph_skipped: sync_mode == "embedding",
        vectors_skipped: sync_mode == "graph",
    }
}

/// `[{name, points}]` for every collection the child reported writing.
/// `points` is null when the lane names a collection without reporting how
/// many points it put there (the message lane, the Python doc ingestor).
fn collections_from_child_summary(summary: &Value) -> Vec<Value> {
    let mut out: Vec<(String, Option<i64>)> = Vec::new();
    let mut push = |name: String, points: Option<i64>| {
        if name.is_empty() {
            return;
        }
        match out.iter_mut().find(|(existing, _)| *existing == name) {
            Some(slot) => {
                if let Some(added) = points {
                    slot.1 = Some(slot.1.unwrap_or(0) + added);
                }
            }
            None => out.push((name, points)),
        }
    };
    if let Some(entries) = summary.get("vector_embeddings").and_then(|v| v.as_array()) {
        for entry in entries {
            let name = entry
                .get("qdrant_collection")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            push(name, entry.get("vector_count").and_then(|v| v.as_i64()));
        }
    }
    if let Some(messages) = summary.get("native_message_scan") {
        let name = messages
            .get("qdrant_collection")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        push(name, None);
    }
    out.into_iter()
        .map(|(name, points)| json!({"name": name, "points": points}))
        .collect()
}

fn first_string(summary: &Value, path: &[&str]) -> String {
    let mut node = summary;
    for key in path {
        node = match node.get(*key) {
            Some(v) => v,
            None => return String::new(),
        };
    }
    node.as_str().unwrap_or("").to_string()
}

/// The graph-journal SQLite files the run used: the replay's own journal when
/// it reported one, plus every per-stage `journal_path`. These are the exact
/// paths `dev journal status --journal-path` expects.
fn journal_paths_from_child_summary(summary: &Value) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut push = |path: &str| {
        if !path.is_empty() && !out.iter().any(|existing| existing == path) {
            out.push(path.to_string());
        }
    };
    push(&first_string(summary, &["journal", "path"]));
    for key in [
        "primary_parsers",
        "framework_overlays",
        "topology_overlays",
        "vector_embeddings",
    ] {
        let Some(entries) = summary.get(key).and_then(|v| v.as_array()) else {
            continue;
        };
        for entry in entries {
            push(entry.get("journal_path").and_then(|v| v.as_str()).unwrap_or(""));
        }
    }
    out.sort();
    out
}

/// One `  <label> <Backend> (<scope>): <location>` line, with the namespace,
/// TLS and credential presence appended without ever exposing a secret.
fn render_destination(label: &str, dest: &Destination, namespace_label: &str) -> String {
    let mut line = format!(
        "  {:<9} {} ({}): {}",
        label,
        backend_label(&dest.backend),
        dest.scope,
        dest.location
    );
    if !dest.namespace.is_empty() {
        line.push_str(&format!("  {namespace_label}={}", dest.namespace));
    }
    if dest.scope == "remote" || dest.scope.ends_with("server") {
        line.push_str(if dest.tls { "  tls=yes" } else { "  tls=no" });
        if dest.auth {
            line.push_str("  auth=set");
        }
    }
    line
}

/// The "where did the data go" block: the effective targets for the lane plus
/// the on-disk artifacts (collections, journal, state) the run touched. Built
/// as lines so the summary box and the tests share one rendering.
fn destination_lines(dest: &LaneDestinations, summaries: &[Value]) -> Vec<String> {
    let mut out = Vec::new();
    let instance = if dest.instance.is_empty() {
        "default"
    } else {
        dest.instance.as_str()
    };
    out.push(format!("  Stored in  instance={instance}"));

    if dest.graph_skipped {
        out.push("  graph     skipped (--sync-mode embedding)".to_string());
    } else if let Some(graph) = &dest.graph {
        let label = if graph.backend == "neo4j" { "db" } else { "graph" };
        out.push(render_destination("graph", graph, label));
    } else {
        out.push("  graph     no graph target resolved".to_string());
    }

    if dest.vectors_skipped {
        out.push("  vectors   skipped (--sync-mode graph)".to_string());
    } else if let Some(vectors) = &dest.vectors {
        let collected = collected_writes(summaries);
        // None while no lane reported a count, so an unknown never prints as 0.
        let mut total: Option<i64> = None;
        for (_, points) in collected.iter() {
            if let Some(value) = points {
                total = Some(total.unwrap_or(0) + value);
            }
        }
        let mut line = render_destination("vectors", vectors, "collection");
        if let Some(sum) = total {
            line.push_str(&format!("  points={sum}"));
        }
        out.push(line);
        for (name, points) in collected.iter().take(6) {
            out.push(match points {
                Some(count) => format!("             {name}  {count}"),
                None => format!("             {name}"),
            });
        }
        if collected.len() > 6 {
            out.push(format!(
                "             +{} more collection(s)",
                collected.len() - 6
            ));
        }
    } else {
        out.push("  vectors   no vector target resolved".to_string());
    }

    out.extend(artifact_lines("journal", &artifact_paths(summaries, "journal_paths")));
    out.extend(artifact_lines("state", &artifact_paths(summaries, "state_path")));
    out
}

/// Unique paths recorded under one summary key across every folder.
fn artifact_paths(summaries: &[Value], key: &str) -> Vec<String> {
    let mut paths: Vec<String> = Vec::new();
    for s in summaries {
        match s.get(key) {
            Some(Value::String(text)) => {
                if !text.is_empty() && !paths.contains(text) {
                    paths.push(text.clone());
                }
            }
            Some(Value::Array(items)) => {
                for item in items.iter().filter_map(|v| v.as_str()) {
                    if !item.is_empty() && !paths.iter().any(|existing| existing == item) {
                        paths.push(item.to_string());
                    }
                }
            }
            _ => {}
        }
    }
    paths.sort();
    paths
}

/// Three pasteable paths at most; every printed path is exact.
fn artifact_lines(label: &str, paths: &[String]) -> Vec<String> {
    let Some(first) = paths.first() else {
        return Vec::new();
    };
    let mut out = vec![format!("  {:<9} {}", label, first)];
    for path in paths.iter().skip(1).take(2) {
        out.push(format!("             {path}"));
    }
    if paths.len() > 3 {
        out.push(format!("             +{} more", paths.len() - 3));
    }
    out
}

/// Every collection the run reported writing, merged across folders.
fn collected_writes(summaries: &[Value]) -> Vec<(String, Option<i64>)> {
    let mut collected: Vec<(String, Option<i64>)> = Vec::new();
    for s in summaries {
        let Some(entries) = s.get("collections").and_then(|v| v.as_array()) else {
            continue;
        };
        for entry in entries {
            let name = entry.get("name").and_then(|v| v.as_str()).unwrap_or("");
            if name.is_empty() {
                continue;
            }
            let points = entry.get("points").and_then(|v| v.as_i64());
            match collected.iter_mut().find(|(existing, _)| *existing == name) {
                Some(slot) => {
                    if let Some(added) = points {
                        slot.1 = Some(slot.1.unwrap_or(0) + added);
                    }
                }
                None => collected.push((name.to_string(), points)),
            }
        }
    }
    collected.sort();
    collected
}

fn print_destinations(dest: &LaneDestinations, summaries: &[Value]) {
    echo(&"─".repeat(52));
    for line in destination_lines(dest, summaries) {
        echo(&line);
    }
}

// ---------------------------------------------------------------------------
// Summary printer (dev.py `_print_summary`)
// ---------------------------------------------------------------------------

pub(super) fn print_summary(summaries: &[Value], total_elapsed: f64, dest: &LaneDestinations) {
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
    print_destinations(dest, summaries);
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

#[cfg(test)]
mod destination_tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> Value {
        let mut map = serde_json::Map::new();
        for (key, value) in pairs {
            map.insert((*key).to_string(), json!(*value));
        }
        Value::Object(map)
    }

    #[test]
    fn ladybug_reports_the_embedded_store_file_and_graph() {
        let dest = graph_destination(
            &env(&[
                ("CODE_GRAPH_PROVIDER", "ladybug"),
                ("LADYBUG_PATH", "/data/ladybug/code/code.lbug/hyper_graph"),
                ("LADYBUG_GRAPH", "cortext"),
            ]),
            "code",
        )
        .expect("ladybug target");
        assert_eq!(dest.backend, "ladybug");
        assert_eq!(dest.scope, "local file");
        assert_eq!(dest.location, "/data/ladybug/code/code.lbug/hyper_graph");
        assert_eq!(dest.namespace, "cortext");
        assert!(!dest.tls);
    }

    #[test]
    fn falkordb_distinguishes_embedded_file_from_remote_endpoint() {
        let embedded = graph_destination(
            &env(&[
                ("CODE_GRAPH_PROVIDER", "falkordb"),
                ("FALKORDB_PATH", "/data/falkordb/code/data.rdb"),
                ("FALKORDB_GRAPH", "cortext"),
            ]),
            "code",
        )
        .expect("embedded target");
        assert_eq!(embedded.scope, "local file");
        assert_eq!(embedded.namespace, "cortext");

        let remote = graph_destination(
            &env(&[
                ("DOC_GRAPH_PROVIDER", "falkordb"),
                ("FALKORDB_URI", "rediss://db.example.com:6379"),
                ("FALKORDB_PASSWORD", "hunter2"),
            ]),
            "doc",
        )
        .expect("remote target");
        assert_eq!(remote.scope, "remote");
        assert!(remote.tls, "rediss:// implies TLS");
        assert!(remote.auth);
        // The name the doc lane writes is the role-scoped graph default.
        assert_eq!(remote.namespace, "hyper_graph");
        assert!(
            !remote.location.contains("hunter2"),
            "credentials never reach the location"
        );
    }

    #[test]
    fn loopback_endpoints_are_local_servers_not_remote() {
        let dest = graph_destination(
            &env(&[
                ("CODE_GRAPH_PROVIDER", "neo4j"),
                ("NEO4J_URI", "bolt://localhost:7687"),
                ("NEO4J_DB", "cortext"),
            ]),
            "code",
        )
        .expect("neo4j target");
        assert_eq!(dest.scope, "local server");
        assert_eq!(dest.location, "bolt://localhost:7687");
        assert_eq!(dest.namespace, "cortext");
    }

    #[test]
    fn vector_target_prefers_the_lane_path_the_children_receive() {
        let local = vector_destination(
            &env(&[("QDRANT_CODE_PATH", "/data/qdrant/code"), ("QDRANT_COLLECTION", "cortext")]),
            "code",
        )
        .expect("local vector target");
        assert_eq!(local.scope, "local dir");
        assert_eq!(local.location, "/data/qdrant/code");
        assert_eq!(local.namespace, "cortext");

        let remote = vector_destination(
            &env(&[
                ("QDRANT_DOC_PATH", "http://127.0.0.1:6333"),
                ("QDRANT_URL", "http://127.0.0.1:6333"),
                ("QDRANT_API_KEY", "secret"),
                ("QDRANT_COLLECTION_DOC", "cortext_doc"),
            ]),
            "doc",
        )
        .expect("server vector target");
        assert_eq!(remote.scope, "local server");
        assert_eq!(remote.namespace, "cortext_doc");
        assert!(remote.auth);
        assert!(!remote.location.contains("secret"));
    }

    #[test]
    fn sync_mode_subsets_are_reported_as_skipped() {
        let env = env(&[
            ("CODE_GRAPH_PROVIDER", "ladybug"),
            ("LADYBUG_PATH", "/data/lbug"),
            ("QDRANT_CODE_PATH", "/data/qdrant/code"),
        ]);
        let graph_only = lane_destinations(&env, "code", "graph");
        assert!(graph_only.vectors_skipped);
        assert!(!graph_only.graph_skipped);
        assert!(graph_only.graph.is_some());

        let embedding_only = lane_destinations(&env, "code", "embedding");
        assert!(embedding_only.graph_skipped);
        assert!(!embedding_only.vectors_skipped);
    }

    #[test]
    fn collections_merge_per_parser_entries_and_keep_unknown_counts_null() {
        let summary = json!({
            "vector_embeddings": [
                {"qdrant_collection": "cortext__ab12__go_functions", "vector_count": 7},
                {"qdrant_collection": "cortext__ab12__go_functions", "vector_count": 3},
                {"qdrant_collection": "", "vector_count": 5},
                {"qdrant_collection": "cortext__ab12__rust_functions", "vector_count": Value::Null}
            ],
            "native_message_scan": {"qdrant_collection": "cortext_mess"}
        });
        let collections = collections_from_child_summary(&summary);
        assert_eq!(
            collections,
            vec![
                json!({"name": "cortext__ab12__go_functions", "points": 10}),
                json!({"name": "cortext__ab12__rust_functions", "points": null}),
                json!({"name": "cortext_mess", "points": null}),
            ]
        );
    }

    #[test]
    fn journal_paths_collect_every_stage_and_drop_blanks() {
        assert_eq!(
            journal_paths_from_child_summary(&json!({
                "journal": {"path": "/j/replay.sqlite3"},
                "primary_parsers": [
                    {"journal_path": "/j/scope/cobol.sqlite3"},
                    {"journal_path": null},
                    {"journal_path": "/j/scope/cobol.sqlite3"}
                ],
                "framework_overlays": [{"journal_path": "/j/scope/spring.sqlite3"}]
            })),
            vec![
                "/j/replay.sqlite3".to_string(),
                "/j/scope/cobol.sqlite3".to_string(),
                "/j/scope/spring.sqlite3".to_string(),
            ]
        );
        assert!(journal_paths_from_child_summary(&json!({})).is_empty());
    }

    #[test]
    fn rendered_lines_name_the_engine_scope_and_namespace() {
        let dest = Destination {
            backend: "falkordb".to_string(),
            scope: "remote".to_string(),
            location: "redis://db.example.com:6379".to_string(),
            namespace: "cortext".to_string(),
            tls: false,
            auth: true,
        };
        assert_eq!(
            render_destination("graph", &dest, "graph"),
            "  graph     FalkorDB (remote): redis://db.example.com:6379  graph=cortext  \
             tls=no  auth=set"
        );
        let local = Destination {
            backend: "qdrant".to_string(),
            scope: "local dir".to_string(),
            location: "/data/qdrant/code".to_string(),
            namespace: String::new(),
            tls: false,
            auth: false,
        };
        assert_eq!(
            render_destination("vectors", &local, "collection"),
            "  vectors   Qdrant (local dir): /data/qdrant/code"
        );
    }

    #[test]
    fn block_lists_targets_writes_and_local_artifacts() {
        let env = env(&[
            ("CORTEX_STORAGE_INSTANCE", "cortex"),
            ("CODE_GRAPH_PROVIDER", "ladybug"),
            ("LADYBUG_PATH", "/data/ladybug/code/code.lbug/hyper_graph"),
            ("LADYBUG_GRAPH", "cortext"),
            ("QDRANT_CODE_PATH", "/data/qdrant/code"),
            ("QDRANT_COLLECTION_CODE", "cortext"),
        ]);
        let summaries = vec![json!({
            "folder": "rust",
            "status": "ok",
            "collections": [
                {"name": "cortext__ab12__rust_functions", "points": 412},
                {"name": "cortext_mess", "points": null}
            ],
            "journal_paths": ["/data/journal/0708/cobol.sqlite3"],
            "state_path": "/repo/.cache/incremental_sync/rust",
        })];
        assert_eq!(
            destination_lines(&lane_destinations(&env, "code", "both"), &summaries),
            vec![
                "  Stored in  instance=cortex".to_string(),
                "  graph     Ladybug (local file): /data/ladybug/code/code.lbug/hyper_graph  \
                 graph=cortext"
                    .to_string(),
                "  vectors   Qdrant (local dir): /data/qdrant/code  collection=cortext  \
                 points=412"
                    .to_string(),
                "             cortext__ab12__rust_functions  412".to_string(),
                "             cortext_mess".to_string(),
                "  journal   /data/journal/0708/cobol.sqlite3".to_string(),
                "  state     /repo/.cache/incremental_sync/rust".to_string(),
            ]
        );
    }
}

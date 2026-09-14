//! `dev sync code|doc` — thin orchestrator shells around the Python
//! toolchain, exactly as dev.py drives them:
//!   code → `code-tiny/tools/sync/incremental_sync.py`
//!   doc  → `doc-tiny/graphrag_ingest_langextract.py`
//! plus the native scan-root selection, retry loop, state files, and summary
//! printer. The per-process storage environment comes from the Python layer.

use crate::config::{graph_provider, ignore_folders, load_active_config, save_config};
use crate::parser::Matches;
use crate::pyexec;
use crate::util::{
    echo, echo_err, fail, fnmatch, git_head, git_status_since, iso_utc_now, is_excluded_dir_name,
    is_sensitive, load_state, local_strftime, match_any, prompt, save_state,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::Command;

pub const DOC_EXT_FLAGS: &[(&str, &str)] = &[
    (".pdf", "--pdf"),
    (".md", "--md"),
    (".docx", "--docx"),
    (".txt", "--text-file"),
    (".pptx", "--pptx"),
    (".xlsx", "--xlsx"),
];

/// Parser-name → analyzer-script map (paths relative to code-tiny/).
const LANG_ANALYZERS: &[(&str, &str)] = &[
    ("cobol", "tools/cobol/cobol_analyzer.py"),
    ("dart", "tools/flutter/flutter_analyzer.py"),
    ("delphi", "tools/delphi/delphi_analyzer.py"),
    ("go", "tools/go/go_analyzer.py"),
    ("perl", "tools/perl/perl_analyzer.py"),
    ("shell", "tools/shell/shell_analyzer.py"),
    ("jp1", "tools/jp1/jp1_analyzer.py"),
    ("kotlin", "tools/kotlin/kotlin_analyzer.py"),
    ("java", "tools/java/java_analyzer.py"),
    ("ts", "tools/ts/ts_analyzer.py"),
    ("js", "tools/js/js_analyzer.py"),
    ("php", "tools/php/php_analyzer.py"),
    ("sql", "tools/sql/sql_analyzer.py"),
    ("plsql", "tools/plsql/plsql_analyzer.py"),
    ("cplus", "tools/cplus/cplus_analyzer.py"),
    ("csharp", "tools/csharp/csharp_analyzer.py"),
    ("python", "tools/python/python_analyzer.py"),
    ("rust", "tools/rust/rust_analyzer.py"),
    ("swift", "tools/swift/swift_analyzer.py"),
    ("vbnet", "tools/vb/vbnet_analyzer.py"),
    ("vb6", "tools/vb/vb6_analyzer.py"),
    ("vba", "tools/vb/vba_analyzer.py"),
    ("vbscript", "tools/vb/vbscript_analyzer.py"),
    ("android_java", "tools/android/android_java_analyzer.py"),
    ("android_kotlin", "tools/android/android_kotlin_analyzer.py"),
    ("android_mixed", "tools/android/android_mixed_analyzer.py"),
];

const FRAMEWORK_ANALYZERS: &[(&str, &str)] = &[
    ("spring", "tools/spring/spring_analyzer.py"),
    ("servlet_jsp", "tools/servlet_jsp/servlet_jsp_analyzer.py"),
    ("mybatis", "tools/mybatis/mybatis_analyzer.py"),
    ("struts", "tools/struts/struts_analyzer.py"),
    ("flutter", "tools/flutter/flutter_analyzer.py"),
    ("aspnet_framework", "tools/aspnet_framework/aspnet_framework_analyzer.py"),
    ("aspnet_core", "tools/aspnet_core/aspnet_core_analyzer.py"),
    ("fastapi_django", "tools/web_framework/web_framework_analyzer.py"),
    ("express_js", "tools/web_framework/web_framework_analyzer.py"),
    ("laravel", "tools/web_framework/web_framework_analyzer.py"),
    ("database_sql", "tools/database_schema/database_schema_analyzer.py"),
    ("database_plsql", "tools/database_schema/database_schema_analyzer.py"),
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

fn absolute_or_join(folder: &str, project_path: &Path) -> PathBuf {
    let p = Path::new(folder);
    if p.is_absolute() {
        p.to_path_buf()
    } else {
        project_path.join(p)
    }
}

/// dev.py `_select_folders_interactive`.
fn select_folders_interactive(folders: &[String]) -> Vec<String> {
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
fn env_to_neo4j_args(env: &Value) -> Vec<String> {
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

        let journal_mode = process_env
            .iter()
            .find(|(k, _)| k == "CORTEX_GRAPH_JOURNAL_MODE")
            .map(|(_, v)| v.to_lowercase())
            .unwrap_or_default();
        if matches!(journal_mode.as_str(), "required" | "shared-required") {
            let root = pyexec::repo_root();
            let code_tiny = root.join("code-tiny");
            let python = cmd[0].clone();
            let mut module_env = process_env.clone();
            let pythonpath = module_env
                .iter()
                .find(|(k, _)| k == "PYTHONPATH")
                .map(|(_, v)| v.clone())
                .unwrap_or_default();
            let new_path = if pythonpath.is_empty() {
                code_tiny.to_string_lossy().to_string()
            } else {
                format!("{}:{}", code_tiny.display(), pythonpath)
            };
            replace_or_push(&mut module_env, "PYTHONPATH", new_path);
            let recovery = Command::new(&python)
                .args(["-m", "tools.graph.journal.consumer"])
                .current_dir(&code_tiny)
                .envs(module_env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
                .status();
            let rc = recovery.map(|s| s.code().unwrap_or(1)).unwrap_or(1);
            if rc != 0 {
                echo_err(&format!("  [error] graph journal recovery exited {}", rc));
                return rc;
            }
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
            let result = pyexec::call_raw(
                "mcp_start",
                &json!({ "name": self.service_name, "project_dir": self.project_path.to_string_lossy() }),
            );
            let parsed: Option<Value> = serde_json::from_str(result.stdout.trim()).ok();
            match parsed {
                Some(v) if v.get("status").and_then(|s| s.as_str()) == Some("started") => {
                    echo(&format!(
                        "[sync] {} MCP restarted (pid={})",
                        capitalize(&self.owner),
                        v.get("pid").map(|p| p.to_string()).unwrap_or_else(|| "?".to_string())
                    ));
                }
                Some(v) => {
                    let reason = v.get("reason").and_then(|r| r.as_str()).unwrap_or("unknown error");
                    echo_err(&format!(
                        "[sync] WARNING: {} MCP was paused but could not be restarted: {}",
                        self.owner, reason
                    ));
                }
                None => {
                    echo_err(&format!(
                        "[sync] WARNING: {} MCP was paused but could not be restarted",
                        self.owner
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
                pyexec::call_json(
                    "mcp_stop",
                    &json!({ "pattern": service.2, "instance_id": instance_id }),
                );
            } else {
                echo("[sync] Stopping orphaned embedded FalkorDB before sync");
            }
            let stopped = pyexec::call_json("stop_embedded", &json!({ "db_path": db_path.to_string_lossy() }));
            if stopped.as_i64().unwrap_or(0) > 0 {
                echo(&format!(
                    "[sync] stopped {} embedded FalkorDB process(es)",
                    stopped.as_i64().unwrap_or(0)
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
    match pyexec::try_call_json("embedded_falkordb_pids", &json!({ "db_path": db_path })) {
        Ok(v) => v
            .as_array()
            .map(|a| a.iter().filter_map(|x| x.as_i64()).collect())
            .unwrap_or_default(),
        Err(_) => Vec::new(),
    }
}

/// dev.py `_stop_sync_workers`.
fn stop_sync_workers(owner: &str, process_env: &Value, prefix: &str, include_launchers: bool) {
    let args = json!({
        "owner": owner,
        "include_launchers": include_launchers,
        "falkordb_path": process_env.get("FALKORDB_PATH").and_then(|v| v.as_str()).unwrap_or(""),
    });
    let report = pyexec::call_json("stop_sync_workers", &args);
    let matched = report.get("matched").and_then(|m| m.as_array()).map(|a| a.len()).unwrap_or(0);
    if matched > 0 {
        echo(&format!(
            "[sync] {}: owner={} matched={} terminated={} forced={}",
            prefix,
            owner,
            matched,
            report.get("terminated").and_then(|m| m.as_array()).map(|a| a.len()).unwrap_or(0),
            report.get("forced").and_then(|m| m.as_array()).map(|a| a.len()).unwrap_or(0),
        ));
    }
    let remaining: Vec<String> = report
        .get("remaining")
        .and_then(|m| m.as_array())
        .map(|a| a.iter().map(|x| x.to_string()).collect())
        .unwrap_or_default();
    if !remaining.is_empty() {
        fail(&format!(
            "Could not stop {} sync process(es): {}",
            owner,
            remaining.join(", ")
        ));
    }
    let embedded_stopped = report.get("embedded_stopped").and_then(|m| m.as_array()).map(|a| a.len()).unwrap_or(0);
    if embedded_stopped > 0 {
        echo(&format!(
            "[sync] {}: stopped {} embedded FalkorDB process(es)",
            prefix, embedded_stopped
        ));
    }
    let embedded_remaining: Vec<String> = report
        .get("embedded_remaining")
        .and_then(|m| m.as_array())
        .map(|a| a.iter().map(|x| x.to_string()).collect())
        .unwrap_or_default();
    if !embedded_remaining.is_empty() {
        fail(&format!(
            "Could not stop embedded FalkorDB process(es): {}",
            embedded_remaining.join(", ")
        ));
    }
}

fn stop_sync_command(owner: &str, project_path: &Path, process_env: &Value) {
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

    let incremental_sync = pyexec::repo_file("code-tiny/tools/sync/incremental_sync.py");
    let python = pyexec::venv_python(&pyexec::repo_root().join("code-tiny"));
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
            python.clone(),
            incremental_sync.to_string_lossy().to_string(),
            "--root".to_string(),
            folder_path.to_string_lossy().to_string(),
            "--project-id".to_string(),
            project_id.clone(),
            "--project-name".to_string(),
            project.get("name").and_then(|v| v.as_str()).unwrap_or("project").to_string(),
            "--python-bin".to_string(),
            python.clone(),
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

    let mut available: Vec<String> = Vec::new();
    for (lang, rel) in LANG_ANALYZERS.iter().chain(FRAMEWORK_ANALYZERS.iter()) {
        if pyexec::repo_file(&format!("code-tiny/{}", rel)).exists() {
            available.push(lang.to_string());
        }
    }
    echo(&format!(
        "\n[sync-code all]  folders={}  analyzers={}",
        folders.len(),
        available.len()
    ));
    echo(&format!("  tools: {}", available.join(", ")));

    let incremental_sync = pyexec::repo_file("code-tiny/tools/sync/incremental_sync.py");
    let python = pyexec::venv_python(&pyexec::repo_root().join("code-tiny"));
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
            python.clone(),
            incremental_sync.to_string_lossy().to_string(),
            "--root".to_string(),
            folder_path.to_string_lossy().to_string(),
            "--project-id".to_string(),
            project_id.clone(),
            "--project-name".to_string(),
            project.get("name").and_then(|v| v.as_str()).unwrap_or("project").to_string(),
            "--python-bin".to_string(),
            python.clone(),
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

pub fn sync_doc(m: &Matches, force_full: bool) {
    sync_doc_impl(m, force_full)
}

pub fn sync_doc_impl(m: &Matches, force_full: bool) {
    let project_dir = m.value_or("--project-dir", ".");
    let preview = m.flag("--preview");
    let entity_provider = m.value_or("--entity-provider", "gliner");
    let dry_run = m.flag("--dry-run");

    let project_path = super::init::resolve_path(Path::new(&project_dir));
    let (cfg, _) = load_active_config(&project_path);
    let doc_cfg = cfg.get("doc").cloned().unwrap_or(json!({}));
    let env = crate::env::doc_env(&project_path);
    let env = with_extra_ignores(&cfg, &env);
    let extra_ignores = ignore_folders(&cfg);
    let project = cfg.get("project").cloned().unwrap_or(json!({}));
    let folders = crate::config::source_folders(doc_cfg.get("source").unwrap_or(&json!({})));

    if folders.is_empty() {
        echo("[warn] No doc folders configured. Run 'dev init' or 'dev sync doc add'.");
        return;
    }

    let doc_ingestor = pyexec::repo_file("doc-tiny/graphrag_ingest_langextract.py");
    if !doc_ingestor.exists() {
        echo_err(&format!("[error] Ingestor not found: {}", doc_ingestor.display()));
        std::process::exit(1);
    }

    let selected: Vec<String> = if force_full {
        folders.clone()
    } else {
        select_folders_interactive(&folders)
    };
    if !force_full {
        warn_scan_roots_matching_ignores(&selected, &extra_ignores);
    } else {
        echo(&format!("\n[sync-doc all]  folders={}", folders.len()));
        warn_scan_roots_matching_ignores(&folders, &extra_ignores);
    }
    if selected.is_empty() {
        echo("[info] No folders selected.");
        return;
    }

    let python = pyexec::venv_python(&pyexec::repo_root().join("doc-tiny"));
    let mut summaries: Vec<Value> = Vec::new();
    let total_start = std::time::Instant::now();

    let guard = sync_lifecycle("doc", &env, &project_path, !dry_run, true);
    for folder in &selected {
        let result = sync_doc_folder(
            &project_path,
            folder,
            &env,
            &python,
            &project,
            if force_full { "full" } else { "auto" },
            &entity_provider,
            dry_run,
            preview && !force_full,
            &extra_ignores,
        );
        summaries.push(result);
    }
    drop(guard);
    print_summary(&summaries, total_start.elapsed().as_secs_f64());
}

pub fn sync_doc_stop(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let env = crate::env::doc_env(&project_path);
    stop_sync_command("doc", &project_path, &env);
}

/// dev.py `_sync_doc_folder`.
#[allow(clippy::too_many_arguments)]
fn sync_doc_folder(
    project_path: &Path,
    folder: &str,
    env: &Value,
    python: &str,
    project: &Value,
    force_mode: &str,
    entity_provider: &str,
    dry_run: bool,
    preview: bool,
    extra_ignores: &[String],
) -> Value {
    let folder_path = absolute_or_join(folder, project_path);
    if !folder_path.exists() {
        echo(&format!("\n[warn] Folder not found: {} — skipping", folder_path.display()));
        return json!({"folder": folder, "status": "skipped", "reason": "not found"});
    }

    let state_key = format!("doc:{}", folder);
    let state = load_state(project_path, &state_key);
    let has_state = state.as_object().map(|o| !o.is_empty()).unwrap_or(false);
    let mode = if force_mode != "auto" {
        force_mode.to_string()
    } else if has_state {
        "incremental".to_string()
    } else {
        "full".to_string()
    };

    let project_id = project
        .get("code")
        .and_then(|v| v.as_str())
        .or_else(|| project.get("name").and_then(|v| v.as_str()))
        .unwrap_or("project")
        .to_string();
    let collection = env
        .get("QDRANT_COLLECTION_DOC")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| env.get("QDRANT_COLLECTION").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
        .map(String::from)
        .unwrap_or_else(|| format!("{}_doc", project_id));

    let doc_ingestor = pyexec::repo_file("doc-tiny/graphrag_ingest_langextract.py");
    let mut base_cmd = vec![
        python.to_string(),
        doc_ingestor.to_string_lossy().to_string(),
    ];
    base_cmd.extend(env_to_neo4j_args(env));
    let qdrant_doc_path = env
        .get("QDRANT_DOC_PATH")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    base_cmd.push("--qdrant-path".to_string());
    base_cmd.push(qdrant_doc_path);
    base_cmd.push("--project-id".to_string());
    base_cmd.push(project_id.clone());
    base_cmd.push("--collection".to_string());
    base_cmd.push(collection);
    base_cmd.push("--entity-provider".to_string());
    base_cmd.push(entity_provider.to_string());
    base_cmd.push("--embedding-model".to_string());
    base_cmd.push(
        env.get("EMBEDDING_MODEL")
            .and_then(|v| v.as_str())
            .unwrap_or("BAAI/bge-m3")
            .to_string(),
    );
    base_cmd.push("--embedding-device".to_string());
    base_cmd.push(embed_device_cli_arg(env));
    base_cmd.push("--max-paragraph-chars".to_string());
    base_cmd.push(
        env.get("MAX_PARAGRAPH_CHARS")
            .and_then(|v| v.as_str())
            .unwrap_or("500")
            .to_string(),
    );
    base_cmd.push("--gliner-model-name".to_string());
    base_cmd.push(
        env.get("GLINER_MODEL_NAME")
            .and_then(|v| v.as_str())
            .unwrap_or("urchade/gliner_large-v2.1")
            .to_string(),
    );
    base_cmd.push("--gliner-labels".to_string());
    base_cmd.push(
        env.get("GLINER_LABELS")
            .and_then(|v| v.as_str())
            .unwrap_or("PERSON,ORG,PRODUCT,GPE,DATE,TECH,CRYPTO,STANDARD")
            .to_string(),
    );
    base_cmd.push("--gliner-threshold".to_string());
    base_cmd.push(
        env.get("GLINER_THRESHOLD")
            .and_then(|v| v.as_str())
            .unwrap_or("0.35")
            .to_string(),
    );
    base_cmd.push("--gliner-batch-size".to_string());
    base_cmd.push(
        env.get("GLINER_BATCH_SIZE")
            .and_then(|v| v.as_str())
            .unwrap_or("1")
            .to_string(),
    );
    base_cmd.push("--neo4j-batch-size".to_string());
    base_cmd.push(
        env.get("NEO4J_BATCH_SIZE")
            .and_then(|v| v.as_str())
            .unwrap_or("1")
            .to_string(),
    );
    base_cmd.push("--no-batch".to_string());

    let divider = "─".repeat(52);
    echo(&format!("\n{}", divider));
    echo(&format!(" folder : {}", folder));
    echo(&format!(" mode   : {}", mode));
    echo(&format!(" provider: {}", entity_provider));

    let start_ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();

    if mode == "full" {
        let all_files = find_doc_files(&folder_path, extra_ignores);
        echo(&format!(" files  : {} document(s)", all_files.len()));
        if all_files.is_empty() {
            echo("  [warn] No supported document files found — skipping");
            return json!({"folder": folder, "status": "skipped", "reason": "no doc files"});
        }

        let mut cmd = base_cmd.clone();
        cmd.push("--folder".to_string());
        cmd.push(folder_path.to_string_lossy().to_string());
        let opts = RetryOptions {
            max_retries: 3,
            dry_run,
            env: Some(env),
            non_retryable_exit_codes: &[],
            result_path: None,
        };
        let rc = run_with_retry(&cmd, &opts);
        let elapsed = start_elapsed(start_ts);

        if rc == 0 && !dry_run {
            let file_hashes = build_file_hashes(&folder_path, extra_ignores);
            save_state(
                project_path,
                &state_key,
                &json!({
                    "folder": folder,
                    "last_sync": iso_utc_now(),
                    "last_sync_ts": start_ts,
                    "mode": "full",
                    "git_commit": git_head(&folder_path),
                    "file_hashes": file_hashes,
                    "file_count": all_files.len(),
                }),
            );
        }

        return json!({
            "folder": folder,
            "status": if rc == 0 { "ok" } else { "error" },
            "mode": mode,
            "elapsed": elapsed,
        });
    }

    // Incremental.
    let (changed_files, deleted_rel) = detect_changed_docs(&folder_path, &state, extra_ignores);
    if changed_files.is_empty() && deleted_rel.is_empty() {
        echo("  [ok] No changes detected — skipping");
        return json!({"folder": folder, "status": "skipped", "reason": "no changes"});
    }
    echo(&format!(
        "  changed: {}  deleted: {}",
        changed_files.len(),
        deleted_rel.len()
    ));

    if preview && !changed_files.is_empty() {
        echo(&format!("\n  Preview — {} file(s) queued:", changed_files.len()));
        for f in sorted_paths(&changed_files).into_iter().take(20) {
            let rel = f
                .strip_prefix(&folder_path)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| f.to_string_lossy().to_string());
            echo(&format!("    + {}", rel));
        }
        if changed_files.len() > 20 {
            echo(&format!("    … and {} more", changed_files.len() - 20));
        }
        if !deleted_rel.is_empty() {
            echo(&format!("  Deleted ({}):", deleted_rel.len()));
            let mut sorted_deleted = deleted_rel.clone();
            sorted_deleted.sort();
            for r in sorted_deleted.iter().take(10) {
                echo(&format!("    - {}", r));
            }
        }
        if !prompt("\n  Proceed?", "y").starts_with('y') {
            return json!({"folder": folder, "status": "cancelled"});
        }
    }

    let mut errors = 0u32;
    for file_path in &changed_files {
        let ext = file_path
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        let Some(flag) = DOC_EXT_FLAGS
            .iter()
            .find(|(e, _)| *e == ext)
            .map(|(_, f)| f.to_string())
        else {
            continue;
        };
        let name = file_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        echo(&format!("  [+] {}", name));
        let mut cmd = base_cmd.clone();
        cmd.push(flag);
        cmd.push(file_path.to_string_lossy().to_string());
        let opts = RetryOptions {
            max_retries: 3,
            dry_run,
            env: Some(env),
            non_retryable_exit_codes: &[],
            result_path: None,
        };
        let rc = run_with_retry(&cmd, &opts);
        if rc != 0 {
            echo(&format!("    [error] exited {}", rc));
            errors += 1;
        }
    }

    let elapsed = start_elapsed(start_ts);
    let success = errors == 0;

    if success && !dry_run {
        let mut new_hashes: serde_json::Map<String, Value> = state
            .get("file_hashes")
            .and_then(|h| h.as_object())
            .cloned()
            .unwrap_or_default();
        for f in &changed_files {
            if let Ok(rel) = f.strip_prefix(&folder_path) {
                new_hashes.insert(
                    rel.to_string_lossy().to_string(),
                    json!(sha256_file(f)),
                );
            }
        }
        for rel in &deleted_rel {
            new_hashes.remove(rel);
        }
        save_state(
            project_path,
            &state_key,
            &json!({
                "folder": folder,
                "last_sync": iso_utc_now(),
                "last_sync_ts": start_ts,
                "mode": "incremental",
                "git_commit": git_head(&folder_path),
                "file_hashes": Value::Object(new_hashes),
                "file_count": changed_files.len(),
            }),
        );
    }

    json!({
        "folder": folder,
        "status": if success { "ok" } else { "error" },
        "mode": mode,
        "elapsed": elapsed,
    })
}

fn start_elapsed(start_ts: f64) -> f64 {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    now - start_ts
}

fn sorted_paths(paths: &[PathBuf]) -> Vec<PathBuf> {
    let mut v = paths.to_vec();
    v.sort();
    v
}

fn embed_device_cli_arg(env: &Value) -> String {
    crate::env::normalize_embed_device(env.get("device").and_then(|v| v.as_str()).unwrap_or("cpu"))
}

// ---------------------------------------------------------------------------
// Doc file discovery + change detection
// ---------------------------------------------------------------------------

fn doc_extension(ext: &str) -> bool {
    DOC_EXT_FLAGS.iter().any(|(e, _)| e.eq_ignore_ascii_case(ext))
}

fn find_doc_files(folder: &Path, extra_ignores: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    collect_doc_files(folder, folder, extra_ignores, &mut out);
    out.sort();
    out
}

fn collect_doc_files(base: &Path, root: &Path, extra_ignores: &[String], out: &mut Vec<PathBuf>) {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(base)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .collect();
    entries.sort();
    for entry in entries {
        if entry.is_dir() {
            let name = entry.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            if is_excluded_dir_name(&name) || match_any(&name, extra_ignores) {
                continue;
            }
            collect_doc_files(&entry, root, extra_ignores, out);
            continue;
        }
        if !entry.is_file() {
            continue;
        }
        let ext = entry
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        if doc_extension(&ext) && !is_sensitive(&entry) {
            let rel_parts: Vec<String> = entry
                .strip_prefix(root)
                .map(|p| p.iter().map(|x| x.to_string_lossy().to_string()).collect())
                .unwrap_or_default();
            if rel_parts.iter().any(|p| is_excluded_dir_name(p) || match_any(p, extra_ignores)) {
                continue;
            }
            out.push(entry);
        }
    }
}

fn sha256_file(path: &Path) -> String {
    let Ok(data) = std::fs::read(path) else { return String::new() };
    let mut hasher = Sha256::new();
    hasher.update(&data);
    hex(&hasher.finalize())
}

fn sha256_hex_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex(&hasher.finalize())[..16].to_string()
}

fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{:02x}", b)).collect()
}

fn detect_changed_docs(
    folder_path: &Path,
    state: &Value,
    extra_ignores: &[String],
) -> (Vec<PathBuf>, Vec<String>) {
    let since_commit = state.get("git_commit").and_then(|v| v.as_str()).unwrap_or("");
    let since_ts = state.get("last_sync_ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let stored_hashes = state.get("file_hashes").cloned().unwrap_or(json!({}));

    if !since_commit.is_empty() {
        let current = git_head(folder_path);
        if !current.is_empty() && current == since_commit {
            return (Vec::new(), Vec::new());
        }
        let (changed_rel, deleted_rel) = git_status_since(folder_path, since_commit);
        let changed: Vec<PathBuf> = changed_rel
            .iter()
            .map(|r| folder_path.join(r))
            .filter(|p| {
                let ext = p
                    .extension()
                    .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                    .unwrap_or_default();
                doc_extension(&ext) && !is_sensitive(p)
            })
            .collect();
        let deleted: Vec<String> = deleted_rel
            .into_iter()
            .filter(|r| {
                let ext = Path::new(r)
                    .extension()
                    .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                    .unwrap_or_default();
                doc_extension(&ext)
            })
            .collect();
        return (changed, deleted);
    }

    let stored_map = stored_hashes.as_object().cloned().unwrap_or_default();
    if !stored_map.is_empty() {
        let current_files = find_doc_files(folder_path, extra_ignores);
        let mut changed = Vec::new();
        let mut deleted = Vec::new();
        let mut current_rels: HashSet<String> = HashSet::new();
        for f in &current_files {
            let rel = f
                .strip_prefix(folder_path)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();
            current_rels.insert(rel.clone());
            let stored = stored_map.get(&rel).and_then(|v| v.as_str()).unwrap_or("");
            if sha256_file(f) != stored {
                changed.push(f.clone());
            }
        }
        for key in stored_map.keys() {
            if !current_rels.contains(key) {
                deleted.push(key.clone());
            }
        }
        return (changed, deleted);
    }

    if since_ts > 0.0 {
        let changed: Vec<PathBuf> = find_doc_files(folder_path, extra_ignores)
            .into_iter()
            .filter(|f| {
                std::fs::metadata(f)
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs_f64() > since_ts)
                    .unwrap_or(false)
            })
            .collect();
        return (changed, Vec::new());
    }

    (Vec::new(), Vec::new())
}

fn build_file_hashes(folder: &Path, extra_ignores: &[String]) -> Value {
    let mut out = serde_json::Map::new();
    for f in find_doc_files(folder, extra_ignores) {
        if let Ok(rel) = f.strip_prefix(folder) {
            out.insert(rel.to_string_lossy().to_string(), json!(sha256_file(&f)));
        }
    }
    Value::Object(out)
}

// ---------------------------------------------------------------------------
// Summary printer (dev.py `_print_summary`)
// ---------------------------------------------------------------------------

fn print_summary(summaries: &[Value], total_elapsed: f64) {
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
fn with_extra_ignores(cfg: &Value, process_env: &Value) -> Value {
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

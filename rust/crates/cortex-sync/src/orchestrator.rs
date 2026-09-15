//! `_run_incremental` port — the incremental-sync orchestrator main line.

use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde_json::{json, Value};

use crate::cli::Args;
use crate::frameworks;
use crate::gitdiff;
use crate::graphops::{self, GraphContext};
use crate::inventory::{self, SourceInventory};
use crate::journalenv;
use crate::registry::{self, AnalyzerConfig};
use crate::routing;
use crate::state::{self, IncrementalSyncState};
use crate::syncscope::{
    read_lock_metadata, resolve_sync_cache_dir, safe_cache_root, scan_scope_id, ProjectRunLock,
};
use crate::tsdetect;
use crate::util;
use crate::walk;

const MAX_OUTPUT_TAIL_CHARS: usize = 65_536;

/// Sentinel prefix marking Python-plane delegation.
const DELEGATE_SENTINEL: &str = "__delegate__";

/// Subprocess failure mirroring `subprocess.CalledProcessError` with tails.
#[derive(Debug)]
#[allow(dead_code)]
pub struct ChildError {
    pub returncode: i32,
    pub cmd: Vec<String>,
    pub output_tail: String,
    pub stderr_tail: String,
}

impl std::fmt::Display for ChildError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Command returned non-zero exit status {}.", self.returncode)
    }
}

/// `_run` — stream child stdout line-by-line, capture the last [SCAN_RESULT].
fn run_child(
    cmd: &[String],
    cwd: &Path,
    verbose: bool,
    env: &BTreeMap<String, String>,
) -> Result<String, ChildError> {
    if verbose {
        println!("[upsert] exec: {}", cmd.join(" "));
    }
    let mut command = std::process::Command::new(&cmd[0]);
    command
        .args(&cmd[1..])
        .current_dir(cwd)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    for (key, value) in env {
        command.env(key, value);
    }
    let mut child = command.spawn().map_err(|error| ChildError {
        returncode: 127,
        cmd: cmd.to_vec(),
        output_tail: String::new(),
        stderr_tail: error.to_string(),
    })?;
    let stdout = child.stdout.take().expect("stdout piped");
    let stderr = child.stderr.take().expect("stderr piped");
    let stderr_thread = std::thread::spawn(move || {
        let mut tail = String::new();
        let mut reader = std::io::BufReader::new(stderr);
        let mut line = String::new();
        while matches!(reader.read_line(&mut line), Ok(n) if n > 0) {
            push_window(&mut tail, &line);
            line.clear();
        }
        tail
    });
    let mut scan_result = String::new();
    let mut output_tail = String::new();
    let mut reader = std::io::BufReader::new(stdout);
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {
                print!("{line}");
                let _ = std::io::stdout().flush();
                if line.contains("[SCAN_RESULT]") {
                    scan_result = tail_window(&line);
                }
                push_window(&mut output_tail, &line);
            }
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                let stderr_tail = stderr_thread.join().unwrap_or_default();
                return Err(ChildError {
                    returncode: -1,
                    cmd: cmd.to_vec(),
                    output_tail,
                    stderr_tail: format!("{stderr_tail}{error}"),
                });
            }
        }
    }
    let returncode = child.wait().map(|status| status.code().unwrap_or(-1)).unwrap_or(-1);
    let stderr_tail = stderr_thread.join().unwrap_or_default();
    if returncode != 0 {
        return Err(ChildError {
            returncode,
            cmd: cmd.to_vec(),
            output_tail,
            stderr_tail,
        });
    }
    if !stderr_tail.is_empty() {
        eprint!("{stderr_tail}");
    }
    Ok(scan_result)
}

fn push_window(tail: &mut String, line: &str) {
    let window = tail_window(line);
    tail.push_str(&window);
    if tail.len() > MAX_OUTPUT_TAIL_CHARS {
        let cut = tail.len() - MAX_OUTPUT_TAIL_CHARS;
        *tail = tail[cut..].to_string();
    }
}

fn tail_window(line: &str) -> String {
    if line.len() > MAX_OUTPUT_TAIL_CHARS {
        line[line.len() - MAX_OUTPUT_TAIL_CHARS..].to_string()
    } else {
        line.to_string()
    }
}

/// `_run_incremental` exit codes: 0 success, 1 failure, 2 lock busy.
pub fn run_incremental(args: &Args) -> i32 {
    let started = Instant::now();
    let run_id = env_lookup("CORTEX_RUN_ID").unwrap_or_else(util::uuid4_hex);
    let correlation_id = env_lookup("CORTEX_CORRELATION_ID").unwrap_or_else(|| run_id.clone());
    let root = util::realpath(&args.root);
    let root_str = util::path_to_string(&root);
    let project_id = args
        .project_id
        .clone()
        .unwrap_or_else(|| {
            root.file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| root_str.clone())
        });
    let project_name = args.project_name.clone().unwrap_or_else(|| project_id.clone());
    let control_cache_dir = resolve_sync_cache_dir(args.cache_dir.as_deref(), &root);
    let scope_id = scan_scope_id(&project_id, &root);
    let graph_selected = matches!(args.sync_mode.as_str(), "both" | "graph");
    let embedding_selected = graph_selected || args.sync_mode == "embedding";

    let graph_context: Option<GraphContext> = if !graph_selected || args.no_graph {
        None
    } else {
        match graphops::prepare_graph_args(args) {
            Ok(context) => context,
            Err(reason) => {
                return delegate_to_python(&format!("graph target resolution: {reason}"))
            }
        }
    };
    let graph_ready = graph_context.is_some();

    let summary_path: String = match &args.summary_path {
        Some(path) => path.clone(),
        None => {
            let summary_root = control_cache_dir.join("incremental_sync_summaries");
            let invocation = format!("{}_{}", std::process::id(), util::uuid4_hex());
            summary_root
                .join(format!(
                    "{}_{}_{}.json",
                    util::safe_segment(&project_id),
                    scope_id,
                    invocation
                ))
                .to_string_lossy()
                .to_string()
        }
    };

    let mut summary = initial_summary(
        args,
        &run_id,
        &correlation_id,
        &root_str,
        &project_id,
        &project_name,
        graph_ready,
        embedding_selected,
        &control_cache_dir,
        &scope_id,
    );

    let mut exit_code = 1i32;
    let mut lock_acquired = false;
    let mut state_opt: Option<IncrementalSyncState> = None;
    let mut state_path = PathBuf::new();
    let mut current_inventory: Option<SourceInventory> = None;
    let mut parse_quality_manifest_path: Option<String> = None;

    match run_flow(
        args,
        &mut summary,
        &root,
        &root_str,
        &project_id,
        &project_name,
        &control_cache_dir,
        &scope_id,
        graph_ready,
        graph_context.as_ref(),
        &summary_path,
        &run_id,
        &correlation_id,
        &mut lock_acquired,
        &mut state_opt,
        &mut state_path,
        &mut current_inventory,
        &mut parse_quality_manifest_path,
        &mut exit_code,
    ) {
        Ok(()) => {}
        Err(message) => {
            if let Some(reason) = message.strip_prefix(DELEGATE_SENTINEL) {
                return delegate_to_python(reason);
            }
            summary.insert("status".into(), json!("failed"));
            summary.insert("outcome".into(), json!("failed"));
            summary.insert("error".into(), json!(message));
            let dirty_marked = mark_dirty_on_failure(
                &control_cache_dir,
                state_opt.as_mut(),
                &state_path,
                lock_acquired,
                current_inventory.as_ref(),
                &message,
                &mut summary,
            );
            summary.insert("dirty_marked".into(), json!(dirty_marked));
            if dirty_marked {
                eprintln!("[state] marked dirty: {message}");
            } else {
                eprintln!("[state] failed before dirty-state update: {message}");
            }
            exit_code = 1;
        }
    }

    // ── finally: aggregate, release lock, write summary ──
    let mut aggregate: Vec<Value> = Vec::new();
    for key in ["primary_parsers", "framework_overlays", "topology_overlays", "vector_embeddings"] {
        if let Some(Value::Array(entries)) = summary.get(key).cloned() {
            for entry in entries {
                if !aggregate.contains(&entry) {
                    aggregate.push(entry);
                }
            }
        }
    }
    summary.insert("parsers".into(), json!(aggregate));
    if lock_acquired && args.verbose {
        let lock_path_display = summary
            .get("lock")
            .and_then(|lock| lock.get("path"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        println!("[state] lock released: {lock_path_display}");
    }
    summary.insert("finished_at".into(), json!(util::now_iso()));
    summary.insert(
        "duration_seconds".into(),
        json!(util::round_digits(started.elapsed().as_secs_f64(), 6)),
    );
    if let Some(manifest_path) = &parse_quality_manifest_path {
        let artifact_dir = Path::new(manifest_path).parent().map(Path::to_path_buf);
        if let Some(artifact_dir) = artifact_dir {
            let payload = json!({
                "schema_version": "1",
                "policy": args.parse_quality,
                "artifacts": [],
            });
            let text = format!("{}\n", serde_json::to_string_pretty(&payload).unwrap());
            if std::fs::create_dir_all(&artifact_dir).is_ok() && std::fs::write(manifest_path, text).is_ok() {
                println!("[parse-quality] run artifact: {manifest_path}");
                let _ = std::io::stdout().flush();
            } else {
                eprintln!("[parse-quality] failed writing run artifact");
            }
        }
    }
    let started_at = summary.get("started_at").and_then(Value::as_str).unwrap_or_default().to_string();
    let finished_at = summary.get("finished_at").and_then(Value::as_str).unwrap_or_default().to_string();
    let status = summary.get("status").and_then(Value::as_str).unwrap_or_default().to_string();
    let legacy_outcome = summary.get("outcome").and_then(Value::as_str).unwrap_or_default().to_string();
    let outcome = if status == "success" {
        match legacy_outcome.as_str() {
            "no_changes" => "no_changes",
            "partial_coverage" => "success_with_quarantine",
            _ => "success",
        }
    } else {
        "failed_terminal"
    };
    let mut artifacts: Vec<Value> = Vec::new();
    if let Some(manifest_path) = &parse_quality_manifest_path
        && Path::new(manifest_path).exists() {
            artifacts.push(json!({
                "kind": "parse_quality",
                "path": manifest_path,
                "sha256": "",
                "byte_count": 0,
                "item_count": 0,
            }));
        }
    let run_result = json!({
        "schema_version": "1.0",
        "run_id": run_id,
        "correlation_id": correlation_id,
        "outcome": outcome,
        "phase": summary.get("phase").cloned().unwrap_or(json!("publishing")),
        "component": "incremental_sync",
        "failure": Value::Null,
        "phase_results": [],
        "artifacts": artifacts,
        "current_generation": "",
        "retained_generation": "",
        "retry_after_seconds": Value::Null,
        "started_at": started_at,
        "finished_at": finished_at,
    });
    summary.insert("run_result".into(), run_result);
    write_summary(Path::new(&summary_path), &Value::Object(summary));
    if args.verbose {
        println!("[state] summary json: {summary_path}");
    }
    exit_code
}

#[allow(clippy::too_many_arguments)]
fn initial_summary(
    args: &Args,
    run_id: &str,
    correlation_id: &str,
    root_str: &str,
    project_id: &str,
    project_name: &str,
    graph_ready: bool,
    embedding_selected: bool,
    control_cache_dir: &Path,
    scope_id: &str,
) -> serde_json::Map<String, Value> {
    let mut summary = serde_json::Map::new();
    summary.insert("run_id".into(), json!(run_id));
    summary.insert("correlation_id".into(), json!(correlation_id));
    summary.insert("phase".into(), json!("discovering"));
    summary.insert("project_id".into(), json!(project_id));
    summary.insert("project_name".into(), json!(project_name));
    summary.insert("root".into(), json!(root_str));
    summary.insert("strict_mode".into(), json!(args.strict));
    summary.insert("ignore_cache".into(), json!(args.ignore_cache));
    summary.insert("full_scan".into(), json!(args.full_scan));
    summary.insert("sync_mode".into(), json!(args.sync_mode));
    summary.insert("bootstrap_full_scan".into(), json!(false));
    summary.insert("recovery_full_scan".into(), json!(false));
    summary.insert("started_at".into(), json!(util::now_iso()));
    summary.insert("finished_at".into(), Value::Null);
    summary.insert("duration_seconds".into(), Value::Null);
    summary.insert("status".into(), json!("running"));
    summary.insert("outcome".into(), json!("running"));
    summary.insert("error".into(), json!(""));
    summary.insert("before_sha".into(), json!(""));
    summary.insert("after_sha".into(), json!(""));
    summary.insert(
        "services".into(),
        json!({
            "graph_ready": graph_ready,
            "neo4j_ready": graph_ready,
            "qdrant_ready": args.qdrant_url.is_some() && embedding_selected,
            "impact_expansion_used": false,
            "message_sync_enabled": args.sync_messages,
            "message_qdrant_collection": args.message_qdrant_collection.clone().unwrap_or_default(),
        }),
    );
    summary.insert("diff".into(), json!({"entries": 0, "changed": 0, "deleted": 0}));
    summary.insert("impact".into(), json!({"expanded_impacted": 0}));
    summary.insert("parsers".into(), json!([]));
    summary.insert("primary_parsers".into(), json!([]));
    summary.insert("framework_overlays".into(), json!([]));
    summary.insert("topology_overlays".into(), json!([]));
    summary.insert("vector_embeddings".into(), json!([]));
    summary.insert("component_failures".into(), json!([]));
    summary.insert("state_before".into(), json!({}));
    summary.insert("state_after".into(), json!({}));
    summary.insert("dirty_marked".into(), json!(false));
    summary.insert(
        "scope".into(),
        json!({"id": scope_id, "root": root_str, "cache_dir": control_cache_dir.to_string_lossy()}),
    );
    summary.insert("lock".into(), json!({}));
    summary.insert("change_detection".into(), json!(args.change_detection));
    summary.insert(
        "change_sources".into(),
        json!({"committed": 0, "staged": 0, "unstaged": 0, "untracked": 0, "inventory": 0}),
    );
    summary.insert("repositories".into(), json!([]));
    summary.insert("coverage_warnings".into(), json!([]));
    summary.insert(
        "reconciliation".into(),
        json!({"requested": args.reconcile, "performed": false}),
    );
    summary.insert(
        "parse_quality".into(),
        json!({"policy": args.parse_quality, "artifact": "", "artifacts": [], "aggregates": {}}),
    );
    summary
}

fn mark_dirty_on_failure(
    control_cache_dir: &Path,
    state_opt: Option<&mut IncrementalSyncState>,
    state_path: &Path,
    lock_acquired: bool,
    current_inventory: Option<&SourceInventory>,
    error: &str,
    summary: &mut serde_json::Map<String, Value>,
) -> bool {
    let mut dirty_inventory_path: Option<String> = None;
    if let Some(inventory) = current_inventory {
        match inventory::write_inventory_generation(control_cache_dir, inventory) {
            Ok(path) => dirty_inventory_path = Some(path),
            Err(inventory_error) => {
                push_warning(
                    summary,
                    json!({
                        "code": "dirty_inventory_write_failed",
                        "error": inventory_error.to_string(),
                    }),
                );
            }
        }
    }
    if !(lock_acquired && state_opt.is_some()) {
        return false;
    }
    let state = state_opt.expect("state checked");
    let before_sha = summary
        .get("before_sha")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let after_sha = summary
        .get("after_sha")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let inventory_path = state.inventory_path.clone();
    let last_good = state.last_good_sha.clone();
    let _ = state::mark_dirty(
        state_path,
        state,
        error,
        &before_sha,
        &after_sha,
        dirty_inventory_path.as_deref(),
    );
    summary.insert(
        "state_after".into(),
        json!({
            "dirty": true,
            "last_good_sha": last_good,
            "inventory_path": inventory_path,
            "dirty_inventory_paths": state.dirty_inventory_paths,
            "last_error": error,
            "last_run_before": before_sha,
            "last_run_after": after_sha,
        }),
    );
    true
}

#[allow(clippy::type_complexity)]
fn set_change_source(
    summary: &mut serde_json::Map<String, Value>,
    source: &str,
    count: usize,
) {
    if let Some(map) = summary.get_mut("change_sources").and_then(Value::as_object_mut) {
        map.insert(source.to_string(), json!(count));
    }
}

/// The main-line flow; `Err(DELEGATE_SENTINEL + reason)` requests Python-plane
/// delegation, other messages mirror the Python exception path.
#[allow(clippy::too_many_arguments)]
fn run_flow(
    args: &Args,
    summary: &mut serde_json::Map<String, Value>,
    root: &Path,
    root_str: &str,
    project_id: &str,
    project_name: &str,
    control_cache_dir: &Path,
    scope_id: &str,
    graph_selected: bool,
    graph_context: Option<&GraphContext>,
    summary_path: &str,
    run_id: &str,
    correlation_id: &str,
    lock_acquired: &mut bool,
    state_opt: &mut Option<IncrementalSyncState>,
    state_path: &mut PathBuf,
    current_inventory_out: &mut Option<SourceInventory>,
    parse_quality_manifest_path: &mut Option<String>,
    exit_code: &mut i32,
) -> Result<(), String> {
    let graph_ready = graph_context.is_some();
    if !root.is_dir() {
        return Err(format!("Root not found: {root_str}"));
    }
    let git_available = is_git_repo(root_str);
    if git_available && args.after_sha != "HEAD" {
        let requested_after = normalize_sha(root_str, &args.after_sha).map_err(|e| e.1)?;
        let checkout_head = normalize_sha(root_str, "HEAD").map_err(|e| e.1)?;
        if requested_after != checkout_head {
            return Err(
                "--after-sha must match the checked-out HEAD because analyzers read the current worktree"
                    .to_string(),
            );
        }
    }

    // ── lock ──
    let lock_root = control_cache_dir.join("incremental_sync_locks");
    let lock_path = lock_root.join(format!("{scope_id}.lock"));
    let mut run_lock = ProjectRunLock::new(lock_path.clone(), scope_id);
    let lock_wait_started = Instant::now();
    if let Err(message) =
        run_lock.acquire(&format!("project_id={project_id}"), root, args.lock_timeout_seconds)
    {
        summary.insert("status".into(), json!("lock_busy"));
        summary.insert("outcome".into(), json!("lock_busy"));
        summary.insert(
            "lock".into(),
            json!({
                "path": lock_path.to_string_lossy(),
                "acquired": false,
                "wait_seconds": util::round_digits(lock_wait_started.elapsed().as_secs_f64(), 6),
                "owner": read_lock_metadata(&lock_path),
            }),
        );
        summary.insert("error".into(), json!(message));
        eprintln!("[state] lock busy: {message}");
        *exit_code = 2;
        return Ok(());
    }
    *lock_acquired = true;
    summary.insert(
        "lock".into(),
        json!({
            "path": lock_path.to_string_lossy(),
            "acquired": true,
            "wait_seconds": util::round_digits(lock_wait_started.elapsed().as_secs_f64(), 6),
            "owner": read_lock_metadata(&lock_path),
        }),
    );
    if args.verbose {
        println!("[state] lock acquired: {}", lock_path.to_string_lossy());
    }

    // ── state ──
    let primary_state_path = crate::syncscope::state_file_path(control_cache_dir, project_id, root);
    let legacy_state_path = crate::syncscope::legacy_state_file_path(control_cache_dir, project_id, root);
    let load_path = if !primary_state_path.exists() && legacy_state_path.exists() {
        legacy_state_path
    } else {
        primary_state_path.clone()
    };
    let mut state = state::load_sync_state(&load_path, project_id, root_str);
    if state.migration_required {
        let backup = state::backup_legacy_state(&load_path, &state);
        summary.insert(
            "migration".into(),
            json!({
                "from": state.migrated_from,
                "backup": backup,
                "strategy": "conservative_full_bootstrap",
            }),
        );
    }
    summary.insert(
        "state_before".into(),
        json!({
            "schema_version": state.schema_version,
            "dirty": state.dirty,
            "last_good_sha": state.last_good_sha,
            "snapshot_id": state.snapshot_id,
            "inventory_path": state.inventory_path,
            "dirty_inventory_paths": state.dirty_inventory_paths,
            "migration_required": state.migration_required,
            "last_error": state.last_error,
            "last_run_before": state.last_run_before,
            "last_run_after": state.last_run_after,
            "updated_at": state.updated_at,
        }),
    );
    *state_opt = Some(state.clone());
    *state_path = primary_state_path.clone();

    let mut previous_inventory: Option<SourceInventory> = None;
    if !state.inventory_path.is_empty() {
        match inventory::load_inventory_generation(Path::new(&state.inventory_path)) {
            Ok(loaded) => previous_inventory = Some(loaded),
            Err(error) => push_warning(
                summary,
                json!({"code": "inventory_unavailable", "path": state.inventory_path, "error": error}),
            ),
        }
    }
    let mut dirty_inventories: Vec<SourceInventory> = Vec::new();
    let mut dirty_inventory_errors: Vec<Value> = Vec::new();
    for dirty_path in &state.dirty_inventory_paths {
        match inventory::load_inventory_generation(Path::new(dirty_path)) {
            Ok(loaded) => dirty_inventories.push(loaded),
            Err(error) => dirty_inventory_errors.push(json!({"path": dirty_path, "error": error})),
        }
    }
    if !dirty_inventory_errors.is_empty() {
        push_warning(
            summary,
            json!({"code": "dirty_inventory_unavailable", "artifacts": dirty_inventory_errors}),
        );
    }

    let mut full_scan = args.full_scan;
    let mut recovery_full_scan = false;
    if state.dirty {
        if !state.inventory_path.is_empty() && previous_inventory.is_none() {
            return Err(
                "dirty sync state's last-good inventory is unavailable; refusing recovery without project-scoped storage reconciliation"
                    .to_string(),
            );
        }
        if !dirty_inventory_errors.is_empty() {
            return Err(
                "dirty sync state has incomplete recovery inventory; refusing to clear dirty state without project-scoped storage reconciliation"
                    .to_string(),
            );
        }
        if dirty_inventories.is_empty() {
            if let Some(previous) = previous_inventory.clone() {
                dirty_inventories.push(previous);
                push_warning(
                    summary,
                    json!({
                        "code": "legacy_dirty_recovery_from_last_good_inventory",
                        "path": state.inventory_path,
                    }),
                );
            } else {
                return Err(
                    "dirty sync state has no recovery or last-good inventory; project-scoped storage reconciliation is required"
                        .to_string(),
                );
            }
        }
        recovery_full_scan = true;
        full_scan = true;
        summary.insert("full_scan".into(), json!(true));
        summary.insert("recovery_full_scan".into(), json!(true));
        if args.verbose {
            println!("[recovery] previous run is dirty; replaying all current files and retained deletions");
        }
    }

    if !full_scan
        && (state.migration_required
            || previous_inventory.is_none()
            || (args.before_sha.as_deref().unwrap_or_default().is_empty()
                && state.last_good_sha.is_empty()
                && git_available))
    {
        full_scan = true;
        summary.insert("full_scan".into(), json!(true));
        summary.insert("bootstrap_full_scan".into(), json!(true));
        if args.verbose {
            println!("[bootstrap] no trustworthy content baseline; scanning all source files");
        }
    }

    if args.strict {
        let mut missing: Vec<&str> = Vec::new();
        if graph_selected && graph_context.is_none() {
            missing.push("graph_store");
        }
        if matches!(args.sync_mode.as_str(), "both" | "embedding") && args.qdrant_url.is_none() {
            missing.push("qdrant_url");
        }
        if !missing.is_empty() {
            return Err(format!("strict mode missing required services: {}", missing.join(", ")));
        }
    }

    // ── change detection ──
    let mut effective_detection = args.change_detection.clone();
    let mut root_after_sha = String::new();
    if git_available {
        match normalize_sha(root_str, &args.after_sha) {
            Ok(sha) => root_after_sha = sha,
            Err((code, message)) => {
                if args.after_sha != "HEAD" {
                    return Err(message);
                }
                let _ = code;
                effective_detection = "hash".to_string();
                push_warning(summary, json!({"code": "git_unborn_hash_fallback", "path": root_str}));
            }
        }
        if !root_after_sha.is_empty() {
            let checkout_head = normalize_sha(root_str, "HEAD").map_err(|e| e.1)?;
            if root_after_sha != checkout_head {
                return Err(
                    "--after-sha must match the checked-out HEAD because analyzers read the current worktree"
                        .to_string(),
                );
            }
        }
    }
    if effective_detection == "hybrid" && !git_available {
        effective_detection = "hash".to_string();
        push_warning(summary, json!({"code": "git_unavailable_hash_fallback", "path": root_str}));
    }
    if effective_detection == "committed" && !git_available {
        return Err("--change-detection=committed requires a Git repository".to_string());
    }
    summary.insert("change_detection_effective".into(), json!(effective_detection));

    // ── repository scopes ──
    let mut topology_warnings: Vec<Value> = Vec::new();
    let mut scopes: Vec<gitdiff::RepositoryScope> = Vec::new();
    let mut ignored_prefixes: BTreeSet<String> = BTreeSet::new();
    if git_available {
        let (discovered, discovered_warnings) = gitdiff::discover_repository_scopes(root_str, true);
        if args.submodules == "recursive" {
            scopes = discovered;
            topology_warnings = discovered_warnings;
        } else {
            scopes = discovered.iter().take(1).cloned().collect();
            ignored_prefixes = discovered.iter().skip(1).map(|item| item.source_prefix.clone()).collect();
            for warning in &discovered_warnings {
                if let Some(path) = warning.get("path").and_then(Value::as_str)
                    && !path.is_empty() {
                        ignored_prefixes.insert(path.to_string());
                    }
            }
            summary.insert(
                "submodules_ignored".into(),
                json!(ignored_prefixes.iter().cloned().collect::<Vec<_>>()),
            );
        }
        if let Some(Value::Array(warnings)) = summary.get_mut("coverage_warnings") {
            warnings.extend(topology_warnings.iter().cloned());
        }
        if !topology_warnings.is_empty() && args.strict {
            let mut codes: BTreeSet<String> = BTreeSet::new();
            for warning in &topology_warnings {
                if let Some(code) = warning.get("code").and_then(Value::as_str) {
                    codes.insert(code.to_string());
                }
            }
            return Err(format!(
                "strict mode repository coverage warning: {}",
                codes.into_iter().collect::<Vec<_>>().join(", ")
            ));
        }
    }
    let mut preserved_prefixes: BTreeSet<String> = BTreeSet::new();
    let mut unreadable_prefixes: BTreeSet<String> = BTreeSet::new();
    for warning in &topology_warnings {
        let code = warning.get("code").and_then(Value::as_str).unwrap_or_default();
        let path = warning.get("path").and_then(Value::as_str).unwrap_or_default();
        if code == "submodule_uninitialized" && !path.is_empty() {
            preserved_prefixes.insert(path.to_string());
        }
        if code == "submodule_unreadable" && !path.is_empty() {
            unreadable_prefixes.insert(path.to_string());
        }
    }
    preserved_prefixes.extend(ignored_prefixes.iter().cloned());
    let mut repository_state: BTreeMap<String, BTreeMap<String, Value>> = BTreeMap::new();
    for prefix in preserved_prefixes.iter().chain(unreadable_prefixes.iter()) {
        if let Some(previous) = state.repositories.get(prefix) {
            repository_state.insert(prefix.clone(), previous.clone());
        }
    }

    let summary_rel_path = util::normalize_project_path(root, summary_path);

    // ── git candidates per scope ──
    let mut committed_candidates: BTreeSet<String> = BTreeSet::new();
    let mut worktree_candidates: BTreeSet<String> = BTreeSet::new();
    let mut committed_deleted: BTreeSet<String> = BTreeSet::new();
    let mut worktree_deleted: BTreeSet<String> = BTreeSet::new();
    let mut working_tree_paths: BTreeSet<String> = BTreeSet::new();
    let mut candidates_by_source: BTreeMap<&'static str, BTreeSet<String>> = BTreeMap::from([
        ("committed", BTreeSet::new()),
        ("staged", BTreeSet::new()),
        ("unstaged", BTreeSet::new()),
        ("untracked", BTreeSet::new()),
    ]);
    let mut git_entry_count = 0usize;
    let mut baseline_missing_prefixes: BTreeSet<String> = unreadable_prefixes.clone();
    let mut after_sha = String::new();
    let mut repositories: Vec<Value> = Vec::new();

    let scope_path = |prefix: &str, path: &str| -> String {
        if prefix == "." {
            path.to_string()
        } else {
            format!("{}/{path}", prefix.trim_end_matches('/'))
        }
    };

    for scope in &scopes {
        let current_sha = if scope.source_prefix == "." {
            root_after_sha.clone()
        } else {
            normalize_sha(&scope.root, "HEAD").map_err(|e| e.1)?
        };
        let prior_repo = state.repositories.get(&scope.source_prefix);
        let mut prior_sha = prior_repo
            .and_then(|repo| repo.get("last_good_sha"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if scope.source_prefix == "." {
            if let Some(before) = args.before_sha.as_deref().filter(|s| !s.is_empty()) {
                prior_sha = before.to_string();
            }
            after_sha = current_sha.clone();
        }
        repository_state.insert(
            scope.source_prefix.clone(),
            BTreeMap::from([
                ("root".to_string(), json!(scope.root)),
                ("git_root".to_string(), json!(scope.git_root)),
                ("git_pathspec".to_string(), json!(scope.git_pathspec)),
                ("last_good_sha".to_string(), json!(current_sha)),
            ]),
        );
        repositories.push(json!({
            "source_prefix": scope.source_prefix,
            "root": scope.root,
            "git_root": scope.git_root,
            "git_pathspec": scope.git_pathspec,
            "before_sha": prior_sha,
            "after_sha": current_sha,
        }));

        if !full_scan
            && (effective_detection == "hybrid" || effective_detection == "committed")
            && !prior_sha.is_empty()
        {
            let committed_entries =
                match gitdiff::collect_git_diff_entries(&scope.root, &prior_sha, &current_sha) {
                    Ok(entries) => entries,
                    Err(_) => {
                        push_warning(
                            summary,
                            json!({
                                "code": "repository_baseline_missing",
                                "path": scope.source_prefix,
                                "baseline": prior_sha,
                            }),
                        );
                        if args.strict || effective_detection == "committed" {
                            return Err(format!(
                                "missing repository baseline: {} {}",
                                scope.source_prefix, prior_sha
                            ));
                        }
                        baseline_missing_prefixes.insert(scope.source_prefix.clone());
                        Vec::new()
                    }
                };
            git_entry_count += committed_entries.len();
            let (changed, deleted) = gitdiff::collect_changed_and_deleted(&committed_entries);
            let mapped_changed: BTreeSet<String> =
                changed.iter().map(|item| scope_path(&scope.source_prefix, item)).collect();
            let mapped_deleted: BTreeSet<String> =
                deleted.iter().map(|item| scope_path(&scope.source_prefix, item)).collect();
            committed_candidates.extend(mapped_changed.iter().cloned());
            committed_deleted.extend(mapped_deleted.iter().cloned());
            candidates_by_source
                .get_mut("committed")
                .expect("committed source")
                .extend(mapped_changed.union(&mapped_deleted).cloned());
        }

        let mut work_entries: Vec<gitdiff::DiffEntry> =
            if effective_detection == "hybrid" || effective_detection == "committed" {
                gitdiff::collect_worktree_entries(&scope.root).map_err(|e| e.1)?
            } else {
                Vec::new()
            };
        work_entries.retain(|item| {
            let Some(raw) = item.new_path.clone().or_else(|| item.old_path.clone()) else {
                return false;
            };
            let mapped = scope_path(&scope.source_prefix, &raw);
            walk::is_source_candidate(root, &mapped, summary_rel_path.as_deref())
        });
        if scope.source_prefix == "." && args.submodules == "recursive" {
            let submodule_prefixes: Vec<String> = scopes
                .iter()
                .filter(|item| item.source_prefix != ".")
                .map(|item| format!("{}/", item.source_prefix.trim_end_matches('/')))
                .collect();
            work_entries.retain(|item| {
                let raw = item.new_path.clone().or_else(|| item.old_path.clone()).unwrap_or_default();
                !submodule_prefixes.iter().any(|prefix| raw.starts_with(prefix.as_str()))
            });
        }
        if effective_detection == "committed" && !work_entries.is_empty() {
            return Err(
                "committed change detection requires a clean worktree; use --change-detection=hybrid"
                    .to_string(),
            );
        }
        if effective_detection == "hybrid" {
            git_entry_count += work_entries.len();
            let (changed, deleted) = gitdiff::collect_changed_and_deleted(&work_entries);
            let mapped_changed: BTreeSet<String> =
                changed.iter().map(|item| scope_path(&scope.source_prefix, item)).collect();
            let mapped_deleted: BTreeSet<String> =
                deleted.iter().map(|item| scope_path(&scope.source_prefix, item)).collect();
            for item in &work_entries {
                if let Some(raw) = item.new_path.clone().or_else(|| item.old_path.clone()) {
                    candidates_by_source
                        .get_mut(item.source.as_str())
                        .expect("worktree source")
                        .insert(scope_path(&scope.source_prefix, &raw));
                }
            }
            worktree_candidates.extend(mapped_changed.iter().cloned());
            worktree_deleted.extend(mapped_deleted.iter().cloned());
            working_tree_paths.extend(mapped_changed.union(&mapped_deleted).cloned());
        }
    }
    summary.insert("repositories".into(), json!(repositories));

    if git_available && after_sha.is_empty() {
        after_sha = if root_after_sha.is_empty() { "full_scan".to_string() } else { root_after_sha.clone() };
    }
    if !git_available {
        after_sha = "full_scan".to_string();
    }
    let before_sha = if full_scan {
        "full_scan".to_string()
    } else if let Some(before) = args.before_sha.as_deref().filter(|s| !s.is_empty()) {
        before.to_string()
    } else if !state.last_good_sha.is_empty() {
        state.last_good_sha.clone()
    } else {
        after_sha.clone()
    };

    // ── inventory ──
    let under_preserved = |path: &str| -> bool { under_any_prefix(path, &preserved_prefixes) };
    let mut all_source_paths: BTreeSet<String> = walk::walk_all_source_files(root)
        .iter()
        .filter_map(|path| util::normalize_project_path(root, path))
        .collect();
    all_source_paths.retain(|path| !under_preserved(path));
    if let Some(summary_rel) = &summary_rel_path {
        all_source_paths.remove(summary_rel);
    }
    let mut eligible_paths = all_source_paths.clone();
    if let Some(previous) = &previous_inventory {
        eligible_paths.extend(previous.entries.keys().cloned());
    }
    committed_candidates.retain(|path| eligible_paths.contains(path));
    committed_deleted.retain(|path| eligible_paths.contains(path));
    worktree_candidates.retain(|path| eligible_paths.contains(path));
    worktree_deleted.retain(|path| eligible_paths.contains(path));
    working_tree_paths.retain(|path| eligible_paths.contains(path));
    for (source, paths) in candidates_by_source.iter_mut() {
        paths.retain(|path| eligible_paths.contains(path));
        set_change_source(summary, source, paths.len());
    }
    let raw_prior_worktree_paths: BTreeSet<String> = state.working_tree_paths.iter().cloned().collect();
    for path in &raw_prior_worktree_paths {
        if under_preserved(path) {
            working_tree_paths.insert(path.clone());
        }
    }
    let prior_worktree_paths: BTreeSet<String> =
        raw_prior_worktree_paths.intersection(&eligible_paths).cloned().collect();
    let mut force_hash_paths: BTreeSet<String> = committed_candidates
        .union(&worktree_candidates)
        .cloned()
        .collect();
    force_hash_paths.extend(prior_worktree_paths.iter().cloned());
    if !baseline_missing_prefixes.is_empty() {
        for path in &all_source_paths {
            if baseline_missing_prefixes.iter().any(|prefix| {
                prefix == "." || path == prefix || path.starts_with(format!("{}/", prefix.trim_end_matches('/')).as_str())
            }) {
                force_hash_paths.insert(path.clone());
            }
        }
        if let Some(map) = summary.get_mut("reconciliation").and_then(Value::as_object_mut) {
            map.insert("performed".into(), json!(true));
        }
    }
    if full_scan || effective_detection == "hash" || args.reconcile || state.dirty {
        force_hash_paths = all_source_paths.clone();
        if let Some(map) = summary.get_mut("reconciliation").and_then(Value::as_object_mut) {
            map.insert(
                "performed".into(),
                json!(args.reconcile || state.dirty || effective_detection == "hash"),
            );
        }
    }
    force_hash_paths.retain(|path| !under_preserved(path));

    let current = inventory::capture_source_inventory(
        root,
        &all_source_paths,
        previous_inventory.as_ref(),
        &force_hash_paths,
    )
    .map_err(|error| error.0)?;
    let current =
        inventory::preserve_inventory_prefixes(current, previous_inventory.as_ref(), &preserved_prefixes);
    *current_inventory_out = Some(current.clone());
    let (inventory_changed, inventory_deleted) =
        inventory::diff_source_inventories(previous_inventory.as_ref(), &current);
    let recovery_deleted: BTreeSet<String> = dirty_inventories
        .iter()
        .flat_map(|dirty| dirty.entries.keys().cloned())
        .filter(|path| !current.entries.contains_key(path) && !under_preserved(path))
        .collect();
    set_change_source(
        summary,
        "inventory",
        inventory_changed.len() + inventory_deleted.len(),
    );

    let (changed_paths, deleted_paths): (BTreeSet<String>, BTreeSet<String>);
    if full_scan || recovery_full_scan {
        changed_paths = all_source_paths.clone();
        deleted_paths = if recovery_full_scan {
            inventory_deleted.union(&recovery_deleted).cloned().collect()
        } else {
            BTreeSet::new()
        };
    } else if effective_detection == "hybrid" || effective_detection == "hash" {
        changed_paths = inventory_changed.clone();
        deleted_paths = inventory_deleted.clone();
    } else {
        changed_paths = committed_candidates.clone();
        deleted_paths = committed_deleted.clone();
    }

    let diff_entries_count = if full_scan || recovery_full_scan {
        changed_paths.len() + deleted_paths.len()
    } else {
        git_entry_count
    };
    summary.insert(
        "diff".into(),
        json!({"entries": diff_entries_count, "changed": changed_paths.len(), "deleted": deleted_paths.len()}),
    );
    if args.verbose {
        println!(
            "[diff] mode={} entries={} changed={} deleted={}",
            effective_detection,
            git_entry_count,
            changed_paths.len(),
            deleted_paths.len()
        );
    }
    summary.insert("before_sha".into(), json!(before_sha));
    summary.insert("after_sha".into(), json!(after_sha));

    // ── parser selection ──
    let (parser_filter, parser_auto_mode) = registry::selected_parsers(&args.parsers)?;

    // ── graph setup (Python-plane delegation point for required lanes) ──
    let configured_journal_mode =
        env_lookup("CORTEX_GRAPH_JOURNAL_MODE").unwrap_or_default().trim().to_lowercase();
    let cplus_changed = routing::group_paths_by_parser(
        &changed_paths.union(&deleted_paths).cloned().collect(),
        root,
    )
    .get("cplus")
    .cloned()
    .unwrap_or_default();
    let setup_is_required = journalenv::REQUIRED_MODES.contains(&configured_journal_mode.as_str())
        || (configured_journal_mode.is_empty()
            && parser_filter.contains("cplus")
            && !cplus_changed.is_empty());
    if setup_is_required {
        // Required journal lanes need the SQLite store (resume/quarantine/
        // finalize drain) — Python-plane.
        return Err(format!(
            "{DELEGATE_SENTINEL}required graph journal lane (SQLite store, resume/finalize) is Python-plane"
        ));
    }
    if graph_ready {
        let store = graphops::open_store(graph_context.expect("graph context"))
            .map_err(|error| format!("graph schema/project setup failed before streaming: {error}"))?;
        let mut store = store;
        let setup_mutated = graphops::ensure_project_repository_graph(
            &mut store,
            Some(&graph_context.expect("graph context").neo4j_db),
            project_id,
            project_name,
            &registry::repository_name(project_name, root),
            args.verbose,
        )
        .map_err(|error| format!("graph schema/project setup failed before streaming: {error}"))?;
        if args.verbose {
            if setup_mutated {
                println!("[setup] Project+Repository nodes ensured");
            } else {
                println!("[setup] Project+Repository mutation deferred to required journal");
            }
        }
    }

    // ── no-change early exit ──
    if changed_paths.is_empty() && deleted_paths.is_empty() && !recovery_full_scan {
        inventory::validate_inventory_unchanged(root, &current, &force_hash_paths)
            .map_err(|error| error.0)?;
        let mut unchanged_paths: BTreeSet<String> = walk::walk_all_source_files(root)
            .iter()
            .filter_map(|path| util::normalize_project_path(root, path))
            .collect();
        unchanged_paths.retain(|path| !under_preserved(path));
        if let Some(summary_rel) = &summary_rel_path {
            unchanged_paths.remove(summary_rel);
        }
        let expected_available_paths: BTreeSet<String> = current
            .entries
            .keys()
            .filter(|path| !under_preserved(path))
            .cloned()
            .collect();
        if unchanged_paths != expected_available_paths {
            return Err("source file set changed during no-change verification".to_string());
        }
        let inventory_path =
            inventory::write_inventory_generation(control_cache_dir, &current).map_err(|e| e.to_string())?;
        let last_good = if root_after_sha.is_empty() { state.last_good_sha.clone() } else { after_sha.clone() };
        state::mark_clean(
            &primary_state_path,
            &mut state,
            &last_good,
            &before_sha,
            &after_sha,
            Some(&current.snapshot_id),
            Some(&inventory_path),
            Some(repository_state.clone()),
            Some(working_tree_paths.iter().cloned().collect()),
            1,
        )
        .map_err(|error| error.to_string())?;
        *state_opt = Some(state.clone());
        summary.insert(
            "state_after".into(),
            json!({
                "dirty": false,
                "last_good_sha": state.last_good_sha,
                "snapshot_id": current.snapshot_id,
                "inventory_path": inventory_path,
                "dirty_inventory_paths": [],
                "last_error": "",
                "last_run_before": before_sha,
                "last_run_after": after_sha,
            }),
        );
        summary.insert("status".into(), json!("success"));
        summary.insert(
            "outcome".into(),
            json!(if topology_warnings.is_empty() { "no_changes" } else { "partial_coverage" }),
        );
        println!("[state] no changes detected; state marked clean");
        *exit_code = 0;
        return Ok(());
    }

    // ── impact expansion ──
    let mut impacted_paths: BTreeSet<String> = BTreeSet::new();
    if !full_scan && !recovery_full_scan && graph_ready && !changed_paths.is_empty() {
        if let Some(map) = summary.get_mut("services").and_then(Value::as_object_mut) {
            map.insert("impact_expansion_used".into(), json!(true));
        }
        let mut store = graphops::open_store(graph_context.expect("graph context"))
            .map_err(|error| error.to_string())?;
        let database = graph_context.expect("graph context").neo4j_db.clone();
        impacted_paths = graphops::query_impacted_files(
            &mut store,
            Some(&database),
            project_id,
            &changed_paths,
        )
        .map_err(|error| error.to_string())?;
        impacted_paths = impacted_paths
            .iter()
            .filter_map(|path| util::normalize_project_path(root, path))
            .collect();
    } else if args.verbose {
        println!("[impact] graph store missing; skip graph-based impact expansion");
    }
    summary.insert("impact".into(), json!({"expanded_impacted": impacted_paths.len()}));
    if args.verbose {
        println!("[impact] expanded_impacted={}", impacted_paths.len());
    }

    // ── grouping ──
    let changed_by_parser = routing::group_paths_by_parser(&changed_paths, root);
    let deleted_by_parser = routing::group_paths_by_parser(&deleted_paths, root);
    let impacted_by_parser = routing::group_paths_by_parser(&impacted_paths, root);
    let routing_paths: BTreeSet<String> = changed_paths
        .union(&deleted_paths)
        .cloned()
        .chain(impacted_paths.iter().cloned())
        .collect();
    let framework_routing = frameworks::group_paths_by_framework(&routing_paths, root);
    let mut framework_grouped = framework_routing.grouped;
    let framework_evidence = framework_routing.evidence;
    let topology_changed: BTreeSet<String> = changed_paths
        .union(&impacted_paths).filter(|&path| walk::is_descriptor_path(path)).cloned()
        .collect();
    let topology_deleted: BTreeSet<String> = deleted_paths
        .iter()
        .filter(|path| walk::is_descriptor_path(path))
        .cloned()
        .collect();
    let mut topology_bootstrap_needed = false;
    if !full_scan
        && !recovery_full_scan
        && parser_filter.contains("project_topology")
        && topology_changed.is_empty()
        && topology_deleted.is_empty()
        && graph_ready
    {
        if let Ok(mut store) = graphops::open_store(graph_context.expect("graph context")) {
            let database = graph_context.expect("graph context").neo4j_db.clone();
            match graphops::project_topology_bootstrap_needed(&mut store, Some(&database), project_id) {
                Ok(needed) => topology_bootstrap_needed = needed,
                Err(error) => {
                    if args.verbose {
                        println!("[topology] bootstrap probe failed: {error}");
                    }
                }
            }
        }
        if topology_bootstrap_needed && args.verbose {
            println!("[topology] no ProjectModule facts found; scheduling bootstrap overlay");
        }
    }
    if !parser_auto_mode {
        let candidate_paths: BTreeSet<String> = changed_paths
            .union(&deleted_paths)
            .cloned()
            .chain(impacted_paths.iter().cloned())
            .collect();
        let framework_names: BTreeSet<String> =
            registry::framework_analyzers().keys().map(|k| k.to_string()).collect();
        for framework in parser_filter.intersection(&framework_names) {
            let entry = framework_grouped.entry(framework.clone()).or_default();
            for path in &candidate_paths {
                if frameworks::is_framework_candidate(framework, path) {
                    entry.insert(path.clone());
                }
            }
        }
    }

    // ── artifact roots ──
    let artifact_token = format!(
        "{}_{}_{}",
        &current.snapshot_id[..current.snapshot_id.len().min(12)],
        std::process::id(),
        &util::uuid4_hex()[..8],
    );
    let cache_dir_str = control_cache_dir.to_string_lossy().to_string();
    let manifest_root = safe_cache_root(Some(&cache_dir_str), "incremental_sync_manifests", Some(root))
        .join(scope_id);
    let message_output_dir: String = args.message_output_dir.clone().unwrap_or_else(|| {
        safe_cache_root(Some(&cache_dir_str), "message_scan_artifacts", Some(root))
            .to_string_lossy()
            .to_string()
    });
    let message_qdrant_collection = args
        .message_qdrant_collection
        .clone()
        .unwrap_or_else(|| registry::message_collection_name(project_id));
    let parse_quality_artifact_dir = safe_cache_root(Some(&cache_dir_str), "parse_quality_artifacts", Some(root))
        .join(scope_id)
        .join(&artifact_token);
    let parse_quality_manifest = parse_quality_artifact_dir.join("manifest.json");
    *parse_quality_manifest_path = Some(parse_quality_manifest.to_string_lossy().to_string());
    if let Some(map) = summary.get_mut("parse_quality").and_then(Value::as_object_mut) {
        map.insert("artifact".into(), json!(parse_quality_manifest.to_string_lossy()));
    }
    if let Some(map) = summary.get_mut("services").and_then(Value::as_object_mut) {
        map.insert("message_qdrant_collection".into(), json!(message_qdrant_collection));
    }

    // ── environments ──
    let mut env = build_analyzer_env(args, graph_context);
    env.insert("CORTEX_RUN_ID".to_string(), run_id.to_string());
    env.insert("CORTEX_CORRELATION_ID".to_string(), correlation_id.to_string());
    let graph_env = graph_phase_env(&env);
    let embedding_env = embedding_phase_env(&env);
    let graph_target_args = graph_context
        .map(|context| graphops::graph_target_cli_args(args, context))
        .unwrap_or_default();
    let run_graph_pass = graph_selected && graph_ready;
    let run_embedding_pass = matches!(args.sync_mode.as_str(), "both" | "embedding")
        && (args.qdrant_url.is_some() || (args.sync_mode == "both" && !run_graph_pass));

    summary.insert("phase".into(), json!("parsing"));
    let mut executed_parsers: Vec<String> = Vec::new();
    let mut parser_summaries: Vec<Value> = Vec::new();
    let mut framework_summaries: Vec<Value> = Vec::new();
    let mut topology_summaries: Vec<Value> = Vec::new();
    let mut vector_summaries: Vec<Value> = Vec::new();
    let mut component_failures: Vec<(String, String)> = Vec::new();
    let run_cwd = crate::registry::repo_root().join("code-tiny");

    // ── primary parsers ──
    if run_graph_pass {
        let analyzers = registry::analyzers();
        for parser_name in registry::PARSER_ITERATION_ORDER {
            if !parser_filter.contains(parser_name) {
                continue;
            }
            let Some(base_config) = analyzers.get(parser_name) else { continue };
            let config: AnalyzerConfig = if parser_name == "ts" {
                resolve_ts_analyzer(root)
            } else {
                base_config.clone()
            };
            let parser_changed = changed_by_parser.get(parser_name).cloned().unwrap_or_default();
            let parser_deleted = deleted_by_parser.get(parser_name).cloned().unwrap_or_default();
            let parser_impacted = impacted_by_parser.get(parser_name).cloned().unwrap_or_default();
            let parser_scan: BTreeSet<String> =
                parser_changed.union(&parser_impacted).cloned().collect();
            if parser_scan.is_empty() && parser_deleted.is_empty() {
                continue;
            }
            let changed_manifest = manifest_root.join(format!("{parser_name}_changed_{artifact_token}.json"));
            let deleted_manifest = manifest_root.join(format!("{parser_name}_deleted_{artifact_token}.json"));
            gitdiff::write_manifest_paths(&changed_manifest, &parser_scan).map_err(|e| e.to_string())?;
            gitdiff::write_manifest_paths(&deleted_manifest, &parser_deleted).map_err(|e| e.to_string())?;

            let parser_started = Instant::now();
            let mut parser_info = serde_json::Map::new();
            parser_info.insert("parser".into(), json!(parser_name));
            parser_info.insert("role".into(), json!("primary"));
            parser_info.insert("changed".into(), json!(parser_changed.len()));
            parser_info.insert("impacted".into(), json!(parser_impacted.len()));
            parser_info.insert("scan".into(), json!(parser_scan.len()));
            parser_info.insert("deleted".into(), json!(parser_deleted.len()));
            parser_info.insert("incremental_supported".into(), json!(config.incremental_supported));
            parser_info.insert("status".into(), json!("pending"));
            parser_info.insert("error".into(), json!(""));
            parser_info.insert("started_at".into(), json!(util::now_iso()));
            parser_info.insert("finished_at".into(), Value::Null);
            parser_info.insert("duration_seconds".into(), Value::Null);
            parser_info.insert("changed_manifest".into(), json!(changed_manifest.to_string_lossy()));
            parser_info.insert("deleted_manifest".into(), json!(deleted_manifest.to_string_lossy()));
            parser_info.insert(
                "qdrant_collection".into(),
                json!(registry::code_collection_name(project_id, root, parser_name)),
            );
            parser_info.insert("writes_vectors".into(), json!(config.writes_vectors));
            parser_info.insert("seeded_by".into(), json!(config.seeded_by));
            parser_info.insert(
                "vector_status".into(),
                json!(if run_embedding_pass && config.writes_vectors { "deferred" } else { "disabled" }),
            );
            parser_info.insert("vector_count".into(), json!(0));
            parser_info.insert("message_scan_enabled".into(), json!(false));
            parser_info.insert("ignore_cache".into(), json!(args.ignore_cache));
            parser_info.insert("message_qdrant_collection".into(), json!(""));
            if parser_name == "cplus" && args.parse_quality != "off" {
                let report = parse_quality_artifact_dir.join("cplus.json");
                parser_info.insert("parse_quality_artifact".into(), json!(report.to_string_lossy()));
            }
            parser_summaries.push(Value::Object(parser_info.clone()));
            sync_list(summary, "primary_parsers", &parser_summaries);

            println!(
                "[impact] parser={} changed={} impacted={} scan={} deleted={}",
                parser_name,
                parser_changed.len(),
                parser_impacted.len(),
                parser_scan.len(),
                parser_deleted.len()
            );
            if !config.incremental_supported && !args.allow_full_fallback {
                if parser_auto_mode {
                    println!(
                        "[impact] parser={} skipped (incremental unsupported; use --allow-full-fallback to force full)",
                        parser_name
                    );
                    let last_index = parser_summaries.len() - 1;
                    finalize_list_entry(
                        &mut parser_summaries,
                        last_index,
                        "skipped",
                        parser_started,
                    );
                    sync_list(summary, "primary_parsers", &parser_summaries);
                    continue;
                }
                return Err(format!(
                    "parser '{parser_name}' has no incremental mode yet; rerun with --allow-full-fallback or exclude parser."
                ));
            }
            let run_incrementally = config.incremental_supported && (!full_scan || recovery_full_scan);
            let cmd = if run_incrementally {
                registry::build_analyzer_cmd(
                    &args.python_bin,
                    &config,
                    root_str,
                    project_id,
                    project_name,
                    &before_sha,
                    &after_sha,
                    Some(changed_manifest.to_string_lossy().as_ref()),
                    Some(deleted_manifest.to_string_lossy().as_ref()),
                    None,
                    false,
                    None,
                    None,
                    true,
                    args.verbose,
                    args.ignore_cache,
                    None,
                    None,
                    None,
                    None,
                    &args.parse_quality,
                    None,
                    args.parse_quality_max_files,
                    args.parse_quality_wall_seconds,
                    args.parse_quality_workers,
                    args.parse_quality_max_records,
                    args.parse_quality_max_bytes,
                    &graph_target_args,
                )
            } else {
                let reason = if full_scan { "requested" } else { "fallback" };
                println!("[upsert] parser={parser_name} mode=full reason={reason}");
                registry::build_analyzer_cmd(
                    &args.python_bin,
                    &config,
                    root_str,
                    project_id,
                    project_name,
                    &before_sha,
                    &after_sha,
                    None,
                    None,
                    None,
                    false,
                    None,
                    None,
                    false,
                    args.verbose,
                    args.ignore_cache,
                    None,
                    None,
                    None,
                    None,
                    &args.parse_quality,
                    None,
                    args.parse_quality_max_files,
                    args.parse_quality_wall_seconds,
                    args.parse_quality_workers,
                    args.parse_quality_max_records,
                    args.parse_quality_max_bytes,
                    &graph_target_args,
                )
            };
            let command_vec: Vec<String> =
                std::iter::once(cmd.program.clone()).chain(cmd.args.iter().cloned()).collect();
            {
                let last_index = parser_summaries.len() - 1;
                set_list_field(&mut parser_summaries, last_index, "command", json!(command_vec));
            }
            let mut parser_env = graph_env.clone();
            let lane_mode =
                journalenv::normalize_mode(&journalenv::journal_mode_for_lane(&parser_env, parser_name))?;
            if !graph_disabled_env(&parser_env) {
                let physical_target = journalenv::physical_target_from_env(&parser_env)
                    .map_err(|error| format!("graph schema/project setup failed before streaming: {error}"))?;
                let journal = journalenv::configure_journal_env(
                    &mut parser_env,
                    root,
                    project_id,
                    parser_name,
                    &after_sha,
                    &current.snapshot_id,
                    &physical_target,
                    control_cache_dir,
                    &lane_mode,
                    Some(&artifact_token),
                )?;
                {
                    let last_index = parser_summaries.len() - 1;
                    set_list_field(
                        &mut parser_summaries,
                        last_index,
                        "journal_path",
                        json!(journal.path.to_string_lossy()),
                    );
                }
            }
            sync_list(summary, "primary_parsers", &parser_summaries);
            match run_child(&command_vec, &run_cwd, args.verbose, &parser_env) {
                Ok(_output) => {
                    let entry_index = parser_summaries.len() - 1;
                    read_parse_quality_aggregates(
                        parser_name,
                        &parse_quality_artifact_dir,
                        &args.parse_quality,
                        entry_index,
                        &mut parser_summaries,
                    );
                    set_list_field(&mut parser_summaries, entry_index, "status", json!("success"));
                    executed_parsers.push(parser_name.to_string());
                }
                Err(error) => {
                    let mut info = parser_summaries.pop().expect("entry exists");
                    record_component_failure(
                        summary,
                        &mut component_failures,
                        info.as_object_mut().expect("object entry"),
                        "primary",
                        parser_name,
                        &error,
                        true,
                    );
                    parser_summaries.push(info);
                }
            }
            {
                let last_index = parser_summaries.len() - 1;
                finalize_list_entry(&mut parser_summaries, last_index, "", parser_started);
            }
            sync_list(summary, "primary_parsers", &parser_summaries);
        }
    }

    // ── framework overlays ──
    if run_graph_pass {
        let framework_configs = registry::framework_analyzers();
        let mut ordered: Vec<&str> = framework_configs.keys().copied().collect();
        ordered.sort_by_key(|name| (framework_configs.get(name).expect("framework").order, *name));
        for framework_name in ordered {
            if !parser_filter.contains(framework_name) {
                continue;
            }
            let framework_config = framework_configs.get(framework_name).expect("framework").clone();
            let routed = framework_grouped.get(framework_name).cloned().unwrap_or_default();
            let framework_changed: BTreeSet<String> = routed.intersection(&changed_paths).cloned().collect();
            let framework_deleted: BTreeSet<String> = routed.intersection(&deleted_paths).cloned().collect();
            let framework_impacted: BTreeSet<String> = routed.intersection(&impacted_paths).cloned().collect();
            let framework_scan: BTreeSet<String> =
                framework_changed.union(&framework_impacted).cloned().collect();
            let mut framework_info = serde_json::Map::new();
            framework_info.insert("parser".into(), json!(framework_name));
            framework_info.insert("framework".into(), json!(framework_name));
            framework_info.insert("role".into(), json!("overlay"));
            framework_info.insert("changed".into(), json!(framework_changed.len()));
            framework_info.insert("impacted".into(), json!(framework_impacted.len()));
            framework_info.insert("scan".into(), json!(framework_scan.len()));
            framework_info.insert("deleted".into(), json!(framework_deleted.len()));
            framework_info.insert("incremental_supported".into(), json!(framework_config.incremental_supported));
            framework_info.insert("prerequisite_parsers".into(), json!(framework_config.prerequisite_parsers));
            framework_info.insert("writes_vectors".into(), json!(framework_config.writes_vectors));
            framework_info.insert("qdrant_collection".into(), json!(""));
            framework_info.insert(
                "semantic_seed_collections".into(),
                json!(framework_config
                    .prerequisite_parsers
                    .iter()
                    .map(|parser| registry::code_collection_name(project_id, root, parser))
                    .collect::<Vec<_>>()),
            );
            framework_info.insert("vector_status".into(), json!("disabled"));
            framework_info.insert(
                "detector_evidence".into(),
                json!(framework_evidence.get(framework_name).cloned().unwrap_or_default()),
            );
            framework_info.insert("status".into(), json!("pending"));
            framework_info.insert("error".into(), json!(""));
            framework_info.insert("started_at".into(), json!(util::now_iso()));
            framework_info.insert("finished_at".into(), Value::Null);
            framework_info.insert("duration_seconds".into(), Value::Null);
            framework_summaries.push(Value::Object(framework_info.clone()));
            sync_list(summary, "framework_overlays", &framework_summaries);
            if framework_scan.is_empty() && framework_deleted.is_empty() {
                let index = framework_summaries.len() - 1;
                set_list_field(&mut framework_summaries, index, "status", json!("skipped"));
                set_list_field(
                    &mut framework_summaries,
                    index,
                    "skip_reason",
                    json!("no framework evidence in changed/deleted paths"),
                );
                set_list_field(&mut framework_summaries, index, "finished_at", json!(util::now_iso()));
                set_list_field(&mut framework_summaries, index, "duration_seconds", json!(0.0));
                sync_list(summary, "framework_overlays", &framework_summaries);
                continue;
            }
            let changed_manifest = manifest_root.join(format!("{framework_name}_changed_{artifact_token}.json"));
            let deleted_manifest = manifest_root.join(format!("{framework_name}_deleted_{artifact_token}.json"));
            gitdiff::write_manifest_paths(&changed_manifest, &framework_scan).map_err(|e| e.to_string())?;
            gitdiff::write_manifest_paths(&deleted_manifest, &framework_deleted).map_err(|e| e.to_string())?;
            let index = framework_summaries.len() - 1;
            set_list_field(
                &mut framework_summaries,
                index,
                "changed_manifest",
                json!(changed_manifest.to_string_lossy()),
            );
            set_list_field(
                &mut framework_summaries,
                index,
                "deleted_manifest",
                json!(deleted_manifest.to_string_lossy()),
            );
            println!(
                "[overlay] framework={} changed={} impacted={} scan={} deleted={}",
                framework_name,
                framework_changed.len(),
                framework_impacted.len(),
                framework_scan.len(),
                framework_deleted.len()
            );
            let incremental = !full_scan || recovery_full_scan;
            let changed_manifest_str = changed_manifest.to_string_lossy().to_string();
            let deleted_manifest_str = deleted_manifest.to_string_lossy().to_string();
            // Carry the framework extra_args (`--framework`, `--dialect`,
            // `--mode`) — they are required flags for several overlays and
            // with_script() alone drops them.
            let mut overlay_config =
                AnalyzerConfig::with_script(framework_name, &framework_config.script_path);
            overlay_config.extra_args = framework_config.extra_args.clone();
            let cmd = registry::build_analyzer_cmd(
                &args.python_bin,
                &overlay_config,
                root_str,
                project_id,
                project_name,
                &before_sha,
                &after_sha,
                if incremental { Some(changed_manifest_str.as_str()) } else { None },
                if incremental { Some(deleted_manifest_str.as_str()) } else { None },
                None,
                false,
                None,
                None,
                incremental,
                args.verbose,
                args.ignore_cache,
                None,
                None,
                None,
                None,
                &args.parse_quality,
                None,
                args.parse_quality_max_files,
                args.parse_quality_wall_seconds,
                args.parse_quality_workers,
                args.parse_quality_max_records,
                args.parse_quality_max_bytes,
                &graph_target_args,
            );
            let command_vec: Vec<String> =
                std::iter::once(cmd.program.clone()).chain(cmd.args.iter().cloned()).collect();
            set_list_field(&mut framework_summaries, index, "command", json!(command_vec));
            let mut framework_env = graph_env.clone();
            let lane_mode = journalenv::normalize_mode(&journalenv::journal_mode_for_lane(
                &framework_env,
                framework_name,
            ))?;
            if !graph_disabled_env(&framework_env) {
                let physical_target = journalenv::physical_target_from_env(&framework_env)
                    .map_err(|error| format!("graph schema/project setup failed before streaming: {error}"))?;
                let journal = journalenv::configure_journal_env(
                    &mut framework_env,
                    root,
                    project_id,
                    framework_name,
                    &after_sha,
                    &current.snapshot_id,
                    &physical_target,
                    control_cache_dir,
                    &lane_mode,
                    Some(&artifact_token),
                )?;
                set_list_field(
                    &mut framework_summaries,
                    index,
                    "journal_path",
                    json!(journal.path.to_string_lossy()),
                );
            }
            sync_list(summary, "framework_overlays", &framework_summaries);
            let framework_started = Instant::now();
            match run_child(&command_vec, &run_cwd, args.verbose, &framework_env) {
                Ok(_) => {
                    set_list_field(&mut framework_summaries, index, "status", json!("success"));
                    executed_parsers.push(framework_name.to_string());
                }
                Err(error) => {
                    let mut info = framework_summaries.remove(index);
                    record_component_failure(
                        summary,
                        &mut component_failures,
                        info.as_object_mut().expect("object entry"),
                        "overlay",
                        framework_name,
                        &error,
                        true,
                    );
                    framework_summaries.insert(index, info);
                }
            }
            set_list_field(&mut framework_summaries, index, "finished_at", json!(util::now_iso()));
            set_list_field(
                &mut framework_summaries,
                index,
                "duration_seconds",
                json!(util::round_digits(framework_started.elapsed().as_secs_f64(), 6)),
            );
            sync_list(summary, "framework_overlays", &framework_summaries);
        }
    }

    // ── topology overlay ──
    if run_graph_pass
        && parser_filter.contains("project_topology")
        && (full_scan
            || recovery_full_scan
            || !topology_changed.is_empty()
            || !topology_deleted.is_empty()
            || topology_bootstrap_needed)
    {
        let topology_changed_manifest =
            manifest_root.join(format!("project_topology_changed_{artifact_token}.json"));
        let topology_deleted_manifest =
            manifest_root.join(format!("project_topology_deleted_{artifact_token}.json"));
        gitdiff::write_manifest_paths(&topology_changed_manifest, &topology_changed)
            .map_err(|e| e.to_string())?;
        gitdiff::write_manifest_paths(&topology_deleted_manifest, &topology_deleted)
            .map_err(|e| e.to_string())?;
        let mut topology_info = serde_json::Map::new();
        topology_info.insert("parser".into(), json!("project_topology"));
        topology_info.insert("role".into(), json!("topology_overlay"));
        topology_info.insert("changed".into(), json!(topology_changed.len()));
        topology_info.insert("deleted".into(), json!(topology_deleted.len()));
        topology_info.insert("bootstrap".into(), json!(topology_bootstrap_needed));
        topology_info.insert("incremental_supported".into(), json!(true));
        topology_info.insert("writes_vectors".into(), json!(false));
        topology_info.insert("vector_status".into(), json!("disabled"));
        topology_info.insert("status".into(), json!("pending"));
        topology_info.insert("error".into(), json!(""));
        topology_info.insert("started_at".into(), json!(util::now_iso()));
        topology_info.insert("finished_at".into(), Value::Null);
        topology_info.insert("duration_seconds".into(), Value::Null);
        topology_info.insert("changed_manifest".into(), json!(topology_changed_manifest.to_string_lossy()));
        topology_info.insert("deleted_manifest".into(), json!(topology_deleted_manifest.to_string_lossy()));
        topology_summaries.push(Value::Object(topology_info.clone()));
        sync_list(summary, "topology_overlays", &topology_summaries);
        let topology_started = Instant::now();
        let incremental = !full_scan || recovery_full_scan;
        let changed_manifest_str = topology_changed_manifest.to_string_lossy().to_string();
        let deleted_manifest_str = topology_deleted_manifest.to_string_lossy().to_string();
        let topology_analyzer = registry::project_topology_analyzer();
        let cmd = registry::build_analyzer_cmd(
            &args.python_bin,
            &topology_analyzer,
            root_str,
            project_id,
            project_name,
            &before_sha,
            &after_sha,
            if incremental { Some(changed_manifest_str.as_str()) } else { None },
            if incremental { Some(deleted_manifest_str.as_str()) } else { None },
            None,
            false,
            None,
            None,
            incremental,
            args.verbose,
            args.ignore_cache,
            None,
            None,
            None,
            None,
            &args.parse_quality,
            None,
            args.parse_quality_max_files,
            args.parse_quality_wall_seconds,
            args.parse_quality_workers,
            args.parse_quality_max_records,
            args.parse_quality_max_bytes,
            &graph_target_args,
        );
        let command_vec: Vec<String> =
            std::iter::once(cmd.program.clone()).chain(cmd.args.iter().cloned()).collect();
        set_list_field(&mut topology_summaries, 0, "command", json!(command_vec));
        let mut topology_env = graph_env.clone();
        if !graph_disabled_env(&topology_env) {
            let lane_mode = journalenv::normalize_mode(&journalenv::journal_mode_for_lane(
                &topology_env,
                "project_topology",
            ))?;
            let physical_target = journalenv::physical_target_from_env(&topology_env)
                .map_err(|error| format!("graph schema/project setup failed before streaming: {error}"))?;
            let journal = journalenv::configure_journal_env(
                &mut topology_env,
                root,
                project_id,
                "project_topology",
                &after_sha,
                &current.snapshot_id,
                &physical_target,
                control_cache_dir,
                &lane_mode,
                Some(&artifact_token),
            )?;
            set_list_field(
                &mut topology_summaries,
                0,
                "journal_path",
                json!(journal.path.to_string_lossy()),
            );
        }
        let topology_mode = if recovery_full_scan {
            "recovery"
        } else if full_scan {
            "full"
        } else if topology_bootstrap_needed {
            "bootstrap"
        } else {
            "incremental"
        };
        println!(
            "[overlay] project_topology changed={} deleted={} mode={}",
            topology_changed.len(),
            topology_deleted.len(),
            topology_mode
        );
        sync_list(summary, "topology_overlays", &topology_summaries);
        match run_child(&command_vec, &run_cwd, args.verbose, &topology_env) {
            Ok(_) => {
                set_list_field(&mut topology_summaries, 0, "status", json!("success"));
                executed_parsers.push("project_topology".to_string());
            }
            Err(error) => {
                let mut info = topology_summaries.remove(0);
                record_component_failure(
                    summary,
                    &mut component_failures,
                    info.as_object_mut().expect("object entry"),
                    "topology",
                    "project_topology",
                    &error,
                    true,
                );
                topology_summaries.insert(0, info);
            }
        }
        set_list_field(&mut topology_summaries, 0, "finished_at", json!(util::now_iso()));
        set_list_field(
            &mut topology_summaries,
            0,
            "duration_seconds",
            json!(util::round_digits(topology_started.elapsed().as_secs_f64(), 6)),
        );
        sync_list(summary, "topology_overlays", &topology_summaries);
    }

    // ── embedding pass ──
    if run_embedding_pass {
        println!("[embedding] starting graph-disabled primary analyzer pass");
        let analyzers = registry::analyzers();
        for parser_name in registry::PARSER_ITERATION_ORDER {
            let Some(config) = analyzers.get(parser_name) else { continue };
            if !parser_filter.contains(parser_name) || !config.writes_vectors {
                continue;
            }
            let mut config = if parser_name == "ts" { resolve_ts_analyzer(root) } else { config.clone() };
            // Vector + message lanes stay on Python children until the native
            // planes land (plan phases 05–06) — Rust children do not embed or
            // message-scan yet, so the flip must not select them here.
            config.force_python = true;
            let parser_changed = changed_by_parser.get(parser_name).cloned().unwrap_or_default();
            let parser_deleted = deleted_by_parser.get(parser_name).cloned().unwrap_or_default();
            let parser_impacted = impacted_by_parser.get(parser_name).cloned().unwrap_or_default();
            let parser_scan: BTreeSet<String> = parser_changed.union(&parser_impacted).cloned().collect();
            if parser_scan.is_empty() && parser_deleted.is_empty() {
                continue;
            }
            let changed_manifest =
                manifest_root.join(format!("{parser_name}_embedding_changed_{artifact_token}.json"));
            let deleted_manifest =
                manifest_root.join(format!("{parser_name}_embedding_deleted_{artifact_token}.json"));
            gitdiff::write_manifest_paths(&changed_manifest, &parser_scan).map_err(|e| e.to_string())?;
            gitdiff::write_manifest_paths(&deleted_manifest, &parser_deleted).map_err(|e| e.to_string())?;
            let collection = registry::code_collection_name(project_id, root, parser_name);
            let mut vector_info = serde_json::Map::new();
            vector_info.insert("parser".into(), json!(parser_name));
            vector_info.insert("role".into(), json!("embedding"));
            vector_info.insert("changed".into(), json!(parser_changed.len()));
            vector_info.insert("impacted".into(), json!(parser_impacted.len()));
            vector_info.insert("scan".into(), json!(parser_scan.len()));
            vector_info.insert("deleted".into(), json!(parser_deleted.len()));
            vector_info.insert("incremental_supported".into(), json!(config.incremental_supported));
            vector_info.insert("status".into(), json!("pending"));
            vector_info.insert("error".into(), json!(""));
            vector_info.insert("started_at".into(), json!(util::now_iso()));
            vector_info.insert("finished_at".into(), Value::Null);
            vector_info.insert("duration_seconds".into(), Value::Null);
            vector_info.insert("changed_manifest".into(), json!(changed_manifest.to_string_lossy()));
            vector_info.insert("deleted_manifest".into(), json!(deleted_manifest.to_string_lossy()));
            vector_info.insert("qdrant_collection".into(), json!(collection));
            vector_info.insert("writes_vectors".into(), json!(true));
            vector_info.insert(
                "vector_status".into(),
                json!(if args.qdrant_url.is_some() { "pending" } else { "disabled" }),
            );
            vector_info.insert(
                "vector_count".into(),
                json!(if args.qdrant_url.is_some() { Value::Null } else { json!(0) }),
            );
            vector_info.insert("graph_status".into(), json!("disabled"));
            let message_enabled =
                args.sync_messages && registry::message_enabled_parsers().contains(parser_name);
            vector_info.insert("message_scan_enabled".into(), json!(message_enabled));
            vector_info.insert("ignore_cache".into(), json!(args.ignore_cache));
            vector_info.insert(
                "message_qdrant_collection".into(),
                json!(if message_enabled { message_qdrant_collection.clone() } else { String::new() }),
            );
            vector_summaries.push(Value::Object(vector_info.clone()));
            sync_list(summary, "vector_embeddings", &vector_summaries);
            let index = vector_summaries.len() - 1;
            let vector_started = Instant::now();
            if !config.incremental_supported && !args.allow_full_fallback {
                if parser_auto_mode {
                    set_list_field(&mut vector_summaries, index, "status", json!("skipped"));
                    set_list_field(&mut vector_summaries, index, "vector_status", json!("skipped"));
                    set_list_field(&mut vector_summaries, index, "finished_at", json!(util::now_iso()));
                    set_list_field(
                        &mut vector_summaries,
                        index,
                        "duration_seconds",
                        json!(util::round_digits(vector_started.elapsed().as_secs_f64(), 6)),
                    );
                    sync_list(summary, "vector_embeddings", &vector_summaries);
                    continue;
                }
                return Err(format!(
                    "parser '{parser_name}' has no incremental mode yet; rerun with --allow-full-fallback or exclude parser."
                ));
            }
            let run_incrementally = config.incremental_supported && (!full_scan || recovery_full_scan);
            let changed_manifest_str = changed_manifest.to_string_lossy().to_string();
            let deleted_manifest_str = deleted_manifest.to_string_lossy().to_string();
            let cmd = registry::build_analyzer_cmd(
                &args.python_bin,
                &config,
                root_str,
                project_id,
                project_name,
                &before_sha,
                &after_sha,
                if run_incrementally { Some(changed_manifest_str.as_str()) } else { None },
                if run_incrementally { Some(deleted_manifest_str.as_str()) } else { None },
                if args.qdrant_url.is_some() { Some(collection.as_str()) } else { None },
                message_enabled,
                if message_enabled { Some(message_output_dir.as_str()) } else { None },
                if message_enabled { Some(message_qdrant_collection.as_str()) } else { None },
                run_incrementally,
                args.verbose,
                args.ignore_cache,
                args.embed_model.as_deref(),
                Some(args.embed_device.as_str()),
                Some(args.embed_batch_size),
                Some(args.max_embed_chars),
                "off",
                None,
                args.parse_quality_max_files,
                args.parse_quality_wall_seconds,
                args.parse_quality_workers,
                args.parse_quality_max_records,
                args.parse_quality_max_bytes,
                &graph_target_args,
            );
            let command_vec: Vec<String> =
                std::iter::once(cmd.program.clone()).chain(cmd.args.iter().cloned()).collect();
            set_list_field(&mut vector_summaries, index, "command", json!(command_vec));
            sync_list(summary, "vector_embeddings", &vector_summaries);
            match run_child(&command_vec, &run_cwd, args.verbose, &embedding_env) {
                Ok(output) => {
                    set_list_field(&mut vector_summaries, index, "status", json!("success"));
                    if args.qdrant_url.is_some() {
                        set_list_field(&mut vector_summaries, index, "vector_status", json!("success"));
                    }
                    if let Some(count) = scan_result_vector_count(&output) {
                        set_list_field(&mut vector_summaries, index, "vector_count", json!(count));
                    }
                    propagate_vector_status(&mut parser_summaries, parser_name, &vector_summaries[index]);
                    sync_list(summary, "primary_parsers", &parser_summaries);
                    executed_parsers.push(format!("{parser_name}:embedding"));
                }
                Err(error) => {
                    set_list_field(&mut vector_summaries, index, "vector_status", json!("failed"));
                    propagate_vector_status(&mut parser_summaries, parser_name, &vector_summaries[index]);
                    sync_list(summary, "primary_parsers", &parser_summaries);
                    let mut info = vector_summaries.remove(index);
                    record_component_failure(
                        summary,
                        &mut component_failures,
                        info.as_object_mut().expect("object entry"),
                        "embedding",
                        parser_name,
                        &error,
                        true,
                    );
                    vector_summaries.insert(index, info);
                }
            }
            set_list_field(&mut vector_summaries, index, "finished_at", json!(util::now_iso()));
            set_list_field(
                &mut vector_summaries,
                index,
                "duration_seconds",
                json!(util::round_digits(vector_started.elapsed().as_secs_f64(), 6)),
            );
            sync_list(summary, "vector_embeddings", &vector_summaries);
        }
    }

    // ── native message-scan lane (phase-05) ──
    // Runs the Rust port of `tools.common.message_scan` cross-parser plane,
    // independent of whether primary/embedding pass selected Python or Rust
    // children. This is the lane that allows flipping default to Rust
    // without losing message-scan capability (the embedding pass still
    // delegates message-scan to Python children for backwards compatibility
    // — phase-08 retired-error closes that gap).
    if args.sync_messages
        && !args.no_graph
        && run_graph_pass
    {
        run_native_message_scan_lane(
            args,
            root_str,
            project_id,
            project_name,
            &before_sha,
            &after_sha,
            &changed_by_parser,
            &deleted_by_parser,
            &message_output_dir,
            &message_qdrant_collection,
            summary,
        )?;
    }

    // ── final parser aggregation ──
    if args.sync_mode == "both" && !run_graph_pass {
        summary.insert("primary_parsers".into(), Value::Array(vector_summaries.clone()));
        parser_summaries = vector_summaries.clone();
    }
    let _ = (&framework_summaries, &topology_summaries, &vector_summaries);
    {
        let all_parsers: Vec<Value> = parser_summaries
            .iter()
            .chain(framework_summaries.iter())
            .chain(topology_summaries.iter())
            .chain(vector_summaries.iter())
            .cloned()
            .collect();
        summary.insert("parsers".into(), json!(all_parsers));
    }

    if !component_failures.is_empty() {
        let (_, message) = component_failures[0].clone();
        return Err(message);
    }

    // ── verifying generation ──
    summary.insert("phase".into(), json!("verifying_generation"));
    let verification_paths: BTreeSet<String> = if full_scan
        || recovery_full_scan
        || effective_detection == "hash"
        || args.reconcile
    {
        current.entries.keys().filter(|path| !under_preserved(path)).cloned().collect()
    } else {
        changed_paths
            .union(&impacted_paths).filter(|&path| !under_preserved(path)).cloned()
            .collect()
    };
    inventory::validate_inventory_unchanged(root, &current, &verification_paths)
        .map_err(|error| error.0)?;
    let mut post_source_paths: BTreeSet<String> = walk::walk_all_source_files(root)
        .iter()
        .filter_map(|path| util::normalize_project_path(root, path))
        .collect();
    post_source_paths.retain(|path| !under_preserved(path));
    if let Some(summary_rel) = &summary_rel_path {
        post_source_paths.remove(summary_rel);
    }
    let expected_available_paths: BTreeSet<String> = current
        .entries
        .keys()
        .filter(|path| !under_preserved(path))
        .cloned()
        .collect();
    if post_source_paths != expected_available_paths {
        return Err("source file set changed during scan".to_string());
    }

    // ── publishing ──
    summary.insert("phase".into(), json!("publishing"));
    if args.sync_mode == "both" {
        let inventory_path =
            inventory::write_inventory_generation(control_cache_dir, &current).map_err(|e| e.to_string())?;
        let last_good = if root_after_sha.is_empty() { state.last_good_sha.clone() } else { after_sha.clone() };
        state::mark_clean(
            &primary_state_path,
            &mut state,
            &last_good,
            &before_sha,
            &after_sha,
            Some(&current.snapshot_id),
            Some(&inventory_path),
            Some(repository_state.clone()),
            Some(working_tree_paths.iter().cloned().collect()),
            1,
        )
        .map_err(|error| error.to_string())?;
        *state_opt = Some(state.clone());
        summary.insert(
            "state_after".into(),
            json!({
                "dirty": false,
                "last_good_sha": state.last_good_sha,
                "snapshot_id": current.snapshot_id,
                "inventory_path": inventory_path,
                "dirty_inventory_paths": [],
                "last_error": "",
                "last_run_before": before_sha,
                "last_run_after": after_sha,
                "baseline_advanced": true,
            }),
        );
    } else {
        summary.insert(
            "state_after".into(),
            json!({
                "dirty": state.dirty,
                "last_good_sha": state.last_good_sha,
                "snapshot_id": state.snapshot_id,
                "inventory_path": state.inventory_path,
                "dirty_inventory_paths": state.dirty_inventory_paths,
                "last_error": state.last_error,
                "last_run_before": state.last_run_before,
                "last_run_after": state.last_run_after,
                "baseline_advanced": false,
            }),
        );
        println!(
            "[state] {}-only full scan completed; shared incremental baseline preserved",
            args.sync_mode
        );
    }
    summary.insert("status".into(), json!("success"));
    summary.insert(
        "outcome".into(),
        json!(if topology_warnings.is_empty() { "scanned" } else { "partial_coverage" }),
    );
    println!(
        "[state] summary changed={} deleted={} impacted={} parsers={}",
        changed_paths.len(),
        deleted_paths.len(),
        impacted_paths.len(),
        executed_parsers.len()
    );
    println!("[state] incremental sync completed successfully");
    *exit_code = 0;
    Ok(())
}

// ── list/summary helpers ──────────────────────────────────────────────────

fn sync_list(summary: &mut serde_json::Map<String, Value>, key: &str, list: &[Value]) {
    summary.insert(key.to_string(), Value::Array(list.to_vec()));
}

fn set_list_field(list: &mut [Value], index: usize, field: &str, value: Value) {
    if let Some(entry) = list.get_mut(index)
        && let Some(map) = entry.as_object_mut() {
            map.insert(field.to_string(), value);
        }
}

fn finalize_list_entry(list: &mut [Value], index: usize, status: &str, started: Instant) {
    if !status.is_empty() {
        set_list_field(list, index, "status", json!(status));
    }
    set_list_field(list, index, "finished_at", json!(util::now_iso()));
    set_list_field(
        list,
        index,
        "duration_seconds",
        json!(util::round_digits(started.elapsed().as_secs_f64(), 6)),
    );
}

fn propagate_vector_status(
    parser_summaries: &mut [Value],
    parser: &str,
    vector_info: &Value,
) {
    let status = vector_info.get("vector_status").cloned().unwrap_or(json!(""));
    let count = vector_info.get("vector_count").cloned().unwrap_or(json!(0));
    for entry in parser_summaries.iter_mut() {
        if entry.get("parser").and_then(Value::as_str) == Some(parser)
            && let Some(map) = entry.as_object_mut() {
                map.insert("vector_status".into(), status.clone());
                map.insert("vector_count".into(), count.clone());
            }
    }
}

fn record_component_failure(
    summary: &mut serde_json::Map<String, Value>,
    component_failures: &mut Vec<(String, String)>,
    info: &mut serde_json::Map<String, Value>,
    role: &str,
    name: &str,
    error: &ChildError,
    continued: bool,
) {
    let message = error.to_string();
    info.insert("status".into(), json!("failed"));
    info.insert("error".into(), json!(message));
    info.insert("failure_class".into(), json!("parser_isolation"));
    info.insert("failure_code".into(), json!("analyzer_child_failed"));
    info.insert("failure_artifacts".into(), json!([]));
    if let Some(Value::Array(failures)) = summary.get_mut("component_failures") {
        failures.push(json!({
            "component": format!("{role}:{name}"),
            "role": role,
            "name": name,
            "error": message,
            "exception_type": "CalledProcessError",
            "failure_class": "parser_isolation",
            "failure_code": "analyzer_child_failed",
            "retryable": false,
            "safe_action": "inspect the child debug artifact and quarantine or correct the failing input",
            "continued": continued,
            "details": {"exception_type": "CalledProcessError"},
            "artifacts": [],
        }));
    }
    component_failures.push((format!("{role}:{name}"), message));
    eprintln!(
        "[continue] component={role}:{name} failed: {error}; continuing remaining components"
    );
}

fn read_parse_quality_aggregates(
    parser: &str,
    artifact_dir: &Path,
    policy: &str,
    entry_index: usize,
    parser_summaries: &mut [Value],
) {
    if parser != "cplus" || policy == "off" {
        return;
    }
    let report_path = artifact_dir.join("cplus.json");
    if !report_path.is_file() {
        return;
    }
    match std::fs::read_to_string(&report_path)
        .map_err(|e| e.to_string())
        .and_then(|text| serde_json::from_str::<Value>(&text).map_err(|e| e.to_string()))
    {
        Ok(payload) => {
            let aggregates = payload.get("aggregates").cloned().unwrap_or_else(|| json!({}));
            set_list_field(
                parser_summaries,
                entry_index,
                "parse_quality_aggregates",
                aggregates.clone(),
            );
        }
        Err(error) => {
            set_list_field(parser_summaries, entry_index, "parse_quality_error", json!(error));
        }
    }
}

// ── small utilities ───────────────────────────────────────────────────────

fn env_lookup(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.trim().is_empty())
}

fn is_git_repo(root: &str) -> bool {
    std::process::Command::new("git")
        .args(["-C", root, "rev-parse", "--is-inside-work-tree"])
        .stderr(std::process::Stdio::null())
        .output()
        .map(|output| {
            output.status.success()
                && String::from_utf8_lossy(&output.stdout).trim().eq_ignore_ascii_case("true")
        })
        .unwrap_or(false)
}

fn normalize_sha(root: &str, reference: &str) -> Result<String, (i32, String)> {
    let output = std::process::Command::new("git")
        .args(["-C", root, "rev-parse", reference])
        .stderr(std::process::Stdio::null())
        .output()
        .map_err(|error| (-1, error.to_string()))?;
    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        Err((output.status.code().unwrap_or(128), "git rev-parse failed".to_string()))
    }
}

fn under_any_prefix(path: &str, prefixes: &BTreeSet<String>) -> bool {
    prefixes
        .iter()
        .any(|prefix| path == prefix || path.starts_with(format!("{}/", prefix.trim_end_matches('/')).as_str()))
}

fn push_warning(summary: &mut serde_json::Map<String, Value>, warning: Value) {
    if let Some(Value::Array(warnings)) = summary.get_mut("coverage_warnings") {
        warnings.push(warning);
    }
}

fn graph_disabled_env(env: &BTreeMap<String, String>) -> bool {
    matches!(
        env.get("CORTEX_DISABLE_GRAPH").cloned().unwrap_or_default().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// `_resolve_ts_analyzer` — print the detection line and pick the script.
fn resolve_ts_analyzer(root: &Path) -> AnalyzerConfig {
    let result = tsdetect::detect_project_type(root);
    let script = if result.project_type == "backend" || result.project_type == "fullstack" {
        crate::registry::repo_root().join("code-tiny/tools/ts/ts_backend_analyzer.py")
    } else {
        crate::registry::repo_root().join("code-tiny/tools/ts/ts_analyzer.py")
    };
    println!(
        "[ts-detect] project_type={} framework={} -> {}",
        result.project_type,
        result.framework,
        script.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
    );
    AnalyzerConfig::with_script("ts", &script.to_string_lossy())
}

/// `_scan_result_vector_count`.
fn scan_result_vector_count(output: &str) -> Option<i64> {
    let re = regex::Regex::new(r"(?m)^\[SCAN_RESULT\].*\bvectors=(\d+)\b").ok()?;
    re.captures(output)?.get(1)?.as_str().parse().ok()
}

/// `_build_analyzer_env`.
fn build_analyzer_env(
    args: &Args,
    graph_context: Option<&GraphContext>,
) -> BTreeMap<String, String> {
    let mut env: BTreeMap<String, String> = std::env::vars().collect();
    if args.no_graph {
        env.insert("CORTEX_DISABLE_GRAPH".to_string(), "1".to_string());
        env.insert("CODE_GRAPH_PROVIDER".to_string(), "neo4j".to_string());
        env.insert("GRAPH_PROVIDER".to_string(), "neo4j".to_string());
        for key in [
            "FALKORDB_PATH",
            "FALKORDB_URI",
            "FALKORDB_PASSWORD",
            "FALKORDB_SSL",
            "FALKORDB_GRAPH",
            "FALKORDB_DATABASE",
            "NEO4J_URI",
            "NEO4J_USER",
            "NEO4J_PASS",
            "NEO4J_DB",
        ] {
            env.remove(key);
        }
        return env;
    }
    env.insert("CODE_GRAPH_PROVIDER".to_string(), args.graph_provider.clone());
    env.insert("GRAPH_PROVIDER".to_string(), args.graph_provider.clone());
    if let Some(uri) = &args.neo4j_uri {
        env.insert("NEO4J_URI".to_string(), uri.clone());
    }
    if let Some(user) = &args.neo4j_user {
        env.insert("NEO4J_USER".to_string(), user.clone());
    }
    if let Some(password) = &args.neo4j_password {
        env.insert("NEO4J_PASS".to_string(), password.clone());
    }
    if let Some(db) = &args.neo4j_db {
        env.insert("NEO4J_DB".to_string(), db.clone());
    }
    let explicit_target = args.explicit_falkordb_target;
    let mut falkordb_uri = String::new();
    if explicit_target != Some("path") {
        falkordb_uri = args
            .falkordb_uri
            .clone()
            .or_else(|| env_lookup("FALKORDB_URI"))
            .unwrap_or_default()
            .trim()
            .to_string();
    }
    if !falkordb_uri.is_empty() {
        env.insert("FALKORDB_URI".to_string(), falkordb_uri);
        env.remove("FALKORDB_PATH");
        let password = args
            .falkordb_password
            .clone()
            .or_else(|| env_lookup("FALKORDB_PASSWORD"));
        if let Some(password) = password {
            env.insert("FALKORDB_PASSWORD".to_string(), password);
        }
        if args.falkordb_ssl {
            env.insert("FALKORDB_SSL".to_string(), "1".to_string());
        }
    } else if let Some(path) = &args.falkordb_path {
        env.insert("FALKORDB_PATH".to_string(), path.clone());
        env.remove("FALKORDB_URI");
        env.remove("FALKORDB_PASSWORD");
        env.remove("FALKORDB_SSL");
    }
    if let Some(graph) = graph_context.map(|context| context.falkordb_graph.clone()) {
        if !graph.is_empty() {
            env.insert("FALKORDB_GRAPH".to_string(), graph);
        }
    } else if let Some(graph) = &args.falkordb_graph
        && !graph.is_empty() {
            env.insert("FALKORDB_GRAPH".to_string(), graph.clone());
        }
    if let Some(url) = &args.qdrant_url {
        env.insert("QDRANT_CODE_PATH".to_string(), url.clone());
    }
    if let Some(cache_dir) = &args.cache_dir {
        env.insert("QDRANT_CACHE_DIR".to_string(), cache_dir.clone());
    }
    if let Some(model) = &args.embed_model {
        env.insert("CODE_EMBEDDING_MODEL".to_string(), model.clone());
        env.insert("EMBED_MODEL".to_string(), model.clone());
    }
    env.insert("EMBED_DEVICE".to_string(), args.embed_device.clone());
    env.insert("EMBED_BATCH_SIZE".to_string(), args.embed_batch_size.to_string());
    env.insert("MAX_EMBED_CHARS".to_string(), args.max_embed_chars.to_string());
    env
}

/// `_graph_phase_env` — child env that cannot open the vector store.
fn graph_phase_env(base_env: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let mut env = base_env.clone();
    for key in [
        "QDRANT_URL",
        "QDRANT_PATH",
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "QDRANT_COLLECTION_CODE",
    ] {
        env.insert(key.to_string(), String::new());
    }
    env
}

/// `_embedding_phase_env` — child env that cannot touch graph storage.
fn embedding_phase_env(base_env: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let mut env = base_env.clone();
    env.insert("CORTEX_DISABLE_GRAPH".to_string(), "1".to_string());
    env.insert("CODE_GRAPH_PROVIDER".to_string(), "neo4j".to_string());
    env.insert("GRAPH_PROVIDER".to_string(), "neo4j".to_string());
    for key in [
        "FALKORDB_PATH",
        "FALKORDB_URI",
        "FALKORDB_PASSWORD",
        "FALKORDB_SSL",
        "FALKORDB_GRAPH",
        "FALKORDB_DATABASE",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
    ] {
        env.remove(key);
    }
    env
}

/// `_write_summary` — atomic JSON write with mode 0600.
pub fn write_summary(target: &Path, payload: &Value) {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let encoded = format!("{}\n", serde_json::to_string_pretty(payload).unwrap());
    let temporary = target.with_file_name(format!(
        ".{}.{}.{}.tmp",
        target
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or_default(),
    ));
    if std::fs::write(&temporary, encoded.as_bytes()).is_ok() {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o600));
        }
        if std::fs::rename(&temporary, target).is_err() {
            let _ = std::fs::remove_file(&temporary);
        }
    } else {
        let _ = std::fs::remove_file(&temporary);
    }
}

/// Delegation: run the Python orchestrator with identical args.
pub fn delegate_to_python(reason: &str) -> i32 {
    println!("[cortex-sync] python-plane delegation: {reason}");
    let _ = std::io::stdout().flush();
    let repo_root = crate::registry::repo_root();
    let script = repo_root.join("code-tiny/tools/sync/incremental_sync.py");
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let python_bin = crate::cli::resolve_python_bin(&raw);
    match std::process::Command::new(python_bin)
        .arg(script)
        .args(&raw)
        .status()
    {
        Ok(status) => status.code().unwrap_or(1),
        Err(error) => {
            eprintln!("[cortex-sync] python delegation failed: {error}");
            1
        }
    }
}

// ── phase-05 native message-scan lane ──────────────────────────────────────
//
// Independent cross-parser lane that scans source files via the Rust port of
// `tools.common.message_scan`. Activated when `--sync-messages` is true
// (default) and at least the graph pass ran.
//
// Today the lane only emits the per-parser artifact JSON
// (`<output_dir>/<project>/<parser>_messages.json`). Graph upsert and
// Qdrant vector lane are owned by phase-06 (cortex-embed component gates);
// the Python children continue to write the graph MessageEndpoint nodes /
// message vectors during the embedding pass until phase-08 retired-errors
// the value.
#[allow(clippy::too_many_arguments)]
fn run_native_message_scan_lane(
    args: &crate::cli::Args,
    root: &str,
    project_id: &str,
    project_name: &str,
    before_sha: &str,
    after_sha: &str,
    changed_by_parser: &BTreeMap<String, BTreeSet<String>>,
    deleted_by_parser: &BTreeMap<String, BTreeSet<String>>,
    message_output_dir: &str,
    message_qdrant_collection: &str,
    summary: &mut serde_json::Map<String, Value>,
) -> Result<(), String> {
    use crate::message_scan;

    let root_path = std::path::Path::new(root);
    let output_dir = std::path::Path::new(message_output_dir);
    if let Err(e) = std::fs::create_dir_all(output_dir) {
        return Err(format!("message-scan output dir: {e}"));
    }
    let mut lane_summaries: Vec<Value> = Vec::new();
    let mut total_messages = 0u64;
    let mut lane_failures: Vec<(String, String)> = Vec::new();
    let enabled_parsers = crate::registry::message_enabled_parsers();

    let run_incrementally = !args.full_scan;
    for parser in enabled_parsers {
        let changed = changed_by_parser
            .get(parser)
            .cloned()
            .unwrap_or_default();
        let deleted = deleted_by_parser
            .get(parser)
            .cloned()
            .unwrap_or_default();
        let mut target_files: Vec<String> = changed.iter().cloned().collect();
        target_files.extend(deleted.iter().cloned());
        if run_incrementally && target_files.is_empty() {
            // No changed files in incremental — skip (mirrors Python behaviour).
            continue;
        }
        let target_ref = if run_incrementally {
            Some(target_files.as_slice())
        } else {
            None
        };
        let started = Instant::now();
        let collect_result = message_scan::collect_messages_for_parser(
            root_path,
            parser,
            project_id,
            parser,
            target_ref,
        );
        let records = match collect_result {
            Ok(records) => records,
            Err(error) => {
                lane_failures.push((parser.to_string(), error.clone()));
                let mut info = serde_json::Map::new();
                info.insert("parser".into(), json!(parser));
                info.insert("status".into(), json!("failed"));
                info.insert("error".into(), json!(error));
                info.insert(
                    "duration_seconds".into(),
                    json!(util::round_digits(started.elapsed().as_secs_f64(), 6)),
                );
                lane_summaries.push(Value::Object(info));
                continue;
            }
        };
        let artifact_result = message_scan::write_message_artifact(
            root_path,
            parser,
            project_id,
            project_name,
            &records,
            Some(output_dir),
            None,
            before_sha,
            after_sha,
        );
        let mut info = serde_json::Map::new();
        info.insert("parser".into(), json!(parser));
        info.insert("message_count".into(), json!(records.len()));
        info.insert(
            "qdrant_collection".into(),
            json!(message_qdrant_collection),
        );
        match artifact_result {
            Ok(path) => {
                info.insert("status".into(), json!("success"));
                info.insert("artifact_path".into(), json!(path.to_string_lossy()));
                total_messages += records.len() as u64;
            }
            Err(error) => {
                info.insert("status".into(), json!("failed"));
                info.insert("error".into(), json!(error.to_string()));
                lane_failures.push((parser.to_string(), error.to_string()));
            }
        }
        info.insert(
            "duration_seconds".into(),
            json!(util::round_digits(started.elapsed().as_secs_f64(), 6)),
        );
        lane_summaries.push(Value::Object(info));
        if args.verbose {
            println!(
                "[message-scan][native] parser={} messages={}",
                parser,
                records.len()
            );
        }
    }
    summary.insert(
        "native_message_scan".into(),
        json!({
            "parsers": lane_summaries,
            "total_messages": total_messages,
            "output_dir": message_output_dir,
            "qdrant_collection": message_qdrant_collection,
            "graph_upsert": "deferred-phase-06",
            "vector_upsert": "deferred-phase-06",
        }),
    );
    if !lane_failures.is_empty() {
        let (parser, error) = lane_failures[0].clone();
        return Err(format!("native message-scan[{parser}]: {error}"));
    }
    Ok(())
}

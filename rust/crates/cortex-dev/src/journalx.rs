//! Native journal status/purge — port of the dev.py journal commands plus
//! the `scan_scope_id` / `ProjectRunLock` helpers from
//! `tools/common/sync_scope.py` (flock contract kept interoperable with the
//! Python sync workers and the cortex-sync port). Replaces the retired bridge ops
//! `journal_status` / `journal_purge` (phase-03 of the cutover plan).

use cortex_graph_core::journal::{inspect_journal, Journal};
use cortex_graph_core::journal::RunSummary;
use cortex_graph_core::models::{JournalError, JournalLimits};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::OpenOptions;
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};

// ---------------------------------------------------------------------------
// Scope identity (sync_scope.py ports)
// ---------------------------------------------------------------------------

/// `canonical_root` — POSIX identity normcase(realpath(abspath)).
pub fn canonical_root(root: &Path) -> String {
    let real = std::fs::canonicalize(root).unwrap_or_else(|_| crate::env::abspath(root));
    real.to_string_lossy().to_string()
}

/// `scan_scope_id` — sha256(f"{project_id}\0{canonical_root}")[:24].
pub fn scan_scope_id(project_id: &str, root: &Path) -> String {
    let identity = format!("{project_id}\0{}", canonical_root(root));
    let digest = Sha256::digest(identity.as_bytes());
    digest
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>()[..24]
        .to_string()
}

// ---------------------------------------------------------------------------
// ProjectRunLock (flock, portalocker-compatible contract)
// ---------------------------------------------------------------------------

pub struct ProjectRunLock {
    path: PathBuf,
    scope_id: String,
    handle: Option<std::fs::File>,
}

#[allow(unsafe_code)]
fn flock_exclusive(fd: std::os::unix::io::RawFd) -> Result<(), String> {
    // SAFETY: fd is a live descriptor; LOCK_EX|LOCK_NB is nonblocking.
    let rc = unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) };
    if rc == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error().to_string())
    }
}

#[allow(unsafe_code)]
fn flock_release(fd: std::os::unix::io::RawFd) {
    // SAFETY: fd was locked by this process.
    unsafe {
        libc::flock(fd, libc::LOCK_UN);
    }
}

impl ProjectRunLock {
    pub fn new(path: PathBuf, scope_id: &str) -> Self {
        ProjectRunLock {
            path,
            scope_id: scope_id.to_string(),
            handle: None,
        }
    }

    /// Poll flock(LOCK_EX|LOCK_NB) until `timeout_seconds` elapses; on
    /// success write the diagnostic metadata blob the Python lock leaves.
    pub fn acquire(&mut self, description: &str, root: &Path, timeout_seconds: f64) -> Result<(), String> {
        if self.handle.is_some() {
            return Ok(());
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.path)
            .map_err(|e| e.to_string())?;
        let deadline =
            std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout_seconds.max(0.0));
        loop {
            match flock_exclusive(file.as_raw_fd()) {
                Ok(()) => break,
                Err(_) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(format!(
                            "scan scope is busy: {description} ({})",
                            self.scope_id
                        ));
                    }
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
            }
        }
        let metadata = json!({
            "pid": std::process::id(),
            "scope_id": self.scope_id,
            "description": description,
            "root": canonical_root(root),
            "started_at": crate::util::iso_utc_now(),
        });
        {
            use std::io::Write as _;
            let mut handle = file;
            handle.set_len(0).ok();
            handle.write_all(format!("{metadata}\n").as_bytes()).ok();
            handle.flush().ok();
            self.handle = Some(handle);
        }
        Ok(())
    }

    pub fn release(&mut self) {
        if let Some(file) = self.handle.take() {
            flock_release(file.as_raw_fd());
        }
    }
}

impl Drop for ProjectRunLock {
    fn drop(&mut self) {
        self.release();
    }
}

// ---------------------------------------------------------------------------
// Status payload
// ---------------------------------------------------------------------------

fn now_epoch_s() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

fn summary_to_value(item: &RunSummary) -> Value {
    json!({
        "run_id": item.run_id,
        "status": item.status,
        "resumed": item.resumed,
        "parser": item.parser,
        "produced": item.produced,
        "acked": item.acked,
        "pending": item.pending,
        "leased": item.leased,
        "retrying": item.retrying,
        "reconciling": item.reconciling,
        "blocked": item.blocked,
        "dead_letter": item.dead_letter,
        "rows": item.rows,
        "payload_bytes": item.payload_bytes,
        "artifact_bytes": item.artifact_bytes,
        "journal_bytes": item.journal_bytes,
        "oldest_unfinished_at": item.oldest_unfinished_at,
        "oldest_unfinished_age_seconds": item.oldest_unfinished_age_seconds,
        "next_action": item.next_action,
        "error_code": item.error_code,
    })
}

/// dev.py `journal_status` payload — `{"journal_path": ..., "runs": [...]}`.
pub fn status_payload(journal_path: &str) -> Result<Value, String> {
    let resolved = resolve_strict(Path::new(journal_path))
        .map_err(|e| format!("Error: journal status failed (journal_corrupt): cannot inspect graph-write journal: {e}"))?;
    let summaries = inspect_journal(&resolved, now_epoch_s()).map_err(inspect_error_line)?;
    let runs: Vec<Value> = summaries.iter().map(summary_to_value).collect();
    Ok(json!({
        "journal_path": resolved.to_string_lossy(),
        "runs": runs,
    }))
}

/// `JournalInspectError` → the exact dev.py stderr line.
fn inspect_error_line(err: cortex_graph_core::journal::JournalInspectError) -> String {
    use cortex_graph_core::journal::JournalInspectError;
    match err {
        JournalInspectError::IncompatibleSchema(detail) => format!(
            "Error: journal status failed (incompatible_schema): {detail}"
        ),
        JournalInspectError::Corrupt(detail) => {
            format!("Error: journal status failed (journal_corrupt): {detail}")
        }
    }
}

fn resolve_strict(path: &Path) -> Result<PathBuf, String> {
    let expanded = match path.to_string_lossy().strip_prefix("~/") {
        Some(rest) => std::env::var("HOME")
            .map(|home| PathBuf::from(home).join(rest))
            .unwrap_or_else(|_| path.to_path_buf()),
        None => path.to_path_buf(),
    };
    std::fs::canonicalize(&expanded).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// Purge flow
// ---------------------------------------------------------------------------

/// dev.py `journal_purge` — full validation chain + `purge_run`; errors carry
/// the exact stderr line and the caller exits 1.
pub fn purge(journal_path: &str, run_id: &str, project_id: &str, root: &str) -> Result<Value, String> {
    let resolved = resolve_strict(Path::new(journal_path))?;
    let canonical_root_path = resolve_strict(Path::new(root))?;
    let scope_id = scan_scope_id(project_id, &canonical_root_path);
    let cache_root = resolved
        .parent()
        .and_then(Path::parent)
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .ok_or_else(|| "Error: journal path does not match the exact project/root scope".to_string())?;
    let expected_parent = std::fs::canonicalize(cache_root.join("graph-write-journal").join(&scope_id))
        .unwrap_or_else(|_| {
            crate::env::abspath(&cache_root.join("graph-write-journal").join(&scope_id))
        });
    if resolved.parent().unwrap_or(Path::new("")) != expected_parent {
        return Err("Error: journal path does not match the exact project/root scope".to_string());
    }
    let lock_path = cache_root
        .join("incremental_sync_locks")
        .join(format!("{scope_id}.lock"));
    let mut ownership = ProjectRunLock::new(lock_path, &scope_id);
    if ownership
        .acquire(
            &format!("journal purge project_id={project_id}"),
            &canonical_root_path,
            0.0,
        )
        .is_err()
    {
        return Err(
            "Error: journal scope is active; wait for sync/consumer completion".to_string(),
        );
    }
    let outcome = (|| -> Result<Value, String> {
        let artifact_root = resolved.parent().map(|p| p.join("artifacts")).unwrap_or_default();
        let database = Journal::open(
            &resolved,
            &artifact_root,
            JournalLimits::default(),
            Box::new(now_epoch_s),
        )
        .map_err(|e: JournalError| format!("Error: journal purge refused ({}): {}", e.code, e.message))?;
        let run = database
            .get_run(run_id)
            .map_err(|e| format!("Error: journal purge refused ({}): {}", e.code, e.message))?
            .ok_or_else(|| "Error: journal run does not exist".to_string())?;
        if run.metadata.project_id != project_id || run.metadata.scope_id != scope_id {
            return Err(
                "Error: journal run metadata does not match the exact project/root scope"
                    .to_string(),
            );
        }
        let safe_parser: String = run
            .metadata
            .parser
            .chars()
            .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' { c } else { '_' })
            .collect();
        if resolved
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .as_deref()
            != Some(format!("{safe_parser}.sqlite3").as_str())
        {
            return Err("Error: journal filename does not match the run parser".to_string());
        }
        let removed = database
            .purge_run(run_id, true)
            .map_err(|e| format!("Error: journal purge refused ({}): {}", e.code, e.message))?;
        Ok(json!({
            "event_type": "journal_purged",
            "journal_path": resolved.to_string_lossy(),
            "run_id": run_id,
            "scope_id": scope_id,
            "removed_artifacts": removed,
        }))
    })();
    ownership.release();
    outcome
}

// ---------------------------------------------------------------------------
// Required-journal recovery (phase-04)
// ---------------------------------------------------------------------------

/// dev.py `run_with_retry`'s pre-attempt journal recovery. Phase-03: the
/// replay driver is NATIVE — exec `cortex-sync --journal-recover-only`
/// (same lib the orchestrator uses; the python consumer spawn + its
/// `":"`-joined PYTHONPATH bug died with the phase-03 cutover, and the
/// python plane itself was deleted at phase-06). Returns Some(rc) on failure.
pub fn recover_required_lane(process_env: &[(String, String)]) -> Option<i32> {
    let journal_mode = process_env
        .iter()
        .find(|(k, _)| k == "CORTEX_GRAPH_JOURNAL_MODE")
        .map(|(_, v)| v.to_lowercase())
        .unwrap_or_default();
    if !matches!(journal_mode.as_str(), "required" | "shared-required") {
        return None;
    }
    let root = crate::util::repo_root();
    let sync_bin = cortex_sync_binary(&root);
    let recovery = std::process::Command::new(&sync_bin)
        .arg("--journal-recover-only")
        .envs(process_env.iter().map(|(k, v)| (k.as_str(), v.as_str())))
        .status();
    let rc = recovery.map(|s| s.code().unwrap_or(1)).unwrap_or(1);
    if rc != 0 {
        return Some(rc);
    }
    None
}

/// Same resolution as `cmds::sync::cortex_sync_binary` (env override →
/// repo release → repo debug target).
fn cortex_sync_binary(root: &std::path::Path) -> std::path::PathBuf {
    if let Ok(explicit) = std::env::var("CORTEX_SYNC_BIN") {
        let path = std::path::PathBuf::from(explicit);
        if path.is_file() {
            return path;
        }
    }
    for candidate in [
        root.join("rust").join("target").join("release").join("cortex-sync"),
        root.join("rust").join("target").join("debug").join("cortex-sync"),
    ] {
        if candidate.is_file() {
            return candidate;
        }
    }
    std::path::PathBuf::from("cortex-sync")
}

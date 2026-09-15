//! Process discovery and scoped termination — native port of
//! `cortex_harness/sync_processes.py` (psutil → `ps` + libc signals) and
//! dev.py's `_mcp_pids` / `_mcp_uptime` / `_mcp_stop_pattern` helpers.
//! Replaces the `pyexec` bridge ops `stop_sync_workers`,
//! `embedded_falkordb_pids`, `stop_embedded`, `mcp_pids`, `mcp_uptime`,
//! `mcp_stop` (phase-02 of the dev/make cutover plan).

use crate::util::shlex_split;
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

// ---------------------------------------------------------------------------
// Process table (psutil.process_iter equivalent)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct ProcessRecord {
    pub pid: i64,
    pub ppid: i64,
    pub argv: Vec<String>,
}

impl ProcessRecord {
    /// psutil `ProcessRecord.command` contract (parity references/tests).
    #[allow(dead_code)]
    pub fn command(&self) -> String {
        self.argv.join(" ")
    }
}

/// Cross-platform snapshot without shell process-name matching. On macOS
/// `ps -ax -o pid=,ppid=,command=` covers every process the user can signal.
pub fn process_table() -> BTreeMap<i64, ProcessRecord> {
    let mut records = BTreeMap::new();
    let output = match std::process::Command::new("ps")
        .args(["-ax", "-o", "pid=,ppid=,command="])
        .output()
    {
        Ok(o) if o.status.success() => o,
        _ => {
            // Linux: same columns via -e.
            match std::process::Command::new("ps")
                .args(["-e", "-o", "pid=,ppid=,command="])
                .output()
            {
                Ok(o) => o,
                Err(_) => return records,
            }
        }
    };
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let trimmed = line.trim();
        // ps right-aligns pid/ppid columns, so whitespace RUNS separate the
        // two numeric fields; split field-by-field, command takes the rest.
        let mut rest = trimmed;
        let mut fields = [""; 2];
        for field in fields.iter_mut() {
            rest = rest.trim_start();
            let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
            *field = &rest[..end];
            rest = &rest[end..];
        }
        let command = rest.trim_start();
        let (Ok(pid), Ok(ppid)) = (fields[0].parse::<i64>(), fields[1].parse::<i64>()) else {
            continue;
        };
        let Some(argv) = shlex_split(command) else { continue };
        if argv.is_empty() {
            continue;
        }
        records.insert(pid, ProcessRecord { pid, ppid, argv });
    }
    records
}

/// `Path(argument).expanduser().resolve(strict=False)` for non-flag args.
fn resolved_argument(argument: &str) -> Option<PathBuf> {
    if argument.is_empty() || argument.starts_with('-') {
        return None;
    }
    let expanded = if let Some(rest) = argument.strip_prefix("~/") {
        std::env::var("HOME").ok().map(|home| PathBuf::from(home).join(rest))?
    } else {
        PathBuf::from(argument)
    };
    Some(crate::env::abspath(&expanded))
}

fn is_dev_sync(record: &ProcessRecord, owner: &str, root: &Path) -> bool {
    let dev_script = crate::env::abspath(&root.join("cortex_harness").join("dev.py"));
    for (index, argument) in record.argv.iter().enumerate() {
        let Some(resolved) = resolved_argument(argument) else {
            continue;
        };
        if resolved != dev_script {
            continue;
        }
        let tail = &record.argv[index + 1..];
        return tail.len() >= 2
            && tail[0] == "sync"
            && tail[1] == owner
            && !tail[2..].iter().any(|arg| arg == "stop");
    }
    false
}

const CODE_WORKER_NAMES: [&str; 3] =
    ["build_owner_manifests.py", "clang_worker.py", "incremental_sync.py"];

fn is_code_worker(record: &ProcessRecord, root: &Path) -> bool {
    let code_root = crate::env::abspath(&root.join("code-tiny"));
    for argument in &record.argv {
        let Some(path) = resolved_argument(argument) else {
            continue;
        };
        if !path.starts_with(&code_root) || path == code_root {
            continue;
        }
        let Some(name) = path.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if CODE_WORKER_NAMES.contains(&name) || name.ends_with("_analyzer.py") {
            return true;
        }
    }
    false
}

fn is_doc_worker(record: &ProcessRecord, root: &Path) -> bool {
    let ingestor = crate::env::abspath(
        &root
            .join("doc-tiny")
            .join("graphrag_ingest_langextract.py"),
    );
    record
        .argv
        .iter()
        .any(|argument| resolved_argument(argument).is_some_and(|path| path == ingestor))
}

/// `sync_processes` — harness sync launchers/workers for `owner`, sorted by pid.
pub fn sync_processes(
    owner: &str,
    root: &Path,
    table: &BTreeMap<i64, ProcessRecord>,
    exclude_pids: &[i64],
    include_launchers: bool,
) -> Vec<ProcessRecord> {
    let excluded: BTreeSet<i64> = exclude_pids.iter().copied().collect();
    let mut matches = Vec::new();
    for record in table.values() {
        if excluded.contains(&record.pid) {
            continue;
        }
        let worker_match = if owner == "code" {
            is_code_worker(record, root)
        } else {
            is_doc_worker(record, root)
        };
        if (include_launchers && is_dev_sync(record, owner, root)) || worker_match {
            matches.push(record.clone());
        }
    }
    matches.sort_by_key(|r| r.pid);
    matches
}

/// Transitive children of `roots` in the table.
fn descendants(roots: &BTreeSet<i64>, table: &BTreeMap<i64, ProcessRecord>) -> BTreeSet<i64> {
    let mut selected = roots.clone();
    let mut changed = true;
    while changed {
        changed = false;
        for record in table.values() {
            if selected.contains(&record.ppid) && !selected.contains(&record.pid) {
                selected.insert(record.pid);
                changed = true;
            }
        }
    }
    selected
}

/// Depth of `pid` within the selected subtree (walk ppid until leaving it).
fn depth(pid: i64, table: &BTreeMap<i64, ProcessRecord>, selected: &BTreeSet<i64>) -> usize {
    let mut result = 0;
    let mut current = pid;
    while let Some(record) = table.get(&current) {
        if !selected.contains(&record.ppid) {
            break;
        }
        result += 1;
        current = record.ppid;
    }
    result
}

#[derive(Debug, Clone, Default)]
pub struct StopReport {
    pub matched: Vec<i64>,
    pub terminated: Vec<i64>,
    pub forced: Vec<i64>,
    pub remaining: Vec<i64>,
}

fn pid_alive(pid: i64) -> bool {
    // kill(pid, 0) succeeds for zombies too; classify zombies as dead by
    // asking ps for the state (psutil.STATUS_ZOMBIE equivalent).
    if !send_signal(pid, 0) {
        return false;
    }
    let output = std::process::Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output();
    match output {
        Ok(o) if o.status.success() => {
            let stat = String::from_utf8_lossy(&o.stdout);
            !stat.trim().starts_with('Z')
        }
        _ => true,
    }
}

/// kill(2) wrapper. Crate denies `unsafe_code` globally; the libc call is
/// confined here (signal delivery to a numeric pid, rc-checked).
#[allow(unsafe_code)]
fn send_signal(pid: i64, sig: i32) -> bool {
    // kill(pid, 0) performs no delivery — existence check only.
    let rc = unsafe { libc::kill(pid as libc::pid_t, sig) };
    rc == 0
}

const SIGTERM: i32 = libc::SIGTERM;
const SIGKILL: i32 = libc::SIGKILL;

/// `stop_sync_processes` — terminate matching workers and descendants,
/// escalating to SIGKILL only after `timeout` seconds.
pub fn stop_sync_processes(
    owner: &str,
    root: &Path,
    exclude_pids: &[i64],
    timeout: f64,
    include_launchers: bool,
) -> StopReport {
    let mut excluded: Vec<i64> = exclude_pids.to_vec();
    excluded.push(std::process::id() as i64);
    let table = process_table();
    let matched: BTreeSet<i64> = sync_processes(owner, root, &table, &excluded, include_launchers)
        .into_iter()
        .map(|r| r.pid)
        .collect();
    let targets: BTreeSet<i64> = descendants(&matched, &table)
        .into_iter()
        .filter(|pid| !excluded.contains(pid))
        .collect();
    let mut ordered: Vec<i64> = targets.iter().copied().collect();
    ordered.sort_by_key(|pid| (std::cmp::Reverse(depth(*pid, &table, &targets)), std::cmp::Reverse(*pid)));

    for pid in &ordered {
        send_signal(*pid, SIGTERM);
    }
    // psutil.wait_procs(timeout): poll until all gone or deadline.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout.max(0.0));
    let mut remaining: Vec<i64> = ordered.clone();
    while !remaining.is_empty() && std::time::Instant::now() < deadline {
        remaining.retain(|pid| pid_alive(*pid));
        if remaining.is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    let mut forced = Vec::new();
    for pid in &remaining {
        if send_signal(*pid, SIGKILL) {
            forced.push(*pid);
        }
    }
    if !remaining.is_empty() {
        let kill_deadline =
            std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout.min(2.0).max(0.0));
        while std::time::Instant::now() < kill_deadline {
            remaining.retain(|pid| pid_alive(*pid));
            if remaining.is_empty() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    }
    let still_running: Vec<i64> = targets.iter().copied().filter(|pid| pid_alive(*pid)).collect();
    let terminated: Vec<i64> = targets
        .iter()
        .copied()
        .filter(|pid| !still_running.contains(pid))
        .collect();
    StopReport {
        matched: matched.into_iter().collect(),
        terminated,
        forced,
        remaining: still_running,
    }
}

// ---------------------------------------------------------------------------
// Embedded FalkorDB (redislite) discovery
// ---------------------------------------------------------------------------

/// `embedded_falkordb_pids` — redis-server processes whose config points at
/// one exact RDB file.
pub fn embedded_falkordb_pids(db_path: &Path, table: Option<&BTreeMap<i64, ProcessRecord>>) -> Vec<i64> {
    let target = crate::env::abspath(&expand_home(db_path));
    let owned;
    let table_ref = match table {
        Some(t) => t,
        None => {
            owned = process_table();
            &owned
        }
    };
    let mut matches = Vec::new();
    for record in table_ref.values() {
        let first = record.argv.first().map(String::as_str).unwrap_or("");
        let first_name = Path::new(first)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        if !first_name.contains("redis-server") {
            continue;
        }
        let mut config_paths: Vec<PathBuf> = Vec::new();
        for argument in record.argv.iter().skip(1) {
            if let Some(socket) = argument.strip_prefix("unixsocket:") {
                config_paths.push(
                    PathBuf::from(socket)
                        .parent()
                        .map(|p| p.join("redis.config"))
                        .unwrap_or_else(|| PathBuf::from("redis.config")),
                );
            } else {
                let candidate = PathBuf::from(argument);
                if candidate.is_file() {
                    config_paths.push(candidate);
                }
            }
        }
        for config_path in config_paths {
            let Ok(text) = std::fs::read_to_string(&config_path) else {
                continue;
            };
            let mut dir: Option<String> = None;
            let mut dbfilename: Option<String> = None;
            for line in text.lines() {
                let Some(tokens) = shlex_split_config(line) else { continue };
                if tokens.len() < 2 {
                    continue;
                }
                match tokens[0].as_str() {
                    "dir" => dir = Some(tokens[1].clone()),
                    "dbfilename" => dbfilename = Some(tokens[1].clone()),
                    _ => {}
                }
            }
            let (Some(dir), Some(dbfilename)) = (dir, dbfilename) else {
                continue;
            };
            let configured = crate::env::abspath(&expand_home(&PathBuf::from(dir).join(dbfilename)));
            if configured == target {
                matches.push(record.pid);
                break;
            }
        }
    }
    matches.sort();
    matches
}

/// `stop_embedded_falkordb` — stop only embedded redis processes owning
/// `db_path`; returns the pid list that was found (Python semantics).
pub fn stop_embedded_falkordb(db_path: &Path, timeout: f64) -> Vec<i64> {
    let pids = embedded_falkordb_pids(db_path, None);
    for pid in &pids {
        send_signal(*pid, SIGTERM);
    }
    let mut remaining: Vec<i64> = pids.clone();
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout.max(0.0));
    while !remaining.is_empty() && std::time::Instant::now() < deadline {
        remaining.retain(|pid| pid_alive(*pid));
        if remaining.is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    for pid in &remaining {
        send_signal(*pid, SIGKILL);
    }
    if !remaining.is_empty() {
        let kill_deadline =
            std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout.min(2.0).max(0.0));
        while std::time::Instant::now() < kill_deadline {
            remaining.retain(|pid| pid_alive(*pid));
            if remaining.is_empty() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    }
    pids
}

fn expand_home(path: &Path) -> PathBuf {
    let text = path.to_string_lossy().to_string();
    if let Some(rest) = text.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(rest);
        }
    }
    path.to_path_buf()
}

/// shlex.split with comments=True for redis config lines.
fn shlex_split_config(line: &str) -> Option<Vec<String>> {
    let hash = line.find('#').unwrap_or(line.len());
    let body = &line[..hash];
    if body.trim().is_empty() {
        return None;
    }
    shlex_split(body)
}

// ---------------------------------------------------------------------------
// MCP pid / uptime / stop (dev.py `_mcp_*` ports)
// ---------------------------------------------------------------------------

/// dev.py `_mcp_pids` POSIX branch — python processes whose command arguments
/// contain the pattern file name; optional per-instance sidecar filter.
pub fn mcp_pids(pattern: &str, instance_id: Option<&str>) -> Vec<i64> {
    let output = match std::process::Command::new("ps")
        .args(["-ax", "-o", "pid=,command="])
        .output()
    {
        Ok(o) => o,
        Err(_) => return Vec::new(),
    };
    let mut pids = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let trimmed = line.trim();
        let Some((pid_str, command)) = trimmed.split_once(char::is_whitespace) else {
            continue;
        };
        let Ok(pid) = pid_str.trim().parse::<i64>() else { continue };
        let Some(parts) = shlex_split(command) else { continue };
        if parts.is_empty() {
            continue;
        }
        let executable = Path::new(&parts[0])
            .file_name()
            .map(|n| n.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if executable.contains("python")
            && parts[1..]
                .iter()
                .any(|arg| Path::new(arg).file_name().is_some_and(|n| n == pattern))
        {
            pids.push(pid);
        }
    }

    if instance_id.is_none() || legacy_pause_by_instance_disabled() {
        return pids;
    }
    let wanted = instance_id.unwrap();
    pids.into_iter()
        .filter(|pid| pid_instance_id(*pid).as_deref() == Some(wanted))
        .collect()
}

fn legacy_pause_by_instance_disabled() -> bool {
    std::env::var("CORTEX_MCP_PAUSE_BY_INSTANCE")
        .map(|v| v.trim() == "0")
        .unwrap_or(false)
}

/// dev.py `_pid_instance_id` — sidecar pid files first, then the process env
/// block via `ps eww` (POSIX; macOS has no /proc/<pid>/environ).
pub fn pid_instance_id(pid: i64) -> Option<String> {
    if pid <= 0 {
        return None;
    }
    let cache_dir = PathBuf::from(".cache");
    if let Ok(entries) = std::fs::read_dir(&cache_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("dev-mcp-") || !name.ends_with(".pid") || !name.contains('-') {
                continue;
            }
            // dev.py globs `dev-mcp-*-*.pid` — at least two dashes total.
            if name.trim_end_matches(".pid").split('-').count() < 3 {
                continue;
            }
            if let Ok(text) = std::fs::read_to_string(entry.path()) {
                let mut recorded: Option<i64> = None;
                let mut instance: Option<String> = None;
                for line in text.lines() {
                    let stripped = line.trim();
                    if let Some(v) = stripped.strip_prefix("pid=") {
                        recorded = v.trim().parse().ok();
                    }
                    if let Some(v) = stripped.strip_prefix("instance_id=") {
                        instance = Some(v.trim().to_string());
                    }
                }
                if recorded == Some(pid) {
                    return instance.or_else(|| {
                        name.trim_end_matches(".pid").split('-').next_back().map(String::from)
                    });
                }
            }
        }
    }
    read_instance_from_process_env(pid)
}

fn read_instance_from_process_env(pid: i64) -> Option<String> {
    // POSIX fallback: `ps eww -p <pid>` (macOS: `ps -E` is not available for
    // arbitrary pids; eww prints the env on Linux only). Best-effort both.
    for args in [
        vec!["eww".to_string(), "-p".to_string(), pid.to_string()],
        vec!["-E".to_string(), "-p".to_string(), pid.to_string()],
    ] {
        if let Ok(output) = std::process::Command::new("ps").args(&args).output() {
            if !output.status.success() {
                continue;
            }
            let text = String::from_utf8_lossy(&output.stdout);
            for token in text.split_whitespace() {
                if let Some(value) = token.strip_prefix("CORTEX_STORAGE_INSTANCE=") {
                    let cleaned = value.trim_matches(|c| c == '"' || c == '\'');
                    if !cleaned.is_empty() {
                        return Some(cleaned.to_string());
                    }
                }
            }
        }
    }
    None
}

/// dev.py `_mcp_uptime` — `ps -p pid -o etime=`.
pub fn mcp_uptime(pid: i64) -> String {
    std::process::Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "etime="])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "?".to_string())
}

/// dev.py `_mcp_stop_pattern` — TERM the matching pids, wait up to 5s, KILL
/// the rest; returns how many pids were signalled initially.
pub fn mcp_stop_pattern(pattern: &str, instance_id: Option<&str>) -> usize {
    let pids = mcp_pids(pattern, instance_id);
    for pid in &pids {
        send_signal(*pid, SIGTERM);
    }
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(5.0);
    let mut remaining: Vec<i64> = pids.clone();
    while !pids.is_empty() && std::time::Instant::now() < deadline {
        remaining = mcp_pids(pattern, instance_id);
        if remaining.is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    for pid in &remaining {
        send_signal(*pid, SIGKILL);
    }
    pids.len()
}

/// Read a small file's bytes (helper kept for future parity probes).
#[allow(dead_code)]
pub fn read_bytes(path: &Path) -> Option<Vec<u8>> {
    let mut buf = Vec::new();
    std::fs::File::open(path).ok()?.read_to_end(&mut buf).ok()?;
    Some(buf)
}

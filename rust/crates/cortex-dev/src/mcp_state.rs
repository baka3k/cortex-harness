//! `.cache/mcp` lifecycle state — native port of the PID-record / active-env
//! half of `scripts/mcp-lifecycle.py` (phase-05b, `start` + `stop`).
//!
//! The on-disk contract is unchanged from the Python launcher so the still
//! shimmed `doctor` action and every pre-existing record keep working:
//! `<instance>-<server>.active.env` (0600), `<instance>-<server>.pid`,
//! `start-<instance>-<server>.command` and `pids.json`.

use crate::procinfo::{self, ProcessRecord};
use crate::util::{echo, repo_root};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::time::Duration;

/// dev.py `MCP_PROCESS_MARKERS`.
pub const MCP_PROCESS_MARKERS: [&str; 4] = [
    "code-tiny/mcp.sh",
    "doc-tiny/mcp.sh",
    "mcp/unified_mcp.py",
    "mcp_graph_rag.py",
];

pub fn state_dir() -> PathBuf {
    repo_root().join(".cache").join("mcp")
}

pub fn pid_file() -> PathBuf {
    state_dir().join("pids.json")
}

// ---------------------------------------------------------------------------
// PID records
// ---------------------------------------------------------------------------

/// `read_pid_records` — a missing, malformed, or non-list payload reads as empty.
pub fn read_records() -> Vec<Value> {
    read_records_from(&pid_file())
}

pub fn read_records_from(path: &Path) -> Vec<Value> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Array(items)) => items.into_iter().filter(Value::is_object).collect(),
        _ => Vec::new(),
    }
}

/// `write_pid_records` — an empty record set removes the file entirely.
pub fn write_records(records: &[Value]) {
    write_records_to(&pid_file(), records)
}

pub fn write_records_to(path: &Path, records: &[Value]) {
    if records.is_empty() {
        let _ = std::fs::remove_file(path);
        return;
    }
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match serde_json::to_string_pretty(records) {
        Ok(payload) => {
            let _ = std::fs::write(path, payload);
        }
        Err(error) => {
            // A record file that cannot be serialized would leave `start`
            // silently without a PID record; surface it on stderr instead.
            crate::util::echo_err(&format!("[start] could not serialize pid records: {error}"));
        }
    }
}

/// `record_pid` — anything that is not an int (or int-like string) is 0.
pub fn record_pid(record: &Value) -> i64 {
    match record.get("pid") {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

fn record_text<'a>(record: &'a Value, key: &str, default: &'a str) -> &'a str {
    if let Some(Value::String(text)) = record.get(key) {
        text.as_str()
    } else {
        default
    }
}

// ---------------------------------------------------------------------------
// Instance naming
// ---------------------------------------------------------------------------

/// dev.py `validate_instance_name` (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`).
pub fn validate_instance_name(value: &str) -> Result<String, String> {
    let characters: Vec<char> = value.chars().collect();
    let valid = !characters.is_empty()
        && characters.len() <= 64
        && characters[0].is_ascii_alphanumeric()
        && characters
            .iter()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'));
    if valid {
        Ok(value.to_string())
    } else {
        Err("Instance name must match [A-Za-z0-9][A-Za-z0-9_.-]{0,63}.".to_string())
    }
}

/// dev.py `_config_instance` — the graph key follows the active provider
/// (`ladybug` would otherwise fall through to `NEO4J_DB` and never match).
pub fn config_instance(
    env: &BTreeMap<String, String>,
    graph_key: &str,
) -> Result<String, String> {
    let raw = ["PROJECT_ID", "CORTEX_STORAGE_INSTANCE", graph_key]
        .iter()
        .map(|key| env.get(*key).map(String::as_str).unwrap_or_default().trim())
        .find(|value| !value.is_empty())
        .unwrap_or("cortext")
        .to_string();
    let normalized = substitute_invalid_chars(&raw);
    let trimmed = normalized.trim_matches(|c| c == '-' || c == '.');
    let truncated: String = trimmed.chars().take(64).collect();
    if truncated.is_empty() {
        return validate_instance_name("cortext");
    }
    validate_instance_name(&truncated)
}

/// `re.sub(r"[^A-Za-z0-9_.-]+", "-", raw)` — every maximal run of disallowed
/// characters collapses into exactly one dash.
fn substitute_invalid_chars(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut inside_run = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | '-') {
            out.push(character);
            inside_run = false;
        } else if !inside_run {
            out.push('-');
            inside_run = true;
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Provider-aware graph key + bash export rendering
// ---------------------------------------------------------------------------

/// The env key that names the active graph for one provider.
pub fn graph_key(provider: &str) -> &'static str {
    match provider {
        "falkordb" => "FALKORDB_GRAPH",
        "ladybug" => "LADYBUG_GRAPH",
        _ => "NEO4J_DB",
    }
}

/// shlex.quote (POSIX mode) for a single token.
pub fn shlex_quote(value: &str) -> String {
    let safe = !value.is_empty()
        && value.chars().all(|c| {
            c.is_ascii_alphanumeric() || matches!(c, '@' | '_' | '%' | '+' | '=' | ':' | '.' | '/' | '-')
        });
    if safe {
        return value.to_string();
    }
    format!("'{}'", value.replace('\'', "'\\''"))
}

/// `mcp_runtime_config.format_bash_exports`, extended to the three-way
/// unset matrix that `env::isolate_graph_provider_environment` already applies
/// to the in-process map (a stale inherited `LADYBUG_PATH` must not survive a
/// `falkordb` launch either).
pub fn format_bash_exports(env: &BTreeMap<String, String>) -> Result<String, String> {
    let mut lines: Vec<String> = env
        .iter()
        .map(|(key, value)| format!("export {key}={}", shlex_quote(value)))
        .collect();
    let provider = ["CODE_GRAPH_PROVIDER", "DOC_GRAPH_PROVIDER", "GRAPH_PROVIDER"]
        .iter()
        .map(|key| env.get(*key).map(String::as_str).unwrap_or_default().trim())
        .find(|value| !value.is_empty())
        .unwrap_or("falkordb")
        .to_lowercase();
    let (prefixes, extra): (&[&str], &[&str]) = match provider.as_str() {
        "falkordb" | "falkor" => (&["NEO4J_", "LADYBUG_"], &[]),
        "neo4j" => (&["FALKORDB_", "LADYBUG_"], &["DOC_FALKORDB_GRAPH"]),
        "ladybug" | "lbug" | "lady-bug" | "kuzu" => {
            (&["FALKORDB_", "NEO4J_"], &["DOC_FALKORDB_GRAPH"])
        }
        other => return Err(format!("Unsupported graph provider: {other}")),
    };
    for prefix in prefixes {
        lines.push(format!(
            "for _cortex_inactive_key in \"${{!{prefix}@}}\"; do unset \"$_cortex_inactive_key\"; done"
        ));
    }
    let mut final_unset = extra.to_vec();
    final_unset.push("_cortex_inactive_key");
    lines.push(format!(
        "unset {} 2>/dev/null || true",
        final_unset.join(" ")
    ));
    Ok(lines.join("\n"))
}

// ---------------------------------------------------------------------------
// Port probing
// ---------------------------------------------------------------------------

/// dev.py `_tcp_port_open`.
pub fn tcp_port_open(host: &str, port: u16) -> bool {
    let timeout = Duration::from_secs(1);
    let Ok(addresses) = (host, port).to_socket_addrs() else {
        return false;
    };
    addresses
        .into_iter()
        .any(|address| TcpStream::connect_timeout(&address, timeout).is_ok())
}

/// dev.py `_next_available_port`.
pub fn next_available_port(
    host: &str,
    preferred: u16,
    reserved: &mut BTreeSet<u16>,
) -> Result<u16, String> {
    let mut port = preferred;
    loop {
        if !reserved.contains(&port) && !tcp_port_open(host, port) {
            reserved.insert(port);
            return Ok(port);
        }
        if port == u16::MAX {
            return Err(format!("No available MCP port at or above {preferred}."));
        }
        port += 1;
    }
}

// ---------------------------------------------------------------------------
// Termination
// ---------------------------------------------------------------------------

/// dev.py `_stop_process_tree` — children first, SIGTERM only (no escalation).
pub fn stop_process_tree(pid: i64, processes: &BTreeMap<i64, ProcessRecord>) {
    let children: Vec<i64> = processes
        .values()
        .filter(|record| record.ppid == pid)
        .map(|record| record.pid)
        .collect();
    for child in children {
        stop_process_tree(child, processes);
    }
    procinfo::send_signal(pid, procinfo::SIGTERM);
}

/// dev.py `_invoke_stop` — saved records first, then (for a full stop) every
/// MCP process whose command line references this harness root.
pub fn stop(instance: Option<&str>) {
    let processes = procinfo::process_table();
    let mut stopped: BTreeSet<i64> = BTreeSet::new();
    let mut remaining: Vec<Value> = Vec::new();

    for record in read_records() {
        if let Some(wanted) = instance
            && record_text(&record, "instance", "") != wanted
        {
            remaining.push(record);
            continue;
        }
        let pid = record_pid(&record);
        let command = processes.get(&pid).map(ProcessRecord::command).unwrap_or_default();
        let script = record_text(&record, "script", "").to_string();
        let name = record_text(&record, "name", "unknown");
        if pid > 1 && !script.is_empty() && command.contains(&script) {
            echo(&format!("[stop] Stopping saved process {pid} ({name})"));
            stop_process_tree(pid, &processes);
            stopped.insert(pid);
        } else if processes.contains_key(&pid) {
            echo(&format!("[stop] Skipping stale PID record {pid} ({name})"));
        }
    }

    if instance.is_none() {
        let root = repo_root().to_string_lossy().to_string();
        let own_pid = std::process::id() as i64;
        let candidates: Vec<(i64, String)> = processes
            .values()
            .filter(|record| {
                record.pid != own_pid
                    && !stopped.contains(&record.pid)
                    && record.command().contains(&root)
                    && MCP_PROCESS_MARKERS
                        .iter()
                        .any(|marker| record.command().contains(marker))
            })
            .map(|record| (record.pid, record.command()))
            .collect();
        for (pid, _) in candidates {
            echo(&format!("[stop] Stopping MCP process {pid}"));
            stop_process_tree(pid, &processes);
        }
        remaining.clear();
    }

    write_records(&remaining);
    let scope = match instance {
        Some(value) => format!(" instance '{value}'"),
        None => String::new(),
    };
    echo(&format!("[stop] MCP{scope} stop complete."));
}

/// dev.py `_running_mcp_instances` PID-alive half — used by `start` to decide
/// whether a saved record can be reused.
pub fn record_is_live(
    record: &Value,
    processes: &BTreeMap<i64, ProcessRecord>,
) -> bool {
    let pid = record_pid(record);
    if pid <= 1 {
        return false;
    }
    let Some(command) = processes.get(&pid).map(ProcessRecord::command) else {
        return false;
    };
    MCP_PROCESS_MARKERS.iter().any(|marker| command.contains(marker))
}

pub fn state_paths(instance: &str, server_name: &str) -> (PathBuf, PathBuf, PathBuf, PathBuf) {
    let directory = state_dir();
    let state_name = format!("{instance}-{server_name}");
    (
        directory.join(format!("start-{state_name}.command")),
        directory.join(format!("{state_name}.pid")),
        directory.join(format!("{state_name}.active.env")),
        directory,
    )
}

/// Restrict the active-env file the way Python's `chmod(0o600)` does.
pub fn write_active_env(path: &Path, exports: &str) -> std::io::Result<()> {
    std::fs::write(path, exports)?;
    set_mode(path, 0o600)
}

/// `Path.chmod(0o755)` for the generated Terminal wrapper.
pub fn write_executable(path: &Path, body: &str) -> std::io::Result<()> {
    std::fs::write(path, body)?;
    set_mode(path, 0o755)
}

fn set_mode(path: &Path, mode: u32) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))?;
    }
    #[cfg(not(unix))]
    {
        let _ = (path, mode);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn instance_name_validator_matches_the_python_regex() {
        assert_eq!(validate_instance_name("shop").unwrap(), "shop");
        assert_eq!(validate_instance_name("a_b.c-d").unwrap(), "a_b.c-d");
        assert!(validate_instance_name("").is_err());
        assert!(validate_instance_name("_leading").is_err());
        assert!(validate_instance_name("has space").is_err());
        assert!(validate_instance_name(&"x".repeat(65)).is_err());
    }

    #[test]
    fn config_instance_sanitizes_and_collapses_runs() {
        let environment = env(&[("PROJECT_ID", "AWS SA Associate")]);
        assert_eq!(config_instance(&environment, "FALKORDB_GRAPH").unwrap(), "AWS-SA-Associate");
        // A run of disallowed characters collapses to a single dash.
        let environment = env(&[("PROJECT_ID", "a@@@b")]);
        assert_eq!(config_instance(&environment, "FALKORDB_GRAPH").unwrap(), "a-b");
        // Empty PROJECT_ID falls through to the graph key, then to the default.
        let environment = env(&[("PROJECT_ID", ""), ("FALKORDB_GRAPH", "cortext_doc")]);
        assert_eq!(config_instance(&environment, "FALKORDB_GRAPH").unwrap(), "cortext_doc");
        let environment = env(&[]);
        assert_eq!(config_instance(&environment, "LADYBUG_GRAPH").unwrap(), "cortext");
        // All-dash input falls back to the default instead of failing.
        let environment = env(&[("PROJECT_ID", "!!!")]);
        assert_eq!(config_instance(&environment, "FALKORDB_GRAPH").unwrap(), "cortext");
    }

    #[test]
    fn graph_key_follows_the_active_provider() {
        assert_eq!(graph_key("falkordb"), "FALKORDB_GRAPH");
        assert_eq!(graph_key("ladybug"), "LADYBUG_GRAPH");
        assert_eq!(graph_key("neo4j"), "NEO4J_DB");
    }

    #[test]
    fn shlex_quote_matches_posix_quoting() {
        assert_eq!(shlex_quote("/tmp/a-b_c.sh"), "/tmp/a-b_c.sh");
        assert_eq!(shlex_quote("two words"), "'two words'");
        assert_eq!(shlex_quote(""), "''");
        assert_eq!(shlex_quote("it's"), "'it'\\''s'");
    }

    #[test]
    fn bash_exports_unset_matrix() {
        let exports = format_bash_exports(&env(&[
            ("GRAPH_PROVIDER", "falkordb"),
            ("FALKORDB_GRAPH", "primary"),
        ]))
        .unwrap();
        assert!(exports.contains("export FALKORDB_GRAPH=primary"));
        assert!(exports.contains("${!NEO4J_@}"));
        assert!(exports.contains("${!LADYBUG_@}"));

        let exports = format_bash_exports(&env(&[("GRAPH_PROVIDER", "ladybug")])).unwrap();
        assert!(exports.contains("${!FALKORDB_@}"));
        assert!(exports.contains("${!NEO4J_@}"));
        assert!(exports.contains("unset DOC_FALKORDB_GRAPH _cortex_inactive_key"));
        assert!(!exports.contains("${!LADYBUG_@}"));

        let exports = format_bash_exports(&env(&[("GRAPH_PROVIDER", "neo4j")])).unwrap();
        assert!(exports.contains("${!FALKORDB_@}"));
        assert!(exports.contains("${!LADYBUG_@}"));

        assert!(format_bash_exports(&env(&[("GRAPH_PROVIDER", "redisgraph")])).is_err());
        // Empty env keeps the historical falkordb default.
        assert!(format_bash_exports(&env(&[])).unwrap().contains("${!NEO4J_@}"));
    }

    #[test]
    fn records_round_trip_through_the_state_file() {
        let directory = std::env::temp_dir().join(format!("cortex-mcp-state-{}", std::process::id()));
        let path = directory.join("pids.json");
        write_records_to(&path, &[serde_json::json!({"name": "code-tiny", "pid": 4242})]);
        let records = read_records_from(&path);
        assert_eq!(records.len(), 1);
        assert_eq!(record_pid(&records[0]), 4242);
        assert_eq!(record_text(&records[0], "name", "unknown"), "code-tiny");
        assert_eq!(record_text(&records[0], "missing", "unknown"), "unknown");
        assert_eq!(record_pid(&serde_json::json!({"pid": "bad"})), 0);
        // Python `json.dumps(indent=2)`: two-space indent, no trailing newline.
        let payload = std::fs::read_to_string(&path).unwrap();
        assert!(payload.contains("\n  {\n"));
        assert!(!payload.ends_with('\n'));
        // Malformed payloads read as empty, and an empty set removes the file.
        std::fs::write(&path, "not json").unwrap();
        assert!(read_records_from(&path).is_empty());
        write_records_to(&path, &[]);
        assert!(!path.exists());
        let _ = std::fs::remove_dir_all(&directory);
    }
}

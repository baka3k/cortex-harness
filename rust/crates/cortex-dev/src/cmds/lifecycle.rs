//! Lifecycle commands (`_run_lifecycle` port): help/build/install/uninstall,
//! infra-up/down, storage-*, start, stop, doctor — all delegate to
//! `scripts/mcp-lifecycle.py` with the harness venv Python, plus the native
//! `mcp-gates` printer.

use crate::parser::Matches;
use crate::pyexec::{repo_root, venv_python};
use crate::util::{echo, echo_err, fail};
use std::path::PathBuf;
use std::process::Command;

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

    let mut command = Command::new(venv_python(&root));
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

pub fn lifecycle_help() {
    run_lifecycle("help", &[]);
}

pub fn build() {
    run_lifecycle("build", &[]);
}

pub fn install() {
    run_lifecycle("install", &[]);
}

pub fn uninstall() {
    run_lifecycle("uninstall", &[]);
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

pub fn storage_init() {
    run_lifecycle("storage-init", &[]);
}

pub fn storage_layout() {
    run_lifecycle("storage-layout", &[]);
}

pub fn storage_migrate_layout(m: &Matches) {
    let legacy_root = m.value_or("--legacy-root", "");
    let legacy_root = if legacy_root.is_empty() {
        repo_root().to_string_lossy().to_string()
    } else {
        legacy_root
    };
    let mut arguments = vec!["--legacy-root".to_string(), legacy_root];
    if m.flag("--apply") {
        arguments.push("--apply".to_string());
    }
    run_lifecycle("storage-migrate-layout", &arguments);
}

pub fn storage_backup(m: &Matches) {
    let owner = m.value_or("--owner", "code");
    run_lifecycle("storage-backup", &["--owner".to_string(), owner]);
}

pub fn storage_stop() {
    run_lifecycle("storage-stop", &[]);
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

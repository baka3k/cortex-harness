//! `dev mcp start/add` — MCP process lifecycle and agent-config integration.
//! PID discovery mirrors dev.py's `ps`-based scan; the process environment is
//! resolved through the Python layer (`_mcp_env_from_config`), and server
//! startup reuses `_mcp_start_one` verbatim.

use crate::parser::Matches;
use crate::pyexec;
use crate::util::{echo, echo_err, shlex_split};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

struct McpService {
    name: &'static str,
    rel_cmd0: &'static str,
    port: u16,
    pattern: &'static str,
    url: &'static str,
}

const SERVICES: [McpService; 2] = [
    McpService {
        name: "code-tiny",
        rel_cmd0: "mcp/unified_mcp.py",
        port: 8788,
        pattern: "unified_mcp.py",
        url: "http://127.0.0.1:8788/mcp",
    },
    McpService {
        name: "doc-tiny",
        rel_cmd0: "mcp_graph_rag.py",
        port: 8789,
        pattern: "mcp_graph_rag.py",
        url: "http://127.0.0.1:8789/mcp",
    },
];

pub fn start(m: &Matches) {
    let force_restart = m.flag("--force-restart");
    let project_dir = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));

    for svc in SERVICES {
        let pids = mcp_pids(svc.pattern, None);
        let dashes = "-".repeat(30);
        echo(&format!("\n-- {} (port {}) {}", svc.name, svc.port, dashes));

        if !pids.is_empty() && !force_restart {
            let uptime = pyexec::call_raw(
                "mcp_uptime",
                &json!({ "pid": pids[0] }),
            )
            .stdout
            .trim()
            .to_string();
            let uptime = if uptime.is_empty() { "?".to_string() } else { uptime };
            echo(&format!("  [running]  pid={}  uptime={}", pids[0], uptime));
            echo(&format!("  [url]      {}", svc.url));
            continue;
        }

        if !pids.is_empty() && force_restart {
            let stopped = pyexec::call_json("mcp_stop", &json!({ "pattern": svc.pattern }));
            let count = stopped.as_i64().unwrap_or(0);
            echo(&format!("  [stopped]  killed {} process(es)", count));
        }

        echo(&format!("  [starting] {}", svc.rel_cmd0));
        let result = pyexec::call_json(
            "mcp_start",
            &json!({ "name": svc.name, "project_dir": project_dir.to_string_lossy() }),
        );
        let status = result.get("status").and_then(|s| s.as_str()).unwrap_or("");
        if status == "started" {
            echo(&format!("  [ok]  url={}", svc.url));
            if let Some(pid) = result.get("pid") {
                echo(&format!("  [pid] {}", pid));
            }
            if let Some(log) = result.get("log").and_then(|l| l.as_str()) {
                echo(&format!("  [log] {}", log));
            }
        } else {
            let reason = result.get("reason").and_then(|r| r.as_str()).unwrap_or("?");
            echo_err(&format!("  [error] {}", reason));
        }
    }
}

/// dev.py `_mcp_pids` POSIX branch: scan `ps -ax -o pid=,command=`, keep
/// python processes whose command arguments contain the pattern file name.
pub fn mcp_pids(pattern: &str, instance_id: Option<&str>) -> Vec<i64> {
    let output = std::process::Command::new("ps")
        .args(["-ax", "-o", "pid=,command="])
        .output();
    let Ok(out) = output else { return Vec::new() };
    let mut pids = Vec::new();
    for line in String::from_utf8_lossy(&out.stdout).lines() {
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
            && parts[1..].iter().any(|arg| {
                Path::new(arg)
                    .file_name()
                    .map(|n| n == pattern)
                    .unwrap_or(false)
            })
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

fn pid_instance_id(pid: i64) -> Option<String> {
    let cache_dir = PathBuf::from(".cache");
    if let Ok(entries) = std::fs::read_dir(&cache_dir) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("dev-mcp-") || !name.ends_with(".pid") {
                continue;
            }
            if let Ok(text) = std::fs::read_to_string(entry.path()) {
                let mut recorded: Option<i64> = None;
                let mut instance: Option<String> = None;
                for line in text.lines() {
                    let s = line.trim();
                    if let Some(v) = s.strip_prefix("pid=") {
                        recorded = v.trim().parse().ok();
                    }
                    if let Some(v) = s.strip_prefix("instance_id=") {
                        instance = Some(v.trim().to_string());
                    }
                }
                if recorded == Some(pid) {
                    return instance.or_else(|| {
                        name.trim_end_matches(".pid")
                            .split('-')
                            .next_back()
                            .map(String::from)
                    });
                }
            }
        }
    }
    None
}

// ---------------------------------------------------------------------------
// mcp add
// ---------------------------------------------------------------------------

struct AgentConfig {
    name: &'static str,
    path: PathBuf,
    key: &'static str,
}

fn agent_configs() -> Vec<AgentConfig> {
    let home = dirs_home();
    let app_support = if cfg!(target_os = "macos") {
        home.join("Library").join("Application Support")
    } else if cfg!(windows) {
        std::env::var("APPDATA").map(PathBuf::from).unwrap_or(home.clone())
    } else {
        home.join(".config")
    };
    vec![
        AgentConfig {
            name: "claude",
            path: app_support.join("Claude").join("claude_desktop_config.json"),
            key: "mcpServers",
        },
        AgentConfig {
            name: "claude-code",
            path: home.join(".claude").join("settings.json"),
            key: "mcpServers",
        },
        AgentConfig {
            name: "vscode",
            path: app_support.join("Code").join("User").join("mcp.json"),
            key: "servers",
        },
        AgentConfig {
            name: "cursor",
            path: home.join(".cursor").join("mcp.json"),
            key: "mcpServers",
        },
    ]
}

fn dirs_home() -> PathBuf {
    std::env::var("HOME").map(PathBuf::from).unwrap_or_else(|_| PathBuf::from("/"))
}

pub fn add(m: &Matches) {
    let scope = m.value_or("--scope", "workspace");
    let agent = m.value_or("--agent", "all");
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));

    let entries: Value = json!(
        SERVICES
            .iter()
            .map(|s| (s.name.to_string(), json!({"type": "http", "url": s.url})))
            .collect::<serde_json::Map<String, Value>>()
    );

    if scope == "workspace" {
        integrate_workspace(&project_path, &entries);
        return;
    }

    echo("\n[warn] Modifying system-wide agent configuration files.\n");

    let configs = agent_configs();
    let targets: Vec<&AgentConfig> = if agent == "all" {
        configs.iter().collect()
    } else {
        match configs.iter().find(|c| c.name == agent) {
            Some(c) => vec![c],
            None => {
                let names: Vec<&str> = configs.iter().map(|c| c.name).collect();
                echo_err(&format!(
                    "[error] Unknown agent '{}'. Choose from: {} or 'all'",
                    agent,
                    names.join(", ")
                ));
                std::process::exit(1);
            }
        }
    };

    for cfg in targets {
        let dashes = "─".repeat(40);
        echo(&format!("── {} {}", cfg.name, dashes));

        if !cfg.path.parent().map(|p| p.exists()).unwrap_or(false) {
            echo(&format!(
                "  [skip] directory not found: {}",
                cfg.path.parent().unwrap_or(Path::new("")).display()
            ));
            continue;
        }

        let mut existing: Value = Value::Object(serde_json::Map::new());
        if cfg.path.exists() {
            match std::fs::read_to_string(&cfg.path)
                .ok()
                .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            {
                Some(v) => existing = v,
                None => echo(&format!(
                    "  [warn] could not parse {} — patching section only",
                    cfg.path.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()
                )),
            }
            let ts = timestamp_now();
            let bak = cfg.path.with_extension(format!("bak.{}.json", ts));
            let _ = std::fs::copy(&cfg.path, &bak);
            echo(&format!(
                "  [backup]  {}",
                bak.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()
            ));
        } else {
            echo(&format!("  [create]  {}", cfg.path.display()));
        }

        if !existing.is_object() {
            existing = json!({});
        }
        let obj = existing.as_object_mut().expect("object enforced above");
        let section = obj
            .entry(cfg.key.to_string())
            .or_insert_with(|| json!({}));
        let mut added = Vec::new();
        let mut updated = Vec::new();
        if let Some(section_obj) = section.as_object_mut() {
            for (svc_name, entry) in entries.as_object().unwrap() {
                match section_obj.get(svc_name) {
                    Some(current) if current == entry => {}
                    Some(_) => {
                        section_obj.insert(svc_name.clone(), entry.clone());
                        updated.push(svc_name.clone());
                    }
                    None => {
                        section_obj.insert(svc_name.clone(), entry.clone());
                        added.push(svc_name.clone());
                    }
                }
            }
        }

        if let Some(parent) = cfg.path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let text = serde_json::to_string_pretty(&existing).unwrap_or_default();
        let _ = std::fs::write(&cfg.path, text);

        if !added.is_empty() {
            echo(&format!("  [added]   {}", added.join(", ")));
        }
        if !updated.is_empty() {
            echo(&format!("  [updated] {}", updated.join(", ")));
        }
        if added.is_empty() && updated.is_empty() {
            echo("  [ok] already up to date");
        }
        echo(&format!("  [saved]   {}\n", cfg.path.display()));
    }
}

/// dev.py `_integrate_workspace`.
fn integrate_workspace(project_path: &Path, entries: &Value) {
    let mcp_file = project_path.join(".mcp.json");
    let mut existing: Value = Value::Object(serde_json::Map::new());

    if mcp_file.exists() {
        if let Some(v) = std::fs::read_to_string(&mcp_file)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        {
            existing = v;
        }
        let ts = timestamp_now();
        let bak = mcp_file.with_extension(format!("bak.{}.json", ts));
        let _ = std::fs::copy(&mcp_file, &bak);
        echo(&format!(
            "  [backup] {}",
            bak.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()
        ));
    }

    if !existing.is_object() {
        existing = json!({});
    }
    let obj = existing.as_object_mut().expect("object enforced above");
    let section = obj.entry("mcpServers".to_string()).or_insert_with(|| json!({}));
    let mut added = Vec::new();
    let mut updated = Vec::new();
    if let Some(section_obj) = section.as_object_mut() {
        for (svc_name, entry) in entries.as_object().unwrap() {
            match section_obj.get(svc_name) {
                Some(current) if current == entry => {}
                Some(_) => {
                    section_obj.insert(svc_name.clone(), entry.clone());
                    updated.push(svc_name.clone());
                }
                None => {
                    section_obj.insert(svc_name.clone(), entry.clone());
                    added.push(svc_name.clone());
                }
            }
        }
    }

    let text = serde_json::to_string_pretty(&existing).unwrap_or_default();
    let _ = std::fs::write(&mcp_file, text);

    echo(&format!("\n[workspace] {}", mcp_file.display()));
    if !added.is_empty() {
        echo(&format!("  [added]   {}", added.join(", ")));
    }
    if !updated.is_empty() {
        echo(&format!("  [updated] {}", updated.join(", ")));
    }
    if added.is_empty() && updated.is_empty() {
        echo("  [ok] already up to date");
    }
    echo("  [note] Restart Claude Code / your editor to apply changes.");
}

/// `datetime.now().strftime("%Y%m%d_%H%M%S")`.
pub fn timestamp_now() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    crate::util::local_strftime(now, "%Y%m%d_%H%M%S")
}

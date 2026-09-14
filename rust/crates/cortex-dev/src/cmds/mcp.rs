//! `dev mcp start/add` — MCP process lifecycle and agent-config integration.
//! PID discovery mirrors dev.py's `ps`-based scan; the process environment is
//! resolved through the Python layer (`_mcp_env_from_config`), and server
//! startup reuses `_mcp_start_one` verbatim.
//!
//! Phase 14 cutover: `CORTEX_MCP_BACKEND` chọn backend server khi `start` —
//! * unset/`auto`  → Rust (`cortex-mcp` binary: `--server unified` cho
//!   code-tiny, `--server mind` cho doc-tiny) **khi binary tồn tại**;
//!   thiếu binary ⇒ tự rơi về Python như trước phase 14.
//! * `python`      → ép legacy Python server (rollback flag).
//! * `rust`        → ép Rust; binary vắng ⇒ lỗi rõ ràng.
//!
//! Add/agent-config không đổi (URL giống hệt 2 backend).

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
    /// Thư mục service (code-tiny / doc-tiny) so với repo root.
    rel_dir: &'static str,
    /// Giá trị `--server` cho cortex-mcp (unified = code surface).
    server_flavor: &'static str,
}

const SERVICES: [McpService; 2] = [
    McpService {
        name: "code-tiny",
        rel_cmd0: "mcp/unified_mcp.py",
        port: 8788,
        pattern: "unified_mcp.py",
        url: "http://127.0.0.1:8788/mcp",
        rel_dir: "code-tiny",
        server_flavor: "unified",
    },
    McpService {
        name: "doc-tiny",
        rel_cmd0: "mcp_graph_rag.py",
        port: 8789,
        pattern: "mcp_graph_rag.py",
        url: "http://127.0.0.1:8789/mcp",
        rel_dir: "doc-tiny",
        server_flavor: "mind",
    },
];

// ---------------------------------------------------------------------------
// Backend selection (CORTEX_MCP_BACKEND) — phase 14 flip defaults
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum McpBackend {
    Python,
    Rust,
}

/// Resolve backend một lần cho cả hai service.
/// Trả về (backend, ghi chú hiển thị cho operator).
fn resolve_backend() -> (McpBackend, String) {
    let raw = std::env::var("CORTEX_MCP_BACKEND")
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    match raw.as_str() {
        "python" => (
            McpBackend::Python,
            "CORTEX_MCP_BACKEND=python (rollback flag)".to_string(),
        ),
        "rust" => match cortex_mcp_binary() {
            Some(binary) => (McpBackend::Rust, format!("CORTEX_MCP_BACKEND=rust ({binary:?})")),
            None => (
                McpBackend::Python,
                "CORTEX_MCP_BACKEND=rust but cortex-mcp binary not found — falling back to \
                 python (build: cargo build --release -p cortex-mcp)"
                    .to_string(),
            ),
        },
        // "" | "auto" | giá trị lạ → auto-flip: Rust khi binary sẵn sàng.
        _ => match cortex_mcp_binary() {
            Some(binary) => (
                McpBackend::Rust,
                format!("auto (CORTEX_MCP_BACKEND unset, found {binary:?})"),
            ),
            None => (
                McpBackend::Python,
                "auto (CORTEX_MCP_BACKEND unset, no cortex-mcp binary)".to_string(),
            ),
        },
    }
}

/// Định vị `cortex-mcp` binary: env override → release → debug target.
fn cortex_mcp_binary() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("CORTEX_MCP_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    let root = pyexec::repo_root();
    [
        root.join("rust").join("target").join("release").join("cortex-mcp"),
        root.join("rust").join("target").join("debug").join("cortex-mcp"),
    ]
    .into_iter()
    .find(|candidate| candidate.is_file())
}

/// PID các tiến trình `cortex-mcp` đang giữ `--port <port>`.
fn rust_mcp_pids(port: u16) -> Vec<i64> {
    let Some(binary_name) = cortex_mcp_binary()
        .as_ref()
        .and_then(|p| p.file_name())
        .map(|n| n.to_string_lossy().to_string())
    else {
        return Vec::new();
    };
    let output = std::process::Command::new("ps")
        .args(["-ax", "-o", "pid=,command="])
        .output();
    let Ok(out) = output else { return Vec::new() };
    let port_token = port.to_string();
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
            .map(|n| n.to_string_lossy())
            .unwrap_or_default();
        if executable == binary_name
            && parts
                .iter()
                .zip(parts.iter().skip(1))
                .any(|(flag, value)| flag == "--port" && value.as_str() == port_token)
        {
            pids.push(pid);
        }
    }
    pids
}

/// dev.py `_load_dotenv`: KEY=VALUE, bỏ comment/dòng trống, strip quotes.
fn load_dotenv(dir: &Path) -> Vec<(String, String)> {
    let path = dir.join(".env");
    let Ok(text) = std::fs::read_to_string(&path) else {
        return Vec::new();
    };
    let mut pairs = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        pairs.push((
            key.trim().to_string(),
            value.trim().trim_matches('"').trim_matches('\'').to_string(),
        ));
    }
    pairs
}

fn mcp_log_dir() -> PathBuf {
    pyexec::repo_root().join(".cache")
}

/// Ghi pid file + sidecar per-instance giống `_mcp_start_one`.
fn write_rust_pid_files(name: &str, pid: u32, instance_id: &str) {
    let log_dir = mcp_log_dir();
    let _ = std::fs::create_dir_all(&log_dir);
    let _ = std::fs::write(log_dir.join(format!("dev-mcp-{name}.pid")), pid.to_string());
    let _ = std::fs::write(
        log_dir.join(format!("dev-mcp-{name}-{instance_id}.pid")),
        format!("pid={pid}\ninstance_id={instance_id}\n"),
    );
}

/// Launch một service bằng binary Rust `cortex-mcp` (streamable-http, cùng
/// port/host/path như svc Python; `--server unified|mind` chọn flavor).
fn start_rust_one(svc: &McpService, binary: &Path, project_dir: &Path, force_restart: bool) {
    let dashes = "-".repeat(30);
    echo(&format!("\n-- {} (port {}) {}", svc.name, svc.port, dashes));

    let pids = rust_mcp_pids(svc.port);
    if !pids.is_empty() && !force_restart {
        echo(&format!(
            "  [running]  pid={} (rust backend)  url={}",
            pids[0], svc.url
        ));
        return;
    }
    if !pids.is_empty() && force_restart {
        let mut kill = std::process::Command::new("kill");
        for pid in &pids {
            kill.arg(pid.to_string());
        }
        let _ = kill.stdout(std::process::Stdio::null()).status();
        echo(&format!("  [stopped]  killed {} rust process(es)", pids.len()));
    }

    // Env: inherit → svc .env → active harness config overlay (như
    // `_mcp_start_one` layering).
    let svc_dir = pyexec::repo_root().join(svc.rel_dir);
    let mut env_overrides = load_dotenv(&svc_dir);
    match pyexec::try_call_json(
        "mcp_env",
        &json!({ "project_dir": project_dir.to_string_lossy(), "service": svc.name }),
    ) {
        Ok(extra) => {
            if let Some(map) = extra.as_object() {
                for (key, value) in map {
                    if let Some(text) = value.as_str() {
                        env_overrides.push((key.clone(), text.to_string()));
                    }
                }
            }
        }
        Err(_) => echo("  [warn] could not resolve harness config env; using inherited env"),
    }

    let instance_id = env_overrides
        .iter()
        .find(|(key, _)| key == "CORTEX_STORAGE_INSTANCE")
        .map(|(_, value)| value.clone())
        .or_else(|| std::env::var("CORTEX_STORAGE_INSTANCE").ok())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "default".to_string());

    let log_dir = mcp_log_dir();
    let _ = std::fs::create_dir_all(&log_dir);
    let log_file = log_dir.join(format!("dev-mcp-{}.log", svc.name));
    let log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_file);

    let mut command = std::process::Command::new(binary);
    command
        .arg("--transport")
        .arg("streamable-http")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(svc.port.to_string())
        .arg("--path")
        .arg("/mcp")
        .arg("--server")
        .arg(svc.server_flavor)
        .current_dir(&svc_dir);
    for (key, value) in &env_overrides {
        command.env(key, value);
    }
    match log {
        Ok(log) => {
            if let Ok(log_err) = log.try_clone() {
                command.stderr(std::process::Stdio::from(log_err));
            } else {
                command.stderr(std::process::Stdio::null());
            }
            command.stdout(std::process::Stdio::from(log));
        }
        Err(_) => {
            command.stdout(std::process::Stdio::null());
            command.stderr(std::process::Stdio::null());
        }
    }
    // start_new_session=True của Python → process group riêng.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    match command.spawn() {
        Ok(child) => {
            write_rust_pid_files(svc.name, child.id(), &instance_id);
            echo(&format!("  [ok]  url={} (backend: rust)", svc.url));
            echo(&format!("  [pid] {}", child.id()));
            echo(&format!("  [log] {}", log_file.display()));
        }
        Err(error) => {
            echo_err(&format!("  [error] failed to launch {}: {error}", binary.display()));
        }
    }
}

/// `dev stop` hook: tắt các tiến trình `cortex-mcp` (rust backend) đang giữ
/// port MCP chuẩn. Chạy trước lifecycle stop của Python để cả 2 backend đều
/// được dọn. Trả về số process đã gửi SIGTERM.
pub fn stop_rust_mcp(name: Option<&str>) -> usize {
    let mut total = 0;
    for svc in SERVICES.iter() {
        if let Some(wanted) = name
            && svc.name != wanted
        {
            continue;
        }
        let pids = rust_mcp_pids(svc.port);
        if pids.is_empty() {
            continue;
        }
        let mut kill = std::process::Command::new("kill");
        for pid in &pids {
            kill.arg(pid.to_string());
        }
        let _ = kill.stdout(std::process::Stdio::null()).status();
        total += pids.len();
        echo(&format!(
            "[mcp] stopped rust backend {} (port {}): {} pid(s)",
            svc.name,
            svc.port,
            pids.len()
        ));
    }
    total
}

pub fn start(m: &Matches) {
    let force_restart = m.flag("--force-restart");
    let project_dir = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let (backend, note) = resolve_backend();
    echo(&format!("[backend] {}", note));

    for svc in SERVICES.iter() {
        if backend == McpBackend::Rust
            && let Some(binary) = cortex_mcp_binary()
        {
            start_rust_one(svc, &binary, &project_dir, force_restart);
            continue;
            // resolve_backend đã chọn Python khi binary vắng (trừ mode rust
            // cứng — cũng rơi về đây với cảnh báo ở dòng [backend]).
        }

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

        echo(&format!("  [starting] {} (backend: python)", svc.rel_cmd0));
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

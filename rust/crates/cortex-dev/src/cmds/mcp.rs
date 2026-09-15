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
    let root = crate::util::repo_root();
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
    crate::util::repo_root().join(".cache")
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
    let svc_dir = crate::util::repo_root().join(svc.rel_dir);
    let mut env_overrides = load_dotenv(&svc_dir);
    let extra = crate::env::mcp_env_from_config(project_dir, svc.name);
    if extra.is_empty() {
        echo("  [warn] could not resolve harness config env; using inherited env");
    }
    for (key, value) in &extra {
        if let Some(text) = value.as_str() {
            env_overrides.push((key.clone(), text.to_string()));
        }
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
            let uptime = crate::procinfo::mcp_uptime(pids[0]);
            echo(&format!("  [running]  pid={}  uptime={}", pids[0], uptime));
            echo(&format!("  [url]      {}", svc.url));
            continue;
        }

        if !pids.is_empty() && force_restart {
            let count = crate::procinfo::mcp_stop_pattern(svc.pattern, None);
            echo(&format!("  [stopped]  killed {} process(es)", count));
        }

        echo(&format!("  [starting] {} (backend: python)", svc.rel_cmd0));
        let extra_env = crate::env::mcp_env_from_config(&project_dir, svc.name);
        let result = mcp_start_one(svc.name, &extra_env);
        if result.status == "started" {
            echo(&format!("  [ok]  url={}", svc.url));
            echo(&format!("  [pid] {}", result.pid.map(|p| p.to_string()).unwrap_or_else(|| "?".to_string())));
            echo(&format!("  [log] {}", result.log));
        } else {
            echo_err(&format!("  [error] {}", result.reason));
        }
    }
}

/// dev.py `_mcp_pids` POSIX branch — delegated to the native procinfo module.
pub fn mcp_pids(pattern: &str, instance_id: Option<&str>) -> Vec<i64> {
    crate::procinfo::mcp_pids(pattern, instance_id)
}

/// Shape-parity with the `_mcp_start_one` return dict (phase-02 port);
/// some fields are only consumed by callers that mirror the Python output.
#[allow(dead_code)]
pub struct McpStartResult {
    pub name: String,
    pub status: String,
    pub pid: Option<u32>,
    pub url: String,
    pub log: String,
    pub reason: String,
}

/// Fixed launch args per service, mirroring dev.py `MCP_SERVICES[*]["cmd"]`
/// (flag order included so child argv matches the Python launcher).
fn service_args(name: &str, port: &str) -> Vec<&'static str> {
    if name == "code-tiny" {
        vec![
            "--transport", "streamable-http",
            "--host", "127.0.0.1",
            "--port", "8788",
            "--path", "/mcp",
        ]
    } else {
        let _ = port;
        vec![
            "--host", "127.0.0.1",
            "--port", "8789",
            "--transport", "streamable-http",
            "--path", "/mcp",
        ]
    }
}

/// dev.py `_mcp_start_one` — spawn the legacy Python MCP server for `name`
/// (`code-tiny` / `doc-tiny` from the SERVICES catalog): entry script +
/// harness venv, env = inherit -> service .env -> harness config overlay,
/// provider isolation, remote-key hygiene, log redirect, pid + sidecar files.
pub fn mcp_start_one(
    name: &str,
    extra_env: &serde_json::Map<String, serde_json::Value>,
) -> McpStartResult {
    use std::process::Stdio;

    let Some(svc) = SERVICES.iter().find(|s| s.name == name) else {
        return McpStartResult {
            name: name.to_string(),
            status: "error".to_string(),
            pid: None,
            url: String::new(),
            log: String::new(),
            reason: format!("unknown service: {name}"),
        };
    };
    let svc_dir = crate::util::repo_root().join(svc.rel_dir);
    let entry_script = svc_dir.join(svc.rel_cmd0);
    if !entry_script.is_file() {
        return McpStartResult {
            name: name.to_string(),
            status: "error".to_string(),
            pid: None,
            url: String::new(),
            log: String::new(),
            reason: format!("entry not found: {}", entry_script.display()),
        };
    }

    // env = {**os.environ, **dotenv, **extra_env}
    let mut env: std::collections::BTreeMap<String, String> = std::env::vars().collect();
    for (key, value) in load_dotenv(&svc_dir) {
        env.insert(key, value);
    }
    for (key, value) in extra_env {
        if let Some(text) = value.as_str() {
            env.insert(key.clone(), text.to_string());
        }
    }

    // Provider isolation over the merged env (graph-provider scoping).
    let scoped_provider = if name == "doc-tiny" {
        "DOC_GRAPH_PROVIDER"
    } else {
        "CODE_GRAPH_PROVIDER"
    };
    let mut provider_map: std::collections::BTreeMap<String, serde_json::Value> = env
        .iter()
        .map(|(k, v)| (k.clone(), serde_json::Value::String(v.clone())))
        .collect();
    let provider =
        crate::env::isolate_graph_provider_environment(&mut provider_map, scoped_provider);
    let mut env: std::collections::BTreeMap<String, String> = provider_map
        .into_iter()
        .map(|(k, v)| {
            let text = match v {
                serde_json::Value::String(s) => s,
                other => other.to_string(),
            };
            (k, text)
        })
        .collect();
    if provider == "falkordb" {
        // Explicit remote endpoints handed to the launcher always win over
        // inherited stale ones; otherwise strip remote keys entirely.
        let mut explicit_remote: Vec<(String, String)> = Vec::new();
        for (key, value) in extra_env {
            let Some(text) = value.as_str() else { continue };
            if crate::env::REMOTE_STORAGE_KEYS.contains(&key.as_str()) && !text.trim().is_empty() {
                explicit_remote.push((key.clone(), text.to_string()));
            }
        }
        for key in crate::env::REMOTE_STORAGE_KEYS {
            env.remove(key);
        }
        let mut has_uri = false;
        for (key, value) in explicit_remote {
            if key == "FALKORDB_URI" || key == "FALKORDB_URL" {
                has_uri = true;
            }
            env.insert(key, value);
        }
        if has_uri {
            for key in ["FALKORDB_PATH", "FALKORDB_CODE_PATH", "FALKORDB_DOC_PATH"] {
                env.remove(key);
            }
        }
    }

    let log_dir = mcp_log_dir();
    let _ = std::fs::create_dir_all(&log_dir);
    let log_file = log_dir.join(format!("dev-mcp-{name}.log"));
    let pid_file = log_dir.join(format!("dev-mcp-{name}.pid"));
    let instance_id = env
        .get("CORTEX_STORAGE_INSTANCE")
        .cloned()
        .or_else(|| std::env::var("CORTEX_STORAGE_INSTANCE").ok())
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| "default".to_string());
    let sidecar_pid_file = log_dir.join(format!("dev-mcp-{name}-{instance_id}.pid"));

    let log = std::fs::OpenOptions::new().create(true).append(true).open(&log_file);
    let port = svc.port.to_string();
    let mut command = std::process::Command::new(crate::util::harness_python(&svc_dir));
    command
        .arg(&entry_script)
        .args(service_args(name, &port))
        .current_dir(&svc_dir);
    for (key, value) in &env {
        command.env(key, value);
    }
    match log {
        Ok(log) => {
            if let Ok(log_err) = log.try_clone() {
                command.stderr(Stdio::from(log_err));
            } else {
                command.stderr(Stdio::null());
            }
            command.stdout(Stdio::from(log));
        }
        Err(_) => {
            command.stdout(Stdio::null());
            command.stderr(Stdio::null());
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    match command.spawn() {
        Ok(child) => {
            let pid = child.id();
            let _ = std::fs::write(&pid_file, pid.to_string());
            let _ = std::fs::write(
                &sidecar_pid_file,
                format!("pid={pid}\ninstance_id={instance_id}\n"),
            );
            McpStartResult {
                name: name.to_string(),
                status: "started".to_string(),
                pid: Some(pid),
                url: svc.url.to_string(),
                log: log_file.to_string_lossy().to_string(),
                reason: String::new(),
            }
        }
        Err(error) => McpStartResult {
            name: name.to_string(),
            status: "error".to_string(),
            pid: None,
            url: svc.url.to_string(),
            log: String::new(),
            reason: format!("failed to launch: {error}"),
        },
    }
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

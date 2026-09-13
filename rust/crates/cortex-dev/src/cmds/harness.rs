//! `dev harness` — .harness/ bootstrap, task backlog, orchestrator delegates.

use crate::parser::Matches;
use crate::pyexec;
use crate::util::{echo, echo_err};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::Command;

fn harness_dir(project_dir: &Path) -> PathBuf {
    project_dir.join(".harness")
}

fn feature_list(project_dir: &Path) -> PathBuf {
    harness_dir(project_dir).join("state").join("feature_list.json")
}

fn load_features(project_dir: &Path) -> Value {
    let p = feature_list(project_dir);
    if !p.exists() {
        echo_err(&format!(
            "[error] No feature_list.json at '{}'. Run 'dev harness init' first.",
            p.display()
        ));
        std::process::exit(1);
    }
    std::fs::read_to_string(&p)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| json!({}))
}

fn save_features(project_dir: &Path, payload: &Value) {
    let p = feature_list(project_dir);
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // json.dump(..., indent=2, ensure_ascii=False) + trailing newline.
    let text = serde_json::to_string_pretty(payload).unwrap_or_default();
    let _ = std::fs::write(p, format!("{}\n", text));
}

fn next_task_id(features: &[Value]) -> String {
    let mut nums = Vec::new();
    for t in features {
        if let Some(n) = t
            .get("id")
            .and_then(|i| i.as_str())
            .and_then(|tid| tid.strip_prefix("task-"))
            .and_then(|rest| rest.parse::<i64>().ok())
        {
            nums.push(n);
        }
    }
    let n = nums.iter().max().map(|m| m + 1).unwrap_or(1);
    format!("task-{:03}", n)
}

fn scripts_dir() -> PathBuf {
    crate::pyexec::repo_root().join("harness").join("scripts")
}

fn templates_dir() -> PathBuf {
    crate::pyexec::repo_root().join("harness").join("templates")
}

// ---------------------------------------------------------------------------
// init
// ---------------------------------------------------------------------------

pub fn init(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let h_dir = harness_dir(&project_path);

    if !scripts_dir().exists() {
        echo_err(&format!(
            "[error] harness/scripts not found at '{}'",
            scripts_dir().display()
        ));
        std::process::exit(1);
    }

    echo(&format!(
        "\n─── Bootstrapping .harness/ in: {} ───",
        project_path.display()
    ));

    for sub in ["scripts", "templates", "state", "state/session_log"] {
        let _ = std::fs::create_dir_all(h_dir.join(sub));
    }

    for script in ["init.sh", "verify.sh", "context_selector.py", "orchestrator.py"] {
        let src = scripts_dir().join(script);
        let dst = h_dir.join("scripts").join(script);
        if dst.exists() {
            echo(&format!("  [skip]   scripts/{} (already exists)", script));
        } else {
            let _ = std::fs::copy(&src, &dst);
            if script.ends_with(".sh")
                .then(|| std::fs::metadata(&dst).ok())
                .flatten()
                .is_some()
            {
                let mut perms = std::fs::metadata(&dst).unwrap().permissions();
                use std::os::unix::fs::PermissionsExt;
                perms.set_mode(perms.mode() | 0o111);
                let _ = std::fs::set_permissions(&dst, perms);
            }
            echo(&format!("  [copied] scripts/{}", script));
        }
    }

    for tmpl in ["feature_template.json", "session_template.json", "AGENT.md"] {
        let src = templates_dir().join(tmpl);
        let dst = h_dir.join("templates").join(tmpl);
        if dst.exists() {
            echo(&format!("  [skip]   templates/{} (already exists)", tmpl));
        } else {
            let _ = std::fs::copy(&src, &dst);
            echo(&format!("  [copied] templates/{}", tmpl));
        }
    }

    let fl = feature_list(&project_path);
    if fl.exists() {
        echo("  [skip]   state/feature_list.json (already exists)");
    } else {
        let tmpl_fl = templates_dir().join("state").join("feature_list.json");
        if tmpl_fl.exists() {
            let _ = std::fs::copy(&tmpl_fl, &fl);
        } else if let Some(parent) = fl.parent() {
            let _ = std::fs::create_dir_all(parent);
            let _ = std::fs::write(&fl, "{\n  \"features\": []\n}\n");
        }
        echo("  [created] state/feature_list.json");
    }

    let progress_md = h_dir.join("state").join("progress.md");
    if progress_md.exists() {
        echo("  [skip]   state/progress.md (already exists)");
    } else {
        let tmpl_prog = templates_dir().join("progress.md");
        if tmpl_prog.exists() {
            let _ = std::fs::copy(&tmpl_prog, &progress_md);
            echo("  [copied] state/progress.md");
        }
    }

    let handoff_tmpl = h_dir.join("templates").join("session-handoff.md");
    if handoff_tmpl.exists() {
        echo("  [skip]   templates/session-handoff.md (already exists)");
    } else {
        let tmpl_ho = templates_dir().join("session-handoff.md");
        if tmpl_ho.exists() {
            let _ = std::fs::copy(&tmpl_ho, &handoff_tmpl);
            echo("  [copied] templates/session-handoff.md");
        }
    }

    let cfg_path = h_dir.join("config.yaml");
    if cfg_path.exists() {
        echo("  [skip]   config.yaml (already exists)");
    } else {
        echo("\n─── MCP configuration ──────────────────────────");
        let graph_url = crate::util::prompt(
            "  graph_mcp_url (code-tiny)",
            "http://127.0.0.1:8788/mcp",
        );
        let mind_url = crate::util::prompt("  mind_mcp_url  (doc-tiny)", "http://127.0.0.1:8789/mcp");
        echo("\n─── Verify commands (blank = skip) ─────────────");
        let test_cmd = crate::util::prompt("  critical test_cmd", "");
        let lint_cmd = crate::util::prompt("  critical lint_cmd", "");
        let type_cmd = crate::util::prompt("  critical type_cmd", "");

        let cfg_text = std::fs::read_to_string(templates_dir().join("config.yaml"))
            .unwrap_or_default()
            .replace(
                "graph_mcp_url: \"http://127.0.0.1:8788/mcp\"",
                &format!("graph_mcp_url: \"{}\"", graph_url),
            )
            .replace(
                "mind_mcp_url: \"http://127.0.0.1:8789/mcp\"",
                &format!("mind_mcp_url: \"{}\"", mind_url),
            )
            .replace("    test_cmd: \"\"", &format!("    test_cmd: \"{}\"", test_cmd))
            .replace("    lint_cmd: \"\"", &format!("    lint_cmd: \"{}\"", lint_cmd))
            .replace("    type_cmd: \"\"", &format!("    type_cmd: \"{}\"", type_cmd));
        let _ = std::fs::write(&cfg_path, cfg_text);
        echo("  [created] config.yaml");
    }

    let claude_dir = project_path.join(".claude");
    let claude_settings = claude_dir.join("settings.json");
    let tmpl_claude_settings = templates_dir().join(".claude").join("settings.json");
    if claude_settings.exists() {
        echo("  [skip]   .claude/settings.json (already exists)");
    } else if tmpl_claude_settings.exists() {
        let _ = std::fs::create_dir_all(&claude_dir);
        let _ = std::fs::copy(&tmpl_claude_settings, &claude_settings);
        echo("  [created] .claude/settings.json (Claude Code hooks)");
    } else {
        echo("  [skip]   .claude/settings.json (template not found)");
    }

    let manifest_path = h_dir.join("harness_manifest.json");
    let manifest = json!({
        "generated_at_utc": crate::util::iso_utc_now(),
        "project_root": project_path.to_string_lossy(),
        "cortex_harness_version": "cli",
        "subsystems": ["instructions", "state", "verification", "scope", "lifecycle"],
    });
    let text = serde_json::to_string_pretty(&manifest).unwrap_or_default();
    let _ = std::fs::write(&manifest_path, format!("{}\n", text));
    echo("  [created] harness_manifest.json");

    echo(&format!("\n[ok] .harness/ is ready at: {}", h_dir.display()));
    echo("     Next steps:");
    echo("       dev harness task add        # add your first task");
    echo("       dev mcp start               # start MCP servers");
    echo("       dev harness run             # run orchestrator");
    echo("       /cortex-harness-session     # invoke skill at session start");
}

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

pub fn status(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let payload = load_features(&project_path);
    let features = payload
        .get("features")
        .and_then(|f| f.as_array())
        .cloned()
        .unwrap_or_default();

    let mut counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
    for t in &features {
        let s = t.get("status").and_then(|x| x.as_str()).unwrap_or("unknown").to_string();
        *counts.entry(s).or_insert(0) += 1;
    }

    echo(&format!(
        "\n── Harness status: {} ─────────────────",
        project_path.display()
    ));
    echo(&format!("  Tasks total : {}", features.len()));
    for s in ["todo", "in_progress", "done", "blocked"] {
        echo(&format!("  {:<12}: {}", s, counts.get(s).copied().unwrap_or(0)));
    }

    if !features.is_empty() {
        let in_prog = features
            .iter()
            .find(|t| t.get("status").and_then(|x| x.as_str()) == Some("in_progress"));
        if let Some(t) = in_prog {
            echo(&format!(
                "\n  In-progress : [{}] {}",
                t.get("id").and_then(|x| x.as_str()).unwrap_or("?"),
                t.get("title").and_then(|x| x.as_str()).unwrap_or("")
            ));
        }
    }

    let cfg = read_config_yaml(&project_path);
    let mcp_cfg = cfg.get("mcp").cloned().unwrap_or(json!({}));
    let graph_url = mcp_cfg
        .get("graph_mcp_url")
        .and_then(|v| v.as_str())
        .unwrap_or("http://127.0.0.1:8788/mcp");
    let mind_url = mcp_cfg
        .get("mind_mcp_url")
        .and_then(|v| v.as_str())
        .unwrap_or("http://127.0.0.1:8789/mcp");

    echo("\n── MCP endpoints ───────────────────────────────────");
    echo(&format!("  graph_mcp  {}", graph_url));
    echo(&format!("             → {}", probe_mcp(graph_url)));
    echo(&format!("  mind_mcp   {}", mind_url));
    echo(&format!("             → {}", probe_mcp(mind_url)));
}

/// Minimal HTTP probe of an MCP endpoint (dev.py uses urllib with 5s timeout).
fn probe_mcp(url: &str) -> String {
    if url.is_empty() {
        return "not configured".to_string();
    }
    let payload = json!({
        "jsonrpc": "2.0", "id": "probe", "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "dev-harness-probe", "version": "0.1"}
        }
    })
    .to_string();
    probe_http_post(url, &payload)
}

fn probe_http_post(url: &str, body: &str) -> String {
    let without_scheme = url.split("://").nth(1).unwrap_or(url);
    let (host_port, path) = match without_scheme.find('/') {
        Some(i) => (&without_scheme[..i], &without_scheme[i..]),
        None => (without_scheme, "/"),
    };
    let request = format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        path,
        host_port,
        body.len(),
        body
    );
    use std::io::{Read, Write};
    let attempt = || -> std::io::Result<String> {
        let host = host_port.split(':').next().unwrap_or("127.0.0.1");
        let port = host_port
            .rsplit_once(':')
            .and_then(|(_, p)| p.parse::<u16>().ok())
            .unwrap_or(80);
        let addr = format!("{}:{}", host, port);
        use std::net::ToSocketAddrs;
        let addrs: Vec<_> = addr.to_socket_addrs()?.collect();
        let mut stream = std::net::TcpStream::connect_timeout(
            addrs.first().ok_or_else(|| std::io::Error::other("no address"))?,
            std::time::Duration::from_secs(5),
        )?;
        stream.set_read_timeout(Some(std::time::Duration::from_secs(5)))?;
        stream.write_all(request.as_bytes())?;
        let mut buf = [0u8; 256];
        let n = stream.read(&mut buf).unwrap_or(0);
        Ok(String::from_utf8_lossy(&buf[..n]).to_string())
    };
    match attempt() {
        Ok(resp) => {
            let status_line = resp.lines().next().unwrap_or("");
            status_line
                .split_whitespace()
                .nth(1)
                .map(|code| format!("ok (HTTP {})", code))
                .unwrap_or_else(|| "unreachable (invalid response)".to_string())
        }
        Err(e) => format!("unreachable ({})", e),
    }
}

// ---------------------------------------------------------------------------
// task list / add / show
// ---------------------------------------------------------------------------

pub fn task_list(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let payload = load_features(&project_path);
    let features = payload
        .get("features")
        .and_then(|f| f.as_array())
        .cloned()
        .unwrap_or_default();

    if features.is_empty() {
        echo("[info] No tasks yet. Use 'dev harness task add' to create one.");
        return;
    }

    let icon = |s: &str| match s {
        "todo" => "○",
        "in_progress" => "◎",
        "done" => "✓",
        "blocked" => "✗",
        _ => "?",
    };
    echo(&format!("\n{:<12} {:<2} {:<3} {:<10} TITLE", "ID", "ST", "P", "TYPE"));
    echo(&"─".repeat(70));
    for t in &features {
        let s = t.get("status").and_then(|x| x.as_str()).unwrap_or("");
        echo(&format!(
            "{:<12} {:<2} {:<3} {:<10} {}",
            t.get("id").and_then(|x| x.as_str()).unwrap_or("?"),
            icon(s),
            t.get("priority").map(|p| p.to_string()).unwrap_or_default(),
            t.get("type").and_then(|x| x.as_str()).unwrap_or(""),
            t.get("title").and_then(|x| x.as_str()).unwrap_or("")
        ));
    }
}

pub fn task_add(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let mut payload = load_features(&project_path);
    let mut features = payload
        .get("features")
        .and_then(|f| f.as_array())
        .cloned()
        .unwrap_or_default();

    let task_id = next_task_id(&features);

    echo(&format!(
        "\n─── Add task {} ─────────────────────────────────",
        task_id
    ));
    let title = crate::util::prompt_required("  Title");
    let task_type = crate::util::prompt("  Type (feature, bugfix, refactor)", "feature");
    let priority_raw = crate::util::prompt("  Priority", "1");
    let priority: i64 = priority_raw.parse().unwrap_or(1);
    let entry_node = crate::util::prompt("  Graph entry node (namespace.Symbol)", "");
    let modules_raw = crate::util::prompt("  Related modules (comma-separated)", "");
    let files_raw = crate::util::prompt("  Related files   (comma-separated)", "");
    let notes = crate::util::prompt("  Notes", "");

    let split_list = |s: &str| -> Vec<String> {
        s.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect()
    };

    let task = json!({
        "id": task_id,
        "title": title,
        "type": task_type,
        "status": "todo",
        "priority": priority,
        "graph_entry_node": if entry_node.is_empty() { Value::Null } else { json!(entry_node) },
        "related_modules": split_list(&modules_raw),
        "related_files": split_list(&files_raw),
        "notes": notes,
        "session_id": Value::Null,
    });
    features.push(task);
    payload["features"] = Value::Array(features);
    save_features(&project_path, &payload);
    echo(&format!(
        "\n[ok] Task '{}' added → {}",
        task_id,
        feature_list(&project_path).display()
    ));
}

pub fn task_show(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let payload = load_features(&project_path);
    let features = payload
        .get("features")
        .and_then(|f| f.as_array())
        .cloned()
        .unwrap_or_default();
    let task_id = m.positionals().first().cloned().unwrap_or_default();

    for t in &features {
        if t.get("id").and_then(|x| x.as_str()) == Some(task_id.as_str()) {
            let text = serde_json::to_string_pretty(t).unwrap_or_default();
            echo(&text);
            return;
        }
    }
    echo_err(&format!("[error] Task '{}' not found.", task_id));
    std::process::exit(1);
}

// ---------------------------------------------------------------------------
// run / context / verify
// ---------------------------------------------------------------------------

pub fn run(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let orchestrator = project_path.join(".harness").join("scripts").join("orchestrator.py");

    if !orchestrator.exists() {
        echo_err("[error] orchestrator.py not found. Run 'dev harness init' first.");
        std::process::exit(1);
    }

    let python = pyexec::venv_python(&pyexec::repo_root());
    let mut cmd = Command::new(&python);
    cmd.arg(&orchestrator)
        .args(["--root", &project_path.to_string_lossy()])
        .args(["--config", ".harness/config.yaml"])
        .args(["--state", ".harness/state/feature_list.json"])
        .args(["--progress", ".harness/state/progress.md"])
        .args(["--session-log-dir", ".harness/state/session_log"]);

    if let Some(task_id) = m.value("--task-id") {
        cmd.args(["--task-id", task_id]);
    }
    if let Some(max_rounds) = m.value("--max-rounds") {
        cmd.args(["--max-rounds", max_rounds]);
    }
    if let Some(agent_command) =
        m.value("--agent-command").filter(|c| !c.is_empty())
    {
        cmd.args(["--agent-command", agent_command]);
    }

    let short = format!(
        "[harness run] {} {} {} …",
        python,
        orchestrator.display(),
        "--root"
    );
    echo(&short);
    let code = cmd.current_dir(&project_path).status().map(|s| s.code().unwrap_or(1)).unwrap_or(1);
    std::process::exit(code);
}

pub fn context(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let selector = project_path.join(".harness").join("scripts").join("context_selector.py");

    if !selector.exists() {
        echo_err("[error] context_selector.py not found. Run 'dev harness init' first.");
        std::process::exit(1);
    }

    let cfg = read_config_yaml(&project_path);
    let mcp = cfg.get("mcp").cloned().unwrap_or(json!({}));
    let task_id = m.positionals().first().cloned().unwrap_or_default();
    let output = m.value_or("--output", "-");

    let python = pyexec::venv_python(&pyexec::repo_root());
    let mut cmd = Command::new(python);
    cmd.arg(&selector)
        .args(["--state", ".harness/state/feature_list.json"])
        .args(["--task-id", &task_id])
        .args(["--output", &output])
        .args([
            "--graph-mcp-url",
            mcp.get("graph_mcp_url").and_then(|v| v.as_str()).unwrap_or("http://127.0.0.1:8788/mcp"),
        ])
        .args([
            "--mind-mcp-url",
            mcp.get("mind_mcp_url").and_then(|v| v.as_str()).unwrap_or("http://127.0.0.1:8789/mcp"),
        ]);
    if let Some(tool) = mcp
        .get("graph_mcp_tool")
        .and_then(|v| v.as_str())
        .filter(|t| !t.is_empty())
    {
        cmd.args(["--graph-mcp-tool", tool]);
    }
    if let Some(tool) = mcp
        .get("mind_mcp_tool")
        .and_then(|v| v.as_str())
        .filter(|t| !t.is_empty())
    {
        cmd.args(["--mind-mcp-tool", tool]);
    }

    echo(&format!("[harness context] task={}", task_id));
    let code = cmd.current_dir(&project_path).status().map(|s| s.code().unwrap_or(1)).unwrap_or(1);
    std::process::exit(code);
}

pub fn verify(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let verify_sh = project_path.join(".harness").join("scripts").join("verify.sh");

    if !verify_sh.exists() {
        echo_err("[error] verify.sh not found. Run 'dev harness init' first.");
        std::process::exit(1);
    }

    let cfg = read_config_yaml(&project_path);
    let verify_cfg = cfg
        .get("verify")
        .and_then(|v| v.get("critical"))
        .cloned()
        .unwrap_or(json!({}));

    let mut command = Command::new("bash");
    command.arg(&verify_sh).current_dir(&project_path);
    for (key, env_name) in [
        ("test_cmd", "CRITICAL_TEST_CMD"),
        ("lint_cmd", "CRITICAL_LINT_CMD"),
        ("type_cmd", "CRITICAL_TYPE_CMD"),
    ] {
        if let Some(v) = verify_cfg
            .get(key)
            .and_then(|x| x.as_str())
            .filter(|x| !x.is_empty())
        {
            command.env(env_name, v);
        }
    }

    echo("[harness verify] running verify.sh");
    let code = command
        .status()
        .map(|s| s.code().unwrap_or(1))
        .unwrap_or(1);
    std::process::exit(code);
}

// ---------------------------------------------------------------------------
// config.yaml mini-parser (same subset as dev.py `_harness_read_config_yaml`)
// ---------------------------------------------------------------------------

pub fn read_config_yaml(project_dir: &Path) -> Value {
    let cfg_path = harness_dir(project_dir).join("config.yaml");
    if !cfg_path.exists() {
        return json!({});
    }
    let text = std::fs::read_to_string(&cfg_path).unwrap_or_default();

    let mut root = serde_json::Map::new();
    // Stack of (indent, key path); mirrors dev.py's (indent, dict) stack.
    let mut stack: Vec<(i64, Vec<String>)> = vec![(-1, Vec::new())];

    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with('-') {
            continue;
        }
        let indent = (line.len() - line.trim_start_matches(' ').len()) as i64;
        let Some((key, value)) = trimmed.split_once(':') else { continue };
        let key = key.trim().to_string();
        let value = value.trim().trim_matches('"').trim_matches('\'').to_string();

        while let Some((top, _)) = stack.last() {
            if indent <= *top {
                stack.pop();
            } else {
                break;
            }
        }
        let path = stack.last().unwrap().1.clone();
        let parent = navigate_mut(&mut root, &path);
        if value.is_empty() {
            parent.insert(key.clone(), Value::Object(serde_json::Map::new()));
            let mut child = path;
            child.push(key);
            stack.push((indent, child));
        } else {
            parent.insert(key, Value::String(value));
        }
    }
    Value::Object(root)
}

fn navigate_mut<'a>(
    root: &'a mut serde_json::Map<String, Value>,
    path: &[String],
) -> &'a mut serde_json::Map<String, Value> {
    let mut cur = root;
    for key in path {
        if !cur.contains_key(key) {
            cur.insert(key.clone(), Value::Object(serde_json::Map::new()));
        }
        cur = cur
            .get_mut(key)
            .and_then(|v| v.as_object_mut())
            .expect("inserted above");
    }
    cur
}

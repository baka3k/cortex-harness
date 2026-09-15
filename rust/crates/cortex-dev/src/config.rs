//! Config resolution — port of dev.py config helpers (`.cortext-harness/config/*.json`,
//! active-project semantics, graph-provider resolution, storage targets).

use crate::util::{echo, echo_err};
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

pub const HARNESS_CONFIG_DIR: &str = ".cortext-harness/config";

pub fn config_dir(project_dir: &Path) -> PathBuf {
    project_dir.join(HARNESS_CONFIG_DIR)
}

pub fn config_path(project_dir: &Path, env: &str) -> PathBuf {
    config_dir(project_dir).join(format!("{}.json", env))
}

/// Sorted list of `*.json` config files in the project config dir.
pub fn config_files(project_dir: &Path) -> Vec<PathBuf> {
    let dir = config_dir(project_dir);
    let mut files: Vec<PathBuf> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "json").unwrap_or(false) && p.is_file())
        .collect();
    files.sort();
    files
}

/// `_load_active_config` — the active config, or the first (sorted) one with a
/// warning; exits 1 with the exact dev.py error lines when nothing exists.
pub fn load_active_config(project_dir: &Path) -> (Value, PathBuf) {
    let cfg_dir = config_dir(project_dir);
    if !cfg_dir.exists() {
        echo_err(&format!(
            "[error] No config found at '{}'. Run 'dev init' first.",
            cfg_dir.display()
        ));
        std::process::exit(1);
    }

    let configs = config_files(project_dir);
    if configs.is_empty() {
        echo_err(&format!(
            "[error] No config files in '{}'. Run 'dev init' first.",
            cfg_dir.display()
        ));
        std::process::exit(1);
    }

    for p in &configs {
        if let Some(cfg) = std::fs::read_to_string(p)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .filter(|cfg| cfg.get("active").and_then(|v| v.as_bool()).unwrap_or(false))
        {
            return (cfg, p.clone());
        }
    }

    let p = configs[0].clone();
    let cfg = std::fs::read_to_string(&p)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .unwrap_or_else(|| Value::Object(Map::new()));
    echo_err(&format!(
        "[warn] No active config found; using '{}'",
        p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
    ));
    (cfg, p)
}

/// `_save_config` — 2-space JSON, `ensure_ascii=False`, no trailing newline.
pub fn save_config(cfg: &Value, path: &Path) {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let text = serde_json::to_string_pretty(cfg).unwrap_or_else(|_| "{}".to_string());
    if std::fs::write(path, text).is_ok() {
        echo(&format!("[ok] Config saved -> {}", path.display()));
    }
}

/// `_deactivate_other_envs` — flip `active` off in sibling configs.
pub fn deactivate_other_envs(project_dir: &Path, current_env: &str) {
    let cfg_dir = config_dir(project_dir);
    if !cfg_dir.exists() {
        return;
    }
    for p in config_files(project_dir) {
        let stem = p
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        if stem == current_env {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&p) else { continue };
        let Ok(mut other) = serde_json::from_str::<Value>(&text) else { continue };
        if other.get("active").and_then(|v| v.as_bool()).unwrap_or(false) {
            if let Some(obj) = other.as_object_mut() {
                obj.insert("active".to_string(), Value::Bool(false));
            }
            let out = serde_json::to_string_pretty(&other).unwrap_or_default();
            if std::fs::write(&p, out).is_ok() {
                echo(&format!(
                    "[info] Deactivated config: {}",
                    p.file_name()
                        .map(|n| n.to_string_lossy().to_string())
                        .unwrap_or_default()
                ));
            }
        }
    }
}

/// `_ignore_folders` — deduped, order-preserving ignore patterns.
pub fn ignore_folders(cfg: &Value) -> Vec<String> {
    let section = cfg.get("ignore");
    let raw = section.and_then(|s| s.as_object()).and_then(|s| s.get("folders"));
    let Some(list) = raw.and_then(|r| r.as_array()) else {
        return Vec::new();
    };
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for item in list {
        if let Some(s) = item.as_str() {
            let trimmed = s.trim();
            if !trimmed.is_empty() && seen.insert(trimmed.to_string()) {
                out.push(trimmed.to_string());
            }
        }
    }
    out
}

/// `_source_projects` — new `{projects: [...]}` and legacy flat formats.
pub fn source_projects(source: &Value) -> Vec<Value> {
    if let Some(projects) = source.get("projects").and_then(|p| p.as_array()) {
        return projects.clone();
    }
    let git = source
        .get("git")
        .cloned()
        .unwrap_or(Value::String(String::new()));
    let folder = source.get("folder").cloned().unwrap_or(Value::Array(Vec::new()));
    let mut proj = Map::new();
    proj.insert("git".to_string(), git);
    proj.insert("folder".to_string(), folder);
    vec![Value::Object(proj)]
}

/// `_source_folders` — flattened, deduped, order-preserving folder list.
pub fn source_folders(source: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for p in source_projects(source) {
        if let Some(folders) = p.get("folder").and_then(|f| f.as_array()) {
            for f in folders {
                if let Some(s) = f
                    .as_str()
                    .filter(|s| !s.is_empty() && seen.insert(s.to_string()))
                {
                    out.push(s.to_string());
                }
            }
        }
    }
    out
}

/// `_default_graph_provider` — POSIX keeps the historical FalkorDBLite default.
pub fn default_graph_provider() -> &'static str {
    if cfg!(windows) {
        "ladybug"
    } else {
        "falkordb"
    }
}

pub(crate) const LADYBUG_ALIASES: &[&str] = &["ladybug", "lbug", "lady-bug", "kuzu"];

/// `_graph_provider` — resolve the effective provider for a scoped key.
pub fn graph_provider(env: &Value, scoped_key: &str) -> Result<String, String> {
    let scoped_value = env
        .get(scoped_key)
        .map(value_to_trimmed_string)
        .unwrap_or_default();
    let global_value = env
        .get("GRAPH_PROVIDER")
        .map(value_to_trimmed_string)
        .unwrap_or_default();
    let default = if scoped_value.is_empty() && global_value.is_empty() {
        default_graph_provider().to_string()
    } else {
        "falkordb".to_string()
    };
    let provider = if !scoped_value.is_empty() {
        scoped_value
    } else if !global_value.is_empty() {
        global_value
    } else {
        default
    }
    .to_lowercase();

    match provider.as_str() {
        "falkor" | "falkordb" => Ok("falkordb".to_string()),
        "neo4j" => Ok("neo4j".to_string()),
        p if LADYBUG_ALIASES.contains(&p) => Ok("ladybug".to_string()),
        other => Err(format!(
            "Unsupported graph provider for {scoped_key}: '{other}'; expected \
             'falkordb' (alias 'falkor'), 'ladybug' (alias 'lbug'), or 'neo4j'"
        )),
    }
}

fn value_to_trimmed_string(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.trim().to_string(),
        other => other.to_string().trim().to_string(),
    }
}


/// dev.py `_is_local` — localhost endpoints skip credential prompts.
pub fn is_local(url_or_uri: &str) -> bool {
    const LOCALHOST: &[&str] = &["localhost", "127.0.0.1", "::1", ""];
    if url_or_uri.is_empty() {
        return false;
    }
    let with_scheme = if url_or_uri.contains("://") {
        url_or_uri.to_string()
    } else {
        format!("//{}", url_or_uri)
    };
    let host = urlparse_hostname(&with_scheme)
        .unwrap_or_else(|| {
            url_or_uri
                .split(':')
                .next()
                .unwrap_or("")
                .to_lowercase()
                .trim_matches('/')
                .to_string()
        });
    LOCALHOST.contains(&host.as_str())
}

/// Minimal hostname extraction for scheme://host:port/path URLs.
fn urlparse_hostname(url: &str) -> Option<String> {
    let after_scheme = url.split("://").nth(1).unwrap_or(url);
    let after_authority = after_scheme.split(['/', '?', '#']).next()?;
    // Strip userinfo.
    let host_port = after_authority.rsplit('@').next()?;
    let host = host_port
        .rsplit_once(':')
        .map(|(h, _)| h)
        .unwrap_or(host_port);
    if host.is_empty() {
        return None;
    }
    Some(host.trim_matches(['[', ']']).to_lowercase())
}


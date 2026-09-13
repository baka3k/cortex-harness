//! cortex-sync — Rust port of `code-tiny/tools/sync/incremental_sync.py`
//! (phase 09 of the rust-full-migration plan).
//!
//! Entry point mirrors the Python `__main__`: preload the harness config
//! (pre-scanning `--root`/`--config`), then run the orchestrator.

mod cli;
mod frameworks;
mod gitdiff;
mod graphops;
mod inventory;
mod journalenv;
mod orchestrator;
mod registry;
mod routing;
mod state;
mod syncscope;
mod tsdetect;
mod util;
mod walk;

use std::collections::BTreeMap;

fn main() {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let (root, config) = pre_scan(&raw);
    let default_config = std::path::Path::new(&root)
        .join(".cortext-harness/config/dev.json")
        .to_string_lossy()
        .to_string();
    load_harness_config(config.as_deref().unwrap_or(default_config.as_str()));
    let args = cli::Args::parse();
    let exit = orchestrator::run_incremental(&args);
    std::process::exit(exit);
}

/// The Python `__main__` pre-parser: only `--root` and `--config`.
fn pre_scan(raw: &[String]) -> (String, Option<String>) {
    let mut root = ".".to_string();
    let mut config: Option<String> = None;
    let mut index = 0;
    while index < raw.len() {
        match raw[index].as_str() {
            "--root" if index + 1 < raw.len() => {
                root = raw[index + 1].clone();
                index += 2;
            }
            "--config" if index + 1 < raw.len() => {
                config = Some(raw[index + 1].clone());
                index += 2;
            }
            other if other.starts_with("--root=") => {
                root = other["--root=".len()..].to_string();
                index += 1;
            }
            other if other.starts_with("--config=") => {
                config = Some(other["--config=".len()..].to_string());
                index += 1;
            }
            _ => index += 1,
        }
    }
    (root, config)
}

/// `load_harness_config` — provider-aware env propagation from a harness
/// dev.json. An unreadable/empty config is a no-op (matches `_read_config`).
/// The local storage overlay (`resolve_storage`) is Python-plane: configs
/// that declare `CORTEX_DATA_HOME`-style storage keys under an embedded
/// backend should run the Python orchestrator instead.
#[allow(unsafe_code)]
fn load_harness_config(config_path: &str) {
    let text = match std::fs::read_to_string(config_path) {
        Ok(text) => text,
        Err(_) => return,
    };
    let cfg: serde_json::Value = match serde_json::from_str(&text) {
        Ok(value) => value,
        Err(_) => return,
    };
    if !cfg.is_object() || cfg.as_object().expect("object").is_empty() {
        return;
    }
    let code_env = cfg
        .get("code")
        .and_then(|c| c.get("env"))
        .and_then(|e| e.as_object())
        .cloned()
        .unwrap_or_default();
    let doc_env = cfg
        .get("doc")
        .and_then(|d| d.get("env"))
        .and_then(|e| e.as_object())
        .cloned()
        .unwrap_or_default();
    let env_str = |map: &serde_json::Map<String, serde_json::Value>, key: &str| -> Option<String> {
        map.get(key).map(|v| match v {
            serde_json::Value::String(s) => s.clone(),
            other => other.to_string(),
        })
    };
    let provider = std::env::var("CODE_GRAPH_PROVIDER")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| std::env::var("GRAPH_PROVIDER").ok().filter(|v| !v.trim().is_empty()))
        .or_else(|| env_str(&code_env, "CODE_GRAPH_PROVIDER"))
        .or_else(|| env_str(&code_env, "GRAPH_PROVIDER"))
        .unwrap_or_else(|| "falkordb".to_string())
        .to_lowercase();
    let provider = match provider.as_str() {
        "falkordb" | "falkor" | "local" | "embedded" => "falkordb",
        "neo4j" | "neo" => "neo4j",
        other => {
            eprintln!("Unsupported graph provider '{other}'. Expected 'falkordb' or 'neo4j'.");
            std::process::exit(2);
        }
    };
    // SAFETY: single-threaded startup, before any child process spawns.
    unsafe {
        std::env::set_var("GRAPH_PROVIDER", provider);
        std::env::set_var("CODE_GRAPH_PROVIDER", provider);
    }
    if provider == "neo4j" {
        for (key, _) in std::env::vars() {
            if key.starts_with("FALKORDB_") {
                // SAFETY: single-threaded startup.
                unsafe { std::env::remove_var(&key) };
            }
        }
        for key in ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASS", "NEO4J_DB"] {
            if let Some(value) = env_str(&code_env, key) {
                if std::env::var(key).is_err() {
                    // SAFETY: single-threaded startup.
                    unsafe { std::env::set_var(key, value) };
                }
            }
        }
    } else {
        for (key, _) in std::env::vars() {
            if key.starts_with("NEO4J_") {
                // SAFETY: single-threaded startup.
                unsafe { std::env::remove_var(&key) };
            }
        }
        for key in ["FALKORDB_GRAPH", "FALKORDB_DATABASE"] {
            let value = env_str(&code_env, key).or_else(|| env_str(&doc_env, key));
            if let Some(value) = value {
                if std::env::var(key).is_err() {
                    // SAFETY: single-threaded startup.
                    unsafe { std::env::set_var(key, value) };
                }
            }
        }
    }
    let _ = BTreeMap::<String, String>::new();
}

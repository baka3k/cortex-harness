//! `dev status` — native transcript with the native storage layer
//! (`cortex_storage` + `env.rs`) for the per-process environment values,
//! exactly as dev.py reads them from `_code_env_for_process` /
//! `_doc_env_for_process`.

use crate::config::*;
use crate::env;
use crate::parser::Matches;
use crate::util::{echo, echo_err};
use serde_json::Value;
use std::path::Path;

pub fn run(m: &Matches) {
    let project_dir = m.value_or("--project-dir", ".");
    let project_path = super::init::resolve_path(Path::new(&project_dir));
    let cfg_dir = config_dir(&project_path);

    if !cfg_dir.exists() {
        echo_err("[error] No config directory found. Run 'dev init' first.");
        std::process::exit(1);
    }

    let envs = config_files(&project_path);
    echo(&format!("\nProject dir : {}", project_path.display()));
    echo(&format!("Config dir  : {}\n", cfg_dir.display()));
    echo("Environments:");
    for p in &envs {
        let name = p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        let active = std::fs::read_to_string(p)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .and_then(|c| c.get("active").and_then(|a| a.as_bool()))
            .unwrap_or(false);
        echo(&format!("  {}{}", name, if active { " [ACTIVE]" } else { "" }));
    }

    let payload = env::status_env_payload(&project_path);

    let proj = payload.get("project").cloned().unwrap_or(Value::Null);
    let config_name = payload
        .get("config_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    echo(&format!(
        "\n--- Active: {} -----------------------------",
        config_name
    ));
    echo(&format!(
        "Project     : {} ({})",
        proj.get("name").and_then(|v| v.as_str()).unwrap_or("None"),
        proj.get("code").and_then(|v| v.as_str()).unwrap_or("None")
    ));

    for (section, provider_key, qdrant_key) in [
        ("code", "code_provider", "QDRANT_CODE_PATH"),
        ("doc", "doc_provider", "QDRANT_DOC_PATH"),
    ] {
        let env_raw = payload
            .get(if section == "code" { "code_env_raw" } else { "doc_env_raw" })
            .cloned()
            .unwrap_or(Value::Null);
        let process_env = payload
            .get(if section == "code" { "code_env" } else { "doc_env" })
            .cloned()
            .unwrap_or(Value::Null);
        let src = {
            // Read the section straight from the config on disk so `source`
            // stays exactly what the file holds.
            let (_, active_path) = load_active_config(&project_path);
            let cfg: Value = std::fs::read_to_string(&active_path)
                .ok()
                .and_then(|t| serde_json::from_str(&t).ok())
                .unwrap_or(Value::Null);
            cfg.get(section).cloned().unwrap_or(Value::Null)
        };
        let projects = source_projects(src.get("source").unwrap_or(&Value::Null));
        echo(&format!("\n[{}]", section));
        let provider = payload.get(provider_key).and_then(|v| v.as_str()).unwrap_or("");
        if provider == "neo4j" {
            echo(&format!(
                "  Neo4j     : {}  db={}",
                env_raw.get("NEO4J_URI").map(str_or_none).unwrap_or_else(|| "None".into()),
                env_raw.get("NEO4J_DB").map(str_or_none).unwrap_or_else(|| "None".into()),
            ));
        } else {
            echo(&format!(
                "  FalkorDB  : {}  graph={}",
                process_env.get("FALKORDB_PATH").map(str_or_none).unwrap_or_else(|| "None".into()),
                process_env.get("FALKORDB_GRAPH").map(str_or_none).unwrap_or_else(|| "None".into()),
            ));
        }
        echo(&format!(
            "  Qdrant    : {}",
            process_env.get(qdrant_key).map(str_or_none).unwrap_or_else(|| "None".into())
        ));
        if section == "code" {
            echo(&format!(
                "  Collection: {}",
                payload
                    .get("code_collection")
                    .and_then(|v| v.as_str())
                    .unwrap_or("project")
            ));
        }
        echo(&format!(
            "  Embedding : {}  device={}",
            env_raw.get("EMBEDDING_MODEL").map(str_or_none).unwrap_or_else(|| "None".into()),
            env_raw.get("device").map(str_or_none).unwrap_or_else(|| "None".into()),
        ));
        echo(&format!("  Projects  : {}", projects.len()));
        for (i, p) in projects.iter().enumerate() {
            let git = p.get("git").and_then(|g| g.as_str()).unwrap_or("");
            let git_label = if git.is_empty() { "(local)" } else { git };
            let folders: Vec<&str> = p
                .get("folder")
                .and_then(|f| f.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_str()).filter(|s| !s.is_empty()).collect())
                .unwrap_or_default();
            echo(&format!(
                "    [{}] {}  ({} folder(s))",
                i + 1,
                git_label,
                folders.len()
            ));
            for f in folders {
                echo(&format!("         • {}", f));
            }
        }
    }
}

/// Python prints `None` for missing dict keys; mirror that shape.
fn str_or_none(v: &Value) -> String {
    match v {
        Value::Null => "None".to_string(),
        other => other.to_string().trim_matches('"').to_string(),
    }
}

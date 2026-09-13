//! `dev init` — interactive config creation + project scaffolding, ported
//! line-for-line from dev.py (prompt order, defaults, JSON key order).

use crate::config::*;
use crate::parser::Matches;
use crate::util::{confirm, echo, prompt};
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

const SCAFFOLD_DIRS: &[&str] = &[
    "docs/design-docs",
    "docs/exec-plans/active",
    "docs/exec-plans/completed",
    "docs/generated",
    "docs/product-specs",
    "docs/references",
    "src/core/migration",
    "src/core/services",
    "src/infra/persistence",
    "src/infra/providers",
    "src/interface/api",
    "src/interface/cli",
    "src/shared",
];

const SCAFFOLD_FILES: &[(&str, &str)] = &[
    ("docs/design-docs/index.md", "# Design Docs\n"),
    ("docs/exec-plans/tech-debt-tracker.md", "# Tech Debt Tracker\n"),
    ("docs/generated/db-schema.md", "# DB Schema\n"),
    ("docs/product-specs/index.md", "# Product Specs\n"),
    ("docs/DESIGN.md", "# Design\n"),
    ("docs/FRONTEND.md", "# Frontend Guidelines\n"),
    ("docs/PLANS.md", "# Project Roadmap\n"),
    ("docs/PRODUCT_SENSE.md", "# Product Logic & Philosophy\n"),
    ("docs/QUALITY_SCORE.md", "# Engineering Standards\n"),
    ("docs/RELIABILITY.md", "# Stability & Error Handling\n"),
    ("docs/SECURITY.md", "# Security Protocols\n"),
    ("AGENTS.md", "# Agents\n"),
    ("ARCHITECTURE.md", "# Architecture\n"),
    (".cursorrules", "# AI Instruction Set\n"),
    ("README.md", "# Project\n"),
];

pub fn run(m: &Matches) {
    let env_choice = m.value_or("--env", "dev");
    let project_dir_opt = m.value("--project-dir").map(String::from);
    let path_arg = m.positionals().first().cloned();
    let path_is_current_dir = path_arg.as_deref() == Some(".");

    let raw = path_arg
        .or(project_dir_opt)
        .unwrap_or_else(|| ".".to_string());
    let project_path = resolve_path(Path::new(&raw));
    let config_path = config_path(&project_path, &env_choice);

    let existing: Value = if config_path.exists() {
        std::fs::read_to_string(&config_path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_else(|| Value::Object(Map::new()))
    } else {
        echo(&format!(
            "[info] Creating new {} config: {}\n",
            env_choice,
            config_path.display()
        ));
        Value::Object(Map::new())
    };
    if !existing.is_null() && config_path.exists() {
        echo(&format!(
            "[info] Updating existing config: {}\n",
            config_path.display()
        ));
    }
    let existing_obj = existing.as_object().cloned().unwrap_or_default();

    // ── Project ──────────────────────────────────────────────────────────
    echo("─── Project ────────────────────────────────");
    let project_code = nested_prompt(
        &existing_obj,
        "Project code (short ID)",
        &["project", "code"],
        "my_project",
    );
    let project_name = nested_prompt(
        &existing_obj,
        "Project name",
        &["project", "name"],
        &project_code,
    );

    // ── Storage backend ──────────────────────────────────────────────────
    echo("\n─── Storage backend ────────────────────────");
    let existing_backend = existing_obj
        .get("storage_backend")
        .and_then(|v| v.as_str())
        .unwrap_or("local")
        .to_string();
    let storage_backend = prompt(
        "Storage backend (local, remote)",
        &existing_backend,
    )
    .to_lowercase();
    let mut remote_section = Map::new();
    let mut remote_is_local_docker = false;
    if storage_backend == "remote" {
        let existing_remote = existing_obj
            .get("remote")
            .and_then(|v| v.as_object())
            .cloned()
            .unwrap_or_default();
        let qdrant_port = std::env::var("QDRANT_HTTP_PORT").unwrap_or_else(|_| "6333".to_string());
        let falkordb_port = std::env::var("FALKORDB_PORT").unwrap_or_else(|_| "6379".to_string());
        let qdrant_default = existing_remote
            .get("qdrant_url")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("http://localhost:{}", qdrant_port));
        let falkordb_default = existing_remote
            .get("falkordb_uri")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| format!("localhost:{}", falkordb_port));

        loop {
            echo("\n─── Remote backend ─────────────────────────");
            echo(
                "  Defaults match local Docker containers (managed by 'dev infra-up'). \
                 Press Enter to accept. Secrets are stored in plaintext in config — \
                 do NOT commit a populated config to a shared repo.",
            );
            let qdrant_url = prompt("  Qdrant URL", &qdrant_default).trim().to_string();
            let falkordb_uri = prompt("  FalkorDB URI", &falkordb_default).trim().to_string();

            let q_local = qdrant_url.is_empty() || is_local(&qdrant_url);
            let f_local = falkordb_uri.is_empty() || is_local(&falkordb_uri);
            let skip_creds = q_local && f_local;

            let mut qdrant_api_key = String::new();
            let mut falkordb_password = String::new(); // sensitive-guard:allow (tên biến)
            let mut falkordb_ssl = false;
            if !skip_creds {
                qdrant_api_key = prompt(
                    "  Qdrant API key (blank = none)",
                    existing_remote
                        .get("qdrant_api_key")
                        .and_then(|v| v.as_str())
                        .unwrap_or(""),
                )
                .trim()
                .to_string();
                falkordb_password = prompt( // sensitive-guard:allow (tên biến)
                    "  FalkorDB password (blank = none)",
                    existing_remote
                        .get("falkordb_password")
                        .and_then(|v| v.as_str())
                        .unwrap_or(""),
                )
                .trim()
                .to_string();
                let default_ssl = existing_remote
                    .get("falkordb_ssl")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false);
                falkordb_ssl = confirm("  FalkorDB TLS?", default_ssl);
            }

            let candidate = json!({
                "qdrant_url": opt_str(&qdrant_url),
                "qdrant_api_key": opt_str(&qdrant_api_key),
                "falkordb_uri": opt_str(&falkordb_uri),
                "falkordb_password": opt_str(&falkordb_password),
                "falkordb_ssl": falkordb_ssl,
            });
            if let Err(exc) = validate_remote(&candidate) {
                echo(&format!("[error] {}", exc));
                if !confirm("  Retry remote fields?", true) {
                    std::process::exit(1);
                }
                continue;
            }
            for (k, v) in candidate.as_object().unwrap() {
                let keep = match v {
                    Value::Null => false,
                    Value::String(s) => !s.is_empty(),
                    Value::Bool(_) => true,
                    _ => true,
                };
                if keep || k == "falkordb_ssl" {
                    remote_section.insert(k.clone(), v.clone());
                }
            }
            remote_is_local_docker = skip_creds;
            break;
        }
    }

    let existing_code_env = section_env(&existing_obj, "code");
    let existing_doc_env = section_env(&existing_obj, "doc");
    let mut storage_env = Map::new();
    if storage_backend == "local" {
        let storage_instance_default = existing_code_env
            .get("CORTEX_STORAGE_INSTANCE")
            .and_then(|v| v.as_str())
            .or_else(|| existing_doc_env.get("CORTEX_STORAGE_INSTANCE").and_then(|v| v.as_str()))
            .unwrap_or("default");
        let data_home_default = existing_code_env
            .get("CORTEX_DATA_HOME")
            .and_then(|v| v.as_str())
            .or_else(|| existing_doc_env.get("CORTEX_DATA_HOME").and_then(|v| v.as_str()))
            .unwrap_or("");
        echo("\n─── Local storage ──────────────────────────");
        let storage_instance = prompt("CORTEX_STORAGE_INSTANCE", storage_instance_default);
        let data_home = prompt("CORTEX_DATA_HOME (blank = account default)", data_home_default)
            .trim()
            .to_string();
        storage_env.insert("CORTEX_STORAGE_INSTANCE".to_string(), json!(storage_instance));
        if !data_home.is_empty() {
            storage_env.insert("CORTEX_DATA_HOME".to_string(), json!(data_home));
        }
    }

    // ── Code — Graph + Qdrant + Embedding ───────────────────────────────
    echo("\n─── Code — Graph + Qdrant + Embedding ──────");
    let (code_provider, code_graph_env) = prompt_graph_env(
        &existing_obj,
        "code",
        "CODE_GRAPH_PROVIDER",
        &project_code,
        "falkordb",
    );
    let code_qdrant_collection = nested_prompt(
        &existing_obj,
        "QDRANT_COLLECTION",
        &["code", "env", "QDRANT_COLLECTION"],
        &project_code,
    );
    let code_embed_model = nested_prompt(
        &existing_obj,
        "EMBEDDING_MODEL",
        &["code", "env", "EMBEDDING_MODEL"],
        "jinaai/jina-embeddings-v3",
    );
    let code_batch_size =
        nested_prompt(&existing_obj, "BATCH_SIZE", &["code", "env", "BATCH_SIZE"], "8");
    let code_max_chars = nested_prompt(
        &existing_obj,
        "MAX_EMBED_CHARS",
        &["code", "env", "MAX_EMBED_CHARS"],
        "500",
    );
    let code_device = nested_prompt(&existing_obj, "device", &["code", "env", "device"], "auto");

    // ── Doc — Graph + Qdrant + Embedding ────────────────────────────────
    echo("\n─── Doc — Graph + Qdrant + Embedding ───────");
    let (_, doc_graph_env) = prompt_graph_env(
        &existing_obj,
        "doc",
        "DOC_GRAPH_PROVIDER",
        &format!("{}_doc", project_code),
        &code_provider,
    );
    let doc_embed_model = nested_prompt(
        &existing_obj,
        "EMBEDDING_MODEL",
        &["doc", "env", "EMBEDDING_MODEL"],
        "BAAI/bge-m3",
    );
    let doc_batch_size =
        nested_prompt(&existing_obj, "BATCH_SIZE", &["doc", "env", "BATCH_SIZE"], "8");
    let doc_max_chars = nested_prompt(
        &existing_obj,
        "MAX_EMBED_CHARS",
        &["doc", "env", "MAX_EMBED_CHARS"],
        "500",
    );
    let doc_device = nested_prompt(
        &existing_obj,
        "device",
        &["doc", "env", "device"],
        &code_device,
    );

    // ── Code source — first project ─────────────────────────────────────
    let existing_code_projects = existing_obj
        .get("code")
        .and_then(|c| c.get("source"))
        .map(source_projects)
        .unwrap_or_default();
    let first_code: Value = existing_code_projects
        .first()
        .cloned()
        .unwrap_or_else(|| json!({}));

    echo("\n─── Code — first project ───────────────────");
    let has_code_folders = existing_code_projects
        .iter()
        .any(|p| p.get("folder").and_then(|f| f.as_array()).map(|a| !a.is_empty()).unwrap_or(false));
    if !existing_code_projects.is_empty() && has_code_folders {
        echo(&format!("  Existing projects: {}", existing_code_projects.len()));
        for (i, p) in existing_code_projects.iter().enumerate() {
            let git = p.get("git").and_then(|g| g.as_str()).unwrap_or("");
            let folders: Vec<String> = p
                .get("folder")
                .and_then(|f| f.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                .unwrap_or_default();
            echo(&format!(
                "    [{}] git={}  folders={:?}",
                i + 1,
                if git.is_empty() { "(local)" } else { git },
                folders
            ));
        }
        echo("  (Run 'dev sync code add' to add more; editing here updates project #1 only)");
    }

    let mut code_folders_default = first_code
        .get("folder")
        .and_then(|f| f.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    if code_folders_default.is_empty() && path_is_current_dir {
        code_folders_default = project_path.to_string_lossy().to_string();
    }

    let code_git = prompt(
        "  Git URL (blank = local)",
        first_code.get("git").and_then(|g| g.as_str()).unwrap_or(""),
    );
    let code_folders_raw = prompt(
        "  Source folders (comma-separated, blank = auto-scaffold)",
        &code_folders_default,
    );

    // ── Doc source — first project ──────────────────────────────────────
    let existing_doc_projects = existing_obj
        .get("doc")
        .and_then(|d| d.get("source"))
        .map(source_projects)
        .unwrap_or_default();
    let first_doc: Value = existing_doc_projects
        .first()
        .cloned()
        .unwrap_or_else(|| json!({}));

    echo("\n─── Doc — first project ────────────────────");
    let has_doc_folders = existing_doc_projects
        .iter()
        .any(|p| p.get("folder").and_then(|f| f.as_array()).map(|a| !a.is_empty()).unwrap_or(false));
    if !existing_doc_projects.is_empty() && has_doc_folders {
        echo(&format!("  Existing projects: {}", existing_doc_projects.len()));
        for (i, p) in existing_doc_projects.iter().enumerate() {
            let git = p.get("git").and_then(|g| g.as_str()).unwrap_or("");
            let folders: Vec<String> = p
                .get("folder")
                .and_then(|f| f.as_array())
                .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
                .unwrap_or_default();
            echo(&format!(
                "    [{}] git={}  folders={:?}",
                i + 1,
                if git.is_empty() { "(local)" } else { git },
                folders
            ));
        }
        echo("  (Run 'dev sync doc add' to add more; editing here updates project #1 only)");
    }

    let doc_git = prompt(
        "  Git URL (blank = local)",
        first_doc.get("git").and_then(|g| g.as_str()).unwrap_or(""),
    );
    let doc_folders_default = first_doc
        .get("folder")
        .and_then(|f| f.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    let doc_folders_raw = prompt("  Doc folders (comma-separated, blank = auto-scaffold)", &doc_folders_default);

    // ── Ignore folders ──────────────────────────────────────────────────
    let existing_ignore_folders = crate::config::ignore_folders(&Value::Object(existing_obj.clone()));
    echo("\n─── Ignore folders ─────────────────────────");
    let ignore_folders_raw = prompt(
        "  Folders to ignore when scanning (comma-separated, glob allowed)",
        &existing_ignore_folders.join(", "),
    );
    let ignore_folders: Vec<String> = ignore_folders_raw
        .split(',')
        .map(|f| f.trim().to_string())
        .filter(|f| !f.is_empty())
        .collect();

    // ── Scaffold / resolve folders ──────────────────────────────────────
    let (code_folders, doc_folders) = if !code_folders_raw.trim().is_empty()
        || !doc_folders_raw.trim().is_empty()
    {
        let split = |s: &str| -> Vec<String> {
            s.split(',').map(|f| f.trim().to_string()).filter(|f| !f.is_empty()).collect()
        };
        (split(&code_folders_raw), split(&doc_folders_raw))
    } else {
        echo("");
        scaffold_project(&project_path)
    };

    // ── Merge projects ──────────────────────────────────────────────────
    let new_first_code = json!({"git": code_git, "folder": code_folders});
    let mut code_projects = vec![new_first_code];
    if existing_code_projects.len() > 1 {
        code_projects.extend(existing_code_projects[1..].iter().cloned());
    }
    let new_first_doc = json!({"git": doc_git, "folder": doc_folders});
    let mut doc_projects = vec![new_first_doc];
    if existing_doc_projects.len() > 1 {
        doc_projects.extend(existing_doc_projects[1..].iter().cloned());
    }

    let mut code_env_map = Map::new();
    for (k, v) in storage_env.iter() {
        code_env_map.insert(k.clone(), v.clone());
    }
    for (k, v) in code_graph_env.iter() {
        code_env_map.insert(k.clone(), v.clone());
    }
    code_env_map.insert("QDRANT_COLLECTION".to_string(), json!(code_qdrant_collection));
    code_env_map.insert("EMBEDDING_MODEL".to_string(), json!(code_embed_model));
    code_env_map.insert("BATCH_SIZE".to_string(), json!(code_batch_size));
    code_env_map.insert("MAX_EMBED_CHARS".to_string(), json!(code_max_chars));
    code_env_map.insert("device".to_string(), json!(code_device));

    let mut doc_env_map = Map::new();
    for (k, v) in storage_env.iter() {
        doc_env_map.insert(k.clone(), v.clone());
    }
    for (k, v) in doc_graph_env.iter() {
        doc_env_map.insert(k.clone(), v.clone());
    }
    doc_env_map.insert("EMBEDDING_MODEL".to_string(), json!(doc_embed_model));
    doc_env_map.insert("BATCH_SIZE".to_string(), json!(doc_batch_size));
    doc_env_map.insert("MAX_EMBED_CHARS".to_string(), json!(doc_max_chars));
    doc_env_map.insert("device".to_string(), json!(doc_device));

    let mut cfg = Map::new();
    cfg.insert("active".to_string(), json!(true));
    cfg.insert("project".to_string(), json!({"code": project_code, "name": project_name}));
    cfg.insert("storage_backend".to_string(), json!(storage_backend));
    cfg.insert(
        "code".to_string(),
        json!({
            "env": Value::Object(code_env_map),
            "source": {"projects": code_projects},
        }),
    );
    cfg.insert(
        "doc".to_string(),
        json!({
            "env": Value::Object(doc_env_map),
            "source": {"projects": doc_projects},
        }),
    );
    if !remote_section.is_empty() {
        cfg.insert("remote".to_string(), Value::Object(remote_section.clone()));
    }
    if !ignore_folders.is_empty() || existing_obj.contains_key("ignore") {
        cfg.insert("ignore".to_string(), json!({"folders": ignore_folders}));
    }

    deactivate_other_envs(&project_path, &env_choice);
    save_config(&Value::Object(cfg.clone()), &config_path);
    if storage_backend == "remote" {
        if remote_is_local_docker {
            echo(
                "     [info] storage_backend=remote (local Docker defaults) — \
                 run 'dev infra-up --provision' to start local Qdrant/FalkorDB containers.",
            );
        } else {
            echo(
                "     [info] storage_backend=remote — run 'make infra-up' or 'dev doctor' \
                 to verify connectivity.",
            );
        }
    }

    let code_projects_arr = cfg
        .get("code")
        .and_then(|c| c.get("source"))
        .map(source_projects)
        .unwrap_or_default();
    let doc_projects_arr = cfg
        .get("doc")
        .and_then(|d| d.get("source"))
        .map(source_projects)
        .unwrap_or_default();
    let code_folder_count = code_projects_arr
        .iter()
        .map(|p| p.get("folder").and_then(|f| f.as_array()).map(|a| a.len()).unwrap_or(0))
        .sum::<usize>();
    let doc_folder_count = doc_projects_arr
        .iter()
        .map(|p| p.get("folder").and_then(|f| f.as_array()).map(|a| a.len()).unwrap_or(0))
        .sum::<usize>();

    echo(&format!(
        "\n[ok] Environment '{}' is now active.",
        env_choice
    ));
    echo(&format!(
        "     Code projects : {}  (total {} folders)",
        code_projects_arr.len(),
        code_folder_count
    ));
    echo(&format!(
        "     Doc  projects : {}  (total {} folders)",
        doc_projects_arr.len(),
        doc_folder_count
    ));
    echo("     Tip: 'dev sync code add' / 'dev sync doc add' to add more projects.");
}

// ---------------------------------------------------------------------------

fn opt_str(s: &str) -> Value {
    if s.is_empty() {
        Value::Null
    } else {
        json!(s)
    }
}

/// dev.py `_p`: prompt with the existing value (if str) as default.
fn nested_prompt(existing: &Map<String, Value>, label: &str, keys: &[&str], default: &str) -> String {
    let mut cur: &Value = &Value::Object(existing.clone());
    for k in keys {
        cur = cur.get(k).unwrap_or(&Value::Null);
    }
    let cur_val = cur.as_str().unwrap_or("");
    let effective = if cur_val.is_empty() { default } else { cur_val };
    prompt(label, effective)
}

fn section_env(existing: &Map<String, Value>, section: &str) -> Map<String, Value> {
    existing
        .get(section)
        .and_then(|s| s.get("env"))
        .and_then(|e| e.as_object())
        .cloned()
        .unwrap_or_default()
}

/// dev.py `_provider_default` + `_prompt_graph_env`.
fn prompt_graph_env(
    existing: &Map<String, Value>,
    section: &str,
    scoped_key: &str,
    graph_default: &str,
    provider_default: &str,
) -> (String, Map<String, Value>) {
    let env_values = section_env(existing, section);
    let value = env_values
        .get(scoped_key)
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| env_values.get("GRAPH_PROVIDER").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
        .unwrap_or(provider_default);
    let value = value.trim().to_lowercase();
    let shown_default = if value == "falkor" || value == "falkordb" {
        "falkordb"
    } else {
        "neo4j"
    };
    let provider = prompt("GRAPH_PROVIDER (neo4j, falkordb)", shown_default).to_lowercase();

    let mut graph_env = Map::new();
    graph_env.insert("GRAPH_PROVIDER".to_string(), json!(provider));
    graph_env.insert(scoped_key.to_string(), json!(provider));
    if provider == "neo4j" {
        graph_env.insert(
            "NEO4J_URI".to_string(),
            json!(nested_prompt(existing, "NEO4J_URI", &[section, "env", "NEO4J_URI"], "bolt://localhost:7687")),
        );
        graph_env.insert(
            "NEO4J_DB".to_string(),
            json!(nested_prompt(existing, "NEO4J_DB", &[section, "env", "NEO4J_DB"], graph_default)),
        );
        graph_env.insert(
            "NEO4J_USER".to_string(),
            json!(nested_prompt(existing, "NEO4J_USER", &[section, "env", "NEO4J_USER"], "neo4j")),
        );
        graph_env.insert(
            "NEO4J_PASS".to_string(),
            json!(nested_prompt(existing, "NEO4J_PASS", &[section, "env", "NEO4J_PASS"], "")),
        );
        return (provider, graph_env);
    }
    graph_env.insert(
        "FALKORDB_GRAPH".to_string(),
        json!(nested_prompt(existing, "FALKORDB_GRAPH", &[section, "env", "FALKORDB_GRAPH"], graph_default)),
    );
    (provider, graph_env)
}

/// dev.py `validate_backend_config("remote", candidate)` subset (the caller
/// only reaches this in remote mode, where ladybug is irrelevant).
fn validate_remote(candidate: &Value) -> Result<(), String> {
    let nonempty = |k: &str| -> bool {
        candidate
            .get(k)
            .and_then(|v| v.as_str())
            .map(|s| !s.trim().is_empty())
            .unwrap_or(false)
    };
    if !nonempty("qdrant_url") && !nonempty("falkordb_uri") {
        return Err("remote config must specify at least qdrant_url or falkordb_uri".to_string());
    }
    Ok(())
}

/// `_scaffold_project` — create dirs/files, print the transcript, return
/// (doc_folders, code_folders) discovered under docs/ and src/.
fn scaffold_project(project_dir: &Path) -> (Vec<String>, Vec<String>) {
    echo("\n─── Scaffolding project structure ─────────");
    let mut created = Vec::new();

    for d in SCAFFOLD_DIRS {
        let full = project_dir.join(d);
        if !full.exists() {
            let _ = std::fs::create_dir_all(&full);
            created.push(format!("  [dir]  {}/", d));
        }
    }
    for (rel, content) in SCAFFOLD_FILES {
        let full = project_dir.join(rel);
        if !full.exists() {
            if let Some(parent) = full.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            let _ = std::fs::write(&full, content);
            created.push(format!("  [file] {}", rel));
        }
    }

    if created.is_empty() {
        echo("  (all paths already exist, nothing created)");
    } else {
        for line in created {
            echo(&line);
        }
    }

    let doc_folders = discover_folders(project_dir, "docs");
    let code_folders = discover_folders(project_dir, "src");
    echo(&format!(
        "  [scan] doc folders: {}  code folders: {}",
        doc_folders.len(),
        code_folders.len()
    ));
    (code_folders, doc_folders)
}

/// `_discover_folders`: `[root_prefix] + sorted(relative dir paths)`, pruning
/// the built-in scan excludes.
fn discover_folders(project_dir: &Path, root_prefix: &str) -> Vec<String> {
    let base = project_dir.join(root_prefix);
    if !base.exists() {
        return vec![root_prefix.to_string()];
    }
    let mut dirs = Vec::new();
    collect_dirs(&base, project_dir, &mut dirs);
    dirs.sort();
    let mut result = vec![root_prefix.to_string()];
    result.extend(dirs);
    result
}

fn collect_dirs(dir: &Path, project_dir: &Path, out: &mut Vec<String>) {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .collect();
    // Python sorts by the full path string; matching that is enough here
    // because everything shares one parent at each level.
    entries.sort();
    for entry in entries {
        if !entry.is_dir() {
            continue;
        }
        let Ok(rel) = entry.strip_prefix(project_dir) else { continue };
        let parts: Vec<String> = rel
            .iter()
            .map(|p| p.to_string_lossy().to_string())
            .collect();
        if parts.iter().any(|p| crate::util::is_excluded_dir_name(p)) {
            continue;
        }
        out.push(parts.join("/"));
        collect_dirs(&entry, project_dir, out);
    }
}

/// `Path(...).resolve()` equivalent: absolute + symlink-free + `..` removed.
pub fn resolve_path(p: &Path) -> PathBuf {
    match std::fs::canonicalize(p) {
        Ok(c) => c,
        Err(_) => {
            // Resolve lexically against the cwd, like realpath(non-strict).
            let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
            let joined = if p.is_absolute() { p.to_path_buf() } else { cwd.join(p) };
            let mut out = PathBuf::new();
            for component in joined.components() {
                match component {
                    std::path::Component::ParentDir => {
                        out.pop();
                    }
                    std::path::Component::CurDir => {}
                    other => out.push(other.as_os_str()),
                }
            }
            out
        }
    }
}

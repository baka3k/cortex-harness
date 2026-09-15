//! CLI runner cho 3 bin web overlay (`analyzer-fastapi-django`,
//! `analyzer-express-js`, `analyzer-laravel`) — mirror `parse_args` +
//! `main` của `tools/web_framework/web_framework_analyzer.py`, thêm các cờ
//! orchestrator nhận-và-bỏ-qua (qdrant/embedding/message-scan lane).

use std::path::{Path, PathBuf};

use clap::Parser;
use serde_json::{json, Map, Value};

use cortex_analyzer_framework::cli::{parse_falkordb_uri, validate_falkordb_uri};
use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore, StoreError};

use crate::web::pipeline::analyze_project;
use crate::web::writer::WebFrameworkWriter;

/// Framework key của từng binary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WebFramework {
    FastapiDjango,
    ExpressJs,
    Laravel,
}

impl WebFramework {
    pub fn key(&self) -> &'static str {
        match self {
            WebFramework::FastapiDjango => "fastapi_django",
            WebFramework::ExpressJs => "express_js",
            WebFramework::Laravel => "laravel",
        }
    }

    /// `framework_names` trong `_write`/`main`.
    pub fn framework_names(&self) -> &'static [&'static str] {
        match self {
            WebFramework::FastapiDjango => &["fastapi", "django"],
            WebFramework::ExpressJs => &["express_js"],
            WebFramework::Laravel => &["laravel"],
        }
    }
}

#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
pub struct WebOverlayArgs {
    #[arg(long, required = true)]
    pub root: String,
    #[arg(long, default_value = "")]
    pub project_id: String,
    #[arg(long, default_value = "")]
    pub project_name: String,
    #[arg(long)]
    pub framework: String,
    #[arg(long, default_value = "")]
    pub commit_sha_before: String,
    #[arg(long, default_value = "")]
    pub commit_sha_after: String,
    #[arg(long)]
    pub incremental: bool,
    #[arg(long, default_value = "")]
    pub changed_files_manifest: String,
    #[arg(long, default_value = "")]
    pub deleted_files_manifest: String,
    #[arg(long)]
    pub ignore_cache: bool,
    #[arg(long)]
    pub disable_message_scan: bool,
    #[arg(long)]
    pub verbose: bool,
    #[arg(long)]
    pub dry_run: bool,
    #[arg(long)]
    pub neo4j_uri: Option<String>,
    #[arg(long)]
    pub neo4j_user: Option<String>,
    #[arg(long)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub neo4j_db: Option<String>,
    // graph provider args (add_graph_provider_args)
    #[arg(long)]
    pub graph_provider: Option<String>,
    #[arg(long)]
    pub ladybug_path: Option<String>,
    #[arg(long)]
    pub ladybug_graph: Option<String>,
    #[arg(long)]
    pub falkordb_path: Option<String>,
    #[arg(long)]
    pub falkordb_uri: Option<String>,
    #[arg(long)]
    pub falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub falkordb_ssl: bool,
    #[arg(long)]
    pub falkordb_graph: Option<String>,
    // Orchestrator flags — nhận và bỏ qua (giữ contract _build_analyzer_cmd).
    #[arg(long)]
    pub language: Option<String>,
    #[arg(long)]
    pub repo: Option<String>,
    #[arg(long)]
    pub build_system: Option<String>,
    #[arg(long = "build_system", hide = true)]
    pub build_system_alt: Option<String>,
    #[arg(long)]
    pub qdrant_url: Option<String>,
    #[arg(long)]
    pub qdrant_collection: Option<String>,
    #[arg(long)]
    pub device: Option<String>,
    #[arg(long)]
    pub batch_size: Option<i64>,
    #[arg(long)]
    pub max_embed_chars: Option<i64>,
    #[arg(long)]
    pub embed_model: Option<String>,
    #[arg(long)]
    pub enable_message_scan: bool,
    #[arg(long)]
    pub message_output_dir: Option<String>,
    #[arg(long)]
    pub message_qdrant_collection: Option<String>,
    #[arg(long)]
    pub config: Option<String>,
    #[arg(long)]
    pub quiet: bool,
}

/// `_manifest` — JSON `{"paths": [...]}` hoặc list; giữ thứ tự, POSIX hoá.
pub fn manifest_paths(path: &str) -> Vec<String> {
    if path.is_empty() {
        return Vec::new();
    }
    let Ok(text) = std::fs::read_to_string(path) else {
        // Python Path(path).read_text lỗi → exception → crash; không xảy ra
        // trên harness (manifest luôn tồn tại khi được truyền).
        return Vec::new();
    };
    let Ok(payload) = serde_json::from_str::<Value>(&text) else {
        return Vec::new();
    };
    let values = match &payload {
        Value::Object(map) => map.get("paths").cloned().unwrap_or(Value::Array(vec![])),
        Value::Array(items) => Value::Array(items.clone()),
        _ => Value::Array(vec![]),
    };
    values
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(|item| item.as_str().unwrap_or("").replace('\\', "/"))
                .collect()
        })
        .unwrap_or_default()
}

pub fn graph_writes_disabled() -> bool {
    matches!(
        std::env::var("CORTEX_DISABLE_GRAPH")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// `env_graph_provider()` — env `CORTEX_GRAPH_PROVIDER`, default "neo4j" như
/// Python CLI.
fn env_graph_provider() -> String {
    std::env::var("CORTEX_GRAPH_PROVIDER")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "neo4j".into())
}

/// `prepare_graph_args` cho provider falkordb — trả graph name đã resolve.
/// Trả None khi graph writes disabled.
fn prepare_falkordb_graph(args: &WebOverlayArgs) -> Option<String> {
    if graph_writes_disabled() {
        return None;
    }
    let graph = args
        .falkordb_graph
        .clone()
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
        .or_else(|| std::env::var("FALKORDB_DATABASE").ok().filter(|v| !v.is_empty()))
        .unwrap_or_else(|| {
            if args.project_id.is_empty() {
                "hyper_graph".into()
            } else {
                args.project_id.clone()
            }
        });
    Some(graph)
}

fn open_store(args: &WebOverlayArgs) -> Result<(Box<dyn GraphStore>, Option<String>), StoreError> {
    let provider = args
        .graph_provider
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(env_graph_provider);
    match provider.to_lowercase().as_str() {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| {
                    StoreError::Invalid("provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH".into())
                })?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| {
                    if args.project_id.is_empty() {
                        "hyper_graph".into()
                    } else {
                        args.project_id.clone()
                    }
                });
            let store = LadybugStore::open(Path::new(&path), &graph)?;
            Ok((Box::new(store), Some(graph)))
        }
        "falkordb" | "falkor" => {
            let graph = prepare_falkordb_graph(args)
                .ok_or_else(|| StoreError::Invalid("graph writes disabled".into()))?;
            if args
                .falkordb_path
                .clone()
                .or_else(|| std::env::var("FALKORDB_PATH").ok().filter(|v| !v.is_empty()))
                .is_some()
            {
                // Embedded FalkorDBLite chỉ chạy phía Python — fail-closed.
                return Err(StoreError::Invalid(
                    "--falkordb-path (embedded FalkorDBLite) chỉ chạy phía Python; \
                     dùng --falkordb-uri cho remote hoặc --graph-provider ladybug"
                        .into(),
                ));
            }
            let uri = args
                .falkordb_uri
                .clone()
                .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "127.0.0.1:6379".to_string());
            validate_falkordb_uri(&uri)?;
            let (host, port) = parse_falkordb_uri(&uri);
            let client = FalkorDbClient::connect_verified(&host, port)?;
            Ok((Box::new(FalkorDbStore::new(client, graph.clone())), Some(graph)))
        }
        other => Err(StoreError::Invalid(format!(
            "unsupported --graph-provider: {other}"
        ))),
    }
}

/// Summary dict theo insertion order Python.
enum Summary {
    Disabled,
    Written {
        nodes: usize,
        relationships: usize,
        deleted: usize,
    },
}

impl Summary {
    fn entries(&self) -> Vec<(&'static str, Value)> {
        match self {
            Summary::Disabled => vec![
                ("nodes", json!(0)),
                ("relationships", json!(0)),
                ("deleted", json!(0)),
                ("disabled", json!(true)),
            ],
            Summary::Written {
                nodes,
                relationships,
                deleted,
            } => vec![
                ("nodes", json!(nodes)),
                ("relationships", json!(relationships)),
                ("deleted", json!(deleted)),
            ],
        }
    }
}

/// `_write` — driver None (graphless) → Disabled; exception → Err.
fn write_all(
    framework: WebFramework,
    args: &WebOverlayArgs,
    node_rows: &[Map<String, Value>],
    relationship_rows: &[Map<String, Value>],
    deleted_paths: &[String],
) -> Result<Summary, String> {
    if graph_writes_disabled() {
        return Ok(Summary::Disabled);
    }
    let provider = args
        .graph_provider
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(env_graph_provider);
    let neo4j_creds_complete = args.neo4j_uri.is_some()
        && args.neo4j_user.is_some()
        && args.neo4j_password.is_some();
    // Python: create_graph_driver_from_args trả None khi thiếu creds → disabled.
    if provider.to_lowercase().starts_with("neo4j")
        && !neo4j_creds_complete
    {
        return Ok(Summary::Disabled);
    }
    let (mut store, database) = match open_store(args) {
        Ok(result) => result,
        Err(error) => return Err(error.to_string()),
    };
    let mut writer = WebFrameworkWriter::new(store.as_mut(), database, 500);
    let mut deleted = 0usize;
    for name in framework.framework_names() {
        if !deleted_paths.is_empty() {
            deleted += writer.delete_paths(&args.project_id, name, deleted_paths)?;
        }
    }
    let (nodes, relationships) = writer
        .write_all(node_rows, relationship_rows)
        .map_err(|error| error.to_string())?;
    Ok(Summary::Written {
        nodes,
        relationships,
        deleted,
    })
}

/// `main` của web_framework_analyzer.py.
pub fn run(framework: WebFramework) -> i32 {
    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    // --framework được binary quyết định; vẫn accept cờ (orchestrator truyền
    // `--framework <key>`); nếu giá trị khác key của binary → lỗi như argparse
    // choices.
    let expected = framework.key();
    let passed = argv
        .iter()
        .position(|arg| arg == "--framework")
        .and_then(|position| argv.get(position + 1));
    if let Some(value) = passed {
        if value != expected {
            eprintln!(
                "argument --framework: invalid choice: '{value}' (choose from 'fastapi_django', 'express_js', 'laravel')"
            );
            return 2;
        }
    } else {
        argv.push("--framework".into());
        argv.push(expected.into());
    }
    let args = WebOverlayArgs::parse_from(&argv);
    let root: PathBuf = crate::pyutil::realpath(Path::new(&args.root));
    if !root.is_dir() {
        eprintln!("Root not found: {}", root.to_string_lossy());
        return 2;
    }
    let project_id = if args.project_id.is_empty() {
        root.file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_default()
    } else {
        args.project_id.clone()
    };
    let changed = if args.incremental {
        manifest_paths(&args.changed_files_manifest)
    } else {
        Vec::new()
    };
    let deleted = if args.incremental {
        manifest_paths(&args.deleted_files_manifest)
    } else {
        Vec::new()
    };
    let result = analyze_project(&root, &project_id, framework.framework_names(), &changed);
    let (node_rows, relationship_rows) = result.graph_rows();
    if args.dry_run {
        let payload = json!({
            "nodes": node_rows.iter().map(|row| Value::Object(row.clone())).collect::<Vec<_>>(),
            "relationships": relationship_rows.iter().map(|row| Value::Object(row.clone())).collect::<Vec<_>>(),
        });
        println!("{}", crate::pyjson::dumps_default(&payload));
        return 0;
    }
    let summary = match write_all(framework, &args, &node_rows, &relationship_rows, &deleted) {
        Ok(summary) => summary,
        Err(error) => {
            eprintln!("[web_framework] graph write failed: {error}");
            return 3;
        }
    };
    let repr = crate::pyrepr::py_dict_repr(&summary.entries()
        .iter()
        .map(|(key, value)| (*key, value.clone()))
        .collect::<Vec<_>>());
    println!(
        "[overlay] framework={} endpoints={} relationships={} graph={}",
        framework.key(),
        node_rows.len(),
        relationship_rows.len(),
        repr
    );
    0
}

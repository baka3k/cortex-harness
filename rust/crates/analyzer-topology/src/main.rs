//! analyzer-topology — Rust port của `tools/project_topology/topology_analyzer.py`
//! (phase 04 `260915-analyzer-layer-rust-cutover`).
//!
//! Pipeline: parse args (byte-stable với `_build_analyzer_cmd` cho entry
//! `project_topology`) → analyze_project (discovery → parse → resolve) →
//! summary JSON (`[project_topology] {...}`, sort_keys + ensure_ascii) →
//! graph writes qua `cortex_graph_writer::topology::ProjectTopologyWriter`
//! (nửa writer đã port, KHÔNG reimplement).
//!
//! Differences có chủ đích:
//! * Neo4j provider: Rust backend chỉ hỗ trợ falkordb/ladybug — provider
//!   neo4j trả graph_writes {} (như Python khi driver không dựng được).
//! * `--falkordb-path` (embedded FalkorDBLite) fail-closed như các analyzer
//!   Rust khác.
//! * Journal attach là plane orchestrator-side; binary này không tự attach.

mod detector;
mod etdom;
mod models;
mod parsers;
mod pipeline;
mod pyjson;
mod registry;
mod resolver;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use clap::Parser;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, IndexSpec, LadybugStore, StoreError};
use cortex_graph_writer::ProjectTopologyWriter;
use cortex_falkordb::client::FalkorDbClient;
use serde_json::{json, Value};

use crate::models::TopologyAnalysisResult;
use crate::pipeline::{AnalyzeInput, analyze_project};
use crate::registry::descriptor_candidates;

/// CLI contract của `topology_analyzer.parse_args` + `add_graph_provider_args`.
#[derive(Debug, Clone, Parser)]
#[command(no_binary_name = true, disable_help_flag = true)]
pub struct TopologyArgs {
    #[arg(long)]
    pub root: String,

    #[arg(long)]
    pub project_id: Option<String>,

    #[arg(long)]
    pub project_name: Option<String>,

    #[arg(long, default_value = "")]
    pub commit_sha_before: String,

    #[arg(long, default_value = "")]
    pub commit_sha_after: String,

    #[arg(long)]
    pub incremental: bool,

    #[arg(long)]
    pub changed_files_manifest: Option<String>,

    #[arg(long)]
    pub deleted_files_manifest: Option<String>,

    #[arg(long)]
    pub cache_dir: Option<String>,

    #[arg(long)]
    pub ignore_cache: bool,

    #[arg(long)]
    pub disable_message_scan: bool,

    #[arg(long)]
    pub dry_run: bool,

    #[arg(long)]
    pub verbose: bool,

    #[arg(long)]
    pub summary_output: Option<String>,

    #[arg(long)]
    pub neo4j_uri: Option<String>,

    #[arg(long)]
    pub neo4j_user: Option<String>,

    #[arg(long)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow

    #[arg(long)]
    pub neo4j_db: Option<String>,

    #[arg(long, default_missing_value = "500", num_args = 0..=1)]
    pub neo4j_batch_size: Option<i64>,

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
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = match TopologyArgs::try_parse_from(&argv) {
        Ok(args) => args,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };
    std::process::exit(run(&args));
}

/// `Path(args.root).resolve()` — canonicalize; fallback lexical absolute cho
/// path không tồn tại (Path.resolve non-strict).
fn resolve_root(raw: &str) -> PathBuf {
    if let Ok(resolved) = Path::new(raw).canonicalize() {
        return resolved;
    }
    let path = Path::new(raw);
    if path.is_absolute() {
        lexical_normalize(path)
    } else {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        lexical_normalize(&cwd.join(path))
    }
}

fn lexical_normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn run(args: &TopologyArgs) -> i32 {
    let root = resolve_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", root.to_string_lossy());
        return 2;
    }
    let project_id = args
        .project_id
        .clone()
        .or_else(|| std::env::var("PROJECT_ID").ok().filter(|v| !v.is_empty()))
        .unwrap_or_else(|| {
            root.file_name()
                .map(|name| name.to_string_lossy().to_string())
                .unwrap_or_default()
        })
        .trim()
        .to_string();
    let changed = manifest_paths(
        args.changed_files_manifest.as_deref().unwrap_or(""),
        &root,
    );
    let deleted = manifest_paths(
        args.deleted_files_manifest.as_deref().unwrap_or(""),
        &root,
    );
    // Full recomputation deliberate (như Python) — manifests chỉ phục vụ
    // summary, không cắt phạm vi parse.
    let mut result = match analyze_project(&AnalyzeInput {
        root: &root,
        project_id: &project_id,
        changed_paths: None,
        deleted_paths: None,
    }) {
        Ok(result) => result,
        Err(error) => {
            eprintln!("{error}");
            return 1;
        }
    };
    result.root = root.to_string_lossy().to_string();
    // Debug dump (env-only, không nằm trong CLI contract): so sánh 1:1 với
    // Python TopologyAnalysisResult.to_dict() trong parity script.
    if let Ok(dump_path) = std::env::var("ANALYZER_TOPOLOGY_DUMP_RESULT")
        && !dump_path.is_empty()
    {
        let payload = pyjson::dumps_sorted(&result.to_json());
        let _ = std::fs::write(&dump_path, format!("{payload}\n"));
    }
    let mut summary = serde_json::Map::new();
    summary.insert("project_id".into(), json!(result.project_id));
    summary.insert("root".into(), json!(result.root));
    summary.insert("changed_descriptors".into(), json!(changed));
    summary.insert("deleted_descriptors".into(), json!(deleted));
    summary.insert("modules".into(), json!(result.modules.len()));
    summary.insert("descriptors".into(), json!(result.descriptors.len()));
    summary.insert("dependencies".into(), json!(result.dependencies.len()));
    summary.insert("endpoints".into(), json!(result.endpoints.len()));
    summary.insert("frameworks".into(), json!(result.frameworks.len()));
    summary.insert(
        "diagnostics".into(),
        Value::Array(
            result
                .diagnostics
                .iter()
                .map(|item| Value::Object(item.to_dict()))
                .collect(),
        ),
    );
    summary.insert(
        "coverage".into(),
        json!({
            "mode": "static_allowlist",
            "build_execution": false,
            "secret_values": false,
        }),
    );
    if !args.dry_run {
        summary.insert(
            "graph_writes".into(),
            write_graph(args, &result).unwrap_or_else(|error| {
                eprintln!("[project_topology] graph write failed: {error}");
                json!({})
            }),
        );
    }
    let payload = pyjson::dumps_sorted(&Value::Object(summary));
    println!("[project_topology] {payload}");
    if let Some(summary_output) = &args.summary_output
        && !summary_output.is_empty()
    {
        let output = Path::new(summary_output);
        if let Some(parent) = output.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Err(error) = std::fs::write(output, format!("{payload}\n")) {
            eprintln!("[project_topology] summary write failed: {error}");
        }
    }
    0
}

fn manifest_paths(path: &str, root: &Path) -> Vec<String> {
    if path.is_empty() {
        return Vec::new();
    }
    descriptor_candidates(
        cortex_analyzer_framework::manifest::load_manifest_paths(path, root),
    )
}

/// `_write_graph` — create indexes → cleanup_project → write; graph disabled → {}.
fn write_graph(args: &TopologyArgs, result: &TopologyAnalysisResult) -> Result<Value, String> {
    if graph_writes_disabled() {
        return Ok(json!({}));
    }
    let provider = args
        .graph_provider
        .clone()
        .unwrap_or_else(|| "falkordb".to_string())
        .to_lowercase();
    if provider == "neo4j" {
        // Rust backend không có neo4j driver — tương đương driver None Python.
        return Ok(json!({}));
    }
    let mut store = open_store(args, &provider)?;
    let database = args.neo4j_db.clone().filter(|value| !value.is_empty());
    store
        .create_indexes(
            &[
                IndexSpec {
                    label: "ProjectModule".to_string(),
                    property: "id".to_string(),
                    index_type: "range".to_string(),
                },
                IndexSpec {
                    label: "ProjectModule".to_string(),
                    property: "project_id_normalized".to_string(),
                    index_type: "range".to_string(),
                },
                IndexSpec {
                    label: "BuildDescriptor".to_string(),
                    property: "id".to_string(),
                    index_type: "range".to_string(),
                },
                IndexSpec {
                    label: "Dependency".to_string(),
                    property: "id".to_string(),
                    index_type: "range".to_string(),
                },
                IndexSpec {
                    label: "FrameworkInstance".to_string(),
                    property: "id".to_string(),
                    index_type: "range".to_string(),
                },
                IndexSpec {
                    label: "GrpcEndpoint".to_string(),
                    property: "id".to_string(),
                    index_type: "range".to_string(),
                },
            ],
            database.as_deref(),
        )
        .map_err(|error| error.to_string())?;
    let batch_size = args.neo4j_batch_size.unwrap_or(500).max(1) as usize;
    let mut writer = ProjectTopologyWriter::new(store, database, batch_size);
    writer
        .cleanup_project(&result.project_id)
        .map_err(|error| error.to_string())?;
    let counts: BTreeMap<String, i64> = writer
        .write(&result.to_json())
        .map_err(|error: StoreError| error.to_string())?;
    let mut map = serde_json::Map::new();
    for (key, value) in counts {
        map.insert(key, json!(value));
    }
    Ok(Value::Object(map))
}

fn graph_writes_disabled() -> bool {
    matches!(
        std::env::var("CORTEX_DISABLE_GRAPH")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

/// `GraphDriverFactory` theo precedence flag → env → project_id → "hyper_graph"
/// (như `AnalyzerArgs::open_store` của framework).
fn open_store(args: &TopologyArgs, provider: &str) -> Result<Box<dyn GraphStore>, String> {
    match provider {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| "provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH".to_string())?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "hyper_graph".to_string());
            LadybugStore::open(Path::new(&path), &graph)
                .map(|store| Box::new(store) as Box<dyn GraphStore>)
                .map_err(|error| error.to_string())
        }
        "falkordb" | "falkor" => {
            if args.falkordb_path.is_some() {
                return Err("embedded FalkorDBLite (--falkordb-path) chỉ chạy phía Python; \
                            dùng --falkordb-uri cho remote"
                    .to_string());
            }
            let graph = args
                .falkordb_graph
                .clone()
                .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                .or_else(|| std::env::var("FALKORDB_DATABASE").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "hyper_graph".to_string());
            let uri = args
                .falkordb_uri
                .clone()
                .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "127.0.0.1:6379".to_string());
            cortex_analyzer_framework::cli::validate_falkordb_uri(&uri)
                .map_err(|error| error.to_string())?;
            if args.falkordb_password.is_some() {
                return Err(
                    "xác thực remote (falkordb-password): backend Rust chưa hỗ trợ".to_string(),
                );
            }
            let (host, port) = cortex_analyzer_framework::cli::parse_falkordb_uri(&uri);
            let client = FalkorDbClient::connect_verified(&host, port)
                .map_err(|error| error.to_string())?;
            Ok(Box::new(FalkorDbStore::new(client, &graph)))
        }
        other => Err(format!("unsupported --graph-provider: {other}")),
    }
}

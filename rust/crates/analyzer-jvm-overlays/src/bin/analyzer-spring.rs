//! analyzer-spring — Rust port của `tools/spring/spring_analyzer.py`
//! (phase 08 rust-full-migration).
//!
//! CLI mirror `parse_args` + cờ orchestrator (`_build_analyzer_cmd`):
//! `--root --project-id --project-name --commit-sha-* --incremental
//!  --changed-files-manifest --deleted-files-manifest --disable-message-scan
//!  --verbose` + graph-provider flags. Qdrant/message-scan nhận và bỏ qua.

use std::path::Path;

use clap::Parser;
use serde_json::Value;

use cortex_analyzer_framework::cli::{abs_root, parse_falkordb_uri, validate_falkordb_uri};
use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore, StoreError};

use analyzer_jvm_overlays::pyutil::realpath;
use analyzer_jvm_overlays::spring::cache::{default_fact_artifact_path, write_fact_artifact};
use analyzer_jvm_overlays::spring::pipeline::run_spring_foundation;
use analyzer_jvm_overlays::spring::writer::SpringFactWriter;

#[derive(Debug, Parser)]
#[command(no_binary_name = true)]
struct SpringArgs {
    #[arg(long, required = true)]
    root: String,
    #[arg(long, default_value = "auto")]
    languages: String,
    #[arg(long)]
    project_id: Option<String>,
    #[arg(long = "project_id", hide = true)]
    project_id_alt: Option<String>,
    #[arg(long)]
    project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    project_name_alt: Option<String>,
    #[arg(long)]
    language: Option<String>,
    #[arg(long)]
    repo: Option<String>,
    #[arg(long)]
    build_system: Option<String>,
    #[arg(long = "build_system", hide = true)]
    build_system_alt: Option<String>,
    #[arg(long, default_value = "")]
    commit_sha_before: String,
    #[arg(long, default_value = "")]
    commit_sha_after: String,
    #[arg(long)]
    incremental: bool,
    #[arg(long, default_value = "")]
    changed_files_manifest: String,
    #[arg(long, default_value = "")]
    deleted_files_manifest: String,
    #[arg(long)]
    cache_dir: Option<String>,
    #[arg(long)]
    ignore_cache: bool,
    #[arg(long, default_value = "")]
    spring_facts_output: String,
    #[arg(long)]
    dry_run: bool,
    #[arg(long)]
    verbose: bool,
    #[arg(long)]
    neo4j_uri: Option<String>,
    #[arg(long)]
    neo4j_user: Option<String>,
    #[arg(long)]
    neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    neo4j_db: Option<String>,
    #[arg(long, default_value_t = 1000)]
    neo4j_batch_size: i64,
    #[arg(long)]
    qdrant_url: Option<String>,
    #[arg(long)]
    qdrant_collection: Option<String>,
    #[arg(long, default_value = "auto")]
    device: String,
    #[arg(long)]
    disable_message_scan: bool,
    #[arg(long)]
    enable_message_scan: bool,
    #[arg(long)]
    message_output_dir: Option<String>,
    #[arg(long)]
    message_qdrant_collection: Option<String>,
    // graph provider args
    #[arg(long, default_value = "neo4j")]
    graph_provider: String,
    #[arg(long)]
    falkordb_uri: Option<String>,
    #[arg(long)]
    falkordb_path: Option<String>,
    #[arg(long)]
    falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    falkordb_ssl: bool,
    #[arg(long)]
    falkordb_graph: Option<String>,
    #[arg(long)]
    ladybug_path: Option<String>,
    #[arg(long)]
    ladybug_graph: Option<String>,
    #[arg(long, default_value = "auto")]
    require_neo4j: String,
}

fn main() {
    let args = SpringArgs::parse_from(std::env::args().skip(1));
    std::process::exit(run(&args));
}

fn languages_from_arg(value: &str) -> Vec<&'static str> {
    match value {
        "java" => vec!["java"],
        "kotlin" => vec!["kotlin"],
        _ => vec!["java", "kotlin"],
    }
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

fn resolve_require_neo4j(args: &SpringArgs) -> bool {
    match args.require_neo4j.to_lowercase().as_str() {
        "1" => true,
        "0" => false,
        _ => args.neo4j_uri.as_deref().map(|v| !v.is_empty()).unwrap_or(false),
    }
}

fn open_store(args: &SpringArgs) -> Result<Box<dyn GraphStore>, StoreError> {
    match args.graph_provider.to_lowercase().as_str() {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| StoreError::Invalid("provider=ladybug cần --ladybug-path".into()))?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "hyper_graph".to_string());
            Ok(Box::new(LadybugStore::open(Path::new(&path), &graph)?))
        }
        "falkordb" | "falkor" => {
            let uri = args
                .falkordb_uri
                .clone()
                .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "127.0.0.1:6379".to_string());
            validate_falkordb_uri(&uri)?;
            let (host, port) = parse_falkordb_uri(&uri);
            let client = FalkorDbClient::connect_verified(&host, port)?;
            let graph = args
                .falkordb_graph
                .clone()
                .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| args.project_id.clone().unwrap_or_else(|| "hyper_graph".into()));
            Ok(Box::new(FalkorDbStore::new(client, graph)))
        }
        other => Err(StoreError::Invalid(format!("unsupported --graph-provider: {other}"))),
    }
}

fn run(args: &SpringArgs) -> i32 {
    let root = abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return 2;
    }
    let project_id = args
        .project_id
        .clone()
        .or_else(|| args.project_id_alt.clone())
        .unwrap_or_else(|| root_basename(&root));
    let project_name = args
        .project_name
        .clone()
        .or_else(|| args.project_name_alt.clone())
        .unwrap_or_else(|| project_id.clone());
    let languages = languages_from_arg(&args.languages);

    let result = run_spring_foundation(
        &root,
        &project_id,
        &project_name,
        &languages,
        args.incremental,
        &args.changed_files_manifest,
        &args.deleted_files_manifest,
    );
    let artifact_path = if !args.spring_facts_output.is_empty() {
        args.spring_facts_output.clone()
    } else {
        default_fact_artifact_path(args.cache_dir.as_deref(), &root.to_string_lossy(), &project_id)
            .to_string_lossy()
            .to_string()
    };
    if let Err(error) = write_fact_artifact(Path::new(&artifact_path), &result) {
        eprintln!("[spring] ERROR: artifact write failed: {error}");
        return 3;
    }
    println!(
        "[spring] modules={} configs={} language_facts={} semantic_facts={} relationships={} diagnostics={} artifact={}",
        result.modules.len(),
        result.config_values.len(),
        result.language_facts.len(),
        result.semantic_facts.len(),
        result.relationships.len(),
        result.diagnostics.len(),
        artifact_path
    );
    if args.dry_run {
        return 0;
    }
    match write_graph(args, &result, &project_id) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("[spring] ERROR: {error}");
            3
        }
    }
}

fn root_basename(root: &std::path::Path) -> String {
    root.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string_lossy().to_string())
}

fn write_graph(
    args: &SpringArgs,
    result: &analyzer_jvm_overlays::spring::models::SpringAnalysisResult,
    project_id: &str,
) -> Result<i32, String> {
    if graph_writes_disabled() {
        return Ok(0);
    }
    let provider = args.graph_provider.clone();
    let provider_label = provider.clone();
    let database = if provider == "falkordb" {
        args.falkordb_graph.clone().filter(|v| !v.is_empty())
    } else {
        let require_neo4j = resolve_require_neo4j(args);
        let creds_complete = args.neo4j_uri.is_some() && args.neo4j_user.is_some() && args.neo4j_password.is_some();
        if !creds_complete {
            if require_neo4j {
                eprintln!("[spring] ERROR: --require-neo4j is on but Neo4j credentials are incomplete");
                return Ok(2);
            }
            return Ok(0);
        }
        eprintln!("[spring] ERROR: Neo4j driver creation failed: unsupported provider on Rust backend");
        return Ok(3);
    };
    let mut store = match open_store(args) {
        Ok(store) => store,
        Err(error) => {
            eprintln!(
                "[spring] ERROR: FalkorDB driver creation failed: {error:?}. Path={:?} graph={:?}.",
                args.falkordb_path, args.falkordb_graph
            );
            return Ok(3);
        }
    };

    let outcome = (|| -> Result<(), String> {
        let mut writer =
            SpringFactWriter::new(store.as_mut(), database.clone(), args.neo4j_batch_size.max(1) as usize, args.verbose);
        let mut cleanup_paths: Vec<String> = Vec::new();
        if args.incremental {
            if !args.changed_files_manifest.is_empty() {
                cleanup_paths.extend(cortex_analyzer_framework::manifest::load_manifest_paths(
                    &args.changed_files_manifest,
                    &realpath(Path::new(&args.root)),
                ));
            }
            if !args.deleted_files_manifest.is_empty() {
                cleanup_paths.extend(cortex_analyzer_framework::manifest::load_manifest_paths(
                    &args.deleted_files_manifest,
                    &realpath(Path::new(&args.root)),
                ));
            }
        }
        if !cleanup_paths.is_empty() {
            let cleanup = writer.cleanup_files(project_id, &cleanup_paths)?;
            println!(
                "[cleanup][{provider_label}] deleted_nodes={} deleted_unknown_functions=0",
                cleanup.get("deleted_nodes").copied().unwrap_or(0)
            );
        }
        let node_rows: Vec<Value> = result.semantic_facts.iter().map(|f| Value::Object(f.to_graph_node())).collect();
        let written = writer.write_fact_nodes(&node_rows)?;
        println!("[{provider_label}] spring_facts {written}/{}", result.semantic_facts.len());
        let rel_rows: Vec<Value> = result
            .relationships
            .iter()
            .map(|r| Value::Object(r.to_graph_row()))
            .collect();
        let rel_written = writer.write_relationships(&rel_rows)?;
        println!("[{provider_label}] spring_relationships {rel_written}/{}", result.relationships.len());
        Ok(())
    })();
    match outcome {
        Ok(()) => Ok(0),
        Err(error) => Err(error),
    }
}

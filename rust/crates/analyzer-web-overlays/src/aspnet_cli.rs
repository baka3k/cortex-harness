//! CLI runner cho 2 bin ASP.NET overlay — mirror `add_shared_arguments` +
//! `main` của `tools/aspnet_{core,framework}/aspnet_*_analyzer.py` và phần
//! graph (`_create_driver` + `apply_graph`) của `cli_runtime.py`.

use std::path::{Path, PathBuf};

use clap::Parser;
use serde_json::{json, Value};

use cortex_analyzer_framework::cli::{parse_falkordb_uri, validate_falkordb_uri};
use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore, StoreError};

use crate::aspnet::models::{analysis_result_to_value, AnalysisResult};
use crate::aspnet::safe_formats::redact_value;
use crate::aspnet::writer::apply_graph;
use crate::pyrepr::py_dict_repr;

#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
pub struct AspNetArgs {
    #[arg(long, required = true)]
    pub root: String,
    #[arg(long)]
    pub project_id: Option<String>,
    #[arg(long = "project_id", hide = true)]
    pub project_id_alt: Option<String>,
    #[arg(long)]
    pub project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    pub project_name_alt: Option<String>,
    #[arg(long)]
    pub language: Option<String>,
    #[arg(long)]
    pub repo: Option<String>,
    #[arg(long)]
    pub build_system: Option<String>,
    #[arg(long = "build_system", hide = true)]
    pub build_system_alt: Option<String>,
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
    pub cache_dir: Option<String>,
    #[arg(long)]
    pub ignore_cache: bool,
    /// `--aspnet-core-preview-output` / `--aspnet-framework-preview-output`.
    #[arg(long = "aspnet-core-preview-output")]
    pub aspnet_core_preview_output: Option<String>,
    #[arg(long = "aspnet-framework-preview-output")]
    pub aspnet_framework_preview_output: Option<String>,
    #[arg(long, default_value = "")]
    pub diagnostics_output: String,
    #[arg(long, default_value = "auto")]
    pub semantic: String,
    #[arg(long, default_value = "")]
    pub roslyn_worker_project: String,
    #[arg(long = "fail-on", default_value = "")]
    pub fail_on: String,
    #[arg(long)]
    pub dry_run: bool,
    #[arg(long)]
    pub quiet: bool,
    #[arg(long)]
    pub verbose: bool,
    #[arg(long)]
    pub neo4j_uri: Option<String>,
    #[arg(long)]
    pub neo4j_user: Option<String>,
    #[arg(long)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub neo4j_db: Option<String>,
    #[arg(long, default_value_t = 1000)]
    pub neo4j_batch_size: i64,
    #[arg(long)]
    pub qdrant_url: Option<String>,
    #[arg(long)]
    pub qdrant_collection: Option<String>,
    #[arg(long, default_value = "auto")]
    pub device: String,
    #[arg(long)]
    pub disable_message_scan: bool,
    #[arg(long)]
    pub enable_message_scan: bool,
    #[arg(long)]
    pub message_output_dir: Option<String>,
    #[arg(long)]
    pub message_qdrant_collection: Option<String>,
    // add_graph_provider_arguments + add_require_neo4j_argument
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
    #[arg(long, default_value = "auto")]
    pub require_neo4j: String,
}

pub struct ResolvedArgs {
    pub args: AspNetArgs,
    pub project_id: String,
    pub project_name: String,
    pub preview_output: String,
}

impl ResolvedArgs {
    pub fn parse(argv: &[String]) -> Self {
        let mut args = AspNetArgs::parse_from(argv);
        if args.project_id.is_none() {
            args.project_id = args.project_id_alt.take();
        }
        if args.project_name.is_none() {
            args.project_name = args.project_name_alt.take();
        }
        if args.build_system.is_none() {
            args.build_system = args.build_system_alt.take();
        }
        // Env defaults như add_shared_arguments.
        let env = |name: &str| std::env::var(name).ok().filter(|value| !value.is_empty());
        let project_id = args
            .project_id
            .clone()
            .or_else(|| env("PROJECT_ID"))
            .unwrap_or_default();
        let project_name = args
            .project_name
            .clone()
            .or_else(|| env("PROJECT_NAME"))
            .unwrap_or_default();
        if args.neo4j_uri.is_none() {
            args.neo4j_uri = env("NEO4J_URI");
        }
        if args.neo4j_user.is_none() {
            args.neo4j_user = env("NEO4J_USER");
        }
        if args.neo4j_password.is_none() { // sensitive-guard:allow
            args.neo4j_password = env("NEO4J_PASS"); // sensitive-guard:allow
        }
        if args.neo4j_db.is_none() {
            args.neo4j_db = env("NEO4J_DB");
        }
        if args.cache_dir.is_none() {
            args.cache_dir = env("QDRANT_CACHE_DIR");
        }
        if args.qdrant_url.is_none() {
            args.qdrant_url = env("QDRANT_URL");
        }
        if args.qdrant_collection.is_none() {
            args.qdrant_collection = env("QDRANT_COLLECTION");
        }
        if args.message_output_dir.is_none() {
            args.message_output_dir = env("MESSAGE_OUTPUT_DIR");
        }
        if args.message_qdrant_collection.is_none() {
            args.message_qdrant_collection = env("MESSAGE_QDRANT_COLLECTION");
        }
        if args.graph_provider.is_none() {
            args.graph_provider = env("CORTEX_GRAPH_PROVIDER");
        }
        if args.ladybug_path.is_none() {
            args.ladybug_path = env("LADYBUG_PATH");
        }
        if args.ladybug_graph.is_none() {
            args.ladybug_graph = env("LADYBUG_GRAPH");
        }
        if args.falkordb_path.is_none() {
            args.falkordb_path = env("FALKORDB_PATH");
        }
        if args.falkordb_uri.is_none() {
            args.falkordb_uri = env("FALKORDB_URI");
        }
        if args.falkordb_password.is_none() { // sensitive-guard:allow
            args.falkordb_password = env("FALKORDB_PASSWORD"); // sensitive-guard:allow
        }
        if args.falkordb_graph.is_none() {
            let env_graph = env("FALKORDB_GRAPH").or_else(|| env("FALKORDB_DATABASE"));
            args.falkordb_graph = env_graph;
        }
        // preview_output: cờ output-specific của từng binary (default "").
        let preview_output = args
            .aspnet_core_preview_output
            .clone()
            .or_else(|| args.aspnet_framework_preview_output.clone())
            .unwrap_or_default();
        Self {
            args,
            project_id,
            project_name,
            preview_output,
        }
    }
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

pub fn resolve_require_neo4j(args: &AspNetArgs) -> bool {
    match args.require_neo4j.to_lowercase().as_str() {
        "1" => true,
        "0" => false,
        _ => args.neo4j_uri.as_deref().map(|v| !v.is_empty()).unwrap_or(false),
    }
}

/// `load_manifest` — sorted(load_manifest_paths(path, root)).
pub fn load_manifest(path: &str, root: &Path) -> Vec<String> {
    if path.is_empty() {
        return Vec::new();
    }
    let mut paths: Vec<String> =
        cortex_analyzer_framework::manifest::load_manifest_paths(path, root)
            .into_iter()
            .collect();
    paths.sort();
    paths
}

/// `_atomic_write`.
fn atomic_write(path: &str, payload: &str) -> Result<(), String> {
    let target = crate::pyutil::abs_path(Path::new(path));
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    let mut temporary = target.clone().into_os_string();
    temporary.push(".tmp");
    std::fs::write(&temporary, payload).map_err(|error| error.to_string())?;
    std::fs::rename(&temporary, &target).map_err(|error| error.to_string())
}

/// `write_outputs`.
pub fn write_outputs(resolved: &ResolvedArgs, result: &AnalysisResult) -> Result<(), String> {
    if !resolved.preview_output.is_empty() {
        let payload = crate::pyjson::dumps_pretty_sorted_ascii(&analysis_result_to_value(result));
        atomic_write(&resolved.preview_output, &payload)?;
    }
    if !resolved.args.diagnostics_output.is_empty() {
        let diagnostics: Vec<Value> = result
            .diagnostics
            .iter()
            .map(|item| {
                json!({
                    "code": item.code,
                    "message": item.message,
                    "severity": item.severity,
                    "file_path": item.source.file_path,
                    "start_line": item.source.start_line,
                    "details": item.details,
                })
            })
            .collect();
        let redacted = redact_value("diagnostics", &Value::Array(diagnostics));
        let payload = format!(
            "{}\n",
            crate::pyjson::dumps_pretty_sorted_ascii(&redacted).trim_end_matches('\n')
        );
        atomic_write(&resolved.args.diagnostics_output, &payload)?;
    }
    Ok(())
}

/// `fail_code`.
pub fn fail_code(resolved: &ResolvedArgs, result: &AnalysisResult) -> i32 {
    let fail_on = resolved.args.fail_on.as_str();
    if fail_on == "error"
        && result
            .diagnostics
            .iter()
            .any(|item| item.severity == "error")
    {
        return 4;
    }
    if fail_on == "partial" && result.coverage_status != "complete" {
        return 4;
    }
    if fail_on == "truncation"
        && result
            .diagnostics
            .iter()
            .any(|item| item.code.contains("truncat"))
    {
        return 4;
    }
    0
}

fn open_falkordb(resolved: &ResolvedArgs) -> Result<(Box<dyn GraphStore>, Option<String>), StoreError> {
    let args = &resolved.args;
    let graph = args
        .falkordb_graph
        .clone()
        .filter(|value| !value.is_empty())
        .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
        .unwrap_or_else(|| {
            if resolved.project_id.is_empty() {
                "hyper_graph".into()
            } else {
                resolved.project_id.clone()
            }
        });
    if args
        .falkordb_path
        .clone()
        .or_else(|| std::env::var("FALKORDB_PATH").ok().filter(|v| !v.is_empty()))
        .is_some()
    {
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

/// `_create_driver` + `apply_graph` — Ok(None) = graphless (driver None).
pub fn run_apply_graph(
    resolved: &ResolvedArgs,
    result: &AnalysisResult,
) -> Result<Option<crate::aspnet::writer::ApplyTotals>, String> {
    let args = &resolved.args;
    if graph_writes_disabled() {
        return Ok(None);
    }
    let provider = args
        .graph_provider
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "falkordb".into())
        .to_lowercase();
    if provider == "falkordb" || provider == "falkor" {
        let (mut store, database) = open_falkordb(resolved).map_err(|error| error.to_string())?;
        let totals = apply_graph(
            store.as_mut(),
            result,
            database.as_deref(),
            args.neo4j_batch_size.max(1) as usize,
            args.verbose,
        )?;
        return Ok(Some(totals));
    }
    if provider == "ladybug" {
        let path = args
            .ladybug_path
            .clone()
            .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
            .ok_or_else(|| {
                "provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH".to_string()
            })?;
        let graph = args
            .ladybug_graph
            .clone()
            .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
            .unwrap_or_else(|| {
                if resolved.project_id.is_empty() {
                    "hyper_graph".into()
                } else {
                    resolved.project_id.clone()
                }
            });
        let mut store: Box<dyn GraphStore> = Box::new(
            LadybugStore::open(Path::new(&path), &graph.clone()).map_err(|error| error.to_string())?,
        );
        let totals = apply_graph(
            store.as_mut(),
            result,
            Some(graph.as_str()),
            args.neo4j_batch_size.max(1) as usize,
            args.verbose,
        )?;
        return Ok(Some(totals));
    }
    // Neo4j lane — credentials đầy đủ mới tới đây (graphless nếu thiếu).
    let creds_complete = args.neo4j_uri.is_some()
        && args.neo4j_user.is_some()
        && args.neo4j_password.is_some(); // sensitive-guard:allow
    if !creds_complete {
        if resolve_require_neo4j(args) {
            return Err("--require-neo4j is on but Neo4j credentials are incomplete".into());
        }
        return Ok(None);
    }
    Err("Neo4j driver is not supported on the Rust backend; use --graph-provider falkordb".into())
}

/// Fn-pointer kiểu pipeline (7 tham số của run_*_analysis trừ `root`).
pub type AnalysisPipeline = fn(
    &Path,
    &str,
    &str,
    &str,
    &[String],
    &[String],
    Option<&str>,
    bool,
) -> Result<AnalysisResult, String>;

/// Summary dict repr theo insertion order Python của `apply_graph` totals.
pub fn totals_repr(totals: &Option<crate::aspnet::writer::ApplyTotals>) -> String {
    match totals {
        None => py_dict_repr(&[
            ("stage", json!("graphless")),
            ("nodes", json!(0)),
            ("relationships", json!(0)),
        ]),
        Some(totals) => {
            py_dict_repr(&[
                ("stage", json!(totals.stage)),
                ("nodes", json!(totals.nodes)),
                ("relationships", json!(totals.relationships)),
                ("preserved_modules", json!(totals.preserved_modules)),
            ])
        }
    }
}

/// `main` của aspnet_*_analyzer.py. `framework_label` = "aspnet_core" /
/// "aspnet_framework"; `run_analysis` là pipeline tương ứng.
pub fn run(framework_label: &'static str, run_analysis: AnalysisPipeline) -> i32 {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let resolved = ResolvedArgs::parse(&argv);
    let root: PathBuf = crate::pyutil::realpath(Path::new(&resolved.args.root));
    if !root.is_dir() {
        eprintln!("Root not found: {}", resolved.args.root);
        return 2;
    }
    let project_id = if !resolved.project_id.is_empty() {
        resolved.project_id.clone()
    } else {
        root.file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_default()
    };
    let project_name = if !resolved.project_name.is_empty() {
        resolved.project_name.clone()
    } else {
        project_id.clone()
    };
    // load_manifest(deleted) KHÔNG gate theo --incremental; selected thì có.
    let deleted_paths = load_manifest(&resolved.args.deleted_files_manifest, &root);
    let selected_paths = if resolved.args.incremental {
        load_manifest(&resolved.args.changed_files_manifest, &root)
    } else {
        Vec::new()
    };
    let result = match run_analysis(
        &root,
        &project_id,
        &project_name,
        &resolved.args.semantic,
        &deleted_paths,
        &selected_paths,
        if resolved.args.roslyn_worker_project.is_empty() {
            None
        } else {
            Some(resolved.args.roslyn_worker_project.as_str())
        },
        resolved.args.verbose,
    ) {
        Ok(result) => result,
        Err(error) => {
            eprintln!("[{framework_label}] analysis failed: {error}");
            return 3;
        }
    };
    if let Err(error) = write_outputs(&resolved, &result) {
        eprintln!("[{framework_label}] output write failed: {error}");
        return 3;
    }
    if !resolved.args.quiet {
        println!(
            "[{framework_label}] modules={} facts={} relationships={} diagnostics={} coverage={}",
            result.modules.len(),
            result.facts.len(),
            result.relationships.len(),
            result.diagnostics.len(),
            result.coverage_status
        );
    }
    let code = fail_code(&resolved, &result);
    if code != 0 || resolved.args.dry_run {
        return code;
    }
    let totals = match run_apply_graph(&resolved, &result) {
        Ok(totals) => totals,
        Err(error) => {
            eprintln!("[{framework_label}] graph write failed: {error}");
            return 3;
        }
    };
    if resolved.args.verbose {
        println!("[{framework_label}] graph={}", totals_repr(&totals));
    }
    0
}

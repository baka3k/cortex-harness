//! analyzer-js — Rust port của `tools/js/js_analyzer.py` (phase 05).
//!
//! Pipeline: scan (`_scan_js_files`) → manifest selection + import-graph
//! impacted expansion → cleanup changed∪deleted → parse (tree-sitter
//! javascript) → call resolution (`resolve_callee_id`) →
//! `LanguageCodeWriter::write_all(use_full_writers=True,
//! files_variant="with_jsx")` → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (plan key decision #3):
//! * Qdrant/embedding KHÔNG port — flags nhận và bỏ qua (vector plane Python).
//! * Message scan là plane Python-side; flag nhận (mặc định bật như Python),
//!   skip có kiểm soát.
//! * Parse cache / neo4j resume state — nhận và bỏ qua (Rust luôn parse đủ).

mod jsparse;
mod pipeline;

use std::path::PathBuf;

use clap::Parser as ClapParser;

use cortex_analyzer_framework::cli::{abs_root, parse_falkordb_uri, validate_falkordb_uri};
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_graph_writer::store::{FalkorDbStore, LadybugStore, GraphStore, StoreError};
use cortex_falkordb::client::FalkorDbClient;

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = JsArgs::parse_from(&argv);
    let code = run(&args);
    std::process::exit(code);
}

fn run(args: &JsArgs) -> i32 {
    let root = abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return 2;
    }

    // Store mở trước dry-run như Python main (driver trước dry-run check).
    let store = if args.graph_writes_disabled() {
        None
    } else {
        match open_store(args) {
            Ok(store) => Some(store),
            Err(error) => {
                eprintln!("{error}");
                return 1;
            }
        }
    };

    // Manifests (Python main: sorted(load_manifest_paths(...)) khi incremental).
    let mut changed_manifest_files: Vec<String> = Vec::new();
    let mut deleted_manifest_files: Vec<String> = Vec::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            changed_manifest_files = load_manifest_paths(manifest, &root).into_iter().collect();
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            deleted_manifest_files = load_manifest_paths(manifest, &root).into_iter().collect();
        }
        if args.verbose {
            println!(
                "[diff] incremental manifests changed={} deleted={}",
                changed_manifest_files.len(),
                deleted_manifest_files.len()
            );
        }
    }

    if args.dry_run {
        let files = pipeline::scan_js_files(&root);
        if args.incremental && !changed_manifest_files.is_empty() {
            let manifest_set: std::collections::HashSet<&String> =
                changed_manifest_files.iter().collect();
            let selected: Vec<&PathBuf> = files
                .iter()
                .filter(|path| {
                    manifest_set.contains(&cortex_analyzer_framework::scan::rel_posix(&root, path))
                })
                .collect();
            println!(
                "Dry run (incremental): {} JavaScript files selected (manifest={})",
                selected.len(),
                changed_manifest_files.len()
            );
        } else {
            println!("Dry run: {} JavaScript files found", files.len());
        }
        return 0;
    }

    match pipeline::build_call_graph(
        args,
        &root,
        &changed_manifest_files,
        &deleted_manifest_files,
        store,
    ) {
        Ok(_outcome) => 0,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

// ── CLI contract — mirror `parse_args` của js_analyzer.py + flags orchestrator
// `_build_analyzer_cmd` gửi thêm. Cờ lane vector/message/cache nhận-và-bỏ-qua.

#[derive(Debug, Clone, ClapParser)]
#[command(no_binary_name = true)]
pub struct JsArgs {
    /// Root folder chứa JavaScript sources.
    #[arg(long, required = true)]
    pub root: String,

    /// Harness dev.json config — nhận và bỏ qua.
    #[arg(long, hide = true)]
    pub config: Option<String>,

    // Neo4j legacy — nhận-và-bỏ-qua (graph provider bên dưới).
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,
    #[arg(long, default_value = "1000")]
    pub neo4j_batch_size: i64,
    #[arg(long, hide = true)]
    pub neo4j_state: Option<String>,
    #[arg(long, hide = true)]
    pub disable_neo4j_resume: bool,

    // Graph provider — add_graph_provider_args.
    #[arg(long, default_value = "falkordb")]
    pub graph_provider: String,
    #[arg(long)]
    pub falkordb_uri: Option<String>,
    #[arg(long)]
    pub falkordb_path: Option<String>,
    #[arg(long)]
    pub falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub falkordb_graph: Option<String>,
    #[arg(long)]
    pub ladybug_path: Option<String>,
    #[arg(long)]
    pub ladybug_graph: Option<String>,

    // Qdrant/embedding lane — nhận-và-bỏ-qua (key decision #3).
    #[arg(long, hide = true)]
    pub qdrant_url: Option<String>,
    #[arg(long, hide = true)]
    pub qdrant_collection: Option<String>,
    #[arg(long, hide = true)]
    pub embed_model: Option<String>,
    #[arg(long, hide = true)]
    pub max_embed_chars: Option<i64>,
    #[arg(long, hide = true)]
    pub chunk_embed: bool,
    #[arg(long, hide = true)]
    pub device: Option<String>,
    #[arg(long, hide = true)]
    pub batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_timeout: Option<f64>,
    #[arg(long, hide = true)]
    pub qdrant_retries: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_retry_sleep: Option<f64>,

    // Message scan — plane Python; mặc định bật như Python set_defaults.
    #[arg(long)]
    pub enable_message_scan: bool,
    #[arg(long)]
    pub disable_message_scan: bool,
    #[arg(long, hide = true)]
    pub message_output_dir: Option<String>,
    #[arg(long, hide = true)]
    pub message_qdrant_collection: Option<String>,

    // Cache — nhận-và-bỏ-qua (Rust parse trực tiếp, không resume state).
    #[arg(long, hide = true)]
    pub cache_dir: Option<String>,
    #[arg(long, hide = true)]
    pub keep_cache: bool,
    #[arg(long, hide = true)]
    pub disable_parse_cache: bool,
    #[arg(long, hide = true)]
    pub ignore_cache: bool,

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
    #[arg(long)]
    pub changed_files_manifest: Option<String>,
    #[arg(long)]
    pub deleted_files_manifest: Option<String>,

    #[arg(long)]
    pub dry_run: bool,
    #[arg(long)]
    pub verbose: bool,
}

impl JsArgs {
    pub fn parse_from(argv: &[String]) -> Self {
        let mut argv: Vec<String> = argv.to_vec();
        // Python `set_defaults(enable_message_scan=True)`; `--disable` thắng.
        argv.retain(|arg| arg != "--enable-message-scan");
        if !argv.iter().any(|arg| arg == "--disable-message-scan") {
            argv.push("--enable-message-scan".to_string());
        }
        let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
        let mut args = <Self as ClapParser>::parse_from(strs);
        if args.project_id.is_none() {
            args.project_id = args.project_id_alt.take();
        }
        if args.project_name.is_none() {
            args.project_name = args.project_name_alt.take();
        }
        if args.build_system.is_none() {
            args.build_system = args.build_system_alt.take();
        }
        args
    }

    /// `project_id = args.project_id or os.path.basename(os.path.abspath(root))`.
    pub fn project_id_or_root(&self) -> String {
        self.project_id.clone().unwrap_or_else(|| {
            let abs = abs_root(&self.root);
            abs.file_name()
                .map(|name| name.to_string_lossy().to_string())
                .unwrap_or_else(|| self.root.clone())
        })
    }

    pub fn project_name_or_id(&self) -> String {
        self.project_name
            .clone()
            .unwrap_or_else(|| self.project_id_or_root())
    }

    /// `CORTEX_DISABLE_GRAPH=1|true|yes|on` ⇒ parse-only (khớp
    /// `prepare_graph_args` Python).
    pub fn graph_writes_disabled(&self) -> bool {
        matches!(
            std::env::var("CORTEX_DISABLE_GRAPH")
                .unwrap_or_default()
                .trim()
                .to_lowercase()
                .as_str(),
            "1" | "true" | "yes" | "on"
        )
    }

    pub fn message_scan_enabled(&self) -> bool {
        !self.disable_message_scan
    }
}

/// Mở GraphStore theo provider — tương đương `GraphDriverFactory` +
/// `prepare_graph_args` (graph precedence: flag → env → project_id).
fn open_store(args: &JsArgs) -> Result<Box<dyn GraphStore>, StoreError> {
    let provider = args.graph_provider.to_lowercase();
    match provider.as_str() {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| {
                    StoreError::Invalid(
                        "provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH".into(),
                    )
                })?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| args.project_id_or_root());
            Ok(Box::new(LadybugStore::open(
                std::path::Path::new(&path),
                &graph,
            )?))
        }
        "falkordb" | "falkor" => {
            let graph = args
                .falkordb_graph
                .clone()
                .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| args.project_id_or_root());
            if let Some(path) = args
                .falkordb_path
                .clone()
                .or_else(|| std::env::var("FALKORDB_PATH").ok().filter(|v| !v.is_empty()))
            {
                // Embedded FalkorDBLite chỉ chạy phía Python — fail-closed.
                let _ = path;
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
            if args.falkordb_password.is_some() {
                return Err(StoreError::Invalid(
                    // sensitive-guard:allow (tên flag, không phải secret)
                    "xác thực remote (falkordb_password): backend Rust chưa hỗ trợ — \
                     chạy provider này phía Python"
                        .into(),
                ));
            }
            let (host, port) = parse_falkordb_uri(&uri);
            let client = FalkorDbClient::connect_verified(&host, port)?;
            Ok(Box::new(FalkorDbStore::new(client, graph)))
        }
        other => Err(StoreError::Invalid(format!(
            "unsupported --graph-provider: {other}"
        ))),
    }
}

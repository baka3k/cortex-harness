//! analyzer-shell — Rust port của `tools/shell/shell_analyzer.py` (phase 05).
//!
//! Khác biệt scope có chủ đích: `_sync_vectors` là plane Python (embedding
//! tách khỏi analyzer — key decision #3); không qdrant ⇒ `vector_status=disabled`,
//! `vectors=0` — khớp Python chạy không có `--qdrant-url`.

mod mapping;
mod models;
mod parser;
mod pipeline;
mod rows;
mod shlex;

use clap::Parser as ClapParser;

use cortex_analyzer_framework::cli::{abs_root, parse_falkordb_uri, validate_falkordb_uri};
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::{FalkorDbStore, LadybugStore};
use cortex_falkordb::client::FalkorDbClient;

use crate::mapping::load_program_mappings;
use crate::models::ProgramMapping;
use crate::pipeline::run_shell_analysis;

#[derive(Debug, Clone, ClapParser)]
#[command(no_binary_name = true)]
struct ShellArgs {
    /// Vị trí lựa chọn khác --root (Python `path` positional).
    pub path: Option<String>,

    #[arg(long)]
    pub root: Option<String>,

    #[arg(long = "project-id")]
    pub project_id: Option<String>,
    #[arg(long = "project_id", hide = true)]
    pub project_id_alt: Option<String>,

    #[arg(long = "project-name")]
    pub project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    pub project_name_alt: Option<String>,

    #[arg(long)]
    pub repo: Option<String>,
    #[arg(long = "build-system", default_value = "shell")]
    pub build_system: String,
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
    pub cache_dir: Option<String>,
    /// Ledger JSON: program_id/source_path/evidence_hash.
    #[arg(long)]
    pub program_mapping_ledger: Option<String>,
    #[arg(long)]
    pub program_mapping_id_field: Option<String>,
    #[arg(long)]
    pub program_mapping_source_field: Option<String>,
    #[arg(long)]
    pub program_mapping_evidence_field: Option<String>,

    #[arg(long)]
    pub ignore_cache: bool,
    #[arg(long)]
    pub dry_run: bool,
    #[arg(long = "output")]
    pub output: Option<String>,
    #[arg(short = 'o', hide = true)]
    pub output_short: Option<String>,
    #[arg(long)]
    pub pretty: bool,
    #[arg(long)]
    pub verbose: bool,

    // Graph provider — giữ contract như add_graph_provider_args.
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

    // Neo4j legacy flags — nhận-và-bỏ-qua.
    #[arg(long)]
    pub neo4j_uri: Option<String>,
    #[arg(long)]
    pub neo4j_user: Option<String>,
    #[arg(long)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    pub neo4j_db: Option<String>,
    #[arg(long, default_value = "1000")]
    pub neo4j_batch_size: i64,

    // Qdrant lane — nhận-và-bỏ-qua (embedding plane Python).
    #[arg(long)]
    pub qdrant_url: Option<String>,
    #[arg(long, default_value = "shell_functions")]
    pub qdrant_collection: Option<String>,
    #[arg(long, default_value = "")]
    pub embed_model: Option<String>,
    #[arg(long, default_value = "cpu")]
    pub device: Option<String>,
    #[arg(long, default_value = "4")]
    pub batch_size: i64,
    #[arg(long, default_value = "4000")]
    pub max_embed_chars: i64,
    #[arg(long, default_value = "128")]
    pub qdrant_batch_size: i64,
    #[arg(long, default_value = "300.0")]
    pub qdrant_timeout: f64,
    #[arg(long, default_value = "3")]
    pub qdrant_retries: i64,
    #[arg(long, default_value = "2.0")]
    pub qdrant_retry_sleep: f64,

    #[arg(long)]
    pub enable_message_scan: bool,
    #[arg(long)]
    pub disable_message_scan: bool,
    #[arg(long)]
    pub message_output_dir: Option<String>,
    #[arg(long, default_value = "")]
    pub message_qdrant_collection: Option<String>,

    /// Phase-06 embedding-input artifact path — set only by the orchestrator
    /// native embedding pass (graphless). Mirrors the framework contract.
    #[arg(long)]
    pub embedding_input_output: Option<String>,
}

impl ShellArgs {
    /// Trimmed, non-empty embedding-input artifact path (None ⇒ no emission).
    fn embedding_input_output(&self) -> Option<&str> {
        self.embedding_input_output
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
    }

    fn resolved_project_id(&self, root: &str) -> String {
        self.project_id
            .clone()
            .or_else(|| self.project_id_alt.clone())
            .unwrap_or_else(|| {
                abs_root(root)
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| root.to_string())
            })
    }

    fn resolved_project_name(&self, project_id: &str) -> String {
        self.project_name
            .clone()
            .or_else(|| self.project_name_alt.clone())
            .unwrap_or_else(|| project_id.to_string())
    }

    fn output_path(&self) -> Option<&String> {
        self.output.as_ref().or(self.output_short.as_ref())
    }

    /// `graph_writes_disabled` — CORTEX_DISABLE_GRAPH (embedding pass).
    fn graph_writes_disabled(&self) -> bool {
        matches!(
            std::env::var("CORTEX_DISABLE_GRAPH")
                .unwrap_or_default()
                .trim()
                .to_lowercase()
                .as_str(),
            "1" | "true" | "yes" | "on"
        )
    }

    fn open_store(&self, graph: &str) -> Result<Box<dyn cortex_graph_writer::store::GraphStore>, String> {
        match self.graph_provider.to_lowercase().as_str() {
            "ladybug" => {
                let path = self
                    .ladybug_path
                    .clone()
                    .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                    .ok_or("provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH")?;
                Ok(Box::new(LadybugStore::open(
                    std::path::Path::new(&path),
                    self.ladybug_graph.as_deref().unwrap_or(graph),
                )
                .map_err(|e| e.to_string())?))
            }
            _ => {
                let uri = self
                    .falkordb_uri
                    .clone()
                    .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| "127.0.0.1:6379".to_string());
                validate_falkordb_uri(&uri).map_err(|e| e.to_string())?;
                let (host, port) = parse_falkordb_uri(&uri);
                let client = FalkorDbClient::connect_verified(&host, port)
                    .map_err(|e| e.to_string())?;
                Ok(Box::new(FalkorDbStore::new(client, graph)))
            }
        }
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = ShellArgs::parse_from(&argv);
    std::process::exit(run(&args));
}

fn run(args: &ShellArgs) -> i32 {
    let root_raw = args
        .root
        .clone()
        .or_else(|| args.path.clone())
        .unwrap_or_default();
    // Python: os.path.realpath — RESOLVE symlink (khác abspath).
    let root = match std::fs::canonicalize(&root_raw) {
        Ok(path) => path,
        Err(_) => {
            eprintln!("A valid --root directory is required");
            return 2;
        }
    };
    if !root.is_dir() {
        eprintln!("A valid --root directory is required");
        return 2;
    }
    let project_id = args.resolved_project_id(&root.to_string_lossy());
    let project_name = args.resolved_project_name(&project_id);
    let repo = args
        .repo
        .clone()
        .unwrap_or_else(|| project_id.clone());

    let changed = if args.incremental {
        args.changed_files_manifest
            .as_deref()
            .map(|manifest| {
                cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                    .into_iter()
                    .collect::<Vec<_>>()
            })
    } else {
        None
    };
    let deleted: Vec<String> = if args.incremental {
        args.deleted_files_manifest
            .as_deref()
            .map(|manifest| {
                cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                    .into_iter()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default()
    } else {
        Default::default()
    };

    let result = run_shell_analysis(&root, &project_id, changed.as_deref(), &deleted);

    let program_mappings: Vec<ProgramMapping> = if let Some(ledger) = &args.program_mapping_ledger {
        match load_program_mappings(
            ledger,
            &root,
            args.program_mapping_id_field
                .as_deref()
                .map(str::to_string)
                .or_else(|| Some(std::env::var("SHELL_PROGRAM_MAPPING_ID_FIELD").unwrap_or("program_id".into())))
                .as_deref()
                .unwrap_or("program_id"),
            args.program_mapping_source_field
                .as_deref()
                .map(str::to_string)
                .or_else(|| Some(std::env::var("SHELL_PROGRAM_MAPPING_SOURCE_FIELD").unwrap_or("source_path".into())))
                .as_deref()
                .unwrap_or("source_path"),
            args.program_mapping_evidence_field
                .as_deref()
                .map(str::to_string)
                .or_else(|| Some(std::env::var("SHELL_PROGRAM_MAPPING_EVIDENCE_FIELD").unwrap_or("evidence_hash".into())))
                .as_deref()
                .unwrap_or("evidence_hash"),
        ) {
            Ok(mappings) => mappings,
            Err(error) => {
                eprintln!("Invalid --program-mapping-ledger: {error}");
                return 2;
            }
        }
    } else {
        Vec::new()
    };

    // Payload JSON — sort_keys=True phía Python; serde_json Map mặc định sorted.
    let payload = serde_json::to_string(&result).unwrap_or_default();
    if let Some(output) = args.output_path()
        && let Err(error) = std::fs::write(output, payload.clone() + "\n") {
            eprintln!("[output] write failed: {error}");
            return 1;
        }

    let vector_count = 0usize;
    // ── Phase-06 embedding-input artifact (only when orchestrator asked) ───
    if !args.dry_run
        && let Some(output) = args.embedding_input_output()
        && let Err(error) =
            cortex_analyzer_framework::embedding_artifact::maybe_emit_embedding_artifact(
                    Some(output),
                    cortex_analyzer_framework::embedding_artifact::EmbeddingEmission {
                        parser: "shell",
                        project_id: &result.project_id,
                        root_scope: &repo,
                        full_replace: !args.incremental,
                        scanned_directory: true,
                        files_selected: result.changed_paths.to_vec(),
                        files_deleted: result.deleted_paths.to_vec(),
                        categories: rows::embedding_categories(
                            &result,
                            &project_name,
                            &repo,
                            &program_mappings,
                        ),
                    },
                )
            {
                eprintln!("[embedding] shell artifact failed: {error}");
                return 1;
            }
    if !args.dry_run && !args.graph_writes_disabled() {
        let graph = args
            .falkordb_graph
            .clone()
            .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
            .unwrap_or_else(|| project_id.clone());
        let store = match args.open_store(&graph) {
            Ok(store) => store,
            Err(error) => {
                eprintln!("[graph] open store failed: {error}");
                return 1;
            }
        };
        if args.falkordb_password.is_some() {
            eprintln!("[graph] xác thực remote: backend Rust chưa hỗ trợ");
            return 1;
        }
        let mut writer = LanguageCodeWriter::new(store, None, args.neo4j_batch_size.max(1) as usize, args.verbose);
        match rows::write_graph(
            &mut writer,
            &result,
            &program_mappings,
            &project_name,
            &repo,
            args.incremental,
        ) {
            Ok(counts) => {
                if args.verbose && counts.unresolved_relations > 0 {
                    println!(
                        "[graph] optional unresolved shell relations skipped={}",
                        counts.unresolved_relations
                    );
                }
            }
            Err(error) => {
                eprintln!("[graph] write failed: {error}");
                return 1;
            }
        }
    }

    if args.dry_run || args.pretty {
        println!("{payload}");
    }
    let function_count: usize = result
        .files
        .iter()
        .map(|file| file.functions.len())
        .sum();
    // Vector sync là plane Python; Rust backend không upsert → disabled.
    let vector_status = "disabled";
    let _ = vector_count;
    println!(
        "[SCAN_RESULT] parser=shell files={} functions={} vectors={} vector_status={vector_status}",
        result.files.len(),
        function_count,
        vector_count
    );
    0
}

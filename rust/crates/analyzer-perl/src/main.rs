//! analyzer-perl — Rust port của `tools/perl/perl_analyzer.py` (phase 05).
//!
//! Khác biệt scope có chủ đích:
//! * `_sync_vectors` là plane Python (embedding tách khỏi analyzer — key
//!   decision #3): không qdrant ⇒ `vectors=0`, `vector_status=disabled`,
//!   cờ qdrant/embedding/message-scan nhận-và-bỏ-qua.
//! * Graph provider contract giữ nguyên như `add_graph_provider_args` +
//!   `prepare_graph_args` (graph name: flag → env → project_id →
//!   hyper_graph; `CORTEX_DISABLE_GRAPH` ⇒ parse-only).

mod grammar;
mod models;
mod parser;
mod pipeline;
mod resolver;
mod rows;
mod runtime;

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use clap::Parser as ClapParser;
use serde_json::Value;

use cortex_analyzer_framework::cli::{parse_falkordb_uri, validate_falkordb_uri};
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore};
use cortex_falkordb::client::FalkorDbClient;

use crate::models::AnalysisResult;
use crate::pipeline::run_perl_analysis;

#[derive(Debug, Clone, ClapParser)]
#[command(
    no_binary_name = true,
    name = "analyzer-perl",
    about = "Perl 5 Tree-sitter structural analyzer"
)]
struct PerlArgs {
    /// Perl source file or project directory (Python `path` positional).
    path: Option<String>,

    #[arg(long)]
    root: Option<String>,

    #[arg(long = "project-id")]
    project_id: Option<String>,
    #[arg(long = "project_id", hide = true)]
    project_id_alt: Option<String>,

    #[arg(long = "project-name")]
    project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    project_name_alt: Option<String>,

    #[arg(long)]
    repo: Option<String>,
    #[arg(long, default_value = "perl")]
    language: String,
    #[arg(long = "build-system", default_value = "perl")]
    build_system: String,
    #[arg(long = "build_system", hide = true)]
    build_system_alt: Option<String>,

    #[arg(long, default_value = "")]
    commit_sha_before: String,
    #[arg(long, default_value = "")]
    commit_sha_after: String,

    #[arg(long)]
    incremental: bool,
    #[arg(long)]
    changed_files_manifest: Option<String>,
    #[arg(long)]
    deleted_files_manifest: Option<String>,

    #[arg(long)]
    cache_dir: Option<String>,
    #[arg(long)]
    ignore_cache: bool,
    #[arg(long = "include-docs")]
    include_docs: bool,
    #[arg(long, default_value_t = pipeline::DEFAULT_MAX_FILE_BYTES)]
    max_file_bytes: i64,
    #[arg(long, default_value_t = pipeline::DEFAULT_MAX_TOTAL_BYTES)]
    max_total_bytes: i64,
    #[arg(long, default_value_t = pipeline::DEFAULT_MAX_FILES)]
    max_files: i64,
    #[arg(long, default_value_t = 4000)]
    max_snippet_chars: i64,
    #[arg(long, default_value_t = 8000)]
    max_doc_chars: i64,
    #[arg(long = "fail-on-partial")]
    fail_on_partial: bool,
    #[arg(long = "output")]
    output: Option<String>,
    #[arg(short = 'o', hide = true)]
    output_short: Option<String>,
    #[arg(long)]
    diagnostics_output: Option<String>,
    #[arg(long)]
    pretty: bool,
    #[arg(long)]
    dry_run: bool,
    #[arg(long)]
    verbose: bool,
    #[arg(long, default_value = "INFO")]
    log_level: String,

    // Neo4j legacy flags — nhận-và-bỏ-qua (giữ contract parse_args).
    #[arg(long)]
    neo4j_uri: Option<String>,
    #[arg(long)]
    neo4j_user: Option<String>,
    #[arg(long)]
    neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    neo4j_db: Option<String>,
    #[arg(long, default_value = "1000")]
    neo4j_batch_size: i64,

    // Graph provider — contract của tools.graph.cli.add_graph_provider_args.
    #[arg(long, default_value = "falkordb")]
    graph_provider: String,
    #[arg(long)]
    falkordb_uri: Option<String>,
    #[arg(long)]
    falkordb_path: Option<String>,
    #[arg(long)]
    falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long = "falkordb-ssl")]
    falkordb_ssl: bool,
    #[arg(long)]
    falkordb_graph: Option<String>,
    #[arg(long)]
    ladybug_path: Option<String>,
    #[arg(long)]
    ladybug_graph: Option<String>,

    // Qdrant/embedding lane — nhận-và-bỏ-qua (embedding là plane Python).
    #[arg(long)]
    qdrant_url: Option<String>,
    #[arg(long, default_value = "perl_functions")]
    qdrant_collection: Option<String>,
    #[arg(long = "embed-model", default_value = "")]
    embed_model: Option<String>,
    #[arg(long, default_value = "cpu")]
    device: Option<String>,
    #[arg(long = "batch-size", default_value = "8")]
    batch_size: i64,
    #[arg(long = "max-embed-chars", default_value = "4000")]
    max_embed_chars: i64,
    #[arg(long = "qdrant-batch-size", default_value = "128")]
    qdrant_batch_size: i64,
    #[arg(long = "qdrant-timeout", default_value = "300")]
    qdrant_timeout: f64,
    #[arg(long = "qdrant-retries", default_value = "3")]
    qdrant_retries: i64,
    #[arg(long = "qdrant-retry-sleep", default_value = "2")]
    qdrant_retry_sleep: f64,

    // Python set_defaults(enable_message_scan=False).
    #[arg(long = "enable-message-scan")]
    enable_message_scan: bool,
    #[arg(long = "disable-message-scan")]
    disable_message_scan: bool,
    #[arg(long = "message-output-dir")]
    message_output_dir: Option<String>,
    #[arg(long = "message-qdrant-collection", default_value = "")]
    message_qdrant_collection: Option<String>,

    /// Harness dev.json config — Python-side convenience; accept-and-ignore.
    #[arg(long, hide = true)]
    config: Option<String>,

    /// Phase-06 embedding-input artifact path — set only by the orchestrator
    /// native embedding pass (graphless). Mirrors the framework contract.
    #[arg(long)]
    embedding_input_output: Option<String>,
}

impl PerlArgs {
    /// Trimmed, non-empty embedding-input artifact path (None ⇒ no emission).
    fn embedding_input_output(&self) -> Option<&str> {
        self.embedding_input_output
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
    }

    fn project_id_value(&self) -> Option<String> {
        self.project_id
            .clone()
            .or_else(|| self.project_id_alt.clone())
    }

    fn project_name_value(&self) -> Option<String> {
        self.project_name
            .clone()
            .or_else(|| self.project_name_alt.clone())
    }

    fn build_system_value(&self) -> String {
        if let Some(alt) = &self.build_system_alt {
            return alt.clone();
        }
        self.build_system.clone()
    }

    fn output_path(&self) -> Option<&String> {
        self.output.as_ref().or(self.output_short.as_ref())
    }

    fn env_or(flag: Option<String>, env: &str) -> Option<String> {
        flag.or_else(|| std::env::var(env).ok().filter(|value| !value.is_empty()))
    }

    /// `prepare_graph_args` + `create_graph_driver_from_args` (falkordb/ladybug).
    fn open_store(&self, project_id: &str) -> Result<Option<Box<dyn GraphStore>>, String> {
        if graph_writes_disabled() {
            return Ok(None);
        }
        match self.graph_provider.to_lowercase().as_str() {
            "ladybug" => {
                let path = Self::env_or(self.ladybug_path.clone(), "LADYBUG_PATH").ok_or(
                    "provider=ladybug cần --ladybug-path hoặc LADYBUG_PATH",
                )?;
                let graph = self
                    .ladybug_graph
                    .clone()
                    .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| project_id.to_string());
                Ok(Some(Box::new(
                    LadybugStore::open(Path::new(&path), &graph).map_err(|e| e.to_string())?,
                )))
            }
            "falkordb" | "falkor" => {
                if self
                    .falkordb_path
                    .clone()
                    .or_else(|| std::env::var("FALKORDB_PATH").ok().filter(|v| !v.is_empty()))
                    .is_some()
                {
                    // Embedded FalkorDBLite chỉ chạy phía Python — fail-closed.
                    return Err(
                        "--falkordb-path (embedded FalkorDBLite) chỉ chạy phía Python; dùng \
                         --falkordb-uri cho remote hoặc --graph-provider ladybug"
                            .to_string(),
                    );
                }
                let uri = Self::env_or(self.falkordb_uri.clone(), "FALKORDB_URI")
                    .unwrap_or_else(|| "127.0.0.1:6379".to_string());
                validate_falkordb_uri(&uri).map_err(|error| error.to_string())?;
                if self.falkordb_password.is_some() {
                    return Err(
                        "xác thực remote (falkordb_password): backend Rust chưa hỗ trợ — chạy \
                         provider này phía Python"
                            .to_string(),
                    );
                }
                let (host, port) = parse_falkordb_uri(&uri);
                let client =
                    FalkorDbClient::connect_verified(&host, port).map_err(|error| error.to_string())?;
                let graph = self
                    .falkordb_graph
                    .clone()
                    .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                    .unwrap_or_else(|| project_id.to_string());
                Ok(Some(Box::new(FalkorDbStore::new(client, graph))))
            }
            other => Err(format!("unsupported --graph-provider: {other}")),
        }
    }
}

/// `graph_writes_disabled` — CORTEX_DISABLE_GRAPH=1|true|yes|on ⇒ parse-only.
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

fn vector_configured(url: Option<&str>) -> bool {
    url.map(str::trim).map(|value| !value.is_empty()).unwrap_or(false)
}

/// `_output_path` — candidate phải nằm trong analysis root.
fn output_path(root: &Path, raw_path: &str) -> Result<PathBuf, String> {
    let root_real = std::fs::canonicalize(root).map_err(|error| error.to_string())?;
    let candidate_raw = PathBuf::from(raw_path);
    let candidate = if candidate_raw.is_absolute() {
        candidate_raw
    } else {
        root_real.join(candidate_raw)
    };
    let parent = candidate.parent().unwrap_or(&root_real).to_path_buf();
    let parent_real = std::fs::canonicalize(&parent).unwrap_or(parent);
    if !parent_real.starts_with(&root_real) {
        return Err("output path must remain inside the analysis root".to_string());
    }
    std::fs::create_dir_all(&parent_real).map_err(|error| error.to_string())?;
    Ok(parent_real.join(candidate.file_name().unwrap_or_default()))
}

/// `_write_text_atomic` — tmp + fsync + replace.
fn write_text_atomic(path: &Path, text: &str) -> Result<(), String> {
    use std::io::Write;
    let temp_path = path.with_extension(format!(
        "{}.{}.tmp",
        path.extension()
            .map(|extension| extension.to_string_lossy().to_string())
            .unwrap_or_default(),
        std::process::id()
    ));
    let mut handle = std::fs::File::create(&temp_path).map_err(|error| error.to_string())?;
    handle.write_all(text.as_bytes()).map_err(|error| error.to_string())?;
    handle.flush().map_err(|error| error.to_string())?;
    handle
        .sync_all()
        .map_err(|error| error.to_string())?;
    std::fs::rename(&temp_path, path).map_err(|error| error.to_string())
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = PerlArgs::parse_from(&argv);
    std::process::exit(run(&args));
}

fn run(args: &PerlArgs) -> i32 {
    let raw_root = args
        .root
        .clone()
        .or_else(|| args.path.clone())
        .unwrap_or_default();
    if raw_root.is_empty() {
        eprintln!("Either path or --root is required.");
        return 2;
    }
    let raw_path = PathBuf::from(&raw_root);
    let path = match raw_path.canonicalize() {
        Ok(canonical) => canonical,
        Err(_) => {
            eprintln!("Analysis path does not exist: {raw_root}");
            return 2;
        }
    };
    let scanned_directory = path.is_dir();
    let root: PathBuf = if path.is_file() {
        path.parent().unwrap_or(Path::new(".")).to_path_buf()
    } else if scanned_directory {
        path.clone()
    } else {
        eprintln!("Analysis path does not exist: {raw_root}");
        return 2;
    };
    let project_id = args
        .project_id_value()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            root.file_name()
                .map(|name| name.to_string_lossy().to_string())
                .filter(|name| !name.is_empty())
                .unwrap_or_else(|| "perl-project".to_string())
        });

    let changed: Option<Vec<String>> = if args.incremental {
        Some(
            args.changed_files_manifest
                .as_deref()
                .map(|manifest| {
                    cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                        .into_iter()
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default(),
        )
    } else {
        None
    };
    let deleted: Option<Vec<String>> = if args.incremental {
        Some(
            args.deleted_files_manifest
                .as_deref()
                .map(|manifest| {
                    cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                        .into_iter()
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default(),
        )
    } else {
        None
    };

    let result: AnalysisResult = match run_perl_analysis(
        &root,
        &project_id,
        changed.as_deref(),
        deleted.as_deref(),
        args.cache_dir.as_deref(),
        args.ignore_cache,
        args.include_docs,
        args.max_file_bytes,
        args.max_total_bytes,
        args.max_files,
        args.max_snippet_chars,
        args.max_doc_chars,
    ) {
        Ok(result) => result,
        Err(error) => {
            eprintln!("Perl analysis failed: {error}");
            return 3;
        }
    };

    let payload = result.to_json();
    if let Some(output) = args.output_path() {
        match output_path(&root, output)
            .and_then(|target| write_text_atomic(&target, &format!("{payload}\n")))
        {
            Ok(()) => {}
            Err(error) => {
                eprintln!("Unable to write analyzer output: {error}");
                return 2;
            }
        }
    }
    if let Some(diagnostics_output) = &args.diagnostics_output {
        let diagnostic_payload = {
            let items: Vec<Value> = result
                .diagnostics
                .iter()
                .map(|diag| serde_json::to_value(diag).unwrap_or(Value::Null))
                .collect();
            serde_json::to_string_pretty(&items).unwrap_or_else(|_| "[]".to_string())
        };
        match output_path(&root, diagnostics_output)
            .and_then(|target| write_text_atomic(&target, &format!("{diagnostic_payload}\n")))
        {
            Ok(()) => {}
            Err(error) => {
                eprintln!("Unable to write analyzer output: {error}");
                return 2;
            }
        }
    }

    if args.fail_on_partial && result.coverage == "partial" {
        println!("{payload}");
        return 3;
    }
    let mut counts: BTreeSet<String> = BTreeSet::new();
    let mut vector_count = 0usize;
    if !args.dry_run {
        // ── Phase-06 embedding-input artifact (only when orchestrator asked) ──
        if let Some(output) = args.embedding_input_output() {
            let (project_name, repo, build_system) = graph_scope(args, &root, &result);
            if let Err(error) =
                cortex_analyzer_framework::embedding_artifact::maybe_emit_embedding_artifact(
                    Some(output),
                    cortex_analyzer_framework::embedding_artifact::EmbeddingEmission {
                        parser: "perl",
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
                            &build_system,
                        ),
                    },
                )
            {
                eprintln!("Perl embedding-input artifact failed: {error}");
                return 4;
            }
        }
        match write_graph(args, &root, &result) {
            Ok(write_counts) => {
                counts = write_counts.keys().cloned().collect();
            }
            Err(error) => {
                eprintln!("Perl graph persistence failed: {error}");
                return 4;
            }
        }
        // `_sync_vectors` là plane Python — Rust không embed ⇒ luôn 0.
        vector_count = 0;
    }
    if args.dry_run || args.pretty || counts.is_empty() {
        println!("{payload}");
    }
    let function_count = result
        .symbols
        .iter()
        .filter(|symbol| symbol.kind == "subroutine")
        .count();
    let class_count = result
        .symbols
        .iter()
        .filter(|symbol| symbol.kind == "package")
        .count();
    let vector_status = if vector_configured(args.qdrant_url.as_deref()) && !args.dry_run {
        "success"
    } else {
        "disabled"
    };
    println!(
        "[SCAN_RESULT] parser=perl files={} functions={function_count} classes={class_count} \
         vectors={vector_count} vector_status={vector_status}",
        result.files.len()
    );
    0
}

/// Graph-scope strings shared by the graph write and the phase-06 embedding
/// artifact (project_name / repo / build_system must be byte-identical across
/// the two planes or point ids drift). Mirrors Python `_write_graph` defaults.
fn graph_scope(args: &PerlArgs, root: &Path, result: &AnalysisResult) -> (String, String, String) {
    // Python: project_name = args.project_name or result.project_id;
    //         repo = args.repo or f"{project_name}/{os.path.basename(args.root)}"
    let project_name = args
        .project_name_value()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| result.project_id.clone());
    let root_basename = root
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default();
    let repo = args
        .repo
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| format!("{project_name}/{root_basename}"));
    let build_system = {
        let value = args.build_system_value();
        if value.is_empty() {
            "perl".to_string()
        } else {
            value
        }
    };
    (project_name, repo, build_system)
}

/// `_write_graph` — mở store, rồi rows → cleanup → write_all.
fn write_graph(
    args: &PerlArgs,
    root: &Path,
    result: &AnalysisResult,
) -> Result<std::collections::BTreeMap<String, i64>, String> {
    let Some(store) = args.open_store(&result.project_id)? else {
        // prepare_graph_args trả False (graph writes disabled).
        return Ok(std::collections::BTreeMap::new());
    };
    let (project_name, repo, build_system) = graph_scope(args, root, result);
    let mut writer = LanguageCodeWriter::new(store, None, args.neo4j_batch_size.max(1) as usize, args.verbose);
    let counts = rows::write_graph(
        &mut writer,
        result,
        &project_name,
        &repo,
        &build_system,
        args.incremental,
    )?;
    Ok(counts)
}

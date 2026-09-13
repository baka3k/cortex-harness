//! analyzer-rust — Rust port của `tools/rust/rust_analyzer.py` (phase 07).
//!
//! Pipeline: scan (`_scan_rust_files` với skip-list riêng) → manifest
//! selection (không import-impact expansion — Python gốc không có) → parse
//! (tree-sitter rust) → call resolution per-file (`_resolve_calls`) →
//! `LanguageCodeWriter::write_all(use_full_writers=True)` → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (key decision #3):
//! * Qdrant/embedding KHÔNG port — flags nhận và bỏ qua (vector plane
//!   Python; `[SCAN_RESULT]` luôn in `vectors=0 vector_status=disabled`).
//! * Message scan là plane Python-side; flag nhận, skip có kiểm soát
//!   (Python gốc `set_defaults(enable_message_scan=False)`).
//! * Parse cache / neo4j resume state — nhận và bỏ qua (Rust luôn parse đủ).
//! * `--config` nhận và bỏ qua (orchestrator Rust truyền explicit args).

mod pipeline;
mod rustparse;

use std::collections::BTreeSet;

use clap::Parser as ClapParser;

use cortex_analyzer_framework::cli::{abs_root, AnalyzerArgs};
use cortex_analyzer_framework::scan::rel_posix;
use rustparse::RUST_SOURCE_EXTENSIONS;

/// Cờ rust-analyzer-specific ngoài contract chung — mirror `parse_args` của
/// rust_analyzer.py; phần không áp dụng cho backend Rust nhận và bỏ qua.
#[derive(Debug, Default, clap::Args)]
pub struct RustExtraArgs {
    /// Rust source file hoặc directory (positional, mặc định = --root).
    pub path: Option<String>,

    /// Write JSON payload to this file — nhận và bỏ qua (plane Python).
    #[arg(short = 'o', long, hide = true)]
    pub output: Option<String>,
    /// Pretty-print JSON to stdout — nhận và bỏ qua.
    #[arg(long, hide = true)]
    pub pretty: bool,

    // Neo4j legacy — nhận-và-bỏ-qua (graph provider bên dưới).
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, default_value = "1000")]
    pub neo4j_batch_size: i64,

    // Qdrant tuning — nhận-và-bỏ-qua.
    #[arg(long, hide = true)]
    pub qdrant_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_timeout: Option<f64>,
    #[arg(long, hide = true)]
    pub qdrant_retries: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_retry_sleep: Option<f64>,
}

#[derive(Debug, ClapParser)]
#[command(no_binary_name = true)]
struct RustAnalyzerArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: RustExtraArgs,
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    // rust_analyzer.py: `set_defaults(enable_message_scan=False)` — KHÔNG
    // normalize default-on như java/js; cả 2 cờ chỉ nhận-và-bỏ-qua.
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = RustAnalyzerArgs::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs, extra: &RustExtraArgs) -> i32 {
    // Python main: `if not args.path and not args.root` — root là required
    // flag phía Rust (orchestrator luôn truyền) nên nhánh này giữ cho đủ.
    if extra.path.is_none() && args.root.trim().is_empty() {
        eprintln!("Either path or --root is required");
        return 2;
    }

    let root = abs_root(&args.root);
    let target = abs_root(extra.path.as_deref().unwrap_or(&args.root));

    // `args._scanned_directory = os.path.isdir(path)`.
    if target.is_dir() {
        // Directory mode — root = abspath(root) (flag required ⇒ luôn có).
        let mut selected_rel_paths: Option<BTreeSet<String>> = None;
        let mut deleted_rel_paths: BTreeSet<String> = BTreeSet::new();
        if args.incremental {
            if let Some(manifest) = &args.changed_files_manifest {
                selected_rel_paths = Some(pipeline::manifest_rs_paths(
                    manifest,
                    &root,
                    &RUST_SOURCE_EXTENSIONS,
                ));
            }
            if let Some(manifest) = &args.deleted_files_manifest {
                deleted_rel_paths = pipeline::manifest_rs_paths(
                    manifest,
                    &root,
                    &RUST_SOURCE_EXTENSIONS,
                );
            }
        }

        if args.dry_run {
            let files = pipeline::scan_rust_files(
                &root,
                selected_rel_paths.as_ref().unwrap_or(&BTreeSet::new()),
            );
            println!("Dry run: {} Rust files found", files.len());
            return 0;
        }

        let payloads = match pipeline::build_payloads(
            &root,
            selected_rel_paths.as_ref().unwrap_or(&BTreeSet::new()),
        ) {
            Ok(payloads) => payloads,
            Err(error) => {
                eprintln!("{error}");
                return 1;
            }
        };
        finish(args, extra, &root, &payloads, selected_rel_paths, deleted_rel_paths)
    } else {
        // Single-file mode: root = abspath(--root) (Python: root or dirname).
        let file_root = if args.root.trim().is_empty() {
            target
                .parent()
                .map(|parent| parent.to_path_buf())
                .unwrap_or_else(|| root.clone())
        } else {
            root.clone()
        };
        if args.dry_run {
            println!("Dry run: 1 Rust file: {}", rel_posix(&file_root, &target));
            return 0;
        }
        let payload = match rustparse::parse_rust_file(&target, &file_root) {
            Ok(payload) => payload,
            Err(error) => {
                eprintln!("{error}");
                return 1;
            }
        };
        finish(args, extra, &file_root, &[payload], None, BTreeSet::new())
    }
}

/// Phần còn lại của Python `main`: `_write_graph` (trừ env disabled), rồi
/// `[SCAN_RESULT]` (luôn in; vector lane không port ⇒ disabled).
fn finish(
    args: &AnalyzerArgs,
    extra: &RustExtraArgs,
    root: &std::path::Path,
    payloads: &[rustparse::FilePayload],
    selected_rel_paths: Option<BTreeSet<String>>,
    deleted_rel_paths: BTreeSet<String>,
) -> i32 {
    let verbose = args.verbose;
    let mut exit_code = 0;

    if !args.graph_writes_disabled() {
        let store = match args.open_store() {
            Ok(store) => Some(store),
            Err(error) => {
                eprintln!("Rust graph persistence failed: {error}");
                return 3;
            }
        };
        if let Some(store) = store {
            let project_id = non_empty(args.project_id.clone())
                .or_else(|| basename_of(root))
                .unwrap_or_else(|| "rust-project".to_string());
            let project_name =
                non_empty(args.project_name.clone()).unwrap_or_else(|| project_id.clone());
            let language = non_empty(args.language.clone()).unwrap_or_else(|| "rust".to_string());
            let repo = non_empty(args.repo.clone())
                .unwrap_or_else(|| pipeline::repo_name(&project_name, root));
            // Python parse_args: --build-system default = env
            // PROJECT_BUILD_SYSTEM hoặc "cargo"; `args.build_system or ""`.
            let build_system =
                non_empty(args.build_system.clone()).unwrap_or_else(|| "cargo".to_string());
            let config = pipeline::PipelineConfig {
                project_id,
                project_name,
                language,
                repo,
                build_system,
                incremental: args.incremental,
                cleanup_selected: selected_rel_paths.unwrap_or_default(),
                cleanup_deleted: deleted_rel_paths,
                neo4j_db: args.neo4j_db.clone(),
                neo4j_batch_size: extra.neo4j_batch_size,
                verbose,
            };
            if let Err(error) = pipeline::write_graph(&config, payloads, store) {
                eprintln!("Rust graph persistence failed: {error}");
                exit_code = 3;
            }
        }
    } else if verbose {
        println!("[graph] disabled; missing graph connection settings");
    }

    // Vector lane — không port (key decision #3): luôn vectors=0/disabled.
    let sr_fn: usize = payloads.iter().map(|payload| payload.functions.len()).sum();
    let sr_cls: usize = payloads.iter().map(|payload| payload.types.len()).sum();
    let sr_files = payloads.len();
    println!(
        "[SCAN_RESULT] parser=rust files={sr_files} functions={sr_fn} classes={sr_cls} \
         vectors=0 vector_status=disabled"
    );
    exit_code
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.filter(|value| !value.is_empty())
}

fn basename_of(root: &std::path::Path) -> Option<String> {
    root.file_name()
        .map(|name| name.to_string_lossy().to_string())
}

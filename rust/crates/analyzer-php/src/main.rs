//! analyzer-php — Rust port của `code-tiny/tools/php/php_analyzer.py`
//! (phase 05). Pipeline: scan → parse → call resolution →
//! `LanguageCodeWriter::write_all` → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (theo plan):
//! * Qdrant/embedding KHÔNG port — embeddings tách khỏi analyzer, orchestrator
//!   điều phối (key decision #3). Flags liên quan nhận và bỏ qua.
//! * Message scan là plane Python-side; flag nhận, skip có kiểm soát.
//! * Parse cache / neo4j resume state là plane Python (`analyzer_cache.py`);
//!   flags nhận và bỏ qua.
//! * `--config` nhận và bỏ qua (Python pre-parse `load_harness_config`).

mod php_analyzer;
mod phpparse;
mod resolve;

use clap::Parser;

use cortex_analyzer_framework::cli::{abs_root, AnalyzerArgs};
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::rel_posix;
use php_analyzer::PhpAnalyzer;

/// Superset CLI của PHP analyzer: `AnalyzerArgs` (khung dùng chung) + các cờ
/// riêng của `php_analyzer.py::parse_args` mà khung chưa có — tất cả accept
/// theo contract, phần không tác động graph-plane chỉ giữ để orchestrator gọi
/// được 2 backend thay thế cho nhau.
#[derive(Debug, Clone, Parser)]
#[command(no_binary_name = true)]
struct PhpArgs {
    #[command(flatten)]
    base: AnalyzerArgs,

    #[arg(long, hide = true)]
    neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    neo4j_password: Option<String>, // sensitive-guard:allow
    /// Writer batch size (`--neo4j-batch-size`, default 1000 như Python).
    #[arg(long, default_value_t = 1000, hide = true)]
    neo4j_batch_size: i64,
    #[arg(long, hide = true)]
    neo4j_state: Option<String>,
    #[arg(long, hide = true)]
    disable_neo4j_resume: bool,
    #[arg(long, default_value_t = 128, hide = true)]
    qdrant_batch_size: i64,
    #[arg(long, default_value_t = 300.0, hide = true)]
    qdrant_timeout: f64,
    #[arg(long, default_value_t = 3, hide = true)]
    qdrant_retries: i64,
    #[arg(long, default_value_t = 2.0, hide = true)]
    qdrant_retry_sleep: f64,
}

impl PhpArgs {
    fn parse_from(env: &[String]) -> Self {
        // Message-scan flags: normalize — dedupe cờ bật, giữ cờ tắt (Python
        // `set_defaults(enable_message_scan=True)`; `--disable` thắng).
        let mut argv: Vec<String> = env.to_vec();
        argv.retain(|a| a != "--enable-message-scan");
        if !argv.iter().any(|a| a == "--disable-message-scan") {
            argv.push("--enable-message-scan".to_string());
        }
        let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
        let mut args = <Self as clap::Parser>::parse_from(strs);
        if args.base.project_id.is_none() {
            args.base.project_id = args.base.project_id_alt.take();
        }
        if args.base.project_name.is_none() {
            args.base.project_name = args.base.project_name_alt.take();
        }
        if args.base.build_system.is_none() {
            args.base.build_system = args.base.build_system_alt.take();
        }
        args
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = PhpArgs::parse_from(&argv);
    let code = run(&args);
    std::process::exit(code);
}

fn run(args: &PhpArgs) -> i32 {
    let base = &args.base;
    let root = abs_root(&base.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", base.root);
        return 2;
    }

    if base.dry_run {
        let mut files = phpparse::scan_php_files(&root);
        // Khớp Python: chỉ lọc theo manifest KHI --incremental VÀ có manifest
        // khác rỗng; ngược lại in plain message.
        let manifest_set = if base.incremental {
            base.changed_files_manifest
                .as_deref()
                .map(|manifest| load_manifest_paths(manifest, &root))
                .unwrap_or_default()
        } else {
            Default::default()
        };
        if base.incremental && !manifest_set.is_empty() {
            files.retain(|path| manifest_set.contains(&rel_posix(&root, path)));
            println!(
                "Dry run (incremental): {} PHP files selected (manifest={})",
                files.len(),
                manifest_set.len()
            );
        } else {
            println!("Dry run: {} PHP files found", files.len());
        }
        return 0;
    }

    let mut analyzer = PhpAnalyzer::new();
    match analyzer.execute(&root, base) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

//! analyzer-python — Rust port của `tools/python/python_analyzer.py`
//! (phase 04 template). Pipeline: scan → parse → semantic enrich → call
//! resolution → `LanguageCodeWriter::write_all` → `[SCAN_RESULT]` + summary.
//!
//! Đây là exemplar consumer của khung `cortex-analyzer-framework` — analyzer
//! Wave D khác (phase 05–08) cài `Analyzer` trên `PythonAnalyzer::run` như
//! file này.
//!
//! Khác biệt scope có chủ đích (theo plan):
//! * Qdrant/embedding KHÔNG port — embeddings tách khỏi analyzer, orchestrator
//!   điều phối (key decision #3). Flags liên quan nhận và bỏ qua.
//! * Message scan là plane Python-side; flag nhận, skip có kiểm soát.
//! * `--enable-flows` / `--enable-llm-summary` chưa port (default off).

mod pyparse;
mod python_analyzer;
mod resolve;

use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::{rel_posix, scan_files};
use python_analyzer::PythonAnalyzer;

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if cortex_analyzer_framework::print_version_probe("analyzer-python", &argv) {
        return;
    }
    let args = AnalyzerArgs::parse_from(&argv);
    let code = run(&args);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs) -> i32 {
    let root = cortex_analyzer_framework::cli::abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return 2;
    }

    if args.dry_run {
        let mut files = scan_files(&root, &[".py", ".pyi"]);
        // Khớp Python: chỉ lọc theo manifest KHI --incremental VÀ có manifest
        // khác rỗng; ngược lại in plain message.
        let manifest_set = if args.incremental {
            args.changed_files_manifest
                .as_deref()
                .map(|manifest| load_manifest_paths(manifest, &root))
                .unwrap_or_default()
        } else {
            Default::default()
        };
        if args.incremental && !manifest_set.is_empty() {
            files.retain(|path| manifest_set.contains(&rel_posix(&root, path)));
            println!(
                "Dry run (incremental): {} Python files selected (manifest={})",
                files.len(),
                manifest_set.len()
            );
        } else {
            println!("Dry run: {} Python files found", files.len());
        }
        return 0;
    }

    let mut analyzer = PythonAnalyzer::new();
    match analyzer.execute(&root, args) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

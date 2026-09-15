//! analyzer-java — Rust port của `tools/java/java_analyzer.py` (phase 06,
//! JVM batch). Pipeline: scan (skip-list java riêng) → import-impact
//! selection → cleanup → parse (tree-sitter-java) → call resolution →
//! `LanguageCodeWriter::write_all` → `[SCAN_RESULT]`.
//!
//! Scope khác biệt có chủ đích (như phase 04):
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, ... nhận và bỏ qua.
//! * Message scan là plane Python-side; `--enable/--disable-message-scan`
//!   nhận, skip có kiểm soát.
//! * Parse cache / neo4j resume state không áp dụng cho backend này — nhận
//!   `--disable-parse-cache`, `--ignore-cache`, `--neo4j-state`,
//!   `--disable-neo4j-resume`, `--keep-cache`, `--cache-dir` và bỏ qua.
//! * `--config` nhận và bỏ qua (orchestrator Rust truyền explicit args).

mod java_analyzer;
mod javaparse;
mod javascan;
mod resolve;

use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;
use java_analyzer::JavaExtraArgs;

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ java-specific.
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct JavaArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: JavaExtraArgs,
}

fn main() {
    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    // Message-scan normalize — khớp `set_defaults(enable_message_scan=True)`:
    // dedupe cờ bật, `--disable-message-scan` thắng.
    argv.retain(|a| a != "--enable-message-scan");
    if !argv.iter().any(|a| a == "--disable-message-scan") {
        argv.push("--enable-message-scan".to_string());
    }
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = JavaArgs::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs, extra: &JavaExtraArgs) -> i32 {
    match java_analyzer::execute(args, extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

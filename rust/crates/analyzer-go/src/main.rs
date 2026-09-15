//! analyzer-go — Rust port của `code-tiny/tools/go/go_analyzer.py` (phase 07,
//! systems & legacy batch). Pipeline: scan (`_scan_go_files`) → manifest
//! selection → cleanup → parse (tree-sitter-go) → per-file call resolution →
//! `LanguageCodeWriter::write_all` → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (như phase 05/06):
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, ... nhận và bỏ qua;
//!   `[SCAN_RESULT]` luôn `vectors=0 vector_status=disabled`.
//! * Message scan là plane Python-side (go_analyzer.py default TẮT qua
//!   `set_defaults(enable_message_scan=False)` — flags nhận, no-op).
//! * Parse cache (`--cache-dir`, `--ignore-cache`) là plane Python — nhận và
//!   bỏ qua.
//! * `--config` nhận và bỏ qua (Python pre-parse harness config).
//! * `--output/-o/--pretty` nhận và bỏ qua (Rust không dump payload JSON).

mod goanalyzer;
mod goparse;
mod resolve;

use clap::Parser;
use goanalyzer::GoExtraArgs;

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ go-specific.
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct GoArgs {
    #[command(flatten)]
    base: cortex_analyzer_framework::cli::AnalyzerArgs,
    #[command(flatten)]
    extra: GoExtraArgs,
}

impl GoArgs {
    /// Parse thẳng argv — go_analyzer.py KHÔNG normalize message-scan flags
    /// (default tắt), nên bỏ qua bước auto-enable của framework.
    fn parse_from(env: &[String]) -> Self {
        let strs: Vec<&str> = env.iter().map(String::as_str).collect();
        <Self as clap::Parser>::parse_from(strs)
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = GoArgs::parse_from(&argv);
    let code = run(&args.base, &args.extra);
    std::process::exit(code);
}

fn run(args: &cortex_analyzer_framework::cli::AnalyzerArgs, extra: &GoExtraArgs) -> i32 {
    match goanalyzer::execute(args, extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

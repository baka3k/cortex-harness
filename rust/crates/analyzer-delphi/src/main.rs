//! analyzer-delphi — Rust port của `code-tiny/tools/delphi/delphi_analyzer.py`
//! (phase 07, systems & legacy batch). Pipeline: scan (`_scan_delphi_files`) →
//! manifest selection (changed ∪ uses-impact BFS) → cleanup → parse (regex
//! extraction + tree-sitter-pascal cho parse_meta) → `LanguageCodeWriter::
//! write_all` + `write_calls_with_site` → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (như phase 05/06):
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, ... nhận và bỏ qua.
//! * Message scan là plane Python-side (default BẬT như Python) — flags nhận,
//!   no-op.
//! * Parse cache (`--cache-dir`, `--ignore-cache`, `--keep-cache`,
//!   `--disable-parse-cache`) là plane Python — nhận và bỏ qua.
//! * `--config` nhận và bỏ qua (Python pre-parse harness config).

mod danalyzer;
mod dparse;
mod resolve;
#[cfg(test)]
mod tests;

use clap::Parser;
use danalyzer::DelphiExtraArgs;

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ delphi-specific.
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct DelphiArgs {
    #[command(flatten)]
    base: cortex_analyzer_framework::cli::AnalyzerArgs,
    #[command(flatten)]
    extra: DelphiExtraArgs,
}

impl DelphiArgs {
    /// Parse thẳng argv — delphi_analyzer.py SET default enable_message_scan
    /// = True (giống normalize của framework).
    fn parse_from(env: &[String]) -> Self {
        let strs: Vec<&str> = env.iter().map(String::as_str).collect();
        <Self as clap::Parser>::parse_from(strs)
    }
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = DelphiArgs::parse_from(&argv);
    let code = run(&args.base, &args.extra);
    std::process::exit(code);
}

fn run(args: &cortex_analyzer_framework::cli::AnalyzerArgs, extra: &DelphiExtraArgs) -> i32 {
    match danalyzer::execute(args, extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

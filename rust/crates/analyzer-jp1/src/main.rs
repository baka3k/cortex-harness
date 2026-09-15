//! analyzer-jp1 — Rust port của `code-tiny/tools/jp1/jp1_analyzer.py`
//! (phase 07, systems & legacy batch). Pipeline: scan `.txt` JP1 jobnet
//! exports (sniff) → line-based parse (unit/property/ar=) → graph write
//! (Jp1Unit MERGE + typed relations + incremental cleanup) → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (như analyzer-go/analyzer-cobol):
//! * Qdrant/embedding KHÔNG port (plane Python) — cờ `--qdrant-*`, `--embed-*`,
//!   `--device`, `--batch-size`, ... nhận và bỏ qua; `[SCAN_RESULT]` luôn
//!   `vectors=0 vector_status=disabled`.
//! * Message scan flags nhận và bỏ qua (pipeline jp1 không dùng).
//! * `--output/-o`, `--pretty`, `--dry-run` payload JSON, `--cache-dir`,
//!   `--ignore-cache` nhận và bỏ qua (debug/parse-cache plane Python).
//! * `--config /dev/null` nhận và bỏ qua (harness contract).

use analyzer_jp1::analyzer;
use clap::Parser;

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ jp1-specific.
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct Jp1Args {
    #[command(flatten)]
    base: cortex_analyzer_framework::cli::AnalyzerArgs,
    #[command(flatten)]
    extra: analyzer::Jp1ExtraArgs,
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = <Jp1Args as clap::Parser>::parse_from(strs);
    let code = match analyzer::execute(&args.base, &args.extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };
    std::process::exit(code);
}

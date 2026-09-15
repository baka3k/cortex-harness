//! analyzer-cplus — Rust port của `tools/cplus/cplus_analyzer.py` (phase 07,
//! systems & legacy batch). Pipeline: scan (skip-list cplus riêng) →
//! compile-db index (fallback heuristics khi thiếu compile_commands.json) →
//! incremental include-impact selection → cleanup → parse (tree-sitter-c /
//! tree-sitter-cpp, kèm header alternate-grammar retry) → payload validation
//! 2 pass → call resolution → `LanguageCodeWriter` streaming planes →
//! `[SCAN_RESULT]`.
//!
//! Scope khác biệt có chủ đích (key decision #8 — clang plane giữ Python):
//! * clang semantic-evidence plane (clang_worker.py/libclang, parse_recovery,
//!   semantic_worker/semantic_context) là Python subprocess — Rust nhận
//!   `--parse-quality repair` và bỏ qua phần recovery (parity harness chạy
//!   policy `report`/`off` ở fallback mode; `--disable-compile-db-bootstrap`
//!   nhận và in cùng thông báo).
//! * Qdrant/embedding (torch/transformers) KHÔNG port — cờ `--qdrant-*`,
//!   `--embed-*`, `--device`, `--batch-size`, ... nhận và bỏ qua.
//! * Message scan là plane Python-side; `--enable/--disable-message-scan`
//!   nhận, skip có kiểm soát.
//! * Parse cache / resume là Python-only — `--disable-parse-cache`,
//!   `--ignore-cache`, `--neo4j-state`, `--disable-neo4j-resume`,
//!   `--keep-cache`, `--cache-dir`, `--parse-run-id` nhận và bỏ qua.
//! * Pro*C (.pc/.pcc) plane (proc_analyzer/proc_source_map masking + SQL
//!   nodes) chưa port — .pc/.pcc vẫn được scan và parse bằng grammar C ở
//!   tầng structure nhưng không sinh proc_nodes (xem phase07 report).
//! * .rc/.rc2 Windows resource plane (rc_parser.py) PORT đầy đủ.

use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;
use analyzer_cplus::{analyzer, CplusExtraArgs};

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ cplus-specific.
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct CplusCli {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: CplusExtraArgs,
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
    let args = CplusCli::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs, extra: &CplusExtraArgs) -> i32 {
    match analyzer::execute(args, extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

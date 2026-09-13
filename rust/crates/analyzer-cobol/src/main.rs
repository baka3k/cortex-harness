//! analyzer-cobol — Rust port của `code-tiny/tools/cobol/cobol_analyzer.py`
//! (phase 07, systems & legacy batch). Pipeline: preflight (dlopen native
//! COBOL grammar) → scan (line-based, fixed/free format, copybooks) → resolve
//! → semantic facts → write graph (files + FileMetadata + label batches +
//! typed relations) → incremental cleanup → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích:
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, ... nhận và bỏ qua;
//!   `[SCAN_RESULT]` luôn `vectors=0`.
//! * Message scan: cobol_analyzer.py có cờ nhưng không dùng trong pipeline —
//!   nhận và bỏ qua.
//! * Grammar: dlopen CÙNG native library `code-tiny/tools/cobol/lib/*.so`
//!   (mà Python ctypes load) ⇒ error/missing node sets byte-identical.
//!   Python có fallback `tree_sitter_language_pack` — Rust không có plane đó,
//!   fail với COBOL_RUNTIME_UNAVAILABLE khi thiếu library.


use analyzer_cobol::analyzer;
use clap::Parser;

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ cobol-specific.
#[derive(Debug, Parser)]
#[command(no_binary_name = true)]
struct CobolArgs {
    #[command(flatten)]
    base: cortex_analyzer_framework::cli::AnalyzerArgs,
    #[command(flatten)]
    extra: analyzer::CobolExtraArgs,
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = <CobolArgs as clap::Parser>::parse_from(strs);
    let code = match analyzer::execute(&args.base, &args.extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };
    std::process::exit(code);
}

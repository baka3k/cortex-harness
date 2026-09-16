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

#[cfg(test)]
mod tests;

use analyzer_delphi::{DelphiArgs, run};

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = DelphiArgs::parse_from(&argv);
    let code = run(&args.base, &args.extra);
    std::process::exit(code);
}

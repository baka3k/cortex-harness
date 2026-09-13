//! analyzer-vb — Rust port của `code-tiny/tools/vb/` (phase 07, systems &
//! legacy batch). Một crate, bốn binary (`analyzer-vbnet`, `analyzer-vb6`,
//! `analyzer-vba`, `analyzer-vbscript`) chia sẻ:
//!
//! * `pycompat`  — helpers semantic Python (`str.find`, `splitlines`,
//!   `splitext`, `json.dumps(ensure_ascii=True)`, decode utf-8 ignore).
//! * `model`     — dataclass payload (FunctionDef/.../FileDef + CallEdge).
//! * `parse`     — `vb_common.parse_vb_file`: parser LINE/REGEX thuần (không
//!   tree-sitter cho graph rows; tree-sitter Python chỉ feed `has_error` vào
//!   `parse_meta` vốn không bao giờ vào graph).
//! * `classifier`— `vb_path_classifier.VBPathClassifier` (routing theo ext +
//!   heuristic nội dung vb6/vba + ancestor project files).
//! * `scan`      — `_scan_vb_files` (skip-list riêng của vb_analyzer_base).
//! * `roslyn`    — `vb_roslyn_adapter` subprocess port (dotnet worker, key
//!   decision #8: plane C# Roslyn giữ nguyên là process ngoài).
//! * `pipeline`  — `vb_analyzer_base.main` + `build_call_graph`.
//!
//! Khác biệt scope có chủ đích (như phase 05/06):
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, ... nhận và bỏ qua.
//! * Message scan là plane Python-side; default BẬT như Python
//!   (`set_defaults(enable_message_scan=True)`) — flag nhận, skip có kiểm soát.
//! * Parse cache (`--cache-dir`, `--disable-parse-cache`, `--ignore-cache`)
//!   không áp dụng cho backend Rust — nhận và bỏ qua.
//! * `--config` nhận và bỏ qua (Python pre-parse harness config ở `__main__`).

pub mod classifier;
pub mod cli;
pub mod model;
pub mod parse;
pub mod pipeline;
pub mod pycompat;
pub mod roslyn;
pub mod scan;

/// Dialect hợp lệ (mirror `sorted(_PARSER_FACTORY.keys())`).
pub const DIALECTS: [&str; 4] = ["vb6", "vba", "vbnet", "vbscript"];

/// Điểm vào chung cho 4 binary — mirror entry script Python: inject
/// `--dialect <mặc định của binary>` khi argv chưa có, rồi chạy pipeline.
pub fn run_binary(default_dialect: &'static str) -> i32 {
    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    // Python entry script: `if "--dialect" not in args: args = ["--dialect", X, *args]`
    // (prepend — occurrence trên CLI, nếu có, thắng).
    if !argv.iter().any(|a| a == "--dialect" || a.starts_with("--dialect=")) {
        argv.insert(0, default_dialect.to_string());
        argv.insert(0, "--dialect".to_string());
    }
    // Message-scan normalize — khớp `set_defaults(enable_message_scan=True)`:
    // dedupe cờ bật, `--disable-message-scan` thắng.
    argv.retain(|a| a != "--enable-message-scan");
    if !argv.iter().any(|a| a == "--disable-message-scan") {
        argv.push("--enable-message-scan".to_string());
    }
    let parsed = match cli::VbArgs::parse_argv(&argv) {
        Ok(parsed) => parsed,
        Err(code) => return code,
    };
    match pipeline::execute(&parsed) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

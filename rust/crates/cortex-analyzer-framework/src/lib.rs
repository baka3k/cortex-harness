//! Analyzer framework dùng chung cho mọi language analyzer Rust (phase 04).
//!
//! Khối dựng khung của Wave D: mọi analyzer (python, js, java, cplus, ...)
//! điền nội dung vào khung này:
//! * `cli` — CLI contract giữ nguyên từng chữ với bản Python (orchestrator
//!   `incremental_sync.py` gọi được cả 2 backend bằng đúng lệnh).
//! * `scan` — file walker với ignore list port từ `tools/common/scan_ignore.py`
//!   + `_should_ignore_directory`.
//! * `manifest` — port `tools/common/git_diff.py::load_manifest_paths`.
//! * `semantic` — port `semantic_inference.py` + `call_graph_builder.py` +
//!   `confidence_scorer.py` (source của note/summary "Performs X operation
//!   (takes N parameters)" — byte-identical vì được embed sau này).
//! * `ts` — tree-sitter helpers (decode ignore, node text, cursor walk).
//! * `traits` — `Analyzer` trait + `AnalyzerContext`.
//! * `summary` — structured JSON summary cho orchestrator.

pub mod cli;
pub mod manifest;
pub mod scan;
pub mod semantic;
pub mod summary;
pub mod traits;
pub mod ts;

pub use cli::AnalyzerArgs;
pub use traits::{Analyzer, AnalyzerContext, AnalyzerResult};

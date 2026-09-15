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

pub mod cleanup;
pub mod cli;
pub mod embedding_artifact;
pub mod manifest;
pub mod scan;
pub mod semantic;
pub mod summary;
pub mod traits;
pub mod ts;

/// Git short SHA baked at build time (`build.rs`); every analyzer binary
/// prints it on `--version` and the `cortex-sync` orchestrator refuses to
/// spawn a child whose stamp differs from its own (phase-08 build-commit
/// handshake, red-team F5 stale binary). "unknown" = non-git build.
pub const BUILD_COMMIT: &str = match option_env!("CORTEX_BUILD_COMMIT") {
    Some(sha) if !sha.is_empty() => sha,
    _ => "unknown",
};

/// `--version` probe for binaries that parse [`cli::AnalyzerArgs`] directly
/// (`analyzer-python`, `analyzer-ts`) and so have no per-binary wrapper
/// struct to hang a clap `version` attribute on. Prints the same
/// `<name> <commit>` shape the clap attribute produces and returns `true`
/// when the probe fired (caller exits before parsing).
pub fn print_version_probe(binary_name: &str, argv: &[String]) -> bool {
    if !argv.iter().any(|arg| arg == "--version") {
        return false;
    }
    println!("{binary_name} {BUILD_COMMIT}");
    true
}

pub use cli::AnalyzerArgs;
pub use traits::{Analyzer, AnalyzerContext, AnalyzerResult};

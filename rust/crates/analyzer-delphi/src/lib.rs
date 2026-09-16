//! analyzer-delphi support lib — module root cho binary + integration tests.
//!
//! Phase-01 hoist (plan `260916-1520-legacy17-graphless-emit`): exposes
//! `run(args, extra)` cho integration tests in `tests/graphless_emit.rs` để
//! verify graphless mode (`CORTEX_DISABLE_GRAPH=1`) emits the
//! `EmbeddingInputArtifact` even when no graph store is opened.

pub mod danalyzer;
pub mod dparse;
pub mod resolve;

use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;

pub use danalyzer::DelphiExtraArgs;

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ delphi-specific.
/// Re-exported từ lib để integration tests parse full argv một lần (thay vì
/// phải tách AnalyzerArgs / DelphiExtraArgs ra, dễ vỡ khi argv có flag
/// chung mà DelphiExtraArgs không khai báo).
#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
pub struct DelphiArgs {
    #[command(flatten)]
    pub base: AnalyzerArgs,
    #[command(flatten)]
    pub extra: DelphiExtraArgs,
}

impl DelphiArgs {
    /// Parse thẳng argv — delphi_analyzer.py SET default enable_message_scan
    /// = True (giống normalize của framework).
    pub fn parse_from(env: &[String]) -> Self {
        let strs: Vec<&str> = env.iter().map(String::as_str).collect();
        <Self as clap::Parser>::parse_from(strs)
    }
}

/// Re-export `execute` cho integration tests — tương đương `main()` nhưng
/// trả `i32` trực tiếp thay vì exit.
pub fn run(args: &AnalyzerArgs, extra: &DelphiExtraArgs) -> i32 {
    match danalyzer::execute(args, extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}
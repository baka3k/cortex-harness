//! analyzer-cplus support lib — module root của binary (cargo test chạy ở đây).
//!
//! Port parity-first: giữ literal các function signatures / vòng lặp của
//! Python nên một số pedantic lint bị tắt ở mức crate.
#![allow(clippy::too_many_arguments)]
#![allow(clippy::type_complexity)]
#![allow(clippy::needless_range_loop)]
#![allow(clippy::doc_lazy_continuation)]
#![allow(clippy::manual_find)]
#![allow(clippy::field_reassign_with_default)]

pub mod analyzer;
pub mod cparse;
pub mod cscan;
pub mod identity;
pub mod position;
pub mod quality;
pub mod rcparse;
pub mod validate;

use clap::Args;

/// Cờ cplus-specific ngoài contract chung — mirror `parse_args` của
/// cplus_analyzer.py; phần không áp dụng cho backend Rust nhận và bỏ qua.
#[derive(Debug, Clone, Default, Args)]
pub struct CplusExtraArgs {
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub neo4j_calls_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub neo4j_state: Option<String>,
    #[arg(long, hide = true)]
    pub disable_neo4j_resume: bool,

    #[arg(long, hide = true)]
    pub qdrant_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_timeout: Option<f64>,
    #[arg(long, hide = true)]
    pub qdrant_retries: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_retry_sleep: Option<f64>,

    /// Tree-sitter parser-quality policy: off | report | repair.
    /// `repair` cho phép same-backend retry phía clang Python plane —
    /// backend Rust ghi nhận policy nhưng recovery chạy phía Python.
    #[arg(long, default_value = "report")]
    pub parse_quality: String,
    #[arg(long, hide = true)]
    pub parse_quality_report: Option<String>,
    #[arg(long, alias = "parse-errors-path", hide = true)]
    pub parse_errors_path: Option<String>,
    #[arg(long, hide = true)]
    pub parse_quality_max_records: Option<i64>,
    #[arg(long, hide = true)]
    pub parse_quality_max_bytes: Option<i64>,
    #[arg(long, hide = true)]
    pub parse_quality_max_files: Option<i64>,
    #[arg(long, hide = true)]
    pub parse_quality_wall_seconds: Option<i64>,
    #[arg(long, hide = true)]
    pub parse_quality_workers: Option<i64>,

    #[arg(long, hide = true)]
    pub compile_commands_path: Option<String>,
    /// Skip auto-bootstrap của compile_commands.json — mirror thông báo
    /// `[compile-db] executable bootstrap disabled; ...` của Python.
    #[arg(long)]
    pub disable_compile_db_bootstrap: bool,
    #[arg(long, hide = true)]
    pub compile_db_symlink: bool,

    #[arg(long, hide = true)]
    pub event_map: Option<String>,
    #[arg(long, hide = true)]
    pub call_stats_path: Option<String>,
    #[arg(long, hide = true)]
    pub possible_calls_path: Option<String>,
    #[arg(long, hide = true)]
    pub unresolved_calls_path: Option<String>,
}

/// Wrapper test: parse 1 file trả payload.
pub struct Payload(pub cparse::FilePayload);

impl std::ops::Deref for Payload {
    type Target = cparse::FilePayload;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

pub fn parse_file_for_test(
    path: &std::path::Path,
    root: &std::path::Path,
    is_cpp: bool,
) -> Payload {
    Payload(cparse::load_or_parse_payload(path, root, is_cpp))
}

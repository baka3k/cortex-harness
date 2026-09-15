//! CLI — `AnalyzerArgs` (contract chung) + `VbExtraArgs` (cờ riêng của
//! `vb_analyzer_base.parse_args`; phần không áp dụng cho backend Rust nhận và
//! bỏ qua).

use cortex_analyzer_framework::cli::AnalyzerArgs;

/// Dialect hợp lệ cho `--dialect` (mirror `sorted(_PARSER_FACTORY.keys())`).
#[derive(Debug, Clone, Copy, clap::ValueEnum, PartialEq, Eq)]
pub enum Dialect {
    Vb6,
    Vba,
    Vbnet,
    Vbscript,
}

impl Dialect {
    pub fn as_str(&self) -> &'static str {
        match self {
            Dialect::Vb6 => "vb6",
            Dialect::Vba => "vba",
            Dialect::Vbnet => "vbnet",
            Dialect::Vbscript => "vbscript",
        }
    }
}

/// Cờ extra của `vb_analyzer_base.parse_args` mà `AnalyzerArgs` chưa có.
/// (plane Python hoặc không tác động graph-plane Rust — nhận và bỏ qua, trừ
/// `--neo4j-batch-size` dùng cho writer batch size như Python.)
#[derive(Debug, Clone, clap::Args)]
pub struct VbExtraArgs {
    /// Dialect — mỗi binary preset riêng; entry script Python inject khi thiếu.
    #[arg(long, hide = true)]
    pub dialect: Option<Dialect>,

    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow

    /// Writer batch size (`--neo4j-batch-size`, default 1000 như Python).
    #[arg(long, default_value_t = 1000, hide = true)]
    pub neo4j_batch_size: i64,

    #[arg(long, default_value_t = 128, hide = true)]
    pub qdrant_batch_size: i64,
    #[arg(long, default_value_t = 300.0, hide = true)]
    pub qdrant_timeout: f64,
    #[arg(long, default_value_t = 3, hide = true)]
    pub qdrant_retries: i64,
    #[arg(long, default_value_t = 2.0, hide = true)]
    pub qdrant_retry_sleep: f64,

    /// Parse parallelism — Rust parse tuần tự (deterministic); chỉ nhận.
    #[arg(long, default_value_t = 4, hide = true)]
    pub parallel_workers: i64,

    /// VB.NET parser engine: `auto` (Roslyn worker subprocess, key decision #8)
    /// | `roslyn` | `regex` (fallback path — port exact).
    #[arg(long, default_value = "auto", hide = true)]
    pub vbnet_parser_engine: String,

    /// Roslyn semantic mode: `auto|on|off` — pass-through cho worker.
    #[arg(long, default_value = "auto", hide = true)]
    pub vbnet_semantic: String,

    /// Path tới RoslynVbWorker.csproj (default: env `VBNET_ROSLYN_WORKER_PROJECT`
    /// hoặc layout repo chuẩn `code-tiny/tools/vb/roslyn_worker/...`).
    #[arg(long, hide = true)]
    pub vbnet_roslyn_worker_project: Option<String>,
    #[arg(long, default_value_t = 600.0, hide = true)]
    pub vbnet_roslyn_timeout_sec: f64,
    #[arg(long, default_value_t = 120000, hide = true)]
    pub vbnet_roslyn_workspace_timeout_ms: i64,
    #[arg(long, default_value_t = 60000, hide = true)]
    pub vbnet_roslyn_file_timeout_ms: i64,
}

/// CLI đầy đủ: contract chung (AnalyzerArgs) flatten + cờ vb-specific.
#[derive(Debug, clap::Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
pub struct VbArgs {
    #[command(flatten)]
    pub base: AnalyzerArgs,
    #[command(flatten)]
    pub extra: VbExtraArgs,
}

impl VbArgs {
    /// Parse argv (đã normalize message-scan/dialect) — clap error → exit 2
    /// (khớp argparse error code).
    pub fn parse_argv(argv: &[String]) -> Result<Self, i32> {
        let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
        match <Self as clap::Parser>::try_parse_from(&strs) {
            Ok(args) => Ok(args),
            Err(error) => {
                // argparse in usage/error ra stderr rồi exit 2; --help exit 0.
                if error.use_stderr() {
                    let _ = error.print();
                    Err(2)
                } else {
                    let _ = error.print();
                    Err(0)
                }
            }
        }
    }
}

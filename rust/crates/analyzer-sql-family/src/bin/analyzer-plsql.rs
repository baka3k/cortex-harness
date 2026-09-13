//! analyzer-plsql — Rust port của `tools/plsql/plsql_analyzer.py` (phase 08):
//! packages/triggers/jobs regex scan → LanguageCodeWriter::write_all.

use analyzer_sql_family::plsql;
use analyzer_sql_family::sqlcommon;
use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;

#[derive(Debug, Clone, clap::Args)]
pub struct PlsqlExtraArgs {
    #[arg(long, hide = true)]
    pub call_scope: Option<String>,

    #[arg(long, hide = true, default_value_t = 1000)]
    pub neo4j_batch_size: i64,

    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow

    #[arg(long, hide = true)]
    pub qdrant_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_timeout: Option<f64>,
    #[arg(long, hide = true)]
    pub qdrant_retries: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_retry_sleep: Option<f64>,

    #[arg(long, hide = true)]
    pub neo4j_state: Option<String>,
    #[arg(long, hide = true)]
    pub disable_neo4j_resume: bool,

    #[arg(long, hide = true)]
    pub unresolved_calls_path: Option<String>,
    #[arg(long, hide = true)]
    pub call_stats_path: Option<String>,
}

#[derive(Debug, Parser)]
#[command(no_binary_name = true)]
struct PlsqlArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: PlsqlExtraArgs,
}

fn main() {
    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    argv.retain(|a| a != "--enable-message-scan");
    if !argv.iter().any(|a| a == "--disable-message-scan") {
        argv.push("--enable-message-scan".to_string());
    }
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = PlsqlArgs::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs, extra: &PlsqlExtraArgs) -> i32 {
    let call_scope = extra
        .call_scope
        .clone()
        .or_else(|| std::env::var("CALL_SCOPE").ok())
        .unwrap_or_else(|| "internal".to_string());
    let spec = plsql::analyzer_spec();
    match sqlcommon::run(args, &spec, &call_scope, extra.neo4j_batch_size.max(1) as usize) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

//! analyzer-database-schema — Rust port của
//! `tools/database_schema/database_schema_analyzer.py` (phase 08 overlay,
//! dialect sql|plsql): regex DDL scan → DatabaseSchemaWriter.

use analyzer_sql_family::database_schema;
use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;

#[derive(Debug, Clone, clap::Args)]
pub struct SchemaExtraArgs {
    /// sql | plsql (bắt buộc như Python `--dialect`).
    #[arg(long, default_value = "")]
    pub dialect: String,

    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
}

#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct SchemaArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: SchemaExtraArgs,
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    // database_schema parse_args chỉ có `--disable-message-scan` (store_true)
    // — normalize cờ bật để contract parse được.
    let argv: Vec<String> = argv
        .into_iter()
        .filter(|a| a != "--enable-message-scan")
        .collect();
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = SchemaArgs::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs, extra: &SchemaExtraArgs) -> i32 {
    match database_schema::execute(args, &extra.dialect) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

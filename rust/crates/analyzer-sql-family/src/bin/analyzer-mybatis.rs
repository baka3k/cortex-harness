//! analyzer-mybatis — Rust port của `tools/mybatis/mybatis_analyzer.py`
//! (phase 08 framework overlay; prerequisite: java/kotlin base graph).
//! Pipeline: detector → mapper-interface → annotation-mapper → mapper-XML →
//! SQL-semantic → resolver → MyBatisFactWriter.
//!
//! Qdrant/embedding/message-scan nhận và bỏ qua (plane Python).

use analyzer_sql_family::mybatis;
use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;

/// Cờ mybatis-specific ngoài contract chung — mirror `parse_args`.
#[derive(Debug, Clone, clap::Args)]
pub struct MyBatisExtraArgs {
    /// auto | java | kotlin | both (default auto → java+kotlin).
    #[arg(long, default_value = "auto")]
    pub languages: String,

    #[arg(long, default_value = "")]
    pub mybatis_facts_output: String,

    #[arg(long, default_value = "")]
    pub mybatis_dependency_output: String,

    #[arg(long, default_value_t = 1000)]
    pub neo4j_batch_size: i64,

    // Neo4j legacy flags — nhận và bỏ qua (contract).
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,
}

#[derive(Debug, Parser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct MyBatisArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: MyBatisExtraArgs,
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = MyBatisArgs::parse_from(strs);
    let mut common = args.common;
    // mybatis parse_args: message-scan default False (`set_defaults(False)`),
    // --enable/--disable ghi đè — chỉ là plane flag, không ảnh hưởng graph.
    if !common.disable_message_scan {
        common.enable_message_scan = false;
    }
    let code = run(&common, &args.extra);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs, extra: &MyBatisExtraArgs) -> i32 {
    let options = mybatis::MyBatisOptions {
        languages: extra.languages.clone(),
        facts_output: extra.mybatis_facts_output.clone(),
        dependency_output: extra.mybatis_dependency_output.clone(),
        neo4j_batch_size: extra.neo4j_batch_size.max(1) as usize,
        neo4j_creds_complete: extra.neo4j_uri.is_some()
            && extra.neo4j_user.is_some()
            && extra.neo4j_password.is_some(),
    };
    match mybatis::execute(args, &options) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

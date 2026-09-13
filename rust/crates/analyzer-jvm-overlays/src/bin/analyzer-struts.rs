//! analyzer-struts — Rust port của `tools/struts/struts_analyzer.py`.
//!
//! Ghi graph bằng `LanguageCodeWriter` (cortex-graph-writer, cùng contract
//! `write_nodes_batch`/`write_relations_typed` như Python) sau cleanup
//! `StrutsFact` của project. In `[SCAN_RESULT] parser=struts facts=...
//! graph=...`.

use std::collections::BTreeMap;
use std::path::PathBuf;

use clap::Parser;
use serde_json::{json, Value};

use cortex_analyzer_framework::cli::parse_falkordb_uri;
use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore, StoreError};

use analyzer_jvm_overlays::pyjson;
use analyzer_jvm_overlays::struts::models::analysis_result_to_dict;
use analyzer_jvm_overlays::struts::pipeline::run_struts_analysis;

#[derive(Debug, Parser)]
#[command(no_binary_name = true)]
struct StrutsArgs {
    #[arg(long)]
    root: String,
    #[arg(long, default_value = "struts-project")]
    project_id: String,
    #[arg(long, default_value = "")]
    project_name: String,
    #[arg(long = "selected-path")]
    selected_path: Vec<String>,
    #[arg(long)]
    output: Option<String>,
    #[arg(long)]
    compact: bool,
    #[arg(long, default_value = "")]
    commit_sha_before: String,
    #[arg(long, default_value = "")]
    commit_sha_after: String,
    #[arg(long)]
    incremental: bool,
    #[arg(long)]
    changed_files_manifest: Option<String>,
    #[arg(long)]
    deleted_files_manifest: Option<String>,
    #[arg(long)]
    ignore_cache: bool,
    #[arg(long)]
    enable_message_scan: bool,
    #[arg(long)]
    disable_message_scan: bool,
    #[arg(long)]
    message_output_dir: Option<String>,
    #[arg(long)]
    message_qdrant_collection: Option<String>,
    #[arg(long)]
    neo4j_uri: Option<String>,
    #[arg(long)]
    neo4j_user: Option<String>,
    #[arg(long)]
    neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    neo4j_db: Option<String>,
    #[arg(long, default_value_t = 1000)]
    neo4j_batch_size: i64,
    #[arg(long)]
    verbose: bool,
    // graph provider args (tools.graph.cli.add_graph_provider_args)
    #[arg(long, default_value = "falkordb")]
    graph_provider: String,
    #[arg(long)]
    falkordb_uri: Option<String>,
    #[arg(long)]
    falkordb_path: Option<String>,
    #[arg(long)]
    falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    falkordb_ssl: bool,
    #[arg(long)]
    falkordb_graph: Option<String>,
    #[arg(long)]
    ladybug_path: Option<String>,
    #[arg(long)]
    ladybug_graph: Option<String>,
}

fn main() {
    let args = StrutsArgs::parse_from(std::env::args().skip(1));
    std::process::exit(run(&args));
}

fn open_store(args: &StrutsArgs) -> Result<(Box<dyn GraphStore>, Option<String>), StoreError> {
    match args.graph_provider.to_lowercase().as_str() {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| StoreError::Invalid("provider=ladybug cần --ladybug-path".into()))?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| args.project_id.clone());
            let store = LadybugStore::open(std::path::Path::new(&path), &graph)?;
            Ok((Box::new(store), Some(graph)))
        }
        "falkordb" | "falkor" => {
            let graph = args
                .falkordb_graph
                .clone()
                .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| args.project_id.clone());
            let uri = args
                .falkordb_uri
                .clone()
                .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                .unwrap_or_default();
            let (host, port) = parse_falkordb_uri(&uri);
            let client = FalkorDbClient::connect_verified(&host, port)?;
            Ok((Box::new(FalkorDbStore::new(client, graph.clone())), Some(graph)))
        }
        other => Err(StoreError::Invalid(format!(
            "unsupported --graph-provider: {other}"
        ))),
    }
}

/// `_GRAPH_IDENTIFIER_RE.fullmatch` — label/type chỉ [A-Za-z][A-Za-z0-9_]*.
fn safe_graph_identifier(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(first) if first.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|ch| ch.is_ascii_alphanumeric() || ch == '_')
}

fn run(args: &StrutsArgs) -> i32 {
    let result = run_struts_analysis(&args.root, &args.project_id, &args.project_name, &args.selected_path);
    let payload = analysis_result_to_dict(&result);
    if let Some(output) = &args.output {
        let path = PathBuf::from(output);
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let serialized = if args.compact {
            pyjson::dumps_compact_unicode(&payload)
        } else {
            pyjson::dumps_pretty_unicode(&payload)
        };
        let body = if serialized.ends_with('\n') { serialized } else { format!("{serialized}\n") };
        if std::fs::write(&path, body).is_err() {
            eprintln!("[struts] ERROR: cannot write output {output}");
            return 1;
        }
    } else if !(args.commit_sha_before.is_empty() && args.commit_sha_after.is_empty() && !args.incremental) {
        // Python in payload ra stdout chỉ khi KHÔNG ghi graph — nhánh này là
        // "có commit-sha/incremental" nên không in; không output file thì cũng
        // không in (khớp elif python).
    } else {
        println!("{}", pyjson::dumps_pretty_unicode(&payload).trim_end());
    }
    if result.diagnostics.iter().any(|item| item.severity == "error") {
        return 1;
    }
    let should_write_graph = !args.commit_sha_before.is_empty()
        || !args.commit_sha_after.is_empty()
        || args.incremental
        || args.neo4j_uri.is_some()
        || args.falkordb_path.is_some()
        || args.graph_provider == "falkordb"
        || std::env::var("NEO4J_URI").map(|v| !v.is_empty()).unwrap_or(false);
    if !should_write_graph {
        return 0;
    }
    match write_graph(args, &result) {
        Ok(counts) => {
            let total: i64 = counts.values().sum();
            println!(
                "[SCAN_RESULT] parser=struts facts={} relationships={} diagnostics={} graph={}",
                result.semantic_facts.len(),
                result.relationships.len(),
                result.diagnostics.len(),
                total
            );
            0
        }
        Err(error) => {
            eprintln!("[struts] ERROR: graph write failed: {error}");
            3
        }
    }
}

fn write_graph(
    args: &StrutsArgs,
    result: &analyzer_jvm_overlays::struts::models::StrutsAnalysisResult,
) -> Result<BTreeMap<String, i64>, String> {
    let (mut store, database) = open_store(args).map_err(|error| error.to_string())?;
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();

    // Cleanup: xoá StrutsFact cũ của project (chạy trước khi writer chiếm
    // quyền sở hữu store — như python cleanup_batch).
    let cleanup_query = "MATCH (n:StrutsFact {project_id: $project_id}) DETACH DELETE n RETURN count(n) AS count";
    let mut params: BTreeMap<String, Value> = BTreeMap::new();
    params.insert("project_id".to_string(), json!(result.project_id));
    let records = store
        .execute_query(cleanup_query, &params, database.as_deref())
        .map_err(|error| error.to_string())?;
    counts.insert(
        "deleted_nodes".to_string(),
        records
            .first()
            .and_then(|record| record.get("count"))
            .and_then(Value::as_i64)
            .unwrap_or(0),
    );

    let mut writer = LanguageCodeWriter::new(
        store,
        database.clone(),
        args.neo4j_batch_size.max(1) as usize,
        // verbose=false — Python LanguageCodeWriter không in progress trên các
        // đường write_nodes_batch/write_relations_typed.
        false,
    );

    // Node MERGE theo kind (sorted) — write_nodes_batch không RETURN count →
    // ước lượng len(batch) như Python.
    let mut facts_by_kind: BTreeMap<String, Vec<serde_json::Map<String, Value>>> = BTreeMap::new();
    for fact in &result.semantic_facts {
        if !safe_graph_identifier(&fact.kind) {
            return Err(format!("Unsafe Struts graph label: {:?}", fact.kind));
        }
        facts_by_kind
            .entry(fact.kind.clone())
            .or_default()
            .push(fact.to_graph_node());
    }
    for (kind, rows) in &facts_by_kind {
        let cypher = format!(
            "UNWIND $rows AS row MERGE (n:StrutsFact:{kind} {{id: row.id}}) SET n += row"
        );
        let written = writer
            .write_nodes_batch(&format!("struts:{kind}"), &cypher, rows)
            .map_err(|error| error.to_string())?;
        counts.insert(kind.clone(), written as i64);
    }

    // Relationships → write_relations_typed.
    let mut relations: Vec<serde_json::Map<String, Value>> = Vec::new();
    for relationship in &result.relationships {
        if !safe_graph_identifier(&relationship.rel_type) {
            return Err(format!("Unsafe Struts relationship type: {:?}", relationship.rel_type));
        }
        let mut row = relationship.to_graph_row();
        let properties = row
            .remove("properties")
            .and_then(|value| value.as_object().cloned())
            .unwrap_or_default();
        let mut merged = properties;
        for (key, value) in &row {
            if !["from_id", "to_id", "from_label", "to_label", "type"].contains(&key.as_str()) {
                merged.insert(key.clone(), value.clone());
            }
        }
        let mut rel_row = serde_json::Map::new();
        rel_row.insert("source_id".into(), row["from_id"].clone());
        rel_row.insert("target_id".into(), row["to_id"].clone());
        rel_row.insert("source_label".into(), row["from_label"].clone());
        rel_row.insert("target_label".into(), row["to_label"].clone());
        rel_row.insert("rel_type".into(), row["type"].clone());
        rel_row.insert("properties".into(), Value::Object(merged));
        relations.push(rel_row);
    }
    let written = writer
        .write_relations_typed(&relations, Some(&result.project_id))
        .map_err(|error| error.to_string())?;
    counts.insert("relationships".to_string(), written as i64);
    Ok(counts)
}

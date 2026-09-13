//! Port `tools/mybatis/mybatis_analyzer.py` — framework overlay entry:
//! run_mybatis_foundation → fact artifact → MyBatisFactWriter graph write.
//!
//! CLI flags ngoài AnalyzerArgs: `--languages`, `--mybatis-facts-output`,
//! `--mybatis-dependency-output`, `--neo4j-batch-size`. Qdrant/embedding/
//! message-scan/parse-cache là plane Python — nhận và bỏ qua.

pub mod annotation_mapper;
pub mod detector;
pub mod dynamic_sql;
pub mod fact_writer;
pub mod java_symbols;
pub mod mapper_interface;
pub mod mapper_xml;
pub mod models;
pub mod pipeline;
pub mod resolver;
pub mod sql_semantic;
pub mod spring_bridge;

use std::path::Path;

use serde_json::{json, Map, Value};

use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_graph_writer::store::GraphStore;

use crate::mybatis::fact_writer::MyBatisFactWriter;
use crate::mybatis::pipeline::run_mybatis_foundation;

pub const MYBATIS_PARSER_VERSION: &str = "mybatis-v2026-07-13-1";

/// Cờ mybatis-specific (mirror parse_args; phần plane Python nhận-bỏ-qua do
/// binary khai báo) — các flag ảnh hưởng graph/output nằm ở đây.
pub struct MyBatisOptions {
    pub languages: String,
    pub facts_output: String,
    pub dependency_output: String,
    pub neo4j_batch_size: usize,
    /// neo4j-uri/user/password được truyền đầy đủ (contract contract check).
    pub neo4j_creds_complete: bool,
}

/// `_languages_from_arg`.
pub fn languages_from_arg(value: &str) -> Vec<&'static str> {
    match value {
        "java" => vec!["java"],
        "kotlin" => vec!["kotlin"],
        _ => vec!["java", "kotlin"],
    }
}

/// `main()` — trả exit code (0 OK, 2 root not found, 3 driver create failed).
pub fn execute(args: &AnalyzerArgs, options: &MyBatisOptions) -> Result<i32, String> {
    let root = cortex_analyzer_framework::cli::abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return Ok(2);
    }
    let verbose = args.verbose;
    let project_id = args.project_id_or_root();
    let project_name = args.project_name();
    let languages = languages_from_arg(&options.languages);

    let result = run_mybatis_foundation(
        &args.root,
        &project_id,
        &project_name,
        &languages,
    );

    // Artifacts — plane phụ (nội dung không parity); path qua flag hoặc cache.
    let artifact_path = options.facts_output.clone();
    let dependency_path = options.dependency_output.clone();
    write_stub_artifact(&artifact_path, &result);
    write_stub_dependency_index(&dependency_path);

    println!(
        "[mybatis] modules={} artifacts={} parser_capabilities={} semantic_facts={} relationships={} diagnostics={} artifact={}",
        result.modules.len(),
        result.artifacts.len(),
        result.parser_capabilities,
        result.semantic_facts.len(),
        result.relationships.len(),
        result.diagnostics_count,
        artifact_path,
    );

    if args.dry_run {
        return Ok(0);
    }

    // ── graph write ─────────────────────────────────────────────────────────
    if args.graph_writes_disabled() {
        return Ok(0);
    }
    let provider = args.graph_provider.to_lowercase();
    let provider_label = provider.clone();
    let mut store: Box<dyn GraphStore> = match provider.as_str() {
        "falkordb" | "falkor" => match args.open_store() {
            Ok(store) => store,
            Err(error) => {
                eprintln!(
                    "[mybatis] ERROR: FalkorDB driver creation failed: {error:?}. Path={:?} graph={:?}.",
                    args.falkordb_path, args.falkordb_graph
                );
                return Ok(3);
            }
        },
        "ladybug" => match args.open_store() {
            Ok(store) => store,
            Err(error) => {
                eprintln!("[mybatis] ERROR: Ladybug driver creation failed: {error:?}");
                return Ok(3);
            }
        },
        // neo4j credentials path — backend Rust không hỗ trợ; credentials thiếu
        // → return 0 như Python khi không --require-neo4j.
        _ => {
            if !options.neo4j_creds_complete {
                return Ok(0);
            }
            return Err(
                "[mybatis] ERROR: neo4j provider chưa hỗ trợ trên backend Rust".to_string(),
            );
        }
    };

    // Cleanup (incremental) — changed ∪ deleted manifest paths.
    let mut cleanup_paths: Vec<String> = Vec::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            cleanup_paths.extend(load_manifest_paths(manifest, &root));
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            cleanup_paths.extend(load_manifest_paths(manifest, &root));
        }
    }
    let mut writer = MyBatisFactWriter::new(store.as_mut(), options.neo4j_batch_size, verbose);
    if !cleanup_paths.is_empty() {
        let cleanup_project = if project_id.is_empty() {
            root.file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default()
        } else {
            project_id.clone()
        };
        let deleted = writer
            .cleanup_files(&cleanup_project, &cleanup_paths)
            .map_err(|e| e.to_string())?;
        println!(
            "[cleanup][{provider_label}] deleted_nodes={} deleted_unknown_functions=0",
            deleted
        );
    }
    let node_rows: Vec<Map<String, Value>> = result
        .semantic_facts
        .iter()
        .map(|fact| fact.to_graph_node())
        .collect();
    let written = writer
        .write_fact_nodes(&node_rows)
        .map_err(|e| e.to_string())?;
    println!(
        "[{provider_label}] mybatis_facts {}/{}",
        written,
        result.semantic_facts.len()
    );
    let rel_rows: Vec<Map<String, Value>> = result
        .relationships
        .iter()
        .map(|rel| rel.to_graph_relationship())
        .collect();
    let rel_written = writer
        .write_relationships(&rel_rows)
        .map_err(|e| e.to_string())?;
    println!(
        "[{provider_label}] mybatis_relationships {}/{}",
        rel_written,
        result.relationships.len()
    );
    Ok(0)
}

fn write_stub_artifact(path: &str, result: &crate::mybatis::pipeline::MyBatisAnalysisResult) {
    if path.is_empty() {
        return;
    }
    if let Some(parent) = Path::new(path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let payload = json!({
        "cache_version": MYBATIS_PARSER_VERSION,
        "project_id": result.project_id,
        "modules": result.modules.len(),
        "semantic_facts": result.semantic_facts.len(),
        "relationships": result.relationships.len(),
        "note": "rust backend artifact summary (full fact payload is python-plane)",
    });
    let tmp = format!("{path}.tmp");
    if std::fs::write(&tmp, format!("{payload}\n")).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

fn write_stub_dependency_index(path: &str) {
    if path.is_empty() {
        return;
    }
    if let Some(parent) = Path::new(path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let payload = json!({
        "cache_version": MYBATIS_PARSER_VERSION,
        "dependency_index": {"files": {}},
    });
    let tmp = format!("{path}.tmp");
    if std::fs::write(&tmp, format!("{payload}\n")).is_ok() {
        let _ = std::fs::rename(&tmp, path);
    }
}

//! `jp1_analyzer.py::main` orchestration — scan → parse → rows → cleanup →
//! graph write (Jp1Unit MERGE + typed relations) → `[SCAN_RESULT]`.
//!
//! Scope difference: qdrant/embedding is a Python plane — `--qdrant-*`,
//! `--embed-*`, `--device`, `--batch-size`, ... accepted and ignored;
//! `vectors=0 vector_status=disabled` is always emitted. `--output/-o`,
//! `--pretty`, `--dry-run` JSON payload, `--cache-dir`, `--ignore-cache` are
//! accepted-and-ignored / debug-plane (payload JSON is not ported).

use std::collections::BTreeMap;

use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use serde_json::{json, Map, Value};

use crate::parser::{relation_row, unit_row};
use crate::pipeline::{cleanup_paths, py_realpath, run_jp1_analysis};

/// Extra flags của `jp1_analyzer.py::parse_args` mà `AnalyzerArgs` chưa có.
#[derive(Debug, Clone, Parser)]
#[command(no_binary_name = true)]
pub struct Jp1ExtraArgs {
    /// Python positional `path` (fallback khi thiếu --root).
    #[arg(hide = true)]
    pub path: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, default_value_t = 1000, hide = true)]
    pub neo4j_batch_size: usize,
    #[arg(long, default_value_t = 128, hide = true)]
    pub qdrant_batch_size: i64,
    #[arg(long, default_value_t = 300.0, hide = true)]
    pub qdrant_timeout: f64,
    #[arg(long, default_value_t = 3, hide = true)]
    pub qdrant_retries: i64,
    #[arg(long, default_value_t = 2.0, hide = true)]
    pub qdrant_retry_sleep: f64,
    /// `--output/-o` — Python ghi JSON payload ra file; Rust không ghi.
    #[arg(long = "output", short = 'o', hide = true)]
    pub output: Option<String>,
    #[arg(long, hide = true)]
    pub pretty: bool,
}

fn env_or(flag: &Option<String>, env_var: &str) -> Option<String> {
    if let Some(value) = flag
        && !value.is_empty() {
            return Some(value.clone());
        }
    std::env::var(env_var)
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| flag.clone())
}

pub fn execute(args: &AnalyzerArgs, extra: &Jp1ExtraArgs) -> Result<i32, String> {
    // root = os.path.realpath(args.root or args.path or "")
    let root = py_realpath(std::path::Path::new(&args.root));
    if !root.is_dir() {
        println!("A valid --root directory is required");
        return Ok(2);
    }
    let project_id = env_or(&args.project_id, "PROJECT_ID")
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            root.file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default()
        });
    // Python: changed = load_manifest_paths(...) if incremental AND manifest
    // else None (None ⇒ full scan; manifest có nhưng rỗng ⇒ selected rỗng).
    let changed: Option<Vec<String>> = if args.incremental {
        args.changed_files_manifest.as_deref().map(|manifest| {
            cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                .into_iter()
                .collect()
        })
    } else {
        None
    };
    let deleted: Vec<String> = if args.incremental {
        args.deleted_files_manifest
            .as_deref()
            .map(|manifest| {
                cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                    .into_iter()
                    .collect()
            })
            .unwrap_or_default()
    } else {
        Vec::new()
    };
    let result = run_jp1_analysis(&root, &project_id, changed.as_deref(), &deleted)?;

    if !args.dry_run {
        write_graph(args, extra, &result)?;
    }
    // Vector sync — Python plane; Rust accept-and-ignore.
    let vector_count = 0usize;
    let vector_status = "disabled";
    println!(
        "[SCAN_RESULT] parser=jp1 files={} functions=0 vectors={vector_count} vector_status={vector_status}",
        result.files.len()
    );
    Ok(0)
}

/// `build_graph_rows` + `_write_graph`.
fn write_graph(
    args: &AnalyzerArgs,
    extra: &Jp1ExtraArgs,
    result: &crate::pipeline::Jp1AnalysisResult,
) -> Result<(), String> {
    // Python: if not prepare_graph_args(args): return {} — CORTEX_DISABLE_GRAPH
    // hoặc provider neo4j thiếu creds ⇒ im lặng bỏ qua graph writes.
    if args.graph_writes_disabled() {
        return Ok(());
    }
    let mut store = match args.open_store() {
        Ok(store) => store,
        // Python: create_graph_driver_from_args có thể trả None cho một số
        // cấu hình ⇒ RuntimeError; Rust fail-loud cùng ngưỡng.
        Err(error) => return Err(error.to_string()),
    };
    let project_name = env_or(&args.project_name, "PROJECT_NAME")
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| result.project_id.clone());
    let repo = env_or(&args.repo, "PROJECT_REPO")
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| result.project_id.clone());
    let build_system = env_or(&args.build_system, "PROJECT_BUILD_SYSTEM")
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "jp1".to_string());

    // Python: cleanup chạy TRƯỚC writes (cleanup_neo4j_for_files trên driver
    // thô, không ensure schema) — Rust giữ nguyên thứ tự: cleanup với store
    // rồi mới dựng writer.
    let cleanup = cleanup_paths(&result.changed_paths, &result.deleted_paths);
    if args.incremental && !cleanup.is_empty() {
        if args.verbose {
            println!("[cleanup][graph] deleting graph data for {} files", cleanup.len());
        }
        let (deleted_nodes, deleted_unknown) =
            cortex_analyzer_framework::cleanup::cleanup_graph_files(
                store.as_mut(),
                &result.project_id,
                &cleanup,
            )
            .map_err(|e| e.to_string())?;
        if args.verbose {
            println!(
                "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
            );
        }
    }

    let mut writer = LanguageCodeWriter::new(
        store,
        args.neo4j_db.clone(),
        extra.neo4j_batch_size,
        args.verbose,
    );

    let mut common = Map::new();
    common.insert("project_id".into(), json!(result.project_id));
    common.insert("project_name".into(), json!(project_name));
    common.insert("language".into(), json!("jp1"));
    common.insert("repo".into(), json!(repo));
    common.insert("build_system".into(), json!(build_system));

    let mut units: Vec<Map<String, Value>> = Vec::new();
    let mut relations: Vec<Map<String, Value>> = Vec::new();
    for file in &result.files {
        for unit in &file.units {
            units.push(unit_row(unit, &common));
        }
        for relation in &file.relations {
            relations.push(relation_row(relation));
        }
    }
    // required_relations = properties.resolved is not False
    let required: Vec<Map<String, Value>> = relations
        .iter()
        .filter(|row| {
            row.get("properties")
                .and_then(|props| props.get("resolved"))
                .and_then(Value::as_bool)
                != Some(false)
        })
        .cloned()
        .collect();
    let unresolved_count = relations.len() - required.len();
    let _counts_jp1_unit = writer
        .write_nodes_batch(
            "jp1:units",
            "UNWIND $rows AS row MERGE (n:Jp1Unit {id: row.id}) SET n += row",
            &units,
        )
        .map_err(|e| e.to_string())?;
    let _counts_relations = writer
        .write_relations_typed(&required, Some(&result.project_id))
        .map_err(|e| e.to_string())?;
    if args.verbose && unresolved_count > 0 {
        println!("[graph] optional unresolved JP1 relations skipped={unresolved_count}");
    }
    Ok(())
}

// BTreeMap dùng bởi write path params (giữ import cho ví dụ mở rộng).
#[allow(dead_code)]
fn _btree_map_marker() -> BTreeMap<String, Value> {
    BTreeMap::new()
}

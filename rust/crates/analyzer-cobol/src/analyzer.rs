//! `cobol_analyzer.py::main` orchestration — CLI flags, manifests, dependency
//! cache, incremental selection, graph write + incremental cleanup,
//! `[SCAN_RESULT]`.
//!
//! Scope difference: qdrant/embedding is a Python plane (torch/transformers) —
//! `--qdrant-url`, `--qdrant-collection`, `--embed-model`, `--device`,
//! `--batch-size`, `--max-embed-chars` are accepted and ignored;
//! `vectors=0` is always emitted. The message-scan flag is accepted and ignored
//! (cobol_analyzer.py also doesn't use it). `tree_sitter_version` in RuntimeInfo
//! is artifact metadata.

use std::collections::BTreeSet;
use std::collections::BTreeMap;
use std::path::PathBuf;

use clap::Parser;
use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use serde_json::{Value, json};

use crate::parser_runtime::{CobolRuntimeError, preflight};
use crate::pipeline::{analyze_project, expanduser, py_resolve, select_incremental_result, write_graph_facts};
use crate::resolver::DependencyIndex;


/// Extra flags of `cobol_analyzer.py::parse_args` that `AnalyzerArgs` doesn't have.
#[derive(Debug, Clone, Parser)]
#[command(no_binary_name = true)]
pub struct CobolExtraArgs {
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, default_value_t = 1000, hide = true)]
    pub neo4j_batch_size: usize,
    #[arg(long, hide = true)]
    pub cobol_language_library: Option<String>,
    #[arg(long = "copybook-root", hide = true)]
    pub copybook_root: Vec<String>,
    #[arg(long = "copybook-extension", hide = true)]
    pub copybook_extension: Vec<String>,
    #[arg(long, hide = true)]
    pub facts_output: Option<String>,
    #[arg(long, hide = true)]
    pub preflight: bool,
}

/// Env fallback matching argparse: flag wins, then env, then None.
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

/// `_manifest_paths` — JSON dict `{"files": [...]}`/array or TXT lines;
/// resolve to rel-posix; raise on missing manifest or path outside root.
fn manifest_paths(path: Option<&str>, root: &std::path::Path) -> Result<Vec<String>, String> {
    let Some(path) = path.filter(|value| !value.is_empty()) else {
        return Ok(Vec::new());
    };
    let manifest = PathBuf::from(path);
    if !manifest.is_file() {
        return Err(format!("manifest not found: {}", manifest.display()));
    }
    let text = std::fs::read_to_string(&manifest).map_err(|e| e.to_string())?;
    let stripped = text.trim_start();
    let value: Value = if serde_json::from_str::<Value>(&text).is_ok()
        && (stripped.starts_with('{') || stripped.starts_with('['))
    {
        serde_json::from_str(&text).map_err(|e| e.to_string())?
    } else {
        Value::Array(
            text.lines()
                .map(|line| line.trim().to_string())
                .filter(|line| !line.is_empty())
                .map(Value::String)
                .collect(),
        )
    };
    let list: Vec<Value> = match &value {
        Value::Array(items) => items.clone(),
        Value::Object(map) => map
            .get("files")
            .or_else(|| map.get("paths"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default(),
        _ => return Err(format!("manifest must contain a list of paths: {}", manifest.display())),
    };
    let mut normalized: Vec<String> = Vec::new();
    for raw in list {
        let raw = raw.as_str().unwrap_or_default();
        let candidate = PathBuf::from(raw);
        let absolute = if candidate.is_absolute() {
            py_resolve(&candidate)
        } else {
            py_resolve(&root.join(candidate))
        };
        let relative = absolute
            .strip_prefix(root)
            .map_err(|_| format!("manifest path is outside the project root: {raw}"))?;
        normalized.push(
            relative
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join("/"),
        );
    }
    let set: BTreeSet<String> = normalized.into_iter().collect();
    Ok(set.into_iter().collect())
}

fn suffix_in_copybook_set(path: &str) -> bool {
    let lower = path.to_lowercase();
    lower.ends_with(".cpy") || lower.ends_with(".copy")
}

pub struct Scope {
    pub project_id: String,
    pub project_name: String,
    pub repo: String,
    pub build_system: String,
}

pub fn execute(args: &AnalyzerArgs, extra: &CobolExtraArgs) -> Result<i32, String> {
    // root = Path(args.root).expanduser().resolve()
    let root = py_resolve(&expanduser(&args.root));
    if !root.is_dir() {
        eprintln!("[cobol] ERROR: root not found: {}", root.display());
        return Ok(2);
    }
    let runtime = match preflight(extra.cobol_language_library.as_deref()) {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("[cobol] ERROR: {}", cobol_error_text(&error));
            return Ok(2);
        }
    };
    if extra.preflight {
        println!(
            "[cobol] preflight ok provider={} platform={} architecture={} abi={}",
            runtime.provider, runtime.platform, runtime.architecture, runtime.grammar_abi
        );
        return Ok(0);
    }
    let scope = build_scope(args, &root);
    let cache_root = match env_or(&args.cache_dir, "QDRANT_CACHE_DIR") {
        Some(cache_dir) => expanduser(&cache_dir),
        None => root.join(".cortex").join("cobol"),
    };
    let dependency_cache = cache_root.join(format!("{}-copybook-dependencies.json", scope.project_id));

    let analysis = run_staged_analysis(args, extra, &root, &scope, &cache_root, &dependency_cache);
    let (result, dependency_index, impacted, _changed, deleted) = match analysis {
        Ok(outcome) => outcome,
        Err(error) => {
            eprintln!("[cobol] ERROR: staged analysis failed: {error}");
            return Ok(2);
        }
    };
    if args.dry_run {
        println!(
            "Dry run: parser=cobol files={} nodes={} edges={} diagnostics={}",
            result.summary.processed_files,
            result.nodes.len(),
            result.edges.len(),
            result.diagnostics.len(),
        );
        return Ok(0);
    }
    let output = match &extra.facts_output {
        Some(facts_output) => expanduser(facts_output),
        None => cache_root.join("facts.json"),
    };
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&output, result.to_json()).map_err(|e| e.to_string())?;

    let mut cleanup_paths: BTreeSet<String> = deleted.iter().cloned().collect();
    if args.incremental {
        cleanup_paths.extend(impacted.iter().cloned());
    }
    let cleanup_paths: Vec<String> = cleanup_paths.into_iter().collect();

    let graph_total = match write_graph(args, extra, &result, &scope, &cleanup_paths) {
        Ok(total) => total,
        Err(error) => {
            eprintln!("[cobol] ERROR: graph write failed after staged analysis: {error}");
            return Ok(3);
        }
    };

    // Vector sync — Python plane; Rust accept-and-ignore ⇒ vectors=0.
    let vector_count = 0usize;

    if let Err(error) = dependency_index.save(&dependency_cache) {
        eprintln!("[cobol] WARNING: dependency cache was not updated: {error}");
    }

    println!(
        "[SCAN_RESULT] parser=cobol files={} nodes={} edges={} diagnostics={} graph={} vectors={} artifact={}",
        result.summary.processed_files,
        result.nodes.len(),
        result.edges.len(),
        result.diagnostics.len(),
        graph_total,
        vector_count,
        output.display(),
    );
    Ok(0)
}

fn build_scope(args: &AnalyzerArgs, root: &std::path::Path) -> Scope {
    let project_id = env_or(&args.project_id, "PROJECT_ID")
        .unwrap_or_else(|| root.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default());
    let project_name =
        env_or(&args.project_name, "PROJECT_NAME").unwrap_or_else(|| project_id.clone());
    let repo = env_or(&args.repo, "PROJECT_REPO")
        .unwrap_or_else(|| root.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default());
    let build_system = env_or(&args.build_system, "PROJECT_BUILD_SYSTEM").unwrap_or_else(|| "cobol".to_string());
    Scope {
        project_id,
        project_name,
        repo,
        build_system,
    }
}

fn cobol_error_text(error: &CobolRuntimeError) -> String {
    error.to_string()
}

type StagedOutcome = (
    crate::models::AnalysisResult,
    DependencyIndex,
    BTreeSet<String>,
    Vec<String>,
    Vec<String>,
);

#[allow(clippy::too_many_arguments)]
fn run_staged_analysis(
    args: &AnalyzerArgs,
    extra: &CobolExtraArgs,
    root: &std::path::Path,
    scope: &Scope,
    _cache_root: &std::path::Path,
    dependency_cache: &std::path::Path,
) -> Result<StagedOutcome, String> {
    let _ = scope;
    let mut changed: Vec<String> = Vec::new();
    let mut deleted: Vec<String> = Vec::new();
    if args.incremental {
        changed = manifest_paths(args.changed_files_manifest.as_deref(), root)?;
        deleted = manifest_paths(args.deleted_files_manifest.as_deref(), root)?;
    }
    let mut old_index: Option<DependencyIndex> = None;
    if args.incremental && dependency_cache.is_file() && !args.ignore_cache {
        match DependencyIndex::load(dependency_cache) {
            Ok(index) => old_index = Some(index),
            Err(error) => {
                if args.verbose {
                    eprintln!("[cobol] WARNING: ignoring dependency cache: {error}");
                }
            }
        }
    }
    let copybook_roots: Vec<PathBuf> = extra
        .copybook_root
        .iter()
        .map(PathBuf::from)
        .collect();
    let copybook_extensions: Vec<String> = extra.copybook_extension.clone();
    let outcome = analyze_project(
        root,
        &scope.project_id,
        extra.cobol_language_library.as_deref(),
        &copybook_roots,
        &copybook_extensions,
    )?;
    let mut result = outcome.result;
    let dependency_index = outcome.dependency_index;
    let mut impacted: BTreeSet<String> = BTreeSet::new();
    if args.incremental {
        impacted.extend(dependency_index.impacted_files(&changed, &deleted));
        if let Some(old_index) = &old_index {
            impacted.extend(old_index.impacted_files(&changed, &deleted));
        }
        if old_index.is_none()
            && changed
                .iter()
                .chain(deleted.iter())
                .any(|path| suffix_in_copybook_set(path))
        {
            impacted.extend(result.nodes.iter().map(|node| node.file_path.clone()));
        }
        result = select_incremental_result(result, impacted.iter().cloned());
    }
    Ok((result, dependency_index, impacted, changed, deleted))
}

/// `write_graph` — returns `sum(counts.values())`.
fn write_graph(
    args: &AnalyzerArgs,
    extra: &CobolExtraArgs,
    result: &crate::models::AnalysisResult,
    scope: &Scope,
    cleanup_paths: &[String],
) -> Result<i64, String> {
    // Python: driver = await create_graph_driver_from_args(args); if None: {}
    if args.graph_writes_disabled() {
        return Ok(0);
    }
    let store = args.open_store().map_err(|e| e.to_string())?;
    let mut writer = LanguageCodeWriter::new(
        store,
        args.neo4j_db.clone(),
        extra.neo4j_batch_size,
        args.verbose,
    );
    let counts = write_graph_facts(
        &mut writer,
        result,
        &scope.project_name,
        &scope.repo,
        &scope.build_system,
    )
    .map_err(|e| e.to_string())?;
    if !cleanup_paths.is_empty() {
        let keep_ids: Vec<String> = result.nodes.iter().map(|node| node.id.clone()).collect();
        let query = "MATCH (n {project_id: $project_id}) \
                     WHERE (n.file_path IN $paths OR n.path IN $paths) \
                     AND NOT n.id IN $keep_ids \
                     DETACH DELETE n RETURN count(n) AS count";
        let params_row = json!({
            "project_id": result.project_id,
            "paths": cleanup_paths,
            "keep_ids": keep_ids,
        })
        .as_object()
        .cloned()
        .expect("cleanup params object");
        // Python: writer.write_batches("cobol:incremental_cleanup",
        //   [cleanup_parameters], cleanup_batch) — return value KHÔNG được gán
        // vào counts (quirk upstream) nên `graph=` không cộng cleanup.
        writer
            .write_batches("cobol:incremental_cleanup", &[params_row], &mut |store, database, batch| {
                let batch_params = batch
                    .first()
                    .map(|row| {
                        row.iter()
                            .map(|(key, value)| (key.clone(), value.clone()))
                            .collect::<BTreeMap<String, Value>>()
                    })
                    .unwrap_or_default();
                store.execute_query(query, &batch_params, database)?;
                Ok(batch.len() as i64)
            })
            .map_err(|e| e.to_string())?;
    }
    Ok(counts.values().sum())
}

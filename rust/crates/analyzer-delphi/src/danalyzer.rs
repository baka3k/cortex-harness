//! `analyzer-delphi` pipeline — port `delphi_analyzer.py::main`/`build_call_graph`:
//! scan → uses index (toàn bộ file) → incremental selection (changed ∪
//! uses-impact BFS) → cleanup (changed ∪ deleted) → parse payloads →
//! `_resolve_calls` → rows (đầy đủ `project_id`) → `write_all` +
//! `write_calls_with_site` → `[SCAN_RESULT]`.
//!
//! Khác biệt scope có chủ đích (như phase 05/06):
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, `--chunk-embed`,
//!   `--max-embed-chars` nhận và bỏ qua; pipeline Rust không sync vector.
//! * Message scan là plane Python-side (default bật như Python) — flags nhận,
//!   no-op.
//! * Parse cache/`--ignore-cache`/`--keep-cache`/`--cache-dir` là plane Python
//!   — nhận và bỏ qua (Rust parse fresh mỗi run, cache-miss path của Python).
//! * `--config` nhận và bỏ qua (Python pre-parse harness config).

use std::collections::{BTreeSet, HashMap, HashSet};
use std::time::Instant;

use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::rel_posix;
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};
use cortex_graph_writer::store::GraphStore;
use serde_json::{json, Map, Value};

use crate::dparse::{self, CallEdge, FileDef, FieldDef, FunctionDef, ParseMeta, ParsedFile, TypeDef};
use crate::resolve;

/// Cờ extra của `delphi_analyzer.py::parse_args` mà `AnalyzerArgs` chưa có.
#[derive(Debug, Clone, clap::Parser)]
#[command(no_binary_name = true)]
pub struct DelphiExtraArgs {
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    /// Writer database (`--neo4j-db`).
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,
    /// Writer batch size (`--neo4j-batch-size`, default 1000 như Python).
    #[arg(long, default_value_t = 1000, hide = true)]
    pub neo4j_batch_size: usize,
    /// Batch size cho `write_calls_with_site` (Python tạm set writer.batch_size).
    #[arg(long, default_value_t = 100, hide = true)]
    pub neo4j_calls_batch_size: usize,
    /// `--neo4j-state` — Python REFUSE chạy (resume disabled): nhận, in lỗi và
    /// exit 2 nếu non-empty.
    #[arg(long, hide = true)]
    pub neo4j_state: Option<String>,
    #[arg(long, hide = true)]
    pub disable_neo4j_resume: bool,
    #[arg(long, default_value_t = 128, hide = true)]
    pub qdrant_batch_size: i64,
    #[arg(long, default_value_t = 300.0, hide = true)]
    pub qdrant_timeout: f64,
    #[arg(long, default_value_t = 3, hide = true)]
    pub qdrant_retries: i64,
    #[arg(long, default_value_t = 2.0, hide = true)]
    pub qdrant_retry_sleep: f64,
    /// `--parse-run-id` — dùng thật: `site_id` của CALLS edge chứa giá trị này
    /// (khớp Python `args.parse_run_id or f"parse-{ts}-{uuid4[:8]}"`).
    #[arg(long, hide = true)]
    pub parse_run_id: Option<String>,
    /// `--commit-sha` — fallback khi thiếu `--commit-sha-after`.
    #[arg(long, hide = true)]
    pub commit_sha: Option<String>,
    /// Báo cáo call-resolution stats JSON.
    #[arg(long, hide = true)]
    pub call_stats_path: Option<String>,
    /// Ghi unresolved calls JSONL.
    #[arg(long, hide = true)]
    pub unresolved_calls_path: Option<String>,
    /// Ghi parse error report JSON.
    #[arg(long, hide = true)]
    pub parse_errors_path: Option<String>,
}

/// Env fallback khớp argparse defaults (flag non-empty → env → flag).
fn env_or(flag: &Option<String>, env_var: &str) -> Option<String> {
    if let Some(value) = flag && !value.is_empty() {
        return Some(value.clone());
    }
    std::env::var(env_var)
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| flag.clone())
}

/// `_stable_point_id` — `str(uuid.uuid5(uuid.NAMESPACE_URL, symbol_id))`.
pub fn stable_point_id(symbol_id: &str) -> String {
    uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, symbol_id.as_bytes()).to_string()
}

fn detect_git_commit_sha(root: &str) -> String {
    let output = std::process::Command::new("git")
        .args(["-C", root, "rev-parse", "--short", "HEAD"])
        .stderr(std::process::Stdio::null())
        .output();
    match output {
        Ok(out) if out.status.success() => {
            let sha = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if sha.is_empty() {
                "unknown".to_string()
            } else {
                sha
            }
        }
        _ => "unknown".to_string(),
    }
}

/// Payload gộp cho pipeline (dict Python → struct).
struct FilePayload {
    file_def: FileDef,
    functions: Vec<FunctionDef>,
    calls: Vec<CallEdge>,
    types: Vec<TypeDef>,
    namespaces: Vec<dparse::NamespaceDef>,
    fields: Vec<FieldDef>,
    relations: Vec<dparse::RelationEdge>,
    uses_units: Vec<String>,
    parse_meta: ParseMeta,
}

impl From<ParsedFile> for FilePayload {
    fn from(parsed: ParsedFile) -> Self {
        Self {
            file_def: parsed.file_def,
            functions: parsed.functions,
            calls: parsed.calls,
            types: parsed.types,
            namespaces: parsed.namespaces,
            fields: parsed.fields,
            relations: parsed.relations,
            uses_units: parsed.uses_units,
            parse_meta: parsed.parse_meta,
        }
    }
}

pub struct Scope {
    pub project_id: String,
    pub project_name: String,
    pub language: String,
    pub repo: String,
    pub build_system: String,
}

/// `main`: `args.X or env or default` — project_id default basename(abspath(root)).
pub fn build_scope(args: &AnalyzerArgs, root: &std::path::Path) -> Scope {
    let project_id = env_or(&args.project_id, "PROJECT_ID").unwrap_or_else(|| {
        root.file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default()
    });
    let project_name = env_or(&args.project_name, "PROJECT_NAME")
        .unwrap_or_else(|| project_id.clone());
    let language =
        env_or(&args.language, "PROJECT_LANGUAGE").unwrap_or_else(|| "delphi".to_string());
    let repo = env_or(&args.repo, "PROJECT_REPO")
        .unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system =
        env_or(&args.build_system, "PROJECT_BUILD_SYSTEM").unwrap_or_default();
    Scope { project_id, project_name, language, repo, build_system }
}

fn open_store(args: &AnalyzerArgs) -> Result<Option<Box<dyn GraphStore>>, String> {
    if args.graph_writes_disabled() {
        return Ok(None);
    }
    args.open_store().map(Some).map_err(|e| e.to_string())
}

/// Entry của main — chạy pipeline, trả exit code (0/2 như Python).
pub fn execute(args: &AnalyzerArgs, extra: &DelphiExtraArgs) -> Result<i32, String> {
    let start = Instant::now();
    let verbose = args.verbose;
    let root = cortex_analyzer_framework::cli::abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return Ok(2);
    }
    if extra
        .neo4j_state
        .as_deref()
        .is_some_and(|state| !state.is_empty())
    {
        eprintln!(
            "[state] Delphi graph resume is disabled until durable checkpoint \
             fingerprints are implemented; remove --neo4j-state and restart"
        );
        return Ok(2);
    }
    let scope = build_scope(args, &root);

    // ── Manifests (chỉ khi incremental) ─────────────────────────────────────
    let mut changed_manifest: Vec<String> = Vec::new();
    let mut deleted_manifest: Vec<String> = Vec::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            changed_manifest = load_manifest_paths_sorted(manifest, &root);
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            deleted_manifest = load_manifest_paths_sorted(manifest, &root);
        }
        if verbose {
            println!(
                "[diff] incremental manifests changed={} deleted={}",
                changed_manifest.len(),
                deleted_manifest.len()
            );
        }
    }

    if verbose {
        println!(
            "[state] Delphi graph resume mode=full-idempotent-replay \
             checkpoint=disabled persistent_retry_queue=false; interrupted work \
             restarts on the next analyzer attempt"
        );
    }

    let parse_run_id = match &extra.parse_run_id {
        Some(id) if !id.is_empty() => id.clone(),
        _ => {
            let secs = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            let hex = uuid::Uuid::new_v4().simple().to_string();
            format!("parse-{secs}-{}", &hex[..8])
        }
    };
    let commit_sha = {
        let after = args.commit_sha_after.trim();
        if !after.is_empty() {
            after.to_string()
        } else {
            match env_or(&extra.commit_sha, "GIT_COMMIT_SHA") {
                Some(sha) if !sha.is_empty() => sha,
                _ => detect_git_commit_sha(&args.root),
            }
        }
    };
    let commit_sha_before = {
        let flag = args.commit_sha_before.clone();
        if !flag.is_empty() {
            flag
        } else {
            std::env::var("GIT_COMMIT_SHA_BEFORE").unwrap_or_default()
        }
    };

    // ── Dry run ──────────────────────────────────────────────────────────────
    if args.dry_run {
        let mut files = dparse::scan_delphi_files(&root);
        if args.incremental && !changed_manifest.is_empty() {
            let manifest_set: HashSet<&String> = changed_manifest.iter().collect();
            files.retain(|file_path| {
                let rel = rel_posix(&root, std::path::Path::new(file_path));
                manifest_set.contains(&rel)
            });
            println!(
                "Dry run (incremental): {} Delphi files selected (manifest={})",
                files.len(),
                changed_manifest.len()
            );
        } else {
            println!("Dry run: {} Delphi files found", files.len());
        }
        return Ok(0);
    }

    // ── Store + pipeline ─────────────────────────────────────────────────────
    let store = open_store(args)?;
    run_build_call_graph(args, extra, &scope, &root, store, &parse_run_id, &commit_sha, &commit_sha_before, start)
}

fn load_manifest_paths_sorted(manifest: &str, root: &std::path::Path) -> Vec<String> {
    let mut items: Vec<String> = load_manifest_paths(manifest, root).into_iter().collect();
    items.sort();
    items
}

/// Key dedupe của calls_by_key Python: (caller_id, caller_file, call_line,
/// callee_raw, callee_name, call_arity) → callee_id.
type CallKey = (String, String, usize, String, String, usize);
type CallsByKey = HashMap<CallKey, Option<String>>;

#[allow(clippy::too_many_arguments)]
fn run_build_call_graph(
    args: &AnalyzerArgs,
    extra: &DelphiExtraArgs,
    scope: &Scope,
    root: &std::path::Path,
    store: Option<Box<dyn GraphStore>>,
    parse_run_id: &str,
    commit_sha: &str,
    commit_sha_before: &str,
    start: Instant,
) -> Result<i32, String> {
    let verbose = args.verbose;

    let all_scanned_files = dparse::scan_delphi_files(root);
    let all_rel_paths: Vec<String> = all_scanned_files
        .iter()
        .map(|path| rel_posix(root, std::path::Path::new(path)))
        .collect();
    let mut rel_to_abs: HashMap<String, String> = HashMap::new();
    for (abs, rel) in all_scanned_files.iter().zip(all_rel_paths.iter()) {
        rel_to_abs.insert(rel.clone(), abs.clone());
    }

    // Python: changed_set = {item.replace("\\", "/") for item in changed_files}
    // — changed_files là manifest đã load (canonicalize về rel-posix).
    let changed_set: HashSet<String> = load_manifest_paths_flag(args.changed_files_manifest.as_deref(), args.incremental, root);
    let deleted_set: HashSet<String> = load_manifest_paths_flag(args.deleted_files_manifest.as_deref(), args.incremental, root);

    // ── Uses index (toàn bộ file scan được — cả khi incremental) ────────────
    let (unit_name_by_file_all, uses_by_file_all) =
        resolve::collect_unit_and_uses_index(&all_scanned_files, root);
    let resolved_uses_by_file_all = resolve::resolve_uses_by_file(
        &uses_by_file_all,
        &unit_name_by_file_all,
        &all_scanned_files,
        root,
    );

    // ── Selection ────────────────────────────────────────────────────────────
    let incremental = args.incremental;
    let (selected_files, impacted_count) = if incremental {
        let changed_existing: HashSet<String> = changed_set
            .iter()
            .filter(|path| rel_to_abs.contains_key(*path))
            .cloned()
            .collect();
        let impacted_by_uses =
            resolve::expand_impacted_files_by_uses(&changed_existing, &resolved_uses_by_file_all);
        let selected_rel_paths: HashSet<String> =
            changed_existing.union(&impacted_by_uses).cloned().collect();
        let files: Vec<String> = all_rel_paths
            .iter()
            .filter(|path| selected_rel_paths.contains(*path))
            .map(|path| rel_to_abs[path].clone())
            .collect();
        // Python: max(len(selected) - len(changed_existing), 0)
        let impacted = files.len().saturating_sub(changed_existing.len());
        (files, impacted)
    } else {
        (all_scanned_files.clone(), 0usize)
    };
    if verbose {
        if incremental {
            println!(
                "[scan] incremental before={} after={} changed={} deleted={} selected={}/{} impacted_by_uses={}",
                if commit_sha_before.is_empty() { "unknown" } else { commit_sha_before },
                commit_sha,
                changed_set.len(),
                deleted_set.len(),
                selected_files.len(),
                all_scanned_files.len(),
                impacted_count
            );
        }
        println!("[scan] Found {} Delphi files under {}", selected_files.len(), root.display());
    }

    // ── Cleanup (changed ∪ deleted, sorted) ──────────────────────────────────
    let mut cleanup_targets: BTreeSet<String> = BTreeSet::new();
    if incremental {
        cleanup_targets.extend(changed_set.iter().cloned());
        cleanup_targets.extend(deleted_set.iter().cloned());
    }
    let cleanup_targets: Vec<String> = cleanup_targets.into_iter().collect();
    let mut store = store;
    if let Some(store) = store.as_mut()
        && incremental
        && !cleanup_targets.is_empty()
    {
        if verbose {
            println!(
                "[cleanup][graph] deleting graph data for {} files",
                cleanup_targets.len()
            );
        }
        let (deleted_nodes, deleted_unknown) =
            cleanup_graph_files(store.as_mut(), &scope.project_id, &cleanup_targets)
                .map_err(|e| e.to_string())?;
        if verbose {
            println!(
                "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
            );
        }
    }

    // ── Parse payloads + stats ───────────────────────────────────────────────
    let total_files = selected_files.len();
    let mut parse_error_file_count = 0usize;
    let mut parse_error_node_total = 0usize;
    let mut parse_error_examples: Vec<String> = Vec::new();
    let mut parse_error_details: Vec<Value> = Vec::new();
    let mut parser_available_file_count = 0usize;
    let mut parser_fallback_file_count = 0usize;
    let mut parser_language_counts: HashMap<String, usize> = HashMap::new();

    let mut function_defs: Vec<FunctionDef> = Vec::new();
    let mut calls_all: Vec<CallEdge> = Vec::new();
    let mut uses_by_file: HashMap<String, Vec<String>> = HashMap::new();
    let mut payloads: Vec<FilePayload> = Vec::new();

    for (index, file_path) in selected_files.iter().enumerate() {
        let index = index + 1;
        if verbose && (index == 1 || index % 50 == 0 || index == total_files) {
            println!("[parse] {index}/{total_files}: {file_path}");
        }
        let parsed = dparse::parse_delphi_file(std::path::Path::new(file_path), root)
            .map_err(|e| format!("failed to parse {file_path}: {e}"))?;
        let payload: FilePayload = parsed.into();
        let meta = &payload.parse_meta;
        if meta.parser_available {
            parser_available_file_count += 1;
        } else {
            parser_fallback_file_count += 1;
        }
        *parser_language_counts.entry(meta.parser_language.clone()).or_insert(0) += 1;
        if meta.has_error || meta.error_nodes > 0 {
            parse_error_file_count += 1;
            parse_error_node_total += meta.error_nodes;
            let file_rel = payload.file_def.file_path.clone();
            if parse_error_examples.len() < 10 {
                parse_error_examples.push(file_rel.clone());
            }
            parse_error_details.push(json!({
                "file_path": file_rel,
                "parser_language": meta.parser_language,
                "parser_available": meta.parser_available,
                "has_error": meta.has_error,
                "error_nodes": meta.error_nodes,
            }));
        }
        uses_by_file.insert(payload.file_def.file_path.clone(), payload.uses_units.clone());
        for func in &payload.functions {
            function_defs.push(func.clone());
        }
        calls_all.extend(payload.calls.iter().cloned());
        payloads.push(payload);
    }

    if verbose {
        if parser_fallback_file_count > 0 {
            println!(
                "[parse] tree-sitter unavailable for {}/{} files; used regex fallback",
                parser_fallback_file_count, total_files
            );
        }
        if parse_error_file_count > 0 {
            println!(
                "[parse] tree-sitter reported errors in {}/{} files ({} ERROR nodes)",
                parse_error_file_count, parser_available_file_count, parse_error_node_total
            );
            for path in &parse_error_examples {
                println!("  [parse][sample-error] {path}");
            }
        } else if parser_available_file_count > 0 {
            println!(
                "[parse] tree-sitter parse status: no error nodes detected in {}/{} parsed files",
                parser_available_file_count, total_files
            );
        } else {
            println!("[parse] tree-sitter parse status: parser unavailable; regex fallback only");
        }
    }

    if let Some(parse_errors_path) = &extra.parse_errors_path {
        if let Some(parent) = std::path::Path::new(parse_errors_path).parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let report = json!({
            "parse_run_id": parse_run_id,
            "commit_sha": commit_sha,
            "root": args.root,
            "total_files": total_files,
            "parser_available_file_count": parser_available_file_count,
            "parser_fallback_file_count": parser_fallback_file_count,
            "parser_languages": parser_language_counts,
            "error_file_count": parse_error_file_count,
            "error_node_total": parse_error_node_total,
            "files": parse_error_details,
        });
        let pretty = serde_json::to_string_pretty(&report).unwrap_or_default();
        std::fs::write(parse_errors_path, pretty + "\n").map_err(|e| e.to_string())?;
        if verbose {
            println!("[parse] wrote parse error report: {parse_errors_path}");
        }
    }

    // ── Uses closure + call resolution ───────────────────────────────────────
    let selected_rel_keys: Vec<String> = uses_by_file.keys().cloned().collect();
    let uses_closure_by_file =
        resolve::build_uses_closure_by_file(&selected_rel_keys, &resolved_uses_by_file_all);
    resolve::resolve_calls(&function_defs, &mut calls_all, &uses_closure_by_file);

    let call_stats_total = calls_all.len();
    let mut call_stats_resolved = 0usize;
    let mut call_stats_by_file: HashMap<String, (usize, usize)> = HashMap::new();
    let mut unresolved_rows: Vec<Map<String, Value>> = Vec::new();

    // calls_by_key — LAST occurrence thắng (dict comprehension Python).
    let mut calls_by_key: CallsByKey = HashMap::new();
    for call in &calls_all {
        calls_by_key.insert(
            (
                call.caller_id.clone(),
                call.caller_file.clone(),
                call.call_line,
                call.callee_raw.clone(),
                call.callee_name.clone(),
                call.call_arity,
            ),
            call.callee_id.clone(),
        );
    }

    let resolved_uses_by_file: HashMap<String, Vec<String>> = selected_rel_keys
        .iter()
        .map(|fp| (fp.clone(), resolved_uses_by_file_all.get(fp).cloned().unwrap_or_default()))
        .collect();

    let mut all_calls: Vec<Map<String, Value>> = Vec::new();
    if let Some(store) = store.take() {
        if verbose {
            println!("[graph] Collecting nodes and relations for batch write...");
        }

        let project_row: Map<String, Value> = {
            let mut row = Map::new();
            row.insert("id".into(), json!(scope.project_id));
            row.insert("project_id".into(), json!(scope.project_id));
            row.insert("project_name".into(), json!(scope.project_name));
            row.insert("root".into(), json!(args.root));
            row.insert("repo".into(), json!(scope.repo));
            row.insert("language".into(), json!(scope.language));
            row
        };

        let mut all_files_nodes: Vec<Map<String, Value>> = Vec::new();
        let mut all_namespaces: Vec<Map<String, Value>> = Vec::new();
        let mut all_types: Vec<Map<String, Value>> = Vec::new();
        let mut all_functions: Vec<Map<String, Value>> = Vec::new();
        let mut all_fields: Vec<Map<String, Value>> = Vec::new();
        let mut all_relations: Vec<Map<String, Value>> = Vec::new();

        const ALLOWED_REL_TYPES: [&str; 6] =
            ["CONTAINS", "DECLARES", "EXTENDS", "USES_TYPE", "POINTER_TO", "DEPENDS_ON"];

        for payload in &payloads {
            let file_def = &payload.file_def;
            let file_id = file_def.file_path.clone();

            all_files_nodes.push(file_row(file_def, scope));
            all_relations.push(relation_row(
                &scope.project_id,
                "Project",
                &file_id,
                "File",
                "CONTAINS",
                Map::new(),
            ));

            if let Some(deps) = resolved_uses_by_file.get(&file_id) {
                for dep_file in deps {
                    let mut props = Map::new();
                    props.insert("kind".into(), json!("uses"));
                    all_relations.push(relation_row(
                        &file_id,
                        "File",
                        dep_file,
                        "File",
                        "DEPENDS_ON",
                        props,
                    ));
                }
            }

            for ns in &payload.namespaces {
                all_namespaces.push(namespace_row(ns, scope));
                all_relations.push(relation_row(
                    &file_id,
                    "File",
                    &ns.symbol_id,
                    "Namespace",
                    "CONTAINS",
                    Map::new(),
                ));
            }

            for type_def in &payload.types {
                all_types.push(type_row(type_def, scope));
                all_relations.push(relation_row(
                    &file_id,
                    "File",
                    &type_def.symbol_id,
                    "Type",
                    "CONTAINS",
                    Map::new(),
                ));
            }

            for func in &payload.functions {
                all_functions.push(function_row(func, scope));
                all_relations.push(relation_row(
                    &file_id,
                    "File",
                    &func.symbol_id,
                    "Function",
                    "CONTAINS",
                    Map::new(),
                ));
            }

            for field in &payload.fields {
                all_fields.push(field_row(field, scope));
                all_relations.push(relation_row(
                    &file_id,
                    "File",
                    &field.symbol_id,
                    "Field",
                    "CONTAINS",
                    Map::new(),
                ));
            }

            for rel in &payload.relations {
                if !ALLOWED_REL_TYPES.contains(&rel.rel_type.as_str()) {
                    continue;
                }
                all_relations.push(relation_row(
                    &rel.source_id,
                    &rel.source_label,
                    &rel.target_id,
                    &rel.target_label,
                    &rel.rel_type,
                    rel.properties.clone(),
                ));
            }

            for call in &payload.calls {
                let key = (
                    call.caller_id.clone(),
                    call.caller_file.clone(),
                    call.call_line,
                    call.callee_raw.clone(),
                    call.callee_name.clone(),
                    call.call_arity,
                );
                let resolved = calls_by_key.get(&key).cloned().flatten();
                let entry = call_stats_by_file.entry(call.caller_file.clone()).or_insert((0, 0));
                match resolved {
                    Some(callee_id) if !callee_id.is_empty() => {
                        call_stats_resolved += 1;
                        entry.0 += 1;
                        entry.1 += 1;
                        let mut row = Map::new();
                        row.insert("caller_id".into(), json!(call.caller_id));
                        row.insert("callee_id".into(), json!(callee_id));
                        all_calls.push(row);
                    }
                    _ => {
                        entry.0 += 1;
                        let mut row = Map::new();
                        row.insert("caller_id".into(), json!(call.caller_id));
                        row.insert("caller_scope".into(), json!(call.caller_scope.clone().unwrap_or_default()));
                        row.insert("file_path".into(), json!(call.caller_file));
                        row.insert("line".into(), json!(call.call_line));
                        row.insert("callee_name".into(), json!(if call.callee_raw.is_empty() { call.callee_name.clone() } else { call.callee_raw.clone() }));
                        row.insert("call_arity".into(), json!(call.call_arity));
                        row.insert("parse_run_id".into(), json!(parse_run_id));
                        row.insert("commit_sha".into(), json!(commit_sha));
                        unresolved_rows.push(row);
                    }
                }
            }
        }

        let mut writer = LanguageCodeWriter::new(
            store,
            extra.neo4j_db.clone().or_else(|| args.neo4j_db.clone()),
            extra.neo4j_batch_size,
            verbose,
        );
        let projects_rows = vec![project_row];
        let payload = WriteAllPayload {
            projects: &projects_rows,
            packages: &[],
            namespaces: &all_namespaces,
            files: &all_files_nodes,
            classes: &[],
            types: &all_types,
            function_types: &[],
            functions: &all_functions,
            fields: &all_fields,
            aliases: &[],
            templates: &[],
            relations: &all_relations,
            calls: &[],
            calls_with_site: &[],
            properties: &[],
            events: &[],
            interfaces: &[],
            enums: &[],
            constants: &[],
            variables: &[],
            navigators: &[],
            has_routes: &[],
            param_lists: &[],
            workflows: &[],
            workflow_steps: &[],
            call_evidence_sites: &[],
            call_evidence_observations: &[],
            build_configurations: &[],
            semantic_coverage: &[],
            proc_function_joins: &[],
            proc_host_declarations: &[],
            use_full_writers: true,
            files_variant: FilesVariant::Default,
        };
        writer
            .write_all(&payload)
            .map_err(|error| format!("Delphi graph persistence failed: {error}"))?;

        if !all_calls.is_empty() {
            let original_batch_size = writer.batch_size;
            writer.batch_size = extra.neo4j_calls_batch_size.max(1);
            let site_rows: Vec<Map<String, Value>> = all_calls
                .iter()
                .map(|row| {
                    let caller_id = row["caller_id"].as_str().unwrap_or_default();
                    let callee_id = row["callee_id"].as_str().unwrap_or_default();
                    let mut props = Map::new();
                    props.insert("parse_run_id".into(), json!(parse_run_id));
                    props.insert("commit_sha".into(), json!(commit_sha));
                    props.insert("project_id".into(), json!(scope.project_id));
                    let mut site = Map::new();
                    site.insert("caller_id".into(), json!(caller_id));
                    site.insert("callee_id".into(), json!(callee_id));
                    site.insert(
                        "site_id".into(),
                        json!(format!(
                            "{}:{}",
                            parse_run_id,
                            stable_point_id(&format!("{caller_id}->{callee_id}"))
                        )),
                    );
                    site.insert("props".into(), Value::Object(props));
                    site
                })
                .collect();
            let result = writer.write_calls_with_site(&site_rows);
            writer.batch_size = original_batch_size;
            result.map_err(|error| format!("Delphi graph persistence failed: {error}"))?;
        }

        if verbose {
            let unresolved = call_stats_total - call_stats_resolved;
            let ratio = if call_stats_total > 0 {
                call_stats_resolved as f64 / call_stats_total as f64
            } else {
                0.0
            };
            println!(
                "[calls] resolved {} / {} ({:.1}%), unresolved {}",
                call_stats_resolved,
                call_stats_total,
                ratio * 100.0,
                unresolved
            );
        }
    } else {
        // Dry parse mode không có graph writer.
        for call in &calls_all {
            let entry = call_stats_by_file.entry(call.caller_file.clone()).or_insert((0, 0));
            match &call.callee_id {
                Some(callee_id) if !callee_id.is_empty() => {
                    call_stats_resolved += 1;
                    entry.0 += 1;
                    entry.1 += 1;
                }
                _ => {
                    entry.0 += 1;
                    let mut row = Map::new();
                    row.insert("caller_id".into(), json!(call.caller_id));
                    row.insert("caller_scope".into(), json!(call.caller_scope.clone().unwrap_or_default()));
                    row.insert("file_path".into(), json!(call.caller_file));
                    row.insert("line".into(), json!(call.call_line));
                    row.insert("callee_name".into(), json!(if call.callee_raw.is_empty() { call.callee_name.clone() } else { call.callee_raw.clone() }));
                    row.insert("call_arity".into(), json!(call.call_arity));
                    row.insert("parse_run_id".into(), json!(parse_run_id));
                    row.insert("commit_sha".into(), json!(commit_sha));
                    unresolved_rows.push(row);
                }
            }
        }
    }

    if let Some(unresolved_calls_path) = &extra.unresolved_calls_path {
        if let Some(parent) = std::path::Path::new(unresolved_calls_path).parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let mut body = String::new();
        for row in &unresolved_rows {
            body.push_str(&serde_json::to_string(row).unwrap_or_default());
            body.push('\n');
        }
        std::fs::write(unresolved_calls_path, body).map_err(|e| e.to_string())?;
        if verbose {
            println!("[calls] wrote unresolved calls: {unresolved_calls_path}");
        }
    }

    if let Some(call_stats_path) = &extra.call_stats_path {
        if let Some(parent) = std::path::Path::new(call_stats_path).parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let mut by_file: Vec<(String, (usize, usize))> = call_stats_by_file.iter().map(|(k, v)| (k.clone(), *v)).collect();
        by_file.sort();
        let ratio = if call_stats_total > 0 {
            call_stats_resolved as f64 / call_stats_total as f64
        } else {
            0.0
        };
        let payload = json!({
            "parse_run_id": parse_run_id,
            "commit_sha": commit_sha,
            "total_calls": call_stats_total,
            "resolved_calls": call_stats_resolved,
            "unresolved_calls": call_stats_total - call_stats_resolved,
            "resolved_ratio": ratio,
            "by_file": by_file
                .iter()
                .map(|(fp, (total, resolved))| json!({
                    "file_path": fp,
                    "total": total,
                    "resolved": resolved,
                    "unresolved": total - resolved,
                }))
                .collect::<Vec<_>>(),
        });
        let pretty = serde_json::to_string_pretty(&payload).unwrap_or_default();
        std::fs::write(call_stats_path, pretty + "\n").map_err(|e| e.to_string())?;
        if verbose {
            println!("[calls] wrote call stats: {call_stats_path}");
        }
    }

    let _sr_fn: usize = payloads.iter().map(|p| p.functions.len()).sum();
    // Python: `_sr_cls = sum(len(p.get("classes") or []) ...)` — payload Delphi
    // không có key "classes" ⇒ luôn 0.
    let _sr_cls = 0usize;
    println!(
        "[SCAN_RESULT] parser={} files={} functions={} classes={}",
        scope.language,
        payloads.len(),
        _sr_fn,
        _sr_cls
    );
    if verbose {
        println!("[done] Total time: {:.2}s", start.elapsed().as_secs_f64());
    }
    Ok(0)
}

// ── Row builders — khớp dict Python từng field ──────────────────────────────

fn with_scope(mut row: Map<String, Value>, scope: &Scope) -> Map<String, Value> {
    row.insert("project_id".into(), json!(scope.project_id));
    row.insert("project_name".into(), json!(scope.project_name));
    row.insert("language".into(), json!(scope.language));
    row.insert("repo".into(), json!(scope.repo));
    row.insert("build_system".into(), json!(scope.build_system));
    row
}

fn file_row(file_def: &FileDef, scope: &Scope) -> Map<String, Value> {
    let mut row = Map::new();
    let file_id = &file_def.file_path;
    row.insert("id".into(), json!(file_id));
    row.insert("path".into(), json!(file_id));
    row.insert("file_path".into(), json!(file_id));
    row.insert("start_line".into(), json!(file_def.start_line));
    row.insert("end_line".into(), json!(file_def.end_line));
    row.insert("code".into(), json!(file_def.code));
    row.insert("comment".into(), json!(file_def.comment));
    row.insert("summary".into(), json!(file_def.summary));
    row.insert("note".into(), json!(file_def.note));
    with_scope(row, scope)
}

fn namespace_row(ns: &dparse::NamespaceDef, scope: &Scope) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("id".into(), json!(ns.symbol_id));
    row.insert("name".into(), json!(ns.name));
    row.insert("qualified_name".into(), json!(ns.qualified_name));
    row.insert("file_path".into(), json!(ns.file_path));
    row.insert("start_line".into(), json!(ns.start_line));
    row.insert("end_line".into(), json!(ns.end_line));
    row.insert("code".into(), json!(ns.code));
    row.insert("comment".into(), json!(ns.comment));
    row.insert("summary".into(), json!(ns.summary));
    row.insert("note".into(), json!(ns.note));
    with_scope(row, scope)
}

fn type_row(type_def: &TypeDef, scope: &Scope) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("id".into(), json!(type_def.symbol_id));
    row.insert("name".into(), json!(type_def.name));
    row.insert("qualified_name".into(), json!(type_def.qualified_name));
    row.insert("kind".into(), json!(type_def.kind));
    row.insert("file_path".into(), json!(type_def.file_path));
    row.insert("start_line".into(), json!(type_def.start_line));
    row.insert("end_line".into(), json!(type_def.end_line));
    row.insert("code".into(), json!(type_def.code));
    row.insert("comment".into(), json!(type_def.comment));
    row.insert("summary".into(), json!(type_def.summary));
    row.insert("note".into(), json!(type_def.note));
    with_scope(row, scope)
}

fn function_row(func: &FunctionDef, scope: &Scope) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("id".into(), json!(func.symbol_id));
    row.insert("name".into(), json!(func.name));
    row.insert("qualified_name".into(), json!(func.qualified_name));
    row.insert("kind".into(), json!(func.kind));
    row.insert("scope_name".into(), json!(func.scope_name));
    row.insert("class_name".into(), Value::Null);
    row.insert("package_name".into(), Value::Null);
    row.insert("file_path".into(), json!(func.file_path));
    row.insert("start_line".into(), json!(func.start_line));
    row.insert("end_line".into(), json!(func.end_line));
    row.insert("arity".into(), json!(func.arity));
    row.insert("code".into(), json!(func.code));
    row.insert("comment".into(), json!(func.comment));
    row.insert("summary".into(), json!(func.summary));
    row.insert("note".into(), json!(func.note));
    row.insert("exported".into(), json!(false));
    with_scope(row, scope)
}

fn field_row(field: &FieldDef, scope: &Scope) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("id".into(), json!(field.symbol_id));
    row.insert("name".into(), json!(field.name));
    row.insert("qualified_name".into(), json!(field.qualified_name));
    row.insert("scope_name".into(), json!(field.scope_name));
    row.insert("type_signature".into(), json!(field.type_signature));
    row.insert("file_path".into(), json!(field.file_path));
    row.insert("start_line".into(), json!(field.start_line));
    row.insert("end_line".into(), json!(field.end_line));
    row.insert("code".into(), json!(field.code));
    with_scope(row, scope)
}

fn relation_row(
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    properties: Map<String, Value>,
) -> Map<String, Value> {
    let mut row = Map::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_id".into(), json!(target_id));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("properties".into(), Value::Object(properties));
    row
}

// ── helpers tạm ──────────────────────────────────────────────────────────────

fn load_manifest_paths_flag(
    manifest: Option<&str>,
    incremental: bool,
    root: &std::path::Path,
) -> HashSet<String> {
    if !incremental {
        return HashSet::new();
    }
    match manifest {
        Some(path) => load_manifest_paths(path, root).into_iter().collect(),
        None => HashSet::new(),
    }
}

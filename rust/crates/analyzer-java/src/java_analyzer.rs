//! `JavaAnalyzer` — pipeline `build_call_graph` của java_analyzer.py:
//! scan → import-impact selection → cleanup → parse → index → resolve →
//! `LanguageCodeWriter::write_all` → `[SCAN_RESULT]`.
//!
//! Qdrant/embedding và parse-cache KHÔNG port (embedding tách khỏi analyzer,
//! key decision #3; parse cache trong suốt với graph) — cờ nhận và bỏ qua.
//! Message scan là plane Python-side; flag nhận, skip có kiểm soát.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::PathBuf;
use std::time::Instant;

use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::cli::{abs_root, AnalyzerArgs};
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::rel_posix;
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};

use crate::javaparse::{parse_java_file, FilePayload};
use crate::javascan::{
    collect_java_import_graph, expand_impacted_files_by_imports, scan_java_files,
};
use crate::resolve::{assemble_graph, ClassIndex, FunctionIndex};

/// Cờ java-specific ngoài contract chung — mirror `parse_args` của
/// java_analyzer.py; phần không áp dụng cho backend Rust nhận và bỏ qua.
#[derive(Debug, Clone, Default, clap::Args)]
pub struct JavaExtraArgs {
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow

    #[arg(long, hide = true)]
    pub neo4j_batch_size: Option<i64>,
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
}

fn normalize_rel(item: &str) -> String {
    item.replace('\\', "/")
}

/// Chạy pipeline — trả exit code (0 OK, 2 root not found).
pub fn execute(args: &AnalyzerArgs, extra: &JavaExtraArgs) -> Result<i32, String> {
    let root = abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return Ok(2);
    }
    let verbose = args.verbose;

    let mut store = if args.graph_writes_disabled() {
        None
    } else {
        Some(args.open_store().map_err(|e| e.to_string())?)
    };

    // ── Manifests (incremental) ─────────────────────────────────────────────
    let mut changed_files: BTreeSet<String> = BTreeSet::new();
    let mut deleted_files: BTreeSet<String> = BTreeSet::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            changed_files = load_manifest_paths(manifest, &root);
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            deleted_files = load_manifest_paths(manifest, &root);
        }
        if verbose {
            println!(
                "[diff] incremental manifests changed={} deleted={}",
                changed_files.len(),
                deleted_files.len()
            );
        }
    }

    // ── Dry run ─────────────────────────────────────────────────────────────
    if args.dry_run {
        let mut files = scan_java_files(&root);
        if args.incremental && !changed_files.is_empty() {
            files.retain(|path| changed_files.contains(&rel_posix(&root, path)));
            println!(
                "Dry run (incremental): {} Java files selected (manifest={})",
                files.len(),
                changed_files.len()
            );
        } else {
            println!("Dry run: {} Java files found", files.len());
        }
        return Ok(0);
    }

    // ── Project scope (khớp `main` Python) ──────────────────────────────────
    let project_id = non_empty(args.project_id.clone())
        .unwrap_or_else(|| basename_of(&root));
    let project_name = non_empty(args.project_name.clone()).unwrap_or_else(|| project_id.clone());
    let language = non_empty(args.language.clone()).unwrap_or_else(|| "java".to_string());
    let repo = non_empty(args.repo.clone()).unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = args.build_system.clone().unwrap_or_default();
    let commit_sha = args.commit_sha_after.clone();
    let commit_sha_before = args.commit_sha_before.clone();

    let start_time = Instant::now();
    // ── Scan ────────────────────────────────────────────────────────────────
    let all_scanned_files = scan_java_files(&root);
    let all_rel_paths: Vec<String> = all_scanned_files
        .iter()
        .map(|path| rel_posix(&root, path))
        .collect();
    let rel_to_abs: HashMap<String, PathBuf> = all_scanned_files
        .iter()
        .cloned()
        .zip(all_rel_paths.iter().cloned())
        .map(|(path, rel)| (rel, path))
        .collect();
    let changed_set: HashSet<String> = changed_files
        .iter()
        .map(|item| normalize_rel(item))
        .filter(|item| !item.is_empty())
        .collect();
    let deleted_set: HashSet<String> = deleted_files
        .iter()
        .map(|item| normalize_rel(item))
        .filter(|item| !item.is_empty())
        .collect();

    let incremental = args.incremental;
    let selected_rel_paths: BTreeSet<String>;
    let java_files: Vec<PathBuf>;
    let impacted_count;
    if incremental {
        let changed_existing: HashSet<String> = changed_set
            .iter()
            .filter(|path| rel_to_abs.contains_key(*path))
            .cloned()
            .collect();
        let deps_by_file = collect_java_import_graph(&all_scanned_files, &root);
        let impacted = expand_impacted_files_by_imports(&changed_existing, &deps_by_file);
        let mut selected = BTreeSet::new();
        selected.extend(changed_existing.iter().cloned());
        selected.extend(impacted.iter().cloned());
        selected_rel_paths = selected;
        java_files = all_rel_paths
            .iter()
            .filter(|path| selected_rel_paths.contains(*path))
            .map(|path| rel_to_abs[path].clone())
            .collect();
        impacted_count = impacted.len();
    } else {
        selected_rel_paths = all_rel_paths.iter().cloned().collect();
        java_files = all_scanned_files.clone();
        impacted_count = 0;
    }
    if verbose {
        if incremental {
            println!(
                "[scan] incremental before={} after={} changed={} deleted={} selected={}/{} impacted_by_imports={}",
                if commit_sha_before.is_empty() { "unknown" } else { &commit_sha_before },
                if commit_sha.is_empty() { "unknown" } else { &commit_sha },
                changed_set.len(),
                deleted_set.len(),
                java_files.len(),
                all_scanned_files.len(),
                impacted_count,
            );
        }
        println!("[scan] Found {} Java files under {}", java_files.len(), args.root);
    }
    let total_files = java_files.len();

    // ── Cleanup (changed ∪ deleted) ─────────────────────────────────────────
    let cleanup_targets: Vec<String> = changed_set
        .union(&deleted_set)
        .cloned()
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect();
    if incremental
        && !cleanup_targets.is_empty()
        && let Some(store) = store.as_deref_mut()
    {
        if verbose {
            println!(
                "[cleanup][graph] deleting graph data for {} files",
                cleanup_targets.len()
            );
        }
        let (deleted_nodes, deleted_unknown) =
            cleanup_graph_files(store, &project_id, &cleanup_targets).map_err(|e| e.to_string())?;
        if verbose {
            println!(
                "[cleanup][graph] deleted_nodes={} deleted_unknown_functions={}",
                deleted_nodes, deleted_unknown
            );
        }
    }

    // ── Parse selected ──────────────────────────────────────────────────────
    let mut selected_payloads: Vec<FilePayload> = Vec::with_capacity(java_files.len());
    let mut selected_payload_by_rel: HashMap<String, FilePayload> = HashMap::new();
    let mut parse_error_file_count = 0usize;
    let mut parse_error_node_total = 0i64;
    let mut parse_error_examples: Vec<String> = Vec::new();
    for (index, file_path) in java_files.iter().enumerate() {
        if verbose && (index == 0 || (index + 1) % 50 == 0 || index + 1 == total_files) {
            println!("[parse] {}/{}: {}", index + 1, total_files, file_path.display());
        }
        let payload = parse_java_file(file_path, &root)?;
        let rel_path = payload
            .file_def
            .as_ref()
            .map(|f| f.file_path.clone())
            .unwrap_or_default();
        if !rel_path.is_empty() {
            selected_payload_by_rel.insert(rel_path.clone(), payload.clone());
        }
        if payload.has_error || payload.error_nodes > 0 {
            parse_error_file_count += 1;
            parse_error_node_total += payload.error_nodes;
            if !rel_path.is_empty() && parse_error_examples.len() < 10 {
                parse_error_examples.push(rel_path);
            }
        }
        selected_payloads.push(payload);
    }
    if verbose {
        if parse_error_file_count > 0 {
            println!(
                "[parse] tree-sitter reported errors in {}/{} files ({} ERROR nodes)",
                parse_error_file_count, total_files, parse_error_node_total
            );
            for path in &parse_error_examples {
                println!("  [parse][sample-error] {path}");
            }
        } else {
            println!("[parse] tree-sitter parse status: no error nodes detected");
        }
    }

    // ── Index payloads (incremental: TOÀN BỘ file; full: selected) ──────────
    let index_payloads: Vec<FilePayload> = if incremental && !selected_rel_paths.is_empty() {
        let mut payloads = Vec::with_capacity(all_rel_paths.len());
        for (index, rel_path) in all_rel_paths.iter().enumerate() {
            if let Some(cached) = selected_payload_by_rel.get(rel_path) {
                payloads.push(cached.clone());
                continue;
            }
            let abs_path = rel_to_abs
                .get(rel_path)
                .ok_or_else(|| format!("missing rel path: {rel_path}"))?;
            if verbose && (index == 0 || (index + 1) % 200 == 0 || index + 1 == all_rel_paths.len())
            {
                println!("[index] {}/{}: {}", index + 1, all_rel_paths.len(), rel_path);
            }
            payloads.push(parse_java_file(abs_path, &root)?);
        }
        payloads
    } else if incremental {
        Vec::new()
    } else {
        selected_payloads.clone()
    };

    // ── Indexes ─────────────────────────────────────────────────────────────
    let index_refs: Vec<&FilePayload> = index_payloads.iter().collect();
    let class_index = ClassIndex::build(&index_refs);
    let function_index = FunctionIndex::build(&index_refs);

    // ── Graph write ─────────────────────────────────────────────────────────
    if let Some(store) = store.take() {
        if verbose {
            println!("[graph] Writing nodes and relations (streaming)...");
        }
        let selected_refs: Vec<&FilePayload> = selected_payloads.iter().collect();
        // assemble_graph mượn refs — dùng FunctionIndex đã build từ
        // index_payloads (khớp resolve_callee_id của Python).
        let graph = assemble_graph(
            &selected_refs,
            &index_refs,
            &class_index,
            &function_index,
            &project_id,
            &project_name,
            &language,
            &repo,
            &build_system,
            &args.root,
        );
        let batch_size = extra.neo4j_batch_size.unwrap_or(1000).max(1) as usize;
        let mut writer = LanguageCodeWriter::new(store, args.neo4j_db.clone(), batch_size, verbose);
        let counts = writer.write_all(&WriteAllPayload {
            projects: &graph.projects,
            packages: &graph.packages,
            namespaces: &graph.namespaces,
            files: &graph.files,
            classes: &graph.classes,
            types: &[],
            function_types: &graph.function_types,
            functions: &graph.functions,
            fields: &[],
            aliases: &[],
            templates: &[],
            relations: &graph.relations,
            calls: &graph.calls,
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
            files_variant: FilesVariant::WithPackage,
        });
        match counts {
            Ok(_) => {
                if verbose {
                    println!("[graph] Write complete");
                }
            }
            Err(error) => return Err(format!("[graph] write failed: {error}")),
        }
    }

    // ── [SCAN_RESULT] (luôn in, flush như Python) ───────────────────────────
    let sr_fn: usize = selected_payloads.iter().map(|p| p.functions.len()).sum();
    let sr_cls: usize = selected_payloads.iter().map(|p| p.classes.len()).sum();
    println!(
        "[SCAN_RESULT] parser={language} files={} functions={sr_fn} classes={sr_cls}",
        selected_payloads.len()
    );
    if verbose {
        println!("[done] Total time: {:.2}s", start_time.elapsed().as_secs_f64());
    }
    Ok(0)
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.filter(|v| !v.is_empty())
}

fn basename_of(root: &std::path::Path) -> String {
    root.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string_lossy().to_string())
}

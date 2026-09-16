//! `PhpAnalyzer` — impl `Analyzer` của khung; toàn bộ pipeline của
//! `php_analyzer.py::build_call_graph`: scan → manifest selection → cleanup →
//! parse → function index → call resolution → write_all (`use_full_writers`,
//! `files_variant="with_jsx"`) → `[SCAN_RESULT]`.
//!
//! PHP KHÔNG có semantic engine phía Python (không enrichment) — các bước
//! enrich/intent của template analyzer-python được cố ý bỏ qua.

use std::time::Instant;

use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::scan::rel_posix;
use cortex_analyzer_framework::summary::{scan_result_line, summary_json, write_summary};
use cortex_analyzer_framework::traits::{Analyzer, AnalyzerContext, AnalyzerResult};
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};

use crate::phpparse;
use crate::resolve::{assemble_graph, FunctionIndex};

pub struct PhpAnalyzer {
    _private: (),
}

impl Default for PhpAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl PhpAnalyzer {
    pub fn new() -> Self {
        Self { _private: () }
    }

    /// Entry dùng bởi main — dựng context rồi chạy qua trait `Analyzer`.
    pub fn execute(&mut self, root: &std::path::Path, args: &AnalyzerArgs) -> Result<i32, String> {
        let mut ctx = AnalyzerContext::from_args(args)?;
        ctx.root = root.to_path_buf();
        let result = self.run(&mut ctx)?;
        if let (Some(path), Some(summary)) = (&args.summary_path, result.summary_json) {
            write_summary(path, &summary).map_err(|e| format!("[summary] write failed: {e}"))?;
        }
        Ok(0)
    }
}

impl Analyzer for PhpAnalyzer {
    fn language(&self) -> &'static str {
        "php"
    }

    fn detect(&self, root: &std::path::Path) -> bool {
        !phpparse::scan_php_files(root).is_empty()
    }

    fn run(&self, ctx: &mut AnalyzerContext<'_>) -> Result<AnalyzerResult, String> {
        let args = ctx.args;
        let start = Instant::now();
        let verbose = ctx.verbose;
        let project_id = ctx.project_id.clone();
        // Python: `language = args.language or "php"`.
        let language = if ctx.language.is_empty() {
            "php".to_string()
        } else {
            ctx.language.clone()
        };
        let repo = ctx.repo.clone();

        // ── Scan + manifest selection ───────────────────────────────────────
        let all_scanned = phpparse::scan_php_files(&ctx.root);
        let selected: Vec<_> = if args.incremental {
            all_scanned
                .iter()
                .filter(|path| ctx.changed_files.contains(&rel_posix(&ctx.root, path)))
                .cloned()
                .collect()
        } else {
            all_scanned.clone()
        };
        if verbose {
            if args.incremental {
                println!(
                    "[scan] incremental before={} after={} changed={} deleted={} selected={}/{}",
                    if args.commit_sha_before.is_empty() {
                        "unknown"
                    } else {
                        &args.commit_sha_before
                    },
                    if args.commit_sha_after.is_empty() {
                        "unknown"
                    } else {
                        &args.commit_sha_after
                    },
                    ctx.changed_files.len(),
                    ctx.deleted_files.len(),
                    selected.len(),
                    all_scanned.len(),
                );
            }
            println!(
                "[scan] Found {} PHP files under {}",
                selected.len(),
                ctx.root.display()
            );
        }
        let total_files = selected.len();

        // ── Incremental cleanup (changed ∪ deleted) ─────────────────────────
        // Khớp `cleanup_neo4j_for_files`: in 2 dòng verbose, prune
        // UnknownFunction mồ côi.
        if args.incremental
            && (!ctx.changed_files.is_empty() || !ctx.deleted_files.is_empty())
            && ctx.store.is_some()
        {
            let store = ctx.store.as_deref_mut().expect("store checked above");
            let mut targets: Vec<String> = ctx
                .changed_files
                .union(&ctx.deleted_files)
                .cloned()
                .collect();
            targets.sort();
            if verbose {
                println!(
                    "[cleanup][graph] deleting graph data for {} files",
                    targets.len()
                );
            }
            let (deleted_nodes, deleted_unknown) = cleanup_graph_files(store, &project_id, &targets)
                .map_err(|e| e.to_string())?;
            if verbose {
                println!(
                    "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
                );
            }
        } else if verbose
            && args.incremental
            && !ctx.changed_files.is_empty()
            && ctx.store.is_none()
        {
            // Graphless mode (embedding pass chạy graph-disabled) — cleanup
            // graph là no-op; file deletions do embedding artifact xử lý.
            println!(
                "[cleanup][graph] skipped (graphless mode): {} file targets",
                ctx.changed_files.len() + ctx.deleted_files.len()
            );
        }

        // ── Parse ───────────────────────────────────────────────────────────
        let mut payloads: Vec<phpparse::FilePayload> = Vec::with_capacity(selected.len());
        for (index, path) in selected.iter().enumerate() {
            let ordinal = index + 1;
            if verbose && (ordinal == 1 || ordinal % 50 == 0 || ordinal == total_files) {
                println!("[parse] {ordinal}/{total_files}: {}", path.display());
            }
            payloads.push(phpparse::parse_php_file(path, &ctx.root)?);
        }

        // ── Function index + call resolution + rows ─────────────────────────
        let function_index = FunctionIndex::build(&payloads);

        // ── Graph write (skip khi graphless) ────────────────────────────────
        // Python: `_sr_fn = len(all_functions) if 'all_functions' in vars()
        // else 0` — graphless thì counts = 0.
        let (function_total, class_total) = if let Some(store) = ctx.store.take() {
            let mut writer = LanguageCodeWriter::new(
                store,
                ctx.args.neo4j_db.clone(),
                1000,
                verbose,
            );
            if verbose {
                println!("[graph] Writing nodes and relations (streaming)...");
            }
            let graph = assemble_graph(
                &payloads,
                &function_index,
                &project_id,
                &ctx.project_name,
                &language,
                &repo,
                &ctx.build_system,
                &ctx.root.to_string_lossy(),
            );
            writer
                .write_all(&WriteAllPayload {
                    projects: &graph.projects,
                    packages: &[],
                    namespaces: &graph.namespaces,
                    files: &graph.files,
                    classes: &[],
                    types: &graph.types,
                    function_types: &[],
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
                    files_variant: FilesVariant::WithJsx,
                })
                .map_err(|error| format!("[graph] write failed: {error}"))?;
            if verbose {
                println!("[graph] Write complete");
            }
            // Phase-02: capture embedding categories for orchestrator
            // (plan `260916-1432-legacy-17-vector-emit`). Emission deferred.
            let _embedding_categories: Vec<(String, Vec<serde_json::Value>)> = Vec::new();
            (graph.functions.len(), graph.types.len())
        } else {
            (0, 0)
        };

        println!(
            "{}",
            scan_result_line(&language, total_files, function_total, class_total)
        );

        if args.message_scan_enabled() && verbose {
            // Python plane — Rust backend skip có kiểm soát.
            println!("[message] message scan là plane Python; Rust backend skip (phase 05 php)");
        }
        if verbose {
            println!("[done] Total time: {:.2}s", start.elapsed().as_secs_f64());
        }

        let mut result = AnalyzerResult {
            files_scanned: total_files,
            functions: function_total,
            classes: class_total,
            relations: 0,
            calls_resolved: 0,
            written: Default::default(),
            duration_seconds: start.elapsed().as_secs_f64(),
            summary_json: None,
        };
        result.summary_json = Some(summary_json(
            &result,
            &project_id,
            &ctx.project_name,
            &language,
            &repo,
            args.incremental,
            &args.commit_sha_before,
            &args.commit_sha_after,
        ));
        Ok(result)
    }
}

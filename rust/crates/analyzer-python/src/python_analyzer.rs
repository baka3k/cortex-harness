//! `PythonAnalyzer` — impl `Analyzer` của khung; toàn bộ pipeline của
//! `build_call_graph` Python: scan → manifest selection → cleanup → parse →
//! semantic enrich → call resolution → write_all → `[SCAN_RESULT]` + summary.

use std::time::Instant;

use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::semantic::{
    build_usage_index, FunctionRef, SemanticInferenceEngine, UsageCall,
};
use cortex_analyzer_framework::summary::{scan_result_line, summary_json, write_summary};
use cortex_analyzer_framework::scan::rel_posix;
use cortex_analyzer_framework::traits::{Analyzer, AnalyzerContext, AnalyzerResult};
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};

use crate::pyparse;
use crate::resolve::FunctionIndex;

pub struct PythonAnalyzer {
    _private: (),
}

impl Default for PythonAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

impl PythonAnalyzer {
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

impl Analyzer for PythonAnalyzer {
    fn language(&self) -> &'static str {
        "python"
    }

    fn detect(&self, root: &std::path::Path) -> bool {
        !cortex_analyzer_framework::scan::scan_files(root, &[".py", ".pyi"]).is_empty()
    }

    fn run(&self, ctx: &mut AnalyzerContext<'_>) -> Result<AnalyzerResult, String> {
        let args = ctx.args;
        let start = Instant::now();
        let verbose = ctx.verbose;
        let project_id = ctx.project_id.clone();
        let language = if ctx.language.is_empty() {
            "python".to_string()
        } else {
            ctx.language.clone()
        };
        let repo = ctx.repo.clone();

        // ── Scan + manifest selection ───────────────────────────────────────
        let all_scanned = cortex_analyzer_framework::scan::scan_files(&ctx.root, &[".py", ".pyi"]);
        let selected: Vec<_> = if args.incremental {
            all_scanned
                .iter()
                .filter(|path| ctx.changed_files.contains(&rel_of(&ctx.root, path)))
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
                "[scan] Found {} Python files under {}",
                selected.len(),
                ctx.root.display()
            );
        }

        // ── Incremental cleanup (changed ∪ deleted) ─────────────────────────
        if args.incremental
            && (!ctx.changed_files.is_empty() || !ctx.deleted_files.is_empty())
            && ctx.store.is_some()
        {
            // Python: cleanup chỉ chạy `if code_writer` — embedding pass
            // (CORTEX_DISABLE_GRAPH) không có writer ⇒ skip im silent.
            let store = ctx.store.as_deref_mut().expect("checked");
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
            let (deleted_nodes, deleted_unknown) = cortex_analyzer_framework::cleanup::cleanup_graph_files(
                store,
                &project_id,
                &targets,
            )
            .map_err(|e| e.to_string())?;
            if verbose {
                println!(
                    "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
                );
            }
        }

        // ── Parse ───────────────────────────────────────────────────────────
        let mut payloads: Vec<pyparse::FilePayload> = Vec::with_capacity(selected.len());
        for (index, path) in selected.iter().enumerate() {
            if verbose && (index == 0 || (index + 1) % 50 == 0 || index + 1 == selected.len()) {
                println!("[parse] {}/{}: {}", index + 1, selected.len(), path.display());
            }
            payloads.push(pyparse::parse_python_file(path, &ctx.root)?);
        }

        // ── Semantic enrichment (mutates payload functions như Python) ──────
        let engine = SemanticInferenceEngine::new();
        {
            let function_refs: Vec<FunctionRef<'_>> = payloads
                .iter()
                .flat_map(|payload| payload.functions.iter())
                .map(|func| FunctionRef {
                    symbol_id: &func.symbol_id,
                    code: &func.code,
                })
                .collect();
            // Usage index build từ PARSE-stage calls (callee_id None) — by_name only.
            let usage_calls: Vec<UsageCall<'_>> = payloads
                .iter()
                .flat_map(|payload| payload.calls.iter())
                .map(|call| UsageCall {
                    caller_id: &call.caller_id,
                    callee_id: "",
                    callee_name: &call.callee_name,
                })
                .collect();
            let usage_index = build_usage_index(&function_refs, &usage_calls);
            if verbose {
                let total: usize = payloads.iter().map(|payload| payload.functions.len()).sum();
                println!("[semantic] Enriching {total} functions...");
            }
            for payload in &mut payloads {
                for func in &mut payload.functions {
                    let enriched = engine.enrich_one(
                        &func.name,
                        &func.code,
                        &func.comment,
                        &func.summary,
                        func.arity,
                        &func.symbol_id,
                        func.exported,
                        "",
                        &[],
                        &usage_index,
                    );
                    func.intent = enriched.intent;
                    func.inferred_doc = enriched.inferred_doc;
                    func.doc_confidence = enriched.doc_confidence;
                    func.side_effect = enriched.side_effect;
                    if func.comment.is_empty() {
                        func.summary = enriched.summary.clone();
                    }
                    func.note = enriched.note;
                }
            }
        }

        // ── Module index + call resolution + rows ───────────────────────────
        let module_index = pyparse::build_module_index(&all_scanned, &ctx.root);
        let function_index = FunctionIndex::build(&payloads);
        let graph = crate::resolve::assemble_graph(
            &payloads,
            &function_index,
            &module_index,
            &project_id,
            &ctx.project_name,
            &language,
            &repo,
            &ctx.build_system,
            &ctx.root.to_string_lossy(),
        );

        // ── Graph write (skip khi graphless) ────────────────────────────────
        if let Some(store) = ctx.store.take() {
            let mut writer = LanguageCodeWriter::new(store, None, 1000, verbose);
            if verbose {
                println!("[graph] Writing nodes and relations (streaming)...");
            }
            let counts = writer.write_all(&WriteAllPayload {
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
                files_variant: FilesVariant::WithImports,
            });
            match counts {
                Ok(counts) if verbose => {
                    println!("[graph] Write complete ({counts:?})");
                }
                Err(error) => return Err(format!("[graph] write failed: {error}")),
                _ => {}
            }
        }

        let function_total: usize = payloads.iter().map(|payload| payload.functions.len()).sum();
        let class_total: usize = payloads.iter().map(|payload| payload.classes.len()).sum();
        println!(
            "{}",
            scan_result_line(&language, payloads.len(), function_total, class_total)
        );

        if args.message_scan_enabled() && verbose {
            // Python plane — Rust backend skip có kiểm soát (plan: message scan sau).
            println!("[message] message scan là plane Python; Rust backend skip (phase 04)");
        }
        if verbose {
            println!("[done] Total time: {:.2}s", start.elapsed().as_secs_f64());
        }

        let mut result = AnalyzerResult {
            files_scanned: payloads.len(),
            functions: function_total,
            classes: class_total,
            relations: graph.relations.len(),
            calls_resolved: graph.calls.len(),
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

fn rel_of(root: &std::path::Path, path: &std::path::Path) -> String {
    rel_posix(root, path)
}

//! analyzer-ts — Rust port của `tools/ts/ts_analyzer.py` (phase 05, batch 1).
//!
//! Khác biệt scope có chủ đích: embedding/Qdrant + message scan là plane
//! Python (key decision #3); `_frontend_extractor` phía Python không mutate
//! graph rows (kết quả `extract_batch` bị bỏ) nên Rust bỏ qua; LLM react-role
//! upgrade là env-gated no-op default.

mod ast;
mod deps;
mod parser;
mod pipeline;
mod regexes;
mod symbol;
mod traversal;

use std::collections::BTreeSet;

use cortex_analyzer_framework::cli::{abs_root, AnalyzerArgs};
use cortex_graph_writer::language_writer::LanguageCodeWriter;

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let args = AnalyzerArgs::parse_from(&argv);
    let code = run(&args);
    std::process::exit(code);
}

fn run(args: &AnalyzerArgs) -> i32 {
    let root = abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return 2;
    }
    let verbose = args.verbose;
    let project_id = args.project_id_or_root();
    let project_name = args.project_name();
    let language = {
        let lang = args.language_or_default();
        if lang.is_empty() {
            "typescript".to_string()
        } else {
            lang
        }
    };
    let repo = args.repo_or_root();
    let build_system = args.build_system();

    if args.dry_run {
        let mut files = pipeline::scan_ts_files(&root);
        let manifest_set = if args.incremental {
            args.changed_files_manifest
                .as_deref()
                .map(|manifest| {
                    cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
                })
                .unwrap_or_default()
        } else {
            Default::default()
        };
        if args.incremental && !manifest_set.is_empty() {
            files.retain(|path| manifest_set.contains(&cortex_analyzer_framework::scan::rel_posix(&root, path)));
            println!(
                "Dry run (incremental): {} TypeScript files selected (manifest={})",
                files.len(),
                manifest_set.len()
            );
        } else {
            println!("Dry run: {} TypeScript files found", files.len());
        }
        return 0;
    }

    if args.graph_writes_disabled() {
        println!("[graph] CORTEX_DISABLE_GRAPH — parse-only");
        let files = pipeline::scan_ts_files(&root);
        println!(
            "[SCAN_RESULT] parser={language} files={} functions=0 classes=0",
            files.len()
        );
        return 0;
    }

    let store = match args.open_store() {
        Ok(store) => store,
        Err(error) => {
            eprintln!("[graph] open store failed: {error}");
            return 1;
        }
    };
    let mut writer = LanguageCodeWriter::new(store, None, 1000, verbose);

    let changed_set: BTreeSet<String> = if args.incremental {
        args.changed_files_manifest
            .as_deref()
            .map(|manifest| {
                cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
            })
            .unwrap_or_default()
    } else {
        Default::default()
    };
    let deleted_set: BTreeSet<String> = if args.incremental {
        args.deleted_files_manifest
            .as_deref()
            .map(|manifest| {
                cortex_analyzer_framework::manifest::load_manifest_paths(manifest, &root)
            })
            .unwrap_or_default()
    } else {
        Default::default()
    };

    if verbose {
        // [diff] line khớp python_analyzer main
        if args.incremental {
            println!(
                "[diff] incremental manifests changed={} deleted={}",
                changed_set.len(),
                deleted_set.len()
            );
        }
    }

    let ctx = pipeline::TsPipeline {
        project_id,
        project_name,
        language: language.clone(),
        repo,
        build_system,
        verbose,
        incremental: args.incremental,
        commit_sha: args.commit_sha_after.clone(),
        commit_sha_before: args.commit_sha_before.clone(),
        changed_set,
        deleted_set,
    };
    let root_for_pipeline = root.clone();
    if let Err(error) = pipeline::build_call_graph(&ctx, &root_for_pipeline, &mut writer) {
        eprintln!("[graph] pipeline failed: {error}");
        return 1;
    }

    0
}

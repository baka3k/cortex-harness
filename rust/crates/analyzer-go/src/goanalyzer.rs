//! `GoAnalyzer` pipeline — port `go_analyzer.py::main`/`build_call_graph`/
//! `_write_graph`: scan → manifest selection → incremental cleanup → parse →
//! rows → `LanguageCodeWriter::write_all` (`use_full_writers=True`, files
//! variant default) → `[SCAN_RESULT]` (kèm `vectors=0 vector_status=disabled`
//! như Python khi Qdrant không cấu hình — Rust backend không embed vector).

use std::collections::BTreeSet;
use std::time::Instant;

use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::rel_posix;
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};
use cortex_graph_writer::store::GraphStore;

use crate::goparse;
use crate::resolve::prepare_write_rows;

/// Cờ extra của `go_analyzer.py::parse_args` mà `AnalyzerArgs` chưa có — nhận
/// và bỏ qua (plane Python hoặc không tác động graph-plane Rust).
#[derive(Debug, Clone, clap::Parser)]
#[command(no_binary_name = true)]
pub struct GoExtraArgs {
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    /// Writer batch size (`--neo4j-batch-size`, default 1000 như Python).
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
    /// `--output/-o` — Python ghi JSON payload ra file; Rust backend không ghi.
    #[arg(long = "output", short = 'o', hide = true)]
    pub output: Option<String>,
    #[arg(long, hide = true)]
    pub pretty: bool,
}

/// Env fallback khớp argparse defaults: giá trị non-empty của flag thắng,
/// rồi env, rồi default.
fn env_or(flag: &Option<String>, env_var: &str) -> Option<String> {
    if let Some(value) = flag
        && !value.is_empty()
    {
        return Some(value.clone());
    }
    std::env::var(env_var)
        .ok()
        .filter(|value| !value.is_empty())
        .or_else(|| flag.clone())
}

/// Project scope + language/build_system defaults khớp `parse_args` + `main`.
pub struct Scope {
    pub project_id: String,
    pub project_name: String,
    pub language: String,
    pub repo: String,
    pub build_system: String,
}

pub fn build_scope(args: &AnalyzerArgs, root: &std::path::Path) -> Scope {
    // Python: `args.project_id or basename(abspath(root)) or "go-project"`.
    let project_id = env_or(&args.project_id, "PROJECT_ID").unwrap_or_else(|| {
        let base = root
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        if base.is_empty() {
            "go-project".to_string()
        } else {
            base
        }
    });
    let project_name =
        env_or(&args.project_name, "PROJECT_NAME").unwrap_or_else(|| project_id.clone());
    let language =
        env_or(&args.language, "PROJECT_LANGUAGE").unwrap_or_else(|| "go".to_string());
    let repo =
        env_or(&args.repo, "PROJECT_REPO").unwrap_or_else(|| repo_name(&project_name, root));
    let build_system = env_or(&args.build_system, "PROJECT_BUILD_SYSTEM")
        .unwrap_or_else(|| "go".to_string());
    Scope {
        project_id,
        project_name,
        language,
        repo,
        build_system,
    }
}

/// `_repo_name` — `{project_name}/{basename(abspath(root))}`.
fn repo_name(project_name: &str, root: &std::path::Path) -> String {
    let base = root
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    format!("{project_name}/{base}")
}

/// Mở store theo `--graph-provider` trừ khi CORTEX_DISABLE_GRAPH (khớp
/// `prepare_graph_args`/`create_graph_driver_from_args`).
fn open_store(
    args: &AnalyzerArgs,
) -> Result<Option<Box<dyn GraphStore>>, String> {
    if args.graph_writes_disabled() {
        return Ok(None);
    }
    args.open_store().map(Some).map_err(|e| e.to_string())
}

/// Entry của main — chạy pipeline, trả exit code (0/2/3 như Python).
pub fn execute(args: &AnalyzerArgs, extra: &GoExtraArgs) -> Result<i32, String> {
    let start = Instant::now();
    let verbose = args.verbose;
    let root = cortex_analyzer_framework::cli::abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return Ok(2);
    }
    let scope = build_scope(args, &root);

    // ── Dry run — Python in full payload JSON; Rust in đếm file (divergence
    // chấp nhận: payload JSON là debug output, không phải graph-plane) ───────
    if args.dry_run {
        let files = goparse::scan_go_files(&root);
        println!("Dry run: {} Go files found under {}", files.len(), root.display());
        return Ok(0);
    }


    // ── Manifests (chỉ khi incremental; lọc .go như Python) ─────────────────
    let mut changed_manifest: BTreeSet<String> = BTreeSet::new();
    let mut deleted_manifest: BTreeSet<String> = BTreeSet::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            changed_manifest = load_manifest_paths(manifest, &root)
                .into_iter()
                .filter(|path| path.ends_with(".go"))
                .collect();
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            deleted_manifest = load_manifest_paths(manifest, &root)
                .into_iter()
                .filter(|path| path.ends_with(".go"))
                .collect();
        }
    }

    // ── Scan + selection — Python: `if selected and rel_path not in selected`
    // ⇒ set rỗng/không-manifest = KHÔNG lọc (quét tất cả) ───────────────────
    let all_scanned = goparse::scan_go_files(&root);
    let selected: Vec<std::path::PathBuf> = if args.incremental && !changed_manifest.is_empty() {
        all_scanned
            .iter()
            .filter(|path| changed_manifest.contains(&rel_posix(&root, path)))
            .cloned()
            .collect()
    } else {
        all_scanned.clone()
    };
    let payloads: Vec<goparse::FilePayload> = selected
        .iter()
        .map(|path| goparse::parse_go_file(path, &root))
        .collect::<Result<_, _>>()?;

    // ── Rows built for BOTH planes: graph write and the phase-06 native
    // embedding pass (graphless). Rows are cheap to build and the embedding
    // pass runs with the store closed, so we cannot hide construction behind
    // `if store` anymore. ───────────────────────────────────────────────────
    let graph = prepare_write_rows(
        &payloads,
        &scope.project_id,
        &scope.project_name,
        &scope.language,
        &scope.repo,
        &scope.build_system,
    );

    let payload = WriteAllPayload {
        projects: &[],
        packages: &[],
        namespaces: &graph.namespaces,
        files: &graph.files,
        classes: &[],
        types: &graph.types,
        function_types: &[],
        functions: &graph.functions,
        fields: &graph.fields,
        aliases: &graph.aliases,
        templates: &graph.templates,
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
        files_variant: FilesVariant::Default,
    };

    // ── Phase-06 embedding-input artifact (only when orchestrator asked) ───
    if let Some(output) = args.embedding_input_output() {
        let selected_rel: Vec<String> =
            selected.iter().map(|path| rel_posix(&root, path)).collect();
        cortex_analyzer_framework::embedding_artifact::maybe_emit_embedding_artifact(
            Some(output),
            cortex_analyzer_framework::embedding_artifact::EmbeddingEmission {
                parser: "go",
                project_id: &scope.project_id,
                root_scope: &scope.repo,
                full_replace: !args.incremental,
                scanned_directory: true,
                files_selected: selected_rel,
                files_deleted: deleted_manifest.iter().cloned().collect(),
                categories: payload.embedding_categories(),
            },
        )
        .map_err(|e| e.to_string())?;
    }

    // ── Store + incremental cleanup (changed ∪ deleted) ─────────────────────
    let store = open_store(args)?;
    if let Some(mut store) = store {
        if args.incremental && (!changed_manifest.is_empty() || !deleted_manifest.is_empty()) {
            let mut targets: Vec<String> = changed_manifest
                .union(&deleted_manifest)
                .cloned()
                .collect();
            targets.sort();
            if verbose {
                println!(
                    "[cleanup][graph] deleting graph data for {} files",
                    targets.len()
                );
            }
            let (deleted_nodes, deleted_unknown) =
                cleanup_graph_files(store.as_mut(), &scope.project_id, &targets)
                    .map_err(|e| e.to_string())?;
            if verbose {
                println!(
                    "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
                );
            }
        }

        // ── write_all ───────────────────────────────────────────────────────
        let mut writer =
            LanguageCodeWriter::new(store, args.neo4j_db.clone(), extra.neo4j_batch_size, verbose);
        match writer.write_all(&payload) {
            Ok(counts) => {
                if verbose {
                    println!("[graph] written {counts:?}");
                }
            }
            Err(error) => {
                eprintln!("Go graph persistence failed: {error}");
                return Ok(3);
            }
        }
    }

    // ── Vector sync — plane Python (torch/transformers); Rust bỏ qua ────────
    let vector_count = 0usize;
    let vector_status = "disabled";

    println!(
        "[SCAN_RESULT] parser=go files={} functions={} classes={} vectors={vector_count} vector_status={vector_status}",
        payloads.len(),
        total_functions(&payloads),
        total_types(&payloads),
    );
    if verbose {
        println!("[done] Total time: {:.2}s", start.elapsed().as_secs_f64());
    }
    Ok(0)
}

/// Python: `sum(len(payload.get("functions") or []) for payload in payloads)`.
fn total_functions(payloads: &[goparse::FilePayload]) -> usize {
    payloads.iter().map(|p| p.functions.len()).sum()
}

fn total_types(payloads: &[goparse::FilePayload]) -> usize {
    payloads.iter().map(|p| p.types.len()).sum()
}

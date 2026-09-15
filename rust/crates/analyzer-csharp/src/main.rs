//! analyzer-csharp — Rust port của `code-tiny/tools/csharp/csharp_analyzer.py`
//! (phase 03 analyzer-layer-rust-cutover).
//!
//! Pipeline: scan (`.cs` files qua `scan_csharp_files`) → bootstrap Roslyn
//! worker (`ensure_worker_built`, fail-loud nếu dotnet thiếu / build hỏng) →
//! `analyze_csharp_files` (manifest build + spawn `dotnet <dll>`) →
//! `roslyn_evidence_to_payload` per result → `build_graph_rows` →
//! `write_graph` qua `LanguageCodeWriter::write_all` →
//! `[SCAN_RESULT] parser=csharp ...`.
//!
//! Khác biệt scope có chủ đích (key decision #3):
//! * Vector lane (Qdrant/embedding) KHÔNG port — flags nhận, bỏ qua; luôn
//!   in `vectors=0 vector_status=disabled`.
//! * Message scan là plane Python-side — flag nhận và skip có kiểm soát
//!   (Python gốc `set_defaults(enable_message_scan=True)`).
//! * Parse cache / neo4j resume state — Rust luôn scan + viết full graph.
//! * `--disable-roslyn` không có fallback (no tree-sitter) → exit 3 với
//!   chỉ dẫn. Khác với Python (có thể chạy tree-sitter-only).

mod adapter;
mod graph;
mod payload;
mod scan;

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use clap::Parser as ClapParser;
use cortex_analyzer_framework::cli::{abs_root, AnalyzerArgs};
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use serde_json::{json, Value};

use crate::adapter::{analyze_csharp_files, parse_provenance, WorkerRuntime};
use crate::graph::{build_graph_rows, graph_writes_disabled, write_graph};
use crate::payload::roslyn_evidence_to_payload;
use crate::scan::{rel_posix, scan_csharp_files};

/// Cờ C#-specific ngoài contract chung — mirror `parse_args` của
/// `csharp_analyzer.py`. Phần plane Rust không áp dụng (qdrant/embed/...)
/// nhận-và-bỏ-qua để orchestrator truyền từ cờ lane vector mà không vỡ CLI.
#[derive(Debug, Default, clap::Args)]
pub struct CsharpExtraArgs {
    /// Disable Roslyn worker — Rust backend KHÔNG có tree-sitter fallback,
    /// parser entry exit 3 nếu bật (cố ý fail-loud để caller chuyển sang
    /// Python backend nếu cần tree-sitter-only).
    #[arg(long)]
    pub disable_roslyn: bool,

    /// Path tới `CSharpRoslynWorker.csproj` (mặc định
    /// `code-tiny/tools/csharp/roslyn_worker/CSharpRoslynWorker.csproj`).
    #[arg(long)]
    pub roslyn_worker_project: Option<String>,

    /// Chế độ semantic Roslyn: `auto | on | off` (mặc định auto).
    #[arg(long, default_value = "auto")]
    pub semantic_mode: String,

    /// Timeout giây cho mỗi lần spawn worker.
    #[arg(long, default_value_t = 600.0)]
    pub roslyn_timeout: f64,

    // ── Neo4j legacy — orchestrator có thể truyền từ cờ gốc; graph provider
    // bên dưới (`open_store`) xử lý. ───────────────────────────────────────
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, default_value = "1000")]
    pub neo4j_batch_size: i64,
    #[arg(long, hide = true)]
    pub neo4j_state: Option<String>,
    #[arg(long, hide = true)]
    pub disable_neo4j_resume: bool,

    // ── Qdrant tuning — nhận-và-bỏ-qua (vector plane Python). ─────────────
    #[arg(long, hide = true)]
    pub qdrant_batch_size: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_timeout: Option<f64>,
    #[arg(long, hide = true)]
    pub qdrant_retries: Option<i64>,
    #[arg(long, hide = true)]
    pub qdrant_retry_sleep: Option<f64>,
}

#[derive(Debug, ClapParser)]
#[command(no_binary_name = true, version = cortex_analyzer_framework::BUILD_COMMIT)]
struct CsharpAnalyzerArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: CsharpExtraArgs,
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = CsharpAnalyzerArgs::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

fn run(common: &AnalyzerArgs, extra: &CsharpExtraArgs) -> i32 {
    if common.root.trim().is_empty() {
        eprintln!("--root is required");
        return 2;
    }
    let root = abs_root(&common.root);

    let verbose = common.verbose;

    // Project metadata — khớp Python `build_call_graph(...)` defaults.
    let project_id = non_empty(common.project_id.clone()).unwrap_or_else(|| basename(&root));
    let project_name = non_empty(common.project_name.clone()).unwrap_or_else(|| project_id.clone());
    let language = non_empty(common.language.clone()).unwrap_or_else(|| "csharp".to_string());
    let repo = non_empty(common.repo.clone())
        .unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = common.build_system();

    // `--disable-roslyn` ⇒ không fallback tree-sitter (Rust backend chỉ chạy
    // qua worker); thoát sớm với diagnostic để caller chuyển Python child.
    if extra.disable_roslyn {
        eprintln!(
            "--disable-roslyn is not supported in Rust backend (no tree-sitter fallback); \
             run `analyzer-csharp` without --disable-roslyn, or set CORTEX_RUST_ANALYZER=python"
        );
        return 3;
    }

    // Manifest selection (incremental) — khớp `_scan_csharp_files` ⊆
    // `changed_files_manifest` filter của Python entry.
    let selected_rel_paths: BTreeSet<String> = if common.incremental {
        match &common.changed_files_manifest {
            Some(manifest) => load_manifest_paths(manifest, &root)
                .into_iter()
                .filter(|rel| rel.ends_with(".cs"))
                .collect(),
            None => BTreeSet::new(),
        }
    } else {
        BTreeSet::new()
    };
    let deleted_rel_paths: BTreeSet<String> = if common.incremental {
        match &common.deleted_files_manifest {
            Some(manifest) => load_manifest_paths(manifest, &root)
                .into_iter()
                .filter(|rel| rel.ends_with(".cs"))
                .collect(),
            None => BTreeSet::new(),
        }
    } else {
        BTreeSet::new()
    };

    // Graph store mở sớm một lần — dùng chung cho incremental cleanup và
    // graph write cuối. Lỗi mở store KHÔNG dừng parse (khớp behavior gốc:
    // fail ở bước graph write, exit 3 sau khi [SCAN_RESULT] đã in).
    let mut store_open_error: Option<String> = None;
    let mut store_opt = if !common.graph_writes_disabled() && !graph_writes_disabled() {
        match common.open_store() {
            Ok(store) => Some(store),
            Err(error) => {
                store_open_error = Some(format!("{error}"));
                None
            }
        }
    } else {
        None
    };

    // ── Incremental cleanup (changed ∪ deleted) — mirror analyzer-python
    //    src/python_analyzer.rs: cleanup trước parse, cùng format log. ────
    if common.incremental && store_opt.is_some() {
        let mut targets: Vec<String> = selected_rel_paths
            .union(&deleted_rel_paths)
            .cloned()
            .collect();
        targets.sort();
        if !targets.is_empty() {
            if verbose {
                println!("[cleanup][graph] deleting graph data for {} files", targets.len());
            }
            match cortex_analyzer_framework::cleanup::cleanup_graph_files(
                store_opt.as_deref_mut().expect("checked"),
                &project_id,
                &targets,
            ) {
                Ok((deleted_nodes, deleted_unknown)) => {
                    if verbose {
                        println!(
                            "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
                        );
                    }
                }
                Err(error) => {
                    eprintln!("C# graph cleanup failed: {error}");
                    return 3;
                }
            }
        }
    }

    // Scan workspace → áp manifest subset.
    let files = collect_files(&root, &selected_rel_paths);
    if common.dry_run {
        if common.incremental && !selected_rel_paths.is_empty() {
            println!(
                "Dry run (incremental): {} C# files selected (manifest={})",
                files.len(),
                selected_rel_paths.len()
            );
        } else {
            println!("Dry run: {} C# files found", files.len());
        }
        return 0;
    }

    if files.is_empty() {
        // Khớp Python: không có file .cs → graph rỗng, log `[SCAN_RESULT]` rồi thoát.
        println!("[SCAN_RESULT] parser=csharp files=0 functions=0 classes=0 vectors=0 vector_status=disabled");
        return 0;
    }

    // Roslyn worker — bootstrap (build nếu dll thiếu/stale), spawn `dotnet <dll>
    // --manifest <req.json>` qua `analyze_csharp_files`. Lỗi đều loud (exit 3).
    let runtime = WorkerRuntime::default();
    let relative_files: Vec<String> = files.iter().map(|file| rel_posix(&root, file)).collect();

    let worker_response = match analyze_csharp_files(
        &runtime,
        &root,
        &relative_files,
        &extra.semantic_mode,
        extra.roslyn_worker_project.as_deref(),
        extra.roslyn_timeout,
        verbose,
    ) {
        Ok(response) => response,
        Err(error) => {
            eprintln!("C# Roslyn worker failed: {error}");
            if verbose {
                eprintln!(
                    "Hint: build the worker manually with `dotnet build code-tiny/tools/csharp/\
                     roslyn_worker/CSharpRoslynWorker.csproj -c Release` or fall back to the \
                     Python backend via `CORTEX_RUST_ANALYZER=python`."
                );
            }
            return 3;
        }
    };

    // Map worker response → legacy payload shape (khớp `selected_payloads`
    // trong `build_call_graph` Python entry). Mirror `roslyn_integration.py:
    // 119-126`: skip `ok=false`, unwrap the nested `evidence` member — the
    // worker result item is NOT itself the evidence payload.
    let provenance = parse_provenance(&worker_response);
    let payloads: Vec<Value> = match worker_response.get("results").and_then(Value::as_array) {
        Some(results) => results
            .iter()
            .filter_map(|result| {
                if !result.get("ok").and_then(Value::as_bool).unwrap_or(false) {
                    return None;
                }
                let evidence = result
                    .get("evidence")
                    .cloned()
                    .unwrap_or_else(|| Value::Object(serde_json::Map::new()));
                Some(attach_provenance(
                    roslyn_evidence_to_payload(&evidence),
                    &provenance,
                ))
            })
            .collect(),
        None => Vec::new(),
    };

    let mut exit_code = 0;

    if let Some(store) = store_opt.take() {
        let rows = build_graph_rows(
            &payloads,
            &project_id,
            &project_name,
            &language,
            &repo,
            &build_system,
            &root.to_string_lossy(),
        );
        let mut writer = LanguageCodeWriter::new(
            store,
            common
                .neo4j_db
                .clone()
                .filter(|db| !db.is_empty()),
            extra.neo4j_batch_size.max(1) as usize,
            verbose,
        );
        if let Err(error) = write_graph(&mut writer, &rows) {
            eprintln!("C# graph persistence failed: {error}");
            exit_code = 3;
        }
    } else if let Some(error) = store_open_error {
        eprintln!("C# graph persistence failed: {error}");
        exit_code = 3;
    } else if verbose {
        println!("[graph] disabled; missing graph connection settings");
    }

    // Skip message scan plane (Python-side) — log giống Python khi caller
    // bật cờ để kết quả quan sát được, nhưng không exec (key decision #3).
    if common.message_scan_enabled() && verbose {
        println!("[message-scan] skipped (Rust backend, plane Python-side)");
    }

    // `[SCAN_RESULT]` — luôn in, vectors=0 (Rust không port vector lane).
    let sr_fn: usize = payloads
        .iter()
        .map(|payload| payload["functions"].as_array().map(Vec::len).unwrap_or(0))
        .sum();
    let sr_cls: usize = payloads
        .iter()
        .map(|payload| payload["types"].as_array().map(Vec::len).unwrap_or(0))
        .sum();
    let sr_files = payloads.len();
    println!(
        "[SCAN_RESULT] parser=csharp files={sr_files} functions={sr_fn} classes={sr_cls} \
         vectors=0 vector_status=disabled"
    );

    if let Some(summary_path) = common.summary_path.as_deref().filter(|path| !path.is_empty()) {
        let summary = json!({
            "parser": "csharp",
            "files": sr_files,
            "functions": sr_fn,
            "classes": sr_cls,
            "language": language,
            "project_id": project_id,
            "vectors": 0,
            "vector_status": "disabled",
        });
        if let Err(error) = std::fs::write(summary_path, serde_json::to_vec_pretty(&summary).unwrap_or_default()) {
            eprintln!("[summary] write failed: {error}");
        }
    }

    exit_code
}

/// Gộp `parse_meta` trong payload với provenance worker — khớp
/// `parse_meta_from_evidence` của Python.
fn attach_provenance(mut payload: Value, provenance: &Value) -> Value {
    if let Some(parse_meta) = payload
        .get_mut("parse_meta")
        .and_then(Value::as_object_mut)
        && let Some(source) = provenance.as_object() {
            for (key, value) in source {
                parse_meta.insert(key.clone(), value.clone());
            }
        }
    payload
}

/// `_scan_csharp_files` + lọc manifest subset — chỉ trả file `.cs` nằm trong
/// `selected_rel_paths` (incremental) hoặc toàn bộ root nếu không incremental
/// / không có manifest.
fn collect_files(root: &Path, selected_rel_paths: &BTreeSet<String>) -> Vec<PathBuf> {
    let mut files = scan_csharp_files(root);
    if !selected_rel_paths.is_empty() {
        files.retain(|file| {
            let rel = rel_posix(root, file);
            selected_rel_paths.contains(&rel)
        });
    }
    files
}

fn basename(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default()
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.filter(|value| !value.is_empty())
}

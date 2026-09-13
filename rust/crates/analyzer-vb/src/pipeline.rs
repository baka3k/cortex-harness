//! Port `vb_analyzer_base.py::main` + `build_call_graph` (+ helper
//! `_parse_vbnet_with_roslyn_batch`): scan → manifests → cleanup → parse
//! (Roslyn batch cho vbnet engine!=regex, ngược lại regex tuần tự) →
//! resolve_calls → rows → `LanguageCodeWriter::write_all` → `[SCAN_RESULT]`.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::io::Write as _;
use std::path::PathBuf;
use std::time::Instant;

use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::cli::abs_root;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::rel_posix;
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};
use cortex_graph_writer::store::GraphStore;

use crate::cli::{VbArgs, VbExtraArgs};
use crate::model::{valid_payload_shape, FilePayload};
use crate::parse::{
    self, asdict_class, asdict_constant, asdict_enum, asdict_event, asdict_file,
    asdict_function, asdict_interface, asdict_namespace, asdict_property, asdict_variable,
    parse_vb_file, resolve_calls, PARSE_CACHE_VERSION,
};
use crate::roslyn;
use crate::scan::scan_vb_files;

/// Env fallback khớp argparse defaults (flag non-empty thắng → env → flag).
fn env_or(flag: &Option<String>, env_var: &str) -> Option<String> {
    if let Some(value) = flag
        && !value.is_empty()
    {
        return Some(value.clone());
    }
    std::env::var(env_var)
        .ok()
        .filter(|value| !value.is_empty())
}

fn normalize_rel(item: &str) -> String {
    item.replace('\\', "/")
}

fn basename_of(root: &std::path::Path) -> String {
    root.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string_lossy().to_string())
}

/// Entry chung của 4 binary — mirror `main` Python; trả exit code.
pub fn execute(args: &VbArgs) -> Result<i32, String> {
    let base = &args.base;
    let extra = &args.extra;
    let dialect = extra.dialect.map(|d| d.as_str().to_string()).unwrap_or_default();

    let root = abs_root(&base.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", base.root);
        return Ok(2);
    }
    let verbose = base.verbose;

    // ── Manifests (incremental) ─────────────────────────────────────────────
    let mut changed_files: BTreeSet<String> = BTreeSet::new();
    let mut deleted_files: BTreeSet<String> = BTreeSet::new();
    if base.incremental {
        if let Some(manifest) = &base.changed_files_manifest {
            changed_files = load_manifest_paths(manifest, &root);
        }
        if let Some(manifest) = &base.deleted_files_manifest {
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
    if base.dry_run {
        let mut files = scan_vb_files(&root, &dialect);
        if base.incremental && !changed_files.is_empty() {
            files.retain(|path| changed_files.contains(&rel_posix(&root, path)));
            println!(
                "Dry run (incremental): {} {dialect} files selected (manifest={})",
                files.len(),
                changed_files.len()
            );
        } else {
            println!("Dry run: {len} {dialect} files found", len = files.len());
        }
        return Ok(0);
    }

    // ── Store (graph writes) ────────────────────────────────────────────────
    let store: Option<Box<dyn GraphStore>> = if base.graph_writes_disabled() {
        None
    } else {
        Some(base.open_store().map_err(|e| e.to_string())?)
    };

    // Qdrant/embedder plane Python — accept-and-ignore (key decision #3).
    // Parse cache plane Python — accept-and-ignore (`--cache-dir`,
    // `--disable-parse-cache`, `--ignore-cache`).

    // ── Project scope (khớp `main` Python) ──────────────────────────────────
    let project_id = env_or(&base.project_id, "PROJECT_ID")
        .unwrap_or_else(|| basename_of(&root));
    let project_name =
        env_or(&base.project_name, "PROJECT_NAME").unwrap_or_else(|| project_id.clone());
    let language = env_or(&base.language, "PROJECT_LANGUAGE").unwrap_or_else(|| dialect.clone());
    let repo = env_or(&base.repo, "PROJECT_REPO")
        .unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = env_or(&base.build_system, "PROJECT_BUILD_SYSTEM").unwrap_or_default();
    let neo4j_db = env_or(&base.neo4j_db, "NEO4J_DB");

    build_call_graph(
        &root,
        &base.root,
        &dialect,
        store,
        neo4j_db.as_deref(),
        extra,
        &project_id,
        &project_name,
        &language,
        &repo,
        &build_system,
        &changed_files,
        &deleted_files,
        base.incremental,
        verbose,
    )?;

    // Message scan plane Python — flag nhận (default BẬT như Python), skip.

    Ok(0)
}

/// `build_call_graph` — trả exit code semantics qua Result.
#[allow(clippy::too_many_arguments)]
fn build_call_graph(
    root: &std::path::Path,
    root_raw: &str,
    dialect: &str,
    mut store: Option<Box<dyn GraphStore>>,
    neo4j_db: Option<&str>,
    extra: &VbExtraArgs,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
    changed_files: &BTreeSet<String>,
    deleted_files: &BTreeSet<String>,
    incremental: bool,
    verbose: bool,
) -> Result<(), String> {
    let start_time = Instant::now();

    let all_source_files = scan_vb_files(root, dialect);
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

    let parse_files: Vec<PathBuf> = if incremental {
        all_source_files
            .iter()
            .filter(|path| changed_set.contains(&rel_posix(root, path)))
            .cloned()
            .collect()
    } else {
        all_source_files.clone()
    };

    // ── Cleanup (changed ∪ deleted) ─────────────────────────────────────────
    let cleanup_targets: Vec<String> = changed_set
        .union(&deleted_set)
        .cloned()
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect();
    if let Some(store_ref) = store.as_deref_mut()
        && !cleanup_targets.is_empty()
    {
        if verbose {
            println!(
                "[cleanup][graph] deleting graph data for {} files",
                cleanup_targets.len()
            );
        }
        let (deleted_nodes, deleted_unknown) =
            cleanup_graph_files(store_ref, project_id, &cleanup_targets)
                .map_err(|e| e.to_string())?;
        if verbose {
            println!(
                "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
            );
        }
    }

    if verbose {
        println!(
            "[parse][start] parser={dialect} files={} parallel_workers={} cache=off",
            parse_files.len(),
            extra.parallel_workers
        );
        if dialect == "vbnet" {
            let engine_for_run = if extra.vbnet_parser_engine == "regex" {
                "regex"
            } else {
                "roslyn"
            };
            println!(
                "[parse][engine] parser=vbnet engine={engine_for_run} semantic={}",
                extra.vbnet_semantic
            );
        }
    }

    // ── Parse ───────────────────────────────────────────────────────────────
    let mut payloads: Vec<FilePayload> = if dialect == "vbnet" && extra.vbnet_parser_engine != "regex"
    {
        parse_vbnet_with_roslyn_batch(
            &parse_files,
            root,
            extra,
            verbose,
        )?
    } else {
        let mut parsed: Vec<FilePayload> = Vec::with_capacity(parse_files.len());
        for abs_path in &parse_files {
            // Python `_parse_single_file`: parse exception → file bị skip.
            match parse_vb_file(
                abs_path,
                root,
                dialect,
                &extra.vbnet_semantic,
                "",
            ) {
                Ok(payload) => parsed.push(payload),
                Err(_) => continue,
            }
        }
        parsed
    };

    if verbose {
        println!(
            "[parse][done] parser={dialect} parsed={}/{}",
            payloads.len(),
            parse_files.len()
        );
    }

    resolve_calls(&mut payloads);

    if verbose {
        let total_calls: usize = payloads.iter().map(|p| p.calls.len()).sum();
        let resolved_calls: usize = payloads
            .iter()
            .map(|p| p.calls.iter().filter(|c| c.callee_id.is_some()).count())
            .sum();
        let pct = if total_calls > 0 {
            resolved_calls as f64 * 100.0 / total_calls as f64
        } else {
            0.0
        };
        println!("[vb] calls: {total_calls} total, {resolved_calls} resolved ({pct:.1}%)");
    }

    // ── Graph write ─────────────────────────────────────────────────────────
    if let Some(store) = store.take() {
        let mut files_rows: Vec<parse::Row> = Vec::new();
        let mut namespaces_rows: Vec<parse::Row> = Vec::new();
        let mut types_rows: Vec<parse::Row> = Vec::new();
        let mut functions_rows: Vec<parse::Row> = Vec::new();
        let mut relations_rows: Vec<parse::Row> = Vec::new();
        let mut calls_rows: Vec<parse::Row> = Vec::new();
        let mut properties_rows: Vec<parse::Row> = Vec::new();
        let mut events_rows: Vec<parse::Row> = Vec::new();
        let mut interfaces_rows: Vec<parse::Row> = Vec::new();
        let mut enums_rows: Vec<parse::Row> = Vec::new();
        let mut constants_rows: Vec<parse::Row> = Vec::new();
        let mut variables_rows: Vec<parse::Row> = Vec::new();

        for payload in &payloads {
            let file_def = payload.file_def.as_ref().ok_or("payload missing file_def")?;
            let file_row = asdict_file(file_def, project_id, project_name, language, repo, build_system);
            let file_row_id = file_row
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            files_rows.push(file_row);
            relations_rows.push(rel_row(project_id, &file_row_id, "CONTAINS"));

            for ns in &payload.namespaces {
                let row = asdict_namespace(ns, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                namespaces_rows.push(row);
            }
            for cls in &payload.classes {
                let row = asdict_class(cls, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                types_rows.push(row);
            }
            for func in &payload.functions {
                let row = asdict_function(func, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                functions_rows.push(row);
            }
            for rel in &payload.relations {
                relations_rows.push(serde_json::json!({
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "rel_type": rel.rel_type,
                    "properties": rel.properties,
                })
                .as_object()
                .unwrap()
                .clone());
            }
            for call in &payload.calls {
                if let Some(callee_id) = &call.callee_id {
                    calls_rows.push(
                        serde_json::json!({
                            "caller_id": call.caller_id,
                            "callee_id": callee_id,
                            "call_type": "call_expression",
                            // writer contract (mirror analyzer-java): call rows
                            // mang project_id tường minh.
                            "project_id": project_id,
                        })
                        .as_object()
                        .unwrap()
                        .clone(),
                    );
                }
            }
            for prop in &payload.properties {
                let row = asdict_property(prop, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                properties_rows.push(row);
            }
            for event in &payload.events {
                let row = asdict_event(event, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                events_rows.push(row);
            }
            for iface in &payload.interfaces {
                let row = asdict_interface(iface, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                interfaces_rows.push(row);
            }
            for enum_def in &payload.enums {
                let row = asdict_enum(enum_def, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                enums_rows.push(row);
            }
            for constant in &payload.constants {
                let row = asdict_constant(constant, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                constants_rows.push(row);
            }
            for var in &payload.variables {
                let row = asdict_variable(var, project_id, project_name, language, repo, build_system);
                let id = row_id(&row);
                relations_rows.push(rel_row(&file_row_id, &id, "CONTAINS"));
                variables_rows.push(row);
            }
        }

        let mut writer =
            LanguageCodeWriter::new(store, neo4j_db.map(str::to_string), extra.neo4j_batch_size.max(1) as usize, verbose);
        let project_rows: Vec<parse::Row> = vec![serde_json::json!({
            "id": project_id,
            "name": project_name,
            "language": language,
            "repo": repo,
            "root": root_raw,
            "build_system": build_system,
        })
        .as_object()
        .unwrap()
        .clone()];
        let payload = WriteAllPayload {
            projects: &project_rows,
            packages: &[],
            namespaces: &namespaces_rows,
            files: &files_rows,
            classes: &[],
            types: &types_rows,
            function_types: &[],
            functions: &functions_rows,
            fields: &[],
            aliases: &[],
            templates: &[],
            relations: &relations_rows,
            calls: &calls_rows,
            calls_with_site: &[],
            properties: &properties_rows,
            events: &events_rows,
            interfaces: &interfaces_rows,
            enums: &enums_rows,
            constants: &constants_rows,
            variables: &variables_rows,
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
        };
        let counts = writer.write_all(&payload).map_err(|e| format!("[graph] write failed: {e}"))?;
        let _ = counts;
        if verbose {
            println!(
                "[graph] write stats: {} functions, {} calls, {} relations",
                functions_rows.len(),
                calls_rows.len(),
                relations_rows.len()
            );
        }
    }

    // ── [SCAN_RESULT] (luôn in, flush như Python) ───────────────────────────
    let total_functions: usize = payloads.iter().map(|p| p.functions.len()).sum();
    let total_classes: usize = payloads.iter().map(|p| p.classes.len()).sum();
    println!(
        "[SCAN_RESULT] parser={dialect} files={} functions={total_functions} classes={total_classes}",
        payloads.len()
    );
    let _ = std::io::stdout().flush();
    if verbose {
        println!("[done] Total time: {:.2}s", start_time.elapsed().as_secs_f64());
    }
    Ok(())
}

fn row_id(row: &parse::Row) -> String {
    row.get("id")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string()
}

fn rel_row(source_id: &str, target_id: &str, rel_type: &str) -> parse::Row {
    serde_json::json!({
        "source_id": source_id,
        "target_id": target_id,
        "rel_type": rel_type,
        "properties": {},
    })
    .as_object()
    .unwrap()
    .clone()
}

/// `_parse_vbnet_with_roslyn_batch` — engine != "regex" cho vbnet: batch qua
/// Roslyn worker rồi fallback regex từng file khi payload thiếu/hỏng.
fn parse_vbnet_with_roslyn_batch(
    parse_files: &[PathBuf],
    root: &std::path::Path,
    extra: &VbExtraArgs,
    verbose: bool,
) -> Result<Vec<FilePayload>, String> {
    let (mut roslyn_payloads, mut roslyn_errors, batch_error) =
        match roslyn::parse_vbnet_files_with_roslyn(
            &root.to_string_lossy(),
            parse_files,
            &extra.vbnet_semantic,
            extra.vbnet_roslyn_worker_project.as_deref(),
            extra.vbnet_roslyn_timeout_sec,
            extra.vbnet_roslyn_workspace_timeout_ms,
            extra.vbnet_roslyn_file_timeout_ms,
            PARSE_CACHE_VERSION,
            verbose,
        ) {
            Ok((payloads, errors, _meta)) => (payloads, errors, String::new()),
            Err(error) => {
                if verbose {
                    println!("[parse][fallback] parser=vbnet reason=batch_error detail={error}");
                }
                (HashMap::new(), HashMap::new(), error)
            }
        };

    let mut hydrated: Vec<FilePayload> = Vec::with_capacity(parse_files.len());
    for abs_path in parse_files {
        let rel_path = rel_posix(root, abs_path);
        let payload = roslyn_payloads.remove(&rel_path).filter(valid_payload_shape);
        match payload {
            Some(payload) => {
                // Roslyn payload — hydrate theo dataclass shapes (khác biệt
                // parse_meta không vào graph).
                let hydrated_payload: FilePayload = serde_json::from_value(payload)
                    .map_err(|e| format!("invalid roslyn payload for {rel_path}: {e}"))?;
                hydrated.push(hydrated_payload);
            }
            None => {
                let fallback_reason = roslyn_errors
                    .remove(&rel_path)
                    .or_else(|| if batch_error.is_empty() { None } else { Some(batch_error.clone()) })
                    .unwrap_or_else(|| "roslyn_payload_missing_or_invalid".to_string());
                if verbose {
                    println!("[parse][fallback] parser=vbnet file={rel_path} reason={fallback_reason}");
                }
                let payload = parse_vb_file(
                    abs_path,
                    root,
                    "vbnet",
                    &extra.vbnet_semantic,
                    &fallback_reason,
                )?;
                hydrated.push(payload);
            }
        }
    }
    Ok(hydrated)
}

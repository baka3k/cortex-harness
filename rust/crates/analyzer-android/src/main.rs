//! analyzer-android — Rust port của `tools/android/android_kotlin_analyzer.py`
//! (+ phần `android_common.py` mà nó dùng) — phase 06, JVM batch. Pipeline:
//! scan (skip-list android riêng) → parse (tree-sitter-kotlin) → index ALL khi
//! incremental → import-impact selection → cleanup → node/rel/call rows →
//! `LanguageCodeWriter` (write_nodes_batch + write_relations_typed +
//! write_calls_with_site) → `[SCAN_RESULT]`.
//!
//! Scope khác biệt có chủ đích (như phase 04/05):
//! * Qdrant/embedding KHÔNG port (torch/transformers là plane Python) — cờ
//!   `--qdrant-*`, `--embed-*`, `--device`, `--batch-size`, `--max-embed-chars`,
//!   `--chunk-embed` nhận và bỏ qua.
//! * Message scan là plane Python-side; `--enable/--disable-message-scan` và
//!   `--message-*` nhận, skip có kiểm soát.
//! * Parse cache / neo4j resume state không áp dụng cho backend này — nhận
//!   `--disable-parse-cache`, `--ignore-cache`, `--neo4j-*`,
//!   `--disable-neo4j-resume`, `--keep-cache`, `--cache-dir` và bỏ qua.
//! * `--config` nhận và bỏ qua (orchestrator Rust truyền explicit args);
//!   `--event-map` được port đầy đủ (Event nodes + EMITS/HANDLES).

mod assemble;
mod common;
mod kotlinparse;
mod scan;
mod xmldom;

use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::PathBuf;
use std::time::Instant;

use clap::Parser;
use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::cli::AnalyzerArgs;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_graph_writer::language_writer::LanguageCodeWriter;

use assemble::FunctionIndex;
use kotlinparse::{parse_kotlin_file, FilePayload};
use scan::{
    collect_kotlin_import_graph, expand_impacted_files_by_imports,
    scan_android_gradle_files, scan_android_kotlin_files, scan_android_manifest_files,
    scan_android_resource_xml_files,
};

/// Cờ android-specific ngoài contract chung — mirror `parse_args`; phần không
/// áp dụng cho backend Rust nhận và bỏ qua.
#[derive(Debug, Clone, Default, clap::Args)]
pub struct AndroidExtraArgs {
    #[arg(long, hide = true)]
    pub neo4j_uri: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_user: Option<String>,
    #[arg(long, hide = true)]
    pub neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long, hide = true)]
    pub neo4j_db: Option<String>,
    #[arg(long, hide = true)]
    pub qdrant_url: Option<String>,
    #[arg(long, hide = true)]
    pub qdrant_collection: Option<String>,
    #[arg(long, hide = true)]
    pub embed_model: Option<String>,
    #[arg(long, hide = true)]
    pub max_embed_chars: Option<i64>,
    #[arg(long, hide = true)]
    pub device: Option<String>,
    #[arg(long, hide = true)]
    pub batch_size: Option<i64>,
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
    #[arg(long, hide = true)]
    pub event_map: Option<String>,
    #[arg(long, hide = true)]
    pub falkordb_ssl: bool,
}

fn main() {
    let mut argv: Vec<String> = std::env::args().skip(1).collect();
    // Message-scan normalize — khớp `set_defaults(enable_message_scan=True)`:
    // dedupe cờ bật, `--disable-message-scan` thắng.
    argv.retain(|a| a != "--enable-message-scan");
    if !argv.iter().any(|a| a == "--disable-message-scan") {
        argv.push("--enable-message-scan".to_string());
    }
    let strs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let args = AndroidArgs::parse_from(strs);
    let code = run(&args.common, &args.extra);
    std::process::exit(code);
}

#[derive(Debug, Parser)]
#[command(no_binary_name = true)]
struct AndroidArgs {
    #[command(flatten)]
    common: AnalyzerArgs,
    #[command(flatten)]
    extra: AndroidExtraArgs,
}

fn normalize_rel(item: &str) -> String {
    item.replace('\\', "/")
}

fn non_empty(value: Option<String>) -> Option<String> {
    value.filter(|v| !v.is_empty())
}

/// `_load_event_map` — JSON object, "events" list (mặc định rỗng).
fn load_event_map(path: &str) -> Result<serde_json::Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let data: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    if !data.is_object() {
        return Err("Event map must be a JSON object".to_string());
    }
    let mut data = data;
    if data.get("events").is_none()
        && let Some(obj) = data.as_object_mut()
    {
        obj.insert("events".to_string(), serde_json::json!([]));
    }
    if !data.get("events").map(serde_json::Value::is_array).unwrap_or(false) {
        return Err("Event map 'events' must be a list".to_string());
    }
    Ok(data)
}

fn run(args: &AnalyzerArgs, extra: &AndroidExtraArgs) -> i32 {
    match execute(args, extra) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    }
}

fn execute(args: &AnalyzerArgs, extra: &AndroidExtraArgs) -> Result<i32, String> {
    let root = cortex_analyzer_framework::cli::abs_root(&args.root);
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
        let mut files = scan_android_kotlin_files(&root);
        if args.incremental && !changed_files.is_empty() {
            files.retain(|path| {
                changed_files.contains(&scan::rel_path_posix(&root, path))
            });
            println!(
                "Dry run (incremental): {} Android Kotlin files selected (manifest={})",
                files.len(),
                changed_files.len()
            );
        } else {
            println!("Dry run: {} Kotlin files found", files.len());
        }
        return Ok(0);
    }

    // ── Project scope (khớp `main` Python) ──────────────────────────────────
    let project_id = non_empty(args.project_id.clone()).unwrap_or_else(|| basename_of(&root));
    let project_name = non_empty(args.project_name.clone()).unwrap_or_else(|| project_id.clone());
    let language = non_empty(args.language.clone()).unwrap_or_else(|| "android-kotlin".to_string());
    let repo = non_empty(args.repo.clone()).unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = non_empty(args.build_system.clone()).unwrap_or_default();
    let commit_sha = args.commit_sha_after.clone();
    let commit_sha_before = args.commit_sha_before.clone();

    let start_time = Instant::now();
    // ── Scan ────────────────────────────────────────────────────────────────
    let all_kotlin_files = scan_android_kotlin_files(&root);
    let all_manifest_files = scan_android_manifest_files(&root);
    let all_gradle_files = scan_android_gradle_files(&root);
    let all_resource_xml_files = scan_android_resource_xml_files(&root);
    let all_rel_paths: Vec<String> = all_kotlin_files
        .iter()
        .map(|path| scan::rel_path_posix(&root, path))
        .collect();
    let rel_to_abs: HashMap<String, PathBuf> = all_kotlin_files
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
    let kotlin_files: Vec<PathBuf>;
    let manifest_files: Vec<PathBuf>;
    let gradle_files: Vec<PathBuf>;
    let resource_xml_files: Vec<PathBuf>;
    let impacted_count;
    if incremental {
        let changed_existing: HashSet<String> = changed_set
            .iter()
            .filter(|path| rel_to_abs.contains_key(*path))
            .cloned()
            .collect();
        let deps_by_file = collect_kotlin_import_graph(&all_kotlin_files, &root);
        let impacted = expand_impacted_files_by_imports(&changed_existing, &deps_by_file);
        let mut selected_rel_paths = changed_existing.clone();
        selected_rel_paths.extend(impacted.iter().cloned());
        kotlin_files = all_rel_paths
            .iter()
            .filter(|path| selected_rel_paths.contains(*path))
            .map(|path| rel_to_abs[path].clone())
            .collect();
        manifest_files = all_manifest_files
            .iter()
            .filter(|file_path| {
                changed_set
                    .contains(&scan::rel_path_posix(&root, file_path))
            })
            .cloned()
            .collect();
        gradle_files = all_gradle_files
            .iter()
            .filter(|file_path| {
                changed_set
                    .contains(&scan::rel_path_posix(&root, file_path))
            })
            .cloned()
            .collect();
        resource_xml_files = all_resource_xml_files
            .iter()
            .filter(|file_path| {
                changed_set
                    .contains(&scan::rel_path_posix(&root, file_path))
            })
            .cloned()
            .collect();
        impacted_count = impacted.len();
    } else {
        kotlin_files = all_kotlin_files.clone();
        manifest_files = all_manifest_files.clone();
        gradle_files = all_gradle_files.clone();
        resource_xml_files = all_resource_xml_files.clone();
        impacted_count = 0;
    }
    if verbose {
        if incremental {
            println!(
                "[scan] incremental before={} after={} changed={} deleted={} selected(kotlin={},manifest={},gradle={},res_xml={}) impacted_by_imports={}",
                if commit_sha_before.is_empty() { "unknown" } else { &commit_sha_before },
                if commit_sha.is_empty() { "unknown" } else { &commit_sha },
                changed_set.len(),
                deleted_set.len(),
                kotlin_files.len(),
                manifest_files.len(),
                gradle_files.len(),
                resource_xml_files.len(),
                impacted_count,
            );
        }
        println!("[scan] Found {} Kotlin files under {}", kotlin_files.len(), args.root);
    }
    let total_files = kotlin_files.len();

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
    let mut selected_payloads: Vec<FilePayload> = Vec::with_capacity(kotlin_files.len());
    let mut selected_payload_by_rel: HashMap<String, FilePayload> = HashMap::new();
    let mut parse_error_file_count = 0usize;
    let mut parse_error_node_total = 0i64;
    let mut parse_error_examples: Vec<String> = Vec::new();
    for (index, file_path) in kotlin_files.iter().enumerate() {
        if verbose && (index == 0 || (index + 1) % 50 == 0 || index + 1 == total_files) {
            println!("[parse] {}/{}: {}", index + 1, total_files, file_path.display());
        }
        let payload = parse_kotlin_file(file_path, &root)?;
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
    let index_payloads: Vec<FilePayload> = if incremental {
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
            payloads.push(parse_kotlin_file(abs_path, &root)?);
        }
        payloads
    } else {
        selected_payloads.clone()
    };

    // ── Indexes ─────────────────────────────────────────────────────────────
    let index_refs: Vec<&FilePayload> = index_payloads.iter().collect();
    let class_index = assemble::ClassIndex::build(&index_refs);
    let function_index = FunctionIndex::build(&index_refs);
    // file_package_by_path — first-wins theo thứ tự index_payloads.
    let mut file_package_by_path: HashMap<String, Option<String>> = HashMap::new();
    for payload in &index_payloads {
        if let Some(file_def) = &payload.file_def
            && !file_package_by_path.contains_key(&file_def.file_path)
        {
            file_package_by_path.insert(file_def.file_path.clone(), file_def.package_name.clone());
        }
    }

    // ── External classes pass 1 (type edges của selected payloads) ──────────
    let mut external_class_keys: Vec<String> = Vec::new();
    let mut external_classes: HashMap<String, assemble::ExternalClass> = HashMap::new();
    for payload in &selected_payloads {
        for edge in &payload.type_edges {
            let target_name = &edge.target_name;
            let resolved = class_index.resolve_target(target_name, edge.source_package.as_deref());
            if resolved.is_none() && !external_classes.contains_key(target_name) {
                external_classes.insert(
                    target_name.clone(),
                    assemble::ExternalClass {
                        symbol_id: target_name.clone(),
                        name: target_name.clone(),
                    },
                );
                external_class_keys.push(target_name.clone());
            }
        }
    }

    // ── Graph write ─────────────────────────────────────────────────────────
    if let Some(store) = store.take() {
        if verbose {
            println!("[graph] Writing nodes and relations (streaming)...");
        }

        // Manifests → components (+ manifest external classes).
        let mut manifest_defs: Vec<common::AndroidManifestDef> = Vec::new();
        let mut component_defs: Vec<common::AndroidComponentDef> = Vec::new();
        for manifest_path in &manifest_files {
            let (manifest_def, components) =
                common::parse_android_manifest(manifest_path, &root);
            manifest_defs.push(manifest_def);
            for component in components {
                if let Some(class_name) = &component.class_name
                    && !class_index.by_qualified.contains_key(class_name)
                    && !external_classes.contains_key(class_name)
                {
                    external_classes.insert(
                        class_name.clone(),
                        assemble::ExternalClass {
                            symbol_id: class_name.clone(),
                            name: class_name
                                .rsplit('.')
                                .next()
                                .unwrap_or(class_name)
                                .to_string(),
                        },
                    );
                    external_class_keys.push(class_name.clone());
                }
                component_defs.push(component);
            }
        }

        // Inferred components từ EXTENDS (component_candidates).
        let manifest_component_classes: HashSet<String> = component_defs
            .iter()
            .filter_map(|comp| comp.class_name.clone())
            .collect();
        let mut component_infos: HashMap<String, serde_json::Value> = HashMap::new();
        for payload in &index_payloads {
            for class_def in &payload.classes {
                component_infos.insert(
                    class_def.symbol_id.clone(),
                    serde_json::json!({
                        "qualified_name": class_def.qualified_name,
                        "name": class_def.name,
                        "file_path": class_def.file_path,
                        "start_line": class_def.start_line,
                        "end_line": class_def.end_line,
                        "code": class_def.code,
                    }),
                );
            }
        }
        // component_candidates — dict last-wins theo index_payloads.
        let mut component_candidates: Vec<(String, String)> = Vec::new();
        for payload in &index_payloads {
            for edge in &payload.type_edges {
                if edge.rel_type != "EXTENDS" {
                    continue;
                }
                if let Some(component_type) = kotlinparse::infer_component_type(&edge.target_name) {
                    match component_candidates
                        .iter_mut()
                        .find(|(source_id, _)| *source_id == edge.source_id)
                    {
                        Some((_, existing)) => *existing = component_type.to_string(),
                        None => component_candidates.push((
                            edge.source_id.clone(),
                            component_type.to_string(),
                        )),
                    }
                }
            }
        }
        for (class_id, component_type) in &component_candidates {
            let Some(class_def) = component_infos.get(class_id) else {
                continue;
            };
            let qualified = class_def
                .get("qualified_name")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default();
            if qualified.is_empty() || manifest_component_classes.contains(qualified) {
                continue;
            }
            let name = class_def
                .get("name")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .rsplit('.')
                .next()
                .unwrap_or_default()
                .to_string();
            let component_id = common::component_symbol_id(
                component_type,
                Some(qualified),
                class_def
                    .get("file_path")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default(),
                class_def
                    .get("start_line")
                    .and_then(serde_json::Value::as_i64)
                    .unwrap_or(0),
            );
            component_defs.push(common::AndroidComponentDef {
                symbol_id: component_id,
                name,
                component_type: component_type.to_string(),
                class_name: Some(qualified.to_string()),
                exported: None,
                process: None,
                permission: None,
                enabled: None,
                direct_boot_aware: None,
                target_activity: None,
                intent_actions: Vec::new(),
                intent_categories: Vec::new(),
                intent_data: Vec::new(),
                file_path: class_def
                    .get("file_path")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                start_line: class_def
                    .get("start_line")
                    .and_then(serde_json::Value::as_i64)
                    .unwrap_or(0),
                end_line: class_def
                    .get("end_line")
                    .and_then(serde_json::Value::as_i64)
                    .unwrap_or(0),
                code: class_def
                    .get("code")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                summary: String::new(),
                note: String::new(),
            });
        }

        // Resources.
        let (resource_defs, resource_index) =
            scan::collect_android_resources(&root, &resource_xml_files);

        // Gradle.
        let mut gradle_modules: Vec<scan::GradleModuleDef> = Vec::new();
        let mut gradle_dependency_order: Vec<String> = Vec::new();
        let mut gradle_dependencies: HashMap<String, scan::GradleDependencyDef> = HashMap::new();
        let mut gradle_dep_edges: Vec<(String, String, String)> = Vec::new();
        for gradle_path in &gradle_files {
            let (module_def, deps, dep_edges) = scan::parse_gradle_file(gradle_path, &root);
            gradle_modules.push(module_def);
            for dep in deps {
                if !gradle_dependencies.contains_key(&dep.symbol_id) {
                    gradle_dependency_order.push(dep.symbol_id.clone());
                    gradle_dependencies.insert(dep.symbol_id.clone(), dep);
                }
            }
            gradle_dep_edges.extend(dep_edges);
        }

        let selected_refs: Vec<&FilePayload> = selected_payloads.iter().collect();
        let index_refs2: Vec<&FilePayload> = index_payloads.iter().collect();
        let batch_size = extra.neo4j_batch_size.unwrap_or(1000).max(1) as usize;
        let mut writer = LanguageCodeWriter::new(store, extra.neo4j_db.clone(), batch_size, verbose);
        let event_map = match &extra.event_map {
            Some(path) => Some(load_event_map(path)?),
            None => None,
        };
        assemble::write_graph(
            &mut writer,
            &selected_refs,
            &index_refs2,
            &manifest_defs,
            &component_defs,
            &resource_defs,
            &resource_index,
            &gradle_modules,
            &gradle_dependencies,
            &gradle_dependency_order,
            &gradle_dep_edges,
            &external_class_keys,
            &external_classes,
            &class_index,
            &function_index,
            &file_package_by_path,
            event_map.as_ref(),
            &root,
            &project_id,
            &project_name,
            &language,
            &repo,
            &build_system,
        )
        .map_err(|e| format!("[graph] write failed: {e}"))?;
        if verbose {
            println!("[graph] Write complete");
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

fn basename_of(root: &std::path::Path) -> String {
    root.file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string_lossy().to_string())
}

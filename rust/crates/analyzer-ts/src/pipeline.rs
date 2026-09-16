//! Port `build_call_graph` của `tools/ts/ts_analyzer.py` — scan, parse,
//! semantic enrich, indexes + navigator pass, rows, RENDERS, NAVIGATE V2.0,
//! ApiCall bridge, write_all(files_variant=with_jsx).

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::path::Path;

use serde_json::{json, Map, Value};

use cortex_analyzer_framework::semantic::{
    build_usage_index, FunctionRef, SemanticInferenceEngine, UsageCall,
};
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};

use crate::ast::{CallEdge, TsFilePayload};
use crate::deps::{
    collect_ts_import_graph, expand_impacted_files_by_imports, TS_SOURCE_EXTENSIONS,
};
use crate::parser::{
    collect_exports, collect_imports, collect_jsx_tags, extract_file_comment, node_decode,
    parse_file, tree_error_stats,
};
use crate::regexes::ends_with_any;
use crate::symbol::{
    collect_route_configs, extract_api_calls, extract_file_base_url, extract_navigator_declarations,
    file_path_to_route, NAV_CHROME_SUFFIXES_HERE,
};
use crate::traversal::{walk_tree, WalkState};

pub type Row = Map<String, Value>;

/// Python `str.splitlines()` (giữ hành vi; dùng cho deps + payload).
pub fn py_splitlines(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut out: Vec<&str> = Vec::new();
    let mut start = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        let b = bytes[i];
        let boundary_len = match b {
            b'\n' | b'\r' | 0x0b | 0x0c | 0x1c | 0x1d | 0x1e => 1,
            0xc2 if bytes.get(i + 1) == Some(&0x85) => 2,
            0xe2 if bytes.get(i + 1) == Some(&0x80)
                && matches!(bytes.get(i + 2), Some(0xa8) | Some(0xa9)) =>
            {
                3
            }
            _ => 0,
        };
        if boundary_len > 0 {
            if b == b'\r' && bytes.get(i + 1) == Some(&b'\n') {
                out.push(&text[start..i]);
                i += 2;
            } else {
                out.push(&text[start..i]);
                i += boundary_len;
            }
            start = i;
            continue;
        }
        i += if b < 0x80 { 1 } else { (b.leading_ones().min(4) as usize).max(1) };
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

const SCAN_SKIP_DIRS: [&str; 27] = [
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".output",
    ".cache",
    ".parcel-cache",
    "__pycache__",
    "coverage",
    ".nyc_output",
    "test-results",
    ".test-results",
    ".idea",
    ".vscode",
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    ".DS_Store",
    "Thumbs.db",
    "target",
    ".serverless",
];

/// `_scan_ts_files` — skip dirs (union COMMON_SCAN_EXCLUDE) + extensions.
pub fn scan_ts_files(root: &Path) -> Vec<std::path::PathBuf> {
    let mut files: BTreeSet<std::path::PathBuf> = BTreeSet::new();
    walk(root, root, &mut files);
    files.into_iter().collect()
}

fn walk(_root: &Path, dir: &Path, files: &mut BTreeSet<std::path::PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            if SCAN_SKIP_DIRS.contains(&name.as_str())
                || cortex_analyzer_framework::scan::COMMON_SCAN_EXCLUDE.contains(&name.as_str())
                || cortex_analyzer_framework::scan::matches_extra_ignore(&name)
            {
                continue;
            }
            subdirs.push(path);
            continue;
        }
        if TS_SOURCE_EXTENSIONS.iter().any(|ext| name.ends_with(ext)) {
            files.insert(path);
        }
    }
    for sub in subdirs {
        walk(dir, &sub, files);
    }
}

/// `parse_ts_file` + payload wrap (không parse cache — Rust parse inline).
pub fn parse_payload(path: &Path, root: &Path) -> Result<TsFilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let (tree, source) = parse_file(path)?;
    let (has_error, error_nodes) = tree_error_stats(tree.root_node(), &source);
    let snippet = node_decode(&source);
    let end_line = snippet.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment(tree.root_node(), &source);
    let file_summary = file_comment.clone();
    let file_note = crate::traversal::build_note(&snippet, &file_comment, &file_summary);
    let imports = collect_imports(tree.root_node(), &source);
    let exports = collect_exports(tree.root_node(), &source);
    let (jsx_tags, jsx_components) = collect_jsx_tags(tree.root_node(), &source);

    let mut state = WalkState {
        source: &source,
        rel_path: rel_path.clone(),
        namespaces: Vec::new(),
        types: Vec::new(),
        functions: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
        renders: Vec::new(),
        navigates: Vec::new(),
    };
    let mut exported_names: BTreeSet<String> = BTreeSet::new();
    walk_tree(&mut state, tree.root_node(), &[], &[], false, &mut exported_names);
    if !exported_names.is_empty() {
        for func in &mut state.functions {
            if !func.exported && func.scope_name.is_none() && exported_names.contains(&func.name)
            {
                func.exported = true;
            }
        }
        for type_def in &mut state.types {
            if !type_def.exported
                && !type_def.qualified_name.contains("::")
                && exported_names.contains(&type_def.name)
            {
                type_def.exported = true;
            }
        }
    }

    // LLM react-role upgrade — env-gated (REACT_ROLE_LLM_CLASSIFY=1); default no-op.
    // File-level route configs (module scope navigator registration).
    let fn_route_names: BTreeSet<String> = state
        .navigates
        .iter()
        .filter(|nav| nav.nav_method == "__route_config__")
        .map(|nav| nav.target_name.clone())
        .collect();
    for (rname, cname) in collect_route_configs(&snippet) {
        if !fn_route_names.contains(&rname) {
            state.navigates.push(crate::ast::NavigateEdge {
                source_id: format!("file::{rel_path}"),
                target_name: rname,
                nav_method: "__route_config__".to_string(),
                via: cname,
                trigger_type: "user".to_string(),
                guard: None,
            });
        }
    }

    let file_base_url = extract_file_base_url(&snippet);
    let mut api_calls = Vec::new();
    for func in &state.functions {
        if matches!(func.middleware_kind.as_str(), "api_call" | "query_client" | "service") {
            api_calls.extend(extract_api_calls(
                &func.code,
                &func.symbol_id,
                &rel_path,
                func.start_line,
                &file_base_url,
            ));
        }
    }
    let file_navigators = extract_navigator_declarations(&snippet, &rel_path);
    let file_param_lists =
        crate::symbol::extract_param_lists(tree.root_node(), &source, &rel_path);

    Ok(TsFilePayload {
        functions: state.functions,
        calls: state.calls,
        types: state.types,
        namespaces: state.namespaces,
        relations: state.relations,
        renders: state.renders,
        navigates: state.navigates,
        file_def: Some(crate::ast::FileDef {
            file_path: rel_path,
            start_line: 1,
            end_line,
            code: snippet,
            comment: file_comment,
            summary: file_summary,
            note: file_note,
            imports,
            exports,
            jsx_tags,
            jsx_components,
        }),
        parse_meta: crate::ast::ParseMeta {
            has_error,
            error_nodes,
        },
        api_calls,
        navigators: file_navigators,
        param_lists: file_param_lists,
    })
}

// ── Pipeline ────────────────────────────────────────────────────────────────

pub struct TsPipeline {
    pub project_id: String,
    pub project_name: String,
    pub language: String,
    pub repo: String,
    pub build_system: String,
    pub verbose: bool,
    pub incremental: bool,
    pub commit_sha: String,
    pub commit_sha_before: String,
    pub changed_set: BTreeSet<String>,
    pub deleted_set: BTreeSet<String>,
}

struct IndexEntry {
    symbol_id: String,
    scope_name: Option<String>,
    #[allow(dead_code)]
    arity: i64,
}

#[allow(clippy::too_many_lines, clippy::type_complexity)]
pub fn build_call_graph(
    ctx: &TsPipeline,
    root: &Path,
    mut writer: Option<&mut LanguageCodeWriter>,
) -> Result<(), String> {
    let all_scanned_files = scan_ts_files(root);
    let all_rel_paths: Vec<String> = all_scanned_files
        .iter()
        .map(|path| cortex_analyzer_framework::scan::rel_posix(root, path))
        .collect();
    let rel_to_abs: BTreeMap<String, std::path::PathBuf> = all_rel_paths
        .iter()
        .cloned()
        .zip(all_scanned_files.iter().cloned())
        .collect();

    let (selected_rel_paths, selected_files, impacted_by_imports_count): (
        BTreeSet<String>,
        Vec<std::path::PathBuf>,
        usize,
    ) = if ctx.incremental {
        let changed_existing: BTreeSet<String> = ctx
            .changed_set
            .iter()
            .filter(|path| rel_to_abs.contains_key(*path))
            .cloned()
            .collect();
        let deps_by_file = collect_ts_import_graph(&all_scanned_files, root);
        let impacted = expand_impacted_files_by_imports(&changed_existing, &deps_by_file);
        let mut selected = changed_existing;
        selected.extend(impacted.iter().cloned());
        let count = impacted.len();
        let files: Vec<std::path::PathBuf> = all_rel_paths
            .iter()
            .filter(|path| selected.contains(*path))
            .filter_map(|path| rel_to_abs.get(path).cloned())
            .collect();
        (selected, files, count)
    } else {
        (all_rel_paths.iter().cloned().collect(), all_scanned_files.clone(), 0)
    };

    if ctx.verbose {
        if ctx.incremental {
            println!(
                "[scan] incremental before={} after={} changed={} deleted={} selected={}/{} impacted_by_imports={}",
                if ctx.commit_sha_before.is_empty() { "unknown" } else { &ctx.commit_sha_before },
                if ctx.commit_sha.is_empty() { "unknown" } else { &ctx.commit_sha },
                ctx.changed_set.len(),
                ctx.deleted_set.len(),
                selected_files.len(),
                all_scanned_files.len(),
                impacted_by_imports_count,
            );
        }
        println!(
            "[scan] Found {} TypeScript files under {}",
            selected_files.len(),
            root.display()
        );
    }

    // Cleanup changed ∪ deleted — skip khi graphless (embedding pass).
    let cleanup_targets: Vec<String> = ctx
        .changed_set
        .union(&ctx.deleted_set)
        .cloned()
        .collect();
    if ctx.incremental && !cleanup_targets.is_empty() {
        let Some(store) = writer.as_mut().map(|w| w.store.as_mut()) else {
            return Ok(());
        };
        cortex_analyzer_framework::cleanup::cleanup_graph_files(
            store,
            &ctx.project_id,
            &cleanup_targets,
        )
        .map_err(|e| e.to_string())?;
    }

    // Parse selected
    let total_files = selected_files.len();
    let mut selected_payloads: Vec<TsFilePayload> = Vec::with_capacity(total_files);
    let mut selected_payload_by_rel: BTreeMap<String, usize> = BTreeMap::new();
    let mut parse_error_file_count = 0usize;
    let mut parse_error_node_total = 0i64;
    let mut parse_error_examples: Vec<String> = Vec::new();
    for (index, file_path) in selected_files.iter().enumerate() {
        if ctx.verbose && (index == 0 || (index + 1) % 50 == 0 || index + 1 == total_files) {
            println!("[parse] {}/{}: {}", index + 1, total_files, file_path.display());
        }
        let payload = parse_payload(file_path, root)?;
        if let Some(file_def) = &payload.file_def {
            selected_payload_by_rel.insert(file_def.file_path.clone(), selected_payloads.len());
        }
        if payload.parse_meta.has_error || payload.parse_meta.error_nodes > 0 {
            parse_error_file_count += 1;
            parse_error_node_total += payload.parse_meta.error_nodes;
            if let Some(file_def) = &payload.file_def
                && parse_error_examples.len() < 10 {
                    parse_error_examples.push(file_def.file_path.clone());
                }
        }
        selected_payloads.push(payload);
    }
    if ctx.verbose {
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

    // ── Semantic enrichment (return_type/param_types wired) ────────────────
    {
        let function_refs: Vec<FunctionRef<'_>> = selected_payloads
            .iter()
            .flat_map(|payload| payload.functions.iter())
            .map(|func| FunctionRef {
                symbol_id: &func.symbol_id,
                code: &func.code,
            })
            .collect();
        let usage_calls: Vec<UsageCall<'_>> = selected_payloads
            .iter()
            .flat_map(|payload| payload.calls.iter())
            .map(|call| UsageCall {
                caller_id: &call.caller_id,
                callee_id: "",
                callee_name: &call.callee_name,
            })
            .collect();
        let usage_index = build_usage_index(&function_refs, &usage_calls);
        if ctx.verbose {
            let total: usize = selected_payloads
                .iter()
                .map(|payload| payload.functions.len())
                .sum();
            println!("[semantic] Running semantic inference on selected functions...");
            let _ = total;
        }
        let engine = SemanticInferenceEngine::new();
        for payload in &mut selected_payloads {
            for func in &mut payload.functions {
                let enriched = engine.enrich_one(
                    &func.name,
                    &func.code,
                    &func.comment,
                    &func.summary,
                    func.arity,
                    &func.symbol_id,
                    func.exported,
                    &func.return_type,
                    &func.param_types,
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
        if ctx.verbose {
            println!("[frontend-rel] Relationship extraction complete");
        }
    }
    // ── Index payloads (unchanged files trong incremental) ─────────────────
    let index_payloads: Vec<TsFilePayload> = if ctx.incremental && !selected_rel_paths.is_empty() {
        let mut out = Vec::with_capacity(all_rel_paths.len());
        for (index, rel_path) in all_rel_paths.iter().enumerate() {
            if let Some(&pos) = selected_payload_by_rel.get(rel_path) {
                out.push(clone_payload_shallow(&selected_payloads[pos]));
                continue;
            }
            if ctx.verbose && (index == 0 || (index + 1) % 200 == 0 || index + 1 == all_rel_paths.len())
            {
                println!("[index] {}/{}: {}", index + 1, all_rel_paths.len(), rel_path);
            }
            let abs_path = rel_to_abs.get(rel_path).ok_or("rel missing abs")?;
            out.push(parse_payload(abs_path, root)?);
        }
        out
    } else if ctx.incremental {
        Vec::new()
    } else {
        selected_payloads.clone()
    };

    // ── Pass 1: route_config_map ───────────────────────────────────────────
    let mut route_config_map: BTreeMap<String, String> = BTreeMap::new();
    for payload in &index_payloads {
        for nav in &payload.navigates {
            if nav.nav_method == "__route_config__"
                && !nav.target_name.is_empty()
                && !nav.via.is_empty()
            {
                route_config_map
                    .entry(nav.target_name.clone())
                    .or_insert_with(|| nav.via.clone());
            }
        }
    }
    let navigator_registered_screens: BTreeSet<&String> = route_config_map.values().collect();

    // ── Pass 2: function indexes + role upgrade ────────────────────────────
    let mut function_index_by_name: BTreeMap<String, Vec<IndexEntry>> = BTreeMap::new();
    let mut function_index_by_name_arity: BTreeMap<(String, i64), Vec<IndexEntry>> =
        BTreeMap::new();
    let mut render_target_index: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut nav_screen_index: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut nav_route_index: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut func_role_map: BTreeMap<String, String> = BTreeMap::new();

    for payload in &index_payloads {
        let file_path = payload
            .file_def
            .as_ref()
            .map(|f| f.file_path.clone())
            .unwrap_or_default();
        let _ = file_path; // expected_points chỉ dùng cho qdrant (bỏ qua)
        for func in &payload.functions {
            let entry = IndexEntry {
                symbol_id: func.symbol_id.clone(),
                scope_name: func.scope_name.clone(),
                arity: func.arity,
            };
            function_index_by_name
                .entry(func.name.clone())
                .or_default()
                .push(entry);
            function_index_by_name_arity
                .entry((func.name.clone(), func.arity))
                .or_default()
                .push(IndexEntry {
                    symbol_id: func.symbol_id.clone(),
                    scope_name: func.scope_name.clone(),
                    arity: func.arity,
                });

            let mut react_role = func.react_role.clone();
            if navigator_registered_screens.contains(&func.name)
                && (react_role == "component" || react_role.is_empty())
                && !ends_with_any(&func.name, NAV_CHROME_SUFFIXES_HERE)
            {
                react_role = "screen".to_string();
            }
            if react_role == "component" || react_role == "screen" {
                render_target_index
                    .entry(func.name.clone())
                    .or_default()
                    .push(func.symbol_id.clone());
            }
            if react_role == "screen" {
                nav_screen_index
                    .entry(func.name.clone())
                    .or_default()
                    .push(func.symbol_id.clone());
                if let Some(route) = file_path_to_route(&func.file_path) {
                    nav_route_index
                        .entry(route)
                        .or_default()
                        .push(func.symbol_id.clone());
                }
            }
            func_role_map.insert(func.symbol_id.clone(), react_role);
        }
    }

    // ── Rows assembly ──────────────────────────────────────────────────────
    let mut all_projects = Vec::new();
    let mut all_namespaces: Vec<Row> = Vec::new();
    let mut all_files: Vec<Row> = Vec::new();
    let mut all_types: Vec<Row> = Vec::new();
    let mut all_functions: Vec<Row> = Vec::new();
    let mut all_relations: Vec<Row> = Vec::new();
    let mut all_calls: Vec<Row> = Vec::new();
    let mut all_raw_navigates: Vec<crate::ast::NavigateEdge> = Vec::new();
    let mut all_navigators: Vec<&crate::ast::NavigatorDef> = Vec::new();
    let mut all_param_lists: Vec<&crate::ast::ParamListDef> = Vec::new();

    all_projects.push({
        let mut row = Row::new();
        row.insert("id".into(), json!(ctx.project_id));
        row.insert("name".into(), json!(ctx.project_name));
        row.insert("language".into(), json!(ctx.language));
        row.insert("repo".into(), json!(ctx.repo));
        row.insert("root".into(), json!(root.to_string_lossy()));
        row.insert("build_system".into(), json!(ctx.build_system));
        row
    });

    // resolve_callee_id closure data
    let resolve = |index_by_arity: &BTreeMap<(String, i64), Vec<IndexEntry>>,
                   index_by_name: &BTreeMap<String, Vec<IndexEntry>>,
                   call: &CallEdge|
     -> Option<String> {
        let mut candidates: Option<&Vec<IndexEntry>> = None;
        if let Some(arity) = call.callee_arity {
            candidates = index_by_arity.get(&(call.callee_name.clone(), arity));
        }
        let candidates = match candidates {
            Some(candidates) if !candidates.is_empty() => candidates,
            _ => index_by_name.get(&call.callee_name)?,
        };
        if candidates.len() == 1 {
            return Some(candidates[0].symbol_id.clone());
        }
        if let Some(caller_scope) = &call.caller_scope {
            let scoped: Vec<&IndexEntry> = candidates
                .iter()
                .filter(|c| c.scope_name.as_deref() == Some(caller_scope.as_str()))
                .collect();
            if scoped.len() == 1 {
                return Some(scoped[0].symbol_id.clone());
            }
        }
        None
    };

    for payload in &selected_payloads {
        let file_def = payload.file_def.as_ref().ok_or("file_def")?;
        let file_id = file_def.file_path.clone();
        all_files.push({
            let mut row = Row::new();
            row.insert("id".into(), json!(file_id));
            row.insert("path".into(), json!(file_id));
            row.insert("start_line".into(), json!(file_def.start_line));
            row.insert("end_line".into(), json!(file_def.end_line));
            row.insert("code".into(), json!(file_def.code));
            row.insert("comment".into(), json!(file_def.comment));
            row.insert("summary".into(), json!(file_def.summary));
            row.insert("note".into(), json!(file_def.note));
            row.insert("imports".into(), json!(file_def.imports));
            row.insert("exports".into(), json!(file_def.exports));
            row.insert("jsx_tags".into(), json!(file_def.jsx_tags));
            row.insert("jsx_components".into(), json!(file_def.jsx_components));
            insert_common(&mut row, ctx);
            row
        });
        all_relations.push(contains_row(&ctx.project_id, &file_id));
        for ns in &payload.namespaces {
            all_namespaces.push({
                let mut row = Row::new();
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
                insert_common(&mut row, ctx);
                row
            });
            all_relations.push(contains_row(&file_id, &ns.symbol_id));
        }
        for type_def in &payload.types {
            all_types.push({
                let mut row = Row::new();
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
                row.insert("exported".into(), json!(type_def.exported));
                insert_common(&mut row, ctx);
                row
            });
            all_relations.push(contains_row(&file_id, &type_def.symbol_id));
        }
        for func in &payload.functions {
            let upgraded_role = func_role_map
                .get(&func.symbol_id)
                .cloned()
                .unwrap_or_else(|| func.react_role.clone());
            all_functions.push({
                let mut row = Row::new();
                row.insert("id".into(), json!(func.symbol_id));
                row.insert("name".into(), json!(func.name));
                row.insert("qualified_name".into(), json!(func.qualified_name));
                row.insert("kind".into(), json!(func.kind));
                row.insert("scope_name".into(), json!(func.scope_name));
                row.insert("class_name".into(), json!(Value::Null));
                row.insert("package_name".into(), json!(Value::Null));
                row.insert("file_path".into(), json!(func.file_path));
                row.insert("start_line".into(), json!(func.start_line));
                row.insert("end_line".into(), json!(func.end_line));
                row.insert("arity".into(), json!(func.arity));
                row.insert("code".into(), json!(func.code));
                row.insert("comment".into(), json!(func.comment));
                row.insert("summary".into(), json!(func.summary));
                row.insert("note".into(), json!(func.note));
                row.insert("exported".into(), json!(func.exported));
                row.insert("intent".into(), json!(func.intent));
                row.insert("doc_confidence".into(), json!(func.doc_confidence));
                row.insert("inferred_doc".into(), json!(func.inferred_doc));
                row.insert("side_effect".into(), json!(func.side_effect));
                row.insert("return_type".into(), json!(func.return_type));
                row.insert("react_role".into(), json!(upgraded_role));
                row.insert("middleware_kind".into(), json!(func.middleware_kind));
                insert_common(&mut row, ctx);
                row
            });
            all_relations.push(contains_row(&file_id, &func.symbol_id));
        }
        for rel in &payload.relations {
            all_relations.push({
                let mut row = Row::new();
                row.insert("source_id".into(), json!(rel.source_id));
                row.insert("target_id".into(), json!(rel.target_id));
                row.insert("rel_type".into(), json!(rel.rel_type));
                row.insert("properties".into(), Value::Object(rel.properties.clone()));
                row
            });
        }
        for call in &payload.calls {
            if let Some(callee_id) = resolve(
                &function_index_by_name_arity,
                &function_index_by_name,
                call,
            ) {
                all_calls.push({
                    let mut row = Row::new();
                    row.insert("caller_id".into(), json!(call.caller_id));
                    row.insert("callee_id".into(), json!(callee_id));
                    // Explicit scope: journal metadata project_id tương đương.
                    row.insert("project_id".into(), json!(ctx.project_id));
                    row
                });
            }
        }
        // RENDERS resolution
        for render in &payload.renders {
            let candidates = render_target_index
                .get(&render.rendered_name)
                .cloned()
                .unwrap_or_default();
            let renderer_is_screen =
                func_role_map.get(&render.renderer_id).map(String::as_str) == Some("screen");
            if candidates.len() == 1 {
                let cid = &candidates[0];
                if !(renderer_is_screen
                    && func_role_map.get(cid).map(String::as_str) == Some("screen"))
                {
                    all_relations.push(relation_row(
                        &render.renderer_id,
                        cid,
                        "RENDERS",
                        Map::new(),
                    ));
                }
            } else {
                for cid in &candidates {
                    if renderer_is_screen
                        && func_role_map.get(cid).map(String::as_str) == Some("screen")
                    {
                        continue;
                    }
                    all_relations.push(relation_row(
                        &render.renderer_id,
                        cid,
                        "RENDERS",
                        Map::new(),
                    ));
                }
            }
        }
        for nav in &payload.navigates {
            if nav.nav_method != "__route_config__" {
                all_raw_navigates.push(nav.clone());
            }
        }
        all_navigators.extend(payload.navigators.iter());
        all_param_lists.extend(payload.param_lists.iter());
    }

    // Incremental: nav intents/navigators/param_lists từ UNCHANGED files
    let sel_nav_paths: BTreeSet<&str> = selected_payloads
        .iter()
        .filter_map(|p| p.file_def.as_ref())
        .map(|f| f.file_path.as_str())
        .collect();
    for aug in &index_payloads {
        let aug_fp = aug.file_def.as_ref().map(|f| f.file_path.as_str()).unwrap_or("");
        if sel_nav_paths.contains(aug_fp) {
            continue;
        }
        for nav in &aug.navigates {
            if nav.nav_method != "__route_config__" {
                all_raw_navigates.push(nav.clone());
            }
        }
        all_navigators.extend(aug.navigators.iter());
        all_param_lists.extend(aug.param_lists.iter());
    }

    // ── NAVIGATE V2.0: reverse graphs + BFS attribution ────────────────────
    let mut reverse_call_graph: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for call in &all_calls {
        let callee = call.get("callee_id").and_then(Value::as_str).unwrap_or("");
        let caller = call.get("caller_id").and_then(Value::as_str).unwrap_or("");
        if !callee.is_empty() && !caller.is_empty() {
            let list = reverse_call_graph.entry(callee).or_default();
            if !list.contains(&caller) {
                list.push(caller);
            }
        }
    }
    let mut reverse_renders_graph: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for bfs_ip in &index_payloads {
        for r in &bfs_ip.renders {
            if r.renderer_id.is_empty() || r.rendered_name.is_empty() {
                continue;
            }
            if let Some(targets) = render_target_index.get(&r.rendered_name) {
                for target in targets {
                    let list = reverse_renders_graph.entry(target.as_str()).or_default();
                    if !list.contains(&r.renderer_id.as_str()) {
                        list.push(r.renderer_id.as_str());
                    }
                }
            }
        }
    }
    let bfs_sel_paths: BTreeSet<&str> = sel_nav_paths.clone();
    for bfs_ip in &index_payloads {
        let fp = bfs_ip.file_def.as_ref().map(|f| f.file_path.as_str()).unwrap_or("");
        if bfs_sel_paths.contains(fp) {
            continue;
        }
        for call in &bfs_ip.calls {
            // owned String vì reverse graph key cần sống qua vòng lặp payload.
            let Some(callee) = resolve(
                &function_index_by_name_arity,
                &function_index_by_name,
                call,
            ) else {
                continue;
            };
            let caller = call.caller_id.as_str();
            if caller.is_empty() {
                continue;
            }
            // BFS chỉ cần graph sống đến hết pipeline: leak key để tránh
            // ràng buộc lifetime chéo giữa các payload borrow.
            let list = reverse_call_graph.entry(callee.leak()).or_default();
            if !list.contains(&caller) {
                list.push(caller);
            }
        }
    }

    let find_screen_owners = |sid: &str,
                              reverse_call_graph: &BTreeMap<&str, Vec<&str>>,
                              reverse_renders_graph: &BTreeMap<&str, Vec<&str>>,
                              func_role_map: &BTreeMap<String, String>|
     -> Vec<(String, i64)> {
        let max_depth = 6;
        let mut visited: BTreeSet<&str> = BTreeSet::new();
        visited.insert(sid);
        let mut queue: VecDeque<(&str, i64)> = VecDeque::new();
        queue.push_back((sid, 0));
        let mut found: Vec<(String, i64)> = Vec::new();
        while let Some((curr, depth)) = queue.pop_front() {
            if depth >= max_depth {
                continue;
            }
            let mut parents: Vec<&str> = reverse_renders_graph
                .get(curr)
                .cloned()
                .unwrap_or_default();
            for p in reverse_call_graph.get(curr).cloned().unwrap_or_default() {
                if !parents.contains(&p) {
                    parents.push(p);
                }
            }
            for parent in parents {
                if visited.contains(parent) {
                    continue;
                }
                visited.insert(parent);
                if func_role_map.get(parent).map(String::as_str) == Some("screen") {
                    found.push((parent.to_string(), depth + 1));
                }
                queue.push_back((parent, depth + 1));
            }
        }
        found
    };

    let role_to_via = |role: &str| match role {
        "component" => "component",
        "hook" => "hook",
        _ => "wrapped",
    };

    let mut emitted_nav: BTreeSet<(String, String)> = BTreeSet::new();
    for nav in &all_raw_navigates {
        let source_id = nav.source_id.as_str();
        let target_name = nav.target_name.as_str();
        let method = nav.nav_method.as_str();
        let trigger_type = if nav.trigger_type.is_empty() {
            "user"
        } else {
            nav.trigger_type.as_str()
        };
        let guard = nav.guard.clone().unwrap_or_default();

        // Tier 1..4 target resolution
        let mut target_id: Option<String> = None;
        let mut target_confidence = 0.0f64;
        let t1 = nav_screen_index.get(target_name).cloned().unwrap_or_default();
        if !t1.is_empty() {
            target_id = Some(t1[0].clone());
            target_confidence = if t1.len() == 1 { 1.0 } else { 0.7 };
        }
        if target_id.is_none()
            && let Some(comp_name) = route_config_map.get(target_name) {
                let t2 = nav_screen_index.get(comp_name).cloned().unwrap_or_default();
                if !t2.is_empty() {
                    target_id = Some(t2[0].clone());
                    target_confidence = if t2.len() == 1 { 0.9 } else { 0.65 };
                }
            }
        if target_id.is_none() && target_name.contains('/') {
            let normalized = target_name.trim_end_matches('/');
            let t3 = nav_route_index
                .get(normalized)
                .or_else(|| nav_route_index.get(normalized.trim_start_matches('/')))
                .cloned()
                .unwrap_or_default();
            if !t3.is_empty() {
                target_id = Some(t3[0].clone());
                target_confidence = if t3.len() == 1 { 0.85 } else { 0.6 };
            }
        }
        if target_id.is_none() {
            let t4 = nav_screen_index.get(target_name).cloned().unwrap_or_default();
            if !t4.is_empty() {
                target_id = Some(t4[0].clone());
                target_confidence = if t4.len() == 1 { 0.5 } else { 0.3 };
            }
        }
        let Some(target_id) = target_id else { continue };

        let source_role = func_role_map.get(source_id).cloned().unwrap_or_default();
        let mut screen_owners = find_screen_owners(
            source_id,
            &reverse_call_graph,
            &reverse_renders_graph,
            &func_role_map,
        );
        if screen_owners.is_empty() {
            if source_role == "screen" {
                screen_owners = vec![(source_id.to_string(), 0)];
            } else {
                continue;
            }
        }
        for (screen_id, call_depth) in screen_owners {
            let pair = (screen_id.clone(), target_id.clone());
            if emitted_nav.contains(&pair) {
                continue;
            }
            emitted_nav.insert(pair);
            let edge_via = if call_depth == 0 {
                "direct"
            } else {
                role_to_via(&source_role)
            };
            let call_path_score = (0.5f64).max(1.0 - 0.15 * call_depth as f64);
            let confidence = crate::pipeline::py_round3(target_confidence * call_path_score);
            let mut props = Map::new();
            props.insert("method".into(), json!(method));
            props.insert("target".into(), json!(target_name));
            props.insert("via".into(), json!(edge_via));
            props.insert("trigger_type".into(), json!(trigger_type));
            props.insert("guard".into(), json!(guard));
            props.insert("call_depth".into(), json!(call_depth));
            props.insert("confidence".into(), json!(confidence));
            all_relations.push(relation_row(&screen_id, &target_id, "NAVIGATE", props));
        }
    }

    // ── Navigator + has_routes + param_list write rows ─────────────────────
    let mut param_list_by_name: BTreeMap<&str, &crate::ast::ParamListDef> = BTreeMap::new();
    for pl in &all_param_lists {
        param_list_by_name.insert(pl.name.as_str(), pl);
    }
    let mut nav_write_rows: Vec<Row> = Vec::new();
    for nav in &all_navigators {
        let mut row = Row::new();
        row.insert("id".into(), json!(nav.symbol_id));
        row.insert("var_name".into(), json!(nav.var_name));
        row.insert("factory".into(), json!(nav.factory));
        row.insert("nav_type".into(), json!(nav.nav_type));
        row.insert("param_list_ref".into(), json!(nav.param_list_ref));
        row.insert("file_path".into(), json!(nav.file_path));
        row.insert("start_line".into(), json!(nav.start_line));
        row.insert("project_id".into(), json!(ctx.project_id));
        row.insert("project_name".into(), json!(ctx.project_name));
        nav_write_rows.push(row);
    }
    let mut has_routes_rows: Vec<Row> = Vec::new();
    for nav in &all_navigators {
        let pl = param_list_by_name.get(nav.param_list_ref.as_str());
        for (route_name, comp_name) in &nav.routes {
            if let Some(screen_ids) = nav_screen_index.get(comp_name) {
                for screen_id in screen_ids {
                    let mut row = Row::new();
                    row.insert("navigator_id".into(), json!(nav.symbol_id));
                    row.insert("screen_id".into(), json!(screen_id));
                    row.insert("route_name".into(), json!(route_name));
                    row.insert(
                        "param_schema".into(),
                        json!(pl.and_then(|pl| pl.routes.get(route_name)).cloned().unwrap_or_default()),
                    );
                    has_routes_rows.push(row);
                }
            }
        }
    }
    let mut param_list_write_rows: Vec<Row> = Vec::new();
    for pl in &all_param_lists {
        for (route_name, type_str) in &pl.routes {
            let mut row = Row::new();
            row.insert("symbol_id".into(), json!(pl.symbol_id));
            row.insert("name".into(), json!(pl.name));
            row.insert("file_path".into(), json!(pl.file_path));
            row.insert("route_name".into(), json!(route_name));
            row.insert("type_str".into(), json!(type_str));
            row.insert("project_id".into(), json!(ctx.project_id));
            param_list_write_rows.push(row);
        }
    }

    let Some(writer) = writer else {
        // Graphless (CORTEX_DISABLE_GRAPH): vẫn in [SCAN_RESULT] như Python.
        let fn_total: usize = selected_payloads
            .iter()
            .map(|payload| payload.functions.len())
            .sum();
        println!(
            "[SCAN_RESULT] parser={} files={} functions={} classes=0",
            ctx.language,
            selected_payloads.len(),
            fn_total
        );
        return Ok(());
    };
    writer.ensure_schema().map_err(|e| e.to_string())?;
    writer
        .write_all(&WriteAllPayload {
            projects: &all_projects,
            packages: &[],
            namespaces: &all_namespaces,
            files: &all_files,
            classes: &[],
            types: &all_types,
            function_types: &[],
            functions: &all_functions,
            fields: &[],
            aliases: &[],
            templates: &[],
            relations: &all_relations,
            calls: &all_calls,
            calls_with_site: &[],
            properties: &[],
            events: &[],
            interfaces: &[],
            enums: &[],
            constants: &[],
            variables: &[],
            navigators: &nav_write_rows,
            has_routes: &has_routes_rows,
            param_lists: &param_list_write_rows,
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
        .map_err(|e| e.to_string())?;
    // Phase-02: capture embedding categories for orchestrator
    // (plan `260916-1432-legacy-17-vector-emit`). Emission deferred to caller.
    // TS uses inline WriteAllPayload above; deferred to phase-03 widening.

    // ── ApiCall nodes + CALLS_API edges ────────────────────────────────────
    let mut all_api_calls: Vec<Row> = Vec::new();
    for payload in &selected_payloads {
        for ac in &payload.api_calls {
            let mut row = Row::new();
            row.insert("symbol_id".into(), json!(ac.symbol_id));
            row.insert("caller_function_id".into(), json!(ac.caller_function_id));
            row.insert("url_pattern".into(), json!(ac.url_pattern));
            row.insert("raw_url".into(), json!(ac.raw_url));
            row.insert("http_method".into(), json!(ac.http_method));
            row.insert("base_url_ref".into(), json!(ac.base_url_ref));
            row.insert("file_path".into(), json!(ac.file_path));
            row.insert("start_line".into(), json!(ac.start_line));
            row.insert("confidence".into(), json!(ac.confidence));
            row.insert("project_id".into(), json!(ctx.project_id));
            row.insert("project_name".into(), json!(ctx.project_name));
            all_api_calls.push(row);
        }
    }
    if all_api_calls.is_empty() {
        // [SCAN_RESULT] van phai in — khop flow Python.
        let fn_total: usize = selected_payloads
            .iter()
            .map(|payload| payload.functions.len())
            .sum();
        println!(
            "[SCAN_RESULT] parser={} files={} functions={} classes=0",
            ctx.language,
            selected_payloads.len(),
            fn_total
        );
        return Ok(());
    }
    let api_query = r#"
UNWIND $rows AS row
MERGE (ac:ApiCall {symbol_id: row.symbol_id})
SET ac.url_pattern    = row.url_pattern,
    ac.http_method    = row.http_method,
    ac.raw_url        = row.raw_url,
    ac.base_url_ref   = row.base_url_ref,
    ac.file_path      = row.file_path,
    ac.start_line     = row.start_line,
    ac.confidence     = row.confidence,
    ac.project_id     = row.project_id,
    ac.project_name   = row.project_name
RETURN count(ac) AS count
"#;
    let calls_api_query = r#"
UNWIND $rows AS row
MATCH (f:Function {symbol_id: row.caller_function_id})
MATCH (ac:ApiCall  {symbol_id: row.symbol_id})
MERGE (f)-[:CALLS_API]->(ac)
"#;
    let saved_batch_size = writer.batch_size;
    writer.batch_size = 200;
    writer
        .write_nodes_batch("ts:ApiCall", api_query, &all_api_calls)
        .map_err(|e| e.to_string())?;
    writer
        .write_nodes_batch("ts:CALLS_API", calls_api_query, &all_api_calls)
        .map_err(|e| e.to_string())?;
    writer.batch_size = saved_batch_size;
    if ctx.verbose {
        println!("[graph] ApiCall nodes written: {}", all_api_calls.len());
    }

    let fn_total: usize = selected_payloads
        .iter()
        .map(|payload| payload.functions.len())
        .sum();
    // Python: classes = sum(len(p.get("classes"))) — ts payload không có classes → 0.
    println!(
        "[SCAN_RESULT] parser={} files={} functions={} classes=0",
        ctx.language,
        selected_payloads.len(),
        fn_total
    );
    if ctx.verbose {
        println!("[done] done");
    }
    Ok(())
}

fn insert_common(row: &mut Row, ctx: &TsPipeline) {
    row.insert("project_id".into(), json!(ctx.project_id));
    row.insert("project_name".into(), json!(ctx.project_name));
    row.insert("language".into(), json!(ctx.language));
    row.insert("repo".into(), json!(ctx.repo));
    row.insert("build_system".into(), json!(ctx.build_system));
}

fn contains_row(source_id: &str, target_id: &str) -> Row {
    let mut row = Row::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("rel_type".into(), json!("CONTAINS"));
    row.insert("properties".into(), json!({}));
    row
}

fn relation_row(source_id: &str, target_id: &str, rel_type: &str, properties: Map<String, Value>) -> Row {
    let mut row = Row::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("properties".into(), Value::Object(properties));
    row
}

/// Python `round(x, 3)`.
pub fn py_round3(x: f64) -> f64 {
    let text = format!("{x:.3}");
    text.parse().unwrap_or(x)
}

/// Clone payload cho index_payloads (deep đủ dùng).
pub fn clone_payload_shallow(payload: &TsFilePayload) -> TsFilePayload {
    payload.clone()
}

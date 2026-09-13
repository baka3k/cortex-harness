//! Port phần pipeline `build_call_graph` của `tools/js/js_analyzer.py`:
//! scan (`_scan_js_files` với skip-list riêng), incremental selection qua
//! import-graph (`_collect_js_import_graph` + BFS reverse deps), cleanup
//! changed∪deleted, parse payloads, function index + `resolve_callee_id`,
//! JSON rows cho `LanguageCodeWriter::write_all` (`use_full_writers=True`,
//! `files_variant="with_jsx"`), `[SCAN_RESULT]`.

use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::time::Instant;

use regex::Regex;
use serde_json::{json, Value};
use std::sync::OnceLock;

use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::scan::{matches_extra_ignore, rel_posix, COMMON_SCAN_EXCLUDE};
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};
use cortex_graph_writer::store::GraphStore;

use crate::jsparse::{self, FilePayload, Row};
use crate::JsArgs;

/// `_SCAN_SKIP_DIRS` của js_analyzer (union COMMON_SCAN_EXCLUDE tại scan).
const SCAN_SKIP_DIRS: [&str; 31] = [
    // Version control
    ".git",
    ".hg",
    ".svn",
    // Node.js package manager
    "node_modules",
    // Build outputs
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".output",
    // Cache
    ".cache",
    ".parcel-cache",
    ".eslintcache",
    ".stylelintcache",
    "__pycache__",
    // Testing
    "coverage",
    ".nyc_output",
    "test-results",
    ".test-results",
    // IDE
    ".idea",
    ".vscode",
    // Temporary
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    // OS specific
    ".DS_Store",
    "Thumbs.db",
    // Build artifacts
    "target",
    ".serverless",
    // Environment directories
    ".env",
    ".env.local",
];

fn skip_dir(name: &str) -> bool {
    SCAN_SKIP_DIRS.contains(&name)
        || COMMON_SCAN_EXCLUDE.contains(&name)
        || matches_extra_ignore(name)
}

/// `_scan_js_files` — walk root, prune skip dirs, thu file đúng extension,
/// sorted theo full path (os.walk topdown + sorted(files)).
pub fn scan_js_files(root: &Path) -> Vec<PathBuf> {
    let mut files = BTreeSet::new();
    walk(root, root, &mut files);
    files.into_iter().collect()
}

fn walk(_root: &Path, dir: &Path, files: &mut BTreeSet<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let is_dir = path.is_dir(); // os.walk dùng os.path.isdir (follow symlink)
        if is_dir {
            // followlinks=False: symlink dir vẫn bị lọc skip nhưng không walk.
            if !skip_dir(&name) && !path.is_symlink() {
                subdirs.push(path);
            }
            continue;
        }
        if jsparse::JS_SOURCE_EXTENSIONS.iter().any(|ext| name.ends_with(ext)) {
            files.insert(path);
        }
    }
    for sub in subdirs {
        walk(_root, &sub, files);
    }
}

// ── Incremental import-graph (regex-based, dùng cho impacted expansion) ─────

/// `_extract_module_specifiers_from_text`.
fn extract_module_specifiers_from_text(text: &str) -> Vec<String> {
    static IMPORT_RE: OnceLock<Regex> = OnceLock::new();
    static REQUIRE_RE: OnceLock<Regex> = OnceLock::new();
    let import_re = IMPORT_RE.get_or_init(|| {
        Regex::new(r#"^(?:import|export)\s+(?:.+?\s+from\s+)?["'](?P<spec>[^"']+)["']"#)
            .expect("import specifier regex")
    });
    let require_re = REQUIRE_RE.get_or_init(|| {
        Regex::new(r#"(?:require|import)\(\s*["'](?P<spec>[^"']+)["']\s*\)"#)
            .expect("require specifier regex")
    });
    let mut specifiers = Vec::new();
    for raw_line in text.split('\n') {
        let line = raw_line.trim();
        if line.is_empty()
            || line.starts_with("//")
            || line.starts_with("/*")
            || line.starts_with('*')
        {
            continue;
        }
        if let Some(caps) = import_re.captures(line)
            && let Some(m) = caps.name("spec")
        {
            specifiers.push(m.as_str().to_string());
        }
        for caps in require_re.captures_iter(line) {
            if let Some(m) = caps.name("spec") {
                specifiers.push(m.as_str().to_string());
            }
        }
    }
    specifiers
}

/// `os.path.normpath` (posix) — collapse `//`, `.`, `..`.
fn normpath(path: &str) -> String {
    let absolute = path.starts_with('/');
    let mut parts: Vec<&str> = Vec::new();
    for component in path.split('/') {
        match component {
            "" | "." => {}
            ".." => {
                if !parts.is_empty() && *parts.last().unwrap() != ".." {
                    parts.pop();
                } else if !absolute {
                    parts.push("..");
                }
            }
            other => parts.push(other),
        }
    }
    let mut out = parts.join("/");
    if absolute {
        out = format!("/{out}");
    }
    if out.is_empty() {
        out = ".".to_string();
    }
    out
}

/// `os.path.splitext` — extension sau slash cuối, dot đầu file không tính.
fn splitext(path: &str) -> (String, String) {
    let (dir, file) = match path.rfind('/') {
        Some(index) => (&path[..=index], &path[index + 1..]),
        None => ("", path),
    };
    match file.rfind('.') {
        Some(0) | None => (path.to_string(), String::new()),
        Some(index) => (format!("{dir}{}", &file[..index]), file[index..].to_string()),
    }
}

/// `_resolve_js_module_specifier`.
fn resolve_js_module_specifier(
    source_rel_path: &str,
    specifier: &str,
    file_set: &std::collections::HashSet<String>,
) -> Option<String> {
    if specifier.is_empty() || !specifier.starts_with('.') {
        return None;
    }
    let base_dir = match source_rel_path.rfind('/') {
        Some(index) => &source_rel_path[..index],
        None => "",
    };
    let joined = if base_dir.is_empty() {
        specifier.to_string()
    } else {
        format!("{base_dir}/{specifier}")
    };
    let candidate = normpath(&joined);
    if file_set.contains(&candidate) {
        return Some(candidate);
    }
    let (root_candidate, ext) = splitext(&candidate);
    let mut probes: Vec<String> = Vec::new();
    if !ext.is_empty() {
        probes.push(candidate.clone());
    } else {
        for suffix in jsparse::JS_SOURCE_EXTENSIONS {
            probes.push(format!("{candidate}{suffix}"));
        }
    }
    for suffix in jsparse::JS_SOURCE_EXTENSIONS {
        probes.push(format!("{candidate}/index{suffix}"));
    }
    for path in probes {
        let normalized = normpath(&path);
        if file_set.contains(&normalized) {
            return Some(normalized);
        }
    }
    if jsparse::JS_SOURCE_EXTENSIONS.contains(&ext.as_str()) {
        return None;
    }
    if ext.is_empty() {
        for fallback_ext in [".js", ".jsx"] {
            let normalized = format!("{root_candidate}{fallback_ext}");
            if file_set.contains(&normalized) {
                return Some(normalized);
            }
        }
    }
    None
}

/// `_collect_js_import_graph` — rel path → sorted deps trong file set.
fn collect_js_import_graph(all_js_files: &[PathBuf], root: &Path) -> BTreeMap<String, Vec<String>> {
    use cortex_analyzer_framework::ts::decode_ignore;
    let rel_paths: Vec<String> = all_js_files.iter().map(|path| rel_posix(root, path)).collect();
    let file_set: std::collections::HashSet<String> = rel_paths.iter().cloned().collect();
    let mut deps_by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (abs_path, rel_path) in all_js_files.iter().zip(&rel_paths) {
        let mut resolved: BTreeSet<String> = BTreeSet::new();
        let text = match std::fs::read(abs_path) {
            Ok(bytes) => decode_ignore(&bytes),
            Err(_) => {
                deps_by_file.insert(rel_path.clone(), Vec::new());
                continue;
            }
        };
        for specifier in extract_module_specifiers_from_text(&text) {
            if let Some(dep) = resolve_js_module_specifier(rel_path, &specifier, &file_set) {
                resolved.insert(dep);
            }
        }
        resolved.remove(rel_path);
        deps_by_file.insert(rel_path.clone(), resolved.into_iter().collect());
    }
    deps_by_file
}

/// `_expand_impacted_files_by_imports` — BFS trên reverse deps.
fn expand_impacted_files_by_imports(
    changed_existing: &BTreeSet<String>,
    deps_by_file: &BTreeMap<String, Vec<String>>,
) -> BTreeSet<String> {
    let mut reverse_map: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
    for (source, deps) in deps_by_file {
        for dep in deps {
            reverse_map.entry(dep.as_str()).or_default().insert(source.as_str());
        }
    }
    let mut impacted: BTreeSet<String> = BTreeSet::new();
    let mut queue: VecDeque<&str> = changed_existing.iter().map(String::as_str).collect();
    let mut seen: std::collections::HashSet<&str> =
        changed_existing.iter().map(String::as_str).collect();
    while let Some(current) = queue.pop_front() {
        if let Some(dependents) = reverse_map.get(current) {
            for dependent in dependents {
                if seen.contains(dependent) {
                    continue;
                }
                seen.insert(dependent);
                impacted.insert(dependent.to_string());
                queue.push_back(dependent);
            }
        }
    }
    impacted
}

// ── Function index + resolve_callee_id ──────────────────────────────────────

#[derive(Clone)]
struct FuncEntry {
    symbol_id: String,
    scope_name: Option<String>,
}

/// Thứ tự push giữ insertion order (mảng Vec theo key như Python dict).
#[derive(Default)]
pub struct FunctionIndex {
    by_name: BTreeMap<String, Vec<FuncEntry>>,
    by_name_arity: BTreeMap<(String, i64), Vec<FuncEntry>>,
}

impl FunctionIndex {
    pub fn build<'a>(payloads: impl IntoIterator<Item = &'a FilePayload>) -> Self {
        let mut index = FunctionIndex::default();
        for payload in payloads {
            for func in &payload.functions {
                let entry = FuncEntry {
                    symbol_id: func.symbol_id.clone(),
                    scope_name: func.scope_name.clone(),
                };
                index
                    .by_name
                    .entry(func.name.clone())
                    .or_default()
                    .push(entry.clone());
                index
                    .by_name_arity
                    .entry((func.name.clone(), func.arity))
                    .or_default()
                    .push(entry);
            }
        }
        index
    }

    /// `resolve_callee_id` — arity candidates → by_name → single → caller scope.
    fn resolve(&self, call: &jsparse::CallEdge) -> Option<String> {
        let candidates = match self
            .by_name_arity
            .get(&(call.callee_name.clone(), call.callee_arity))
        {
            Some(candidates) if !candidates.is_empty() => candidates,
            _ => self.by_name.get(&call.callee_name)?,
        };
        if candidates.len() == 1 {
            return Some(candidates[0].symbol_id.clone());
        }
        if let Some(caller_scope) = &call.caller_scope {
            let scoped: Vec<&FuncEntry> = candidates
                .iter()
                .filter(|candidate| candidate.scope_name.as_deref() == Some(caller_scope.as_str()))
                .collect();
            if scoped.len() == 1 {
                return Some(scoped[0].symbol_id.clone());
            }
        }
        None
    }
}

// ── Graph rows (asdict shape của build_call_graph) ──────────────────────────

pub struct GraphRows {
    pub projects: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub files: Vec<Row>,
    pub types: Vec<Row>,
    pub functions: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

fn contains_row(source_id: &str, target_id: &str) -> Row {
    let mut row = Row::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("rel_type".into(), json!("CONTAINS"));
    row.insert("properties".into(), json!({}));
    row
}

/// Dựng toàn bộ node/rel rows như `build_call_graph` (graph write pass).
#[allow(clippy::too_many_arguments)]
pub fn assemble_graph(
    payloads: &[FilePayload],
    index: &FunctionIndex,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
    root: &str,
) -> GraphRows {
    let mut projects: Vec<Row> = Vec::new();
    let mut namespaces: Vec<Row> = Vec::new();
    let mut files: Vec<Row> = Vec::new();
    let mut types: Vec<Row> = Vec::new();
    let mut functions: Vec<Row> = Vec::new();
    let mut relations: Vec<Row> = Vec::new();
    let mut calls: Vec<Row> = Vec::new();

    projects.push({
        let mut row = Row::new();
        row.insert("id".into(), json!(project_id));
        row.insert("name".into(), json!(project_name));
        row.insert("language".into(), json!(language));
        row.insert("repo".into(), json!(repo));
        row.insert("root".into(), json!(root));
        row.insert("build_system".into(), json!(build_system));
        row
    });

    for payload in payloads {
        let file_def = payload.file_def.as_ref().expect("file_def");
        let file_id = &file_def.file_path;
        files.push({
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
            row.insert("project_id".into(), json!(project_id));
            row.insert("project_name".into(), json!(project_name));
            row.insert("language".into(), json!(language));
            row.insert("repo".into(), json!(repo));
            row.insert("build_system".into(), json!(build_system));
            row
        });
        relations.push(contains_row(project_id, file_id));
        for ns in &payload.namespaces {
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
            row.insert("project_id".into(), json!(project_id));
            row.insert("project_name".into(), json!(project_name));
            row.insert("language".into(), json!(language));
            row.insert("repo".into(), json!(repo));
            row.insert("build_system".into(), json!(build_system));
            namespaces.push(row);
            relations.push(contains_row(file_id, &ns.symbol_id));
        }
        for type_def in &payload.types {
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
            row.insert("project_id".into(), json!(project_id));
            row.insert("project_name".into(), json!(project_name));
            row.insert("language".into(), json!(language));
            row.insert("repo".into(), json!(repo));
            row.insert("build_system".into(), json!(build_system));
            types.push(row);
            relations.push(contains_row(file_id, &type_def.symbol_id));
        }
        for func in &payload.functions {
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
            row.insert("project_id".into(), json!(project_id));
            row.insert("project_name".into(), json!(project_name));
            row.insert("language".into(), json!(language));
            row.insert("repo".into(), json!(repo));
            row.insert("build_system".into(), json!(build_system));
            functions.push(row);
            relations.push(contains_row(file_id, &func.symbol_id));
        }
        for rel in &payload.relations {
            let mut row = Row::new();
            row.insert("source_id".into(), json!(rel.source_id));
            row.insert("target_id".into(), json!(rel.target_id));
            row.insert("rel_type".into(), json!(rel.rel_type));
            row.insert("properties".into(), json!({}));
            relations.push(row);
        }
        for call in &payload.calls {
            if let Some(callee_id) = index.resolve(call) {
                let mut row = Row::new();
                row.insert("caller_id".into(), json!(call.caller_id));
                row.insert("callee_id".into(), json!(callee_id));
                // CALLS project-scope contract (writer `require_call_project_scope`):
                // Python reference chạy qua orchestrator/journal env nên writer
                // tự bơm project_id từ journal metadata; Rust supply trực tiếp
                // đúng giá trị đó (tiền lệ: analyzer-python resolve.rs).
                row.insert("project_id".into(), json!(project_id));
                calls.push(row);
            }
        }
    }

    GraphRows {
        projects,
        namespaces,
        files,
        types,
        functions,
        relations,
        calls,
    }
}

// ── build_call_graph ────────────────────────────────────────────────────────

/// Port `build_call_graph` — store là `code_writer` (None = graphless).
#[allow(clippy::too_many_arguments)]
pub fn build_call_graph(
    args: &JsArgs,
    root: &Path,
    changed_manifest_files: &[String],
    deleted_manifest_files: &[String],
    mut store: Option<Box<dyn GraphStore>>,
) -> Result<(), String> {
    let start_time = Instant::now();
    let verbose = args.verbose;
    let project_id = args.project_id_or_root();
    let project_name = args.project_name_or_id();
    let language = args
        .language
        .clone()
        .unwrap_or_else(|| "javascript".to_string());
    let repo = args.repo.clone().unwrap_or_else(|| root.to_string_lossy().to_string());
    let build_system = args.build_system.clone().unwrap_or_default();
    let root_raw = args.root.clone();

    let all_scanned_files = scan_js_files(root);
    let all_rel_paths: Vec<String> = all_scanned_files
        .iter()
        .map(|path| rel_posix(root, path))
        .collect();
    let rel_to_abs: HashMap<String, PathBuf> = all_rel_paths
        .iter()
        .cloned()
        .zip(all_scanned_files.iter().cloned())
        .collect();
    let changed_set: BTreeSet<String> = changed_manifest_files
        .iter()
        .map(|item| item.replace('\\', "/"))
        .filter(|item| !item.is_empty())
        .collect();
    let deleted_set: BTreeSet<String> = deleted_manifest_files
        .iter()
        .map(|item| item.replace('\\', "/"))
        .filter(|item| !item.is_empty())
        .collect();

    let selected_rel_paths: BTreeSet<String>;
    let selected_files: Vec<PathBuf>;
    let mut impacted_by_imports_count = 0usize;
    if args.incremental {
        let changed_existing: BTreeSet<String> = changed_set
            .iter()
            .filter(|path| rel_to_abs.contains_key(*path))
            .cloned()
            .collect();
        let deps_by_file = collect_js_import_graph(&all_scanned_files, root);
        let impacted = expand_impacted_files_by_imports(&changed_existing, &deps_by_file);
        selected_rel_paths = changed_existing.union(&impacted).cloned().collect();
        impacted_by_imports_count = impacted.len();
        selected_files = all_rel_paths
            .iter()
            .filter(|path| selected_rel_paths.contains(*path))
            .map(|path| rel_to_abs[path].clone())
            .collect();
    } else {
        selected_rel_paths = all_rel_paths.iter().cloned().collect();
        selected_files = all_scanned_files.clone();
    }
    if verbose {
        if args.incremental {
            println!(
                "[scan] incremental before={} after={} changed={} deleted={} selected={}/{} impacted_by_imports={}",
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
                changed_set.len(),
                deleted_set.len(),
                selected_files.len(),
                all_scanned_files.len(),
                impacted_by_imports_count,
            );
        }
        println!(
            "[scan] Found {} JavaScript files under {}",
            selected_files.len(),
            root_raw
        );
    }
    let total_files = selected_files.len();

    // ── Incremental cleanup (changed ∪ deleted) ─────────────────────────────
    let cleanup_targets: Vec<String> = changed_set.union(&deleted_set).cloned().collect();
    if args.incremental
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
                "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
            );
        }
    }

    // ── Parse selected payloads ─────────────────────────────────────────────
    let mut selected_payloads: Vec<FilePayload> = Vec::with_capacity(total_files);
    let mut selected_payload_by_rel: HashMap<String, usize> = HashMap::new();
    let mut parse_error_file_count = 0i64;
    let mut parse_error_node_total = 0i64;
    let mut parse_error_examples: Vec<String> = Vec::new();
    for (idx, file_path) in selected_files.iter().enumerate() {
        let index = idx + 1;
        if verbose && (index == 1 || index % 50 == 0 || index == total_files) {
            println!("[parse] {index}/{total_files}: {}", file_path.display());
        }
        let payload = parse_js_file_checked(file_path, root)?;
        if let Some(file_def) = &payload.file_def {
            let rel_path = &file_def.file_path;
            if !rel_path.is_empty() {
                selected_payload_by_rel.insert(rel_path.clone(), selected_payloads.len());
            }
            if payload.parse_meta.has_error || payload.parse_meta.error_nodes > 0 {
                parse_error_file_count += 1;
                parse_error_node_total += payload.parse_meta.error_nodes;
                if !rel_path.is_empty() && parse_error_examples.len() < 10 {
                    parse_error_examples.push(rel_path.clone());
                }
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

    // ── Index payloads (incremental: toàn bộ file set) ──────────────────────
    let index_payloads: Vec<FilePayload> =
        if args.incremental && !selected_rel_paths.is_empty() {
            let mut out: Vec<FilePayload> = Vec::with_capacity(all_rel_paths.len());
            for (index, rel_path) in all_rel_paths.iter().enumerate() {
                let index = index + 1;
                if let Some(&payload_index) = selected_payload_by_rel.get(rel_path) {
                    out.push(selected_payloads[payload_index].clone());
                    continue;
                }
                let abs_path = &rel_to_abs[rel_path];
                if verbose && (index == 1 || index % 200 == 0 || index == all_rel_paths.len()) {
                    println!("[index] {}/{}: {rel_path}", index, all_rel_paths.len());
                }
                out.push(parse_js_file_checked(abs_path, root)?);
            }
            out
        } else {
            Vec::new() // full mode: index chạy trực tiếp trên selected payloads
        };

    let function_index = if args.incremental {
        FunctionIndex::build(index_payloads.iter())
    } else {
        FunctionIndex::build(selected_payloads.iter())
    };

    // ── Rows + write ────────────────────────────────────────────────────────
    if let Some(store) = store.take() {
        if verbose {
            println!("[graph] Writing nodes and relations (streaming)...");
        }
        let rows = assemble_graph(
            &selected_payloads,
            &function_index,
            &project_id,
            &project_name,
            &language,
            &repo,
            &build_system,
            &root_raw,
        );
        let database = args
            .neo4j_db
            .clone()
            .filter(|value| !value.is_empty());
        let batch_size = args.neo4j_batch_size.max(1) as usize;
        let mut writer = LanguageCodeWriter::new(store, database, batch_size, verbose);
        let payload = WriteAllPayload {
            projects: &rows.projects,
            packages: &[],
            namespaces: &rows.namespaces,
            files: &rows.files,
            classes: &[],
            types: &rows.types,
            function_types: &[],
            functions: &rows.functions,
            fields: &[],
            aliases: &[],
            templates: &[],
            relations: &rows.relations,
            calls: &rows.calls,
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
        };
        match writer.write_all(&payload) {
            Ok(_counts) => {
                if verbose {
                    println!("[graph] Write complete");
                }
            }
            Err(error) => return Err(format!("[graph] write failed: {error}")),
        }
    }

    let function_total: usize = selected_payloads
        .iter()
        .map(|payload| payload.functions.len())
        .sum();
    // _sr_cls: Python đọc key "classes" không tồn tại ở payload js ⇒ 0.
    println!(
        "[SCAN_RESULT] parser={language} files={} functions={function_total} classes=0",
        selected_payloads.len()
    );
    if verbose {
        println!("[done] Total time: {:.2}s", start_time.elapsed().as_secs_f64());
    }
    Ok(())
}

fn parse_js_file_checked(path: &Path, root: &Path) -> Result<FilePayload, String> {
    jsparse::parse_js_file(path, root)
}

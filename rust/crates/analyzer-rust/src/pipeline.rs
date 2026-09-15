//! Port phần pipeline của `tools/rust/rust_analyzer.py`: `_scan_rust_files`,
//! `build_call_graph`, `_prepare_write_rows` + `_with_common_fields` +
//! `_build_note`, cleanup changed∪deleted, `LanguageCodeWriter::write_all`
//! (`use_full_writers=True`, `files_variant` default) và `[SCAN_RESULT]`.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use cortex_analyzer_framework::cleanup::cleanup_graph_files;
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_analyzer_framework::scan::{matches_extra_ignore, rel_posix, COMMON_SCAN_EXCLUDE};
use cortex_graph_writer::language_writer::{FilesVariant, LanguageCodeWriter, WriteAllPayload};
use cortex_graph_writer::store::GraphStore;

use crate::rustparse::{self, FilePayload, Row};

/// `_SCAN_SKIP_DIRS` của rust_analyzer (union COMMON_SCAN_EXCLUDE tại scan).
const SCAN_SKIP_DIRS: [&str; 23] = [
    // Version control
    ".git",
    ".hg",
    ".svn",
    // IDE
    ".idea",
    ".vs",
    ".vscode",
    ".eclipse",
    ".settings",
    // Build outputs (Rust/Cargo)
    "target",
    // Node / JS tooling
    "node_modules",
    "dist",
    // Cache
    ".cache",
    ".parcel-cache",
    "__pycache__",
    // Testing
    "coverage",
    ".test-results",
    "test-results",
    // Temporary
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    // OS specific
    ".DS_Store",
    "Thumbs.db",
];

fn skip_dir(name: &str) -> bool {
    SCAN_SKIP_DIRS.contains(&name)
        || COMMON_SCAN_EXCLUDE.contains(&name)
        || matches_extra_ignore(name)
}

/// `_scan_rust_files` — walk root, prune skip dirs, thu `.rs`, sorted theo
/// full path; `selected` non-empty ⇒ lọc theo rel path.
pub fn scan_rust_files(root: &Path, selected: &BTreeSet<String>) -> Vec<PathBuf> {
    let mut files = BTreeSet::new();
    walk(root, &mut files);
    files
        .into_iter()
        .filter(|path| selected.is_empty() || selected.contains(&rel_posix(root, path)))
        .collect()
}

fn walk(dir: &Path, files: &mut BTreeSet<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            // os.walk(followlinks=False): symlink dir không được walk.
            if !skip_dir(&name) && !path.is_symlink() {
                subdirs.push(path);
            }
            continue;
        }
        if rustparse::RUST_SOURCE_EXTENSIONS.iter().any(|ext| name.ends_with(ext)) {
            files.insert(path);
        }
    }
    for sub in subdirs {
        walk(&sub, files);
    }
}

/// `build_call_graph` — scan + parse payloads theo selected set
/// (empty set ⇒ full scan, khớp `if selected` của Python).
pub fn build_payloads(
    root: &Path,
    selected: &BTreeSet<String>,
) -> Result<Vec<FilePayload>, String> {
    let files = scan_rust_files(root, selected);
    let mut payloads = Vec::with_capacity(files.len());
    for path in files {
        payloads.push(rustparse::parse_rust_file(&path, root)?);
    }
    Ok(payloads)
}

/// Manifest paths (incremental) — sorted, lọc theo extension nguồn.
pub fn manifest_rs_paths(manifest: &str, root: &Path, extensions: &[&str]) -> BTreeSet<String> {
    load_manifest_paths(manifest, root)
        .into_iter()
        .filter(|item| extensions.iter().any(|ext| item.ends_with(ext)))
        .collect()
}

// ── Row helpers (`_build_note` / `_with_common_fields`) ─────────────────────

/// `_build_note`.
fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

/// `_with_common_fields` — summary/note setdefault + project scope columns.
fn with_common_fields(
    mut row: Row,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let comment = row
        .get("comment")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    if !row.contains_key("summary") {
        row.insert("summary".to_string(), json!(comment));
    }
    if !row.contains_key("note") {
        let code = row
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let summary = row
            .get("summary")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        row.insert(
            "note".to_string(),
            json!(build_note(&code, &comment, &summary)),
        );
    }
    row.insert("project_id".to_string(), json!(project_id));
    row.insert("project_name".to_string(), json!(project_name));
    row.insert("language".to_string(), json!(language));
    row.insert("repo".to_string(), json!(repo));
    row.insert("build_system".to_string(), json!(build_system));
    row
}

/// `_repo_name`.
pub fn repo_name(project_name: &str, root: &Path) -> String {
    let base = root
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default();
    format!("{project_name}/{base}")
}

/// `_prepare_write_rows` — shapes khớp Python row dicts (writer chỉ đọc các
/// key đã biết; rows mang đủ key mà cả 2 writer đọc giống nhau).
pub struct GraphRows {
    pub namespaces: Vec<Row>,
    pub files: Vec<Row>,
    pub types: Vec<Row>,
    pub functions: Vec<Row>,
    pub fields: Vec<Row>,
    pub aliases: Vec<Row>,
    pub templates: Vec<Row>,
    pub relations: Vec<Row>,
    pub calls: Vec<Row>,
}

#[allow(clippy::too_many_arguments)]
pub fn prepare_write_rows(
    payloads: &[FilePayload],
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> GraphRows {
    let mut rows = GraphRows {
        namespaces: Vec::new(),
        files: Vec::new(),
        types: Vec::new(),
        functions: Vec::new(),
        fields: Vec::new(),
        aliases: Vec::new(),
        templates: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
    };
    let common = |row: Row| {
        with_common_fields(row, project_id, project_name, language, repo, build_system)
    };

    for payload in payloads {
        let file_def = &payload.file_def;
        let rel_path = file_def.file_path.replace('\\', "/");
        rows.files.push(common({
            let mut row = Row::new();
            row.insert("id".into(), json!(rel_path));
            row.insert("path".into(), json!(rel_path));
            row.insert("start_line".into(), json!(file_def.start_line));
            row.insert("end_line".into(), json!(file_def.end_line));
            row.insert("code".into(), json!(file_def.code));
            row.insert("comment".into(), json!(file_def.comment));
            row.insert("summary".into(), json!(file_def.summary));
            row
        }));

        for item in &payload.namespaces {
            let mut row = Row::new();
            row.insert("id".into(), json!(item.symbol_id));
            row.insert("qualified_name".into(), json!(item.qualified_name));
            row.insert("name".into(), json!(item.name));
            row.insert("file_path".into(), json!(item.file_path));
            row.insert("start_line".into(), json!(item.start_line));
            row.insert("end_line".into(), json!(item.end_line));
            row.insert("code".into(), json!(item.code));
            row.insert("comment".into(), json!(item.comment));
            rows.namespaces.push(common(row));
        }

        for item in &payload.types {
            let mut row = Row::new();
            row.insert("id".into(), json!(item.symbol_id));
            row.insert("qualified_name".into(), json!(item.qualified_name));
            row.insert("name".into(), json!(item.name));
            row.insert("kind".into(), json!(item.kind));
            row.insert("file_path".into(), json!(item.file_path));
            row.insert("start_line".into(), json!(item.start_line));
            row.insert("end_line".into(), json!(item.end_line));
            row.insert("code".into(), json!(item.code));
            row.insert("comment".into(), json!(item.comment));
            rows.types.push(common(row));
        }

        for item in &payload.functions {
            let mut row = Row::new();
            row.insert("id".into(), json!(item.symbol_id));
            row.insert("qualified_name".into(), json!(item.qualified_name));
            row.insert("name".into(), json!(item.name));
            row.insert("kind".into(), json!(item.kind));
            row.insert("scope_name".into(), json!(item.scope_name));
            // `row.setdefault("class_name", row.get("scope_name"))`.
            row.insert("class_name".into(), json!(item.scope_name));
            row.insert("package_name".into(), Value::Null);
            row.insert("file_path".into(), json!(item.file_path));
            row.insert("start_byte".into(), json!(item.start_byte));
            row.insert("end_byte".into(), json!(item.end_byte));
            row.insert("start_line".into(), json!(item.start_line));
            row.insert("end_line".into(), json!(item.end_line));
            row.insert("arity".into(), json!(item.arity));
            row.insert("code".into(), json!(item.code));
            row.insert("comment".into(), json!(item.comment));
            rows.functions.push(common(row));
        }

        for item in &payload.fields {
            let mut row = Row::new();
            row.insert("id".into(), json!(item.symbol_id));
            row.insert("qualified_name".into(), json!(item.qualified_name));
            row.insert("name".into(), json!(item.name));
            row.insert("scope_name".into(), json!(item.scope_name));
            row.insert("type_signature".into(), json!(item.type_signature));
            row.insert("file_path".into(), json!(item.file_path));
            row.insert("start_line".into(), json!(item.start_line));
            row.insert("end_line".into(), json!(item.end_line));
            row.insert("code".into(), json!(item.code));
            rows.fields.push(common(row));
        }

        for item in &payload.aliases {
            let mut row = Row::new();
            row.insert("id".into(), json!(item.symbol_id));
            row.insert("qualified_name".into(), json!(item.qualified_name));
            row.insert("name".into(), json!(item.name));
            row.insert("kind".into(), json!(item.kind));
            row.insert("target_name".into(), json!(item.target_name));
            row.insert("file_path".into(), json!(item.file_path));
            row.insert("start_line".into(), json!(item.start_line));
            row.insert("end_line".into(), json!(item.end_line));
            // `row.setdefault("code", "")`.
            row.insert("code".into(), json!(item.code));
            rows.aliases.push(common(row));
        }

        for item in &payload.templates {
            let mut row = Row::new();
            row.insert("id".into(), json!(item.symbol_id));
            row.insert("name".into(), json!(item.name));
            row.insert("file_path".into(), json!(item.file_path));
            row.insert("start_line".into(), json!(item.start_line));
            row.insert("end_line".into(), json!(item.end_line));
            // `row.setdefault("code", row.get("name", ""))` — template code
            // luôn non-empty (được set khi parse) nên giữ trực tiếp.
            row.insert("code".into(), json!(item.code));
            rows.templates.push(common(row));
        }

        for item in &payload.relations {
            let mut row = Row::new();
            row.insert("source_id".into(), json!(item.source_id));
            row.insert("source_label".into(), json!(item.source_label));
            row.insert("target_id".into(), json!(item.target_id));
            row.insert("target_label".into(), json!(item.target_label));
            row.insert("rel_type".into(), json!(item.rel_type));
            row.insert("properties".into(), item.properties.clone());
            rows.relations.push(row);
        }
        for item in &payload.calls {
            if item.callee_id.is_some() {
                let mut row = Row::new();
                row.insert("caller_id".into(), json!(item.caller_id));
                row.insert("callee_id".into(), json!(item.callee_id));
                // CALLS project-scope contract: writer `write_calls` yêu cầu
                // project_id trên call rows; Python reference chạy qua
                // orchestrator/journal env để writer bơm giá trị này — Rust
                // supply trực tiếp (tiền lệ analyzer-js).
                row.insert("project_id".into(), json!(project_id));
                row.insert("call_type".into(), json!(item.call_type));
                rows.calls.push(row);
            }
        }
    }

    rows
}

/// Toàn bộ tham số pipeline (thay argparse.Namespace Python).
pub struct PipelineConfig {
    pub project_id: String,
    pub project_name: String,
    pub language: String,
    pub repo: String,
    pub build_system: String,
    pub incremental: bool,
    /// `_selected_rel_paths` (đã lọc .rs).
    pub cleanup_selected: BTreeSet<String>,
    /// `_deleted_rel_paths` (đã lọc .rs).
    pub cleanup_deleted: BTreeSet<String>,
    pub neo4j_db: Option<String>,
    pub neo4j_batch_size: i64,
    pub verbose: bool,
}

impl PipelineConfig {
    /// `sorted(set(selected) | set(deleted))`.
    fn cleanup_targets(&self) -> Vec<String> {
        self.cleanup_selected
            .union(&self.cleanup_deleted)
            .cloned()
            .collect()
    }
}

/// Category field mapping shared by BOTH planes: the graph `write_all` and the
/// phase-06 embedding-input artifact — so point-id/count/category order cannot
/// drift between them. `empty` is the shared `&[]` slice for unused categories.
fn build_payload<'a>(rows: &'a GraphRows, empty: &'a [Row]) -> WriteAllPayload<'a> {
    WriteAllPayload {
        projects: empty,
        packages: empty,
        namespaces: &rows.namespaces,
        files: &rows.files,
        classes: empty,
        types: &rows.types,
        function_types: empty,
        functions: &rows.functions,
        fields: &rows.fields,
        aliases: &rows.aliases,
        templates: &rows.templates,
        relations: &rows.relations,
        calls: &rows.calls,
        calls_with_site: empty,
        properties: empty,
        events: empty,
        interfaces: empty,
        enums: empty,
        constants: empty,
        variables: empty,
        navigators: empty,
        has_routes: empty,
        param_lists: empty,
        workflows: empty,
        workflow_steps: empty,
        call_evidence_sites: empty,
        call_evidence_observations: empty,
        build_configurations: empty,
        semantic_coverage: empty,
        proc_function_joins: empty,
        proc_host_declarations: empty,
        use_full_writers: true,
        files_variant: FilesVariant::Default,
    }
}

/// Phase-06 embedding-input categories for the graphless pass: builds rows the
/// SAME way `write_graph` does and maps them through the shared
/// [`build_payload`] so the artifact and the graph plane never diverge.
pub fn embedding_categories(
    config: &PipelineConfig,
    payloads: &[FilePayload],
) -> Vec<(String, Vec<Value>)> {
    let rows = prepare_write_rows(
        payloads,
        &config.project_id,
        &config.project_name,
        &config.language,
        &config.repo,
        &config.build_system,
    );
    let empty: Vec<Row> = Vec::new();
    build_payload(&rows, &empty).embedding_categories()
}

/// `_write_graph` — cleanup + `write_all`; lỗi bọc thành message exit-3.
pub fn write_graph(
    config: &PipelineConfig,
    payloads: &[FilePayload],
    mut store: Box<dyn GraphStore>,
) -> Result<(), String> {
    let verbose = config.verbose;
    let rows = prepare_write_rows(
        payloads,
        &config.project_id,
        &config.project_name,
        &config.language,
        &config.repo,
        &config.build_system,
    );

    let cleanup_targets = config.cleanup_targets();
    if config.incremental && !cleanup_targets.is_empty() {
        if verbose {
            println!(
                "[cleanup][graph] deleting graph data for {} files",
                cleanup_targets.len()
            );
        }
        let (deleted_nodes, deleted_unknown) =
            cleanup_graph_files(store.as_mut(), &config.project_id, &cleanup_targets)
                .map_err(|error| error.to_string())?;
        if verbose {
            println!(
                "[cleanup][graph] deleted_nodes={deleted_nodes} deleted_unknown_functions={deleted_unknown}"
            );
        }
    }

    if verbose {
        println!("[graph] Writing nodes and relations (streaming)...");
    }
    let batch_size = config.neo4j_batch_size.max(1) as usize;
    let mut writer = LanguageCodeWriter::new(store, config.neo4j_db.clone(), batch_size, verbose);
    let empty: Vec<Row> = Vec::new();
    let counts = writer.write_all(&build_payload(&rows, &empty));
    match counts {
        Ok(counts) => {
            if !counts.is_empty() && verbose {
                // Python: print(f"[graph] written {counts}") — verbose log
                // ngoài parity gates nên format BTreeMap là đủ.
                println!("[graph] written {counts:?}");
            }
            Ok(())
        }
        Err(error) => Err(error.to_string()),
    }
}

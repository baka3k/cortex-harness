//! Staged COBOL parse, resolve, semantic, incremental, and graph pipeline —
//! port `pipeline.py` (analyze_project / select_incremental_result /
//! graph_rows / write_graph_facts).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::language_writer::WriterError;
use serde_json::{Map, Value};

use crate::models::{AnalysisResult, Row, SemanticEdge, sorted_result};
use crate::parser::{COBOL_EXTENSIONS, COPYBOOK_EXTENSIONS, Patterns, iter_cobol_files, parse_paths};
use crate::parser_runtime::load_parser;
use crate::resolver::{DependencyIndex, ResolvedProject, resolve_project};
use crate::semantics::build_semantic_facts;

/// Mở rộng `~` như `Path.expanduser()`.
pub fn expanduser(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/")
        && let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home).join(rest);
        }
    if path == "~"
        && let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home);
        }
    PathBuf::from(path)
}

/// `Path.resolve()` — symlink-resolved khi path tồn tại; nếu không thì
/// normalize abspath.
pub fn py_resolve(path: &Path) -> PathBuf {
    if let Ok(resolved) = std::fs::canonicalize(path) {
        return resolved;
    }
    if path.is_absolute() {
        return normalize_path(path);
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    normalize_path(&cwd.join(path))
}

fn normalize_path(path: &Path) -> PathBuf {
    let mut out = PathBuf::from("/");
    for component in path.components() {
        match component {
            std::path::Component::RootDir | std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

fn normalize_extension(value: &str) -> String {
    if let Some(stripped) = value.strip_prefix('.') {
        format!(".{}", stripped.to_lowercase())
    } else {
        format!(".{}", value.to_lowercase())
    }
}

pub struct AnalyzeOutcome {
    pub result: AnalysisResult,
    pub dependency_index: DependencyIndex,
}

/// `analyze_project`.
pub fn analyze_project(
    root: &Path,
    project_id: &str,
    language_library: Option<&str>,
    copybook_roots: &[PathBuf],
    copybook_extensions: &[String],
) -> Result<AnalyzeOutcome, String> {
    let resolved_root = py_resolve(root);
    let (mut loaded, runtime_info) = load_parser(language_library).map_err(|e| e.to_string())?;
    let normalized_extensions: Vec<String> = if copybook_extensions.is_empty() {
        COPYBOOK_EXTENSIONS.iter().map(|value| value.to_string()).collect()
    } else {
        copybook_extensions.iter().map(|value| normalize_extension(value)).collect()
    };
    let mut source_extensions: Vec<String> = COBOL_EXTENSIONS
        .iter()
        .map(|value| value.to_string())
        .collect();
    for extension in &normalized_extensions {
        if !source_extensions.contains(extension) {
            source_extensions.push(extension.clone());
        }
    }
    let mut source_paths: Vec<PathBuf> = iter_cobol_files(&resolved_root, &source_extensions);
    for copybook_root in copybook_roots {
        let resolved_copybook_root = py_resolve(&expanduser(
            &copybook_root.to_string_lossy(),
        ));
        if resolved_copybook_root.is_dir() {
            source_paths.extend(iter_cobol_files(
                &resolved_copybook_root,
                &normalized_extensions,
            ));
        }
    }
    // sorted(set(source_paths))
    source_paths.sort_by(|a, b| crate::parser::compare_path_parts(a, b));
    source_paths.dedup_by(|a, b| a == b);

    let patterns = Patterns::new();
    let parsed = parse_paths(&patterns, &source_paths, &resolved_root, &mut loaded)?;
    let processed_files = parsed.len() as i64;
    let syntax_error_count: i64 = parsed.iter().map(|item| item.tree_error_count).sum();
    let resolved: ResolvedProject = resolve_project(parsed, &normalized_extensions);
    let (nodes, edges, mut diagnostics) = build_semantic_facts(&resolved, project_id);
    for source in &resolved.files {
        diagnostics.extend(source.diagnostics.clone());
    }
    let result = sorted_result(
        project_id,
        &resolved_root.to_string_lossy(),
        nodes,
        edges,
        diagnostics,
        runtime_info.to_json(),
        processed_files,
        syntax_error_count,
    );
    Ok(AnalyzeOutcome {
        result,
        dependency_index: DependencyIndex::from_resolved(&resolved),
    })
}

/// `select_incremental_result`.
pub fn select_incremental_result(
    result: AnalysisResult,
    impacted_paths: impl IntoIterator<Item = String>,
) -> AnalysisResult {
    let impacted: std::collections::BTreeSet<String> = impacted_paths
        .into_iter()
        .map(|path| path.replace('\\', "/"))
        .collect();
    // labels_by_id từ FULL node set (Python dựng trước khi lọc nodes).
    let labels_by_id: BTreeMap<String, String> = result
        .nodes
        .iter()
        .map(|node| (node.id.clone(), node.label.clone()))
        .collect();
    let nodes: Vec<_> = result
        .nodes
        .iter()
        .filter(|node| impacted.contains(&node.file_path))
        .cloned()
        .collect();
    let selected_ids: std::collections::BTreeSet<String> =
        nodes.iter().map(|node| node.id.clone()).collect();
    let mut edges: Vec<SemanticEdge> = Vec::new();
    for mut edge in result.edges {
        if selected_ids.contains(&edge.source_id) || impacted.contains(&edge.evidence.file) {
            let mut properties = edge.properties.clone();
            if let Some(map) = properties.as_object_mut() {
                // replace(edge, properties={**props, "_source_label": ..., "_target_label": ...})
                map.insert(
                    "_source_label".into(),
                    Value::String(
                        labels_by_id
                            .get(&edge.source_id)
                            .cloned()
                            .expect("edge source label"),
                    ),
                );
                map.insert(
                    "_target_label".into(),
                    Value::String(
                        labels_by_id
                            .get(&edge.target_id)
                            .cloned()
                            .expect("edge target label"),
                    ),
                );
            }
            edge.properties = properties;
            edges.push(edge);
        }
    }
    let diagnostics: Vec<_> = result
        .diagnostics
        .iter()
        .filter(|item| {
            item.evidence
                .as_ref()
                .map(|evidence| impacted.contains(&evidence.file))
                .unwrap_or(true)
        })
        .cloned()
        .collect();
    let processed_files = nodes
        .iter()
        .filter(|node| node.label == "File")
        .map(|node| node.file_path.clone())
        .collect::<std::collections::BTreeSet<_>>()
        .len() as i64;
    let node_count = nodes.len() as i64;
    let edge_count = edges.len() as i64;
    let diagnostic_count = diagnostics.len() as i64;
    let AnalysisResult {
        project_id,
        root,
        summary,
        ..
    } = result;
    AnalysisResult {
        project_id,
        root,
        nodes,
        edges,
        diagnostics,
        summary: crate::models::AnalysisSummary {
            processed_files,
            node_count,
            edge_count,
            diagnostic_count,
            syntax_error_count: summary.syntax_error_count,
            runtime: summary.runtime,
            invalidated_files: impacted.len() as i64,
        },
    }
}

/// `graph_rows` — nodes nhóm theo label + relation rows.
pub fn graph_rows(result: &AnalysisResult) -> (BTreeMap<String, Vec<Row>>, Vec<Row>) {
    let mut nodes_by_label: BTreeMap<String, Vec<Row>> = BTreeMap::new();
    let label_by_id: BTreeMap<String, String> = result
        .nodes
        .iter()
        .map(|node| (node.id.clone(), node.label.clone()))
        .collect();
    for node in &result.nodes {
        let mut properties = node
            .properties
            .as_object()
            .cloned()
            .unwrap_or_else(Map::new);
        properties.insert("id".into(), Value::String(node.id.clone()));
        properties.insert("confidence".into(), serde_json::json!(node.confidence));
        properties.insert(
            "source_start_byte".into(),
            Value::Number(node.evidence.start_byte.into()),
        );
        properties.insert(
            "source_end_byte".into(),
            Value::Number(node.evidence.end_byte.into()),
        );
        nodes_by_label
            .entry(node.label.clone())
            .or_default()
            .push(serde_json::json!({
                "id": node.id,
                "properties": Value::Object(properties),
            })
            .as_object()
            .cloned()
            .expect("row object"));
    }
    let mut relations: Vec<Row> = Vec::new();
    for edge in &result.edges {
        let mut edge_properties = edge
            .properties
            .as_object()
            .cloned()
            .unwrap_or_else(Map::new);
        let source_label = match label_by_id.get(&edge.source_id) {
            Some(label) => label.clone(),
            None => pop_string(&mut edge_properties, "_source_label"),
        };
        let target_label = match label_by_id.get(&edge.target_id) {
            Some(label) => label.clone(),
            None => pop_string(&mut edge_properties, "_target_label"),
        };
        if source_label.is_empty() || target_label.is_empty() {
            panic!("missing endpoint label for COBOL edge {}", edge.id);
        }
        edge_properties.insert("id".into(), Value::String(edge.id.clone()));
        edge_properties.insert("confidence".into(), serde_json::json!(edge.confidence));
        edge_properties.insert("dynamic".into(), Value::Bool(edge.dynamic));
        edge_properties.insert("file_path".into(), Value::String(edge.evidence.file.clone()));
        edge_properties.insert("start_line".into(), Value::Number(edge.evidence.start_line.into()));
        edge_properties.insert("end_line".into(), Value::Number(edge.evidence.end_line.into()));
        relations.push(serde_json::json!({
            "source_id": edge.source_id,
            "source_label": source_label,
            "target_id": edge.target_id,
            "target_label": target_label,
            "rel_type": edge.relationship,
            "properties": Value::Object(edge_properties),
        })
        .as_object()
        .cloned()
        .expect("row object"));
    }
    (nodes_by_label, relations)
}

/// Python `dict.pop(key, None)` — trả Option; giữ None ⇒ chuỗi rỗng.
fn pop_string(map: &mut Map<String, Value>, key: &str) -> String {
    map.remove(key)
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default()
}

/// `write_graph_facts` — files (with_imports) + repo_file_edges + FileMetadata
/// + node batch theo label (sorted) + typed relations.
pub fn write_graph_facts(
    writer: &mut LanguageCodeWriter,
    result: &AnalysisResult,
    project_name: &str,
    repo: &str,
    build_system: &str,
) -> Result<BTreeMap<String, i64>, WriterError> {
    let (mut nodes_by_label, relations) = graph_rows(result);
    let mut counts: BTreeMap<String, i64> = BTreeMap::new();
    let file_metadata_rows = nodes_by_label.remove("File").unwrap_or_default();
    if !file_metadata_rows.is_empty() {
        let mut file_rows: Vec<Row> = Vec::new();
        for row in &file_metadata_rows {
            let properties = row
                .get("properties")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            file_rows.push(
                serde_json::json!({
                    "id": row.get("id").cloned().unwrap_or(Value::Null),
                    "path": properties.get("path").cloned().unwrap_or(Value::Null),
                    "start_line": properties.get("start_line").cloned().unwrap_or_else(|| Value::Number(1.into())),
                    "end_line": properties.get("end_line").cloned().unwrap_or_else(|| Value::Number(1.into())),
                    "code": "",
                    "comment": "",
                    "summary": "COBOL source file",
                    "note": "",
                    "imports": [],
                    "exports": [],
                    "project_id": result.project_id,
                    "project_name": if project_name.is_empty() { result.project_id.clone() } else { project_name.to_string() },
                    "language": "cobol",
                    "repo": repo,
                    "build_system": build_system,
                })
                .as_object()
                .cloned()
                .expect("row object"),
            );
        }
        counts.insert("files".into(), writer.write_files_with_imports(&file_rows)? as i64);
        counts.insert(
            "repo_file_edges".into(),
            writer.write_repo_file_edges(&file_rows)? as i64,
        );
        counts.insert(
            "FileMetadata".into(),
            writer.write_nodes_batch(
                "cobol:FileMetadata",
                "UNWIND $rows AS row MATCH (n:File {id: row.id}) SET n += row.properties",
                &file_metadata_rows,
            )? as i64,
        );
    }
    for (label, rows) in nodes_by_label {
        // Python: re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label)
        if !is_safe_label(&label) {
            return Err(WriterError::Contract(format!("unsafe graph label: {label}")));
        }
        let key = format!("cobol:{label}");
        let query = format!(
            "UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row.properties"
        );
        counts.insert(label.clone(), writer.write_nodes_batch(&key, &query, &rows)? as i64);
    }
    counts.insert(
        "relations".into(),
        writer.write_relations_typed(&relations, Some(&result.project_id))? as i64,
    );
    Ok(counts)
}

fn is_safe_label(label: &str) -> bool {
    let mut chars = label.chars();
    match chars.next() {
        Some(first) if first.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

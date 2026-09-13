//! Port `tools/database_schema/` — schema overlay (dialect `sql` | `plsql`):
//! models (normalize_identifier / stable_id / node & rel rows), pipeline
//! (regex DDL scan) và `DatabaseSchemaWriter` (query giữ nguyên từng chữ).

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use regex::Regex;
use serde_json::{json, Map, Value};

use cortex_analyzer_framework::ts::decode_ignore;
use cortex_graph_writer::store::GraphStore;

use crate::pyutil::sha256_hex;

// ── models ──────────────────────────────────────────────────────────────────

/// `models.normalize_identifier` → (schema, name).
pub fn normalize_identifier(value: &str) -> (String, String) {
    let cleaned = value.trim();
    let cleaned = cleaned.trim_end_matches([';', ',', ')']);
    let parts: Vec<String> = cleaned
        .split('.')
        .filter(|part| !part.is_empty())
        .map(|part| part.trim_matches(['`', '"', '[', ']']).to_lowercase())
        .collect();
    if parts.is_empty() {
        return (String::new(), String::new());
    }
    if parts.len() == 1 {
        return (String::new(), parts[0].clone());
    }
    (
        parts[..parts.len() - 1].join("."),
        parts[parts.len() - 1].clone(),
    )
}

/// `models.stable_id` — `db::sha256("\x1f".join(parts.strip().lower()))[:32]`.
pub fn stable_id(parts: &[&str]) -> String {
    let normalized = parts
        .iter()
        .map(|part| part.trim().to_lowercase())
        .collect::<Vec<_>>()
        .join("\u{1f}");
    format!("db::{}", &sha256_hex(normalized.as_bytes())[..32])
}

#[derive(Debug, Clone)]
pub struct DatabaseObjectFact {
    pub object_id: String,
    pub project_id: String,
    pub label: String,
    pub name: String,
    pub schema_name: String,
    pub dialect: String,
    pub file_path: String,
    pub start_line: i64,
    pub declared: bool,
    pub object_kind: String,
}

impl DatabaseObjectFact {
    fn node_row(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.object_id));
        row.insert("symbol_id".into(), json!(self.object_id));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("name".into(), json!(self.name));
        row.insert(
            "qualified_name".into(),
            json!(if self.schema_name.is_empty() {
                self.name.clone()
            } else {
                format!("{}.{}", self.schema_name, self.name)
            }),
        );
        row.insert("schema_name".into(), json!(self.schema_name));
        row.insert("dialect".into(), json!(self.dialect));
        row.insert("file_path".into(), json!(self.file_path));
        row.insert("start_line".into(), json!(self.start_line));
        row.insert("declared".into(), json!(self.declared));
        row.insert(
            "object_kind".into(),
            json!(if self.object_kind.is_empty() {
                self.label.to_lowercase()
            } else {
                self.object_kind.clone()
            }),
        );
        row.insert("label".into(), json!(self.label));
        row
    }
}

#[derive(Debug, Clone)]
pub struct DatabaseRelationshipFact {
    pub relationship_id: String,
    pub project_id: String,
    pub rel_type: String,
    pub source_id: String,
    pub source_label: String,
    pub source_name: String,
    pub target_id: String,
    pub target_label: String,
    pub target_name: String,
    pub dialect: String,
    pub file_path: String,
    pub start_line: i64,
}

impl DatabaseRelationshipFact {
    fn row(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.relationship_id));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("type".into(), json!(self.rel_type));
        row.insert("source_id".into(), json!(self.source_id));
        row.insert("source_label".into(), json!(self.source_label));
        row.insert("target_id".into(), json!(self.target_id));
        row.insert("target_label".into(), json!(self.target_label));
        row.insert("dialect".into(), json!(self.dialect));
        row.insert("file_path".into(), json!(self.file_path));
        row.insert("start_line".into(), json!(self.start_line));
        row.insert("confidence".into(), json!(0.9));
        row
    }
}

#[derive(Debug, Clone, Default)]
pub struct DatabaseAnalysisResult {
    pub project_id: String,
    pub objects: Vec<DatabaseObjectFact>,
    pub relationships: Vec<DatabaseRelationshipFact>,
}

pub type GraphRows = (Vec<Map<String, Value>>, Vec<Map<String, Value>>);

impl DatabaseAnalysisResult {
    /// `graph_rows()` — objects sort theo (label, schema_name, name);
    /// relationships sort theo (source_id, rel_type, target_id).
    pub fn graph_rows(&self) -> GraphRows {
        let mut objects = self.objects.clone();
        objects.sort_by(|a, b| {
            (&a.label, &a.schema_name, &a.name).cmp(&(&b.label, &b.schema_name, &b.name))
        });
        let mut relationships = self.relationships.clone();
        relationships.sort_by(|a, b| {
            (&a.source_id, &a.rel_type, &a.target_id)
                .cmp(&(&b.source_id, &b.rel_type, &b.target_id))
        });
        (
            objects.iter().map(|item| item.node_row()).collect(),
            relationships.iter().map(|item| item.row()).collect(),
        )
    }
}

// ── pipeline ────────────────────────────────────────────────────────────────

const SQL_EXTENSIONS: [&str; 4] = [".sql", ".ddl", ".dml", ".psql"];
const PLSQL_EXTENSIONS: [&str; 10] = [
    ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc",
];
const SKIP_DIRS: [&str; 10] = [
    ".git", ".cache", ".venv", "venv", "node_modules", "vendor", "dist", "build", "bin", "obj",
];

fn identifier_pattern() -> &'static str {
    r#"(?:[A-Za-z_$#][\w$#]*|"[^"]+"|`[^`]+`|\[[^\]]+\])(?:\s*\.\s*(?:[A-Za-z_$#][\w$#]*|"[^"]+"|`[^`]+`|\[[^\]]+\]))?"#
}

/// Regex set của pipeline.py — dựng 1 lần, tái dùng.
pub struct Pipeline {
    object_re: Regex,
    plsql_routine_re: Regex,
    read_re: Regex,
    write_re: Regex,
    reference_re: Regex,
}

impl Default for Pipeline {
    fn default() -> Self {
        Self::new()
    }
}

/// Regex bản Python của `_mask_non_code` — giữ public cho module sql/plsql tái dùng.
/// Python làm việc trên `list(text)` với char-index span; Rust regex trả byte
/// offset nên map về char offset bằng sweep 1 lần.
pub fn mask_non_code(text: &str) -> String {
    let mut masked: Vec<char> = text.chars().collect();
    let patterns: [Regex; 3] = [
        Regex::new(r"--[^\n]*").unwrap(),
        Regex::new(r"(?s)/\*.*?\*/").unwrap(),
        Regex::new(r#"(?s)'(?:''|[^'])*'"#).unwrap(),
    ];
    for pattern in &patterns {
        // Mask trên bản snapshot hiện tại (giống Python: patterns áp tuần tự
        // lên text gốc — các span đã space vẫn khớp lại nhưng vô hại).
        let original: String = masked.iter().collect();
        for m in pattern.find_iter(&original) {
            let (start, end) = (m.start(), m.end());
            let mut char_index = 0usize;
            let mut byte_index = 0usize;
            // `masked` cùng độ dài với `original` — sweep byte→char offset.
            #[allow(clippy::explicit_counter_loop)]
            for c in original.chars() {
                if byte_index >= start && c != '\n' {
                    masked[char_index] = ' ';
                }
                byte_index += c.len_utf8();
                char_index += 1;
                if byte_index >= end {
                    break;
                }
            }
        }
    }
    masked.into_iter().collect()
}

impl Pipeline {
    pub fn new() -> Self {
        let ident = identifier_pattern();
        let object_re = Regex::new(&format!(
            r#"(?i)\bcreate\s+(?:or\s+replace\s+)?(?P<kind>table|view|procedure|proc|function)\s+(?P<name>{ident})"#
        ))
        .unwrap();
        let plsql_routine_re = Regex::new(&format!(
            r#"(?is)\b(?P<kind>procedure|function)\s+(?P<name>{ident})\b[^;]*?\b(?:is|as)\b"#
        ))
        .unwrap();
        let read_re =
            Regex::new(&format!(r#"(?i)\b(?:from|join)\s+(?P<name>{ident})"#)).unwrap();
        let write_re = Regex::new(&format!(
            r#"(?i)\b(?:insert\s+into|update|delete\s+from|merge\s+into)\s+(?P<name>{ident})"#
        ))
        .unwrap();
        let reference_re =
            Regex::new(&format!(r#"(?i)\breferences\s+(?P<name>{ident})"#)).unwrap();
        Self {
            object_re,
            plsql_routine_re,
            read_re,
            write_re,
            reference_re,
        }
    }

    /// `pipeline._files` — rglob theo dialect extension; (path, dialect).
    pub fn files(&self, root: &Path, dialects: &[&str]) -> Vec<(PathBuf, String)> {
        let enabled: BTreeSet<String> = dialects
            .iter()
            .map(|value| value.trim().to_lowercase())
            .collect();
        let mut out: Vec<(PathBuf, String)> = Vec::new();
        self.rglob_visit(root, &mut |path| {
            let rel_parts: Vec<String> = path
                .strip_prefix(root)
                .unwrap_or(path)
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_string())
                .collect();
            if rel_parts.iter().any(|part| SKIP_DIRS.contains(&part.as_str())) {
                return;
            }
            let name = path
                .file_name()
                .map(|n| n.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            // pathlib.Path.suffix: dot cuối sau vị trí 0; dotfile ".hidden" → "".
            let suffix = match name.rfind('.') {
                Some(0) | None => String::new(),
                Some(index) => name[index..].to_string(),
            };
            if SQL_EXTENSIONS.contains(&suffix.as_str()) && enabled.contains("sql") {
                out.push((path.to_path_buf(), "sql".to_string()));
            } else if PLSQL_EXTENSIONS.contains(&suffix.as_str()) && enabled.contains("plsql") {
                out.push((path.to_path_buf(), "plsql".to_string()));
            }
        });
        out
    }

    fn rglob_visit(&self, root: &Path, visit: &mut impl FnMut(&Path)) {
        fn walk(dir: &Path, visit: &mut impl FnMut(&Path)) {
            let entries = match std::fs::read_dir(dir) {
                Ok(entries) => entries,
                Err(_) => return,
            };
            let mut subdirs: Vec<PathBuf> = Vec::new();
            for entry in entries.flatten() {
                let path = entry.path();
                visit(&path);
                if path.is_dir() {
                    subdirs.push(path);
                }
            }
            for sub in subdirs {
                walk(&sub, visit);
            }
        }
        walk(root, visit);
    }

    /// `pipeline.analyze_project`.
    pub fn analyze_project(
        &self,
        root: &Path,
        project_id: &str,
        dialects: &[&str],
        selected_paths: Option<&[String]>,
    ) -> DatabaseAnalysisResult {
        let selected: BTreeSet<String> = selected_paths
            .unwrap_or(&[])
            .iter()
            .map(|path| path.replace('\\', "/"))
            .collect();
        let mut objects: BTreeMap<String, DatabaseObjectFact> = BTreeMap::new();
        let mut relationships: BTreeMap<String, DatabaseRelationshipFact> = BTreeMap::new();

        for (path, dialect) in self.files(root, dialects) {
            let rel: String = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join("/");
            if !selected.is_empty() && !selected.contains(&rel) {
                continue;
            }
            // read_text(encoding="utf-8", errors="ignore")
            let raw = std::fs::read(&path)
                .map(|bytes| decode_ignore(&bytes))
                .unwrap_or_default();
            let code = mask_non_code(&raw);
            let mut matches: Vec<(usize, usize, String, String)> = self
                .object_re
                .captures_iter(&code)
                .map(capture_kind_name)
                .collect();
            if dialect == "plsql" {
                let routine: Vec<(usize, usize, String, String)> = self
                    .plsql_routine_re
                    .captures_iter(&code)
                    .map(capture_kind_name)
                    .collect();
                matches.extend(routine);
                matches.sort_by_key(|item| (item.0, item.1));
            }
            for (index, (start, end, kind, name_raw)) in matches.iter().enumerate() {
                let (schema, name) = normalize_identifier(name_raw);
                if name.is_empty() {
                    continue;
                }
                let label = object_label(kind);
                let start_line = line_at(&raw, *start);
                let source_id = stable_id(&[project_id, &label, &schema, &name]);
                objects.insert(
                    source_id.clone(),
                    DatabaseObjectFact {
                        object_id: source_id.clone(),
                        project_id: project_id.to_string(),
                        label: label.clone(),
                        name: name.clone(),
                        schema_name: schema.clone(),
                        dialect: dialect.clone(),
                        file_path: rel.clone(),
                        start_line,
                        declared: true,
                        object_kind: kind.clone(),
                    },
                );
                if label == "Table" {
                    let object_id = stable_id(&[project_id, "Table", &schema, &name]);
                    let overwrite = match objects.get(&object_id) {
                        None => true,
                        Some(current) => !current.declared,
                    };
                    if overwrite {
                        objects.insert(
                            object_id.clone(),
                            DatabaseObjectFact {
                                object_id: object_id.clone(),
                                project_id: project_id.to_string(),
                                label: "Table".to_string(),
                                name: name.clone(),
                                schema_name: schema.clone(),
                                dialect: dialect.clone(),
                                file_path: rel.clone(),
                                start_line,
                                declared: true,
                                object_kind: "table".to_string(),
                            },
                        );
                    }
                }
                let body_start = (*end).min(code.len());
                let body_end = matches
                    .get(index + 1)
                    .map(|m| m.0)
                    .unwrap_or(code.len())
                    .min(code.len());
                if body_start > body_end {
                    continue;
                }
                let body = &code[body_start..body_end];
                let mut references: BTreeMap<(String, String), BTreeSet<String>> = BTreeMap::new();
                for caps in self.read_re.captures_iter(body) {
                    let (target_schema, target_name) = normalize_identifier(&caps["name"]);
                    if !target_name.is_empty() {
                        references
                            .entry((target_schema, target_name))
                            .or_default()
                            .insert("READS_FROM".to_string());
                    }
                }
                for caps in self.write_re.captures_iter(body) {
                    let (target_schema, target_name) = normalize_identifier(&caps["name"]);
                    if !target_name.is_empty() {
                        references
                            .entry((target_schema, target_name))
                            .or_default()
                            .insert("WRITES_TO".to_string());
                    }
                }
                for caps in self.reference_re.captures_iter(body) {
                    let (target_schema, target_name) = normalize_identifier(&caps["name"]);
                    if !target_name.is_empty() {
                        references
                            .entry((target_schema, target_name))
                            .or_default()
                            .insert("REFERENCES_TABLE".to_string());
                    }
                }
                for ((target_schema, target_name), semantic_types) in &references {
                    let target_id =
                        stable_id(&[project_id, "Table", target_schema, target_name]);
                    let overwrite = match objects.get(&target_id) {
                        None => true,
                        Some(current) => !current.declared,
                    };
                    if overwrite {
                        objects.insert(
                            target_id.clone(),
                            DatabaseObjectFact {
                                object_id: target_id.clone(),
                                project_id: project_id.to_string(),
                                label: "Table".to_string(),
                                name: target_name.clone(),
                                schema_name: target_schema.clone(),
                                dialect: dialect.clone(),
                                file_path: rel.clone(),
                                start_line,
                                declared: false,
                                object_kind: "table".to_string(),
                            },
                        );
                    }
                    let target_object_id = objects[&target_id].object_id.clone();
                    let mut rel_types: BTreeSet<String> = semantic_types.clone();
                    rel_types.insert("REFERENCES_TABLE".to_string());
                    for rel_type in rel_types {
                        let relationship_id =
                            stable_id(&[&source_id, &rel_type, &target_object_id]);
                        relationships.insert(
                            relationship_id.clone(),
                            DatabaseRelationshipFact {
                                relationship_id,
                                project_id: project_id.to_string(),
                                rel_type,
                                source_id: source_id.clone(),
                                source_label: label.clone(),
                                source_name: name.clone(),
                                target_id: target_object_id.clone(),
                                target_label: "Table".to_string(),
                                target_name: target_name.clone(),
                                dialect: dialect.clone(),
                                file_path: rel.clone(),
                                start_line,
                            },
                        );
                    }
                }
            }
        }
        DatabaseAnalysisResult {
            project_id: project_id.to_string(),
            objects: objects.into_values().collect(),
            relationships: relationships.into_values().collect(),
        }
    }
}

fn capture_kind_name(caps: regex::Captures<'_>) -> (usize, usize, String, String) {
    (
        caps.get(0).map(|m| m.start()).unwrap_or(0),
        caps.get(0).map(|m| m.end()).unwrap_or(0),
        caps.name("kind")
            .map(|m| m.as_str().to_lowercase())
            .unwrap_or_default(),
        caps.name("name")
            .map(|m| m.as_str().to_string())
            .unwrap_or_default(),
    )
}

fn object_label(kind: &str) -> String {
    match kind {
        "table" => "Table".to_string(),
        "view" => "View".to_string(),
        _ => "Procedure".to_string(),
    }
}

fn line_at(text: &str, offset: usize) -> i64 {
    text.as_bytes()[..offset.min(text.len())]
        .iter()
        .filter(|&&b| b == b'\n')
        .count() as i64
        + 1
}

// ── writer ──────────────────────────────────────────────────────────────────

const LABELS: [&str; 3] = ["Procedure", "Table", "View"]; // sorted như Python `sorted(_LABELS)`
const RELATIONSHIP_TYPES: [&str; 3] = ["READS_FROM", "REFERENCES_TABLE", "WRITES_TO"];

fn node_query(label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MERGE (node:{label} {{id: row.id}})
    SET node += row
    RETURN count(node) AS count
    "#
    )
}

fn relationship_query(source_label: &str, rel_type: &str, target_label: &str) -> String {
    format!(
        r#"
    UNWIND $rows AS row
    MATCH (source:{source_label} {{id: row.source_id, project_id: row.project_id}})
    MATCH (target:{target_label} {{id: row.target_id, project_id: row.project_id}})
    MERGE (source)-[rel:{rel_type} {{id: row.id}}]->(target)
    SET rel.project_id = row.project_id,
        rel.dialect = row.dialect,
        rel.file_path = row.file_path,
        rel.start_line = row.start_line,
        rel.confidence = row.confidence
    RETURN count(rel) AS count
    "#
    )
}

pub const DELETE_PATHS_QUERY: &str = r#"
MATCH (node {project_id: $project_id})
WHERE (node:Table OR node:View OR node:Procedure) AND node.file_path IN $paths
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"#;

/// `DatabaseSchemaWriter` — write_all + delete_paths trên GraphStore.
pub struct DatabaseSchemaWriter<'a> {
    store: &'a mut dyn GraphStore,
    batch_size: usize,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct WriteSummary {
    pub nodes: usize,
    pub relationships: usize,
    pub deleted: usize,
}

impl<'a> DatabaseSchemaWriter<'a> {
    pub fn new(store: &'a mut dyn GraphStore) -> Self {
        Self {
            store,
            batch_size: 500,
        }
    }

    fn count_of(records: &[Map<String, Value>], fallback: usize) -> usize {
        records
            .first()
            .and_then(|record| record.get("count"))
            .and_then(Value::as_i64)
            .map(|value| value.max(0) as usize)
            .unwrap_or(fallback)
    }

    fn write_batches(
        &mut self,
        query: &str,
        rows: &[Map<String, Value>],
    ) -> Result<usize, String> {
        let mut total = 0usize;
        for chunk in rows.chunks(self.batch_size.max(1)) {
            let mut params = BTreeMap::new();
            params.insert(
                "rows".to_string(),
                Value::Array(chunk.iter().cloned().map(Value::Object).collect()),
            );
            let records = self
                .store
                .execute_query(query, &params, None)
                .map_err(|e| e.to_string())?;
            total += Self::count_of(&records, chunk.len());
        }
        Ok(total)
    }

    /// `write_all(node_rows=..., relationship_rows=...)` → (nodes, relationships).
    pub fn write_all(
        &mut self,
        node_rows: &[Map<String, Value>],
        relationship_rows: &[Map<String, Value>],
    ) -> Result<(usize, usize), String> {
        for row in node_rows {
            let label = row.get("label").and_then(Value::as_str).unwrap_or("");
            if !LABELS.contains(&label) {
                return Err(format!("unsupported database node label: {label}"));
            }
            for key in ["id", "project_id", "name"] {
                let value = row.get(key);
                let empty = match value {
                    None | Some(Value::Null) => true,
                    Some(Value::String(text)) => text.is_empty(),
                    _ => false,
                };
                if empty {
                    return Err("database node row is missing identity or ownership".into());
                }
            }
        }
        for row in relationship_rows {
            let rel_type = row.get("type").and_then(Value::as_str).unwrap_or("");
            if !RELATIONSHIP_TYPES.contains(&rel_type) {
                return Err(format!("unsupported database relationship: {rel_type}"));
            }
            let source_label = row.get("source_label").and_then(Value::as_str).unwrap_or("");
            let target_label = row.get("target_label").and_then(Value::as_str).unwrap_or("");
            if !LABELS.contains(&source_label) || !LABELS.contains(&target_label) {
                return Err("database relationship labels are not allowlisted".into());
            }
        }
        let mut nodes = 0usize;
        for label in LABELS {
            let selected: Vec<Map<String, Value>> = node_rows
                .iter()
                .filter(|row| row.get("label").and_then(Value::as_str) == Some(label))
                .cloned()
                .collect();
            nodes += self.write_batches(&node_query(label), &selected)?;
        }
        let mut grouped: BTreeMap<(String, String, String), Vec<Map<String, Value>>> =
            BTreeMap::new();
        for row in relationship_rows {
            let key = (
                row.get("source_label")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
                row.get("type").and_then(Value::as_str).unwrap_or("").to_string(),
                row.get("target_label")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string(),
            );
            grouped.entry(key).or_default().push(row.clone());
        }
        let mut relationships = 0usize;
        for ((source_label, rel_type, target_label), rows) in grouped {
            relationships += self.write_batches(
                &relationship_query(&source_label, &rel_type, &target_label),
                &rows,
            )?;
        }
        Ok((nodes, relationships))
    }

    pub fn delete_paths(&mut self, project_id: &str, paths: &[String]) -> Result<usize, String> {
        let mut params = BTreeMap::new();
        params.insert("project_id".to_string(), json!(project_id));
        params.insert(
            "paths".to_string(),
            Value::Array(paths.iter().map(|p| json!(p)).collect()),
        );
        let records = self
            .store
            .execute_query(DELETE_PATHS_QUERY, &params, None)
            .map_err(|e| e.to_string())?;
        Ok(Self::count_of(&records, 0))
    }
}

// ── analyzer main ───────────────────────────────────────────────────────────

/// `_manifest` — JSON `{"paths": [...]}` hoặc JSON array; slash hoá.
pub fn load_paths_manifest(path: &str) -> Vec<String> {
    if path.is_empty() {
        return Vec::new();
    }
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(_) => return Vec::new(),
    };
    let value: Value = match serde_json::from_str(&text) {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    let items: Vec<String> = match &value {
        Value::Object(map) => map
            .get("paths")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| item.as_str().map(str::to_string).unwrap_or_default())
                    .collect()
            })
            .unwrap_or_default(),
        Value::Array(items) => items
            .iter()
            .map(|item| item.as_str().map(str::to_string).unwrap_or_default())
            .collect(),
        _ => Vec::new(),
    };
    items
        .into_iter()
        .map(|item| item.replace('\\', "/"))
        .collect()
}

/// `main()` — trả exit code (0 OK, 2 root not found, 3 write failed).
pub fn execute(args: &cortex_analyzer_framework::cli::AnalyzerArgs, dialect: &str) -> Result<i32, String> {
    let root = std::fs::canonicalize(&args.root)
        .unwrap_or_else(|_| PathBuf::from(&args.root));
    if !root.is_dir() {
        eprintln!("Root not found: {}", root.display());
        return Ok(2);
    }
    let root_name = root
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_default();
    let project_id = args
        .project_id
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            args.project_id_alt
                .clone()
                .filter(|value| !value.is_empty())
                .unwrap_or(root_name)
        });
    let changed = if args.incremental {
        load_paths_manifest(args.changed_files_manifest.as_deref().unwrap_or(""))
    } else {
        Vec::new()
    };
    let deleted = if args.incremental {
        load_paths_manifest(args.deleted_files_manifest.as_deref().unwrap_or(""))
    } else {
        Vec::new()
    };

    let pipeline = Pipeline::new();
    let selected = if changed.is_empty() {
        None
    } else {
        Some(changed.as_slice())
    };
    let result = pipeline.analyze_project(&root, &project_id, &[dialect], selected);
    let (node_rows, relationship_rows) = result.graph_rows();

    if args.dry_run {
        let payload = json!({
            "nodes": node_rows.iter().cloned().map(Value::Object).collect::<Vec<_>>(),
            "relationships": relationship_rows.iter().cloned().map(Value::Object).collect::<Vec<_>>(),
        });
        println!("{payload}");
        return Ok(0);
    }

    let summary = match write_graph(args, &project_id, &node_rows, &relationship_rows, &deleted) {
        Ok(summary) => summary,
        Err(error) => {
            eprintln!("[database_schema] graph write failed: {error}");
            return Ok(3);
        }
    };
    println!(
        "[overlay] database={} objects={} relationships={} graph={{'nodes': {}, 'relationships': {}, 'deleted': {}}}",
        dialect,
        node_rows.len(),
        relationship_rows.len(),
        summary.nodes,
        summary.relationships,
        summary.deleted,
    );
    Ok(0)
}

fn write_graph(
    args: &cortex_analyzer_framework::cli::AnalyzerArgs,
    project_id: &str,
    node_rows: &[Map<String, Value>],
    relationship_rows: &[Map<String, Value>],
    deleted_paths: &[String],
) -> Result<WriteSummary, String> {
    let mut summary = WriteSummary::default();
    if args.graph_writes_disabled() {
        return Ok(summary);
    }
    let mut store = args.open_store().map_err(|e| e.to_string())?;
    store.ensure_schema(None).map_err(|e| e.to_string())?;
    let mut writer = DatabaseSchemaWriter::new(store.as_mut());
    summary.deleted = if deleted_paths.is_empty() {
        0
    } else {
        writer
            .delete_paths(project_id, deleted_paths)
            .map_err(|e| e.to_string())?
    };
    let (nodes, relationships) = writer.write_all(node_rows, relationship_rows)?;
    summary.nodes = nodes;
    summary.relationships = relationships;
    Ok(summary)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_identifier_cases() {
        assert_eq!(normalize_identifier(" public.users "), ("public".into(), "users".into()));
        assert_eq!(
            normalize_identifier("hr.Employees;"),
            ("hr".into(), "employees".into())
        );
        assert_eq!(normalize_identifier("`App`.Orders,"), ("app".into(), "orders".into()));
        assert_eq!(normalize_identifier(""), ("".into(), "".into()));
    }

    #[test]
    fn stable_id_matches_python() {
        // db::sha256("parity\x1ftable\x1f\x1fusers")[:32]
        assert_eq!(
            stable_id(&["parity", "Table", "", "users"]),
            "db::5344246dbf3bc6d8b0cf00d4b0ac1ab9"
        );
    }

    #[test]
    fn analyze_basic_ddl() {
        let dir = std::env::temp_dir().join(format!("p08_schema_test_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("ddl")).unwrap();
        std::fs::write(
            dir.join("ddl/app.sql"),
            "-- comment create table fake\nCREATE TABLE users (id INT REFERENCES account(id));\n"

        )
        .unwrap();
        let pipeline = Pipeline::new();
        let result = pipeline.analyze_project(&dir, "p", &["sql"], None);
        let labels: Vec<&str> = result.objects.iter().map(|o| o.label.as_str()).collect();
        assert!(labels.contains(&"Table"));
        assert!(result
            .objects
            .iter()
            .any(|o| o.name == "account" && !o.declared));
        let _ = std::fs::remove_dir_all(&dir);
    }
}

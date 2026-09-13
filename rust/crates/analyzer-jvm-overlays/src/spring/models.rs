//! Port `tools/spring/models.py` — SpringFact/SpringRelationship + row
//! assembly (`to_graph_node`/`to_graph_row`).

use serde_json::{json, Map, Value};

pub const SPRING_PARSER_VERSION: &str = "spring-v2026-07-13-1";

#[derive(Debug, Clone, PartialEq, Default)]
pub struct SourceSpan {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
}

impl SourceSpan {
    pub fn new(file_path: &str, start_line: i64, end_line: i64) -> Self {
        Self { file_path: file_path.to_string(), start_line, end_line }
    }
}

#[derive(Debug, Clone)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub severity: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
}

impl Diagnostic {
    pub fn new(code: &str, message: &str, severity: &str, file_path: &str, start_line: i64, end_line: i64) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
            severity: severity.to_string(),
            file_path: file_path.to_string(),
            start_line,
            end_line,
        }
    }
}

#[derive(Debug, Clone)]
pub struct SpringModule {
    pub root: String,
    pub rel_path: String,
    pub languages: Vec<String>,
    pub build_files: Vec<String>,
    pub config_files: Vec<String>,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

#[derive(Debug, Clone)]
pub struct ConfigValue {
    pub key: String,
    pub value: Value,
    pub source: SourceSpan,
    pub profile: String,
    pub raw_value: String,
    pub resolution_status: String,
}

#[derive(Debug, Clone)]
pub struct LanguageSourceFact {
    pub language: String,
    pub file_path: String,
    pub source_symbol_id: String,
    pub package_name: String,
    pub declarations: Vec<String>,
    pub annotations: Vec<String>,
    pub parser_status: String,
}

#[derive(Debug, Clone)]
pub struct SpringFact {
    pub kind: String,
    pub stable_id: String,
    pub name: String,
    pub source: SourceSpan,
    pub project_id: String,
    pub project_name: String,
    pub language: String,
    pub confidence: f64,
    pub extraction_method: String,
    pub resolution_status: String,
    pub raw_value: String,
    pub resolved_value: String,
    pub source_symbol_id: String,
    pub properties: Map<String, Value>,
}

impl SpringFact {
    /// `to_graph_node` — base row + `row.update(properties)`.
    pub fn to_graph_node(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.stable_id));
        row.insert("symbol_id".into(), json!(self.stable_id));
        row.insert("name".into(), json!(self.name));
        row.insert("kind".into(), json!(self.kind));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("project_name".into(), json!(self.project_name));
        row.insert("language".into(), json!(self.language));
        row.insert("framework".into(), json!("spring"));
        row.insert("file_path".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("extraction_method".into(), json!(self.extraction_method));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("raw_value".into(), json!(self.raw_value));
        row.insert("resolved_value".into(), json!(self.resolved_value));
        row.insert("source_symbol_id".into(), json!(self.source_symbol_id));
        row.insert("parser_version".into(), json!(SPRING_PARSER_VERSION));
        for (key, value) in &self.properties {
            row.insert(key.clone(), value.clone());
        }
        row
    }
}

#[derive(Debug, Clone)]
pub struct SpringRelationship {
    pub rel_type: String,
    pub from_label: String,
    pub from_id: String,
    pub to_label: String,
    pub to_id: String,
    pub project_id: String,
    pub confidence: f64,
    pub resolution_status: String,
    pub reason: String,
    pub source: SourceSpan,
    pub properties: Map<String, Value>,
}

impl SpringRelationship {
    /// `to_graph_row`.
    pub fn to_graph_row(&self) -> Map<String, Value> {
        let mut props = Map::new();
        props.insert("confidence".into(), json!(self.confidence));
        props.insert("resolution_status".into(), json!(self.resolution_status));
        props.insert("reason".into(), json!(self.reason));
        props.insert("source_file".into(), json!(self.source.file_path));
        props.insert("start_line".into(), json!(self.source.start_line));
        props.insert("end_line".into(), json!(self.source.end_line));
        for (key, value) in &self.properties {
            props.insert(key.clone(), value.clone());
        }
        let mut row = Map::new();
        row.insert("type".into(), json!(self.rel_type));
        row.insert("from_label".into(), json!(self.from_label));
        row.insert("from_id".into(), json!(self.from_id));
        row.insert("to_label".into(), json!(self.to_label));
        row.insert("to_id".into(), json!(self.to_id));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("source_file".into(), json!(self.source.file_path));
        row.insert("properties".into(), Value::Object(props));
        row
    }
}

#[derive(Debug, Clone)]
pub struct SpringAnalysisResult {
    pub project_id: String,
    pub project_name: String,
    pub root: String,
    pub modules: Vec<SpringModule>,
    pub config_values: Vec<ConfigValue>,
    pub language_facts: Vec<LanguageSourceFact>,
    pub semantic_facts: Vec<SpringFact>,
    pub relationships: Vec<SpringRelationship>,
    pub diagnostics: Vec<Diagnostic>,
}

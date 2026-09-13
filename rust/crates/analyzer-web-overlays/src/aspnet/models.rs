//! Port `tools/common/aspnet/models.py` — SemanticFact / SemanticRelationship /
//! AnalysisResult / dedupe + `to_graph_node` / `to_graph_row` / `to_dict`.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::aspnet::safe_formats::{graph_property_value, redact_value};
use crate::pyutil::{normalize_relative_path, sha256_hex};

pub const ASPNET_PROTOCOL_VERSION: &str = "aspnet-roslyn-v1";
pub const ASPNET_MODEL_VERSION: &str = "aspnet-semantic-v1";

pub const ASPNET_NODE_LABELS: [&str; 25] = [
    "HttpEndpoint", "Route", "Middleware", "Controller", "Action", "RazorPage",
    "PageHandler", "WebFormPage", "HttpHandler", "HttpModule", "Filter", "Result",
    "View", "Layout", "PartialView", "Service", "Repository", "Model", "ViewModel",
    "ValidationRule", "ConfigurationKey", "SessionState", "ApplicationEvent",
    "AuthenticationScheme", "AuthorizationPolicy",
];

pub const ASPNET_RELATIONSHIP_TYPES: [&str; 17] = [
    "MAPPED_TO", "HANDLED_BY", "PASSES_THROUGH", "INVOKES", "INJECTS",
    "VALIDATES_WITH", "RENDERS", "REDIRECTS_TO", "FORWARDS_TO", "LOADS_FROM",
    "DEPENDS_ON", "READS_CONFIG", "WRITES_SESSION", "POSTS_BACK_TO", "INITIALIZES",
    "RETURNS_RESULT", "SEMANTIC_OF",
];

pub const EXTERNAL_LABELS: [&str; 4] = ["Class", "Function", "File", "Type"];

#[derive(Debug, Clone, PartialEq)]
pub struct SourceSpan {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub start_column: i64,
    pub end_column: i64,
}

impl SourceSpan {
    pub fn new(file_path: &str) -> Self {
        Self {
            file_path: file_path.to_string(),
            start_line: 1,
            end_line: 1,
            start_column: 1,
            end_column: 1,
        }
    }

    pub fn with_line(file_path: &str, start_line: i64) -> Self {
        Self {
            file_path: file_path.to_string(),
            start_line,
            end_line: 1,
            start_column: 1,
            end_column: 1,
        }
    }

    /// `normalized()` — clamp line/column >= 1 và chuẩn hoá path.
    pub fn normalized(&self) -> SourceSpan {
        let start_line = self.start_line.max(1);
        SourceSpan {
            file_path: normalize_relative_path(&self.file_path),
            start_line,
            end_line: start_line.max(self.end_line),
            start_column: self.start_column.max(1),
            end_column: self.end_column.max(1),
        }
    }

    pub fn to_value(&self) -> Value {
        json!({
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
        })
    }
}

#[derive(Debug, Clone)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub severity: String,
    pub source: SourceSpan,
    pub details: Value,
}

impl Diagnostic {
    pub fn new(code: &str, message: &str, severity: &str, source: SourceSpan) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
            severity: severity.to_string(),
            source,
            details: json!({}),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ParserCapability {
    pub name: String,
    pub available: bool,
    pub mandatory: bool,
    pub mode: String,
    pub status: String,
    pub message: String,
}

#[derive(Debug, Clone)]
pub struct AnalysisModule {
    pub module_id: String,
    pub module_path: String,
    pub framework: String,
    pub evidence: Vec<String>,
    pub confidence: f64,
    pub artifacts: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct SemanticFact {
    pub kind: String,
    pub stable_id: String,
    pub name: String,
    pub framework: String,
    pub project_id: String,
    pub project_name: String,
    pub module_id: String,
    pub source: SourceSpan,
    pub confidence: f64,
    pub resolution_status: String,
    pub extraction_method: String,
    pub source_symbol_id: String,
    pub properties: Map<String, Value>,
}

impl SemanticFact {
    /// `to_graph_node(generation_id)` — hàng node cho writer.
    pub fn to_graph_node(&self, generation_id: &str) -> Result<Map<String, Value>, String> {
        if !ASPNET_NODE_LABELS.contains(&self.kind.as_str()) {
            return Err(format!("unsupported ASP.NET node label: {}", self.kind));
        }
        let source = self.source.normalized();
        let storage_id = if generation_id.is_empty() {
            self.stable_id.clone()
        } else {
            format!("{}::generation::{}", self.stable_id, generation_id)
        };
        let safe_properties = redact_value("properties", &Value::Object(self.properties.clone()));
        let mut row = Map::new();
        row.insert("id".into(), json!(storage_id));
        row.insert("semantic_id".into(), json!(self.stable_id));
        row.insert("symbol_id".into(), json!(self.stable_id));
        row.insert("generation_id".into(), json!(generation_id));
        row.insert("name".into(), redact_value("name", &Value::String(self.name.clone())));
        row.insert("kind".into(), json!(self.kind));
        row.insert("framework".into(), json!(self.framework));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("project_name".into(), json!(self.project_name));
        row.insert("module_id".into(), json!(self.module_id));
        row.insert("file_path".into(), json!(source.file_path));
        row.insert("start_line".into(), json!(source.start_line));
        row.insert("end_line".into(), json!(source.end_line));
        row.insert("start_column".into(), json!(source.start_column));
        row.insert("end_column".into(), json!(source.end_column));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("extraction_method".into(), json!(self.extraction_method));
        row.insert("source_symbol_id".into(), json!(self.source_symbol_id));
        row.insert("parser_version".into(), json!(ASPNET_MODEL_VERSION));
        if let Value::Object(map) = safe_properties {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for key in keys {
                if !row.contains_key(key) {
                    row.insert(key.clone(), graph_property_value(&map[key]));
                }
            }
        }
        Ok(row)
    }
}

#[derive(Debug, Clone)]
pub struct SemanticRelationship {
    pub stable_id: String,
    pub relationship_type: String,
    pub from_id: String,
    pub to_id: String,
    pub from_label: String,
    pub to_label: String,
    pub framework: String,
    pub project_id: String,
    pub module_id: String,
    pub source: SourceSpan,
    pub confidence: f64,
    pub resolution_status: String,
    pub reason: String,
    pub properties: Map<String, Value>,
    pub from_generated: bool,
    pub to_generated: bool,
}

fn storage_id(value: &str, generation: &str, generated: bool) -> String {
    if !generation.is_empty() && generated {
        format!("{value}::generation::{generation}")
    } else {
        value.to_string()
    }
}

impl SemanticRelationship {
    /// `to_graph_row(generation_id)`.
    pub fn to_graph_row(&self, generation_id: &str) -> Result<Map<String, Value>, String> {
        if !ASPNET_RELATIONSHIP_TYPES.contains(&self.relationship_type.as_str()) {
            return Err(format!("unsupported ASP.NET relationship type: {}", self.relationship_type));
        }
        let source = self.source.normalized();
        let safe_properties = redact_value("properties", &Value::Object(self.properties.clone()));
        let mut properties = Map::new();
        if let Value::Object(map) = safe_properties {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for key in keys {
                properties.insert(key.clone(), graph_property_value(&map[key]));
            }
        }
        let mut row = Map::new();
        row.insert(
            "id".into(),
            json!(storage_id(&self.stable_id, generation_id, true)),
        );
        row.insert("semantic_id".into(), json!(self.stable_id));
        row.insert("type".into(), json!(self.relationship_type));
        row.insert(
            "from_id".into(),
            json!(storage_id(&self.from_id, generation_id, self.from_generated)),
        );
        row.insert(
            "to_id".into(),
            json!(storage_id(&self.to_id, generation_id, self.to_generated)),
        );
        row.insert("from_label".into(), json!(self.from_label));
        row.insert("to_label".into(), json!(self.to_label));
        row.insert("framework".into(), json!(self.framework));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("module_id".into(), json!(self.module_id));
        row.insert("generation_id".into(), json!(generation_id));
        row.insert("source_file".into(), json!(source.file_path));
        row.insert("start_line".into(), json!(source.start_line));
        row.insert("end_line".into(), json!(source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("reason".into(), json!(self.reason));
        row.insert("properties".into(), Value::Object(properties));
        Ok(row)
    }
}

#[derive(Debug, Clone, Default)]
pub struct AnalysisResult {
    pub project_id: String,
    pub project_name: String,
    pub framework: String,
    pub modules: Vec<AnalysisModule>,
    pub facts: Vec<SemanticFact>,
    pub relationships: Vec<SemanticRelationship>,
    pub capabilities: Vec<ParserCapability>,
    pub diagnostics: Vec<Diagnostic>,
    pub dependency_files: BTreeMap<String, Vec<String>>,
    pub module_coverage: BTreeMap<String, String>,
    pub coverage_status: String,
}

/// `dedupe_facts` — theo stable_id (last wins), sort (kind, stable_id).
pub fn dedupe_facts(values: Vec<SemanticFact>) -> Vec<SemanticFact> {
    let mut by_id: BTreeMap<String, usize> = BTreeMap::new();
    let mut ordered: Vec<SemanticFact> = Vec::new();
    for item in values {
        match by_id.get(&item.stable_id) {
            Some(&index) => ordered[index] = item,
            None => {
                by_id.insert(item.stable_id.clone(), ordered.len());
                ordered.push(item);
            }
        }
    }
    ordered.sort_by(|a, b| (&a.kind, &a.stable_id).cmp(&(&b.kind, &b.stable_id)));
    ordered
}

/// `dedupe_relationships` — theo stable_id (last wins), sort (type, stable_id).
pub fn dedupe_relationships(values: Vec<SemanticRelationship>) -> Vec<SemanticRelationship> {
    let mut by_id: BTreeMap<String, usize> = BTreeMap::new();
    let mut ordered: Vec<SemanticRelationship> = Vec::new();
    for item in values {
        match by_id.get(&item.stable_id) {
            Some(&index) => ordered[index] = item,
            None => {
                by_id.insert(item.stable_id.clone(), ordered.len());
                ordered.push(item);
            }
        }
    }
    ordered.sort_by(|a, b| (&a.relationship_type, &a.stable_id).cmp(&(&b.relationship_type, &b.stable_id)));
    ordered
}

/// `AnalysisResult.to_dict()` → Value (redact_value("result", asdict) + root="."),
/// dùng cho preview output.
pub fn analysis_result_to_value(result: &AnalysisResult) -> Value {
    let mut payload = Map::new();
    payload.insert("project_id".into(), json!(result.project_id));
    payload.insert("project_name".into(), json!(result.project_name));
    payload.insert("framework".into(), json!(result.framework));
    payload.insert(
        "modules".into(),
        Value::Array(result
            .modules
            .iter()
            .map(|module| {
                json!({
                    "module_id": module.module_id,
                    "module_path": module.module_path,
                    "framework": module.framework,
                    "evidence": module.evidence,
                    "confidence": module.confidence,
                    "artifacts": module.artifacts,
                })
            })
            .collect()),
    );
    payload.insert(
        "facts".into(),
        Value::Array(result
            .facts
            .iter()
            .map(|fact| {
                json!({
                    "kind": fact.kind,
                    "stable_id": fact.stable_id,
                    "name": fact.name,
                    "framework": fact.framework,
                    "project_id": fact.project_id,
                    "project_name": fact.project_name,
                    "module_id": fact.module_id,
                    "source": fact.source.to_value(),
                    "confidence": fact.confidence,
                    "resolution_status": fact.resolution_status,
                    "extraction_method": fact.extraction_method,
                    "source_symbol_id": fact.source_symbol_id,
                    "properties": Value::Object(fact.properties.clone()),
                })
            })
            .collect()),
    );
    payload.insert(
        "relationships".into(),
        Value::Array(result
            .relationships
            .iter()
            .map(|relationship| {
                json!({
                    "stable_id": relationship.stable_id,
                    "relationship_type": relationship.relationship_type,
                    "from_id": relationship.from_id,
                    "to_id": relationship.to_id,
                    "from_label": relationship.from_label,
                    "to_label": relationship.to_label,
                    "framework": relationship.framework,
                    "project_id": relationship.project_id,
                    "module_id": relationship.module_id,
                    "source": relationship.source.to_value(),
                    "confidence": relationship.confidence,
                    "resolution_status": relationship.resolution_status,
                    "reason": relationship.reason,
                    "properties": Value::Object(relationship.properties.clone()),
                    "from_generated": relationship.from_generated,
                    "to_generated": relationship.to_generated,
                })
            })
            .collect()),
    );
    payload.insert(
        "capabilities".into(),
        Value::Array(result
            .capabilities
            .iter()
            .map(|capability| {
                json!({
                    "name": capability.name,
                    "available": capability.available,
                    "mandatory": capability.mandatory,
                    "mode": capability.mode,
                    "status": capability.status,
                    "message": capability.message,
                })
            })
            .collect()),
    );
    payload.insert(
        "diagnostics".into(),
        Value::Array(result
            .diagnostics
            .iter()
            .map(|diagnostic| {
                json!({
                    "code": diagnostic.code,
                    "message": diagnostic.message,
                    "severity": diagnostic.severity,
                    "source": diagnostic.source.to_value(),
                    "details": diagnostic.details,
                })
            })
            .collect()),
    );
    payload.insert(
        "dependency_index".into(),
        json!({
            "files": result.dependency_files,
            "symbols": {},
            "modules": {},
        }),
    );
    payload.insert(
        "module_coverage".into(),
        Value::Object(result
            .module_coverage
            .iter()
            .map(|(key, value)| (key.clone(), Value::String(value.clone())))
            .collect()),
    );
    payload.insert("coverage_status".into(), json!(result.coverage_status));
    payload.insert("parser_version".into(), json!(ASPNET_MODEL_VERSION));
    payload.insert("root".into(), json!("."));
    redact_value("result", &Value::Object(payload))
}

/// Checksum staged generation (`apply_graph`):
/// `sha256(json.dumps({...}, sort_keys=True, separators=(",", ":")))`.
pub fn generation_checksum(fact_ids: &[String], relationship_ids: &[String], coverage: &str) -> String {
    let payload = crate::pyjson::dumps_compact_sorted_ascii(&json!({
        "facts": fact_ids,
        "relationships": relationship_ids,
        "coverage": coverage,
    }));
    sha256_hex(payload.as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn span_normalization_matches_python() {
        let span = SourceSpan {
            file_path: "/abs/./a.cs".into(),
            start_line: 0,
            end_line: -1,
            start_column: 0,
            end_column: 5,
        };
        let normalized = span.normalized();
        assert_eq!(normalized.file_path, "abs/./a.cs");
        assert_eq!((normalized.start_line, normalized.end_line), (1, 1));
        assert_eq!((normalized.start_column, normalized.end_column), (1, 5));
    }
}

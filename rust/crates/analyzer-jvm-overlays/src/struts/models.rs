//! Port `tools/struts/models.py` — StrutsFact/StrutsRelationship + stable_id
//! (sha256 24 ký tự) + redaction.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::pyjson::{dumps_compact_unicode, py_repr, py_repr_str};
use crate::pyutil::sha256_hex;

pub const STRUTS_PARSER_VERSION: &str = "2.0.0-mvp1";
pub const FRAMEWORK: &str = "struts2";

/// `(?:password|passwd|secret|token|credential|api[_-]?key)` case-insensitive.
pub fn is_sensitive_key(key: &str) -> bool {
    let lower = key.to_lowercase();
    for marker in ["password", "passwd", "secret", "token", "credential"] {
        if lower.contains(marker) {
            return true;
        }
    }
    // api[_-]?key — match "apikey", "api-key", "api_key" (và chuỗi chứa nó).
    let mut rest = lower.as_str();
    while let Some(index) = rest.find("api") {
        let tail = &rest[index + 3..];
        if let Some(stripped) = tail.strip_prefix('_').or_else(|| tail.strip_prefix('-')) {
            if stripped.starts_with("key") {
                return true;
            }
        } else if tail.starts_with("key") {
            return true;
        }
        rest = &rest[index + 3..];
    }
    false
}

/// `stable_id(kind, *parts)` — `"\x1f".join(str(p).strip())` + sha256
/// của `f"{kind}\x1e{payload}"`, hex[:24].
pub fn stable_id(kind: &str, parts: &[String]) -> String {
    let joined: Vec<String> = parts.iter().map(|part| part.trim().to_string()).collect();
    let payload = joined.join("\u{1f}");
    let digest = sha256_hex(format!("{kind}\u{1e}{payload}").as_bytes());
    format!("struts::{}::{}", kind.to_lowercase(), &digest[..24])
}

/// `_graph_value` — scalar giữ nguyên; dict/list/tuple → json dumps
/// (sort_keys, compact, ensure_ascii=False).
pub fn graph_value(value: &Value) -> Value {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => value.clone(),
        other => Value::String(dumps_compact_unicode(other)),
    }
}

fn redact_payload(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, item) in map {
                let replacement = if is_sensitive_key(key) {
                    Value::String("[REDACTED]".to_string())
                } else {
                    redact_payload(item)
                };
                out.insert(key.clone(), replacement);
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(redact_payload).collect()),
        other => other.clone(),
    }
}

/// `_safe_properties` — sorted keys, redact key nhạy cảm, graph_value payload.
pub fn safe_properties(properties: &Map<String, Value>) -> Map<String, Value> {
    let mut keys: Vec<&String> = properties.keys().collect();
    keys.sort();
    let mut out = Map::new();
    for key in keys {
        let value = &properties[key.as_str()];
        let replacement = if is_sensitive_key(key) {
            Value::String("[REDACTED]".to_string())
        } else {
            graph_value(&redact_payload(value))
        };
        out.insert(key.clone(), replacement);
    }
    out
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct SourceSpan {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub start_column: i64,
    pub end_column: i64,
}

impl SourceSpan {
    pub fn from_path(file_path: &str) -> Self {
        Self { file_path: file_path.to_string(), ..Default::default() }
    }
}

#[derive(Debug, Clone)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub severity: String,
    pub file_path: String,
}

impl Diagnostic {
    pub fn new(code: &str, message: &str, severity: &str, file_path: &str) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
            severity: severity.to_string(),
            file_path: file_path.to_string(),
        }
    }
}

pub type Params = BTreeMap<String, String>;

#[derive(Debug, Clone)]
pub struct InterceptorRef {
    pub name: String,
    pub params: Params,
}

#[derive(Debug, Clone)]
pub struct InterceptorConfig {
    pub name: String,
    pub class_name: String,
    pub params: Params,
}

#[derive(Debug, Clone)]
pub struct InterceptorStackConfig {
    pub name: String,
    pub refs: Vec<InterceptorRef>,
}

#[derive(Debug, Clone)]
pub struct ResultTypeConfig {
    pub name: String,
    pub class_name: String,
    pub default: bool,
    pub params: Params,
}

#[derive(Debug, Clone)]
pub struct ResultConfig {
    pub name: String,
    pub type_name: String,
    pub location: String,
    pub params: Params,
}

#[derive(Debug, Clone)]
pub struct ExceptionMappingConfig {
    pub exception: String,
    pub result: String,
}

#[derive(Debug, Clone)]
pub struct ActionConfig {
    pub name: String,
    pub class_name: String,
    pub method: String,
    pub interceptor_refs: Vec<InterceptorRef>,
    pub results: Vec<ResultConfig>,
    pub exception_mappings: Vec<ExceptionMappingConfig>,
    pub params: Params,
    pub source: SourceSpan,
}

#[derive(Debug, Clone)]
pub struct PackageConfig {
    pub name: String,
    pub namespace: String,
    pub extends: Vec<String>,
    pub default_interceptor_ref: String,
    pub default_result_type: String,
    pub interceptors: Vec<InterceptorConfig>,
    pub interceptor_stacks: Vec<InterceptorStackConfig>,
    pub result_types: Vec<ResultTypeConfig>,
    pub global_results: Vec<ResultConfig>,
    pub exception_mappings: Vec<ExceptionMappingConfig>,
    pub actions: Vec<ActionConfig>,
    pub source: SourceSpan,
}

#[derive(Debug, Clone, Default)]
pub struct StrutsXmlData {
    pub packages: Vec<PackageConfig>,
    pub constants: Params,
    pub includes: Vec<String>,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone)]
pub struct WebFilterConfig {
    pub name: String,
    pub class_name: String,
    pub url_patterns: Vec<String>,
    pub init_params: Params,
    pub source: SourceSpan,
}

#[derive(Debug, Clone, Default)]
pub struct WebXmlData {
    pub filters: Vec<WebFilterConfig>,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone)]
pub struct ValidationRule {
    pub target: String,
    pub method: String,
    pub validator_type: String,
    pub field_name: String,
    pub message: String,
    pub message_key: String,
    pub params: Params,
    pub source: SourceSpan,
}

#[derive(Debug, Clone, Default)]
pub struct ValidationData {
    pub rules: Vec<ValidationRule>,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Debug, Clone)]
pub struct StrutsFact {
    pub kind: String,
    pub stable_id: String,
    pub name: String,
    pub source: SourceSpan,
    pub project_id: String,
    pub project_name: String,
    pub module_id: String,
    pub confidence: f64,
    pub extraction_method: String,
    pub resolution_status: String,
    pub properties: Map<String, Value>,
}

impl StrutsFact {
    /// `to_graph_node`.
    pub fn to_graph_node(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.stable_id));
        row.insert("semantic_id".into(), json!(self.stable_id));
        row.insert("symbol_id".into(), json!(self.stable_id));
        row.insert("name".into(), json!(self.name));
        row.insert("kind".into(), json!(self.kind));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("project_name".into(), json!(self.project_name));
        row.insert("module_id".into(), json!(self.module_id));
        row.insert("language".into(), json!("java"));
        row.insert("framework".into(), json!(FRAMEWORK));
        row.insert("file_path".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("extraction_method".into(), json!(self.extraction_method));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("parser_version".into(), json!(STRUTS_PARSER_VERSION));
        for (key, value) in safe_properties(&self.properties) {
            if !row.contains_key(&key) {
                row.insert(key, value);
            }
        }
        row
    }
}

#[derive(Debug, Clone)]
pub struct StrutsRelationship {
    pub stable_id: String,
    pub from_id: String,
    pub to_id: String,
    pub from_label: String,
    pub to_label: String,
    pub rel_type: String,
    pub project_id: String,
    pub module_id: String,
    pub source: SourceSpan,
    pub confidence: f64,
    pub resolution_status: String,
    pub reason: String,
    pub properties: Map<String, Value>,
}

impl StrutsRelationship {
    /// `to_graph_row`.
    pub fn to_graph_row(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.stable_id));
        row.insert("semantic_id".into(), json!(self.stable_id));
        row.insert("from_id".into(), json!(self.from_id));
        row.insert("to_id".into(), json!(self.to_id));
        row.insert("from_label".into(), json!(self.from_label));
        row.insert("to_label".into(), json!(self.to_label));
        row.insert("type".into(), json!(self.rel_type));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("module_id".into(), json!(self.module_id));
        row.insert("framework".into(), json!(FRAMEWORK));
        row.insert("source_file".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("reason".into(), json!(self.reason));
        row.insert("properties".into(), Value::Object(safe_properties(&self.properties)));
        row
    }
}

#[derive(Debug, Clone, Default)]
pub struct StrutsAnalysisResult {
    pub project_id: String,
    pub project_name: String,
    pub root: String,
    pub semantic_facts: Vec<StrutsFact>,
    pub relationships: Vec<StrutsRelationship>,
    pub diagnostics: Vec<Diagnostic>,
    pub coverage_status: String,
    pub parser_version: String,
}

/// `repr(sorted(payload.items()))` — identity component của relationship
/// stable_id (đúng thứ tự sorted-key của Python).
pub fn repr_sorted_items(properties: &Map<String, Value>) -> String {
    let mut keys: Vec<&String> = properties.keys().collect();
    keys.sort();
    let inner: Vec<String> = keys
        .iter()
        .map(|key| format!("({}, {})", py_repr_str(key), py_repr(&properties[key.as_str()])))
        .collect();
    format!("[{}]", inner.join(", "))
}

/// `_redact_payload(asdict(...))` + `root = "."` cho `to_dict()` output.
pub fn analysis_result_to_dict(result: &StrutsAnalysisResult) -> Value {
    crate::pyjson::py_object(vec![
        ("project_id".into(), json!(result.project_id)),
        ("project_name".into(), json!(result.project_name)),
        ("root".into(), json!(".")),
        (
            "semantic_facts".into(),
            Value::Array(
                result
                    .semantic_facts
                    .iter()
                    .map(|fact| {
                        crate::pyjson::py_object(vec![
                            ("kind".into(), json!(fact.kind)),
                            ("stable_id".into(), json!(fact.stable_id)),
                            ("name".into(), json!(fact.name)),
                            (
                                "source".into(),
                                crate::pyjson::py_object(vec![
                                    ("file_path".into(), json!(fact.source.file_path)),
                                    ("start_line".into(), json!(fact.source.start_line)),
                                    ("end_line".into(), json!(fact.source.end_line)),
                                    ("start_column".into(), json!(fact.source.start_column)),
                                    ("end_column".into(), json!(fact.source.end_column)),
                                ]),
                            ),
                            ("project_id".into(), json!(fact.project_id)),
                            ("project_name".into(), json!(fact.project_name)),
                            ("module_id".into(), json!(fact.module_id)),
                            ("confidence".into(), json!(fact.confidence)),
                            ("extraction_method".into(), json!(fact.extraction_method)),
                            ("resolution_status".into(), json!(fact.resolution_status)),
                            ("properties".into(), redact_payload(&Value::Object(fact.properties.clone()))),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "relationships".into(),
            Value::Array(
                result
                    .relationships
                    .iter()
                    .map(|item| {
                        crate::pyjson::py_object(vec![
                            ("stable_id".into(), json!(item.stable_id)),
                            ("from_id".into(), json!(item.from_id)),
                            ("to_id".into(), json!(item.to_id)),
                            ("from_label".into(), json!(item.from_label)),
                            ("to_label".into(), json!(item.to_label)),
                            ("type".into(), json!(item.rel_type)),
                            ("project_id".into(), json!(item.project_id)),
                            ("module_id".into(), json!(item.module_id)),
                            (
                                "source".into(),
                                crate::pyjson::py_object(vec![
                                    ("file_path".into(), json!(item.source.file_path)),
                                    ("start_line".into(), json!(item.source.start_line)),
                                    ("end_line".into(), json!(item.source.end_line)),
                                    ("start_column".into(), json!(item.source.start_column)),
                                    ("end_column".into(), json!(item.source.end_column)),
                                ]),
                            ),
                            ("confidence".into(), json!(item.confidence)),
                            ("resolution_status".into(), json!(item.resolution_status)),
                            ("reason".into(), json!(item.reason)),
                            ("properties".into(), redact_payload(&Value::Object(item.properties.clone()))),
                        ])
                    })
                    .collect(),
            ),
        ),
        (
            "diagnostics".into(),
            Value::Array(
                result
                    .diagnostics
                    .iter()
                    .map(|item| {
                        crate::pyjson::py_object(vec![
                            ("code".into(), json!(item.code)),
                            ("message".into(), json!(item.message)),
                            ("severity".into(), json!(item.severity)),
                            ("file_path".into(), json!(item.file_path)),
                        ])
                    })
                    .collect(),
            ),
        ),
        ("coverage_status".into(), json!(result.coverage_status)),
        ("parser_version".into(), json!(result.parser_version)),
    ])
}

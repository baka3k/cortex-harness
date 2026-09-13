//! Port `tools/servlet_jsp/models.py` — ServletJspFact/Relationship,
//! stable ids, redaction, budgets, result serialization (asdict).

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use crate::pyjson::{dumps_compact, py_truthy};
use crate::pyutil::sha256_hex;

pub const SERVLET_JSP_PARSER_VERSION: &str = "servlet-jsp-v2026-07-13-1";
pub const FRAMEWORK: &str = "servlet_jsp";

/// `(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential|authorization|cookie|session(?:id)?)` IGNORECASE.
pub fn is_sensitive_key(key: &str) -> bool {
    let lower = key.to_lowercase();
    for marker in ["password", "passwd", "secret", "token", "credential", "authorization", "cookie", "session"] {
        if lower.contains(marker) {
            return true;
        }
    }
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

/// `stable_digest(*parts, length=20)` — sha256 của `"\x1f".join(str(part))`.
pub fn stable_digest(parts: &[String], length: usize) -> String {
    let payload = parts.join("\u{1f}");
    sha256_hex(payload.as_bytes())[..length].to_string()
}

/// `stable_semantic_id`.
pub fn stable_semantic_id(kind: &str, project_id: &str, module_id: &str, parts: &[String]) -> String {
    let lower = kind.to_lowercase();
    let mut safe_kind = String::new();
    for ch in lower.chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_' {
            safe_kind.push(ch);
        } else {
            safe_kind.push('_');
        }
    }
    let safe_kind = safe_kind.trim_matches('_');
    let safe_kind = if safe_kind.is_empty() { "fact" } else { safe_kind };
    let mut all_parts = vec![project_id.to_string(), module_id.to_string()];
    all_parts.extend(parts.iter().cloned());
    format!("servlet_jsp::{safe_kind}::{}", stable_digest(&all_parts, 20))
}

pub fn generation_storage_id(semantic_id: &str, generation_id: &str) -> String {
    if generation_id.is_empty() {
        semantic_id.to_string()
    } else {
        format!("{semantic_id}::generation::{generation_id}")
    }
}

const NAME_FIELDS: [&str; 6] = ["name", "param_name", "key", "config_key", "raw_name", "config_key"];
const VALUE_FIELDS: [&str; 5] = ["value", "param_value", "config_value", "raw_value", "resolved_value"];

/// `redact_value`.
pub fn redact_value(key: &str, value: &Value, max_length: usize) -> Value {
    if is_sensitive_key(key) {
        return Value::String("[REDACTED]".to_string());
    }
    match value {
        Value::String(text) => {
            if text.chars().count() <= max_length {
                value.clone()
            } else {
                let digest = sha256_hex(text.as_bytes())[..16].to_string();
                let prefix: String = text.chars().take(max_length).collect();
                Value::String(format!("{prefix}...[sha256:{digest}]"))
            }
        }
        Value::Object(map) => {
            let sensitive_record = map.iter().any(|(nested_key, nested_value)| {
                NAME_FIELDS.contains(&nested_key.as_str())
                    && nested_value.is_string()
                    && is_sensitive_key(nested_value.as_str().unwrap_or(""))
            });
            let mut sorted_keys: Vec<&String> = map.keys().collect();
            sorted_keys.sort();
            let mut out = Map::new();
            for nested_key in sorted_keys {
                let nested_value = &map[nested_key.as_str()];
                let replacement = if sensitive_record
                    && (NAME_FIELDS.contains(&nested_key.as_str()) || VALUE_FIELDS.contains(&nested_key.as_str()))
                {
                    Value::String("[REDACTED]".to_string())
                } else {
                    redact_value(nested_key, nested_value, max_length)
                };
                out.insert(nested_key.clone(), replacement);
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(|item| redact_value(key, item, max_length)).collect()),
        other => other.clone(),
    }
}

/// `graph_property_value` — scalar/list-of-scalars giữ; khác → json dumps.
pub fn graph_property_value(value: &Value) -> Value {
    match value {
        Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => value.clone(),
        Value::Array(items) => {
            if items.iter().all(|item| {
                matches!(item, Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_))
            }) {
                value.clone()
            } else {
                Value::String(dumps_compact(value))
            }
        }
        other => Value::String(dumps_compact(other)),
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, PartialOrd, Ord)]
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

    pub fn to_value(&self) -> Value {
        crate::pyjson::py_object(vec![
            ("file_path".into(), json!(self.file_path)),
            ("start_line".into(), json!(self.start_line)),
            ("end_line".into(), json!(self.end_line)),
            ("start_column".into(), json!(self.start_column)),
            ("end_column".into(), json!(self.end_column)),
        ])
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
    pub hint: String,
    pub details: Map<String, Value>,
}

impl Diagnostic {
    #[allow(clippy::too_many_arguments)]
    pub fn new(code: &str, message: &str, severity: &str, file_path: &str, start_line: i64, end_line: i64) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
            severity: severity.to_string(),
            file_path: file_path.to_string(),
            start_line,
            end_line,
            hint: String::new(),
            details: Map::new(),
        }
    }

    pub fn to_value(&self) -> Value {
        crate::pyjson::py_object(vec![
            ("code".into(), json!(self.code)),
            ("message".into(), json!(self.message)),
            ("severity".into(), json!(self.severity)),
            ("file_path".into(), json!(self.file_path)),
            ("start_line".into(), json!(self.start_line)),
            ("end_line".into(), json!(self.end_line)),
            ("hint".into(), json!(self.hint)),
            ("details".into(), Value::Object(self.details.clone())),
        ])
    }
}

#[derive(Debug, Clone)]
pub struct ParserCapability {
    pub language: String,
    pub available: bool,
    pub mandatory: bool,
    pub parser: String,
    pub package: String,
    pub package_version: String,
    pub abi_version: String,
    pub status: String,
    pub message: String,
}

impl ParserCapability {
    pub fn to_value(&self) -> Value {
        crate::pyjson::py_object(vec![
            ("language".into(), json!(self.language)),
            ("available".into(), json!(self.available)),
            ("mandatory".into(), json!(self.mandatory)),
            ("parser".into(), json!(self.parser)),
            ("package".into(), json!(self.package)),
            ("package_version".into(), json!(self.package_version)),
            ("abi_version".into(), json!(self.abi_version)),
            ("status".into(), json!(self.status)),
            ("message".into(), json!(self.message)),
        ])
    }
}

/// `ResourceBudgets` — giá trị default như Python dataclass.
#[derive(Debug, Clone)]
pub struct ResourceBudgets {
    pub max_source_bytes: i64,
    pub max_properties_bytes: i64,
    pub max_el_bytes: i64,
    pub max_el_tokens: i64,
    pub max_el_nesting: i64,
    pub max_jsp_regions: i64,
    pub max_diagnostics_per_file: i64,
    pub max_include_depth: i64,
    pub max_include_edges_per_module: i64,
    pub max_constant_steps_per_file: i64,
    pub max_endpoint_filter_relationships: i64,
    pub max_artifacts_per_project: i64,
    pub max_total_source_bytes: i64,
    pub max_dependency_entries: i64,
    pub max_facts_per_project: i64,
    pub max_relationships_per_project: i64,
    pub max_diagnostics_per_project: i64,
    pub max_wall_time_seconds: f64,
    pub max_peak_rss_bytes: i64,
}

impl Default for ResourceBudgets {
    fn default() -> Self {
        Self {
            max_source_bytes: 4 * 1024 * 1024,
            max_properties_bytes: 1024 * 1024,
            max_el_bytes: 64 * 1024,
            max_el_tokens: 2048,
            max_el_nesting: 64,
            max_jsp_regions: 50_000,
            max_diagnostics_per_file: 1_000,
            max_include_depth: 32,
            max_include_edges_per_module: 100_000,
            max_constant_steps_per_file: 10_000,
            max_endpoint_filter_relationships: 250_000,
            max_artifacts_per_project: 250_000,
            max_total_source_bytes: 2 * 1024 * 1024 * 1024,
            max_dependency_entries: 2_000_000,
            max_facts_per_project: 2_000_000,
            max_relationships_per_project: 4_000_000,
            max_diagnostics_per_project: 100_000,
            max_wall_time_seconds: 1_800.0,
            max_peak_rss_bytes: 8 * 1024 * 1024 * 1024,
        }
    }
}

impl ResourceBudgets {
    /// `fingerprint` — asdict trừ hai guard vận hành, json compact sort_keys,
    /// sha256[:20].
    pub fn fingerprint(&self) -> String {
        // Python pop 2 operational guard rồi json.dumps(sort_keys, compact).
        let entries: Vec<(String, Value)> = budget_entries(self)
            .into_iter()
            .filter(|(key, _)| key != "max_wall_time_seconds" && key != "max_peak_rss_bytes")
            .collect();
        let payload = dumps_compact(&crate::pyjson::py_object(entries));
        stable_digest(&[payload], 20)
    }
}

/// asdict(budgets) theo thứ tự khai báo field.
fn budget_entries(budgets: &ResourceBudgets) -> Vec<(String, Value)> {
    vec![
        ("max_source_bytes".into(), json!(budgets.max_source_bytes)),
        ("max_properties_bytes".into(), json!(budgets.max_properties_bytes)),
        ("max_el_bytes".into(), json!(budgets.max_el_bytes)),
        ("max_el_tokens".into(), json!(budgets.max_el_tokens)),
        ("max_el_nesting".into(), json!(budgets.max_el_nesting)),
        ("max_jsp_regions".into(), json!(budgets.max_jsp_regions)),
        ("max_diagnostics_per_file".into(), json!(budgets.max_diagnostics_per_file)),
        ("max_include_depth".into(), json!(budgets.max_include_depth)),
        (
            "max_include_edges_per_module".into(),
            json!(budgets.max_include_edges_per_module),
        ),
        (
            "max_constant_steps_per_file".into(),
            json!(budgets.max_constant_steps_per_file),
        ),
        (
            "max_endpoint_filter_relationships".into(),
            json!(budgets.max_endpoint_filter_relationships),
        ),
        (
            "max_artifacts_per_project".into(),
            json!(budgets.max_artifacts_per_project),
        ),
        (
            "max_total_source_bytes".into(),
            json!(budgets.max_total_source_bytes),
        ),
        (
            "max_dependency_entries".into(),
            json!(budgets.max_dependency_entries),
        ),
        ("max_facts_per_project".into(), json!(budgets.max_facts_per_project)),
        (
            "max_relationships_per_project".into(),
            json!(budgets.max_relationships_per_project),
        ),
        (
            "max_diagnostics_per_project".into(),
            json!(budgets.max_diagnostics_per_project),
        ),
        (
            "max_wall_time_seconds".into(),
            json!(budgets.max_wall_time_seconds),
        ),
        ("max_peak_rss_bytes".into(), json!(budgets.max_peak_rss_bytes)),
    ]
}

#[derive(Debug, Clone)]
pub struct ServletJspArtifact {
    pub kind: String,
    pub file_path: String,
    pub module_id: String,
    pub module_path: String,
    pub evidence: Vec<String>,
    pub confidence: f64,
    pub source: SourceSpan,
}

#[derive(Debug, Clone)]
pub struct ServletJspModule {
    pub module_id: String,
    pub root: String,
    pub rel_path: String,
    pub java_files: Vec<String>,
    pub descriptor_files: Vec<String>,
    pub jsp_files: Vec<String>,
    pub properties_files: Vec<String>,
    pub build_files: Vec<String>,
    pub static_files: Vec<String>,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

#[derive(Debug, Clone, Default)]
pub struct ServletJspFact {
    pub kind: String,
    pub stable_id: String,
    pub name: String,
    pub source: SourceSpan,
    pub project_id: String,
    pub project_name: String,
    pub module_id: String,
    pub language: String,
    pub confidence: f64,
    pub extraction_method: String,
    pub resolution_status: String,
    pub raw_value: String,
    pub resolved_value: String,
    pub source_symbol_id: String,
    pub properties: Map<String, Value>,
}

impl ServletJspFact {
    /// `to_graph_node(generation_id)`.
    pub fn to_graph_node(&self, generation_id: &str) -> Map<String, Value> {
        let semantic_id = self.stable_id.clone();
        let safe_payload = redact_value(
            "fact",
            &crate::pyjson::py_object(vec![
                ("name".into(), json!(self.name)),
                ("raw_value".into(), json!(self.raw_value)),
                ("resolved_value".into(), json!(self.resolved_value)),
                ("properties".into(), Value::Object(self.properties.clone())),
            ]),
            4096,
        );
        let payload_map = safe_payload.as_object().cloned().unwrap_or_default();
        let mut row = Map::new();
        row.insert("id".into(), json!(generation_storage_id(&semantic_id, generation_id)));
        row.insert("semantic_id".into(), json!(semantic_id));
        row.insert("symbol_id".into(), json!(semantic_id));
        row.insert("generation_id".into(), json!(generation_id));
        row.insert("name".into(), payload_map.get("name").cloned().unwrap_or(Value::Null));
        row.insert("kind".into(), json!(self.kind));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("project_name".into(), json!(self.project_name));
        row.insert("module_id".into(), json!(self.module_id));
        row.insert("language".into(), json!(self.language));
        row.insert("framework".into(), json!(FRAMEWORK));
        row.insert("file_path".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("start_column".into(), json!(self.source.start_column));
        row.insert("end_column".into(), json!(self.source.end_column));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("extraction_method".into(), json!(self.extraction_method));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert(
            "raw_value".into(),
            payload_map.get("raw_value").cloned().unwrap_or(Value::Null),
        );
        row.insert(
            "resolved_value".into(),
            payload_map.get("resolved_value").cloned().unwrap_or(Value::Null),
        );
        row.insert("source_symbol_id".into(), json!(self.source_symbol_id));
        row.insert("parser_version".into(), json!(SERVLET_JSP_PARSER_VERSION));
        if let Some(Value::Object(properties)) = payload_map.get("properties") {
            let mut keys: Vec<&String> = properties.keys().collect();
            keys.sort();
            for key in keys {
                if !row.contains_key(key) {
                    row.insert(key.clone(), graph_property_value(&properties[key.as_str()]));
                }
            }
        }
        row
    }
}

#[derive(Debug, Clone)]
pub struct ServletJspRelationship {
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
    pub from_generated: bool,
    pub to_generated: bool,
}

impl ServletJspRelationship {
    /// `to_graph_row(generation_id)`.
    pub fn to_graph_row(&self, generation_id: &str) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert(
            "id".into(),
            json!(generation_storage_id(&self.stable_id, generation_id)),
        );
        row.insert("semantic_id".into(), json!(self.stable_id));
        row.insert(
            "from_id".into(),
            json!(if self.from_generated {
                generation_storage_id(&self.from_id, generation_id)
            } else {
                self.from_id.clone()
            }),
        );
        row.insert(
            "to_id".into(),
            json!(if self.to_generated {
                generation_storage_id(&self.to_id, generation_id)
            } else {
                self.to_id.clone()
            }),
        );
        row.insert("from_label".into(), json!(self.from_label));
        row.insert("to_label".into(), json!(self.to_label));
        row.insert("type".into(), json!(self.rel_type));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("module_id".into(), json!(self.module_id));
        row.insert("generation_id".into(), json!(generation_id));
        row.insert("framework".into(), json!(FRAMEWORK));
        row.insert("source_file".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("reason".into(), json!(self.reason));
        let redacted = redact_value("properties", &Value::Object(self.properties.clone()), 4096);
        let mut properties = Map::new();
        if let Value::Object(map) = redacted {
            for (key, value) in map {
                properties.insert(key, graph_property_value(&value));
            }
        }
        row.insert("properties".into(), Value::Object(properties));
        row
    }
}

#[derive(Debug, Clone, Default)]
pub struct ServletJspDependencyIndex {
    pub files: BTreeMap<String, Vec<String>>,
    pub components: BTreeMap<String, Vec<String>>,
    pub mappings: BTreeMap<String, Vec<String>>,
    pub views: BTreeMap<String, Vec<String>>,
    pub state_slots: BTreeMap<String, Vec<String>>,
}

impl ServletJspDependencyIndex {
    fn category(&self, name: &str) -> &BTreeMap<String, Vec<String>> {
        match name {
            "files" => &self.files,
            "components" => &self.components,
            "mappings" => &self.mappings,
            "views" => &self.views,
            "state_slots" => &self.state_slots,
            _ => unreachable!(),
        }
    }

    /// Public accessor cho pipeline bounded index.
    pub fn category_mut_public(&mut self, name: &str) -> &mut BTreeMap<String, Vec<String>> {
        self.category_mut(name)
    }

    fn category_mut(&mut self, name: &str) -> &mut BTreeMap<String, Vec<String>> {
        match name {
            "files" => &mut self.files,
            "components" => &mut self.components,
            "mappings" => &mut self.mappings,
            "views" => &mut self.views,
            "state_slots" => &mut self.state_slots,
            _ => unreachable!(),
        }
    }

    pub fn to_value(&self) -> Value {
        crate::pyjson::py_object(
            ["files", "components", "mappings", "views", "state_slots"]
                .iter()
                .map(|category| {
                    let map = self.category(category);
                    (
                        (*category).to_string(),
                        Value::Object(
                            map.iter()
                                .map(|(key, values)| (key.clone(), json!(values)))
                                .collect(),
                        ),
                    )
                })
                .collect(),
        )
    }
}

#[derive(Debug, Clone, Default)]
pub struct ServletJspAnalysisResult {
    pub project_id: String,
    pub project_name: String,
    pub root: String,
    pub modules: Vec<ServletJspModule>,
    pub artifacts: Vec<ServletJspArtifact>,
    pub parser_capabilities: Vec<ParserCapability>,
    pub semantic_facts: Vec<ServletJspFact>,
    pub relationships: Vec<ServletJspRelationship>,
    pub dependency_index: ServletJspDependencyIndex,
    pub diagnostics: Vec<Diagnostic>,
    pub coverage_status: String,
    pub missing_anchor_count: i64,
    pub ambiguity_count: i64,
    pub truncation_count: i64,
}

impl ServletJspAnalysisResult {
    /// `to_dict()` — asdict + redact + root="." (modules root=".").
    pub fn to_dict(&self) -> Value {
        let redacted = redact_value("result", &self.asdict_value(), 4096);
        let mut payload = redacted.as_object().cloned().unwrap_or_default();
        payload.insert("root".into(), json!("."));
        if let Some(Value::Array(modules)) = payload.get_mut("modules") {
            for module in modules.iter_mut() {
                if let Some(map) = module.as_object_mut() {
                    map.insert("root".into(), json!("."));
                }
            }
        }
        Value::Object(payload)
    }

    /// `asdict()` — nested dataclass → maps; field order theo khai báo.
    pub fn asdict_value(&self) -> Value {
        crate::pyjson::py_object(vec![
            ("project_id".into(), json!(self.project_id)),
            ("project_name".into(), json!(self.project_name)),
            ("root".into(), json!(self.root)),
            ("modules".into(), Value::Array(self.modules.iter().map(module_to_value).collect())),
            (
                "artifacts".into(),
                Value::Array(
                    self.artifacts
                        .iter()
                        .map(|artifact| {
                            crate::pyjson::py_object(vec![
                                ("kind".into(), json!(artifact.kind)),
                                ("file_path".into(), json!(artifact.file_path)),
                                ("module_id".into(), json!(artifact.module_id)),
                                ("module_path".into(), json!(artifact.module_path)),
                                ("evidence".into(), json!(artifact.evidence)),
                                ("confidence".into(), json!(artifact.confidence)),
                                ("source".into(), artifact.source.to_value()),
                            ])
                        })
                        .collect(),
                ),
            ),
            (
                "parser_capabilities".into(),
                Value::Array(self.parser_capabilities.iter().map(ParserCapability::to_value).collect()),
            ),
            (
                "semantic_facts".into(),
                Value::Array(self.semantic_facts.iter().map(fact_to_value).collect()),
            ),
            (
                "relationships".into(),
                Value::Array(self.relationships.iter().map(relationship_to_value).collect()),
            ),
            ("dependency_index".into(), self.dependency_index.to_value()),
            ("diagnostics".into(), Value::Array(self.diagnostics.iter().map(Diagnostic::to_value).collect())),
            ("coverage_status".into(), json!(self.coverage_status)),
            ("missing_anchor_count".into(), json!(self.missing_anchor_count)),
            ("ambiguity_count".into(), json!(self.ambiguity_count)),
            ("truncation_count".into(), json!(self.truncation_count)),
            (
                "parser_version".into(),
                json!(SERVLET_JSP_PARSER_VERSION),
            ),
        ])
    }
}

pub fn module_to_value(module: &ServletJspModule) -> Value {
    crate::pyjson::py_object(vec![
        ("module_id".into(), json!(module.module_id)),
        ("root".into(), json!(module.root)),
        ("rel_path".into(), json!(module.rel_path)),
        ("java_files".into(), json!(module.java_files)),
        ("descriptor_files".into(), json!(module.descriptor_files)),
        ("jsp_files".into(), json!(module.jsp_files)),
        ("properties_files".into(), json!(module.properties_files)),
        ("build_files".into(), json!(module.build_files)),
        ("static_files".into(), json!(module.static_files)),
        ("evidence".into(), json!(module.evidence)),
        ("confidence".into(), json!(module.confidence)),
    ])
}

pub fn fact_to_value(fact: &ServletJspFact) -> Value {
    crate::pyjson::py_object(vec![
        ("kind".into(), json!(fact.kind)),
        ("stable_id".into(), json!(fact.stable_id)),
        ("name".into(), json!(fact.name)),
        ("source".into(), fact.source.to_value()),
        ("project_id".into(), json!(fact.project_id)),
        ("project_name".into(), json!(fact.project_name)),
        ("module_id".into(), json!(fact.module_id)),
        ("language".into(), json!(fact.language)),
        ("confidence".into(), json!(fact.confidence)),
        ("extraction_method".into(), json!(fact.extraction_method)),
        ("resolution_status".into(), json!(fact.resolution_status)),
        ("raw_value".into(), json!(fact.raw_value)),
        ("resolved_value".into(), json!(fact.resolved_value)),
        ("source_symbol_id".into(), json!(fact.source_symbol_id)),
        (
            "properties".into(),
            Value::Object(fact.properties.clone()),
        ),
    ])
}

pub fn relationship_to_value(item: &ServletJspRelationship) -> Value {
    crate::pyjson::py_object(vec![
        ("stable_id".into(), json!(item.stable_id)),
        ("from_id".into(), json!(item.from_id)),
        ("to_id".into(), json!(item.to_id)),
        ("from_label".into(), json!(item.from_label)),
        ("to_label".into(), json!(item.to_label)),
        ("type".into(), json!(item.rel_type)),
        ("project_id".into(), json!(item.project_id)),
        ("module_id".into(), json!(item.module_id)),
        ("source".into(), item.source.to_value()),
        ("confidence".into(), json!(item.confidence)),
        ("resolution_status".into(), json!(item.resolution_status)),
        ("reason".into(), json!(item.reason)),
        ("properties".into(), Value::Object(item.properties.clone())),
        ("from_generated".into(), json!(item.from_generated)),
        ("to_generated".into(), json!(item.to_generated)),
    ])
}

/// Helper truthy re-export.
#[allow(dead_code)]
pub fn truthy(value: &Value) -> bool {
    py_truthy(value)
}

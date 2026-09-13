//! Port `tools/mybatis/models.py` — fact structs + `MyBatisFact` /
//! `MyBatisRelationship` graph rows + `graph_property_value`.
//!
//! Chỉ mặt graph-plane được port đầy đủ (semantic_facts + relationships +
//! diagnostics count); artifact JSON payload là plane phụ (không parity).

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

pub const MYBATIS_PARSER_VERSION: &str = "mybatis-v2026-07-13-1";

/// `models.graph_property_value` — scalar giữ nguyên; list/tuple của scalar
/// giữ nguyên; còn lại JSON dump (ensure_ascii, sort_keys, compact).
pub fn graph_property_value(value: &Value) -> Value {
    match value {
        Value::Null
        | Value::Bool(_)
        | Value::Number(_)
        | Value::String(_) => value.clone(),
        Value::Array(items) => {
            if items.iter().all(|item| {
                matches!(
                    item,
                    Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_)
                )
            }) {
                value.clone()
            } else {
                Value::String(dump_sorted_json(value))
            }
        }
        Value::Object(_) => Value::String(dump_sorted_json(value)),
    }
}

/// `json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))`.
pub fn dump_sorted_json(value: &Value) -> String {
    fn write_value(out: &mut String, value: &Value, first: bool) {
        let _ = first;
        match value {
            Value::Null => out.push_str("null"),
            Value::Bool(true) => out.push_str("true"),
            Value::Bool(false) => out.push_str("false"),
            Value::Number(num) => out.push_str(&num.to_string()),
            Value::String(text) => out.push_str(&dump_json_string(text)),
            Value::Array(items) => {
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    write_value(out, item, index == 0);
                }
                out.push(']');
            }
            Value::Object(map) => {
                let mut keys: Vec<&String> = map.keys().collect();
                keys.sort();
                out.push('{');
                for (index, key) in keys.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    out.push_str(&dump_json_string(key));
                    out.push(':');
                    write_value(out, map.get(*key).unwrap(), index == 0);
                }
                out.push('}');
            }
        }
    }
    let mut out = String::new();
    write_value(&mut out, value, true);
    out
}

/// JSON string quoting với ensure_ascii=True (non-ASCII → \\uXXXX).
fn dump_json_string(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) <= 0x7f => out.push(c),
            c => {
                let cp = c as u32;
                if cp <= 0xffff {
                    out.push_str(&format!("\\u{:04x}", cp));
                } else {
                    // Python ensure_ascii mã hóa surrogate pair thành 2 escape.
                    let high = 0xd800 + ((cp - 0x10000) >> 10);
                    let low = 0xdc00 + ((cp - 0x10000) & 0x3ff);
                    out.push_str(&format!("\\u{:04x}\\u{:04x}", high, low));
                }
            }
        }
    }
    out.push('"');
    out
}

/// `models.SourceSpan`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
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

    pub fn with_points(
        file_path: &str,
        start_line: i64,
        end_line: i64,
        start_column: i64,
        end_column: i64,
    ) -> Self {
        Self {
            file_path: file_path.to_string(),
            start_line,
            end_line,
            start_column,
            end_column,
        }
    }
}

/// `models.Diagnostic` — chỉ count ảnh hưởng stdout, không vào graph.
#[derive(Debug, Clone)]
pub struct Diagnostic {
    #[allow(dead_code)]
    pub code: String,
    #[allow(dead_code)]
    pub message: String,
}

impl Diagnostic {
    pub fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.to_string(),
            message: message.to_string(),
        }
    }
}

/// `MyBatisAnnotationFact`.
#[derive(Debug, Clone, Default)]
pub struct AnnotationFact {
    pub name: String,
    pub resolved_name: String,
    pub raw_arguments: String,
    pub source: SourceSpan,
}

/// `MyBatisModule`.
#[derive(Debug, Clone, Default)]
pub struct Module {
    pub rel_path: String,
    pub mapper_xml_files: Vec<String>,
    pub config_xml_files: Vec<String>,
    pub java_files: Vec<String>,
    pub build_files: Vec<String>,
    pub spring_config_files: Vec<String>,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

/// `MyBatisArtifact`.
#[derive(Debug, Clone, Default)]
pub struct Artifact {
    pub kind: String,
    pub file_path: String,
    pub module_path: String,
    pub evidence: Vec<String>,
    pub confidence: f64,
    pub source: SourceSpan,
}

/// `MyBatisMapperParameterFact`.
#[derive(Debug, Clone, Default)]
pub struct MapperParameterFact {
    pub stable_id: String,
    pub mapper_method_id: String,
    pub name: String,
    pub position: i64,
    pub java_type: String,
    pub canonical_type: String,
    pub param_alias: String,
    pub special_role: String,
    pub source: SourceSpan,
    pub annotations: Vec<AnnotationFact>,
}

/// `MyBatisMapperMethodFact`.
#[derive(Debug, Clone, Default)]
pub struct MapperMethodFact {
    pub stable_id: String,
    pub java_symbol_id: String,
    pub mapper_fqcn: String,
    pub name: String,
    pub signature: String,
    pub return_type: String,
    pub parameter_types: Vec<String>,
    pub source: SourceSpan,
    pub bindable: bool,
    pub overload_count: i64,
    pub ambiguity_status: String,
    pub modifiers: Vec<String>,
    pub throws: Vec<String>,
    pub annotations: Vec<AnnotationFact>,
    pub parameters: Vec<MapperParameterFact>,
    pub has_body: bool,
}

/// `MyBatisMapperInterfaceFact`.
#[derive(Debug, Clone, Default)]
pub struct MapperInterfaceFact {
    pub stable_id: String,
    pub java_class_symbol_id: String,
    pub name: String,
    pub fqcn: String,
    pub file_path: String,
    pub source: SourceSpan,
    pub package_name: String,
    pub type_parameters: Vec<String>,
    pub extended_interfaces: Vec<String>,
    pub modifiers: Vec<String>,
    pub imports: Vec<String>,
    pub annotations: Vec<AnnotationFact>,
    pub methods: Vec<MapperMethodFact>,
}

/// `MyBatisJavaPropertyFact`.
#[derive(Debug, Clone, Default)]
pub struct JavaPropertyFact {
    pub stable_id: String,
    pub java_type_fqcn: String,
    pub property_name: String,
    pub property_type: String,
    pub source_kind: String,
    pub source: SourceSpan,
    pub readable: bool,
    pub writable: bool,
    pub source_symbol_id: String,
}

/// `MyBatisXmlDocumentFact`.
#[derive(Debug, Clone, Default)]
pub struct XmlDocumentFact {
    pub file_path: String,
    pub document_kind: String,
    pub root_tag: String,
    pub source: SourceSpan,
    pub namespace: String,
    pub doctype: String,
    pub parser_status: String,
}

/// `MyBatisIncludeFact`.
#[derive(Debug, Clone, Default)]
pub struct IncludeFact {
    pub stable_id: String,
    pub owner_id: String,
    pub refid: String,
    pub resolved_refid: String,
    pub source: SourceSpan,
    pub properties: BTreeMap<String, String>,
    pub resolution_status: String,
}

/// `MyBatisDynamicSqlNodeFact`.
#[derive(Debug, Clone, Default)]
pub struct DynamicNodeFact {
    pub stable_id: String,
    pub owner_id: String,
    pub tag: String,
    pub node_kind: String,
    pub source: SourceSpan,
    pub order: i64,
    pub text: String,
    pub attributes: BTreeMap<String, String>,
    pub test: String,
    pub branch_role: String,
    pub referenced_variables: Vec<String>,
}

/// `MyBatisStatementFact`.
#[derive(Debug, Clone, Default)]
pub struct StatementFact {
    pub stable_id: String,
    pub namespace: String,
    pub statement_id: String,
    pub statement_kind: String,
    pub source: SourceSpan,
    pub database_id: String,
    pub attributes: BTreeMap<String, String>,
    pub raw_body: String,
    pub expanded_body: String,
    pub includes: Vec<IncludeFact>,
    pub dynamic_nodes: Vec<DynamicNodeFact>,
    pub parser_status: String,
}

/// `MyBatisSqlFragmentFact`.
#[derive(Debug, Clone, Default)]
pub struct SqlFragmentFact {
    pub stable_id: String,
    pub namespace: String,
    pub fragment_id: String,
    pub source: SourceSpan,
    pub database_id: String,
    pub attributes: BTreeMap<String, String>,
    pub raw_body: String,
    pub expanded_body: String,
    pub includes: Vec<IncludeFact>,
}

/// `MyBatisResultMappingFact`.
#[derive(Debug, Clone, Default)]
pub struct ResultMappingFact {
    pub stable_id: String,
    pub result_map_id: String,
    pub mapping_kind: String,
    pub source: SourceSpan,
    pub property_name: String,
    pub column: String,
    pub java_type: String,
    pub jdbc_type: String,
    pub nested_select: String,
    pub nested_result_map: String,
    pub attributes: BTreeMap<String, String>,
}

/// `MyBatisResultMapFact`.
#[derive(Debug, Clone, Default)]
pub struct ResultMapFact {
    pub stable_id: String,
    pub namespace: String,
    pub result_map_id: String,
    pub source: SourceSpan,
    pub java_type: String,
    pub extends: String,
    pub auto_mapping: String,
    pub mappings: Vec<ResultMappingFact>,
}

/// `MyBatisConfigFact`.
#[derive(Debug, Clone, Default)]
pub struct ConfigFact {
    pub stable_id: String,
    pub file_path: String,
    pub source: SourceSpan,
    pub properties: BTreeMap<String, String>,
    pub settings: BTreeMap<String, String>,
    pub type_aliases: BTreeMap<String, String>,
    pub type_handlers: Vec<BTreeMap<String, String>>,
    pub plugins: Vec<BTreeMap<String, String>>,
    pub environments: Vec<BTreeMap<String, String>>,
    pub database_id_provider: BTreeMap<String, String>,
    pub mapper_registrations: Vec<BTreeMap<String, String>>,
}

/// `MyBatisProviderFact`.
#[derive(Debug, Clone, Default)]
pub struct ProviderFact {
    pub stable_id: String,
    pub mapper_method_id: String,
    pub namespace: String,
    pub statement_id: String,
    pub provider_kind: String,
    pub source: SourceSpan,
    pub provider_type: String,
    pub provider_method: String,
    pub raw_arguments: String,
    pub resolution_status: String,
    pub attributes: BTreeMap<String, String>,
}

/// `MyBatisSpringBridgeFact`.
#[derive(Debug, Clone, Default)]
pub struct SpringBridgeFact {
    pub stable_id: String,
    pub bridge_kind: String,
    pub source: SourceSpan,
    pub name: String,
    pub target: String,
    pub attributes: BTreeMap<String, String>,
    pub resolution_status: String,
}

/// `MyBatisExtensionFact`.
#[derive(Debug, Clone, Default)]
pub struct ExtensionFact {
    pub stable_id: String,
    pub extension_kind: String,
    pub source: SourceSpan,
    pub name: String,
    pub java_type: String,
    pub attributes: BTreeMap<String, String>,
    pub resolution_status: String,
}

/// `MyBatisCacheFact`.
#[derive(Debug, Clone, Default)]
pub struct CacheFact {
    pub stable_id: String,
    pub namespace: String,
    pub cache_kind: String,
    pub source: SourceSpan,
    pub target_namespace: String,
    pub attributes: BTreeMap<String, String>,
    pub resolution_status: String,
}

/// `MyBatisSqlStatementSemanticFact`.
#[derive(Debug, Clone, Default)]
pub struct SqlStatementSemanticFact {
    pub stable_id: String,
    pub owner_statement_id: String,
    pub source: SourceSpan,
    pub crud: String,
    pub xml_statement_kind: String,
    pub database_id: String,
    pub raw_sql: String,
    pub normalized_sql: String,
    pub parser_status: String,
    pub parser_error_count: i64,
    pub has_textual_substitution: bool,
    pub confidence: f64,
}

/// `MyBatisSqlTableFact`.
#[derive(Debug, Clone, Default)]
pub struct SqlTableFact {
    pub stable_id: String,
    pub sql_statement_id: String,
    pub raw_name: String,
    pub normalized_name: String,
    pub role: String,
    pub source: SourceSpan,
    pub alias: String,
    pub catalog: String,
    pub schema: String,
    pub is_cte: bool,
    pub is_dynamic: bool,
    pub dynamic_node_ids: Vec<String>,
    pub branch_roles: Vec<String>,
    pub resolution_status: String,
}

/// `MyBatisSqlColumnFact`.
#[derive(Debug, Clone, Default)]
pub struct SqlColumnFact {
    pub stable_id: String,
    pub sql_statement_id: String,
    pub raw_name: String,
    pub normalized_name: String,
    pub role: String,
    pub source: SourceSpan,
    pub qualifier: String,
    pub table_ref: String,
    pub expression: String,
    pub dynamic_node_ids: Vec<String>,
    pub branch_roles: Vec<String>,
    pub resolution_status: String,
}

/// `MyBatisSqlJoinFact`.
#[derive(Debug, Clone, Default)]
pub struct SqlJoinFact {
    pub stable_id: String,
    pub sql_statement_id: String,
    pub source: SourceSpan,
    pub join_type: String,
    pub right_table: String,
    pub right_alias: String,
    pub condition: String,
    pub dynamic_node_ids: Vec<String>,
    pub branch_roles: Vec<String>,
    pub resolution_status: String,
}

/// `MyBatisSqlParameterFact`.
#[derive(Debug, Clone, Default)]
pub struct SqlParameterFact {
    pub stable_id: String,
    pub sql_statement_id: String,
    pub token: String,
    pub parameter_kind: String,
    pub source: SourceSpan,
    pub name: String,
    pub options: BTreeMap<String, String>,
    pub position: i64,
    pub dynamic_node_ids: Vec<String>,
    pub branch_roles: Vec<String>,
}

/// `MyBatisFact` — graph node row builder.
#[derive(Debug, Clone)]
pub struct MyBatisFact {
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
    pub properties: Vec<(String, Value)>,
}

impl Default for MyBatisFact {
    fn default() -> Self {
        Self {
            kind: String::new(),
            stable_id: String::new(),
            name: String::new(),
            source: SourceSpan::default(),
            project_id: String::new(),
            project_name: String::new(),
            language: "mybatis".to_string(),
            confidence: 1.0,
            extraction_method: "mybatis_foundation".to_string(),
            resolution_status: "resolved".to_string(),
            raw_value: String::new(),
            resolved_value: String::new(),
            source_symbol_id: String::new(),
            properties: Vec::new(),
        }
    }
}

impl MyBatisFact {
    /// `MyBatisFact.to_graph_node()`.
    pub fn to_graph_node(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("id".into(), json!(self.stable_id));
        row.insert("symbol_id".into(), json!(self.stable_id));
        row.insert("name".into(), json!(self.name));
        row.insert("kind".into(), json!(self.kind));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("project_name".into(), json!(self.project_name));
        row.insert("language".into(), json!(self.language));
        row.insert("framework".into(), json!("mybatis"));
        row.insert("file_path".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("extraction_method".into(), json!(self.extraction_method));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("raw_value".into(), json!(self.raw_value));
        row.insert("resolved_value".into(), json!(self.resolved_value));
        row.insert("source_symbol_id".into(), json!(self.source_symbol_id));
        row.insert(
            "parser_version".into(),
            json!(MYBATIS_PARSER_VERSION),
        );
        for (key, value) in &self.properties {
            row.insert(key.clone(), graph_property_value(value));
        }
        row
    }
}

// `MyBatisDependencyIndex` — plane artifact JSON, không port (graph-plane
// không phụ thuộc).

/// `MyBatisRelationship` — graph relationship row builder.
#[derive(Debug, Clone)]
pub struct MyBatisRelationship {
    pub from_label: String,
    pub from_id: String,
    pub to_label: String,
    pub to_id: String,
    pub rel_type: String,
    pub project_id: String,
    pub source: SourceSpan,
    pub confidence: f64,
    pub resolution_status: String,
    pub reason: String,
    pub properties: Vec<(String, Value)>,
}

impl Default for MyBatisRelationship {
    fn default() -> Self {
        Self {
            from_label: String::new(),
            from_id: String::new(),
            to_label: String::new(),
            to_id: String::new(),
            rel_type: String::new(),
            project_id: String::new(),
            source: SourceSpan::default(),
            confidence: 1.0,
            resolution_status: "resolved".to_string(),
            reason: String::new(),
            properties: Vec::new(),
        }
    }
}

impl MyBatisRelationship {
    /// `MyBatisRelationship.to_graph_relationship()`.
    pub fn to_graph_relationship(&self) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("from_label".into(), json!(self.from_label));
        row.insert("from_id".into(), json!(self.from_id));
        row.insert("to_label".into(), json!(self.to_label));
        row.insert("to_id".into(), json!(self.to_id));
        row.insert("type".into(), json!(self.rel_type));
        row.insert("project_id".into(), json!(self.project_id));
        row.insert("framework".into(), json!("mybatis"));
        row.insert("file_path".into(), json!(self.source.file_path));
        row.insert("start_line".into(), json!(self.source.start_line));
        row.insert("end_line".into(), json!(self.source.end_line));
        row.insert("confidence".into(), json!(self.confidence));
        row.insert("resolution_status".into(), json!(self.resolution_status));
        row.insert("reason".into(), json!(self.reason));
        for (key, value) in &self.properties {
            row.insert(key.clone(), graph_property_value(value));
        }
        row
    }
}

/// Helper: string list → JSON array (graph_property_value giữ list scalar).
pub fn string_list(items: &[String]) -> Value {
    Value::Array(items.iter().map(|item| json!(item)).collect())
}

pub fn string_map(map: &BTreeMap<String, String>) -> Value {
    let mut obj = Map::new();
    for (key, value) in map {
        obj.insert(key.clone(), json!(value));
    }
    Value::Object(obj)
}

pub fn string_map_list(maps: &[BTreeMap<String, String>]) -> Value {
    Value::Array(maps.iter().map(string_map).collect())
}

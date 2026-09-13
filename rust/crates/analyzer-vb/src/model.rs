//! Dataclass payload — mirror `vb_common.py` dataclasses (FunctionDef ...
//! VariableDef, CallEdge, RelationEdge). Dùng cho CẢ regex path lẫn Roslyn
//! payload hydration (worker C# emit đúng shape này qua JSON).

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    #[serde(default)]
    pub class_name: Option<String>,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub arity: i64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NamespaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileDef {
    pub file_path: String,
    #[serde(default = "default_one")]
    pub start_line: i64,
    #[serde(default)]
    pub end_line: i64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub imports: Option<Vec<String>>,
    #[serde(default)]
    pub exports: Option<Vec<String>>,
}

fn default_one() -> i64 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PropertyDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    #[serde(default)]
    pub class_name: Option<String>,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub parameters: String,
    #[serde(default)]
    pub return_type: String,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    #[serde(default)]
    pub class_name: Option<String>,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub parameters: String,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InterfaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub base_interfaces: Vec<String>,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnumDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    #[serde(default)]
    pub namespace_name: Option<String>,
    #[serde(default)]
    pub class_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    #[serde(default)]
    pub members: Vec<(String, String)>,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConstantDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    #[serde(default)]
    pub value: String,
    #[serde(default)]
    pub type_name: String,
    #[serde(default)]
    pub class_name: Option<String>,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    #[serde(default)]
    pub line_number: i64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VariableDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    #[serde(default)]
    pub type_name: String,
    #[serde(default)]
    pub is_global: bool,
    #[serde(default)]
    pub is_shared: bool,
    #[serde(default)]
    pub class_name: Option<String>,
    #[serde(default)]
    pub namespace_name: Option<String>,
    pub file_path: String,
    #[serde(default)]
    pub line_number: i64,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub comment: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CallEdge {
    pub caller_id: String,
    #[serde(default)]
    pub caller_scope: Option<String>,
    pub callee_name: String,
    #[serde(default)]
    pub callee_id: Option<String>,
    #[serde(default)]
    pub callee_arity: Option<i64>,
    #[serde(default)]
    pub call_line: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RelationEdge {
    pub source_id: String,
    #[serde(default)]
    pub source_label: String,
    pub target_id: String,
    #[serde(default)]
    pub target_label: String,
    pub rel_type: String,
    #[serde(default)]
    pub properties: std::collections::BTreeMap<String, serde_json::Value>,
}

/// Payload đầy đủ của một file — mirror dict trả bởi `parse_vb_file` /
/// Roslyn worker (`functions`, `calls`, ..., `file_def`, `parse_meta`).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct FilePayload {
    #[serde(default)]
    pub functions: Vec<FunctionDef>,
    #[serde(default)]
    pub calls: Vec<CallEdge>,
    #[serde(default)]
    pub classes: Vec<ClassDef>,
    #[serde(default)]
    pub namespaces: Vec<NamespaceDef>,
    #[serde(default)]
    pub relations: Vec<RelationEdge>,
    #[serde(default)]
    pub properties: Vec<PropertyDef>,
    #[serde(default)]
    pub events: Vec<EventDef>,
    #[serde(default)]
    pub interfaces: Vec<InterfaceDef>,
    #[serde(default)]
    pub enums: Vec<EnumDef>,
    #[serde(default)]
    pub constants: Vec<ConstantDef>,
    #[serde(default)]
    pub variables: Vec<VariableDef>,
    #[serde(default)]
    pub file_def: Option<FileDef>,
    #[serde(default)]
    pub parse_meta: serde_json::Value,
}

/// `_is_valid_payload_shape` — payload Roslyn phải có đủ 5 key bắt buộc.
pub fn valid_payload_shape(value: &serde_json::Value) -> bool {
    if !value.is_object() {
        return false;
    }
    ["functions", "calls", "classes", "file_def", "parse_meta"]
        .iter()
        .all(|key| value.get(key).is_some())
}

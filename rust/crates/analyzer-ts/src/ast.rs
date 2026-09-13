//! Port `tools/ts/types/` — AST + graph dataclasses (asdict shapes).

use std::collections::BTreeMap;

use serde::Serialize;

#[derive(Debug, Clone, Default, Serialize)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub arity: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub exported: bool,
    pub return_type: String,
    pub param_types: Vec<String>,
    pub intent: String,
    pub inferred_doc: bool,
    pub doc_confidence: f64,
    #[serde(skip)]
    #[allow(dead_code)]
    pub signals: BTreeMap<String, f64>,
    pub side_effect: bool,
    pub react_role: String,
    pub middleware_kind: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ApiCallDef {
    pub symbol_id: String,
    pub caller_function_id: String,
    pub url_pattern: String,
    pub raw_url: String,
    pub http_method: String,
    pub base_url_ref: String,
    pub file_path: String,
    pub start_line: i64,
    pub confidence: f64,
}

#[derive(Debug, Clone)]
pub struct RenderEdge {
    pub renderer_id: String,
    pub rendered_name: String,
}

#[derive(Debug, Clone)]
pub struct NavigateEdge {
    pub source_id: String,
    pub target_name: String,
    pub nav_method: String,
    pub via: String,
    pub trigger_type: String,
    pub guard: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NavigatorDef {
    pub symbol_id: String,
    pub var_name: String,
    pub factory: String,
    pub nav_type: String,
    pub param_list_ref: String,
    pub file_path: String,
    pub start_line: i64,
    pub routes: Vec<(String, String)>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ParamListDef {
    pub symbol_id: String,
    pub name: String,
    pub file_path: String,
    pub routes: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub imports: Vec<String>,
    pub exports: Vec<String>,
    pub jsx_tags: Vec<String>,
    pub jsx_components: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NamespaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct TypeDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub exported: bool,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct RelationEdge {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
    pub properties: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub callee_name: String,
    pub callee_id: Option<String>,
    pub callee_arity: Option<i64>,
}

#[derive(Debug, Clone, Default)]
pub struct ParseMeta {
    pub has_error: bool,
    pub error_nodes: i64,
}

#[derive(Debug, Clone, Default)]
pub struct TsFilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub renders: Vec<RenderEdge>,
    pub navigates: Vec<NavigateEdge>,
    pub file_def: Option<FileDef>,
    pub parse_meta: ParseMeta,
    pub api_calls: Vec<ApiCallDef>,
    pub navigators: Vec<NavigatorDef>,
    pub param_lists: Vec<ParamListDef>,
}

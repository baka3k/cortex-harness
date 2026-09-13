//! Port phần parse của `tools/rust/rust_analyzer.py`: tree-sitter walk
//! (`_walk_tree`), FunctionDef/TypeDef/NamespaceDef/FieldDef/AliasDef/
//! TemplateDef/RelationEdge/CallEdge, `_resolve_calls`, `_extract_name`,
//! `_impl_owner_name`, `_add_type_use`, `_control_context`, symbol-id
//! formats, `_build_note`, error stats.
//!
//! Grammar: `tree-sitter-rust` 0.24 (PyPI venv tham chiếu dùng 0.24.2 —
//! cùng dòng grammar nên node kinds khớp từng chữ).

use std::collections::HashSet;
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;
use serde::Serialize;
use serde_json::{json, Map, Value};

use cortex_analyzer_framework::ts::{decode_ignore, find_nodes_by_type, line_from_byte, node_text};

pub type Row = Map<String, Value>;

pub const RUST_SOURCE_EXTENSIONS: [&str; 1] = [".rs"];

/// `_COMMENT_TYPES`.
const COMMENT_TYPES: [&str; 2] = ["line_comment", "block_comment"];

/// `_TYPE_NODES` — node kind → kind label.
const TYPE_NODES: [(&str, &str); 4] = [
    ("struct_item", "struct"),
    ("enum_item", "enum"),
    ("union_item", "union"),
    ("trait_item", "interface"),
];
/// `_FUNCTION_NODES`.
const FUNCTION_NODES: [&str; 2] = ["function_item", "function_signature_item"];
/// `_MODULE_NODES`.
const MODULE_NODES: [&str; 1] = ["mod_item"];
/// `_IMPL_NODES`.
const IMPL_NODES: [&str; 1] = ["impl_item"];
/// `_ALIAS_NODES`.
const ALIAS_NODES: [&str; 1] = ["type_item"];
/// `_CALL_NODES`.
const CALL_NODES: [&str; 3] = ["call_expression", "method_call_expression", "macro_invocation"];
// `_BRANCH_NODES`/`_LOOP_NODES` chỉ dùng cho `_control_context` (không port —
// xem doc-comment `call_name`).

fn is_comment_kind(kind: &str) -> bool {
    COMMENT_TYPES.contains(&kind)
}

fn type_kind_for(kind: &str) -> Option<&'static str> {
    TYPE_NODES
        .iter()
        .find(|(node_kind, _)| *node_kind == kind)
        .map(|(_, label)| *label)
}

// ── Data defs (asdict shape khớp Python payload JSON) ───────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub includes: Vec<String>,
    pub using_namespaces: Vec<String>,
    pub using_imports: Vec<String>,
    pub macros: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_byte: i64,
    pub end_byte: i64,
    pub start_line: i64,
    pub end_line: i64,
    pub arity: i64,
    pub code: String,
    pub comment: String,
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
}

#[derive(Debug, Clone, Serialize)]
pub struct FieldDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub scope_name: Option<String>,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct AliasDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub target_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct TemplateDef {
    pub symbol_id: String,
    pub name: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct RelationEdge {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
    pub properties: Value,
}

/// CallEdge — chỉ giữ trường writer/resolve đọc (caller_file, start_byte,
/// branch/loop control frames của Python không bao giờ vào graph).
#[derive(Debug, Clone, Serialize)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub call_line: i64,
    pub call_column: i64,
    pub call_type: String,
    pub call_arity: i64,
    pub callee_name: String,
    pub callee_id: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct ParseMeta {
    pub has_error: bool,
    pub error_nodes: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct FilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<AliasDef>,
    pub templates: Vec<TemplateDef>,
    pub file_def: FileDef,
    pub parse_meta: ParseMeta,
}

// ── Helpers (port từng hàm `_xxx` của Python) ───────────────────────────────

/// `_node_snippet` — text tính từ sau chuỗi leading comments (kinds theo
/// `_COMMENT_TYPES`), kèm start/end line.
fn node_snippet(node: tree_sitter::Node, source: &[u8]) -> (String, i64, i64) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if is_comment_kind(p.kind()) {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = decode_ignore(&source[start_byte..node.end_byte()]);
    let start_line = line_from_byte(source, start_byte);
    let end_line = node.end_position().row + 1;
    (snippet, start_line as i64, end_line as i64)
}

/// `_extract_comment` — chuỗi comment liền trước node (reversed join).
fn extract_comment(node: tree_sitter::Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if is_comment_kind(p.kind()) {
            let text = node_text(p, source);
            let text = text.trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    parts.reverse();
    parts.join("\n")
}

/// `_extract_file_comment` — các comment đầu file cho tới named child đầu.
fn extract_file_comment(root: tree_sitter::Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if is_comment_kind(child.kind()) {
            let text = node_text(child, source);
            let text = text.trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
            continue;
        }
        if child.is_named() {
            break;
        }
    }
    if parts.is_empty() {
        return String::new();
    }
    parts.join("\n")
}

/// `_tree_error_stats` — (root.has_error, số node "ERROR").
fn tree_error_stats(root: tree_sitter::Node) -> (bool, i64) {
    let error_nodes = find_nodes_by_type(root, "ERROR").len() as i64;
    (root.has_error(), error_nodes)
}

/// `_first_named_child` — phiên bản rust_analyzer KHÔNG lọc is_named
/// (khác swift_analyzer): chỉ so `child.type in wanted`.
fn first_named_child<'a>(
    node: tree_sitter::Node<'a>,
    kinds: &[&str],
) -> Option<tree_sitter::Node<'a>> {
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .find(|child| kinds.contains(&child.kind()))
}

/// `_first_identifier` — DFS tìm node kiểu identifier-like đầu tiên.
fn first_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "identifier" | "type_identifier" | "field_identifier" | "scoped_identifier"
    ) {
        return Some(node_text(node, source).trim().to_string());
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if let Some(result) = first_identifier(child, source)
            && !result.is_empty()
        {
            return Some(result);
        }
    }
    None
}

/// `_extract_name` — field "name" trước, fallback `_first_identifier`.
fn extract_name(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        let text = node_text(name_node, source).trim().to_string();
        return if text.is_empty() { None } else { Some(text) };
    }
    first_identifier(node, source).filter(|text| !text.is_empty())
}

fn collapse_ws(text: &str) -> String {
    static WS_RE: OnceLock<Regex> = OnceLock::new();
    let ws_re = WS_RE.get_or_init(|| Regex::new(r"\s+").expect("ws regex"));
    ws_re.replace_all(text, " ").to_string()
}

/// `_scope_name`.
fn scope_name(scope_stack: &[String]) -> Option<String> {
    if scope_stack.is_empty() {
        None
    } else {
        Some(scope_stack.join("::"))
    }
}

/// `_qualified_name`.
fn qualified_name(scope_stack: &[String], name: &str) -> String {
    if scope_stack.is_empty() {
        name.to_string()
    } else {
        format!("{}::{name}", scope_stack.join("::"))
    }
}

/// `_symbol_id`.
fn symbol_id(qualified_name: &str, arity: i64, rel_path: &str) -> String {
    format!("{qualified_name}/{arity}@{rel_path}")
}

/// `_type_id` — identity.
fn type_id(qualified_name: &str) -> String {
    qualified_name.to_string()
}

/// `_namespace_id`.
fn namespace_id(qualified_name: &str) -> String {
    format!("namespace::{qualified_name}")
}

/// `_anonymous_name`.
fn anonymous_name(prefix: &str, node: tree_sitter::Node) -> String {
    format!(
        "Anonymous{prefix}@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

/// `_count_parameters`.
fn count_parameters(node: tree_sitter::Node) -> i64 {
    let params = match node.child_by_field_name("parameters") {
        Some(params) => Some(params),
        None => first_named_child(node, &["parameters", "parameter_list"]),
    };
    let Some(params) = params else {
        return 0;
    };
    let mut cursor = params.walk();
    params
        .children(&mut cursor)
        .filter(|child| child.is_named() && !is_comment_kind(child.kind()))
        .count() as i64
}

/// `_count_arguments`.
fn count_arguments(node: tree_sitter::Node) -> i64 {
    let args = match node.child_by_field_name("arguments") {
        Some(args) => Some(args),
        None => first_named_child(node, &["arguments", "argument_list"]),
    };
    let Some(args) = args else {
        return 0;
    };
    let mut cursor = args.walk();
    args.children(&mut cursor)
        .filter(|child| child.is_named() && !is_comment_kind(child.kind()))
        .count() as i64
}

/// `_extract_type_signature`.
fn extract_type_signature(node: tree_sitter::Node, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    let text = node_text(node, source).trim().to_string();
    match text.split_once(':') {
        Some((_, rest)) => rest.trim().trim_end_matches(',').to_string(),
        None => String::new(),
    }
}

/// `_extract_alias_target`.
fn extract_alias_target(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(type_node) = node.child_by_field_name("type") {
        return Some(node_text(type_node, source).trim().to_string());
    }
    let text = node_text(node, source);
    static ALIAS_RE: OnceLock<Regex> = OnceLock::new();
    let alias_re =
        ALIAS_RE.get_or_init(|| Regex::new(r"(?s)=\s*(.*?)\s*;").expect("alias target regex"));
    alias_re.captures(&text).map(|caps| {
        let group = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        collapse_ws(group).trim().to_string()
    })
}

/// `_extract_use_path`.
fn extract_use_path(node: tree_sitter::Node, source: &[u8]) -> String {
    static PUB_RE: OnceLock<Regex> = OnceLock::new();
    static USE_RE: OnceLock<Regex> = OnceLock::new();
    let pub_re = PUB_RE.get_or_init(|| Regex::new(r"^pub\s+").expect("pub regex"));
    let use_re = USE_RE.get_or_init(|| Regex::new(r"^use\s+").expect("use regex"));
    let mut text = node_text(node, source).trim().to_string();
    text = pub_re.replace(&text, "").to_string();
    text = use_re.replace(&text, "").to_string();
    text.trim_end_matches(';').trim().to_string()
}

/// `_collect_imports` — (using_namespaces, using_imports).
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut using_imports: Vec<String> = Vec::new();
    let mut using_namespaces: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "use_declaration") {
        let path = extract_use_path(node, source);
        if !path.is_empty() {
            using_imports.push(path.clone());
            if path.ends_with("::*") {
                using_namespaces.push(path[..path.len() - 3].to_string());
            }
        }
    }
    (using_namespaces, using_imports)
}

/// `_collect_macros`.
fn collect_macros(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut macros: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "macro_invocation") {
        if let Some(macro_name) = extract_name(node, source)
            && !macros.contains(&macro_name)
        {
            macros.push(macro_name);
        }
    }
    macros
}

/// `_collect_includes`.
fn collect_includes(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut includes: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "extern_crate_declaration") {
        if let Some(name) = extract_name(node, source) {
            includes.push(name);
        }
    }
    includes
}

/// `_call_name`.
///
/// `_control_context` (branch/loop frames) của Python KHÔNG port: giá trị chỉ
/// nằm trong call-row dicts và writer không bao giờ đọc — không thể vào graph.
fn call_name(call_node: tree_sitter::Node, source: &[u8]) -> String {
    if call_node.kind() == "method_call_expression"
        && let Some(name_node) = call_node.child_by_field_name("name")
    {
        return node_text(name_node, source).trim().to_string();
    }
    if call_node.kind() == "macro_invocation" {
        return extract_name(call_node, source)
            .unwrap_or_else(|| anonymous_name("Macro", call_node));
    }
    if let Some(function_node) = call_node.child_by_field_name("function") {
        let text = node_text(function_node, source).trim().to_string();
        return text
            .rsplit("::")
            .next()
            .unwrap_or("")
            .rsplit('.')
            .next()
            .unwrap_or("")
            .to_string();
    }
    let text = node_text(call_node, source).trim().to_string();
    text.split('(')
        .next()
        .unwrap_or("")
        .trim()
        .rsplit("::")
        .next()
        .unwrap_or("")
        .to_string()
}

/// `_record_relation`.
fn record_relation(
    relations: &mut Vec<RelationEdge>,
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    properties: Value,
) {
    relations.push(RelationEdge {
        source_id: source_id.to_string(),
        source_label: source_label.to_string(),
        target_id: target_id.to_string(),
        target_label: target_label.to_string(),
        rel_type: rel_type.to_string(),
        properties,
    });
}

/// `_add_type_use`.
fn add_type_use(
    owner_id: &str,
    owner_label: &str,
    type_text: &str,
    rel_path: &str,
    walker: &mut Walker,
) {
    static CHARS_RE: OnceLock<Regex> = OnceLock::new();
    static KEYWORDS_RE: OnceLock<Regex> = OnceLock::new();
    static SPLIT_RE: OnceLock<Regex> = OnceLock::new();
    let chars_re =
        CHARS_RE.get_or_init(|| Regex::new(r"[<&*\\\[\\](),;]").expect("type-use char regex"));
    let keywords_re = KEYWORDS_RE.get_or_init(|| {
        Regex::new(r"\b(mut|ref|pub|crate|self|Self|where|dyn|impl)\b").expect("keyword regex")
    });
    let split_re = SPLIT_RE.get_or_init(|| Regex::new(r"\s+|::").expect("split regex"));

    let type_name = chars_re.replace_all(type_text, " ").to_string();
    let type_name = keywords_re.replace_all(&type_name, " ").to_string();
    let candidates: Vec<&str> = split_re
        .split(&type_name)
        .filter(|part| {
            !part.is_empty()
                && part
                    .chars()
                    .next()
                    .map(|c| c.is_uppercase())
                    .unwrap_or(false)
        })
        .collect();
    for candidate in candidates {
        let target_id = type_id(candidate);
        if !walker.external_types.contains(&target_id) {
            walker.external_types.insert(target_id.clone());
            walker.types.push(TypeDef {
                symbol_id: target_id.clone(),
                qualified_name: candidate.to_string(),
                name: candidate.to_string(),
                kind: "external".to_string(),
                file_path: rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: candidate.to_string(),
                comment: String::new(),
            });
        }
        let rel_type = if type_text.contains('&')
            || type_text.contains("*const")
            || type_text.contains("*mut")
        {
            "POINTER_TO"
        } else {
            "USES_TYPE"
        };
        record_relation(
            &mut walker.relations,
            owner_id,
            owner_label,
            &target_id,
            "Type",
            rel_type,
            json!({}),
        );
    }
}

/// Active function context truyền xuống `_walk_tree`.
#[derive(Debug, Clone)]
struct ActiveFn {
    symbol_id: String,
    scope_name: Option<String>,
}

/// Registries + output lists của một file parse (tương đương closure state
/// của `_walk_tree`).
struct Walker {
    source: Vec<u8>,
    rel_path: String,
    namespaces: Vec<NamespaceDef>,
    types: Vec<TypeDef>,
    functions: Vec<FunctionDef>,
    fields: Vec<FieldDef>,
    aliases: Vec<AliasDef>,
    templates: Vec<TemplateDef>,
    relations: Vec<RelationEdge>,
    calls: Vec<CallEdge>,
    namespace_registry: HashSet<String>,
    type_registry: std::collections::HashMap<String, TypeDef>,
    external_types: HashSet<String>,
}

impl Walker {
    /// `_register_type`.
    fn register_type(&mut self, type_def: TypeDef) {
        match self.type_registry.get(&type_def.symbol_id) {
            None => {
                self.type_registry
                    .insert(type_def.symbol_id.clone(), type_def.clone());
                self.types.push(type_def);
            }
            Some(existing) => {
                if existing.kind != "external" || type_def.kind == "external" {
                    return;
                }
                self.type_registry
                    .insert(type_def.symbol_id.clone(), type_def.clone());
                if let Some(index) = self
                    .types
                    .iter()
                    .position(|item| item.symbol_id == type_def.symbol_id)
                {
                    self.types[index] = type_def;
                }
            }
        }
    }

    /// `_ensure_external_type`.
    fn ensure_external_type(&mut self, type_name: &str, node: tree_sitter::Node) {
        if self.type_registry.contains_key(type_name) {
            return;
        }
        self.register_type(TypeDef {
            symbol_id: type_name.to_string(),
            qualified_name: type_name.to_string(),
            name: type_name.rsplit("::").next().unwrap_or("").to_string(),
            kind: "external".to_string(),
            file_path: self.rel_path.clone(),
            start_line: node.start_position().row as i64 + 1,
            end_line: node.end_position().row as i64 + 1,
            code: type_name.to_string(),
            comment: String::new(),
        });
    }

    /// `_scope_owner_endpoint` — (owner_id, owner_label).
    fn scope_owner_endpoint(&self, scope_stack: &[String]) -> Option<(String, &'static str)> {
        let owner_scope = scope_name(scope_stack)?;
        if self.type_registry.contains_key(&owner_scope) {
            return Some((owner_scope, "Type"));
        }
        let namespace = namespace_id(&owner_scope);
        if self.namespace_registry.contains(&namespace) {
            return Some((namespace, "Namespace"));
        }
        None
    }

    /// `_impl_owner_name`.
    fn impl_owner_name(
        &self,
        node: tree_sitter::Node,
        source: &[u8],
        scope_stack: &[String],
    ) -> Option<String> {
        static IDENT_RE: OnceLock<Regex> = OnceLock::new();
        let ident_re = IDENT_RE
            .get_or_init(|| Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*$").expect("ident regex"));
        let mut type_node = node.child_by_field_name("type")?;
        while matches!(
            type_node.kind(),
            "generic_type" | "reference_type" | "pointer_type"
        ) {
            match type_node.child_by_field_name("type") {
                Some(inner) if inner != type_node => type_node = inner,
                _ => break,
            }
        }
        let owner_name = collapse_ws(&node_text(type_node, source)).trim().to_string();
        if owner_name.is_empty() {
            return None;
        }
        let owner_scope = scope_name(scope_stack);
        let qualified_name = match &owner_scope {
            Some(scope) => format!("{scope}::{owner_name}"),
            None => owner_name.clone(),
        };
        if self.type_registry.contains_key(&qualified_name) {
            return Some(qualified_name);
        }
        if self.type_registry.contains_key(&owner_name) {
            return Some(owner_name);
        }
        if owner_scope.is_some()
            && !owner_name.contains("::")
            && ident_re.is_match(&owner_name)
        {
            return Some(qualified_name);
        }
        Some(owner_name)
    }

    /// `_walk_tree`.
    fn walk(&mut self, node: tree_sitter::Node, scope_stack: &mut Vec<String>, active_function: Option<&ActiveFn>) {
        let kind = node.kind();

        // ── mod_item → NamespaceDef ─────────────────────────────────────────
        if MODULE_NODES.contains(&kind) {
            let name = extract_name(node, &self.source)
                .unwrap_or_else(|| anonymous_name("Module", node));
            let qualified = qualified_name(scope_stack, &name);
            let ns_id = namespace_id(&qualified);
            let (snippet, start_line, end_line) = node_snippet(node, &self.source);
            let namespace = NamespaceDef {
                symbol_id: ns_id.clone(),
                qualified_name: qualified,
                name: name.clone(),
                file_path: self.rel_path.clone(),
                start_line,
                end_line,
                code: snippet,
                comment: extract_comment(node, &self.source),
            };
            if !self.namespace_registry.contains(&ns_id) {
                self.namespace_registry.insert(ns_id.clone());
                self.namespaces.push(namespace);
            }
            if !scope_stack.is_empty() {
                let parent_scope = scope_name(scope_stack).unwrap_or_default();
                record_relation(
                    &mut self.relations,
                    &namespace_id(&parent_scope),
                    "Namespace",
                    &ns_id,
                    "Namespace",
                    "CONTAINS",
                    json!({}),
                );
            }
            scope_stack.push(name.clone());
            let mut cursor = node.walk();
            let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
            for child in children {
                self.walk(child, scope_stack, active_function);
            }
            scope_stack.pop();
            return;
        }

        // ── struct/enum/union/trait → TypeDef ───────────────────────────────
        if let Some(type_kind) = type_kind_for(kind) {
            let name = extract_name(node, &self.source)
                .unwrap_or_else(|| anonymous_name("Type", node));
            let qualified = qualified_name(scope_stack, &name);
            let type_id = type_id(&qualified);
            let (snippet, start_line, end_line) = node_snippet(node, &self.source);
            let type_def = TypeDef {
                symbol_id: type_id.clone(),
                qualified_name: qualified,
                name: name.clone(),
                kind: type_kind.to_string(),
                file_path: self.rel_path.clone(),
                start_line,
                end_line,
                code: snippet,
                comment: extract_comment(node, &self.source),
            };
            self.register_type(type_def);
            if let Some((owner_id, owner_label)) = self.scope_owner_endpoint(scope_stack) {
                record_relation(
                    &mut self.relations,
                    &owner_id,
                    owner_label,
                    &type_id,
                    "Type",
                    "DECLARES",
                    json!({}),
                );
            }
            if kind == "trait_item" {
                let rel_path = self.rel_path.clone();
                let bounds: Vec<String> = find_nodes_by_type(node, "trait_bounds")
                    .iter()
                    .map(|bound| node_text(*bound, &self.source))
                    .collect();
                for bound in bounds {
                    add_type_use(&type_id, "Type", &bound, &rel_path, self);
                }
            }
            let rel_path = self.rel_path.clone();
            for template_node in find_nodes_by_type(node, "type_parameters") {
                let template_id = format!(
                    "template::{rel_path}:{}:{}",
                    template_node.start_position().row + 1,
                    template_node.end_position().row + 1
                );
                let text = node_text(template_node, &self.source).trim().to_string();
                self.templates.push(TemplateDef {
                    symbol_id: template_id.clone(),
                    name: text.clone(),
                    file_path: rel_path.clone(),
                    start_line: template_node.start_position().row as i64 + 1,
                    end_line: template_node.end_position().row as i64 + 1,
                    code: text,
                });
                record_relation(
                    &mut self.relations,
                    &template_id,
                    "Template",
                    &type_id,
                    "Type",
                    "TEMPLATES",
                    json!({}),
                );
            }
            scope_stack.push(name.clone());
            let mut cursor = node.walk();
            let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
            for child in children {
                self.walk(child, scope_stack, active_function);
            }
            scope_stack.pop();
            return;
        }

        // ── impl_item → children kế thừa scope của owner type ───────────────
        if IMPL_NODES.contains(&kind) {
            let impl_name = self.impl_owner_name(node, &self.source, scope_stack);
            if let Some(impl_name) = &impl_name {
                self.ensure_external_type(impl_name, node);
            }
            let child_scope: Vec<String> = match &impl_name {
                Some(impl_name) => impl_name.split("::").map(str::to_string).collect(),
                None => scope_stack.clone(),
            };
            let mut cursor = node.walk();
            let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
            for child in children {
                let mut child_scope = child_scope.clone();
                self.walk(child, &mut child_scope, active_function);
            }
            return;
        }

        // ── function_item / function_signature_item → FunctionDef ───────────
        if FUNCTION_NODES.contains(&kind) {
            let name = extract_name(node, &self.source)
                .unwrap_or_else(|| anonymous_name("Function", node));
            let arity = count_parameters(node);
            let qualified = qualified_name(scope_stack, &name);
            let func = FunctionDef {
                symbol_id: symbol_id(&qualified, arity, &self.rel_path),
                qualified_name: qualified,
                name,
                kind: if kind == "function_signature_item" {
                    "declaration".to_string()
                } else {
                    "function".to_string()
                },
                scope_name: scope_name(scope_stack),
                file_path: self.rel_path.clone(),
                start_byte: node.start_byte() as i64,
                end_byte: node.end_byte() as i64,
                start_line: node.start_position().row as i64 + 1,
                end_line: node.end_position().row as i64 + 1,
                arity,
                code: node_text(node, &self.source),
                comment: extract_comment(node, &self.source),
            };
            if let Some((owner_id, owner_label)) = self.scope_owner_endpoint(scope_stack) {
                record_relation(
                    &mut self.relations,
                    &owner_id,
                    owner_label,
                    &func.symbol_id,
                    "Function",
                    "DECLARES",
                    json!({}),
                );
            }
            let active = ActiveFn {
                symbol_id: func.symbol_id.clone(),
                scope_name: func.scope_name.clone(),
            };
            self.functions.push(func);
            let mut cursor = node.walk();
            let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
            for child in children {
                self.walk(child, scope_stack, Some(&active));
            }
            return;
        }

        // ── type_item → AliasDef (KHÔNG return — rơi xuống call/field) ──────
        if ALIAS_NODES.contains(&kind) {
            let name = extract_name(node, &self.source)
                .unwrap_or_else(|| anonymous_name("Alias", node));
            let qualified = qualified_name(scope_stack, &name);
            let target = extract_alias_target(node, &self.source);
            let alias_symbol_id = format!("alias::{qualified}@{}", self.rel_path);
            self.aliases.push(AliasDef {
                symbol_id: alias_symbol_id.clone(),
                qualified_name: qualified,
                name,
                kind: "type".to_string(),
                target_name: target.clone(),
                file_path: self.rel_path.clone(),
                start_line: node.start_position().row as i64 + 1,
                end_line: node.end_position().row as i64 + 1,
                code: node_text(node, &self.source),
            });
            if let Some(target) = &target {
                let target_id = type_id(target);
                self.ensure_external_type(&target_id, node);
                record_relation(
                    &mut self.relations,
                    &alias_symbol_id,
                    "Alias",
                    &target_id,
                    "Type",
                    "ALIASES",
                    json!({}),
                );
                let rel_path = self.rel_path.clone();
                add_type_use(&alias_symbol_id, "Alias", target, &rel_path, self);
            }
        }

        // ── call nodes trong thân function ──────────────────────────────────
        if let Some(active) = active_function
            && CALL_NODES.contains(&kind)
        {
            let callee_name = call_name(node, &self.source);
            self.calls.push(CallEdge {
                caller_id: active.symbol_id.clone(),
                caller_scope: active.scope_name.clone(),
                call_line: node.start_position().row as i64 + 1,
                call_column: node.start_position().column as i64 + 1,
                call_type: if kind == "macro_invocation" {
                    "macro".to_string()
                } else if kind == "method_call_expression" {
                    "method".to_string()
                } else {
                    "function".to_string()
                },
                call_arity: count_arguments(node),
                callee_name,
                callee_id: None,
            });
        }

        // ── field_declaration trong scope type ──────────────────────────────
        if !scope_stack.is_empty()
            && kind == "field_declaration"
            && let Some(name) = extract_name(node, &self.source)
        {
            let owner = scope_name(scope_stack);
            let qualified = match &owner {
                Some(owner) => format!("{owner}::{name}"),
                None => name.clone(),
            };
            let type_signature = extract_type_signature(node, &self.source);
            let field_id = format!("field::{qualified}@{}", self.rel_path);
            self.fields.push(FieldDef {
                symbol_id: field_id.clone(),
                qualified_name: qualified,
                name,
                scope_name: owner.clone(),
                type_signature: type_signature.clone(),
                file_path: self.rel_path.clone(),
                start_line: node.start_position().row as i64 + 1,
                end_line: node.end_position().row as i64 + 1,
                code: node_text(node, &self.source),
            });
            if let Some(owner) = &owner {
                record_relation(
                    &mut self.relations,
                    &type_id(owner),
                    "Type",
                    &field_id,
                    "Field",
                    "DECLARES",
                    json!({}),
                );
            }
            if !type_signature.is_empty() {
                let rel_path = self.rel_path.clone();
                add_type_use(&field_id, "Field", &type_signature, &rel_path, self);
            }
        }

        let mut cursor = node.walk();
        let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
        for child in children {
            self.walk(child, scope_stack, active_function);
        }
    }

    /// `_resolve_calls`.
    fn resolve_calls(&mut self) {
        let mut by_name: std::collections::HashMap<String, Vec<usize>> =
            std::collections::HashMap::new();
        let mut by_name_arity: std::collections::HashMap<(String, i64), Vec<usize>> =
            std::collections::HashMap::new();
        for (index, func) in self.functions.iter().enumerate() {
            by_name.entry(func.name.clone()).or_default().push(index);
            by_name_arity
                .entry((func.name.clone(), func.arity))
                .or_default()
                .push(index);
        }

        let mut resolved: Vec<(usize, String)> = Vec::new();
        for (call_index, call) in self.calls.iter().enumerate() {
            let candidates: Vec<usize> = match by_name_arity
                .get(&(call.callee_name.clone(), call.call_arity))
            {
                Some(indices) if !indices.is_empty() => indices.clone(),
                _ => match by_name.get(&call.callee_name) {
                    Some(indices) => indices.clone(),
                    None => continue,
                },
            };
            let mut candidates = candidates;
            if candidates.len() > 1
                && let Some(caller_scope) = &call.caller_scope
            {
                let scoped: Vec<usize> = candidates
                    .iter()
                    .copied()
                    .filter(|index| {
                        self.functions[*index].scope_name.as_deref() == Some(caller_scope.as_str())
                    })
                    .collect();
                if !scoped.is_empty() {
                    candidates = scoped;
                }
            }
            if candidates.len() == 1 {
                resolved.push((call_index, self.functions[candidates[0]].symbol_id.clone()));
            }
        }
        for (call_index, callee_id) in resolved {
            let call = &mut self.calls[call_index];
            call.callee_id = Some(callee_id.clone());
            let properties = json!({
                "line": call.call_line,
                "column": call.call_column,
                "call_type": call.call_type,
                "arity": call.call_arity,
            });
            let caller_id = call.caller_id.clone();
            record_relation(
                &mut self.relations,
                &caller_id,
                "Function",
                &callee_id,
                "Function",
                "POSSIBLE_CALLS",
                properties,
            );
        }
    }
}

/// `parse_rust_file` — trả payload của một file .rs.
pub fn parse_rust_file(path: &Path, root: &Path) -> Result<FilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let source = std::fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_rust::LANGUAGE.into())
        .map_err(|error| format!("tree-sitter-rust language setup failed: {error}"))?;
    let tree = parser
        .parse(&source, None)
        .ok_or_else(|| format!("{}: tree-sitter parse returned None", path.display()))?;
    let root_node = tree.root_node();
    let (has_error, error_nodes) = tree_error_stats(root_node);
    let code = decode_ignore(&source);
    let comment = extract_file_comment(root_node, &source);
    let (using_namespaces, using_imports) = collect_imports(root_node, &source);
    let includes = collect_includes(root_node, &source);
    let macros = collect_macros(root_node, &source);
    let file_def = FileDef {
        file_path: rel_path.clone(),
        start_line: 1,
        end_line: code.matches('\n').count() as i64 + 1,
        code,
        comment: comment.clone(),
        summary: comment,
        includes,
        using_namespaces,
        using_imports,
        macros,
    };
    let mut walker = Walker {
        source,
        rel_path,
        namespaces: Vec::new(),
        types: Vec::new(),
        functions: Vec::new(),
        fields: Vec::new(),
        aliases: Vec::new(),
        templates: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
        namespace_registry: HashSet::new(),
        type_registry: std::collections::HashMap::new(),
        external_types: HashSet::new(),
    };
    let mut scope_stack: Vec<String> = Vec::new();
    walker.walk(root_node, &mut scope_stack, None);
    walker.resolve_calls();
    Ok(FilePayload {
        functions: walker.functions,
        calls: walker.calls,
        types: walker.types,
        namespaces: walker.namespaces,
        relations: walker.relations,
        fields: walker.fields,
        aliases: walker.aliases,
        templates: walker.templates,
        file_def,
        parse_meta: ParseMeta {
            has_error,
            error_nodes,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_str(code: &str) -> FilePayload {
        use std::sync::atomic::{AtomicU64, Ordering};
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let unique = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!(
            "analyzer_rust_test_{}_{}",
            std::process::id(),
            unique
        ));
        std::fs::create_dir_all(&dir).expect("mkdir");
        let file = dir.join("sample.rs");
        std::fs::write(&file, code).expect("write");
        let payload = parse_rust_file(&file, &dir).expect("parse");
        let _ = std::fs::remove_file(&file);
        let _ = std::fs::remove_dir(&dir);
        payload
    }

    #[test]
    fn parses_struct_impl_calls_and_macros() {
        let payload = parse_str(
            "mod inner {\n\
             \x20 pub struct Point { pub x: i32 }\n\
             \x20 impl Point {\n\
             \x20   pub fn new(x: i32) -> Point { Point { x } }\n\
             \x20   pub fn get(&self) -> i32 { self.x }\n\
             \x20 }\n\
             }\n\
             fn main() {\n\
             \x20 let p = inner::Point::new(3);\n\
             \x20 println!(\"{}\", p.get());\n\
             }\n",
        );
        assert_eq!(payload.file_def.file_path, "sample.rs");
        // types: Point (struct) + 2 external (Point impl, i32 param type)
        let mut type_kinds: Vec<&str> = payload.types.iter().map(|t| t.kind.as_str()).collect();
        type_kinds.sort_unstable();
        assert!(type_kinds.contains(&"struct"));
        assert_eq!(
            payload.functions.iter().map(|f| f.name.as_str()).collect::<Vec<_>>(),
            vec!["new", "get", "main"]
        );
        // new/get scoped trong "inner::Point"; main top-level.
        assert_eq!(
            payload
                .functions
                .iter()
                .map(|f| f.scope_name.clone().unwrap_or_default())
                .collect::<Vec<_>>(),
            vec!["inner::Point", "inner::Point", ""]
        );
        // resolve: `p.get()` nằm TRONG token_tree của macro println nên
        // grammar không tạo call_expression (khớp Python reference); các call
        // nhìn thấy: Point::new(3) [arity 1], Vec::new() [arity 0 → fallback
        // by_name], vec_push(&v) [arity 1].
        let resolved: Vec<(&str, &str)> = payload
            .calls
            .iter()
            .filter_map(|call| {
                call.callee_id
                    .as_deref()
                    .map(|id| (call.callee_name.as_str(), id))
            })
            .collect();
        assert_eq!(
            resolved.iter().map(|(name, _)| *name).collect::<Vec<_>>(),
            vec!["new"]
        );
        assert!(payload.calls.iter().any(|call| call.call_type == "macro"));
        assert!(payload
            .relations
            .iter()
            .any(|rel| rel.rel_type == "POSSIBLE_CALLS"));
    }

    #[test]
    fn parses_type_alias_and_extern_crate() {
        let payload = parse_str(
            "extern crate serde;\n\
             type Alias = Vec<String>;\n\
             fn use_alias(v: &Alias) {}\n",
        );
        assert_eq!(payload.file_def.includes, vec!["serde"]);
        assert_eq!(payload.aliases.len(), 1);
        assert_eq!(payload.aliases[0].target_name.as_deref(), Some("Vec<String>"));
        // `_ensure_external_type` + `_add_type_use` dùng 2 registry RIÊNG nên
        // "Vec<String>" xuất hiện 2 lần trong types (hành vi Python gốc).
        let external_names: Vec<&str> = payload
            .types
            .iter()
            .filter(|t| t.kind == "external")
            .map(|t| t.name.as_str())
            .collect();
        assert_eq!(external_names, vec!["Vec<String>", "Vec<String>"]);
        assert!(payload
            .relations
            .iter()
            .any(|rel| rel.rel_type == "ALIASES" && rel.target_id == "Vec<String>"));
    }

    #[test]
    fn type_use_keeps_angle_brackets_like_python() {
        // charclass `[<&*\\[\\](),;]` của Python parse thành
        // class{<,&,*,\,[} + literal `(),;]` ⇒ gần no-op: candidate giữ nguyên
        // dấu `<>`. Reference: re.sub đó không match "Vec<String>".
        let payload = parse_str("type Alias = Vec<String>;\n");
        assert!(payload
            .relations
            .iter()
            .any(|rel| rel.rel_type == "USES_TYPE" && rel.target_id == "Vec<String>"));
    }
}

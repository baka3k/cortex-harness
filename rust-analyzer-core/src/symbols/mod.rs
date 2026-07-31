//! Symbol extractor dispatch (per `_walk_tree` branch in cplus_analyzer.py).
//!
//! Each module owns one extraction category and is invoked from the walker.

pub mod function;
pub mod type_def;
pub mod namespace;
pub mod field;
pub mod alias;
pub mod template;

// Re-export text helpers used by sibling modules so they can be imported via
// `crate::symbols::*` without needing the private `crate::text` import path.
pub use crate::text::{extract_base_type, first_identifier_str, node_text, normalize_type_signature};

use tree_sitter::Node;

/// Compute the qualified name from a scope + name (None scope → bare name).
#[inline]
pub fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(s) if !s.is_empty() => format!("{}::{}", s, name),
        _ => name.to_string(),
    }
}

/// Compute a function symbol ID matching the Python `_symbol_id` helper.
pub fn function_symbol_id(scope: Option<&str>, name: &str, arity: u32, rel_path: &str) -> String {
    let qn = qualified_name(scope, name);
    format!("{}/{}@{}", qn, arity, rel_path)
}

/// Compute a type ID matching `_type_id` (no leading `type::` prefix).
pub fn type_id(scope: Option<&str>, name: &str) -> String {
    qualified_name(scope, name)
}

/// Compute a namespace ID matching `_namespace_id`.
pub fn namespace_id(name: &str) -> String {
    format!("namespace::{}", name)
}

/// Compute an alias ID matching the Python `_qualified_name(scope, alias_name)@path` pattern.
pub fn alias_id(scope: Option<&str>, name: &str, rel_path: &str) -> String {
    let qn = qualified_name(scope, name);
    format!("alias::{}@{}", qn, rel_path)
}

/// Compute a field ID matching `{scope}::{name}@{path}`.
pub fn field_id(scope: Option<&str>, name: &str, rel_path: &str) -> String {
    let qn = qualified_name(scope, name);
    format!("{}@{}", qn, rel_path)
}

/// Compute a function-type ID matching `_function_type_id` (uses stable hash).
pub fn function_type_id(sig: &str) -> String {
    let normalized = normalize_type_signature(sig);
    format!("functype::{}", crate::text::stable_point_id(&normalized))
}

/// Compute template ID matching the Python pattern `template::{path}:{start}:{end}`.
pub fn template_id(rel_path: &str, start_line: u32, end_line: u32) -> String {
    format!("template::{}:{}:{}", rel_path, start_line, end_line)
}

/// Compute anonymous name (`Anonymous{Type}@{line}:{col}`).
pub fn anonymous_name(prefix: &str, node: Node) -> String {
    let pos = node.start_position();
    format!(
        "Anonymous{}@{}:{}",
        prefix,
        pos.row as u32 + 1,
        pos.column as u32 + 1
    )
}

/// Look up the first identifier in a node (recursive).
pub fn first_identifier_node(node: Node, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier" | "type_identifier" | "field_identifier" | "namespace_identifier" => {
            Some(node_text(node, source).to_string())
        }
        _ => {
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if let Some(s) = first_identifier_node(child, source) {
                    return Some(s);
                }
            }
            None
        }
    }
}

/// Pull a base-class list out of a `base_class_clause` child.
pub fn extract_base_types(node: Node, source: &[u8]) -> Vec<String> {
    let mut base_clause = node.child_by_field_name("base_class_clause");
    if base_clause.is_none() {
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            if child.kind() == "base_class_clause" {
                base_clause = Some(child);
                break;
            }
        }
    }
    let Some(clause) = base_clause else {
        return Vec::new();
    };

    let mut results = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut cursor = clause.walk();
    for child in clause.children(&mut cursor) {
        if !child.is_named() {
            continue;
        }
        if matches!(child.kind(), "access_specifier" | "virtual_specifier") {
            continue;
        }
        let text = node_text(child, source);
        let Some(name) = extract_base_type(text) else {
            continue;
        };
        if matches!(name.as_str(), "public" | "private" | "protected" | "virtual") {
            continue;
        }
        if seen.insert(name.clone()) {
            results.push(name);
        }
    }
    results
}

/// Compute arity from a declarator's `parameter_list` child.
pub fn declarator_arity(declarator: Option<Node>) -> u32 {
    let Some(decl) = declarator else {
        return 0;
    };
    let mut count = 0;
    let mut cursor = decl.walk();
    for child in decl.children(&mut cursor) {
        if child.kind() == "parameter_list" {
            let mut pc = child.walk();
            for grand in child.children(&mut pc) {
                if grand.kind() == "parameter_declaration" {
                    count += 1;
                }
            }
            break;
        }
    }
    count
}

/// Extract function name from a declarator (function_declarator or field_declarator).
pub fn extract_function_name(declarator: Option<Node>, source: &[u8]) -> Option<String> {
    let decl = declarator?;
    let name_node = decl.child_by_field_name("declarator").unwrap_or(decl);
    // Walk into function_declarator / pointer_declarator / array_declarator chains
    let name = match name_node.kind() {
        "function_declarator" | "pointer_declarator" | "array_declarator" | "reference_declarator" => {
            // Try field "declarator" again; if absent, use identifier child
            if let Some(inner) = name_node.child_by_field_name("declarator") {
                first_identifier_node(inner, source)
            } else {
                first_identifier_node(name_node, source)
            }
        }
        _ => first_identifier_node(name_node, source),
    };
    // Strip parens around the name if present
    name.map(|s| {
        let trimmed = s.trim();
        // Unwrap potential parenthesized form
        if trimmed.starts_with('(') && trimmed.ends_with(')') {
            trimmed[1..trimmed.len() - 1].to_string()
        } else {
            trimmed.to_string()
        }
    })
}

/// Extract a scope from a qualified declarator (e.g. `MyClass::MyNamespace::foo`).
pub fn extract_declarator_scope(declarator: Option<Node>, source: &[u8]) -> Option<String> {
    let decl = declarator?;
    let name_node = decl.child_by_field_name("declarator").unwrap_or(decl);
    let text = node_text(name_node, source);
    if text.contains("::") && !text.contains('(') {
        if let Some(idx) = text.rfind("::") {
            return Some(text[..idx].trim().to_string());
        }
    }
    None
}

/// Compute scope string from stacks (`::`.join(stacks)).
pub fn scope_from_stacks(namespace_stack: &[String], type_stack: &[String]) -> Option<String> {
    let mut all: Vec<&str> = Vec::new();
    all.extend(namespace_stack.iter().map(|s| s.as_str()));
    all.extend(type_stack.iter().map(|s| s.as_str()));
    if all.is_empty() {
        None
    } else {
        Some(all.join("::"))
    }
}

/// Extract the `using namespace foo;` name.
pub fn extract_using_namespace(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    // Look for "using" keyword followed by "namespace" then an identifier
    let lower = text.to_ascii_lowercase();
    if !lower.contains("using") || !lower.contains("namespace") {
        return None;
    }
    // Find the keyword indices
    let ns_pos = lower.find("namespace")? + "namespace".len();
    let tail = text[ns_pos..].trim();
    // Strip trailing semicolon / braces
    let cleaned: String = tail
        .chars()
        .take_while(|c| *c != ';' && *c != '{' && *c != '}')
        .collect();
    let cleaned = cleaned.trim();
    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned.to_string())
    }
}

/// Extract a qualified using import (e.g. `using foo::Bar;`).
pub fn extract_using_qualified(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    // Find "using" keyword
    let lower = text.to_ascii_lowercase();
    if !lower.starts_with("using") {
        return None;
    }
    let start = "using".len();
    let tail = text[start..].trim_start();
    // Skip "namespace" form — already handled by extract_using_namespace
    if tail.to_ascii_lowercase().starts_with("namespace") {
        return None;
    }
    let cleaned: String = tail
        .chars()
        .take_while(|c| *c != ';' && *c != '{' && *c != '}')
        .collect();
    let cleaned = cleaned.trim().trim_end_matches('=').trim();
    if cleaned.is_empty() || !cleaned.contains("::") {
        None
    } else {
        Some(cleaned.to_string())
    }
}

/// Compute call arity from the `arguments` field of a call node.
pub fn call_arity(call_node: Node) -> u32 {
    if let Some(args) = call_node.child_by_field_name("arguments") {
        let mut count = 0;
        let mut cursor = args.walk();
        for child in args.children(&mut cursor) {
            if child.is_named() && child.kind() != "comment" {
                count += 1;
            }
        }
        return count;
    }
    0
}

/// Pull call info (callee_name, call_type) from a call_expression node.
pub fn extract_call_info(call_node: Node, source: &[u8]) -> (Option<String>, String) {
    if let Some(function_node) = call_node.child_by_field_name("function") {
        let raw = node_text(function_node, source).trim().to_string();
        let call_type = if raw.contains("->") || raw.contains('.') {
            "member_call"
        } else if raw.contains("::") {
            "qualified_call"
        } else {
            "call_expression"
        };
        return (Some(crate::text::normalize_call_name(&raw)), call_type.to_string());
    }
    let text = node_text(call_node, source);
    let raw = text.split('(').next().unwrap_or("").trim().to_string();
    (Some(crate::text::normalize_call_name(&raw)), "call_expression".to_string())
}

/// Reference for short alias lookup when many helpers live alongside the walker.
pub use crate::walker::WalkContext as _Ctx;

// ============================================================================
// Symbol data structures — Rust mirrors of the Python dataclasses.
// The fields match the ParseResult JSON schema 1:1.
// ============================================================================

#[derive(Debug, Clone, Default)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_byte: u32,
    pub end_byte: u32,
    pub start_line: u32,
    pub end_line: u32,
    pub arity: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct NamespaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct TypeDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct RelationEdge {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
    pub properties: std::collections::HashMap<String, String>,
}

#[derive(Debug, Clone, Default)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_file: String,
    pub caller_scope: Option<String>,
    pub call_line: u32,
    pub call_column: u32,
    pub call_start_byte: u32,
    pub call_branch_kind: String,
    pub call_loop_depth: u32,
    pub call_control_frames_json: String,
    pub call_type: String,
    pub call_arity: u32,
    pub callee_name: String,
    pub callee_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct FunctionTypeDef {
    pub symbol_id: String,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
}

#[derive(Debug, Clone, Default)]
pub struct FieldDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub scope_name: Option<String>,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
}

#[derive(Debug, Clone, Default)]
pub struct AliasDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub target_name: Option<String>,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
}

#[derive(Debug, Clone, Default)]
pub struct TemplateDef {
    pub symbol_id: String,
    pub name: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
}

#[derive(Debug, Clone, Default)]
pub struct ParseMeta {
    pub parser_language: String,
    pub parser_language_initial: String,
    pub header_retry_attempted: bool,
    pub header_retry_selected: bool,
    pub has_error: bool,
    pub error_nodes: u32,
    pub error_nodes_initial: u32,
    pub header_retry_error_nodes: Option<u32>,
    pub header_retry_has_error: Option<bool>,
}

impl ParseMeta {
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "parser_language": self.parser_language,
            "parser_language_initial": self.parser_language_initial,
            "header_retry_attempted": self.header_retry_attempted,
            "header_retry_selected": self.header_retry_selected,
            "has_error": self.has_error,
            "error_nodes": self.error_nodes,
            "error_nodes_initial": self.error_nodes_initial,
            "header_retry_error_nodes": self.header_retry_error_nodes,
            "header_retry_has_error": self.header_retry_has_error,
        })
    }
}

#[derive(Debug, Default)]
pub struct ParseOutput {
    pub file_def: FileDef,
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub function_types: Vec<FunctionTypeDef>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<AliasDef>,
    pub templates: Vec<TemplateDef>,
    pub using_namespaces: Vec<String>,
    pub using_imports: std::collections::HashMap<String, String>,
    pub includes: Vec<String>,
    pub macros: std::collections::HashMap<String, String>,
    pub parse_meta: ParseMeta,
}

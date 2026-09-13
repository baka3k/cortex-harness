//! Port `_walk_tree` + `parse_c_family_file` của `cplus_analyzer.py` —
//! tree-sitter C/C++ parse (grammar `tree-sitter-c` 0.24.2 /
//! `tree-sitter-cpp` 0.23.4, khớp fallback path của Python vì
//! `tree_sitter_languages.get_parser` raise TypeError với tree_sitter 0.26).

use std::collections::{BTreeMap, HashMap, VecDeque};
use std::path::Path;

use serde_json::{json, Map, Value};
use tree_sitter::Node;

use cortex_analyzer_framework::ts::{decode_ignore, node_text};

use crate::cscan;
use crate::identity::{build_function_identity, FUNCTION_IDENTITY_SCHEMA};
use crate::position::{relpath, splitext};
use crate::quality::{
    candidate_is_strictly_better, classify_quality, collect_tree_sitter_damage,
    context_fingerprint, source_fingerprint, tree_selection_semantic_yield, DamageSummary,
    ParseContext, QualityRecord, SemanticYield,
};

pub type Row = Map<String, Value>;

pub fn decode_ignore_bytes(bytes: &[u8]) -> String {
    decode_ignore(bytes)
}

/// `node.children(&mut cursor)` — tree-sitter 0.25 yêu cầu cursor argument.
pub fn children_of<'a, 't>(node: Node<'t>) -> impl Iterator<Item = Node<'t>> + 'a
where
    't: 'a,
{
    let mut cursor = node.walk();
    let mut done = false;
    let mut first = true;
    std::iter::from_fn(move || {
        if done {
            return None;
        }
        let advanced = if first {
            first = false;
            cursor.goto_first_child()
        } else {
            cursor.goto_next_sibling()
        };
        if !advanced {
            done = true;
            return None;
        }
        Some(cursor.node())
    })
}

// ── Helpers khớp các hàm `_` riêng lẻ của cplus_analyzer.py ────────────────

/// `_extract_leading_comment` — nối các comment sibling liền trước.
fn extract_leading_comment(node: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() == "comment" {
            let text = node_text(p, source).trim().to_string();
            if !text.is_empty() {
                parts.push(text);
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

/// `_extract_file_comment` — comment ở đầu file (trước named child đầu).
fn extract_file_comment(root: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut cursor = root.walk();
    if !cursor.goto_first_child() {
        return String::new();
    }
    loop {
        let child = cursor.node();
        if child.kind() == "comment" {
            let text = node_text(child, source).trim().to_string();
            if !text.is_empty() {
                parts.push(text);
            }
        } else if child.is_named() {
            break;
        }
        if !cursor.goto_next_sibling() {
            break;
        }
    }
    if parts.is_empty() {
        String::new()
    } else {
        parts.join("\n")
    }
}

/// `_node_snippet` — text từ sau chuỗi leading comments + (start_line, end_line).
fn node_snippet(node: Node, source: &[u8]) -> (String, i64, i64) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() == "comment" {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = decode_ignore(&source[start_byte..node.end_byte()]);
    // Python dùng start_point[0] + 1 của start_node (comment đầu tiên) —
    // line_from_byte(start_byte) tương đương.
    let start_line = line_from_byte(source, start_byte) as i64;
    let end_line = node.end_position().row as i64 + 1;
    (snippet, start_line, end_line)
}

fn line_from_byte(source: &[u8], byte_index: usize) -> usize {
    source[..byte_index].iter().filter(|&&b| b == b'\n').count() + 1
}

/// `_find_nodes_by_type` — pre-order cursor walk.
pub fn find_nodes_by_type<'t>(root: Node<'t>, kind: &str) -> Vec<Node<'t>> {
    let mut found = Vec::new();
    let mut cursor = root.walk();
    loop {
        if cursor.node().kind() == kind {
            found.push(cursor.node());
        }
        if cursor.goto_first_child() {
            continue;
        }
        if cursor.goto_next_sibling() {
            continue;
        }
        loop {
            if !cursor.goto_parent() {
                return found;
            }
            if cursor.goto_next_sibling() {
                break;
            }
        }
    }
}

/// `_first_identifier`.
fn first_identifier(node: Node, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "identifier" | "type_identifier" | "field_identifier" | "namespace_identifier"
    ) {
        return Some(node_text(node, source));
    }
    let mut cursor = node.walk();
    if cursor.goto_first_child() {
        loop {
            let child = cursor.node();
            if let Some(result) = first_identifier(child, source) {
                return Some(result);
            }
            if !cursor.goto_next_sibling() {
                break;
            }
        }
    }
    None
}

fn extract_type_name(text: &str) -> Option<String> {
    let re = regex::Regex::new(r"[A-Za-z_][A-Za-z0-9_:]*").ok()?;
    re.find(text).map(|m| m.as_str().to_string())
}

fn normalize_type_signature(text: &str) -> String {
    let re = regex::Regex::new(r"\s+").expect("ws regex");
    re.replace_all(text.trim(), " ").to_string()
}

fn stable_point_id(symbol_id: &str) -> String {
    uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, symbol_id.as_bytes()).to_string()
}

fn function_type_id(type_signature: &str) -> String {
    let normalized = normalize_type_signature(type_signature);
    format!("functype::{}", stable_point_id(&normalized))
}

fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(s) => format!("{s}::{name}"),
        None => name.to_string(),
    }
}

fn type_id(scope: Option<&str>, name: &str) -> String {
    qualified_name(scope, name)
}

fn namespace_id(name: &str) -> String {
    format!("namespace::{name}")
}

fn extract_scope_stack(stack: &[String]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(stack.join("::"))
    }
}

fn strip_template_args(text: &str) -> String {
    let re = regex::Regex::new(r"<[^<>]*>").expect("template args regex");
    re.replace_all(text, "").to_string()
}

fn normalize_call_name(text: &str) -> String {
    let mut cleaned = strip_template_args(text);
    cleaned = cleaned.replace("this->", "");
    cleaned = cleaned.replace("->", ".");
    cleaned = cleaned.replace(['&', '*'], "");
    cleaned = cleaned.trim().to_string();
    if cleaned.contains('.')
        && let Some(idx) = cleaned.rfind('.') {
            cleaned = cleaned[idx + 1..].to_string();
        }
    cleaned.trim().to_string()
}

fn extract_base_type(type_text: &str) -> Option<String> {
    let cleaned = strip_template_args(type_text);
    let re = regex::Regex::new(
        r"\b(const|volatile|mutable|static|extern|register|inline|struct|class|enum|typename)\b",
    )
    .ok()?;
    let cleaned = re.replace_all(&cleaned, "");
    let cleaned = cleaned.replace(['&', '*'], " ");
    let re_ws = regex::Regex::new(r"\s+").ok()?;
    let cleaned = re_ws.replace_all(cleaned.trim(), " ").to_string();
    extract_type_name(&cleaned)
}

fn pointer_kind(type_text: &str) -> Option<&'static str> {
    if type_text.contains("&&") {
        Some("rvalue_ref")
    } else if type_text.contains('&') {
        Some("lvalue_ref")
    } else if type_text.contains('*') {
        Some("pointer")
    } else {
        None
    }
}

fn anonymous_name(prefix: &str, node: Node) -> String {
    let point = node.start_position();
    format!("Anonymous{prefix}@{}:{}", point.row + 1, point.column + 1)
}

/// `_extract_base_types` — danh sách base từ base_class_clause.
fn extract_base_types(node: Node, source: &[u8]) -> Vec<String> {
    let mut base_clause = node.child_by_field_name("base_class_clause");
    if base_clause.is_none() {
        for child in children_of(node) {
            if child.kind() == "base_class_clause" {
                base_clause = Some(child);
                break;
            }
        }
    }
    let Some(base_clause) = base_clause else {
        return Vec::new();
    };
    let mut results: Vec<String> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for child in children_of(base_clause) {
        if !child.is_named() {
            continue;
        }
        if matches!(child.kind(), "access_specifier" | "virtual_specifier") {
            continue;
        }
        let Some(name) = extract_base_type(&node_text(child, source)) else {
            continue;
        };
        if matches!(name.as_str(), "public" | "private" | "protected" | "virtual") {
            continue;
        }
        if !seen.insert(name.clone()) {
            continue;
        }
        results.push(name);
    }
    results
}

/// `_iter_calls` — call_expression sorted theo start_byte.
fn iter_calls<'t>(func_node: Node<'t>) -> Vec<Node<'t>> {
    let mut calls = find_nodes_by_type(func_node, "call_expression");
    calls.sort_by_key(|n| n.start_byte());
    calls
}

/// `_extract_call_info` — (callee chuẩn hoá, call_type).
fn extract_call_info(call_node: Node, source: &[u8]) -> (Option<String>, &'static str) {
    if let Some(function_node) = call_node.child_by_field_name("function") {
        let raw = node_text(function_node, source).trim().to_string();
        let call_type = if raw.contains("->") || raw.contains('.') {
            "member_call"
        } else if raw.contains("::") {
            "qualified_call"
        } else {
            "call_expression"
        };
        return (Some(normalize_call_name(&raw)), call_type);
    }
    let raw = node_text(call_node, source).split('(').next().unwrap_or("").trim().to_string();
    (Some(normalize_call_name(&raw)), "call_expression")
}

/// `_identifier_from_node`.
fn identifier_from_node(node: Option<Node>, source: &[u8]) -> Option<String> {
    let node = node?;
    if matches!(
        node.kind(),
        "identifier" | "field_identifier" | "scoped_identifier" | "namespace_identifier"
    ) {
        return Some(node_text(node, source).trim().to_string());
    }
    let text = node_text(node, source).trim().to_string();
    let re = regex::Regex::new(r"[A-Za-z_][A-Za-z0-9_:]*").ok()?;
    re.find(&text).map(|m| m.as_str().to_string())
}

/// Danh sách (child, field_name) qua cursor — cho `field_name_for_child`.
fn children_with_field_names<'t>(node: Node<'t>) -> Vec<(Node<'t>, Option<String>)> {
    let mut out = Vec::new();
    let mut cursor = node.walk();
    if !cursor.goto_first_child() {
        return out;
    }
    loop {
        out.push((cursor.node(), cursor.field_name().map(str::to_string)));
        if !cursor.goto_next_sibling() {
            break;
        }
    }
    out
}

/// `_iter_field_declarators` — mọi declarator của field_declaration.
fn iter_field_declarators<'t>(field_node: Node<'t>) -> Vec<Node<'t>> {
    let mut seen: std::collections::HashSet<(usize, usize, String)> = std::collections::HashSet::new();
    let mut out: Vec<Node<'t>> = Vec::new();
    let mut any = false;
    for (child, field_name) in children_with_field_names(field_node) {
        if !child.is_named() {
            continue;
        }
        if field_name.as_deref() != Some("declarator") {
            continue;
        }
        any = true;
        let key = (child.start_byte(), child.end_byte(), child.kind().to_string());
        if seen.insert(key) {
            out.push(child);
        }
    }
    if any {
        return out;
    }
    // Fallback cho parser không expose field names.
    if let Some(first_decl) = field_node.child_by_field_name("declarator") {
        let key = (first_decl.start_byte(), first_decl.end_byte(), first_decl.kind().to_string());
        seen.insert(key);
        out.push(first_decl);
    }
    for child in children_of(field_node) {
        if !child.is_named() {
            continue;
        }
        if matches!(
            child.kind(),
            "field_identifier"
                | "identifier"
                | "array_declarator"
                | "pointer_declarator"
                | "reference_declarator"
                | "function_declarator"
                | "parenthesized_declarator"
                | "init_declarator"
        ) {
            let key = (child.start_byte(), child.end_byte(), child.kind().to_string());
            if seen.insert(key) {
                out.push(child);
            }
        }
    }
    out
}

/// `_iter_declaration_declarators`.
fn iter_declaration_declarators<'t>(decl_node: Node<'t>) -> Vec<Node<'t>> {
    let mut seen: std::collections::HashSet<(usize, usize, String)> = std::collections::HashSet::new();
    let mut out: Vec<Node<'t>> = Vec::new();
    let mut any = false;
    for (child, field_name) in children_with_field_names(decl_node) {
        if !child.is_named() {
            continue;
        }
        if field_name.as_deref() != Some("declarator") {
            continue;
        }
        any = true;
        let key = (child.start_byte(), child.end_byte(), child.kind().to_string());
        if seen.insert(key) {
            out.push(child);
        }
    }
    if any {
        return out;
    }
    if let Some(first_decl) = decl_node.child_by_field_name("declarator") {
        let key = (first_decl.start_byte(), first_decl.end_byte(), first_decl.kind().to_string());
        seen.insert(key);
        out.push(first_decl);
    }
    for child in children_of(decl_node) {
        if !child.is_named() {
            continue;
        }
        if matches!(
            child.kind(),
            "identifier"
                | "init_declarator"
                | "pointer_declarator"
                | "reference_declarator"
                | "array_declarator"
                | "function_declarator"
                | "parenthesized_declarator"
        ) {
            let key = (child.start_byte(), child.end_byte(), child.kind().to_string());
            if seen.insert(key) {
                out.push(child);
            }
        }
    }
    out
}

/// `_field_name_from_declarator`.
fn field_name_from_declarator(declarator: Option<Node>, source: &[u8]) -> Option<String> {
    let declarator = declarator?;
    if matches!(declarator.kind(), "field_identifier" | "identifier") {
        return Some(node_text(declarator, source).trim().to_string());
    }
    let nested = declarator.child_by_field_name("declarator");
    if let Some(nested) = nested
        && nested != declarator
            && let Some(name) = field_name_from_declarator(Some(nested), source)
                && !name.is_empty() {
                    return Some(name);
                }
    for kind in ["field_identifier", "identifier"] {
        for node in find_nodes_by_type(declarator, kind) {
            let value = node_text(node, source).trim().to_string();
            if !value.is_empty() {
                return Some(value);
            }
        }
    }
    None
}

/// `_declarator_arity`.
fn declarator_arity(declarator: Option<Node>) -> i64 {
    let Some(declarator) = declarator else {
        return 0;
    };
    for child in children_of(declarator) {
        if child.kind() == "parameter_list" {
            return children_of(child)
                .filter(|c| c.kind() == "parameter_declaration")
                .count() as i64;
        }
    }
    if let Some(node) = find_nodes_by_type(declarator, "parameter_list").into_iter().next() {
        return children_of(node)
            .filter(|c| c.kind() == "parameter_declaration")
            .count() as i64;
    }
    0
}

/// `_declaration_type_text`.
fn declaration_type_text(decl_node: Node, source: &[u8]) -> String {
    let type_node = decl_node.child_by_field_name("type");
    let type_text = type_node
        .map(|n| node_text(n, source).trim().to_string())
        .unwrap_or_default();
    let mut prefix_parts: Vec<String> = Vec::new();
    for child in children_of(decl_node) {
        if !child.is_named() {
            continue;
        }
        if matches!(child.kind(), "storage_class_specifier" | "type_qualifier") {
            let text = node_text(child, source).trim().to_string();
            if !text.is_empty() {
                prefix_parts.push(text);
            }
        }
    }
    let prefix_text = prefix_parts.join(" ").trim().to_string();
    if !prefix_text.is_empty() && !type_text.is_empty() {
        return format!("{prefix_text} {type_text}").trim().to_string();
    }
    if !prefix_text.is_empty() {
        prefix_text
    } else {
        type_text
    }
    .trim()
    .to_string()
}

/// `_iter_parameter_declarations`.
fn iter_parameter_declarations<'t>(declarator: Option<Node<'t>>) -> Vec<Node<'t>> {
    let Some(declarator) = declarator else {
        return Vec::new();
    };
    for child in children_of(declarator) {
        if child.kind() == "parameter_list" {
            return children_of(child)
                .filter(|c| c.kind() == "parameter_declaration")
                .collect();
        }
    }
    if let Some(param_list) = find_nodes_by_type(declarator, "parameter_list").into_iter().next() {
        return children_of(param_list)
            .filter(|c| c.kind() == "parameter_declaration")
            .collect();
    }
    Vec::new()
}

/// `_parameter_type_text`.
fn parameter_type_text(param: Node, source: &[u8]) -> String {
    let mut text = node_text(param, source).trim().to_string();
    if let Some(declarator) = param.child_by_field_name("declarator")
        && let Some(name) = field_name_from_declarator(Some(declarator), source)
            && !name.is_empty()
                && let Ok(re) = regex::Regex::new(&format!(
                    r"\b{}\b",
                    regex::escape(&name)
                )) {
                    text = re.replace(&text, "").to_string();
                }
    let text = text.split('=').next().unwrap_or("").to_string();
    let normalized = crate::identity::normalize_syntax(&text);
    if normalized.is_empty() {
        "?".to_string()
    } else {
        normalized
    }
}

/// `_function_qualifiers`.
fn function_qualifiers(declarator: Option<Node>, source: &[u8]) -> String {
    let Some(declarator) = declarator else {
        return String::new();
    };
    let text = node_text(declarator, source);
    let close = text.rfind(')').unwrap_or(0);
    let trailer = if text.rfind(')').is_some() { &text[close + 1..] } else { "" };
    let re = regex::Regex::new(r"\bconst\b|\bvolatile\b|&&|&|\bnoexcept(?:\s*\([^)]*\))?").ok();
    let tokens: Vec<String> = re
        .map(|re| re.find_iter(trailer).map(|m| m.as_str().to_string()).collect())
        .unwrap_or_default();
    crate::identity::normalize_syntax(&tokens.join(" "))
}

/// `_template_arity`.
fn template_arity(node: Node) -> i64 {
    let mut current = Some(node);
    for _ in 0..3 {
        let Some(cur) = current else { break };
        for child in children_of(cur) {
            if child.kind() == "template_parameter_list" {
                return children_of(child).filter(|c| c.is_named()).count() as i64;
            }
        }
        if cur.kind() == "translation_unit" {
            break;
        }
        current = cur.parent();
    }
    0
}

/// `_function_identity_from_declarator`.
fn function_identity_from_declarator(
    scope: Option<&str>,
    name: &str,
    declarator: Option<Node>,
    owner_node: Node,
    source: &[u8],
    rel_path: &str,
) -> crate::identity::FunctionIdentity {
    let qualified = qualified_name(scope, name);
    let parameters: Vec<String> = iter_parameter_declarations(declarator)
        .iter()
        .map(|param| parameter_type_text(*param, source))
        .collect();
    let parameters = if parameters.len() == 1 && parameters[0] == "void" {
        Vec::new()
    } else {
        parameters
    };
    let owner_text = node_text(owner_node, source);
    let linkage = if regex::Regex::new(r"(^|\s)static(\s|$)")
        .map(|re| re.is_match(&owner_text))
        .unwrap_or(false)
    {
        "internal"
    } else {
        "external"
    };
    let parseable = !name.is_empty() && !name.starts_with("<anonymous");
    build_function_identity(
        &qualified,
        &parameters,
        &function_qualifiers(declarator, source),
        template_arity(owner_node),
        linkage,
        rel_path,
        owner_node.start_byte() as i64,
        parseable,
    )
}

/// `_is_function_pointer_declarator`.
/// LƯU Ý parity: Python so `node is declarator` theo IDENTITY object wrapper
/// của binding — với node lấy từ cursor thì LUÔN khác object nên self-skip
/// không bao giờ chạy. Mọi declarator chứa pointer/reference_declarator
/// (kể cả node gốc tự subtype) đều được classify là function-pointer.
fn is_function_pointer_declarator(declarator: Option<Node>, source: &[u8]) -> bool {
    let Some(declarator) = declarator else {
        return false;
    };
    let text = node_text(declarator, source);
    if text.contains("(*") || text.contains("(&") {
        return true;
    }
    if !find_nodes_by_type(declarator, "pointer_declarator").is_empty() {
        return true;
    }
    if !find_nodes_by_type(declarator, "reference_declarator").is_empty() {
        return true;
    }
    false
}

/// `_is_method_field_declarator`.
fn is_method_field_declarator(declarator: Option<Node>, source: &[u8]) -> bool {
    let Some(declarator) = declarator else {
        return false;
    };
    if declarator.kind() != "function_declarator"
        && find_nodes_by_type(declarator, "function_declarator").is_empty()
    {
        return false;
    }
    if is_function_pointer_declarator(Some(declarator), source) {
        return false;
    }
    true
}

/// `_declarator_is_function`.
fn declarator_is_function(declarator: Option<Node>, source: &[u8]) -> bool {
    let Some(declarator) = declarator else {
        return false;
    };
    if declarator.kind() == "function_declarator" {
        return !is_function_pointer_declarator(Some(declarator), source);
    }
    if find_nodes_by_type(declarator, "function_declarator").is_empty() {
        return false;
    }
    !is_function_pointer_declarator(Some(declarator), source)
}

/// `_call_arity`.
fn call_arity(call_node: Node) -> i64 {
    let args = match call_node.child_by_field_name("arguments") {
        Some(args) => Some(args),
        None => children_of(call_node).find(|c| c.kind() == "argument_list"),
    };
    let Some(args) = args else {
        return 0;
    };
    children_of(args).filter(|c| c.is_named()).count() as i64
}

fn node_contains(outer: Node, inner: Node) -> bool {
    outer.start_byte() <= inner.start_byte() && outer.end_byte() >= inner.end_byte()
}

/// `_collect_call_control_context` — (branch_kind, loop_depth, frames_json).
/// Frames JSON được serialize thủ công để giữ đúng thứ tự key của Python
/// (`json.dumps` giữ insertion order; `serde_json::Map` mặc định sort).
fn collect_call_control_context(call_node: Node) -> (String, i64, String) {
    const LOOP_TYPES: [&str; 6] = [
        "for_statement",
        "while_statement",
        "do_statement",
        "range_based_for_statement",
        "for_range_loop",
        "range_for_statement",
    ];
    const BRANCH_TYPES: [&str; 5] = [
        "if_statement",
        "switch_statement",
        "case_statement",
        "default_statement",
        "conditional_expression",
    ];
    let mut loop_depth = 0i64;
    let mut branch_kind = "none".to_string();
    let mut frames: Vec<String> = Vec::new();
    let mut cursor = call_node.parent();
    while let Some(cur) = cursor {
        let node_type = cur.kind();
        let mut frame: Option<String> = None;
        if LOOP_TYPES.contains(&node_type) {
            loop_depth += 1;
            let point = cur.start_position();
            frame = Some(format!(
                "{{\"kind\": \"loop\", \"type\": {}, \"line\": {}, \"start_byte\": {}, \"end_byte\": {}}}",
                crate::identity::json_ensure_ascii(node_type),
                point.row + 1,
                cur.start_byte(),
                cur.end_byte()
            ));
        } else if BRANCH_TYPES.contains(&node_type) {
            let resolved_branch;
            if node_type == "if_statement" {
                let consequence = cur.child_by_field_name("consequence");
                let alternative = cur.child_by_field_name("alternative");
                if alternative.is_some_and(|alt| node_contains(alt, call_node)) {
                    resolved_branch = "if_else";
                } else if consequence.is_some_and(|cons| node_contains(cons, call_node)) {
                    resolved_branch = "if_then";
                } else {
                    resolved_branch = "if";
                }
            } else if node_type == "conditional_expression" {
                resolved_branch = "ternary";
            } else {
                resolved_branch = "switch_case";
            }
            if branch_kind == "none" {
                branch_kind = resolved_branch.to_string();
            }
            let point = cur.start_position();
            frame = Some(format!(
                "{{\"kind\": \"branch\", \"type\": {}, \"branch\": {}, \"line\": {}, \"start_byte\": {}, \"end_byte\": {}}}",
                crate::identity::json_ensure_ascii(node_type),
                crate::identity::json_ensure_ascii(resolved_branch),
                point.row + 1,
                cur.start_byte(),
                cur.end_byte()
            ));
        }
        if let Some(frame) = frame {
            frames.push(frame);
        }
        cursor = cur.parent();
    }
    frames.reverse();
    (branch_kind, loop_depth, format!("[{}]", frames.join(", ")))
}

/// `_collect_fp_aliases` — assignment `left = right` trong thân hàm.
fn collect_fp_aliases(func_node: Node, source: &[u8]) -> HashMap<String, String> {
    let mut aliases = HashMap::new();
    for assign in find_nodes_by_type(func_node, "assignment_expression") {
        let left = assign.child_by_field_name("left");
        let right = assign.child_by_field_name("right");
        let left_name = identifier_from_node(left, source);
        let right_name = identifier_from_node(right, source);
        if let (Some(l), Some(r)) = (left_name, right_name) {
            aliases.insert(l, r);
        }
    }
    aliases
}

/// `_extract_function_name` — LƯU Ý parity: `_first_identifier` trả identifier
/// ĐẦU TIÊN trong declarator (kể cả phần scope của qualified name).
fn extract_function_name(declarator: Option<Node>, source: &[u8]) -> Option<String> {
    let declarator = declarator?;
    for child in children_of(declarator) {
        if child.kind() == "operator_name" {
            return Some(node_text(child, source).trim().to_string());
        }
    }
    if let Some(name) = first_identifier(declarator, source)
        && !name.is_empty() {
            let text = node_text(declarator, source);
            if text.contains(&format!("~{name}")) {
                return Some(format!("~{name}"));
            }
            return Some(name);
        }
    let text = node_text(declarator, source);
    let re = regex::Regex::new(r"operator\s*([^\s(]+)").ok()?;
    re.captures(&text)
        .map(|caps| format!("operator{}", &caps[1]))
}

/// `_extract_declarator_scope`.
fn extract_declarator_scope(declarator: Option<Node>, source: &[u8]) -> Option<String> {
    let declarator = declarator?;
    let mut qualified_text = String::new();
    if declarator.kind() == "qualified_identifier" {
        qualified_text = node_text(declarator, source).trim().to_string();
    } else {
        for node in find_nodes_by_type(declarator, "qualified_identifier") {
            qualified_text = node_text(node, source).trim().to_string();
            if !qualified_text.is_empty() {
                break;
            }
        }
    }
    if qualified_text.is_empty() {
        let head = node_text(declarator, source)
            .split('(')
            .next()
            .unwrap_or("")
            .trim()
            .to_string();
        qualified_text = head;
    }
    if !qualified_text.contains("::") {
        return None;
    }
    let parts: Vec<&str> = qualified_text
        .split("::")
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() < 2 {
        return None;
    }
    Some(parts[..parts.len() - 1].join("::"))
}

/// `_extract_param_name_from_text`.
fn extract_param_name_from_text(text: &str) -> Option<String> {
    if let Ok(re) = regex::Regex::new(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)")
        && let Some(caps) = re.captures(text) {
            return Some(caps[1].to_string());
        }
    let cleaned = regex::Regex::new(r"[&*(),<>]")
        .ok()?
        .replace_all(text, " ")
        .to_string();
    let parts: Vec<&str> = cleaned.split_whitespace().collect();
    parts.last().map(|s| s.to_string())
}

fn extract_using_namespace(text: &str) -> Option<String> {
    let re = regex::Regex::new(r"\busing\s+namespace\s+([A-Za-z_][A-Za-z0-9_:]*)").ok()?;
    re.captures(text).map(|caps| caps[1].to_string())
}

fn extract_using_qualified(text: &str) -> Option<String> {
    let re = regex::Regex::new(r"\busing\s+([A-Za-z_][A-Za-z0-9_:]*)").ok()?;
    let caps = re.captures(text)?;
    let qualified = caps[1].to_string();
    if qualified != "namespace" {
        return Some(qualified);
    }
    None
}

/// `_find_function_pointer_types`.
fn find_function_pointer_types(node: Node, source: &[u8]) -> Vec<String> {
    let mut types = Vec::new();
    for declarator in find_nodes_by_type(node, "function_declarator") {
        let text = node_text(declarator, source);
        if text.contains("(*") {
            types.push(normalize_type_signature(&text));
        }
    }
    types
}

// ── Data structures (payload rows dạng serde Map — khớp asdict Python) ─────

fn push_text_defaults(row: &mut Row) {
    if !row.contains_key("comment") {
        row.insert("comment".into(), json!(""));
    }
    if !row.contains_key("summary") {
        let comment = row.get("comment").cloned().unwrap_or(json!(""));
        row.insert("summary".into(), comment);
    }
    let note_empty = row
        .get("note")
        .map(|n| n.as_str().map(str::is_empty).unwrap_or(true))
        .unwrap_or(true);
    if note_empty {
        let code = row.get("code").and_then(Value::as_str).unwrap_or("");
        let comment = row.get("comment").and_then(Value::as_str).unwrap_or("");
        let summary = row.get("summary").and_then(Value::as_str).unwrap_or("");
        row.insert("note".into(), json!(build_note(code, comment, summary)));
    }
}

/// `_build_note`.
pub fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

/// Payload đầy đủ của một file — khớp dict trả về của `_load_or_parse_payload`.
#[derive(Debug, Clone, Default)]
pub struct FilePayload {
    pub functions: Vec<Row>,
    pub calls: Vec<Row>,
    pub types: Vec<Row>,
    pub namespaces: Vec<Row>,
    pub relations: Vec<Row>,
    pub function_types: Vec<Row>,
    pub fields: Vec<Row>,
    pub aliases: Vec<Row>,
    pub templates: Vec<Row>,
    pub resources: Vec<Row>,
    pub resource_elements: Vec<Row>,
    pub proc_nodes: Vec<Row>,
    pub file_def: Option<Row>,
    pub using_namespaces: Vec<String>,
    pub using_imports: BTreeMap<String, String>,
    pub includes: Vec<String>,
    pub macros: BTreeMap<String, String>,
    pub parse_meta: Row,
}

/// Context chung của một lần parse file.
struct WalkState<'t> {
    source: &'t [u8],
    rel_path: String,
    functions: Vec<Row>,
    calls: Vec<Row>,
    types: Vec<Row>,
    namespaces: Vec<Row>,
    relations: Vec<Row>,
    func_types: BTreeMap<String, Row>,
    fields: Vec<Row>,
    aliases: Vec<Row>,
    templates: Vec<Row>,
    type_registry: std::collections::HashSet<String>,
    namespace_registry: std::collections::HashSet<String>,
}

#[derive(Clone)]
struct Frame<'t> {
    node: Node<'t>,
    namespace_stack: Vec<String>,
    type_stack: Vec<String>,
    using_namespaces: Vec<String>,
    using_imports: BTreeMap<String, String>,
}

impl<'a> WalkState<'a> {
    fn register_type_usage(
        &mut self,
        owner_id: &str,
        owner_label: &str,
        type_text: &str,
    ) {
        let Some(base) = extract_base_type(type_text) else {
            return;
        };
        let tid = type_id(None, &base);
        if !self.type_registry.contains(&tid) {
            self.types.push(type_row(&tid, &base, base.split("::").last().unwrap_or(&base), "external", &self.rel_path, 0, 0, &base, "", "", ""));
            self.type_registry.insert(tid.clone());
        }
        let kind = pointer_kind(type_text);
        let rel_type = if kind.is_some() { "POINTER_TO" } else { "USES_TYPE" };
        let mut properties = Map::new();
        if let Some(kind) = kind {
            properties.insert("kind".into(), json!(kind));
        }
        self.relations.push(relation_row(owner_id, owner_label, &tid, "Type", rel_type, properties));
    }

    fn push_function_row(&mut self, func: Row) {
        self.functions.push(func);
    }
}

#[allow(clippy::too_many_arguments)]
fn type_row(
    symbol_id: &str,
    qualified_name_v: &str,
    name: &str,
    kind: &str,
    file_path: &str,
    start_line: i64,
    end_line: i64,
    code: &str,
    comment: &str,
    summary: &str,
    note: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("qualified_name".into(), json!(qualified_name_v));
    row.insert("name".into(), json!(name));
    row.insert("kind".into(), json!(kind));
    row.insert("file_path".into(), json!(file_path));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("code".into(), json!(code));
    row.insert("comment".into(), json!(comment));
    row.insert("summary".into(), json!(summary));
    row.insert("note".into(), json!(note));
    row
}

fn relation_row(
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    properties: Map<String, Value>,
) -> Row {
    let mut row = Map::new();
    row.insert("source_id".into(), json!(source_id));
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_id".into(), json!(target_id));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("properties".into(), Value::Object(properties));
    row
}

fn function_row(
    symbol_id: &str,
    qualified: &str,
    name: &str,
    kind: &str,
    scope: Option<&str>,
    rel_path: &str,
    start_byte: i64,
    end_byte: i64,
    start_line: i64,
    end_line: i64,
    arity: i64,
    code: &str,
    comment: &str,
    identity: &crate::identity::FunctionIdentity,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("qualified_name".into(), json!(qualified));
    row.insert("name".into(), json!(name));
    row.insert("kind".into(), json!(kind));
    row.insert("scope_name".into(), scope.map(|v| json!(v)).unwrap_or(Value::Null));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_byte".into(), json!(start_byte));
    row.insert("end_byte".into(), json!(end_byte));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("arity".into(), json!(arity));
    row.insert("code".into(), json!(code));
    row.insert("comment".into(), json!(comment));
    row.insert("summary".into(), json!(comment));
    row.insert("note".into(), json!(""));
    row.insert("identity_schema".into(), json!(FUNCTION_IDENTITY_SCHEMA));
    row.insert("signature".into(), json!(identity.canonical_signature));
    row.insert(
        "parameter_types".into(),
        Value::Array(identity.parameter_types.iter().map(|p| json!(p)).collect()),
    );
    row.insert("qualifiers".into(), json!(identity.qualifiers));
    row.insert("template_arity".into(), json!(identity.template_arity));
    row.insert("linkage".into(), json!(identity.linkage));
    row.insert("legacy_symbol_id".into(), json!(identity.legacy_alias));
    row
}

fn field_row(
    symbol_id: &str,
    qualified: &str,
    name: &str,
    scope: Option<&str>,
    type_signature: &str,
    rel_path: &str,
    start_line: i64,
    end_line: i64,
    code: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("qualified_name".into(), json!(qualified));
    row.insert("name".into(), json!(name));
    row.insert("scope_name".into(), scope.map(|v| json!(v)).unwrap_or(Value::Null));
    row.insert("type_signature".into(), json!(type_signature));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("code".into(), json!(code));
    row
}

fn call_row(
    caller_id: &str,
    caller_file: &str,
    caller_scope: Option<&str>,
    call_line: i64,
    call_column: i64,
    call_start_byte: i64,
    call_branch_kind: &str,
    call_loop_depth: i64,
    call_control_frames_json: &str,
    call_type: &str,
    call_arity: i64,
    callee_name: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("caller_id".into(), json!(caller_id));
    row.insert("caller_file".into(), json!(caller_file));
    row.insert(
        "caller_scope".into(),
        caller_scope.map(|v| json!(v)).unwrap_or(Value::Null),
    );
    row.insert("call_line".into(), json!(call_line));
    row.insert("call_column".into(), json!(call_column));
    row.insert("call_start_byte".into(), json!(call_start_byte));
    row.insert("call_branch_kind".into(), json!(call_branch_kind));
    row.insert("call_loop_depth".into(), json!(call_loop_depth));
    row.insert(
        "call_control_frames_json".into(),
        json!(call_control_frames_json),
    );
    row.insert("call_type".into(), json!(call_type));
    row.insert("call_arity".into(), json!(call_arity));
    row.insert("callee_name".into(), json!(callee_name));
    row.insert("callee_id".into(), Value::Null);
    row
}

const TEMPLATE_CHILD_TYPES: [&str; 6] = [
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "function_definition",
    "function_declaration",
];

/// `_walk_tree` — LIFO work stack, mỗi frame copy using-state.
#[allow(clippy::too_many_lines)]
fn walk_tree<'t>(root: Node<'t>, state: &mut WalkState<'t>) {
    let mut work: VecDeque<Frame<'_>> = VecDeque::new();
    work.push_back(Frame {
        node: root,
        namespace_stack: Vec::new(),
        type_stack: Vec::new(),
        using_namespaces: Vec::new(),
        using_imports: BTreeMap::new(),
    });
    while let Some(frame) = work.pop_back() {
        let node = frame.node;
        let namespace_stack = frame.namespace_stack;
        let type_stack = frame.type_stack;
        let using_namespaces = frame.using_namespaces;
        let using_imports = frame.using_imports;

        if matches!(node.kind(), "using_directive" | "using_declaration") {
            let text = node_text(node, state.source);
            if let Some(ns_name) = extract_using_namespace(&text) {
                // LƯU Ý parity: append vào bản COPY của frame (Python bị chặn
                // vì dùng `list(using_namespaces)` khi push children) — danh
                // sách trả về của payload luôn là list gốc chưa mutate.
                let mut local = using_namespaces;
                local.push(ns_name);
                continue;
            }
            if let Some(qualified) = extract_using_qualified(&text)
                && qualified.contains("::") {
                    let short = qualified.rsplit("::").next().unwrap_or("").to_string();
                    let mut local = using_imports;
                    local.insert(short, qualified);
                    continue;
                }
            continue;
        }

        if node.kind() == "template_declaration" {
            let name = anonymous_name("Template", node);
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let template_id = format!("template::{}:{}:{}", state.rel_path, start_line, end_line);
            state.templates.push(template_row(&template_id, &name, &state.rel_path, start_line, end_line, &snippet));
            for child in children_of(node) {
                if TEMPLATE_CHILD_TYPES.contains(&child.kind()) {
                    let declarator = child.child_by_field_name("declarator");
                    let target_name = extract_function_name(declarator, state.source);
                    let mut target_id: Option<String> = None;
                    let mut target_label: Option<&str> = None;
                    if matches!(child.kind(), "function_definition" | "function_declaration")
                        && let Some(ref tname) = target_name
                    {
                        let scope = extract_scope_stack(
                            &namespace_stack.iter().chain(type_stack.iter()).cloned().collect::<Vec<_>>(),
                        );
                        let mut arity = 0i64;
                        if let Some(decl) = declarator {
                            for grand in children_of(decl) {
                                if grand.kind() == "parameter_list" {
                                    arity = children_of(grand)
                                        .filter(|c| c.kind() == "parameter_declaration")
                                        .count() as i64;
                                    break;
                                }
                            }
                        }
                        let identity = function_identity_from_declarator(
                            scope.as_deref(),
                            tname,
                            declarator,
                            child,
                            state.source,
                            &state.rel_path,
                        );
                        let _ = arity;
                        target_id = Some(identity.logical_id);
                        target_label = Some("Function");
                    }
                    if matches!(
                        child.kind(),
                        "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier"
                    ) {
                        let tname = first_identifier(child, state.source)
                            .filter(|s| !s.is_empty())
                            .unwrap_or_else(|| anonymous_name("Type", child));
                        // Python: "::".join(ns + ty + [tname]) nếu có stack.
                        let qualified = if namespace_stack.is_empty() && type_stack.is_empty() {
                            tname
                        } else {
                            let mut parts = namespace_stack.clone();
                            parts.extend(type_stack.iter().cloned());
                            parts.push(tname);
                            parts.join("::")
                        };
                        target_id = Some(type_id(None, &qualified));
                        target_label = Some("Type");
                    }
                    if let (Some(tid), Some(tlabel)) = (target_id, target_label) {
                        state.relations.push(relation_row(
                            &template_id, "Template", &tid, tlabel, "TEMPLATES", Map::new(),
                        ));
                    }
                    work.push_back(Frame {
                        node: child,
                        namespace_stack: namespace_stack.clone(),
                        type_stack: type_stack.clone(),
                        using_namespaces: using_namespaces.clone(),
                        using_imports: using_imports.clone(),
                    });
                    break;
                }
            }
            continue;
        }

        if node.kind() == "namespace_definition" {
            let name = first_identifier(node, state.source)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| anonymous_name("Namespace", node));
            let mut ns_stack_for_qualified = namespace_stack.clone();
            ns_stack_for_qualified.push(name.clone());
            let qualified = ns_stack_for_qualified.join("::");
            let ns_id = namespace_id(&qualified);
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let comment = extract_leading_comment(node, state.source);
            state.namespaces.push(namespace_row(&ns_id, &qualified, &name, &state.rel_path, start_line, end_line, &snippet, &comment));
            state.namespace_registry.insert(ns_id.clone());
            if !namespace_stack.is_empty() {
                let parent = namespace_id(&namespace_stack.join("::"));
                state.relations.push(relation_row(
                    &parent, "Namespace", &ns_id, "Namespace", "CONTAINS", Map::new(),
                ));
            }
            let mut new_ns_stack = namespace_stack.clone();
            new_ns_stack.push(name);
            push_children_reversed(
                &mut work, node, new_ns_stack, type_stack, using_namespaces, using_imports,
            );
            continue;
        }

        if matches!(
            node.kind(),
            "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier"
        ) {
            let kind = match node.kind() {
                "class_specifier" => "class",
                "struct_specifier" => "struct",
                "union_specifier" => "union",
                _ => "enum",
            };
            let mut kind = kind.to_string();
            let mut name = first_identifier(node, state.source).unwrap_or_default();
            if name.is_empty() {
                name = anonymous_name(&capitalize(&kind), node);
                kind = format!("anonymous_{kind}");
            }
            let qualified = if namespace_stack.is_empty() && type_stack.is_empty() {
                name.clone()
            } else {
                let mut parts = namespace_stack.clone();
                parts.extend(type_stack.iter().cloned());
                parts.push(name.clone());
                parts.join("::")
            };
            let tid = type_id(None, &qualified);
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let comment = extract_leading_comment(node, state.source);
            state.types.push(type_row(
                &tid,
                &qualified,
                qualified.rsplit("::").next().unwrap_or(&qualified),
                &kind,
                &state.rel_path,
                start_line,
                end_line,
                &snippet,
                &comment,
                &comment,
                "",
            ));
            state.type_registry.insert(tid.clone());
            if !namespace_stack.is_empty() {
                let ns_id = namespace_id(&namespace_stack.join("::"));
                state.relations.push(relation_row(
                    &ns_id, "Namespace", &tid, "Type", "CONTAINS", Map::new(),
                ));
            }
            if !type_stack.is_empty() {
                let mut parts = namespace_stack.clone();
                parts.extend(type_stack.iter().cloned());
                let parent_type = type_id(None, &parts.join("::"));
                state.relations.push(relation_row(
                    &parent_type, "Type", &tid, "Type", "CONTAINS", Map::new(),
                ));
            }
            if matches!(kind.as_str(), "class" | "struct" | "anonymous_class" | "anonymous_struct") {
                for base in extract_base_types(node, state.source) {
                    let base_id = type_id(None, &base);
                    if !state.type_registry.contains(&base_id) {
                        state.types.push(type_row(
                            &base_id,
                            &base,
                            base.split("::").last().unwrap_or(&base),
                            "external",
                            &state.rel_path,
                            0,
                            0,
                            &base,
                            "",
                            "",
                            "",
                        ));
                        state.type_registry.insert(base_id.clone());
                    }
                    state.relations.push(relation_row(
                        &tid, "Type", &base_id, "Type", "EXTENDS", Map::new(),
                    ));
                }
            }
            let mut new_ty_stack = type_stack.clone();
            new_ty_stack.push(name);
            push_children_reversed(
                &mut work, node, namespace_stack, new_ty_stack, using_namespaces, using_imports,
            );
            continue;
        }

        if node.kind() == "function_definition" || node.kind() == "function_declaration" {
            let is_definition = node.kind() == "function_definition";
            let scope_stack: Vec<String> = namespace_stack
                .iter()
                .chain(type_stack.iter())
                .cloned()
                .collect();
            let mut scope = extract_scope_stack(&scope_stack);
            let declarator = node.child_by_field_name("declarator");
            let declarator_scope = extract_declarator_scope(declarator, state.source);
            if scope.is_none() {
                scope = declarator_scope;
            }
            if let Some(name) = extract_function_name(declarator, state.source) {
                let arity = declarator_arity(declarator);
                let identity = function_identity_from_declarator(
                    scope.as_deref(),
                    &name,
                    declarator,
                    node,
                    state.source,
                    &state.rel_path,
                );
                let symbol_id = identity.logical_id.clone();
                let qualified = qualified_name(scope.as_deref(), &name);
                let (snippet, start_line, end_line) = node_snippet(node, state.source);
                let comment = extract_leading_comment(node, state.source);
                let kind = if !is_definition {
                    "declaration"
                } else if name.starts_with('~') {
                    "destructor"
                } else if !type_stack.is_empty() && name == *type_stack.last().unwrap() {
                    "constructor"
                } else {
                    "function"
                };
                state.push_function_row(function_row(
                    &symbol_id,
                    &qualified,
                    &name,
                    kind,
                    scope.as_deref(),
                    &state.rel_path,
                    node.start_byte() as i64,
                    node.end_byte() as i64,
                    start_line,
                    end_line,
                    arity,
                    &snippet,
                    &comment,
                    &identity,
                ));
                if !namespace_stack.is_empty() {
                    let ns_id = namespace_id(&namespace_stack.join("::"));
                    state.relations.push(relation_row(
                        &ns_id, "Namespace", &symbol_id, "Function", "CONTAINS", Map::new(),
                    ));
                }
                let declaring_type_id: Option<String> = if !type_stack.is_empty() {
                    let mut parts = namespace_stack.clone();
                    parts.extend(type_stack.iter().cloned());
                    Some(type_id(None, &parts.join("::")))
                } else if let Some(sc) = &scope {
                    let candidate = type_id(None, sc);
                    if state.type_registry.contains(&candidate) {
                        Some(candidate)
                    } else {
                        None
                    }
                } else {
                    None
                };
                if let Some(declaring_type_id) = declaring_type_id {
                    state.relations.push(relation_row(
                        &declaring_type_id, "Type", &symbol_id, "Function", "DECLARES", Map::new(),
                    ));
                }
                let mut param_fp_types: HashMap<String, String> = HashMap::new();
                if let Some(decl) = declarator {
                    for param in iter_parameter_declarations(Some(decl)) {
                        state.register_type_usage(
                            &symbol_id,
                            "Function",
                            &node_text(param, state.source),
                        );
                        let param_text = node_text(param, state.source);
                        if param_text.contains("(*")
                            || param_text.contains("std::function")
                            || param_text.contains("function<")
                        {
                            let param_name = extract_param_name_from_text(&param_text);
                            let fp_sig = normalize_type_signature(&param_text);
                            let fp_id = function_type_id(&fp_sig);
                            if !state.func_types.contains_key(&fp_id) {
                                state.func_types.insert(fp_id.clone(), func_type_row(
                                    &fp_id, &fp_sig, &state.rel_path, start_line, end_line,
                                ));
                            }
                            if let Some(pname) = &param_name {
                                param_fp_types.insert(pname.clone(), fp_id.clone());
                            }
                            let mut properties = Map::new();
                            properties.insert(
                                "parameter_name".into(),
                                json!(param_name.unwrap_or_default()),
                            );
                            state.relations.push(relation_row(
                                &symbol_id, "Function", &fp_id, "FunctionType",
                                "TAKES_FUNCTION", properties,
                            ));
                        }
                    }
                }
                if is_definition {
                    let fp_aliases = collect_fp_aliases(node, state.source);
                    for call_node in iter_calls(node) {
                        let (Some(callee), call_type) = extract_call_info(call_node, state.source)
                        else {
                            continue;
                        };
                        if callee.is_empty() {
                            continue;
                        }
                        let point = call_node.start_position();
                        let call_line = point.row as i64 + 1;
                        let call_column = point.column as i64 + 1;
                        let call_start_byte = call_node.start_byte() as i64;
                        let (call_branch_kind, call_loop_depth, call_control_frames_json) =
                            collect_call_control_context(call_node);
                        let call_arity_v = call_arity(call_node);
                        state.calls.push(call_row(
                            &symbol_id,
                            &state.rel_path,
                            scope.as_deref(),
                            call_line,
                            call_column,
                            call_start_byte,
                            &call_branch_kind,
                            call_loop_depth,
                            &call_control_frames_json,
                            call_type,
                            call_arity_v,
                            &callee,
                        ));
                        if let Some(fp_target) = fp_aliases.get(&callee) {
                            state.calls.push(call_row(
                                &symbol_id,
                                &state.rel_path,
                                scope.as_deref(),
                                call_line,
                                call_column,
                                call_start_byte,
                                &call_branch_kind,
                                call_loop_depth,
                                &call_control_frames_json,
                                "fp_alias",
                                call_arity_v,
                                fp_target,
                            ));
                        }
                        if let Some(fp_id) = param_fp_types.get(&callee) {
                            let mut properties = Map::new();
                            properties.insert("parameter_name".into(), json!(callee));
                            properties.insert("line".into(), json!(call_line.to_string()));
                            properties.insert("column".into(), json!(call_column.to_string()));
                            state.relations.push(relation_row(
                                &symbol_id, "Function", fp_id, "FunctionType",
                                "CALLS_FUNCTION_POINTER", properties,
                            ));
                        }
                    }
                    for fp_sig in find_function_pointer_types(node, state.source) {
                        let fp_id = function_type_id(&fp_sig);
                        if !state.func_types.contains_key(&fp_id) {
                            state.func_types.insert(fp_id.clone(), func_type_row(
                                &fp_id, &fp_sig, &state.rel_path, start_line, end_line,
                            ));
                        }
                        state.relations.push(relation_row(
                            &symbol_id, "Function", &fp_id, "FunctionType",
                            "TAKES_FUNCTION", Map::new(),
                        ));
                    }
                }
            }
            continue;
        }

        if node.kind() == "declaration" {
            let parent_type = node.parent().map(|p| p.kind()).unwrap_or("");
            if !matches!(parent_type, "translation_unit" | "declaration_list") {
                continue;
            }
            let scope_stack: Vec<String> = namespace_stack
                .iter()
                .chain(type_stack.iter())
                .cloned()
                .collect();
            let scope = extract_scope_stack(&scope_stack);
            let declarators = iter_declaration_declarators(node);
            if declarators.is_empty() {
                continue;
            }
            let decl_type_text = declaration_type_text(node, state.source);
            let has_extern = children_of(node).any(|child| {
                child.is_named()
                    && child.kind() == "storage_class_specifier"
                    && node_text(child, state.source).trim() == "extern"
            });
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let comment = extract_leading_comment(node, state.source);
            let mut seen_function_keys: std::collections::HashSet<(String, i64)> =
                std::collections::HashSet::new();
            let mut seen_variable_names: std::collections::HashSet<String> =
                std::collections::HashSet::new();
            for declarator in declarators {
                if declarator_is_function(Some(declarator), state.source) {
                    let Some(name) = extract_function_name(Some(declarator), state.source) else {
                        continue;
                    };
                    let arity = declarator_arity(Some(declarator));
                    if !seen_function_keys.insert((name.clone(), arity)) {
                        continue;
                    }
                    let identity = function_identity_from_declarator(
                        scope.as_deref(),
                        &name,
                        Some(declarator),
                        node,
                        state.source,
                        &state.rel_path,
                    );
                    let symbol_id = identity.logical_id.clone();
                    let qualified = qualified_name(scope.as_deref(), &name);
                    let kind = if has_extern { "extern_declaration" } else { "declaration" };
                    state.push_function_row(function_row(
                        &symbol_id,
                        &qualified,
                        &name,
                        kind,
                        scope.as_deref(),
                        &state.rel_path,
                        node.start_byte() as i64,
                        node.end_byte() as i64,
                        start_line,
                        end_line,
                        arity,
                        &snippet,
                        &comment,
                        &identity,
                    ));
                    if !namespace_stack.is_empty() {
                        let ns_id = namespace_id(&namespace_stack.join("::"));
                        state.relations.push(relation_row(
                            &ns_id, "Namespace", &symbol_id, "Function", "CONTAINS", Map::new(),
                        ));
                    }
                    for param in iter_parameter_declarations(Some(declarator)) {
                        state.register_type_usage(
                            &symbol_id,
                            "Function",
                            &node_text(param, state.source),
                        );
                    }
                    continue;
                }
                let mut var_name = field_name_from_declarator(Some(declarator), state.source);
                if var_name.is_none() {
                    var_name = first_identifier(declarator, state.source);
                }
                let Some(var_name) = var_name.filter(|v| !v.is_empty()) else {
                    continue;
                };
                if !seen_variable_names.insert(var_name.clone()) {
                    continue;
                }
                let decl_text = node_text(declarator, state.source).trim().to_string();
                let mut field_signature = normalize_type_signature(
                    format!("{decl_type_text} {decl_text}").trim(),
                );
                if field_signature.is_empty() {
                    field_signature = normalize_type_signature(&node_text(node, state.source));
                }
                let field_id = match &scope {
                    Some(sc) => format!("{sc}::{var_name}@{}", state.rel_path),
                    None => format!("{var_name}@{}", state.rel_path),
                };
                state.fields.push(field_row(
                    &field_id,
                    &qualified_name(scope.as_deref(), &var_name),
                    &var_name,
                    scope.as_deref(),
                    &field_signature,
                    &state.rel_path,
                    start_line,
                    end_line,
                    &snippet,
                ));
                if !namespace_stack.is_empty() {
                    let ns_id = namespace_id(&namespace_stack.join("::"));
                    let mut properties = Map::new();
                    if has_extern {
                        properties.insert("storage".into(), json!("extern"));
                    }
                    state.relations.push(relation_row(
                        &ns_id, "Namespace", &field_id, "Field", "CONTAINS", properties,
                    ));
                }
                state.register_type_usage(&field_id, "Field", &field_signature);
            }
            continue;
        }

        if node.kind() == "field_declaration" {
            let scope_stack: Vec<String> = namespace_stack
                .iter()
                .chain(type_stack.iter())
                .cloned()
                .collect();
            let scope = extract_scope_stack(&scope_stack);
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let type_node = node.child_by_field_name("type");
            let type_text = type_node
                .map(|n| node_text(n, state.source).trim().to_string())
                .unwrap_or_default();
            let mut declarators = iter_field_declarators(node);
            if declarators.is_empty() {
                declarators = vec![node];
            }
            let mut seen_field_names: std::collections::HashSet<String> =
                std::collections::HashSet::new();
            let mut seen_method_keys: std::collections::HashSet<(String, i64)> =
                std::collections::HashSet::new();
            for declarator in declarators {
                if is_method_field_declarator(Some(declarator), state.source) {
                    let Some(method_name) = extract_function_name(Some(declarator), state.source)
                    else {
                        continue;
                    };
                    let arity = declarator_arity(Some(declarator));
                    if !seen_method_keys.insert((method_name.clone(), arity)) {
                        continue;
                    }
                    let identity = function_identity_from_declarator(
                        scope.as_deref(),
                        &method_name,
                        Some(declarator),
                        node,
                        state.source,
                        &state.rel_path,
                    );
                    let symbol_id = identity.logical_id.clone();
                    let qualified = qualified_name(scope.as_deref(), &method_name);
                    let mut func = function_row(
                        &symbol_id,
                        &qualified,
                        &method_name,
                        "declaration",
                        scope.as_deref(),
                        &state.rel_path,
                        node.start_byte() as i64,
                        node.end_byte() as i64,
                        start_line,
                        end_line,
                        arity,
                        &snippet,
                        "",
                        &identity,
                    );
                    // field_declaration methods: comment/summary/note đều rỗng.
                    func.insert("summary".into(), json!(""));
                    func.insert("note".into(), json!(""));
                    state.push_function_row(func);
                    if !type_stack.is_empty() {
                        let mut parts = namespace_stack.clone();
                        parts.extend(type_stack.iter().cloned());
                        let tid = type_id(None, &parts.join("::"));
                        state.relations.push(relation_row(
                            &tid, "Type", &symbol_id, "Function", "DECLARES", Map::new(),
                        ));
                    }
                    for param in iter_parameter_declarations(Some(declarator)) {
                        state.register_type_usage(
                            &symbol_id,
                            "Function",
                            &node_text(param, state.source),
                        );
                    }
                    continue;
                }
                let mut field_name = field_name_from_declarator(Some(declarator), state.source);
                if field_name.is_none() {
                    field_name = first_identifier(declarator, state.source);
                }
                let Some(field_name) = field_name.filter(|f| !f.is_empty()) else {
                    continue;
                };
                if !seen_field_names.insert(field_name.clone()) {
                    continue;
                }
                let decl_text = if declarator != node {
                    node_text(declarator, state.source).trim().to_string()
                } else {
                    String::new()
                };
                let mut field_signature = normalize_type_signature(
                    format!("{type_text} {decl_text}").trim(),
                );
                if field_signature.is_empty() {
                    field_signature = normalize_type_signature(&node_text(node, state.source));
                }
                let field_id = match &scope {
                    Some(sc) => format!("{sc}::{field_name}@{}", state.rel_path),
                    None => format!("{field_name}@{}", state.rel_path),
                };
                state.fields.push(field_row(
                    &field_id,
                    &qualified_name(scope.as_deref(), &field_name),
                    &field_name,
                    scope.as_deref(),
                    &field_signature,
                    &state.rel_path,
                    start_line,
                    end_line,
                    &snippet,
                ));
                if !type_stack.is_empty() {
                    let mut parts = namespace_stack.clone();
                    parts.extend(type_stack.iter().cloned());
                    let tid = type_id(None, &parts.join("::"));
                    state.relations.push(relation_row(
                        &tid, "Type", &field_id, "Field", "DECLARES", Map::new(),
                    ));
                }
                state.register_type_usage(&field_id, "Field", &field_signature);
            }
            continue;
        }

        if matches!(
            node.kind(),
            "type_definition" | "alias_declaration" | "type_alias_declaration"
        ) {
            let kind = if node.kind() == "type_definition" { "typedef" } else { "using" };
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let alias_name = first_identifier(node, state.source)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| anonymous_name("Alias", node));
            let scope_stack: Vec<String> = namespace_stack
                .iter()
                .chain(type_stack.iter())
                .cloned()
                .collect();
            let scope = extract_scope_stack(&scope_stack);
            let alias_id = format!(
                "alias::{}@{}",
                qualified_name(scope.as_deref(), &alias_name),
                state.rel_path
            );
            let target_name = extract_base_type(&node_text(node, state.source));
            state.aliases.push(alias_row(
                &alias_id,
                &qualified_name(scope.as_deref(), &alias_name),
                &alias_name,
                kind,
                target_name.as_deref(),
                &state.rel_path,
                start_line,
                end_line,
                &snippet,
            ));
            if let Some(target_name) = &target_name {
                let tid = type_id(None, target_name);
                if !state.type_registry.contains(&tid) {
                    state.types.push(type_row(
                        &tid,
                        target_name,
                        target_name.split("::").last().unwrap_or(target_name),
                        "external",
                        &state.rel_path,
                        0,
                        0,
                        target_name,
                        "",
                        "",
                        "",
                    ));
                    state.type_registry.insert(tid.clone());
                }
                let mut properties = Map::new();
                properties.insert("kind".into(), json!(kind));
                state.relations.push(relation_row(
                    &alias_id, "Alias", &tid, "Type", "ALIASES", properties,
                ));
            }
            continue;
        }

        if node.kind() == "namespace_alias_definition" {
            let (snippet, start_line, end_line) = node_snippet(node, state.source);
            let alias_name = first_identifier(node, state.source)
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| anonymous_name("NamespaceAlias", node));
            let scope_stack: Vec<String> = namespace_stack
                .iter()
                .chain(type_stack.iter())
                .cloned()
                .collect();
            let scope = extract_scope_stack(&scope_stack);
            let alias_id = format!(
                "alias::{}@{}",
                qualified_name(scope.as_deref(), &alias_name),
                state.rel_path
            );
            let text = node_text(node, state.source);
            let mut target_name: Option<String> = None;
            let mut parts = text.splitn(2, '=');
            let _ = parts.next();
            if let Some(rhs) = parts.next() {
                target_name = extract_type_name(rhs);
            }
            state.aliases.push(alias_row(
                &alias_id,
                &qualified_name(scope.as_deref(), &alias_name),
                &alias_name,
                "namespace_alias",
                target_name.as_deref(),
                &state.rel_path,
                start_line,
                end_line,
                &snippet,
            ));
            if let Some(target_name) = &target_name {
                let ns_id = namespace_id(target_name);
                if !state.namespace_registry.contains(&ns_id) {
                    state.namespaces.push(namespace_row(
                        &ns_id,
                        target_name,
                        target_name.split("::").last().unwrap_or(target_name),
                        &state.rel_path,
                        0,
                        0,
                        target_name,
                        "",
                    ));
                    state.namespace_registry.insert(ns_id.clone());
                }
                let mut properties = Map::new();
                properties.insert("kind".into(), json!("namespace_alias"));
                state.relations.push(relation_row(
                    &alias_id, "Alias", &ns_id, "Namespace", "ALIASES", properties,
                ));
            }
            continue;
        }

        // Default: push children (đảo thứ tự) với bản copy using-state.
        push_children_reversed(
            &mut work, node, namespace_stack, type_stack, using_namespaces, using_imports,
        );
    }
}

#[allow(clippy::too_many_arguments)]
fn push_children_reversed<'t>(
    work: &mut VecDeque<Frame<'t>>,
    node: Node<'t>,
    namespace_stack: Vec<String>,
    type_stack: Vec<String>,
    using_namespaces: Vec<String>,
    using_imports: BTreeMap<String, String>,
) {
    let children: Vec<Node<'t>> = children_of(node).collect();
    for child in children.into_iter().rev() {
        work.push_back(Frame {
            node: child,
            namespace_stack: namespace_stack.clone(),
            type_stack: type_stack.clone(),
            using_namespaces: using_namespaces.clone(),
            using_imports: using_imports.clone(),
        });
    }
}

fn capitalize(s: &str) -> String {
    let mut chars = s.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

fn namespace_row(
    symbol_id: &str,
    qualified: &str,
    name: &str,
    rel_path: &str,
    start_line: i64,
    end_line: i64,
    code: &str,
    comment: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("qualified_name".into(), json!(qualified));
    row.insert("name".into(), json!(name));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("code".into(), json!(code));
    row.insert("comment".into(), json!(comment));
    row.insert("summary".into(), json!(comment));
    row.insert("note".into(), json!(""));
    row
}

fn template_row(
    symbol_id: &str,
    name: &str,
    rel_path: &str,
    start_line: i64,
    end_line: i64,
    code: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("name".into(), json!(name));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("code".into(), json!(code));
    row
}

fn func_type_row(
    symbol_id: &str,
    type_signature: &str,
    rel_path: &str,
    start_line: i64,
    end_line: i64,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("type_signature".into(), json!(type_signature));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("code".into(), json!(type_signature));
    row
}

fn alias_row(
    symbol_id: &str,
    qualified: &str,
    name: &str,
    kind: &str,
    target_name: Option<&str>,
    rel_path: &str,
    start_line: i64,
    end_line: i64,
    code: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("symbol_id".into(), json!(symbol_id));
    row.insert("qualified_name".into(), json!(qualified));
    row.insert("name".into(), json!(name));
    row.insert("kind".into(), json!(kind));
    row.insert("target_name".into(), target_name.map(|v| json!(v)).unwrap_or(Value::Null));
    row.insert("file_path".into(), json!(rel_path));
    row.insert("start_line".into(), json!(start_line));
    row.insert("end_line".into(), json!(end_line));
    row.insert("code".into(), json!(code));
    row
}

// ── Parsers (module-level singleton như Python) ────────────────────────────

thread_local! {
    static CPP_PARSER: std::cell::RefCell<tree_sitter::Parser> = std::cell::RefCell::new({
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_cpp::LANGUAGE.into())
            .expect("cpp grammar");
        parser
    });
    static C_PARSER: std::cell::RefCell<tree_sitter::Parser> = std::cell::RefCell::new({
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_c::LANGUAGE.into())
            .expect("c grammar");
        parser
    });
}

fn parse_with_parser(is_cpp: bool, source: &[u8]) -> Option<tree_sitter::Tree> {
    if is_cpp {
        CPP_PARSER.with(|parser| parser.borrow_mut().parse(source, None))
    } else {
        C_PARSER.with(|parser| parser.borrow_mut().parse(source, None))
    }
}

/// Grammar version khớp `_grammar_version` Python (venv):
/// cpp → `str(abi_version)` = "14"; c → `str((0, 24, 2))` của semantic_version.
pub fn grammar_version(is_cpp: bool) -> String {
    if is_cpp {
        "14".to_string()
    } else {
        "(0, 24, 2)".to_string()
    }
}

/// Parser version khớp `_runtime_package_version("tree-sitter")` của venv.
/// (Artifact-only field — phân biệt được ghi trong report.)
pub fn parser_runtime_version() -> String {
    "0.26.0".to_string()
}

/// `_parse_file` — decode + parse. `.pc/.pcc` masking plane chưa port: parse
/// trực tiếp source bytes (divergence có chủ đích, xem report).
fn parse_file(path: &Path, is_cpp: bool) -> (tree_sitter::Tree, Vec<u8>, String, bool) {
    let decoded = cscan::read_legacy_text(path);
    let source_bytes = decoded.text.clone().into_bytes();
    let tree = parse_with_parser(is_cpp, &source_bytes).expect("parse tree");
    (tree, source_bytes, decoded.encoding, decoded.lossy)
}

/// `parse_c_family_file` — trả payload FilePayload.
#[allow(clippy::too_many_lines)]
pub fn parse_c_family_file(path: &Path, root: &Path, initial_is_cpp: bool) -> FilePayload {
    let (mut tree, mut source_bytes, mut source_encoding, mut source_lossy) =
        parse_file(path, initial_is_cpp);
    let mut selected_is_cpp = initial_is_cpp;
    let mut retry_attempted = false;
    let mut retry_selected = false;
    let mut retry_has_error: Option<bool> = None;
    let mut retry_error_nodes: Option<i64> = None;
    let mut retry_missing_nodes: Option<i64> = None;

    let initial_damage =
        collect_tree_sitter_damage(tree.root_node(), source_bytes.len());
    let mut selected_damage = initial_damage.clone();
    let mut selected_has_error = tree.root_node().has_error();
    let mut selected_error_nodes = initial_damage.error_count;
    let mut selected_missing_nodes = initial_damage.missing_count;

    let ext = splitext(&path.to_string_lossy().to_lowercase()).1;
    if ext == ".h" && (selected_has_error || selected_error_nodes > 0) {
        retry_attempted = true;
        let retry_is_cpp = !initial_is_cpp;
        let (retry_tree, retry_source_bytes, retry_source_encoding, retry_lossy) =
            parse_file(path, retry_is_cpp);
        let retry_damage =
            collect_tree_sitter_damage(retry_tree.root_node(), retry_source_bytes.len());
        retry_has_error = Some(retry_tree.root_node().has_error());
        retry_error_nodes = Some(retry_damage.error_count);
        retry_missing_nodes = Some(retry_damage.missing_count);
        let initial_candidate = (
            &initial_damage,
            &tree_selection_semantic_yield(tree.root_node()),
        );
        let retry_candidate = (
            &retry_damage,
            &tree_selection_semantic_yield(retry_tree.root_node()),
        );
        if candidate_is_strictly_better(retry_candidate, initial_candidate) {
            tree = retry_tree;
            source_bytes = retry_source_bytes;
            source_encoding = retry_source_encoding;
            source_lossy = retry_lossy;
            selected_is_cpp = retry_is_cpp;
            selected_has_error = retry_has_error.unwrap();
            selected_error_nodes = retry_error_nodes.unwrap();
            selected_missing_nodes = retry_missing_nodes.unwrap();
            selected_damage = retry_damage;
            retry_selected = true;
        }
    }
    let rel_path = rel_posix_path(root, path);
    let file_code = String::from_utf8_lossy(&source_bytes).to_string();
    let file_lines = file_code.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment(tree.root_node(), &source_bytes);
    let file_def = {
        let mut row = Map::new();
        row.insert("file_path".into(), json!(rel_path));
        row.insert("start_line".into(), json!(1));
        row.insert("end_line".into(), json!(file_lines));
        row.insert("code".into(), json!(file_code));
        row.insert("comment".into(), json!(file_comment));
        row.insert("summary".into(), json!(file_comment));
        row.insert("note".into(), json!(""));
        row
    };
    let file_includes = cscan::extract_includes(&file_code);
    let file_macros = cscan::extract_macros(&file_code);

    let mut state = WalkState {
        source: &source_bytes,
        rel_path: rel_path.clone(),
        functions: Vec::new(),
        calls: Vec::new(),
        types: Vec::new(),
        namespaces: Vec::new(),
        relations: Vec::new(),
        func_types: BTreeMap::new(),
        fields: Vec::new(),
        aliases: Vec::new(),
        templates: Vec::new(),
        type_registry: std::collections::HashSet::new(),
        namespace_registry: std::collections::HashSet::new(),
    };
    walk_tree(tree.root_node(), &mut state);

    let using_namespaces: Vec<String> = Vec::new();
    let using_imports = BTreeMap::new();

    let semantic_yield = SemanticYield {
        function_count: state.functions.len() as i64,
        type_count: state.types.len() as i64,
        declaration_count: (state.fields.len()
            + state.aliases.len()
            + state.templates.len()
            + state.func_types.len()) as i64,
        stable_scope_count: {
            let qualified = |row: &Row| -> bool {
                row.get("qualified_name")
                    .and_then(Value::as_str)
                    .map(|q| !q.is_empty())
                    .unwrap_or(false)
            };
            state
                .functions
                .iter()
                .chain(state.types.iter())
                .chain(state.namespaces.iter())
                .filter(|row| qualified(row))
                .count() as i64
        },
        call_count: state.calls.len() as i64,
        include_count: file_includes.len() as i64,
    };

    let mut retry_stages: Vec<&'static str> = Vec::new();
    if !matches!(
        source_encoding.to_lowercase().replace('_', "-").as_str(),
        "utf-8" | "utf-8-sig"
    ) {
        retry_stages.push("legacy_decode");
    }
    if retry_attempted {
        retry_stages.push("alternate_grammar");
    }
    if matches!(ext.as_str(), ".pc" | ".pcc") {
        retry_stages.push("dialect_masking");
    }
    let context = ParseContext {
        backend: "tree_sitter".into(),
        parser_language: if selected_is_cpp { "cpp".into() } else { "c".into() },
        parser_version: parser_runtime_version(),
        grammar_version: grammar_version(selected_is_cpp),
        source_encoding: source_encoding.clone(),
        lossy_decode: source_lossy,
        compile_context_available: false,
        compile_context_fingerprint: String::new(),
        masking_fingerprint: String::new(),
        recovery_policy_version: crate::quality::RECOVERY_POLICY_VERSION.into(),
    };
    let quality = build_quality_record_for(&rel_path, &source_bytes, &selected_damage, &semantic_yield, &context, retry_stages, retry_selected);

    let mut parse_meta = Map::new();
    parse_meta.insert(
        "parser_language".into(),
        json!(if selected_is_cpp { "cpp" } else { "c" }),
    );
    parse_meta.insert(
        "parser_language_initial".into(),
        json!(if initial_is_cpp { "cpp" } else { "c" }),
    );
    parse_meta.insert("header_retry_attempted".into(), json!(retry_attempted));
    parse_meta.insert("header_retry_selected".into(), json!(retry_selected));
    parse_meta.insert("has_error".into(), json!(selected_has_error));
    parse_meta.insert("error_nodes".into(), json!(selected_error_nodes));
    parse_meta.insert("missing_nodes".into(), json!(selected_missing_nodes));
    parse_meta.insert("error_nodes_initial".into(), json!(initial_damage.error_count));
    parse_meta.insert(
        "missing_nodes_initial".into(),
        json!(initial_damage.missing_count),
    );
    parse_meta.insert(
        "header_retry_error_nodes".into(),
        retry_error_nodes.map(|v| json!(v)).unwrap_or(Value::Null),
    );
    parse_meta.insert(
        "header_retry_missing_nodes".into(),
        retry_missing_nodes.map(|v| json!(v)).unwrap_or(Value::Null),
    );
    parse_meta.insert(
        "header_retry_has_error".into(),
        retry_has_error.map(|v| json!(v)).unwrap_or(Value::Null),
    );
    parse_meta.insert("source_encoding".into(), json!(source_encoding));
    parse_meta.insert("lossy_decode".into(), json!(source_lossy));
    parse_meta.insert("quality".into(), Value::Object(quality.to_dict()));
    parse_meta.insert("quality_tier".into(), json!(quality.tier));
    parse_meta.insert("parser_backend".into(), json!(context.backend));
    parse_meta.insert(
        "context_fingerprint".into(),
        json!(quality.context_fingerprint),
    );
    parse_meta.insert(
        "recovery_policy_version".into(),
        json!(context.recovery_policy_version),
    );
    parse_meta.insert(
        "damaged_span_ratio".into(),
        json!(quality.damage.damaged_span_ratio),
    );
    parse_meta.insert(
        "critical_structural_damage".into(),
        json!(quality.damage.critical_structural_damage),
    );
    parse_meta.insert("candidate_outcome".into(), json!(quality.candidate_outcome));
    parse_meta.insert("selected_candidate".into(), json!(quality.selected_candidate));
    parse_meta.insert("selection_reason".into(), json!(quality.selection_reason));

    let function_types: Vec<Row> = state.func_types.values().cloned().collect();
    FilePayload {
        functions: state.functions,
        calls: state.calls,
        types: state.types,
        namespaces: state.namespaces,
        relations: state.relations,
        function_types,
        fields: state.fields,
        aliases: state.aliases,
        templates: state.templates,
        resources: Vec::new(),
        resource_elements: Vec::new(),
        proc_nodes: Vec::new(),
        file_def: Some(file_def),
        using_namespaces,
        using_imports,
        includes: file_includes,
        macros: file_macros.into_iter().collect(),
        parse_meta,
    }
}

#[allow(clippy::too_many_arguments)]
fn build_quality_record_for(
    rel_path: &str,
    source: &[u8],
    damage: &DamageSummary,
    semantic_yield: &SemanticYield,
    context: &ParseContext,
    retry_stages: Vec<&'static str>,
    retry_selected: bool,
) -> QualityRecord {
    let source_hash = source_fingerprint(source);
    QualityRecord {
        file_path: rel_path.to_string(),
        source_fingerprint: source_hash.clone(),
        context_fingerprint: context_fingerprint(context, &source_hash),
        tier: classify_quality(damage, semantic_yield, context.lossy_decode),
        damage: damage.clone(),
        semantic_yield: semantic_yield.clone(),
        context: context.clone(),
        retry_stages,
        candidate_outcome: if retry_selected { "selected" } else { "not_attempted" },
        selected_candidate: if retry_selected {
            "alternate_grammar".to_string()
        } else {
            "baseline".to_string()
        },
        selection_reason: if retry_selected {
            "lower_structural_damage".to_string()
        } else {
            "first_pass".to_string()
        },
    }
}

fn rel_posix_path(root: &Path, path: &Path) -> String {
    // os.path.relpath — lexical với "..".
    let rel = relpath(&path.to_string_lossy(), &root.to_string_lossy());
    rel.replace('\\', "/")
}

/// `_load_or_parse_payload` (không cache) — normalize + defaults khớp
/// `normalize_cached_payload` của Python.
pub fn load_or_parse_payload(path: &Path, root: &Path, is_cpp: bool) -> FilePayload {
    let mut payload = parse_c_family_file(path, root, is_cpp);
    // normalize: defaults cho file_def + các collection (bản parse trực tiếp
    // đã đủ field; đảm bảo các key khớp normalize_cached_payload).
    if let Some(file_def) = &mut payload.file_def {
        push_text_defaults(file_def);
    }
    for row in payload
        .functions
        .iter_mut()
        .chain(payload.namespaces.iter_mut())
        .chain(payload.types.iter_mut())
        .chain(payload.fields.iter_mut())
        .chain(payload.aliases.iter_mut())
        .chain(payload.templates.iter_mut())
    {
        push_text_defaults(row);
    }
    // calls sort theo _call_payload_sort_key (stable).
    payload.calls.sort_by(compare_call_rows);
    payload
}

pub(crate) fn compare_call_rows(a: &Row, b: &Row) -> std::cmp::Ordering {
    fn start(row: &Row) -> i64 {
        row.get("call_start_byte").and_then(Value::as_i64).unwrap_or(0)
    }
    fn line(row: &Row) -> i64 {
        row.get("call_line").and_then(Value::as_i64).unwrap_or(0)
    }
    fn column(row: &Row) -> i64 {
        row.get("call_column").and_then(Value::as_i64).unwrap_or(0)
    }
    fn caller(row: &Row) -> String {
        row.get("caller_id").and_then(Value::as_str).unwrap_or("").to_string()
    }
    fn callee(row: &Row) -> String {
        row.get("callee_name").and_then(Value::as_str).unwrap_or("").to_string()
    }
    start(a)
        .cmp(&start(b))
        .then(line(a).cmp(&line(b)))
        .then(column(a).cmp(&column(b)))
        .then(caller(a).cmp(&caller(b)))
        .then(callee(a).cmp(&callee(b)))
}

/// `_cplus_api_visibility` — (visibility, is_public_api, export_evidence).
pub fn cplus_api_visibility(code: &str, file_path: &str) -> (&'static str, bool, &'static str) {
    let header = code.split('{').next().unwrap_or("");
    let patterns = [
        r"__declspec\s*\(\s*dllexport\s*\)",
        r#"__attribute__\s*\(\([^)]*visibility\s*\(\s*["']default["']\s*\)"#,
        r#"\bextern\s+"C"\b"#,
    ];
    for pattern in patterns {
        if let Ok(re) = regex::Regex::new(pattern)
            && re.is_match(header) {
                return ("exported", true, "explicit export/linkage attribute");
            }
    }
    let ext = splitext(file_path.to_lowercase().as_str()).1;
    if matches!(ext.as_str(), ".h" | ".hh" | ".hpp" | ".hxx") {
        return ("inferred", false, "public-header location heuristic");
    }
    ("unknown", false, "no explicit export evidence")
}

/// `type_id` pub cho analyzer (Type id = qualified name).
pub fn type_id_for(scope: Option<&str>, name: &str) -> String {
    type_id(scope, name)
}

/// `normalize_call_name` pub cho resolve_callee_id.
pub fn normalize_call_name_pub(text: &str) -> String {
    normalize_call_name(text)
}

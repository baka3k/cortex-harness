//! Port `tools/ts/agents/parser_agent.py` — ts/tsx parser factory + AST utils.

use tree_sitter::{Node, Parser, Tree};

pub const JSX_NODE_TYPES: [&str; 5] = [
    "jsx_element",
    "jsx_fragment",
    "jsx_text",
    "jsx_opening_element",
    "jsx_self_closing_element",
];

pub fn get_parser(is_tsx: bool) -> Parser {
    let mut parser = Parser::new();
    let language = if is_tsx {
        tree_sitter_typescript::LANGUAGE_TSX
    } else {
        tree_sitter_typescript::LANGUAGE_TYPESCRIPT
    };
    parser
        .set_language(&language.into())
        .expect("ts grammar load");
    parser
}

pub fn parse_file(path: &std::path::Path) -> Result<(Tree, Vec<u8>), String> {
    let is_tsx = path.to_string_lossy().ends_with(".tsx");
    let mut parser = get_parser(is_tsx);
    let source_bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let tree = parser
        .parse(&source_bytes, None)
        .ok_or_else(|| format!("parse failed: {}", path.display()))?;
    Ok((tree, source_bytes))
}

/// `_tree_error_stats` — đếm ERROR nodes trừ pseudo-error '&' trong JSX text.
pub fn tree_error_stats(root: Node, source: &[u8]) -> (bool, i64) {
    let mut real_errors = 0i64;
    for node in cortex_analyzer_framework::ts::find_nodes_by_type(root, "ERROR") {
        if !is_benign_jsx_entity_error(node, source) {
            real_errors += 1;
        }
    }
    (real_errors > 0, real_errors)
}

fn is_benign_jsx_entity_error(node: Node, source: &[u8]) -> bool {
    let text = cortex_analyzer_framework::ts::decode_ignore(&source[node.start_byte()..node.end_byte()]);
    if !text.starts_with('&') {
        return false;
    }
    let mut parent = node.parent();
    while let Some(p) = parent {
        if JSX_NODE_TYPES.contains(&p.kind()) {
            return true;
        }
        parent = p.parent();
    }
    false
}

pub use cortex_analyzer_framework::ts::{
    decode_ignore as node_decode, extract_file_comment, extract_leading_comment, find_nodes_by_type,
    node_snippet,
};

/// `_node_text`.
pub fn node_text(node: Node, source: &[u8]) -> String {
    cortex_analyzer_framework::ts::node_text(node, source)
}

/// `_first_identifier` — thêm property_identifier/type_identifier/
/// namespace_identifier so với bản python_analyzer.
pub fn first_identifier(node: Node, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "identifier" | "property_identifier" | "type_identifier" | "namespace_identifier"
    ) {
        return Some(node_text(node, source));
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if let Some(result) = first_identifier(child, source) {
            return Some(result);
        }
    }
    None
}

pub fn extract_name_field(node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(node, source)
}

/// `_normalize_call_name` — bracket-subscript fragment port thủ công
/// (Python dùng backreference \1 mà Rust regex không hỗ trợ).
pub fn normalize_call_name(text: &str) -> String {
    let re_brackets = regex::Regex::new(r"<[^<>]*>").unwrap();
    let cleaned = re_brackets.replace_all(text, "").to_string();
    let cleaned = cleaned.replace("this.", "").replace("super.", "");
    let cleaned = cleaned.replace("?.", ".").replace("::", ".");
    let cleaned = cleaned.trim().to_string();
    // [  'name'  ]$ — quote phải khớp nhau
    if let Some(name) = trailing_bracket_name(&cleaned) {
        return name;
    }
    if let Some(pos) = cleaned.rfind('.') {
        return cleaned[pos + 1..].trim().to_string();
    }
    cleaned.trim().to_string()
}

fn trailing_bracket_name(cleaned: &str) -> Option<String> {
    let trimmed_end = cleaned.trim_end();
    if !trimmed_end.ends_with(']') {
        return None;
    }
    let inner = &trimmed_end[..trimmed_end.len() - 1].trim_end();
    if !inner.ends_with(['\'', '"']) {
        return None;
    }
    let quote = inner.chars().last().unwrap();
    let without_quote = &inner[..inner.len() - 1];
    // tìm opening [ sau khoảng whitespace
    let open_pos = without_quote.rfind('[')?;
    let name_start = without_quote[open_pos + 1..].trim_start();
    if !name_start.starts_with(quote) {
        return None;
    }
    let name_body = name_start[1..].trim_end();
    let name_body = name_body.strip_suffix(quote)?;
    if name_body.contains(quote) {
        return None;
    }
    // toàn bộ phần giữa [ và quote phải chỉ là whitespace
    if !without_quote[open_pos + 1..].trim_start().starts_with(quote) {
        return None;
    }
    Some(name_body.to_string())
}

pub fn extract_scope_stack(stack: &[String]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(stack.join("::"))
    }
}

pub fn symbol_id(scope: Option<&str>, name: &str, arity: i64, rel_path: &str) -> String {
    let qualified = match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    };
    format!("{qualified}/{arity}@{rel_path}")
}

pub fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    }
}

pub fn type_id(qualified: &str) -> String {
    qualified.to_string()
}

pub fn namespace_id(name: &str) -> String {
    format!("namespace::{name}")
}

pub fn anonymous_name(prefix: &str, node: Node) -> String {
    format!(
        "Anonymous{prefix}@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

pub fn count_parameters(node: Node) -> i64 {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| node.child_by_field_name("parameter_list"));
    let Some(params) = params else { return 0 };
    params
        .children(&mut params.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

/// `_extract_return_type` — text sau dấu ':'.
pub fn extract_return_type(node: Node, source: &[u8]) -> String {
    let Some(ret) = node.child_by_field_name("return_type") else {
        return String::new();
    };
    node_text(ret, source).trim().trim_start_matches(':').trim().to_string()
}

/// `_extract_param_types` — type annotation từng param (rỗng nếu không có).
pub fn extract_param_types(node: Node, source: &[u8]) -> Vec<String> {
    let Some(params) = node
        .child_by_field_name("parameters")
        .or_else(|| node.child_by_field_name("parameter_list"))
    else {
        return Vec::new();
    };
    let mut result = Vec::new();
    for child in params.children(&mut params.walk()) {
        if !child.is_named() || child.kind() == "comment" {
            continue;
        }
        if let Some(type_node) = child.child_by_field_name("type") {
            result.push(
                node_text(type_node, source)
                    .trim()
                    .trim_start_matches(':')
                    .trim()
                    .to_string(),
            );
        } else {
            result.push(String::new());
        }
    }
    result
}

pub fn count_arguments(node: Node) -> i64 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| node.child_by_field_name("argument_list"));
    let Some(args) = args else { return 0 };
    args.children(&mut args.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

/// `_iter_calls` — call_expression + new_expression.
pub fn iter_calls(func_node: Node) -> Vec<Node> {
    let mut calls = find_nodes_by_type(func_node, "call_expression");
    calls.extend(find_nodes_by_type(func_node, "new_expression"));
    calls
}

/// `_extract_call_name`.
pub fn extract_call_name(call_node: Node, source: &[u8]) -> Option<String> {
    let field = match call_node.kind() {
        "call_expression" => Some("function"),
        "new_expression" => Some("constructor"),
        _ => None,
    };
    if let Some(field) = field
        && let Some(expr) = call_node.child_by_field_name(field) {
            return Some(normalize_call_name(node_text(expr, source).trim()));
        }
    let mut text = node_text(call_node, source).trim().to_string();
    if let Some(stripped) = text.strip_prefix("new ") {
        text = stripped.to_string();
    }
    let text = text.split('(').next().unwrap_or("").trim().to_string();
    Some(normalize_call_name(&text))
}

/// `_collect_imports` — import_statement + import_require_clause.
pub fn collect_imports(root: Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_statement") {
        let text = normalize_ws_import(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "import_require_clause") {
        let text = normalize_ws_import(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    imports
}

pub fn collect_exports(root: Node, source: &[u8]) -> Vec<String> {
    let mut exports = Vec::new();
    for node in find_nodes_by_type(root, "export_statement") {
        let text = normalize_ws_import(&node_text(node, source));
        if !text.is_empty() {
            exports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "export_default_declaration") {
        let text = normalize_ws_import(&node_text(node, source));
        if !text.is_empty() {
            exports.push(text);
        }
    }
    exports
}

fn normalize_ws_import(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `_jsx_name`.
pub fn jsx_name(node: Node, source: &[u8]) -> Option<String> {
    let mut name_node = node.child_by_field_name("name");
    if name_node.is_none() {
        for child in node.children(&mut node.walk()) {
            if matches!(
                child.kind(),
                "jsx_identifier" | "jsx_member_expression" | "jsx_namespaced_name"
            ) {
                name_node = Some(child);
                break;
            }
        }
    }
    name_node.map(|n| node_text(n, source))
}

/// `_collect_jsx_tags` — (lowercase_tags, PascalCase_components) sorted.
pub fn collect_jsx_tags(root: Node, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut tags = std::collections::BTreeSet::new();
    let mut components = std::collections::BTreeSet::new();
    for kind in ["jsx_opening_element", "jsx_self_closing_element"] {
        for node in find_nodes_by_type(root, kind) {
            let Some(name) = jsx_name(node, source) else {
                continue;
            };
            if name.is_empty() {
                continue;
            }
            if name.chars().next().unwrap().is_lowercase() {
                tags.insert(name);
            } else {
                components.insert(name);
            }
        }
    }
    (tags.into_iter().collect(), components.into_iter().collect())
}

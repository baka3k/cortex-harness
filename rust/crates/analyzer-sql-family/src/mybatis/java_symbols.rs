//! Mini Java symbol extractor — port đúng phần `parse_java_file` của
//! `tools/java/java_analyzer.py` mà mybatis `_java_symbol_maps` cần:
//! class_ids (qualified → symbol_id) + method_ids
//! ((class_path, name, arity, start_line) → symbol_id) + fallback
//! ((class_path, name, arity) → symbol_id).
//!
//! `start_line` là dòng snippet (kéo qua leading `line_comment`/`block_comment`
//! như `_node_snippet` của java_analyzer.py — KHÔNG giống kind `comment`).

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use cortex_analyzer_framework::ts::decode_ignore;

pub struct JavaSymbolMaps {
    /// class_ids: qualified (pkg.ClassPath) → symbol_id.
    pub class_ids: HashMap<String, String>,
    /// method_ids: (class_path, name, arity, start_line) → symbol_id.
    pub method_ids: HashMap<(String, String, i64, i64), String>,
    /// method_ids_fallback: (class_path, name, arity) → symbol_id.
    pub method_ids_fallback: HashMap<(String, String, i64), String>,
}

fn node_text(node: Node<'_>, source: &[u8]) -> String {
    decode_ignore(&source[node.start_byte()..node.end_byte()])
}

fn first_identifier(node: Option<Node<'_>>, source: &[u8]) -> Option<String> {
    let node = node?;
    if node.kind() == "identifier" || node.kind() == "attribute" {
        return Some(node_text(node, source));
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if let Some(result) = first_identifier(Some(child), source) {
            return Some(result);
        }
    }
    None
}

fn class_kind(node: Node<'_>) -> Option<&'static str> {
    match node.kind() {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "enum_declaration" => Some("enum"),
        "record_declaration" => Some("record"),
        _ => None,
    }
}

fn extract_class_name(node: Node<'_>, source: &[u8]) -> Option<String> {
    let name_node = node.child_by_field_name("name");
    if let Some(name_node) = name_node {
        return Some(node_text(name_node, source));
    }
    first_identifier(Some(node), source)
}

fn extract_method_name(node: Node<'_>, source: &[u8]) -> Option<String> {
    let name_node = node.child_by_field_name("name");
    if let Some(name_node) = name_node {
        return Some(node_text(name_node, source));
    }
    first_identifier(Some(node), source)
}

fn count_parameters(method_node: Node<'_>) -> i64 {
    let Some(param_list) = method_node.child_by_field_name("parameters") else {
        return 0;
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "formal_parameter")
        .count() as i64
}

/// `_node_snippet` của java_analyzer.py — leading line_comment/block_comment.
fn snippet_start_byte(node: Node<'_>) -> usize {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() == "line_comment" || p.kind() == "block_comment" {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    start_byte
}

fn line_from_byte(source: &[u8], byte_index: usize) -> i64 {
    source[..byte_index].iter().filter(|&&b| b == b'\n').count() as i64 + 1
}

fn anonymous_class_name(node: Node<'_>) -> String {
    format!(
        "Anonymous@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

/// Port đúng các phần của `parse_java_file` mà `_java_symbol_maps` đọc.
/// `rel_path` là `os.path.relpath(path, root)` posix.
pub fn java_symbol_maps(
    source_bytes: &[u8],
    rel_path: &str,
) -> JavaSymbolMaps {
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_java::LANGUAGE.into())
        .expect("load java parser");
    let Some(tree) = parser.parse(source_bytes, None) else {
        return JavaSymbolMaps {
            class_ids: HashMap::new(),
            method_ids: HashMap::new(),
            method_ids_fallback: HashMap::new(),
        };
    };
    let root = tree.root_node();
    let package_name = package_name_of(root, source_bytes);

    let mut class_ids: HashMap<String, String> = HashMap::new();
    let mut method_ids: HashMap<(String, String, i64, i64), String> = HashMap::new();
    let mut method_ids_fallback: HashMap<(String, String, i64), String> = HashMap::new();

    // Duyệt 1 lần (stack DFS) với class_stack — kết hợp _iter_type_decls và
    // _iter_methods (thứ tự không quan trọng vì kết quả là maps).
    let mut stack: Vec<(Node<'_>, Vec<String>, bool)> = vec![(root, Vec::new(), false)];
    while let Some((node, class_stack, in_class_body)) = stack.pop() {
        let kind = class_kind(node);
        if kind.is_some() || (in_class_body && node.kind() == "class_body") {
            // class_body cho anonymous: _iter_type_decls yield với path mới.
            let class_name = if node.kind() == "class_body" {
                None
            } else {
                extract_class_name(node, source_bytes)
            };
            let mut next_stack = class_stack.clone();
            match class_name {
                Some(name) => {
                    next_stack.push(name);
                    let class_path = next_stack.join(".");
                    let qualified = join_parts(package_name.as_deref(), &class_path);
                    class_ids.insert(qualified.clone(), qualified);
                }
                None => {
                    // class_body node: con của object_creation_expression —
                    // anonymous name lấy từ node cha.
                    // (đã push tại cha với anonymous name — nhánh này chỉ cho
                    // root class_body không hợp lệ; bỏ qua.)
                }
            }
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                stack.push((child, next_stack.clone(), false));
            }
            continue;
        }
        if node.kind() == "object_creation_expression" {
            let mut class_body = None;
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if child.kind() == "class_body" {
                    class_body = Some(child);
                    break;
                }
            }
            if let Some(class_body) = class_body {
                let anonymous_name = anonymous_class_name(node);
                let mut next_stack = class_stack.clone();
                next_stack.push(anonymous_name);
                let class_path = next_stack.join(".");
                let qualified = join_parts(package_name.as_deref(), &class_path);
                class_ids.insert(qualified.clone(), qualified);
                stack.push((class_body, next_stack, true));
                continue;
            }
        }
        if node.kind() == "method_declaration" || node.kind() == "constructor_declaration" {
            let active_class = if class_stack.is_empty() {
                None
            } else {
                Some(class_stack.join("."))
            };
            if let Some(method_name) = extract_method_name(node, source_bytes) {
                let arity = count_parameters(node);
                let start_line = line_from_byte(source_bytes, snippet_start_byte(node));
                let qualified = join_parts3(
                    package_name.as_deref(),
                    active_class.as_deref(),
                    &method_name,
                );
                let symbol = format!("{qualified}/{arity}@{rel_path}");
                if let Some(class_path) = &active_class {
                    method_ids.insert(
                        (class_path.clone(), method_name.clone(), arity, start_line),
                        symbol.clone(),
                    );
                    method_ids_fallback
                        .entry((class_path.clone(), method_name, arity))
                        .or_insert(symbol);
                }
            }
        }
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            stack.push((child, class_stack.clone(), false));
        }
    }

    JavaSymbolMaps {
        class_ids,
        method_ids,
        method_ids_fallback,
    }
}

fn join_parts(package: Option<&str>, class_path: &str) -> String {
    match package {
        Some(pkg) if !pkg.is_empty() => format!("{pkg}.{class_path}"),
        _ => class_path.to_string(),
    }
}

fn join_parts3(package: Option<&str>, class_path: Option<&str>, name: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    if let Some(pkg) = package.filter(|pkg| !pkg.is_empty()) {
        parts.push(pkg);
    }
    if let Some(class_path) = class_path.filter(|path| !path.is_empty()) {
        parts.push(class_path);
    }
    parts.push(name);
    parts.join(".")
}

/// `_package_name` (java_analyzer.py) — text của package_declaration đầu.
pub fn package_name_of(root: Node<'_>, source: &[u8]) -> Option<String> {
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "package_declaration" {
            let text = node_text(child, source);
            let stripped = text
                .strip_prefix("package")
                .unwrap_or(&text)
                .trim()
                .trim_end_matches(';')
                .trim();
            return Some(stripped.to_string());
        }
    }
    None
}

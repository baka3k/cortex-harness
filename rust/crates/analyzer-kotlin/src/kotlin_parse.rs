//! Port phần parse của `tools/kotlin/kotlin_analyzer.py`: tree-sitter walk
//! (`_iter_type_decls` / `_iter_functions` — LIFO stack, con cuối pop trước),
//! FunctionDef/ClassDef/FileDef/PackageDef, visibility default-public của
//! Kotlin, super types (delegation_specifiers), function types (`->` params),
//! calls (call_expression + normalize_callee), lambda_literal + CONTAINS.

use serde_json::{Map, Value};
use std::sync::OnceLock;

use cortex_analyzer_framework::scan::rel_posix;
use cortex_analyzer_framework::ts::{decode_ignore, find_nodes_by_type, line_from_byte, node_text};

pub type Row = Map<String, Value>;

// ── Data defs (asdict shape khớp Python payload) ────────────────────────────

#[derive(Debug, Clone)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub class_name: Option<String>,
    pub package_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub arity: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub visibility: String,
    pub is_public_api: bool,
    pub visibility_source: String,
    pub export_evidence: String,
    pub signature: String,
}

#[derive(Debug, Clone)]
pub struct PackageDef {
    pub name: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct FileDef {
    pub file_path: String,
    pub package_name: Option<String>,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct ClassDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub package_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub visibility: String,
    pub is_public_api: bool,
    pub visibility_source: String,
    pub export_evidence: String,
    pub signature: String,
}

#[derive(Debug, Clone)]
pub struct FunctionTypeDef {
    pub symbol_id: String,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
}

#[derive(Debug, Clone)]
pub struct RelationEdge {
    pub source_id: String,
    /// Labels chỉ tồn tại trên dataclass Python (không ghi vào all_relations) —
    /// giữ cho khớp shape.
    #[allow(dead_code)]
    pub source_label: String,
    pub target_id: String,
    #[allow(dead_code)]
    pub target_label: String,
    pub rel_type: String,
    pub properties: Row,
}

#[derive(Debug, Clone)]
pub struct TypeEdge {
    pub source_id: String,
    pub source_package: Option<String>,
    pub target_name: String,
    pub rel_type: String,
}

#[derive(Debug, Clone)]
pub struct CallEdge {
    pub caller_id: String,
    /// caller_file chỉ dùng phía Python payload (asdict) — giữ cho khớp shape.
    #[allow(dead_code)]
    pub caller_file: String,
    pub caller_package: Option<String>,
    pub caller_class: Option<String>,
    pub imports: Vec<String>,
    pub callee_name: String,
}

#[derive(Debug, Clone, Default)]
pub struct FilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub classes: Vec<ClassDef>,
    pub type_edges: Vec<TypeEdge>,
    pub function_types: Vec<FunctionTypeDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: Option<FileDef>,
    pub package_def: Option<PackageDef>,
    pub has_error: bool,
    pub error_nodes: i64,
}

// ── Regex singletons ────────────────────────────────────────────────────────

fn generics_re() -> &'static regex::Regex {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"<.*?>").expect("generics re"))
}

fn type_name_re() -> &'static regex::Regex {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"[A-Za-z_][A-Za-z0-9_\.]*").expect("type name re"))
}

// ── Helpers (port từng hàm `_` của kotlin_analyzer.py) ──────────────────────

/// `_kotlin_api_visibility` — default-public rule; kiểm tra private →
/// internal → protected → public theo thứ tự, match đầu tiên thắng.
pub fn kotlin_api_visibility(snippet: &str) -> (String, bool, String) {
    let header = snippet.split('{').next().unwrap_or("");
    let find_word = |word: &str| -> bool {
        let mut regex = String::with_capacity(word.len() + 5);
        regex.push_str(r"\b");
        regex.push_str(word);
        regex.push_str(r"\b");
        regex::Regex::new(&regex)
            .map(|re| re.is_match(header))
            .unwrap_or(false)
    };
    for visibility in ["private", "internal", "protected", "public"] {
        if find_word(visibility) {
            return (
                visibility.to_string(),
                visibility == "public",
                format!("explicit {visibility}"),
            );
        }
    }
    (
        "public".to_string(),
        true,
        "default public by Kotlin language rule".to_string(),
    )
}

/// `_source_signature` — header (trước `{` đầu) chuẩn hoá whitespace, cắt 500.
pub fn source_signature(snippet: &str) -> String {
    let header = snippet.split('{').next().unwrap_or("");
    let collapsed = cortex_analyzer_framework::ts::normalize_ws(header);
    collapsed.chars().take(500).collect()
}

/// `_build_note` — Summary > Comment > Code, join "\n\n".
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

/// `_extract_identifiers` — re.split(r"[^A-Za-z0-9_]+", text) không rỗng.
pub fn extract_identifiers(text: &str) -> Vec<String> {
    let mut parts: Vec<String> = Vec::new();
    let mut current = String::new();
    for c in text.chars() {
        if c.is_ascii_alphanumeric() || c == '_' {
            current.push(c);
        } else if !current.is_empty() {
            parts.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        parts.push(current);
    }
    parts
}

fn is_kotlin_comment(kind: &str) -> bool {
    kind == "line_comment" || kind == "block_comment" || kind == "multiline_comment"
}

/// `_node_snippet` — text tính từ sau chuỗi leading comments, kèm start/end line.
pub fn node_snippet(node: tree_sitter::Node, source: &[u8]) -> (String, usize, usize) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if is_kotlin_comment(p.kind()) {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = decode_ignore(&source[start_byte..node.end_byte()]);
    let start_line = line_from_byte(source, start_byte);
    let end_line = node.end_position().row + 1;
    (snippet, start_line, end_line)
}

/// `_extract_leading_comment` — chuỗi comment liền trước node (reversed join).
pub fn extract_leading_comment(node: tree_sitter::Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if is_kotlin_comment(p.kind()) {
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

/// `_extract_file_comment` — comment đầu file tới named node đầu tiên.
fn extract_file_comment(root: tree_sitter::Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for child in root.children(&mut root.walk()) {
        if is_kotlin_comment(child.kind()) {
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

/// `_normalize_type_signature` — re.sub(r"\s+", " ", text.strip()).
fn normalize_type_signature(text: &str) -> String {
    cortex_analyzer_framework::ts::normalize_ws(text)
}

/// `_first_identifier` — simple_identifier/identifier/type_identifier đầu tiên
/// (depth-first, theo thứ tự con).
fn first_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "simple_identifier" | "identifier" | "type_identifier"
    ) {
        return Some(node_text(node, source));
    }
    for child in node.children(&mut node.walk()) {
        if let Some(result) = first_identifier(child, source)
            && !result.is_empty()
        {
            return Some(result);
        }
    }
    None
}

/// `_collect_package_info` — package_header đầu tiên.
#[allow(clippy::type_complexity)]
fn collect_package_info(
    root: tree_sitter::Node,
    source: &[u8],
) -> (Option<String>, i64, i64, String, String) {
    for node in find_nodes_by_type(root, "package_header") {
        let text = node_text(node, source);
        let identifiers: Vec<String> = extract_identifiers(&text)
            .into_iter()
            .filter(|token| token != "package")
            .collect();
        if !identifiers.is_empty() {
            let (snippet, start_line, end_line) = node_snippet(node, source);
            let comment = extract_leading_comment(node, source);
            return (
                Some(identifiers.join(".")),
                start_line as i64,
                end_line as i64,
                snippet,
                comment,
            );
        }
    }
    (None, 0, 0, String::new(), String::new())
}

/// `_collect_imports` — import_header, bỏ token "import"/"as".
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_header") {
        let text = node_text(node, source);
        let identifiers: Vec<String> = extract_identifiers(&text)
            .into_iter()
            .filter(|token| token != "import" && token != "as")
            .collect();
        if !identifiers.is_empty() {
            imports.push(identifiers.join("."));
        }
    }
    imports
}

/// `_count_parameters` — đếm con `parameter` của field "parameters".
fn count_parameters(function_node: tree_sitter::Node) -> i64 {
    let Some(param_list) = function_node.child_by_field_name("parameters") else {
        return 0;
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "parameter")
        .count() as i64
}

/// `_iter_function_parameters` — con `parameter` của field "parameters".
fn iter_function_parameters(function_node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let Some(param_list) = function_node.child_by_field_name("parameters") else {
        return Vec::new();
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "parameter")
        .collect()
}

/// `_extract_function_name` — name field, fallback first identifier.
fn extract_function_name(function_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = function_node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(function_node, source)
}

/// `_extract_class_name` — name field, fallback first identifier.
fn extract_class_name(class_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = class_node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(class_node, source)
}

/// `_strip_outer_call_args` — nếu text kết thúc ")" thì cắt tại "(" khớp depth.
fn strip_outer_call_args(text: &str) -> String {
    let raw = text.trim();
    if !raw.ends_with(')') {
        return raw.to_string();
    }
    let bytes = raw.as_bytes();
    let mut depth = 0i64;
    for idx in (0..bytes.len()).rev() {
        match bytes[idx] {
            b')' => depth += 1,
            b'(' => {
                depth -= 1;
                if depth == 0 {
                    return raw[..idx].trim().to_string();
                }
            }
            _ => {}
        }
    }
    raw.to_string()
}

/// `_normalize_callee` — cắt call args, `?.`/`::`→`.`, bỏ `<...>`, strip " .",
/// receiver chain lồng nhau → segment cuối.
fn normalize_callee(text: &str) -> String {
    let mut callee = strip_outer_call_args(text);
    callee = callee.replace("?.", ".").replace("::", ".");
    callee = generics_re().replace_all(&callee, "").to_string();
    let callee = callee
        .trim_matches(|c| c == ' ' || c == '.')
        .to_string();
    if callee.contains('(') || callee.contains(')') {
        // Receiver chain contains nested call(s): A.b(...).c -> c
        let tail = callee.rsplit('.').next().unwrap_or("").trim();
        if !tail.is_empty() {
            return tail.to_string();
        }
    }
    callee
}

/// `_rightmost_identifier` — identifier cuối (duyệt con reversed).
fn rightmost_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "identifier" | "simple_identifier" | "field_identifier" | "type_identifier"
    ) {
        let text = node_text(node, source);
        let text = text.trim();
        if !text.is_empty() {
            return Some(text.to_string());
        }
    }
    let children: Vec<tree_sitter::Node> = node.children(&mut node.walk()).collect();
    for child in children.into_iter().rev() {
        if let Some(found) = rightmost_identifier(child, source) {
            return Some(found);
        }
    }
    None
}

/// `_has_descendant_type`.
fn has_descendant_type(node: tree_sitter::Node, target_type: &str) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == target_type || has_descendant_type(child, target_type) {
            return true;
        }
    }
    false
}

/// `_extract_call_name` — field "function"; navigation_expression chứa call
/// con → rightmost identifier; else normalize toàn text.
fn extract_call_name(call_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(function_node) = call_node.child_by_field_name("function") {
        if function_node.kind() == "navigation_expression"
            && has_descendant_type(function_node, "call_expression")
            && let Some(tail) = rightmost_identifier(function_node, source)
            && !tail.is_empty()
        {
            // For call-chains like A.b(...).c(...), prefer the final callee "c".
            return Some(tail);
        }
        let text = node_text(function_node, source);
        return Some(normalize_callee(&text));
    }
    let text = node_text(call_node, source);
    Some(normalize_callee(&text))
}

/// `_extract_parameter_info` — (name, type, (start, end, snippet) khi có type).
type ParamInfo = (Option<String>, Option<String>, Option<(i64, i64, String)>);

fn extract_parameter_info(param_node: tree_sitter::Node, source: &[u8]) -> ParamInfo {
    let name = param_node
        .child_by_field_name("name")
        .map(|n| node_text(n, source));
    let type_node = param_node.child_by_field_name("type");
    let type_text = type_node.map(|n| node_text(n, source));
    if let Some(type_node) = type_node {
        let (snippet, start_line, end_line) = node_snippet(type_node, source);
        return (
            name,
            type_text,
            Some((start_line as i64, end_line as i64, snippet)),
        );
    }
    (name, type_text, None)
}

// ── Symbol id helpers ───────────────────────────────────────────────────────

fn symbol_id(
    package_name: Option<&str>,
    class_name: Option<&str>,
    function_name: &str,
    arity: i64,
    rel_path: &str,
) -> String {
    let mut parts: Vec<&str> = Vec::new();
    if let Some(p) = package_name {
        parts.push(p);
    }
    if let Some(c) = class_name {
        parts.push(c);
    }
    parts.push(function_name);
    let qualified = parts.join(".");
    format!("{qualified}/{arity}@{rel_path}")
}

fn qualified_name(
    package_name: Option<&str>,
    class_name: Option<&str>,
    function_name: &str,
) -> String {
    let mut parts: Vec<&str> = Vec::new();
    if let Some(p) = package_name {
        parts.push(p);
    }
    if let Some(c) = class_name {
        parts.push(c);
    }
    parts.push(function_name);
    parts.join(".")
}

fn class_qualified_name(package_name: Option<&str>, class_name: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    if let Some(p) = package_name {
        parts.push(p);
    }
    parts.push(class_name);
    parts.join(".")
}

pub fn class_id(package_name: Option<&str>, class_name: &str) -> String {
    class_qualified_name(package_name, class_name)
}

/// `_class_kind` — node type → kind label (enum_class/enum_declaration cho
/// grammar cũ; grammar hiện tại xuất class_declaration + modifier `enum`).
fn class_kind(node_type: &str) -> Option<&'static str> {
    match node_type {
        "class_declaration" => Some("class"),
        "object_declaration" => Some("object"),
        "interface_declaration" => Some("interface"),
        "enum_class" => Some("enum"),
        "enum_declaration" => Some("enum"),
        _ => None,
    }
}

/// `_find_child` — con đầu tiên đúng kind.
fn find_child<'t>(node: tree_sitter::Node<'t>, node_type: &str) -> Option<tree_sitter::Node<'t>> {
    node.children(&mut node.walk())
        .find(|child| child.kind() == node_type)
}

/// `_iter_type_decls` — LIFO stack (con cuối pop trước), thứ tự yield khớp
/// generator Python. Trả (node, class_path, kind).
pub fn iter_type_decls<'t>(
    root: tree_sitter::Node<'t>,
    source: &[u8],
) -> Vec<(tree_sitter::Node<'t>, String, &'static str)> {
    let mut out = Vec::new();
    let mut stack: Vec<(tree_sitter::Node<'t>, Vec<String>)> = vec![(root, Vec::new())];
    while let Some((node, class_stack)) = stack.pop() {
        if let Some(kind) = class_kind(node.kind()) {
            let class_name = extract_class_name(node, source);
            let mut next_stack = class_stack.clone();
            if let Some(class_name) = class_name {
                next_stack.push(class_name);
                let class_path = next_stack.join(".");
                out.push((node, class_path, kind));
            }
            for child in node.children(&mut node.walk()) {
                stack.push((child, next_stack.clone()));
            }
            continue;
        }
        if node.kind() == "object_literal"
            && let Some(class_body) = find_child(node, "class_body")
        {
            let anonymous_name = format!(
                "Anonymous@{}:{}",
                node.start_position().row + 1,
                node.start_position().column + 1
            );
            let mut next_stack = class_stack.clone();
            next_stack.push(anonymous_name);
            let class_path = next_stack.join(".");
            out.push((class_body, class_path, "anonymous"));
            for child in class_body.children(&mut class_body.walk()) {
                stack.push((child, next_stack.clone()));
            }
            continue;
        }
        for child in node.children(&mut node.walk()) {
            stack.push((child, class_stack.clone()));
        }
    }
    out
}

/// `_extract_type_name` — token đầu dạng identifier chấm.
fn extract_type_name(text: &str) -> Option<String> {
    type_name_re().find(text).map(|m| m.as_str().to_string())
}

/// `_extract_super_type_from_specifier` — user_type / constructor_invocation /
/// explicit_delegation, fallback chính specifier; candidate đầu có tên thắng.
fn extract_super_type_from_specifier(
    spec_node: tree_sitter::Node,
    source: &[u8],
) -> Option<String> {
    let mut candidate_nodes: Vec<tree_sitter::Node> = Vec::new();
    for child in spec_node.children(&mut spec_node.walk()) {
        match child.kind() {
            "user_type" => candidate_nodes.push(child),
            "constructor_invocation" => {
                candidate_nodes.push(find_child(child, "user_type").unwrap_or(child));
            }
            "explicit_delegation" => candidate_nodes.push(
                find_child(child, "user_type")
                    .or_else(|| find_child(child, "constructor_invocation"))
                    .unwrap_or(child),
            ),
            _ => {}
        }
    }
    candidate_nodes.push(spec_node);
    for candidate in candidate_nodes {
        if let Some(name) = extract_type_name(&node_text(candidate, source)) {
            return Some(name);
        }
    }
    None
}

/// `_extract_super_types` — delegation_specifier list theo thứ tự.
fn extract_super_types(class_node: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut results: Vec<String> = Vec::new();
    let Some(delegation_specifiers) = find_child(class_node, "delegation_specifiers") else {
        return results;
    };
    for child in delegation_specifiers.children(&mut delegation_specifiers.walk()) {
        if child.kind() != "delegation_specifier" {
            continue;
        }
        if let Some(name) = extract_super_type_from_specifier(child, source) {
            results.push(name);
        }
    }
    results
}

/// `_iter_functions` — LIFO walk, yield (function_declaration, active_class).
pub fn iter_functions<'t>(
    root: tree_sitter::Node<'t>,
    source: &[u8],
) -> Vec<(tree_sitter::Node<'t>, Option<String>)> {
    let mut out = Vec::new();
    let mut stack: Vec<(tree_sitter::Node<'t>, Vec<String>)> = vec![(root, Vec::new())];
    while let Some((node, class_stack)) = stack.pop() {
        if class_kind(node.kind()).is_some() {
            let class_name = extract_class_name(node, source);
            let mut next_stack = class_stack.clone();
            if let Some(class_name) = class_name {
                next_stack.push(class_name);
            }
            for child in node.children(&mut node.walk()) {
                stack.push((child, next_stack.clone()));
            }
            continue;
        }
        if node.kind() == "object_literal"
            && let Some(class_body) = find_child(node, "class_body")
        {
            let anonymous_name = format!(
                "Anonymous@{}:{}",
                node.start_position().row + 1,
                node.start_position().column + 1
            );
            let mut next_stack = class_stack.clone();
            next_stack.push(anonymous_name);
            for child in class_body.children(&mut class_body.walk()) {
                stack.push((child, next_stack.clone()));
            }
            continue;
        }
        if node.kind() == "function_declaration" {
            let active_class = if class_stack.is_empty() {
                None
            } else {
                Some(class_stack.join("."))
            };
            out.push((node, active_class));
        }
        for child in node.children(&mut node.walk()) {
            stack.push((child, class_stack.clone()));
        }
    }
    out
}

/// `_tree_error_stats` — (has_error, số node ERROR).
fn tree_error_stats(root: tree_sitter::Node) -> (bool, i64) {
    let has_error = root.has_error();
    let error_nodes = find_nodes_by_type(root, "ERROR").len() as i64;
    (has_error, error_nodes)
}

// ── parse_kotlin_file ───────────────────────────────────────────────────────

/// `parse_kotlin_file` — walk 1 file, payload asdict-shape.
pub fn parse_kotlin_file(
    path: &std::path::Path,
    root: &std::path::Path,
) -> Result<FilePayload, String> {
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_kotlin_ng::LANGUAGE.into())
        .map_err(|e| format!("set_language failed: {e}"))?;
    let source_bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let tree = parser
        .parse(&source_bytes, None)
        .ok_or_else(|| "parse failed".to_string())?;
    let root_node = tree.root_node();
    let (has_error, error_nodes) = tree_error_stats(root_node);
    let rel_path = rel_posix(root, path);
    let (package_name, pkg_start, pkg_end, pkg_snippet, pkg_comment) =
        collect_package_info(root_node, &source_bytes);
    let imports = collect_imports(root_node, &source_bytes);
    let mut payload = FilePayload {
        has_error,
        error_nodes,
        ..Default::default()
    };

    let package_def = package_name.clone().map(|package_name| {
        let pkg_summary = pkg_comment.clone();
        let pkg_note = build_note(&pkg_snippet, &pkg_comment, &pkg_summary);
        PackageDef {
            name: package_name,
            start_line: pkg_start,
            end_line: pkg_end,
            code: pkg_snippet,
            comment: pkg_comment,
            summary: pkg_summary,
            note: pkg_note,
        }
    });

    let file_code = decode_ignore(&source_bytes);
    let file_lines = file_code.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment(root_node, &source_bytes);
    let file_summary = file_comment.clone();
    let file_note = build_note(&file_code, &file_comment, &file_summary);
    payload.file_def = Some(FileDef {
        file_path: rel_path.clone(),
        package_name: package_name.clone(),
        start_line: 1,
        end_line: file_lines,
        code: file_code,
        comment: file_comment,
        summary: file_summary,
        note: file_note,
    });

    // ── Pass 1: type declarations ───────────────────────────────────────────
    for (class_node, class_path, kind) in iter_type_decls(root_node, &source_bytes) {
        let (snippet, start_line, end_line) = node_snippet(class_node, &source_bytes);
        let comment = extract_leading_comment(class_node, &source_bytes);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let cid = class_id(package_name.as_deref(), &class_path);
        let qualified = class_qualified_name(package_name.as_deref(), &class_path);
        let (visibility, is_public_api, export_evidence) = kotlin_api_visibility(&snippet);
        let signature = source_signature(&snippet);
        payload.classes.push(ClassDef {
            symbol_id: cid.clone(),
            qualified_name: qualified,
            name: class_path.clone(),
            kind: kind.to_string(),
            package_name: package_name.clone(),
            file_path: rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment,
            summary,
            note,
            visibility,
            is_public_api,
            visibility_source: "source-modifier".to_string(),
            export_evidence,
            signature,
        });
        let super_types = extract_super_types(class_node, &source_bytes);
        if super_types.is_empty() {
            continue;
        }
        if kind == "class" || kind == "object" || kind == "enum" {
            let mut rest = super_types.iter();
            if let Some(first) = rest.next() {
                payload.type_edges.push(TypeEdge {
                    source_id: cid.clone(),
                    source_package: package_name.clone(),
                    target_name: first.clone(),
                    rel_type: "EXTENDS".to_string(),
                });
                for item in rest {
                    payload.type_edges.push(TypeEdge {
                        source_id: cid.clone(),
                        source_package: package_name.clone(),
                        target_name: item.clone(),
                        rel_type: "IMPLEMENTS".to_string(),
                    });
                }
            }
        } else if kind == "interface" {
            for item in &super_types {
                payload.type_edges.push(TypeEdge {
                    source_id: cid.clone(),
                    source_package: package_name.clone(),
                    target_name: item.clone(),
                    rel_type: "EXTENDS".to_string(),
                });
            }
        }
    }

    // ── Pass 2: functions + params + calls + lambdas ────────────────────────
    for (func_node, class_name) in iter_functions(root_node, &source_bytes) {
        let Some(func_name) = extract_function_name(func_node, &source_bytes) else {
            continue;
        };
        if func_name.is_empty() {
            continue;
        }
        let arity = count_parameters(func_node);
        let symbol = symbol_id(
            package_name.as_deref(),
            class_name.as_deref(),
            &func_name,
            arity,
            &rel_path,
        );
        let qualified = qualified_name(
            package_name.as_deref(),
            class_name.as_deref(),
            &func_name,
        );
        let (snippet, start_line, end_line) = node_snippet(func_node, &source_bytes);
        let comment = extract_leading_comment(func_node, &source_bytes);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let (visibility, is_public_api, export_evidence) = kotlin_api_visibility(&snippet);
        let signature = source_signature(&snippet);
        payload.functions.push(FunctionDef {
            symbol_id: symbol.clone(),
            qualified_name: qualified,
            name: func_name.clone(),
            kind: "function".to_string(),
            class_name: class_name.clone(),
            package_name: package_name.clone(),
            file_path: rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            arity,
            code: snippet.clone(),
            comment: comment.clone(),
            summary,
            note,
            visibility,
            is_public_api,
            visibility_source: "source-modifier".to_string(),
            export_evidence,
            signature,
        });

        // Parameter kiểu hàm (`->`) → FunctionType + TAKES_FUNCTION.
        for param_node in iter_function_parameters(func_node) {
            let (param_name, type_text, type_info) =
                extract_parameter_info(param_node, &source_bytes);
            let Some(type_text) = type_text else {
                continue;
            };
            if !type_text.contains("->") {
                continue;
            }
            let type_signature = normalize_type_signature(&type_text);
            let type_id = format!("functype::{type_signature}");
            let (start_line_t, end_line_t, snippet_t) = match type_info {
                None => (start_line as i64, end_line as i64, type_signature.clone()),
                Some((s, e, sn)) => (s, e, sn),
            };
            payload.function_types.push(FunctionTypeDef {
                symbol_id: type_id.clone(),
                type_signature: type_signature.clone(),
                file_path: rel_path.clone(),
                start_line: start_line_t,
                end_line: end_line_t,
                code: snippet_t,
            });
            let mut properties = Row::new();
            properties.insert(
                "parameter_name".to_string(),
                Value::String(param_name.unwrap_or_default()),
            );
            properties.insert(
                "parameter_type".to_string(),
                Value::String(type_signature),
            );
            payload.relations.push(RelationEdge {
                source_id: symbol.clone(),
                source_label: "Function".to_string(),
                target_id: type_id,
                target_label: "FunctionType".to_string(),
                rel_type: "TAKES_FUNCTION".to_string(),
                properties,
            });
        }

        // Calls trong TOÀN subtree function (gồm cả lambda lồng — python quét
        // find_nodes_by_type trên func_node).
        for call_node in find_nodes_by_type(func_node, "call_expression") {
            let Some(callee) = extract_call_name(call_node, &source_bytes) else {
                continue;
            };
            if callee.is_empty() {
                continue;
            }
            payload.calls.push(CallEdge {
                caller_id: symbol.clone(),
                caller_file: rel_path.clone(),
                caller_package: package_name.clone(),
                caller_class: class_name.clone(),
                imports: imports.clone(),
                callee_name: callee,
            });
        }

        // Lambda literals → FunctionDef(kind="lambda") + CONTAINS + calls.
        for lambda_node in find_nodes_by_type(func_node, "lambda_literal") {
            let (l_snippet, l_start, l_end) = node_snippet(lambda_node, &source_bytes);
            let lambda_name = format!(
                "{func_name}$lambda@{}:{}",
                l_start,
                lambda_node.start_position().column + 1
            );
            let lambda_id = symbol_id(
                package_name.as_deref(),
                class_name.as_deref(),
                &lambda_name,
                0,
                &rel_path,
            );
            let lambda_qualified = qualified_name(
                package_name.as_deref(),
                class_name.as_deref(),
                &lambda_name,
            );
            payload.functions.push(FunctionDef {
                symbol_id: lambda_id.clone(),
                qualified_name: lambda_qualified,
                name: lambda_name,
                kind: "lambda".to_string(),
                class_name: class_name.clone(),
                package_name: package_name.clone(),
                file_path: rel_path.clone(),
                start_line: l_start as i64,
                end_line: l_end as i64,
                arity: 0,
                code: l_snippet.clone(),
                comment: String::new(),
                summary: String::new(),
                note: build_note(&l_snippet, "", ""),
                visibility: "private".to_string(),
                is_public_api: false,
                visibility_source: "source-modifier".to_string(),
                export_evidence: "lambda is not a declared public API".to_string(),
                signature: source_signature(&l_snippet),
            });
            payload.relations.push(RelationEdge {
                source_id: symbol.clone(),
                source_label: "Function".to_string(),
                target_id: lambda_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Row::new(),
            });
            for lambda_call in find_nodes_by_type(lambda_node, "call_expression") {
                let Some(callee) = extract_call_name(lambda_call, &source_bytes) else {
                    continue;
                };
                if callee.is_empty() {
                    continue;
                }
                payload.calls.push(CallEdge {
                    caller_id: lambda_id.clone(),
                    caller_file: rel_path.clone(),
                    caller_package: package_name.clone(),
                    caller_class: class_name.clone(),
                    imports: imports.clone(),
                    callee_name: callee,
                });
            }
        }
    }

    payload.package_def = package_def;
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_src(src: &str) -> FilePayload {
        // Unique per-call — các test chạy song song chung process.
        static SEQ: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let name = format!("Test{seq}.kt");
        let tmp = std::env::temp_dir().join(format!("p06_kt_parse_test_{}_{}", std::process::id(), seq));
        std::fs::create_dir_all(&tmp).unwrap();
        let file = tmp.join(name);
        std::fs::write(&file, src).unwrap();
        let payload = parse_kotlin_file(&file, &tmp).unwrap();
        std::fs::remove_dir_all(&tmp).ok();
        payload
    }

    #[test]
    fn parses_class_function_symbol_ids() {
        let payload = parse_src("package com.demo\n\nclass Greeter {\n    fun greet(): String {\n        return \"hi\"\n    }\n}\n");
        assert_eq!(payload.package_def.as_ref().unwrap().name, "com.demo");
        assert_eq!(payload.classes.len(), 1);
        assert_eq!(payload.classes[0].symbol_id, "com.demo.Greeter");
        assert_eq!(payload.classes[0].kind, "class");
        assert_eq!(payload.functions.len(), 1);
        assert_eq!(payload.functions[0].qualified_name, "com.demo.Greeter.greet");
        assert_eq!(payload.functions[0].arity, 0);
        assert_eq!(payload.functions[0].visibility, "public");
        assert!(payload.functions[0].is_public_api);
    }

    #[test]
    fn private_visibility_and_calls() {
        let payload = parse_src(
            "class A {\n    private fun helper() {\n        this.helper()\n    }\n    fun run() {\n        helper()\n    }\n}\n",
        );
        let helper = payload
            .functions
            .iter()
            .find(|f| f.name == "helper")
            .unwrap();
        assert_eq!(helper.visibility, "private");
        assert!(!helper.is_public_api);
        assert_eq!(helper.export_evidence, "explicit private");
        // `this.helper()` → callee "this.helper" (normalize_callee giữ receiver),
        // `helper()` → "helper" — khớp hành vi Python đã verify.
        let callees: Vec<String> = payload.calls.iter().map(|c| c.callee_name.clone()).collect();
        assert_eq!(callees, vec!["helper", "this.helper"]);
    }

    #[test]
    fn lambda_calls_and_inert_function_type() {
        let payload = parse_src(
            "fun apply(v: Int, transform: (Int) -> Int): Int {\n    return transform(v)\n}\n\nfun demo() {\n    val r = apply(1) { it + 1 }\n}\n",
        );
        // Grammar v1.1.0 KHÔNG có field "type" trên parameter → python bỏ qua
        // (function_types rỗng, không TAKES_FUNCTION) — Rust khớp.
        assert!(payload.function_types.is_empty());
        // 1 relation duy nhất: CONTAINS lambda.
        assert_eq!(payload.relations.len(), 1);
        assert_eq!(payload.relations[0].rel_type, "CONTAINS");
        let lambdas: Vec<&FunctionDef> = payload
            .functions
            .iter()
            .filter(|f| f.kind == "lambda")
            .collect();
        assert_eq!(lambdas.len(), 1);
        assert!(lambdas[0].name.starts_with("demo$lambda@"));
        assert_eq!(lambdas[0].visibility, "private");
        // Trailing-lambda call tạo 2 call_expression: 'apply(1) { it + 1 }' và
        // 'apply(1)' (nested) — khớp Python (verify bằng parse_kotlin_file);
        // cộng 'transform(v)' trong body của apply.
        let callees: Vec<String> = payload.calls.iter().map(|c| c.callee_name.clone()).collect();
        assert_eq!(callees, vec!["apply(1) { it + 1 }", "apply", "transform"]);
    }

    #[test]
    fn interface_extends_class_implements() {
        let payload = parse_src(
            "interface I : Base\n\nclass C : Base(), Extra {\n}\n",
        );
        let i_edge = payload.type_edges.iter().find(|e| e.source_id == "I").unwrap();
        assert_eq!(i_edge.rel_type, "EXTENDS");
        let c_edges: Vec<&TypeEdge> = payload
            .type_edges
            .iter()
            .filter(|e| e.source_id == "C")
            .collect();
        assert_eq!(c_edges.len(), 2);
        assert_eq!(c_edges[0].rel_type, "EXTENDS");
        assert_eq!(c_edges[0].target_name, "Base");
        assert_eq!(c_edges[1].rel_type, "IMPLEMENTS");
        assert_eq!(c_edges[1].target_name, "Extra");
    }
}

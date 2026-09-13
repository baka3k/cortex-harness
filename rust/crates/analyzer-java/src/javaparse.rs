//! Port phần parse của `tools/java/java_analyzer.py`: tree-sitter walk
//! (`_iter_type_decls` / `_iter_methods` — LIFO stack, thứ tự con cuối trước),
//! FunctionDef/ClassDef/FileDef/PackageDef, visibility từ source modifier,
//! super types (extends/implements), generics arity (số formal_parameter),
//! calls (method_invocation / object_creation / explicit ctor / method
//! reference / lambda), function types (TAKES_FUNCTION).

use serde_json::{Map, Value};

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
    pub source_label: String,
    pub target_id: String,
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
    pub caller_package: Option<String>,
    pub caller_class: Option<String>,
    pub imports: Vec<String>,
    pub callee_name: String,
    pub callee_id: Option<String>,
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

fn visibility_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"\b(public|protected|private)\b").expect("visibility re"))
}

fn generics_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"<.*?>").expect("generics re"))
}

fn type_name_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"[A-Za-z_][A-Za-z0-9_\.]*").expect("type name re"))
}

// ── Helpers (port từng hàm `_` của java_analyzer.py) ────────────────────────

/// `_java_api_visibility` — classify source-level Java visibility.
pub fn java_api_visibility(snippet: &str, implicit_public: bool) -> (String, bool, &'static str) {
    let header = snippet.split('{').next().unwrap_or("");
    let mut has_public = false;
    let mut has_protected = false;
    let mut has_private = false;
    for caps in visibility_re().captures_iter(header) {
        match caps.get(1).expect("group").as_str() {
            "public" => has_public = true,
            "protected" => has_protected = true,
            "private" => has_private = true,
            _ => {}
        }
    }
    if has_private {
        return ("private".into(), false, "explicit private");
    }
    if has_protected {
        return ("protected".into(), false, "explicit protected");
    }
    if has_public {
        return ("public".into(), true, "explicit public");
    }
    if implicit_public {
        return (
            "public".into(),
            true,
            "implicit interface/annotation public",
        );
    }
    (
        "package".into(),
        false,
        "package-private by Java language rule",
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

fn is_java_comment(kind: &str) -> bool {
    kind == "line_comment" || kind == "block_comment"
}

/// `_node_snippet` — java comment kinds (line_comment/block_comment).
pub fn node_snippet(node: tree_sitter::Node, source: &[u8]) -> (String, usize, usize) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if is_java_comment(p.kind()) {
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

/// `_extract_leading_comment` — java comment kinds, reversed join "\n".
pub fn extract_leading_comment(node: tree_sitter::Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if is_java_comment(p.kind()) {
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
        if is_java_comment(child.kind()) {
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

/// `_first_identifier` — identifier/type_identifier đầu tiên (depth-first).
fn first_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if node.kind() == "identifier" || node.kind() == "type_identifier" {
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

/// `_collect_package_info` — package_declaration đầu tiên.
#[allow(clippy::type_complexity)]
fn collect_package_info(
    root: tree_sitter::Node,
    source: &[u8],
) -> (Option<String>, i64, i64, String, String) {
    for node in find_nodes_by_type(root, "package_declaration") {
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

/// `_collect_imports` — import_declaration, bỏ token "import"/"static".
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_declaration") {
        let text = node_text(node, source);
        let identifiers: Vec<String> = extract_identifiers(&text)
            .into_iter()
            .filter(|token| token != "import" && token != "static")
            .collect();
        if !identifiers.is_empty() {
            imports.push(identifiers.join("."));
        }
    }
    imports
}

/// `_count_parameters` — đếm formal_parameter (bỏ spread/rest).
fn count_parameters(method_node: tree_sitter::Node) -> i64 {
    let Some(param_list) = method_node.child_by_field_name("parameters") else {
        return 0;
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "formal_parameter")
        .count() as i64
}

/// `_iter_method_parameters` — formal_parameter children.
fn iter_method_parameters(method_node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let Some(param_list) = method_node.child_by_field_name("parameters") else {
        return Vec::new();
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "formal_parameter")
        .collect()
}

/// `_extract_method_name` — name field, fallback first identifier.
fn extract_method_name(method_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = method_node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(method_node, source)
}

/// `_extract_class_name` — name field, fallback first identifier.
fn extract_class_name(class_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = class_node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(class_node, source)
}

/// `_normalize_callee` — cắt "(", "::"→".", bỏ `<...>`, strip " .".
fn normalize_callee(text: &str) -> String {
    let mut callee = text.split('(').next().unwrap_or("").trim().to_string();
    callee = callee.replace("::", ".");
    callee = generics_re().replace_all(&callee, "").to_string();
    callee
        .trim_matches(|c| c == ' ' || c == '.')
        .to_string()
}

/// `_extract_call_name` — "object.name" hoặc name field, fallback full text.
fn extract_call_name(call_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = call_node.child_by_field_name("name") {
        let name_text = node_text(name_node, source);
        if let Some(object_node) = call_node.child_by_field_name("object") {
            let obj_text = node_text(object_node, source);
            return Some(normalize_callee(&format!("{obj_text}.{name_text}")));
        }
        return Some(normalize_callee(&name_text));
    }
    let text = node_text(call_node, source);
    Some(normalize_callee(&text))
}

/// `_is_probably_qualified` — có "." và segment đầu viết thường.
fn is_probably_qualified(type_text: &str) -> bool {
    if !type_text.contains('.') {
        return false;
    }
    let first = type_text.split('.').next().unwrap_or("");
    match first.chars().next() {
        Some(c) => c.is_lowercase(),
        None => false,
    }
}

/// `_constructor_name_from_class` — segment cuối của class name.
fn constructor_name_from_class(class_name: Option<&str>) -> Option<String> {
    let class_name = class_name?;
    class_name.rsplit('.').next().map(str::to_string)
}

/// `_constructor_callee_name` — bỏ generics; qualified → "a.b.B" else "B".
fn constructor_callee_name(type_text: Option<&str>) -> Option<String> {
    let type_text = type_text?;
    let cleaned = generics_re().replace_all(type_text, "").trim().to_string();
    if cleaned.is_empty() {
        return None;
    }
    let simple = cleaned.rsplit('.').next().unwrap_or(&cleaned).to_string();
    if is_probably_qualified(&cleaned) {
        Some(format!("{cleaned}.{simple}"))
    } else {
        Some(simple)
    }
}

/// `_extract_constructor_call_name` — type field, fallback text sau "new".
fn extract_constructor_call_name(
    call_node: tree_sitter::Node,
    source: &[u8],
) -> Option<String> {
    let mut type_text: Option<String> = call_node
        .child_by_field_name("type")
        .map(|n| node_text(n, source));
    let needs_fallback = type_text.as_ref().is_none_or(String::is_empty);
    if needs_fallback {
        let text = node_text(call_node, source);
        if let Some((_, rest)) = text.split_once("new") {
            type_text = Some(rest.split('(').next().unwrap_or("").to_string());
        } else {
            type_text = Some(text);
        }
    }
    constructor_callee_name(type_text.as_deref())
}

/// `_extract_method_reference_name` — `Qualifier::member` / `Qualifier::new`.
fn extract_method_reference_name(ref_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    let text = node_text(ref_node, source);
    if !text.contains("::") {
        return Some(normalize_callee(&text));
    }
    let (qualifier, member) = text.split_once("::")?;
    let qualifier = qualifier.trim();
    let member = member.trim();
    if member == "new" {
        return constructor_callee_name(Some(qualifier));
    }
    Some(normalize_callee(&format!("{qualifier}.{member}")))
}

/// `_extract_explicit_constructor_name` — this(...) / super(...).
fn extract_explicit_constructor_name(
    node: tree_sitter::Node,
    source: &[u8],
    class_name: Option<&str>,
    class_super_map: &std::collections::HashMap<String, Vec<String>>,
) -> Option<String> {
    let text = node_text(node, source);
    let text = text.trim();
    if text.starts_with("this") {
        return constructor_name_from_class(class_name);
    }
    if text.starts_with("super") {
        let class_name = class_name?;
        let super_types = class_super_map.get(class_name)?;
        let first = super_types.first()?;
        return constructor_callee_name(Some(first.as_str()));
    }
    None
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

/// `_class_kind` — node type → kind label.
fn class_kind(node_type: &str) -> Option<&'static str> {
    match node_type {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "enum_declaration" => Some("enum"),
        "record_declaration" => Some("record"),
        _ => None,
    }
}

/// `_extract_type_name` — token đầu dạng identifier chấm.
fn extract_type_name(text: &str) -> Option<String> {
    type_name_re().find(text).map(|m| m.as_str().to_string())
}

/// `_extract_super_types` — superclass + interfaces, dedup giữ thứ tự.
fn extract_super_types(class_node: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    fn extract_type_from_node(node: Option<tree_sitter::Node>, source: &[u8]) -> Option<String> {
        let node = node?;
        if node.kind() == "type_identifier"
            || node.kind() == "scoped_type_identifier"
            || node.kind() == "identifier"
        {
            return Some(node_text(node, source).trim().to_string());
        }
        if node.kind() == "generic_type" {
            let mut child = node.child_by_field_name("type");
            if child.is_none() {
                for item in node.children(&mut node.walk()) {
                    if item.kind() == "type_identifier"
                        || item.kind() == "scoped_type_identifier"
                        || item.kind() == "identifier"
                    {
                        child = Some(item);
                        break;
                    }
                }
            }
            if let Some(child) = child {
                return extract_type_from_node(Some(child), source);
            }
        }
        if node.kind() == "annotated_type" || node.kind() == "array_type" {
            for item in node.children(&mut node.walk()) {
                if item.is_named() {
                    let nested = extract_type_from_node(Some(item), source);
                    if nested.is_some() {
                        return nested;
                    }
                }
            }
        }
        let text = node_text(node, source);
        extract_type_name(text.trim())
    }

    fn collect_type_list(node: Option<tree_sitter::Node>, source: &[u8]) -> Vec<String> {
        let Some(node) = node else {
            return Vec::new();
        };
        let mut type_list = node;
        if node.kind() == "super_interfaces" || node.kind() == "extends_interfaces" {
            for child in node.children(&mut node.walk()) {
                if child.kind() == "type_list" {
                    type_list = child;
                    break;
                }
            }
        }
        let mut names = Vec::new();
        for child in type_list.children(&mut type_list.walk()) {
            if !child.is_named() {
                continue;
            }
            if let Some(name) = extract_type_from_node(Some(child), source)
                && !name.is_empty()
            {
                names.push(name);
            }
        }
        names
    }

    let mut results: Vec<String> = Vec::new();
    if class_node.kind() == "class_declaration" || class_node.kind() == "enum_declaration" {
        let superclass = class_node.child_by_field_name("superclass");
        if let Some(superclass) = superclass {
            for child in superclass.children(&mut superclass.walk()) {
                if !child.is_named() {
                    continue;
                }
                if let Some(name) = extract_type_from_node(Some(child), source)
                    && !name.is_empty()
                {
                    results.push(name);
                    break;
                }
            }
        }
        let interfaces = class_node.child_by_field_name("interfaces");
        results.extend(collect_type_list(interfaces, source));
    } else if class_node.kind() == "record_declaration" {
        let interfaces = class_node.child_by_field_name("interfaces");
        results.extend(collect_type_list(interfaces, source));
    } else if class_node.kind() == "interface_declaration" {
        let mut extends_interfaces = None;
        for child in class_node.children(&mut class_node.walk()) {
            if child.kind() == "extends_interfaces" {
                extends_interfaces = Some(child);
                break;
            }
        }
        results.extend(collect_type_list(extends_interfaces, source));
    }
    let mut deduped: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for item in results {
        if !seen.insert(item.clone()) {
            continue;
        }
        deduped.push(item);
    }
    deduped
}

/// `_tree_error_stats` — (has_error, số node ERROR).
fn tree_error_stats(root: tree_sitter::Node) -> (bool, i64) {
    let has_error = root.has_error();
    let error_nodes = find_nodes_by_type(root, "ERROR").len() as i64;
    (has_error, error_nodes)
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
        if node.kind() == "object_creation_expression" {
            let mut class_body = None;
            for child in node.children(&mut node.walk()) {
                if child.kind() == "class_body" {
                    class_body = Some(child);
                    break;
                }
            }
            if let Some(class_body) = class_body {
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
        }
        for child in node.children(&mut node.walk()) {
            stack.push((child, class_stack.clone()));
        }
    }
    out
}

/// `_iter_methods` — LIFO walk, yield (method/ctor node, active_class).
pub fn iter_methods<'t>(
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
        if node.kind() == "object_creation_expression" {
            let mut class_body = None;
            for child in node.children(&mut node.walk()) {
                if child.kind() == "class_body" {
                    class_body = Some(child);
                    break;
                }
            }
            if let Some(class_body) = class_body {
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
        }
        if node.kind() == "method_declaration" || node.kind() == "constructor_declaration" {
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

/// `_is_functional_interface_type` — substring match bộ interface nổi tiếng.
fn is_functional_interface_type(type_text: &str) -> bool {
    const CANDIDATES: [&str; 12] = [
        "Runnable",
        "Callable",
        "Supplier",
        "Consumer",
        "BiConsumer",
        "Function",
        "BiFunction",
        "Predicate",
        "BiPredicate",
        "UnaryOperator",
        "BinaryOperator",
        "Comparator",
    ];
    CANDIDATES.iter().any(|name| type_text.contains(name))
}

// ── parse_java_file ─────────────────────────────────────────────────────────

/// `parse_java_file` — walk 1 file, payload asdict-shape.
pub fn parse_java_file(path: &std::path::Path, root: &std::path::Path) -> Result<FilePayload, String> {
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_java::LANGUAGE.into())
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

    let package_def = package_name
        .clone()
        .map(|package_name| {
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

    let mut class_super_map: std::collections::HashMap<String, Vec<String>> =
        std::collections::HashMap::new();
    let mut class_kind_map: std::collections::HashMap<String, &'static str> =
        std::collections::HashMap::new();

    // ── Pass 1: type declarations ───────────────────────────────────────────
    for (class_node, class_path, kind) in iter_type_decls(root_node, &source_bytes) {
        let (snippet, start_line, end_line) = node_snippet(class_node, &source_bytes);
        let comment = extract_leading_comment(class_node, &source_bytes);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let cid = class_id(package_name.as_deref(), &class_path);
        let qualified = class_qualified_name(package_name.as_deref(), &class_path);
        let (visibility, is_public_api, export_evidence) = java_api_visibility(&snippet, false);
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
            export_evidence: export_evidence.to_string(),
            signature,
        });
        let super_types = extract_super_types(class_node, &source_bytes);
        class_super_map.insert(class_path.clone(), super_types.clone());
        class_kind_map.insert(class_path.clone(), kind);
        if super_types.is_empty() {
            continue;
        }
        if kind == "class" || kind == "anonymous" {
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
        } else if kind == "enum" || kind == "record" {
            for item in &super_types {
                payload.type_edges.push(TypeEdge {
                    source_id: cid.clone(),
                    source_package: package_name.clone(),
                    target_name: item.clone(),
                    rel_type: "IMPLEMENTS".to_string(),
                });
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

    // ── Pass 2: methods + calls + lambdas ───────────────────────────────────
    for (method_node, class_name) in iter_methods(root_node, &source_bytes) {
        let Some(method_name) = extract_method_name(method_node, &source_bytes) else {
            continue;
        };
        if method_name.is_empty() {
            continue;
        }
        let arity = count_parameters(method_node);
        let kind = if method_node.kind() == "constructor_declaration" {
            "constructor"
        } else {
            "method"
        };
        let symbol = symbol_id(
            package_name.as_deref(),
            class_name.as_deref(),
            &method_name,
            arity,
            &rel_path,
        );
        let qualified = qualified_name(
            package_name.as_deref(),
            class_name.as_deref(),
            &method_name,
        );
        let (snippet, start_line, end_line) = node_snippet(method_node, &source_bytes);
        let comment = extract_leading_comment(method_node, &source_bytes);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let owner_kind = class_name
            .as_deref()
            .and_then(|cn| class_kind_map.get(cn).copied())
            .unwrap_or("");
        let implicit_public = owner_kind == "interface" || owner_kind == "annotation";
        let (visibility, is_public_api, export_evidence) =
            java_api_visibility(&snippet, implicit_public);
        payload.functions.push(FunctionDef {
            symbol_id: symbol.clone(),
            qualified_name: qualified,
            name: method_name.clone(),
            kind: kind.to_string(),
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
            export_evidence: export_evidence.to_string(),
            signature: source_signature(&snippet),
        });

        // Functional-interface parameters → FunctionType + TAKES_FUNCTION.
        for param_node in iter_method_parameters(method_node) {
            let (param_name, type_text, type_info) =
                extract_parameter_info(param_node, &source_bytes);
            let Some(type_text) = type_text else {
                continue;
            };
            if !is_functional_interface_type(&type_text) {
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

        let mut push_call = |callee_name: Option<String>| {
            if let Some(callee) = callee_name {
                if callee.is_empty() {
                    return;
                }
                payload.calls.push(CallEdge {
                    caller_id: symbol.clone(),
                    caller_package: package_name.clone(),
                    caller_class: class_name.clone(),
                    imports: imports.clone(),
                    callee_name: callee,
                    callee_id: None,
                });
            }
        };

        for call_node in find_nodes_by_type(method_node, "method_invocation") {
            push_call(extract_call_name(call_node, &source_bytes));
        }
        for ctor_node in find_nodes_by_type(method_node, "object_creation_expression") {
            push_call(extract_constructor_call_name(ctor_node, &source_bytes));
        }
        for ctor_inv in find_nodes_by_type(method_node, "explicit_constructor_invocation") {
            push_call(extract_explicit_constructor_name(
                ctor_inv,
                &source_bytes,
                class_name.as_deref(),
                &class_super_map,
            ));
        }
        for ref_node in find_nodes_by_type(method_node, "method_reference") {
            push_call(extract_method_reference_name(ref_node, &source_bytes));
        }

        for lambda_node in find_nodes_by_type(method_node, "lambda_expression") {
            let (l_snippet, l_start, l_end) = node_snippet(lambda_node, &source_bytes);
            let lambda_name = format!(
                "{method_name}$lambda@{}:{}",
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
            for lambda_call in find_nodes_by_type(lambda_node, "method_invocation") {
                push_call_lambda(
                    &mut payload,
                    &lambda_id,
                    &package_name,
                    &class_name,
                    &imports,
                    extract_call_name(lambda_call, &source_bytes),
                );
            }
            for lambda_ctor in find_nodes_by_type(lambda_node, "object_creation_expression") {
                push_call_lambda(
                    &mut payload,
                    &lambda_id,
                    &package_name,
                    &class_name,
                    &imports,
                    extract_constructor_call_name(lambda_ctor, &source_bytes),
                );
            }
            for lambda_ctor_inv in
                find_nodes_by_type(lambda_node, "explicit_constructor_invocation")
            {
                push_call_lambda(
                    &mut payload,
                    &lambda_id,
                    &package_name,
                    &class_name,
                    &imports,
                    extract_explicit_constructor_name(
                        lambda_ctor_inv,
                        &source_bytes,
                        class_name.as_deref(),
                        &class_super_map,
                    ),
                );
            }
            for lambda_ref in find_nodes_by_type(lambda_node, "method_reference") {
                push_call_lambda(
                    &mut payload,
                    &lambda_id,
                    &package_name,
                    &class_name,
                    &imports,
                    extract_method_reference_name(lambda_ref, &source_bytes),
                );
            }
        }
    }

    payload.package_def = package_def;
    Ok(payload)
}

/// Push 1 call edge của lambda (caller = lambda_id).
#[allow(clippy::too_many_arguments)]
fn push_call_lambda(
    payload: &mut FilePayload,
    lambda_id: &str,
    package_name: &Option<String>,
    class_name: &Option<String>,
    imports: &[String],
    callee: Option<String>,
) {
    let Some(callee) = callee else { return };
    if callee.is_empty() {
        return;
    }
    payload.calls.push(CallEdge {
        caller_id: lambda_id.to_string(),
        caller_package: package_name.clone(),
        caller_class: class_name.clone(),
        imports: imports.to_vec(),
        callee_name: callee,
        callee_id: None,
    });
}

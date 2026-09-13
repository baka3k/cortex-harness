//! Port phần parse của `tools/swift/swift_analyzer.py`: tree-sitter walk
//! (`_walk_tree`), FunctionDef/TypeDef/FieldDef/AliasDef/TemplateDef/
//! RelationEdge/CallEdge, `_resolve_calls`, `_extract_name` (pattern +
//! bound_identifier + `_clean_identifier`), `_extract_decl_kind`,
//! `_add_type_use`, `_count_arguments` (top-level counting), error stats.
//!
//! Grammar: `tree-sitter-swift` 0.7 (PyPI venv tham chiếu dùng 0.7.3 —
//! crates.io có đúng 0.7.3 nên node kinds khớp từng chữ).
//!
//! `_control_context` (branch/loop frames) KHÔNG port: giá trị chỉ nằm trong
//! call-row dicts mà writer không bao giờ đọc — không thể vào graph.

use std::collections::HashSet;
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;
use serde::Serialize;
use serde_json::{json, Map, Value};

use cortex_analyzer_framework::ts::{decode_ignore, find_nodes_by_type, line_from_byte, node_text};

pub type Row = Map<String, Value>;

pub const SWIFT_SOURCE_EXTENSIONS: [&str; 1] = [".swift"];

/// `_COMMENT_TYPES`.
const COMMENT_TYPES: [&str; 2] = ["comment", "multiline_comment"];

/// `_TYPE_NODES` — class_declaration/protocol_declaration (kind tính riêng).
const TYPE_NODES: [&str; 2] = ["class_declaration", "protocol_declaration"];

/// `_FUNCTION_NODES`.
const FUNCTION_NODES: [&str; 5] = [
    "function_declaration",
    "protocol_function_declaration",
    "init_declaration",
    "deinit_declaration",
    "subscript_declaration",
];

/// `_ALIAS_NODES`.
const ALIAS_NODES: [&str; 2] = ["typealias_declaration", "associatedtype_declaration"];

/// `_CALL_NODES`.
const CALL_NODES: [&str; 3] = ["call_expression", "constructor_expression", "macro_invocation"];

// `_BRANCH_NODES`/`_LOOP_NODES` chỉ dùng cho `_control_context` (không port —
// xem doc-comment đầu module).

fn is_comment_kind(kind: &str) -> bool {
    COMMENT_TYPES.contains(&kind)
}

fn is_type_node(kind: &str) -> bool {
    TYPE_NODES.contains(&kind)
}

fn is_function_node(kind: &str) -> bool {
    FUNCTION_NODES.contains(&kind)
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

/// CallEdge — chỉ giữ trường writer/resolve đọc.
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

/// NamespaceDef — swift walk không bao giờ emit (module system khác) nhưng
/// giữ row shape cho faithfulness như Python dataclass.
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

// ── Helpers (port từng hàm `_xxx` của Python) ───────────────────────────────

/// `_node_snippet` — text tính từ sau chuỗi leading comments, kèm start/end line.
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

/// `_tree_error_stats` — has_error = root.has_error OR count > 0.
fn tree_error_stats(root: tree_sitter::Node) -> (bool, i64) {
    let error_nodes = find_nodes_by_type(root, "ERROR").len() as i64;
    (root.has_error() || error_nodes > 0, error_nodes)
}

/// `_first_named_child` — phiên bản swift_analyzer CÓ lọc `is_named`.
fn first_named_child<'a>(
    node: tree_sitter::Node<'a>,
    kinds: &[&str],
) -> Option<tree_sitter::Node<'a>> {
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .find(|child| child.is_named() && kinds.contains(&child.kind()))
}

/// `_first_descendant` — DFS pre-order, lọc `is_named`.
fn first_descendant<'a>(node: tree_sitter::Node<'a>, kinds: &[&str]) -> Option<tree_sitter::Node<'a>> {
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.is_named() && kinds.contains(&child.kind()) {
            return Some(child);
        }
        if let Some(found) = first_descendant(child, kinds) {
            return Some(found);
        }
    }
    None
}

fn collapse_ws(text: &str) -> String {
    static WS_RE: OnceLock<Regex> = OnceLock::new();
    let ws_re = WS_RE.get_or_init(|| Regex::new(r"\s+").expect("ws regex"));
    ws_re.replace_all(text, " ").to_string()
}

/// `_clean_identifier` — collapse ws, lấy sau dấu "." cuối, strip backticks.
fn clean_identifier(text: &str) -> String {
    let mut text = collapse_ws(text).trim().to_string();
    if text.contains('.') {
        text = text.rsplit('.').next().unwrap_or("").to_string();
    }
    text.trim_matches('`').to_string()
}

/// `_extract_name` — name field (pattern/bound_identifier), bound field,
/// rồi identifier-like đầu tiên (direct child rồi descendant).
fn extract_name(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    const NAME_KINDS: [&str; 3] = ["type_identifier", "simple_identifier", "identifier"];
    if let Some(name_node) = node.child_by_field_name("name") {
        if name_node.kind() == "pattern"
            && let Some(bound) = name_node.child_by_field_name("bound_identifier")
        {
            return Some(node_text(bound, source).trim().to_string());
        }
        let text = node_text(name_node, source).trim().to_string();
        if !text.is_empty() {
            return Some(clean_identifier(&text));
        }
    }
    if let Some(bound) = node.child_by_field_name("bound_identifier") {
        return Some(node_text(bound, source).trim().to_string());
    }
    let found = match first_named_child(node, &NAME_KINDS) {
        Some(found) => Some(found),
        None => first_descendant(node, &NAME_KINDS),
    };
    found.map(|node| clean_identifier(node_text(node, source).trim()))
}

/// `_scope_name` — swift dùng "." (khác rust "::").
fn scope_name(scope_stack: &[String]) -> Option<String> {
    if scope_stack.is_empty() {
        None
    } else {
        Some(scope_stack.join("."))
    }
}

/// `_qualified_name`.
fn qualified_name(scope_stack: &[String], name: &str) -> String {
    if scope_stack.is_empty() {
        name.to_string()
    } else {
        format!("{}.{name}", scope_stack.join("."))
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

/// `_count_parameters` — đếm named children type "parameter".
fn count_parameters(node: tree_sitter::Node) -> i64 {
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .filter(|child| child.is_named() && child.kind() == "parameter")
        .count() as i64
}

/// `_top_level_argument_count` — đếm argument top-level (state machine string).
fn top_level_argument_count(text: &str) -> i64 {
    let text = text.trim();
    if text.is_empty() {
        return 0;
    }
    let mut depth = 0i64;
    let mut count = 1i64;
    let mut in_string: Option<char> = None;
    let mut escaped = false;
    for character in text.chars() {
        if let Some(quote) = in_string {
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == quote {
                in_string = None;
            }
            continue;
        }
        if character == '\'' || character == '"' {
            in_string = Some(character);
            continue;
        }
        if "([{<".contains(character) {
            depth += 1;
        } else if ")]}>".contains(character) {
            depth = (depth - 1).max(0);
        } else if character == ',' && depth == 0 {
            count += 1;
        }
    }
    count
}

/// `_count_arguments` — regex trên text call, fallback call_suffix.
fn count_arguments(node: tree_sitter::Node, source: &[u8]) -> i64 {
    static ARGS_RE: OnceLock<Regex> = OnceLock::new();
    static SUFFIX_RE: OnceLock<Regex> = OnceLock::new();
    let args_re = ARGS_RE.get_or_init(|| Regex::new(r"(?s)\((.*)\)\s*$").expect("args regex"));
    let suffix_re = SUFFIX_RE.get_or_init(|| Regex::new(r"(?s)\((.*)\)").expect("suffix regex"));
    let text = node_text(node, source);
    let captured = match args_re.captures(&text) {
        Some(caps) => caps.get(1).map(|m| m.as_str().to_string()),
        None => {
            let suffix = first_named_child(node, &["call_suffix", "constructor_suffix"]);
            match suffix {
                Some(suffix) => suffix_re
                    .captures(&node_text(suffix, source))
                    .and_then(|caps| caps.get(1).map(|m| m.as_str().to_string())),
                None => None,
            }
        }
    };
    captured
        .map(|group| top_level_argument_count(&group))
        .unwrap_or(0)
}

/// `_extract_type_signature`.
fn extract_type_signature(node: tree_sitter::Node, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    if let Some(annotation) = first_named_child(node, &["type_annotation"]) {
        let inner = annotation
            .child_by_field_name("type")
            .or_else(|| annotation.child_by_field_name("name"));
        if let Some(inner) = inner {
            return node_text(inner, source).trim().to_string();
        }
        let text = node_text(annotation, source).trim().to_string();
        if let Some((_, rest)) = text.split_once(':') {
            return rest.trim().to_string();
        }
    }
    let text = node_text(node, source).trim().to_string();
    if let Some((_, rest)) = text.split_once(':') {
        let rest = match rest.split_once('=') {
            Some((head, _)) => head,
            None => rest,
        };
        return rest.trim().to_string();
    }
    String::new()
}

/// `_extract_alias_target`.
fn extract_alias_target(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    static TARGET_RE: OnceLock<Regex> = OnceLock::new();
    if let Some(value_node) = node.child_by_field_name("value") {
        return Some(node_text(value_node, source).trim().to_string());
    }
    let text = node_text(node, source);
    let target_re =
        TARGET_RE.get_or_init(|| Regex::new(r"(?s)=\s*(.*)$").expect("alias target regex"));
    target_re.captures(&text).map(|caps| {
        let group = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        collapse_ws(group).trim().to_string()
    })
}

/// `_extract_decl_kind`.
fn extract_decl_kind(node: tree_sitter::Node, source: &[u8]) -> String {
    static KIND_RE: OnceLock<Regex> = OnceLock::new();
    if let Some(kind_node) = node.child_by_field_name("declaration_kind") {
        let text = node_text(kind_node, source).trim().to_string();
        if !text.is_empty() {
            return text;
        }
    }
    let text = node_text(node, source);
    let kind_re = KIND_RE.get_or_init(|| {
        Regex::new(r"\b(actor|class|struct|enum|extension|protocol)\b").expect("decl kind regex")
    });
    kind_re
        .captures(&text)
        .and_then(|caps| caps.get(1))
        .map(|m| m.as_str().to_string())
        .unwrap_or_else(|| "record".to_string())
}

/// `_normalize_type_kind`.
fn normalize_type_kind(kind: &str) -> String {
    match kind {
        "protocol" => "interface".to_string(),
        "actor" => "class".to_string(),
        "extension" => "record".to_string(),
        "class" | "struct" | "enum" => kind.to_string(),
        _ => "record".to_string(),
    }
}

/// `_collect_imports` — (using_namespaces sorted-dedup, using_imports theo thứ tự).
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> (Vec<String>, Vec<String>) {
    static IMPORT_RE: OnceLock<Regex> = OnceLock::new();
    let import_re = IMPORT_RE.get_or_init(|| {
        Regex::new(r"^import\s+(class|struct|enum|protocol|func|var|typealias)?\s*")
            .expect("import regex")
    });
    let mut using_imports: Vec<String> = Vec::new();
    let mut using_namespaces: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "import_declaration") {
        let mut cursor = node.walk();
        let identifiers: Vec<String> = node
            .children(&mut cursor)
            .filter(|child| child.is_named() && child.kind() == "identifier")
            .map(|child| node_text(child, source).trim().to_string())
            .collect();
        let mut path = identifiers
            .iter()
            .filter(|part| !part.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join(".");
        if path.is_empty() {
            let text = node_text(node, source).trim().to_string();
            path = import_re.replace(&text, "").trim().to_string();
        }
        if !path.is_empty() {
            using_imports.push(path.clone());
            let head = path.split('.').next().unwrap_or("").to_string();
            using_namespaces.push(head);
        }
    }
    let mut namespaces: Vec<String> = using_namespaces;
    namespaces.sort();
    namespaces.dedup();
    (namespaces, using_imports)
}

/// `_collect_macros` — macro_declaration rồi macro_invocation.
fn collect_macros(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut macros: Vec<String> = Vec::new();
    for node_type in ["macro_declaration", "macro_invocation"] {
        for node in find_nodes_by_type(root, node_type) {
            let name = extract_name(node, source).unwrap_or_else(|| call_name(node, source));
            if !name.is_empty() && !macros.contains(&name) {
                macros.push(name);
            }
        }
    }
    macros
}

/// `_call_name`.
fn call_name(call_node: tree_sitter::Node, source: &[u8]) -> String {
    static MACRO_RE: OnceLock<Regex> = OnceLock::new();
    if call_node.kind() == "macro_invocation" {
        let text = node_text(call_node, source).trim().to_string();
        let macro_re = MACRO_RE
            .get_or_init(|| Regex::new(r"^#?([A-Za-z_][A-Za-z0-9_]*)").expect("macro regex"));
        return macro_re
            .captures(&text)
            .and_then(|caps| caps.get(1))
            .map(|m| m.as_str().to_string())
            .unwrap_or_else(|| anonymous_name("Macro", call_node));
    }
    if call_node.kind() == "constructor_expression"
        && let Some(name) = extract_name(call_node, source)
        && !name.is_empty()
    {
        return name;
    }
    let mut text = node_text(call_node, source).trim().to_string();
    text = collapse_ws(&text);
    let mut before_paren = text.split('(').next().unwrap_or("").trim().to_string();
    before_paren = before_paren
        .rsplit('?')
        .next()
        .unwrap_or("")
        .rsplit('!')
        .next()
        .unwrap_or("")
        .to_string();
    if before_paren.contains('.') {
        before_paren = before_paren.rsplit('.').next().unwrap_or("").to_string();
    }
    let before_paren = before_paren
        .trim_matches(|c| c == '`' || c == '#')
        .to_string();
    if before_paren.is_empty() {
        anonymous_name("Call", call_node)
    } else {
        before_paren
    }
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

/// Active function context truyền xuống `_walk_tree`.
#[derive(Debug, Clone)]
struct ActiveFn {
    symbol_id: String,
    scope_name: Option<String>,
}

/// Registries + output lists của một file parse.
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
    type_registry: std::collections::HashMap<String, TypeDef>,
    external_types: HashSet<String>,
}

impl Walker {
    /// `_add_type_use` — rel_type truyền vào ("EXTENDS" cho inheritance,
    /// default "USES_TYPE"); relation ghi cho MỌI candidate dù type đã tồn tại.
    fn add_type_use(
        &mut self,
        owner_id: &str,
        owner_label: &str,
        type_text: &str,
        rel_type: &str,
    ) {
        static CHARS_RE: OnceLock<Regex> = OnceLock::new();
        static KEYWORDS_RE: OnceLock<Regex> = OnceLock::new();
        static SPLIT_RE: OnceLock<Regex> = OnceLock::new();
        let chars_re =
            CHARS_RE.get_or_init(|| Regex::new(r"[<>\[\](),:?!=&|]").expect("type-use chars"));
        let keywords_re = KEYWORDS_RE.get_or_init(|| {
            Regex::new(r"\b(any|some|inout|async|throws|rethrows|where|Self|self)\b")
                .expect("type-use keywords")
        });
        let split_re = SPLIT_RE.get_or_init(|| Regex::new(r"\s+|\.").expect("type-use split"));

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
            let type_exists = self.types.iter().any(|item| item.symbol_id == target_id);
            if !self.external_types.contains(&target_id) && !type_exists {
                self.external_types.insert(target_id.clone());
                self.types.push(TypeDef {
                    symbol_id: target_id.clone(),
                    qualified_name: candidate.to_string(),
                    name: candidate.to_string(),
                    kind: "external".to_string(),
                    file_path: self.rel_path.clone(),
                    start_line: 0,
                    end_line: 0,
                    code: candidate.to_string(),
                    comment: String::new(),
                });
            }
            record_relation(
                &mut self.relations,
                owner_id,
                owner_label,
                &target_id,
                "Type",
                rel_type,
                json!({}),
            );
        }
    }

    /// `_record_templates`.
    fn record_templates(
        &mut self,
        node: tree_sitter::Node,
        owner_id: &str,
        owner_label: &str,
    ) {
        for template_node in find_nodes_by_type(node, "type_parameters") {
            let template_id = format!(
                "template::{rel_path}:{}:{}",
                template_node.start_position().row + 1,
                template_node.end_position().row + 1,
                rel_path = self.rel_path,
            );
            let text = node_text(template_node, &self.source).trim().to_string();
            self.templates.push(TemplateDef {
                symbol_id: template_id.clone(),
                name: text.clone(),
                file_path: self.rel_path.clone(),
                start_line: template_node.start_position().row as i64 + 1,
                end_line: template_node.end_position().row as i64 + 1,
                code: text,
            });
            record_relation(
                &mut self.relations,
                &template_id,
                "Template",
                owner_id,
                owner_label,
                "TEMPLATES",
                json!({}),
            );
        }
    }

    /// Owner lookup của swift: owner_scope trong type_registry ⇒ Type, ngược
    /// lại namespace_id (LUÔN ghi relation nếu owner_scope non-empty).
    fn owner_endpoint(&self, owner_scope: &str) -> (String, &'static str) {
        if self.type_registry.contains_key(owner_scope) {
            (type_id(owner_scope), "Type")
        } else {
            (namespace_id(owner_scope), "Namespace")
        }
    }

    /// `_walk_tree`.
    fn walk(
        &mut self,
        node: tree_sitter::Node,
        scope_stack: &mut Vec<String>,
        active_function: Option<&ActiveFn>,
    ) {
        let kind = node.kind();

        // ── class/protocol (và class_declaration mang struct/enum/actor) ────
        if is_type_node(kind) {
            let name = extract_name(node, &self.source)
                .unwrap_or_else(|| anonymous_name("Type", node));
            let qualified = qualified_name(scope_stack, &name);
            let type_id = type_id(&qualified);
            let raw_kind = if kind == "protocol_declaration" {
                "protocol".to_string()
            } else {
                extract_decl_kind(node, &self.source)
            };
            let (snippet, start_line, end_line) = node_snippet(node, &self.source);
            let type_def = TypeDef {
                symbol_id: type_id.clone(),
                qualified_name: qualified,
                name: name.clone(),
                kind: normalize_type_kind(&raw_kind),
                file_path: self.rel_path.clone(),
                start_line,
                end_line,
                code: snippet,
                comment: extract_comment(node, &self.source),
            };
            if !self.type_registry.contains_key(&type_id) {
                self.type_registry.insert(type_id.clone(), type_def.clone());
                self.types.push(type_def);
            }
            if let Some(owner_scope) = scope_name(scope_stack) {
                let (owner_id, owner_label) = self.owner_endpoint(&owner_scope);
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
            for inherit in find_nodes_by_type(node, "inheritance_specifier") {
                let inherit_node = inherit
                    .child_by_field_name("inherits_from")
                    .unwrap_or(inherit);
                let text = node_text(inherit_node, &self.source);
                self.add_type_use(&type_id, "Type", &text, "EXTENDS");
            }
            self.record_templates(node, &type_id, "Type");
            scope_stack.push(name);
            let mut cursor = node.walk();
            let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
            for child in children {
                self.walk(child, scope_stack, active_function);
            }
            scope_stack.pop();
            return;
        }

        // ── function/init/deinit/subscript/protocol function ────────────────
        if is_function_node(kind) {
            let (name, fn_kind) = if kind == "init_declaration" {
                ("init".to_string(), "constructor".to_string())
            } else if kind == "deinit_declaration" {
                ("deinit".to_string(), "destructor".to_string())
            } else if kind == "subscript_declaration" {
                (
                    "subscript".to_string(),
                    if !scope_stack.is_empty() {
                        "method".to_string()
                    } else {
                        "function".to_string()
                    },
                )
            } else {
                let name = extract_name(node, &self.source)
                    .unwrap_or_else(|| anonymous_name("Function", node));
                let fn_kind = if kind == "protocol_function_declaration" {
                    "declaration".to_string()
                } else if !scope_stack.is_empty() {
                    "method".to_string()
                } else {
                    "function".to_string()
                };
                (name, fn_kind)
            };
            let arity = count_parameters(node);
            let qualified = qualified_name(scope_stack, &name);
            let func = FunctionDef {
                symbol_id: symbol_id(&qualified, arity, &self.rel_path),
                qualified_name: qualified,
                name,
                kind: fn_kind,
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
            if let Some(owner_scope) = scope_name(scope_stack) {
                let (owner_id, owner_label) = self.owner_endpoint(&owner_scope);
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
            if let Some(return_type) = node.child_by_field_name("return_type") {
                let text = node_text(return_type, &self.source);
                self.add_type_use(&func.symbol_id, "Function", &text, "USES_TYPE");
            }
            let mut cursor = node.walk();
            let parameters: Vec<String> = node
                .children(&mut cursor)
                .filter(|child| child.is_named() && child.kind() == "parameter")
                .map(|param| extract_type_signature(param, &self.source))
                .filter(|signature| !signature.is_empty())
                .collect();
            for param_type in parameters {
                self.add_type_use(&func.symbol_id, "Function", &param_type, "USES_TYPE");
            }
            let func_symbol_id = func.symbol_id.clone();
            self.functions.push(func);
            self.record_templates(node, &func_symbol_id, "Function");
            let active = ActiveFn {
                symbol_id: func_symbol_id,
                scope_name: scope_name(scope_stack),
            };
            let mut cursor = node.walk();
            let children: Vec<tree_sitter::Node> = node.children(&mut cursor).collect();
            for child in children {
                self.walk(child, scope_stack, Some(&active));
            }
            return;
        }

        // ── typealias/associatedtype (KHÔNG return — rơi xuống call/field) ──
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
            if let Some(owner_scope) = scope_name(scope_stack) {
                let (owner_id, owner_label) = self.owner_endpoint(&owner_scope);
                record_relation(
                    &mut self.relations,
                    &owner_id,
                    owner_label,
                    &alias_symbol_id,
                    "Alias",
                    "DECLARES",
                    json!({}),
                );
            }
            if let Some(target) = &target {
                record_relation(
                    &mut self.relations,
                    &alias_symbol_id,
                    "Alias",
                    &type_id(target),
                    "Type",
                    "ALIASES",
                    json!({}),
                );
                self.add_type_use(&alias_symbol_id, "Alias", target, "USES_TYPE");
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
                } else if kind == "constructor_expression" {
                    "constructor".to_string()
                } else {
                    "function".to_string()
                },
                call_arity: count_arguments(node, &self.source),
                callee_name,
                callee_id: None,
            });
        }

        // ── property declarations trong scope type (ngoài function) ─────────
        if active_function.is_none()
            && !scope_stack.is_empty()
            && (kind == "property_declaration" || kind == "protocol_property_declaration")
            && let Some(name) = extract_name(node, &self.source)
        {
            let owner = scope_name(scope_stack);
            let qualified = match &owner {
                Some(owner) => format!("{owner}.{name}"),
                None => name.clone(),
            };
            let field_id = format!("field::{qualified}@{}", self.rel_path);
            let type_signature = extract_type_signature(node, &self.source);
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
                self.add_type_use(&field_id, "Field", &type_signature, "USES_TYPE");
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

/// `parse_swift_file` — trả payload của một file .swift.
pub fn parse_swift_file(path: &Path, root: &Path) -> Result<FilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let source = std::fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_swift::LANGUAGE.into())
        .map_err(|error| format!("tree-sitter-swift language setup failed: {error}"))?;
    let tree = parser
        .parse(&source, None)
        .ok_or_else(|| format!("{}: tree-sitter parse returned None", path.display()))?;
    let root_node = tree.root_node();
    let (has_error, error_nodes) = tree_error_stats(root_node);
    let code = decode_ignore(&source);
    let comment = extract_file_comment(root_node, &source);
    let (using_namespaces, using_imports) = collect_imports(root_node, &source);
    let includes = using_imports.clone();
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
            "analyzer_swift_test_{}_{}",
            std::process::id(),
            unique
        ));
        std::fs::create_dir_all(&dir).expect("mkdir");
        let file = dir.join("sample.swift");
        std::fs::write(&file, code).expect("write");
        let payload = parse_swift_file(&file, &dir).expect("parse");
        let _ = std::fs::remove_file(&file);
        let _ = std::fs::remove_dir(&dir);
        payload
    }

    #[test]
    fn parses_class_protocol_methods_and_calls() {
        let payload = parse_str(
            "import Foundation\n\
             \n\
             protocol Greeter {\n\
             \x20 func greet(name: String) -> String\n\
             }\n\
             \n\
             /// Main service.\n\
             class Service: Greeter {\n\
             \x20 var label: String = \"svc\"\n\
             \x20 let count: Int = 0\n\
             \x20 init(label: String) {\n\
             \x20\x20\x20 self.label = label\n\
             \x20 }\n\
             \x20 func greet(name: String) -> String {\n\
             \x20\x20\x20 return build(name)\n\
             \x20 }\n\
             \x20 private func build(_ name: String) -> String {\n\
             \x20\x20\x20 return \"hi \\(name)\"\n\
             \x20 }\n\
             }\n\
             \n\
             typealias Handler = (String) -> Int\n",
        );
        assert_eq!(payload.file_def.file_path, "sample.swift");
        assert_eq!(payload.file_def.using_imports, vec!["Foundation"]);
        // Greeter: protocol → interface; Service: class.
        let kinds: Vec<(String, String)> = payload
            .types
            .iter()
            .map(|t| (t.name.clone(), t.kind.clone()))
            .collect();
        assert!(kinds.contains(&("Greeter".to_string(), "interface".to_string())));
        assert!(kinds.contains(&("Service".to_string(), "class".to_string())));
        // fields: label + count (property_declaration, ngoài function).
        assert_eq!(payload.fields.len(), 2);
        // functions: protocol greet (declaration) + init + greet (method) + build (method).
        let fns: Vec<(String, String)> = payload
            .functions
            .iter()
            .map(|f| (f.name.clone(), f.kind.clone()))
            .collect();
        assert!(fns.contains(&("greet".to_string(), "declaration".to_string())));
        assert!(fns.contains(&("init".to_string(), "constructor".to_string())));
        assert!(fns.contains(&("greet".to_string(), "method".to_string())));
        assert!(fns.contains(&("build".to_string(), "method".to_string())));
        // typealias → AliasDef; target giữ nguyên.
        assert_eq!(payload.aliases.len(), 1);
        // call `build(name)` resolve được qua by_name (arity khác nên rơi
        // xuống by_name); `String(...)` constructor_expression.
        assert!(payload
            .relations
            .iter()
            .any(|rel| rel.rel_type == "POSSIBLE_CALLS"));
    }

    #[test]
    fn parses_enum_extends_and_templates() {
        let code = concat!(
            "class Box<T>: CustomStringConvertible, Hashable where T: Equatable {\n",
            "    enum Kind {\n",
            "        case small\n",
            "        case large\n",
            "    }\n",
            "    var kind: Kind = .small\n",
            "    func describe() -> String { \"box\" }\n",
            "}\n",
        );
        let payload = parse_str(code);
        // Box + Kind(enum) + externals: CustomStringConvertible, Hashable, Equatable,
        // String (return type).
        assert!(payload.types.iter().any(|t| t.name == "Box"));
        assert!(payload
            .types
            .iter()
            .any(|t| t.name == "Kind" && t.kind == "enum"));
        assert!(payload
            .relations
            .iter()
            .any(|rel| rel.rel_type == "EXTENDS" && rel.target_id == "CustomStringConvertible"));
        assert!(!payload.templates.is_empty());
        assert!(payload
            .relations
            .iter()
            .any(|rel| rel.rel_type == "TEMPLATES"));
        // field `kind: Kind` với type_signature "Kind".
        assert!(payload
            .fields
            .iter()
            .any(|f| f.name == "kind" && f.type_signature == "Kind"));
    }
}

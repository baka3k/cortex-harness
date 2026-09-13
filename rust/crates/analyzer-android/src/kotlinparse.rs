//! Port phần parse của `android_kotlin_analyzer.py`: `parse_kotlin_file` +
//! helpers (`_node_snippet` với comment kinds kotlin line/block/multiline,
//! `_kotlin_api_visibility`, super types theo TEXT, callable references,
//! compose routes từ tree + events Android: Intent/Broadcast/Handler).
//!
//! Ghi chú parity quan trọng (giữ nguyên semantics "regex double-backslash"
//! của bản Python — nhiều pattern là `\\s` = backslash literal + `s*` nên
//! thực tế không match text thường; port giữ y nguyên từng chữ):
//! * `_collect_imports` (`import_header`) — grammar tree-sitter-grammars 1.1.0
//!   không có node này (node là `import` trần) ⇒ imports luôn `[]` (như Python
//!   chạy với wheel PyPI 1.1.0).
//! * `_count_parameters` — grammar không expose field `parameters` ⇒ arity 0.
//! * `_extract_compose_routes_from_tree` nhánh `content\s*=\s*{...(` là pattern
//!   LỖI cú pháp ở Python (unbalanced group → `re.error` khi composable không
//!   có target từ lambda ⇒ Python crash, không có graph). Rust giữ pattern gốc
//!   nhưng compile `.ok()` ⇒ no-match và tiếp tục — khác biệt duy nhất nằm ở
//!   inputs mà Python không chạy được (xem phase06-android-parity.md).

use serde_json::{Map, Value};

use cortex_analyzer_framework::scan::rel_posix;
use cortex_analyzer_framework::ts::{decode_ignore, find_nodes_by_type, line_from_byte, node_text};

pub type Row = Map<String, Value>;

const KOTLIN_COMMENT_KINDS: [&str; 3] = ["line_comment", "block_comment", "multiline_comment"];

fn is_kotlin_comment(kind: &str) -> bool {
    KOTLIN_COMMENT_KINDS.contains(&kind)
}

/// `_find_nodes_by_types` — một cursor walk pre-order, filter theo set kind.
fn find_nodes_by_types<'t>(root: Node<'t>, kinds: &[&str]) -> Vec<Node<'t>> {
    let mut found = Vec::new();
    let mut cursor = root.walk();
    loop {
        if kinds.contains(&cursor.node().kind()) {
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

use tree_sitter::Node;

// ── Data defs (asdict shape khớp payload Python) ────────────────────────────

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
    pub caller_file: String,
    pub caller_package: Option<String>,
    pub caller_class: Option<String>,
    pub imports: Vec<String>,
    pub call_line: i64,
    pub call_column: i64,
    pub call_type: String,
    pub callee_name: String,
    pub callee_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct AndroidEvent {
    pub event_type: String,
    pub function_id: String,
    pub actions: Vec<String>,
    pub targets: Vec<String>,
    pub receiver: Option<String>,
    pub tokens: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct ComposeRoutes {
    pub routes: Vec<String>,
    pub start_routes: Vec<String>,
    /// (route, targets)
    pub route_targets: Vec<(String, Vec<String>)>,
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
    pub compose_routes: ComposeRoutes,
    pub android_events: Vec<AndroidEvent>,
    pub has_error: bool,
    pub error_nodes: i64,
}

// ── Regex singletons ────────────────────────────────────────────────────────

macro_rules! static_re {
    ($fn_name:ident, $pattern:expr) => {
        fn $fn_name() -> &'static regex::Regex {
            static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
            RE.get_or_init(|| regex::Regex::new($pattern).expect("static regex"))
        }
    };
}

// `_kotlin_api_visibility` — word-boundary vis check (đúng).
static_re!(vis_private_re, r"\bprivate\b");
static_re!(vis_internal_re, r"\binternal\b");
static_re!(vis_protected_re, r"\bprotected\b");
static_re!(vis_public_re, r"\bpublic\b");

// `_normalize_callee` — generics strip (đúng).
static_re!(generics_re, r"<.*?>");

// `_extract_super_types` (đúng).
static_re!(paren_re, r"\(.*?\)");
static_re!(by_re, r"\s+by\s+");
static_re!(type_name_re, r"[A-Za-z_][A-Za-z0-9_\.]*");

// `_iter_callable_reference_matches` (đúng, named groups).
static_re!(
    callable_reference_re,
    r"(?P<qual>[A-Za-z_][A-Za-z0-9_.]*)?\s*::\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
);

// `_collect_imports` — per-line match (đúng; chỉ chạy nếu grammar có
// `import_header` — wheel 1.1.0 thì không).
static_re!(
    import_line_re,
    r"^\s*import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?\s*$"
);

// ── `_extract_string_literals` (bản kotlin: KHÔNG dedup, KHÔNG unescape) ────

fn triple_quote_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r#"(?s)"""(.*?)""""#).expect("triple quote re"))
}
static_re!(double_quote_re, r#""([^"\\\\]*(?:\\\\.[^"\\\\]*)*)""#);
static_re!(single_quote_re, r#"'([^'\\\\]*(?:\\\\.[^'\\\\]*)*)'"#);

// ── Compose text fallback `_parse_compose_routes` (regex đúng) ──────────────

static_re!(route_args_re, r#"route\s*=\s*['\"]([^'\"]+)['\"]"#);
static_re!(any_quote_re, r#"['\"]([^'\"]+)['\"]"#);
static_re!(call_name_re, r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(");
static_re!(content_lambda_re, r"content\s*=\s*\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\(");
static_re!(content_ref_re, r"content\s*=\s*::\s*([A-Za-z_][A-Za-z0-9_.]*)");

fn compose_block_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"(?s)\bcomposable\s*\((.*?)\)\s*\{").expect("compose re"))
}

fn navhost_start_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(r#"(?s)\bNavHost\s*\(.*?startDestination\s*=\s*['"]([^'"]+)['"]"#)
            .expect("navhost re")
    })
}

// ── Events — pattern "broken" (double-backslash) sao chép nguyên chữ ────────

static_re!(class_refs_broken_re, r"([A-Za-z_][A-Za-z0-9_\\.]*)::class\\.java");
static_re!(handler_upper_re, r"\\b[A-Z][A-Z0-9_]{2,}\\b");
static_re!(handler_digits_re, r"\\b\\d+\\b");
static_re!(
    register_receiver_re,
    r"registerReceiver\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,"
);
static_re!(
    register_receiver_single_re,
    r"registerReceiver\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)"
);
static_re!(addaction_broken_re, r#"addAction\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)"#);
static_re!(
    intentfilter_var_broken_re,
    r"\\b(?:val|var)\\s+(\\w+)\\s*=\\s*IntentFilter\\b(\\s*\\(([^\\)]*)\\))?"
);
static_re!(
    component_name_broken_re,
    r#"ComponentName\\s*\\([^\\)]*[\"']([^\"']+)[\"']\\s*\\)"#
);
static_re!(
    set_class_name_broken_re,
    r#"setClassName\\s*\\([^,]*,\\s*[\"']([^\"']+)[\"']\\s*\\)"#
);
static_re!(
    set_component_broken_re,
    r#"setComponent\\s*\\(\\s*ComponentName\\s*\\([^\\)]*[\"']([^\"']+)[\"']\\s*\\)\\s*\\)"#
);
static_re!(
    intent_var_broken_re,
    r"\\b(?:val|var)\\s+(\\w+)\\s*=\\s*Intent\\s*\\(([^\\)]*)\\)"
);
static_re!(
    set_action_broken_re,
    r#"\\b(\\w+)\\.setAction\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)"#
);
static_re!(action_assign_broken_re, r#"\\b(\\w+)\\.action\\s*=\\s*[\"']([^\"']+)[\"']\\s*"#);
static_re!(
    set_class_name_member_broken_re,
    r#"\\b(\\w+)\\.setClassName\\s*\\([^,]*,\\s*[\"']([^\"']+)[\"']\\s*\\)"#
);
static_re!(
    set_component_member_broken_re,
    r#"\\b(\\w+)\\.setComponent\\s*\\(\\s*ComponentName\\s*\\([^\\)]*[\"']([^\"']+)[\"']\\s*\\)\\s*\\)"#
);

// ── Compose tree — pattern broken (copy chữ) + pattern lỗi cú pháp ──────────

static_re!(route_args_broken_re, r#"route\\s*=\\s*[\"']([^\"']+)[\"']"#);
static_re!(arg_quote_re, r#"[\"']([^\"']+)[\"']"#);
static_re!(start_destination_broken_re, r#"startDestination\\s*=\\s*[\"']([^\"']+)[\"']"#);
static_re!(
    content_ref_broken_re,
    r"content\\s*=\\s*::\\s*([A-Za-z_][A-Za-z0-9_\\.]*)"
);

/// Pattern LỖI ở Python (unbalanced `(` → re.error khi compile). Giữ chuỗi
/// gốc; compile `.ok()` ⇒ None ⇒ no-match (Python crash ở inputs này).
#[allow(clippy::invalid_regex)] // pattern lỗi cú pháp cố tình giữ nguyên chữ
fn content_tree_broken_re() -> Option<&'static regex::Regex> {
    static RE: std::sync::OnceLock<Option<regex::Regex>> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(r"content\\s*=\\s*\\{\\s*([A-Za-z_][A-Za-z0-9_\\.]*)\\s*\\(").ok()
    })
    .as_ref()
}

// ── Helpers port từng hàm `_` ───────────────────────────────────────────────

/// `_kotlin_api_visibility`.
pub fn kotlin_api_visibility(snippet: &str) -> (String, bool, String) {
    let header = snippet.split('{').next().unwrap_or("");
    let checks: [(&regex::Regex, &str); 4] = [
        (vis_private_re(), "private"),
        (vis_internal_re(), "internal"),
        (vis_protected_re(), "protected"),
        (vis_public_re(), "public"),
    ];
    for (re, visibility) in checks {
        if re.is_match(header) {
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

/// `_source_signature` — header trước `{` đầu, whitespace thu gọn, cắt 500.
pub fn source_signature(snippet: &str) -> String {
    let header = snippet.split('{').next().unwrap_or("");
    let collapsed = cortex_analyzer_framework::ts::normalize_ws(header);
    collapsed.chars().take(500).collect()
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

/// `_extract_identifiers` — split trên [^A-Za-z0-9_]+, bỏ phần rỗng.
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

/// `_line_col_from_byte` — (1-based line, 1-based column).
fn line_col_from_byte(source: &[u8], byte_index: usize) -> (usize, usize) {
    let line = line_from_byte(source, byte_index);
    let last_newline = source[..byte_index].iter().rposition(|&b| b == b'\n');
    let column = match last_newline {
        None => byte_index + 1,
        Some(pos) => byte_index - pos,
    };
    (line, column)
}

/// `_node_snippet` — kotlin comment kinds.
fn node_snippet(node: Node, source: &[u8]) -> (String, usize, usize) {
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

/// `_extract_leading_comment`.
fn extract_leading_comment(node: Node, source: &[u8]) -> String {
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

/// `_extract_file_comment`.
fn extract_file_comment(root: Node, source: &[u8]) -> String {
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

/// `_first_identifier` — simple_identifier/identifier/type_identifier đầu tiên.
fn first_identifier(node: Node, source: &[u8]) -> Option<String> {
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

/// `_tree_error_stats`.
fn tree_error_stats(root: Node) -> (bool, i64) {
    let has_error = root.has_error();
    let error_nodes = find_nodes_by_type(root, "ERROR").len() as i64;
    (has_error, error_nodes)
}

/// `_collect_package_info`.
#[allow(clippy::type_complexity)]
fn collect_package_info(root: Node, source: &[u8]) -> (Option<String>, usize, usize, String, String) {
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
                start_line,
                end_line,
                snippet,
                comment,
            );
        }
    }
    (None, 0, 0, String::new(), String::new())
}

/// `_collect_imports` — chỉ có node khi grammar expose `import_header`.
fn collect_imports(root: Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_header") {
        let text = node_text(node, source);
        for line in text.lines() {
            if let Some(caps) = import_line_re().captures(line.trim()) {
                imports.push(caps[1].to_string());
            }
        }
    }
    imports
}

/// `_count_parameters` — field `parameters` (grammar 1.1.0 không expose ⇒ 0).
fn count_parameters(function_node: Node) -> i64 {
    let Some(param_list) = function_node.child_by_field_name("parameters") else {
        return 0;
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "parameter")
        .count() as i64
}

/// `_iter_function_parameters`.
fn iter_function_parameters(function_node: Node) -> Vec<Node> {
    let Some(param_list) = function_node.child_by_field_name("parameters") else {
        return Vec::new();
    };
    param_list
        .children(&mut param_list.walk())
        .filter(|child| child.kind() == "parameter")
        .collect()
}

/// `_extract_function_name` / `_extract_class_name` — name field, fallback.
fn extract_node_name(node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(node, source)
}

/// `_normalize_callee`.
fn normalize_callee(text: &str) -> String {
    let mut callee = strip_outer_call_args(text);
    callee = callee.replace("?.", ".").replace("::", ".");
    callee = generics_re().replace_all(&callee, "").to_string();
    callee = callee.trim_matches(|c| c == ' ' || c == '.').to_string();
    if callee.contains('(') || callee.contains(')') {
        // Receiver chain lồng call: A.b(...).c -> c
        let tail = callee.rsplit('.').next().unwrap_or("").trim();
        if !tail.is_empty() {
            return tail.to_string();
        }
    }
    callee
}

/// `_strip_outer_call_args`.
fn strip_outer_call_args(text: &str) -> String {
    let raw = text.trim();
    if !raw.ends_with(')') {
        return raw.to_string();
    }
    let bytes = raw.as_bytes();
    let mut depth: i64 = 0;
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

/// `_iter_callable_reference_matches` — (callee, match.start()).
fn iter_callable_reference_matches(text: &str) -> Vec<(String, usize)> {
    let mut out = Vec::new();
    for m in callable_reference_re().captures_iter(text) {
        let whole_start = m.get(0).unwrap().start();
        let name = m.name("name").map(|s| s.as_str()).unwrap_or_default();
        if name == "class" {
            continue;
        }
        let qualifier = m.name("qual").map(|s| s.as_str().to_string());
        let callee = match qualifier {
            Some(qualifier) => {
                let qualifier = generics_re().replace_all(&qualifier, "").to_string();
                let qualifier = qualifier
                    .trim_matches(|c| c == ' ' || c == '.')
                    .to_string();
                if qualifier.is_empty() {
                    name.to_string()
                } else {
                    format!("{qualifier}.{name}")
                }
            }
            None => name.to_string(),
        };
        if !callee.is_empty() {
            out.push((callee, whole_start));
        }
    }
    out
}

/// `_extract_call_name`.
fn extract_call_name(call_node: Node, source: &[u8]) -> Option<String> {
    if let Some(function_node) = call_node.child_by_field_name("function") {
        if function_node.kind() == "navigation_expression"
            && has_descendant_type(function_node, "call_expression")
        {
            // Call chain A.b(...).c(...): ưu tiên callee cuối "c".
            if let Some(tail) = rightmost_identifier(function_node, source) {
                return Some(tail);
            }
        }
        let text = node_text(function_node, source);
        return Some(normalize_callee(&text));
    }
    let text = node_text(call_node, source);
    Some(normalize_callee(&text))
}

/// `_rightmost_identifier`.
fn rightmost_identifier(node: Node, source: &[u8]) -> Option<String> {
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
    let children: Vec<Node> = node.children(&mut node.walk()).collect();
    for child in children.into_iter().rev() {
        if let Some(found) = rightmost_identifier(child, source) {
            return Some(found);
        }
    }
    None
}

/// `_has_descendant_type`.
fn has_descendant_type(node: Node, target_type: &str) -> bool {
    for child in node.children(&mut node.walk()) {
        if child.kind() == target_type {
            return true;
        }
        if has_descendant_type(child, target_type) {
            return true;
        }
    }
    false
}

/// `_extract_parameter_info` — (name, type_text, (start, end, snippet)).
type ParamInfo = (Option<String>, Option<String>, Option<(usize, usize, String)>);

fn extract_parameter_info(param_node: Node, source: &[u8]) -> ParamInfo {
    let name = param_node
        .child_by_field_name("name")
        .map(|n| node_text(n, source));
    let type_node = param_node.child_by_field_name("type");
    let type_text = type_node.map(|n| node_text(n, source));
    if let Some(type_node) = type_node {
        let (snippet, start_line, end_line) = node_snippet(type_node, source);
        return (name, type_text, Some((start_line, end_line, snippet)));
    }
    (name, type_text, None)
}

// ── Symbol ids ──────────────────────────────────────────────────────────────

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
    format!("{}/{}@{}", parts.join("."), arity, rel_path)
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

/// `_class_kind`.
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

/// `_find_child` — con trực tiếp đầu tiên đúng kind.
fn find_child<'t>(node: Node<'t>, node_type: &str) -> Option<Node<'t>> {
    node.children(&mut node.walk())
        .find(|child| child.kind() == node_type)
}

/// `_extract_type_name`.
fn extract_type_name(text: &str) -> Option<String> {
    type_name_re().find(text).map(|m| m.as_str().to_string())
}

/// `_extract_super_types` — TEXT-based: sau ":" đầu, cắt tại `{`/`where`/newline.
fn extract_super_types(class_node: Node, source: &[u8]) -> Vec<String> {
    let text = node_text(class_node, source);
    let Some((_, after)) = text.split_once(':') else {
        return Vec::new();
    };
    let mut stop_at = after.len();
    for token in ["{", "where", "\n"] {
        if let Some(pos) = after.find(token) {
            stop_at = stop_at.min(pos);
        }
    }
    let segment = &after[..stop_at];
    let mut results: Vec<String> = Vec::new();
    for part in segment.split(',') {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let part = generics_re().replace_all(part, "").to_string();
        let part = paren_re().replace_all(&part, "").to_string();
        let part = by_re().split(&part).next().unwrap_or("").to_string();
        if let Some(name) = extract_type_name(&part) {
            results.push(name);
        }
    }
    results
}

/// `_iter_type_decls` — LIFO stack (con cuối pop trước) như generator Python.
fn iter_type_decls<'t>(root: Node<'t>, source: &[u8]) -> Vec<(Node<'t>, String, &'static str)> {
    let mut out = Vec::new();
    let mut stack: Vec<(Node<'t>, Vec<String>)> = vec![(root, Vec::new())];
    while let Some((node, class_stack)) = stack.pop() {
        if let Some(kind) = class_kind(node.kind()) {
            let class_name = extract_node_name(node, source);
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

/// `_iter_functions` — LIFO stack, yield (function_declaration, active_class).
fn iter_functions<'t>(root: Node<'t>, source: &[u8]) -> Vec<(Node<'t>, Option<String>)> {
    let mut out = Vec::new();
    let mut stack: Vec<(Node<'t>, Vec<String>)> = vec![(root, Vec::new())];
    while let Some((node, class_stack)) = stack.pop() {
        if class_kind(node.kind()).is_some() {
            let class_name = extract_node_name(node, source);
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

/// `_iter_calls`.
fn iter_calls(function_node: Node) -> Vec<Node> {
    find_nodes_by_type(function_node, "call_expression")
}

// ── String/class refs + handler tokens ──────────────────────────────────────

/// `_extract_string_literals` (bản kotlin analyzer — thứ tự: triple, double, single).
fn extract_string_literals(text: &str) -> Vec<String> {
    let mut literals: Vec<String> = Vec::new();
    for caps in triple_quote_re().captures_iter(text) {
        literals.push(caps[1].to_string());
    }
    for caps in double_quote_re().captures_iter(text) {
        literals.push(caps[1].to_string());
    }
    for caps in single_quote_re().captures_iter(text) {
        literals.push(caps[1].to_string());
    }
    literals
}

/// `_extract_class_refs` (bản kotlin analyzer — regex broken giữ nguyên).
fn extract_class_refs(text: &str) -> Vec<String> {
    class_refs_broken_re()
        .captures_iter(text)
        .map(|caps| caps[1].to_string())
        .collect()
}

/// `_extract_handler_tokens` (regex broken giữ nguyên).
fn extract_handler_tokens(text: &str) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    for caps in handler_upper_re().captures_iter(text) {
        tokens.push(caps[0].to_string());
    }
    for caps in handler_digits_re().captures_iter(text) {
        tokens.push(caps[0].to_string());
    }
    tokens
}

/// `_extract_register_receiver_target` (bản kotlin analyzer — regex đúng).
fn extract_register_receiver_target(text: &str) -> Option<String> {
    if let Some(caps) = register_receiver_re().captures(text) {
        return Some(caps[1].to_string());
    }
    if let Some(caps) = register_receiver_single_re().captures(text) {
        return Some(caps[1].to_string());
    }
    None
}

/// `_extract_intentfilter_actions` (regex broken giữ nguyên).
fn extract_intentfilter_actions(text: &str) -> Vec<String> {
    addaction_broken_re()
        .captures_iter(text)
        .map(|caps| caps[1].to_string())
        .collect()
}

/// `_extract_intentfilter_var_actions` (regex broken giữ nguyên).
fn extract_intentfilter_var_actions(text: &str) -> Vec<String> {
    let mut actions: Vec<String> = Vec::new();
    for caps in intentfilter_var_broken_re().captures_iter(text) {
        let var_name = caps[1].to_string();
        let ctor_args = caps.get(3).map(|m| m.as_str()).unwrap_or("");
        for action in extract_string_literals(ctor_args) {
            actions.push(action);
        }
        let pattern = regex::Regex::new(&format!(
            r#"\\b{}\\.addAction\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)"#,
            regex::escape(&var_name)
        ))
        .ok();
        if let Some(pattern) = pattern {
            for action_match in pattern.captures_iter(text) {
                actions.push(action_match[1].to_string());
            }
        }
    }
    actions
}

/// `_extract_component_name_target` (regex broken giữ nguyên).
fn extract_component_name_target(text: &str) -> Option<String> {
    if let Some(caps) = component_name_broken_re().captures(text) {
        return Some(caps[1].to_string());
    }
    if let Some(caps) = set_class_name_broken_re().captures(text) {
        return Some(caps[1].to_string());
    }
    if let Some(caps) = set_component_broken_re().captures(text) {
        return Some(caps[1].to_string());
    }
    None
}

/// `_call_argument_node`.
fn call_argument_node<'t>(call_node: Node<'t>) -> Option<Node<'t>> {
    for child in call_node.children(&mut call_node.walk()) {
        if child.kind() == "value_arguments" || child.kind() == "argument_list" {
            return Some(child);
        }
    }
    call_node
        .child_by_field_name("arguments")
        .or_else(|| call_node.child_by_field_name("value_arguments"))
}

/// `_extract_route_from_args` (regex đầu broken, fallback đúng).
fn extract_route_from_args(arg_text: &str) -> Option<String> {
    if arg_text.is_empty() {
        return None;
    }
    if let Some(caps) = route_args_broken_re().captures(arg_text) {
        return Some(caps[1].to_string());
    }
    if let Some(caps) = arg_quote_re().captures(arg_text) {
        return Some(caps[1].to_string());
    }
    None
}

/// `_find_first_lambda_literal`.
fn find_first_lambda_literal<'t>(node: Node<'t>) -> Option<Node<'t>> {
    for child in node.children(&mut node.walk()) {
        if child.kind() == "lambda_literal" || child.kind() == "lambda_expression" {
            return Some(child);
        }
        if let Some(result) = find_first_lambda_literal(child) {
            return Some(result);
        }
    }
    None
}

/// `_iter_call_expressions_in_node` — stack DFS pre-order (children reversed push).
fn iter_call_expressions_in_node<'t>(node: Node<'t>) -> Vec<Node<'t>> {
    let mut out = Vec::new();
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        if current.kind() == "call_expression" {
            out.push(current);
        }
        let children: Vec<Node> = current.children(&mut current.walk()).collect();
        for child in children.into_iter().rev() {
            stack.push(child);
        }
    }
    out
}

/// `_collect_android_events`.
fn collect_android_events(function_node: Node, source: &[u8], function_id: &str) -> Vec<AndroidEvent> {
    let mut events: Vec<AndroidEvent> = Vec::new();
    let function_text = node_text(function_node, source);
    let filter_actions_by_var = extract_intentfilter_var_actions(&function_text);
    let filter_actions_inline = extract_intentfilter_actions(&function_text);
    // intent_vars — dict insertion order: (name, actions, targets).
    let mut intent_vars: Vec<(String, Vec<String>, Vec<String>)> = Vec::new();

    for caps in intent_var_broken_re().captures_iter(&function_text) {
        let var_name = caps[1].to_string();
        let args_text = caps.get(2).map(|m| m.as_str()).unwrap_or("");
        if let Some((actions, targets)) = intent_vars
            .iter_mut()
            .find(|(name, _, _)| *name == var_name)
            .map(|(_, actions, targets)| (actions, targets))
        {
            actions.extend(extract_string_literals(args_text));
            targets.extend(extract_class_refs(args_text));
        } else {
            let mut targets = extract_class_refs(args_text);
            if let Some(component_target) = extract_component_name_target(args_text) {
                targets.push(component_target);
            }
            intent_vars.push((var_name, extract_string_literals(args_text), targets));
        }
    }
    for caps in set_action_broken_re().captures_iter(&function_text) {
        push_intent_action(&mut intent_vars, caps[1].to_string(), caps[2].to_string());
    }
    for caps in action_assign_broken_re().captures_iter(&function_text) {
        push_intent_action(&mut intent_vars, caps[1].to_string(), caps[2].to_string());
    }
    for caps in set_class_name_member_broken_re().captures_iter(&function_text) {
        push_intent_target(&mut intent_vars, caps[1].to_string(), caps[2].to_string());
    }
    for caps in set_component_member_broken_re().captures_iter(&function_text) {
        push_intent_target(&mut intent_vars, caps[1].to_string(), caps[2].to_string());
    }

    for call_node in iter_calls(function_node) {
        let Some(callee) = extract_call_name(call_node, source) else {
            continue;
        };
        let short_name = callee.rsplit('.').next().unwrap_or(&callee).to_string();
        let args_node = call_argument_node(call_node);
        let args_text = args_node
            .map(|node| node_text(node, source))
            .unwrap_or_default();
        let mut implied_actions: Vec<String> = Vec::new();
        let mut implied_targets: Vec<String> = Vec::new();
        for (var_name, meta_actions, meta_targets) in &intent_vars {
            // Python: re.search(rf"\\b{escape(var)}\\b", args_text) — broken
            // pattern (backslash literal + 'b'), copy cùng semantics.
            let word_re =
                regex::Regex::new(&format!(r"\\b{}\\b", regex::escape(var_name))).ok();
            if let Some(word_re) = word_re
                && word_re.is_match(&args_text)
            {
                implied_actions.extend(meta_actions.iter().cloned());
                implied_targets.extend(meta_targets.iter().cloned());
            }
        }
        if matches!(
            short_name.as_str(),
            "startActivity" | "startActivityForResult" | "startService" | "startForegroundService"
        ) {
            let mut actions = extract_string_literals(&args_text);
            actions.extend(implied_actions.iter().cloned());
            let mut targets = extract_class_refs(&args_text);
            targets.extend(implied_targets.iter().cloned());
            events.push(AndroidEvent {
                event_type: "start_component".to_string(),
                function_id: function_id.to_string(),
                actions,
                targets,
                receiver: None,
                tokens: Vec::new(),
            });
        } else if matches!(
            short_name.as_str(),
            "sendBroadcast" | "sendOrderedBroadcast" | "sendStickyBroadcast"
        ) {
            let mut actions = extract_string_literals(&args_text);
            actions.extend(implied_actions.iter().cloned());
            let mut targets = extract_class_refs(&args_text);
            targets.extend(implied_targets.iter().cloned());
            events.push(AndroidEvent {
                event_type: "send_broadcast".to_string(),
                function_id: function_id.to_string(),
                actions,
                targets,
                receiver: None,
                tokens: Vec::new(),
            });
        } else if short_name == "registerReceiver" {
            let receiver_target = extract_register_receiver_target(&args_text);
            let filter_actions = extract_intentfilter_actions(&args_text);
            let mut all_actions = extract_string_literals(&args_text);
            if !filter_actions.is_empty() {
                for action in &filter_actions {
                    if !all_actions.contains(action) {
                        all_actions.push(action.clone());
                    }
                }
            }
            for action in &filter_actions_by_var {
                if !all_actions.contains(action) {
                    all_actions.push(action.clone());
                }
            }
            for action in &filter_actions_inline {
                if !all_actions.contains(action) {
                    all_actions.push(action.clone());
                }
            }
            events.push(AndroidEvent {
                event_type: "register_receiver".to_string(),
                function_id: function_id.to_string(),
                actions: all_actions,
                targets: extract_class_refs(&args_text),
                receiver: receiver_target,
                tokens: Vec::new(),
            });
        } else if matches!(
            short_name.as_str(),
            "sendMessage"
                | "sendEmptyMessage"
                | "sendEmptyMessageDelayed"
                | "sendMessageDelayed"
                | "post"
                | "postDelayed"
        ) {
            events.push(AndroidEvent {
                event_type: "handler_message".to_string(),
                function_id: function_id.to_string(),
                actions: Vec::new(),
                targets: Vec::new(),
                receiver: None,
                tokens: extract_handler_tokens(&args_text),
            });
        }
        if matches!(
            short_name.as_str(),
            "startActivity"
                | "startActivityForResult"
                | "startService"
                | "startForegroundService"
                | "sendBroadcast"
                | "sendOrderedBroadcast"
                | "sendStickyBroadcast"
        ) && let Some(component_target) = extract_component_name_target(&args_text)
    {
        events.push(AndroidEvent {
            event_type: "start_component".to_string(),
            function_id: function_id.to_string(),
            actions: Vec::new(),
            targets: vec![component_target],
            receiver: None,
            tokens: Vec::new(),
        });
        }
    }
    events
}

fn push_intent_action(
    intent_vars: &mut Vec<(String, Vec<String>, Vec<String>)>,
    var_name: String,
    action: String,
) {
    if let Some((_, actions, _)) = intent_vars
        .iter_mut()
        .find(|(name, _, _)| *name == var_name)
    {
        if !actions.contains(&action) {
            actions.push(action);
        }
    } else {
        intent_vars.push((var_name, vec![action], Vec::new()));
    }
}

fn push_intent_target(
    intent_vars: &mut Vec<(String, Vec<String>, Vec<String>)>,
    var_name: String,
    target: String,
) {
    if let Some((_, _, targets)) = intent_vars
        .iter_mut()
        .find(|(name, _, _)| *name == var_name)
    {
        if !targets.contains(&target) {
            targets.push(target);
        }
    } else {
        intent_vars.push((var_name, Vec::new(), vec![target]));
    }
}

/// (routes, start_routes, route_targets).
pub type ComposeTriple = (Vec<String>, Vec<String>, Vec<(String, Vec<String>)>);

/// `_parse_compose_routes` — fallback scan TEXT (regex đúng, index theo char).
pub fn parse_compose_routes(code: &str) -> ComposeTriple {
    if code.is_empty() {
        return (Vec::new(), Vec::new(), Vec::new());
    }
    let mut routes: Vec<String> = Vec::new();
    let mut start_routes: Vec<String> = Vec::new();
    let mut route_targets: Vec<(String, Vec<String>)> = Vec::new();
    let reserved: [&str; 9] = [
        "if", "for", "while", "when", "return", "else", "try", "catch", "finally",
    ];

    let chars: Vec<char> = code.chars().collect();
    for caps in compose_block_re().captures_iter(code) {
        let args_text = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        let Some(route) = extract_route_compose(args_text) else {
            continue;
        };
        routes.push(route.clone());
        // Python: block_start = match.end() - 1 (char index của '{').
        let whole_end_char = byte_len_to_char_len(code, caps.get(0).unwrap().end());
        let block_start = whole_end_char - 1;
        let mut depth: i64 = 0;
        let mut block_end: Option<usize> = None;
        for (idx, ch) in chars.iter().enumerate().skip(block_start) {
            match ch {
                '{' => depth += 1,
                '}' => {
                    depth -= 1;
                    if depth == 0 {
                        block_end = Some(idx);
                        break;
                    }
                }
                _ => {}
            }
        }
        // Python: code[block_start:block_end] — char slice, KHÔNG gồm '}' cuối.
        let block_text: String = match block_end {
            Some(end) => chars[block_start..end].iter().collect(),
            None => String::new(),
        };
        let mut targets = callable_names(&block_text, &reserved);
        if targets.is_empty()
            && let Some(content_caps) = content_lambda_re().captures(args_text)
        {
            targets = vec![content_caps[1].to_string()];
        }
        if targets.is_empty()
            && let Some(content_caps) = content_ref_re().captures(args_text)
        {
            targets = vec![content_caps[1].to_string()];
        }
        if !targets.is_empty() {
            route_targets.push((route, targets));
        }
    }

    for caps in navhost_start_re().captures_iter(code) {
        start_routes.push(caps[1].to_string());
    }
    (routes, start_routes, route_targets)
}

fn byte_len_to_char_len(text: &str, byte_index: usize) -> usize {
    text[..byte_index.min(text.len())].chars().count()
}

fn extract_route_compose(args_text: &str) -> Option<String> {
    if let Some(caps) = route_args_re().captures(args_text) {
        return Some(caps[1].to_string());
    }
    if let Some(caps) = any_quote_re().captures(args_text) {
        return Some(caps[1].to_string());
    }
    None
}

fn callable_names(block_text: &str, reserved: &[&str]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    for caps in call_name_re().captures_iter(block_text) {
        let name = caps[1].to_string();
        let short_name = name.rsplit('.').next().unwrap_or(&name).to_string();
        if reserved.contains(&short_name.as_str()) || short_name == "composable" {
            continue;
        }
        names.push(name);
    }
    for (name, _) in iter_callable_reference_matches(block_text) {
        let short_name = name.rsplit('.').next().unwrap_or(&name).to_string();
        if reserved.contains(&short_name.as_str()) || short_name == "composable" {
            continue;
        }
        names.push(name);
    }
    names
}

/// `_extract_compose_routes_from_tree`.
fn extract_compose_routes_from_tree(root: Node, source: &[u8]) -> ComposeRoutes {
    let mut routes: Vec<String> = Vec::new();
    let mut start_routes: Vec<String> = Vec::new();
    let mut route_targets: Vec<(String, Vec<String>)> = Vec::new();
    let reserved: [&str; 9] = [
        "if", "for", "while", "when", "return", "else", "try", "catch", "finally",
    ];

    for call_node in find_nodes_by_type(root, "call_expression") {
        let callee = extract_call_name(call_node, source).unwrap_or_default();
        let short_name = callee.rsplit('.').next().unwrap_or(&callee).to_string();
        if short_name == "composable" {
            let arg_node = call_argument_node(call_node);
            let arg_text = arg_node
                .map(|node| node_text(node, source))
                .unwrap_or_default();
            let Some(route) = extract_route_from_args(&arg_text) else {
                continue;
            };
            routes.push(route.clone());
            let lambda_node = find_first_lambda_literal(call_node);
            let mut targets: Vec<String> = Vec::new();
            if let Some(lambda_node) = lambda_node {
                targets = callable_names_from_lambda(lambda_node, source, &reserved);
            }
            if targets.is_empty() {
                // Python compile pattern lỗi này runtime (re.error). Rust:
                // pattern gốc không compile ⇒ no-match (đã ghi nhận divergence).
                if let Some(re) = content_tree_broken_re()
                    && let Some(caps) = re.captures(&arg_text)
                {
                    targets = vec![caps[1].to_string()];
                }
            }
            if targets.is_empty()
                && let Some(caps) = content_ref_broken_re().captures(&arg_text)
            {
                targets = vec![caps[1].to_string()];
            }
            route_targets.push((route, targets));
            continue;
        }
        if short_name == "NavHost" {
            let arg_node = call_argument_node(call_node);
            let arg_text = arg_node
                .map(|node| node_text(node, source))
                .unwrap_or_default();
            if let Some(caps) = start_destination_broken_re().captures(&arg_text) {
                start_routes.push(caps[1].to_string());
            }
        }
    }

    ComposeRoutes {
        routes,
        start_routes,
        route_targets,
    }
}

fn callable_names_from_lambda(lambda_node: Node, source: &[u8], reserved: &[&str]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    for call_node in iter_call_expressions_in_node(lambda_node) {
        let Some(callee) = extract_call_name(call_node, source) else {
            continue;
        };
        let short_name = callee.rsplit('.').next().unwrap_or(&callee).to_string();
        if reserved.contains(&short_name.as_str()) || short_name == "composable" {
            continue;
        }
        names.push(callee);
    }
    let lambda_text = node_text(lambda_node, source);
    for (callee, _) in iter_callable_reference_matches(&lambda_text) {
        let short_name = callee.rsplit('.').next().unwrap_or(&callee).to_string();
        if reserved.contains(&short_name.as_str()) || short_name == "composable" {
            continue;
        }
        names.push(callee);
    }
    names
}

/// `_infer_component_type`.
pub fn infer_component_type(target_name: &str) -> Option<&'static str> {
    let short_name = target_name.rsplit('.').next().unwrap_or(target_name);
    if short_name.ends_with("Activity") {
        return Some("activity");
    }
    if short_name.ends_with("Fragment") {
        return Some("fragment");
    }
    if short_name == "Service"
        || short_name == "IntentService"
        || short_name == "JobIntentService"
        || short_name.ends_with("Service")
    {
        return Some("service");
    }
    if short_name.ends_with("BroadcastReceiver") {
        return Some("receiver");
    }
    if short_name.ends_with("ContentProvider") {
        return Some("provider");
    }
    if short_name.ends_with("Application") {
        return Some("application");
    }
    if short_name.ends_with("ViewModel") {
        return Some("view_model");
    }
    None
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
    let compose_routes = extract_compose_routes_from_tree(root_node, &source_bytes);

    if let Some(name) = package_name.clone() {
        let pkg_summary = pkg_comment.clone();
        let pkg_note = build_note(&pkg_snippet, &pkg_comment, &pkg_summary);
        payload.package_def = Some(PackageDef {
            name,
            start_line: pkg_start as i64,
            end_line: pkg_end as i64,
            code: pkg_snippet,
            comment: pkg_comment,
            summary: pkg_summary,
            note: pkg_note,
        });
    }

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
        payload.classes.push(ClassDef {
            symbol_id: cid.clone(),
            qualified_name: qualified,
            name: class_path.clone(),
            kind: kind.to_string(),
            package_name: package_name.clone(),
            file_path: rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet.clone(),
            comment,
            summary,
            note,
            visibility,
            is_public_api,
            visibility_source: "source-modifier".to_string(),
            export_evidence: export_evidence.to_string(),
            signature: source_signature(&snippet),
        });
        let super_types = extract_super_types(class_node, &source_bytes);
        if super_types.is_empty() {
            continue;
        }
        if matches!(kind, "class" | "object" | "enum") {
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

    // ── Pass 2: functions + calls + lambdas + events ────────────────────────
    for (func_node, class_name) in iter_functions(root_node, &source_bytes) {
        let Some(func_name) = extract_node_name(func_node, &source_bytes) else {
            continue;
        };
        let arity = count_parameters(func_node);
        let sid = symbol_id(
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
        payload.functions.push(FunctionDef {
            symbol_id: sid.clone(),
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
            comment,
            summary,
            note,
            visibility,
            is_public_api,
            visibility_source: "source-modifier".to_string(),
            export_evidence: export_evidence.to_string(),
            signature: source_signature(&snippet),
        });
        payload
            .android_events
            .extend(collect_android_events(func_node, &source_bytes, &sid));

        // Function types (param type chứa "->") — grammar 1.1.0 không expose
        // field `parameters` ⇒ vòng này không chạy (như Python).
        for param_node in iter_function_parameters(func_node) {
            let (param_name, type_text, type_info) =
                extract_parameter_info(param_node, &source_bytes);
            let Some(type_text) = type_text else {
                continue;
            };
            if !type_text.contains("->") {
                continue;
            }
            let type_signature = cortex_analyzer_framework::ts::normalize_ws(&type_text);
            let type_id = format!("functype::{type_signature}");
            let (start_line_t, end_line_t, snippet_t) = match type_info {
                None => (start_line as i64, end_line as i64, type_signature.clone()),
                Some((s, e, sn)) => (s as i64, e as i64, sn),
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
            properties.insert("parameter_type".to_string(), Value::String(type_signature));
            payload.relations.push(RelationEdge {
                source_id: sid.clone(),
                source_label: "Function".to_string(),
                target_id: type_id,
                target_label: "FunctionType".to_string(),
                rel_type: "TAKES_FUNCTION".to_string(),
                properties,
            });
        }

        // Calls từ call_expression (subtree hàm, gồm các lambda lồng nhau).
        for call_node in iter_calls(func_node) {
            let Some(callee) = extract_call_name(call_node, &source_bytes) else {
                continue;
            };
            payload.calls.push(CallEdge {
                caller_id: sid.clone(),
                caller_file: rel_path.clone(),
                caller_package: package_name.clone(),
                caller_class: class_name.clone(),
                imports: imports.clone(),
                call_line: call_node.start_position().row as i64 + 1,
                call_column: call_node.start_position().column as i64 + 1,
                call_type: "call_expression".to_string(),
                callee_name: callee,
                callee_id: None,
            });
        }

        // Callable references ngoài lambda ranges.
        let lambda_nodes = find_nodes_by_types(
            func_node,
            &["lambda_literal", "lambda_expression"],
        );
        let lambda_ranges: Vec<(usize, usize)> = lambda_nodes
            .iter()
            .map(|node| (node.start_byte(), node.end_byte()))
            .collect();
        let function_text = node_text(func_node, &source_bytes);
        for (callee, offset) in iter_callable_reference_matches(&function_text) {
            let abs_offset = func_node.start_byte() + offset;
            if lambda_ranges
                .iter()
                .any(|(start, end)| *start <= abs_offset && abs_offset < *end)
            {
                continue;
            }
            let (call_line, call_column) = line_col_from_byte(&source_bytes, abs_offset);
            payload.calls.push(CallEdge {
                caller_id: sid.clone(),
                caller_file: rel_path.clone(),
                caller_package: package_name.clone(),
                caller_class: class_name.clone(),
                imports: imports.clone(),
                call_line: call_line as i64,
                call_column: call_column as i64,
                call_type: "callable_reference".to_string(),
                callee_name: callee,
                callee_id: None,
            });
        }

        // Lambda functions.
        for lambda_node in &lambda_nodes {
            let (l_snippet, l_start, l_end) = node_snippet(*lambda_node, &source_bytes);
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
                source_id: sid.clone(),
                source_label: "Function".to_string(),
                target_id: lambda_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Row::new(),
            });
            for lambda_call in iter_calls(*lambda_node) {
                let Some(callee) = extract_call_name(lambda_call, &source_bytes) else {
                    continue;
                };
                payload.calls.push(CallEdge {
                    caller_id: lambda_id.clone(),
                    caller_file: rel_path.clone(),
                    caller_package: package_name.clone(),
                    caller_class: class_name.clone(),
                    imports: imports.clone(),
                    call_line: lambda_call.start_position().row as i64 + 1,
                    call_column: lambda_call.start_position().column as i64 + 1,
                    call_type: "call_expression".to_string(),
                    callee_name: callee,
                    callee_id: None,
                });
            }
            let lambda_text = node_text(*lambda_node, &source_bytes);
            for (callee, offset) in iter_callable_reference_matches(&lambda_text) {
                let abs_offset = lambda_node.start_byte() + offset;
                let (call_line, call_column) = line_col_from_byte(&source_bytes, abs_offset);
                payload.calls.push(CallEdge {
                    caller_id: lambda_id.clone(),
                    caller_file: rel_path.clone(),
                    caller_package: package_name.clone(),
                    caller_class: class_name.clone(),
                    imports: imports.clone(),
                    call_line: call_line as i64,
                    call_column: call_column as i64,
                    call_type: "callable_reference".to_string(),
                    callee_name: callee,
                    callee_id: None,
                });
            }
        }
    }

    payload.compose_routes = compose_routes;
    Ok(payload)
}


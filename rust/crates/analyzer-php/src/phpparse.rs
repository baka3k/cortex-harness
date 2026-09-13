//! Port phần parse của `code-tiny/tools/php/php_analyzer.py`: tree-sitter walk,
//! FunctionDef/NamespaceDef/TypeDef/FileDef/RelationEdge/CallEdge, node-kind
//! maps (`_NAMESPACE_NODE_TYPES`/`_TYPE_NODE_KINDS`/`_FUNCTION_NODE_KINDS`/
//! `_ANON_FUNCTION_NODE_TYPES`), `_build_note`, call extraction, imports.

use std::collections::HashSet;

use serde_json::{Map, Value};

use cortex_analyzer_framework::ts::{
    decode_ignore, extract_file_comment, extract_leading_comment, find_nodes_by_type, node_snippet,
    node_text, normalize_ws,
};

pub type Row = Map<String, Value>;

// ── Data defs (asdict shape khớp Python payload JSON) ───────────────────────

#[derive(Debug, Clone)]
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
}

#[derive(Debug, Clone)]
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

#[derive(Debug, Clone)]
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
pub struct RelationEdge {
    pub source_id: String,
    pub target_id: String,
    pub rel_type: String,
    pub properties: Row,
}

#[derive(Debug, Clone)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub callee_name: String,
    /// `_count_arguments` luôn trả int — `callee_arity` không bao giờ None.
    pub callee_arity: i64,
}

#[derive(Debug, Clone)]
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

#[derive(Debug, Default)]
pub struct FilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: Option<FileDef>,
}

// ── Node-kind maps (port `_NAMESPACE_NODE_TYPES` v.v. từng chữ) ────────────

const NAMESPACE_NODE_TYPES: [&str; 2] = ["namespace_definition", "namespace_declaration"];

fn type_node_kind(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "trait_declaration" => Some("trait"),
        "enum_declaration" => Some("enum"),
        _ => None,
    }
}

fn function_node_kind(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "function_definition" => Some("function"),
        "method_declaration" => Some("method"),
        _ => None,
    }
}

fn is_anon_function_node_type(node_kind: &str) -> bool {
    matches!(
        node_kind,
        "anonymous_function" | "anonymous_function_creation_expression" | "arrow_function"
    )
}

// ── Helpers (port từng hàm `_` của php_analyzer.py) ─────────────────────────

fn build_note(code: &str, comment: &str, summary: &str) -> String {
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

fn normalize_call_name(text: &str) -> String {
    let re_brackets = regex::Regex::new(r"<[^<>]*>").expect("brackets");
    let cleaned = re_brackets.replace_all(text, "").to_string();
    let cleaned = cleaned
        .replace("this->", "")
        .replace("self::", "")
        .replace("static::", "");
    let cleaned = cleaned.replace("?->", "->");
    let cleaned = cleaned.trim().trim_start_matches('$').to_string();
    let mut cleaned = match cleaned.rfind("->") {
        Some(pos) => cleaned[pos + 2..].to_string(),
        None => cleaned,
    };
    if let Some(pos) = cleaned.rfind("::") {
        cleaned = cleaned[pos + 2..].to_string();
    }
    if let Some(pos) = cleaned.rfind('.') {
        cleaned = cleaned[pos + 1..].to_string();
    }
    cleaned.trim().to_string()
}

fn extract_scope_stack(stack: &[String]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(stack.join("::"))
    }
}

fn symbol_id(scope: Option<&str>, name: &str, arity: i64, rel_path: &str) -> String {
    let qualified = match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    };
    format!("{qualified}/{arity}@{rel_path}")
}

fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    }
}

fn anonymous_name(prefix: &str, node: tree_sitter::Node) -> String {
    format!(
        "Anonymous{prefix}@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

fn count_parameters(node: tree_sitter::Node) -> i64 {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| node.child_by_field_name("parameter_list"));
    let Some(params) = params else { return 0 };
    params
        .children(&mut params.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

fn count_arguments(node: tree_sitter::Node) -> i64 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| node.child_by_field_name("argument_list"));
    let Some(args) = args else { return 0 };
    args.children(&mut args.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

/// `_iter_calls` — 5 lượt walk riêng theo đúng thứ tự Python (function →
/// method → scoped → call → object_creation). Node kind đổi tên giữa các
/// phiên bản grammar (vd `member_call_expression`) KHÔNG được thêm vào đây —
/// tham chiếu cũng không thấy kind mới nên hành vi là bỏ qua.
fn iter_calls<'tree>(func_node: tree_sitter::Node<'tree>) -> Vec<tree_sitter::Node<'tree>> {
    let mut calls = Vec::new();
    for node_type in [
        "function_call_expression",
        "method_call_expression",
        "scoped_call_expression",
        "call_expression",
        "object_creation_expression",
    ] {
        calls.extend(find_nodes_by_type(func_node, node_type));
    }
    calls
}

fn extract_call_name(call_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    for field in ["function", "name", "callable"] {
        if let Some(expr) = call_node.child_by_field_name(field) {
            return Some(normalize_call_name(node_text(expr, source).trim()));
        }
    }
    if call_node.kind() == "object_creation_expression"
        && let Some(name) = first_identifier(call_node, source)
        && !name.is_empty()
    {
        return Some(normalize_call_name(&name));
    }
    let text = node_text(call_node, source);
    let text = text.trim();
    let text = text.split('(').next().unwrap_or("").trim();
    Some(normalize_call_name(text))
}

fn first_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "identifier" | "name" | "qualified_name" | "namespace_name"
    ) {
        return Some(node_text(node, source));
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

fn extract_name_field(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(node, source)
}

// ── Tree walk ───────────────────────────────────────────────────────────────

struct WalkState<'a> {
    source: &'a [u8],
    rel_path: String,
    namespaces: Vec<NamespaceDef>,
    types: Vec<TypeDef>,
    functions: Vec<FunctionDef>,
    relations: Vec<RelationEdge>,
    calls: Vec<CallEdge>,
    skip_function_ranges: HashSet<(usize, usize)>,
}

#[allow(clippy::too_many_arguments)]
fn record_function(
    state: &mut WalkState<'_>,
    node: tree_sitter::Node,
    namespace_stack: &[String],
    type_stack: &[String],
    name_override: Option<&str>,
    kind_override: Option<&str>,
    calls_root: Option<tree_sitter::Node>,
    parameters_node: Option<tree_sitter::Node>,
    exported: bool,
) {
    let source = state.source;
    let mut name = match name_override {
        Some(name) if !name.is_empty() => name.to_string(),
        _ => extract_name_field(node, source).unwrap_or_default(),
    };
    let mut kind = kind_override
        .map(str::to_string)
        .unwrap_or_else(|| function_node_kind(node.kind()).unwrap_or("function").to_string());
    if name.is_empty() {
        name = anonymous_name("Function", node);
    }
    if kind == "method" && name == "__construct" {
        kind = "constructor".to_string();
    }
    let (snippet, start_line, end_line) = node_snippet(node, source);
    let comment = extract_leading_comment(node, source);
    let summary = comment.clone();
    let note = build_note(&snippet, &comment, &summary);
    let mut scope_stack: Vec<String> = namespace_stack.to_vec();
    scope_stack.extend_from_slice(type_stack);
    let scope_name = extract_scope_stack(&scope_stack);
    let arity = count_parameters(parameters_node.unwrap_or(node));
    let func_id = symbol_id(scope_name.as_deref(), &name, arity, &state.rel_path);
    state.functions.push(FunctionDef {
        symbol_id: func_id.clone(),
        qualified_name: qualified_name(scope_name.as_deref(), &name),
        name,
        kind,
        scope_name: scope_name.clone(),
        file_path: state.rel_path.clone(),
        start_line: start_line as i64,
        end_line: end_line as i64,
        arity,
        code: snippet.clone(),
        comment: comment.clone(),
        summary,
        note,
        exported,
    });
    if !type_stack.is_empty() {
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(type_stack);
        state.relations.push(RelationEdge {
            source_id: full.join("::"),
            target_id: func_id.clone(),
            rel_type: "CONTAINS".to_string(),
            properties: Map::new(),
        });
    } else if !namespace_stack.is_empty() {
        state.relations.push(RelationEdge {
            source_id: format!("namespace::{}", namespace_stack.join("::")),
            target_id: func_id.clone(),
            rel_type: "CONTAINS".to_string(),
            properties: Map::new(),
        });
    }
    let call_root = calls_root.unwrap_or(node);
    for call_node in iter_calls(call_root) {
        let Some(callee) = extract_call_name(call_node, source) else {
            continue;
        };
        if callee.is_empty() {
            continue;
        }
        state.calls.push(CallEdge {
            caller_id: func_id.clone(),
            caller_scope: scope_name.clone(),
            callee_name: callee,
            callee_arity: count_arguments(call_node),
        });
    }
}

fn walk_tree(
    state: &mut WalkState<'_>,
    node: tree_sitter::Node,
    namespace_stack: &[String],
    type_stack: &[String],
    exported_context: bool,
) {
    let source = state.source;
    let kind = node.kind();

    // Anonymous function đã ghi qua assignment — chỉ walk children (bỏ ghi
    // lại, giữ named functions lồng bên trong).
    if is_anon_function_node_type(kind)
        && state
            .skip_function_ranges
            .contains(&(node.start_byte(), node.end_byte()))
    {
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            walk_tree(state, child, namespace_stack, type_stack, exported_context);
        }
        return;
    }

    if NAMESPACE_NODE_TYPES.contains(&kind) {
        let mut name = extract_name_field(node, source).unwrap_or_default();
        if name.is_empty() {
            name = anonymous_name("Namespace", node);
        }
        let mut parts: Vec<String> = namespace_stack.to_vec();
        parts.push(name.clone());
        let qualified = parts.join("::");
        let ns_id = format!("namespace::{qualified}");
        let (snippet, start_line, end_line) = node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        state.namespaces.push(NamespaceDef {
            symbol_id: ns_id.clone(),
            qualified_name: qualified,
            name: name.clone(),
            file_path: state.rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment: comment.clone(),
            summary,
            note,
        });
        if !namespace_stack.is_empty() {
            let parent = format!("namespace::{}", namespace_stack.join("::"));
            state.relations.push(RelationEdge {
                source_id: parent,
                target_id: ns_id.clone(),
                rel_type: "CONTAINS".to_string(),
                properties: Map::new(),
            });
        }
        let mut child_ns: Vec<String> = namespace_stack.to_vec();
        child_ns.push(name.clone());
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            walk_tree(state, child, &child_ns, type_stack, false);
        }
        return;
    }

    if let Some(base_kind) = type_node_kind(kind) {
        let mut name = extract_name_field(node, source).unwrap_or_default();
        let type_kind = if name.is_empty() {
            name = anonymous_name(&capitalize(base_kind), node);
            format!("anonymous_{base_kind}")
        } else {
            base_kind.to_string()
        };
        let mut parts: Vec<String> = namespace_stack.to_vec();
        parts.extend_from_slice(type_stack);
        let qualified = if parts.is_empty() {
            name.clone()
        } else {
            parts.push(name.clone());
            parts.join("::")
        };
        let type_id = qualified.clone();
        let (snippet, start_line, end_line) = node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        state.types.push(TypeDef {
            symbol_id: type_id.clone(),
            qualified_name: qualified.clone(),
            name: qualified.rsplit("::").next().unwrap_or(&qualified).to_string(),
            kind: type_kind,
            file_path: state.rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment: comment.clone(),
            summary,
            note,
            exported: exported_context,
        });
        if !namespace_stack.is_empty() {
            let ns_id = format!("namespace::{}", namespace_stack.join("::"));
            state.relations.push(RelationEdge {
                source_id: ns_id,
                target_id: type_id.clone(),
                rel_type: "CONTAINS".to_string(),
                properties: Map::new(),
            });
        }
        if !type_stack.is_empty() {
            let mut parent: Vec<String> = namespace_stack.to_vec();
            parent.extend_from_slice(type_stack);
            state.relations.push(RelationEdge {
                source_id: parent.join("::"),
                target_id: type_id.clone(),
                rel_type: "CONTAINS".to_string(),
                properties: Map::new(),
            });
        }
        let mut child_types: Vec<String> = type_stack.to_vec();
        child_types.push(name);
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            walk_tree(state, child, namespace_stack, &child_types, false);
        }
        return;
    }

    if function_node_kind(kind).is_some() {
        record_function(
            state,
            node,
            namespace_stack,
            type_stack,
            None,
            None,
            None,
            None,
            exported_context,
        );
        return;
    }

    if is_anon_function_node_type(kind) {
        let kind_override = if kind == "arrow_function" {
            "arrow_function"
        } else {
            "anonymous_function"
        };
        record_function(
            state,
            node,
            namespace_stack,
            type_stack,
            None,
            Some(kind_override),
            Some(node),
            Some(node),
            exported_context,
        );
        return;
    }

    if kind == "assignment_expression" || kind == "simple_assignment_expression" {
        let left = node.child_by_field_name("left");
        let right = node.child_by_field_name("right");
        if let Some(right) = right
            && is_anon_function_node_type(right.kind())
        {
            let name = left.and_then(|left| {
                let text = node_text(left, source).trim().to_string();
                if text.is_empty() {
                    None
                } else {
                    Some(text.trim_start_matches('$').to_string())
                }
            });
            record_function(
                state,
                right,
                namespace_stack,
                type_stack,
                name.as_deref(),
                Some("function_variable"),
                Some(right),
                Some(right),
                exported_context,
            );
            state
                .skip_function_ranges
                .insert((right.start_byte(), right.end_byte()));
        }
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_tree(state, child, namespace_stack, type_stack, exported_context);
    }
}

fn capitalize(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

// ── Imports ─────────────────────────────────────────────────────────────────

/// `_collect_imports` — namespace_use_declaration TRƯỚC namespace_use_clause
/// (clause là con của declaration nên cùng text được thu 2 lần như Python),
/// rồi include/require expressions.
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "namespace_use_declaration") {
        let text = normalize_ws(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "namespace_use_clause") {
        let text = normalize_ws(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    for node_type in [
        "include_expression",
        "include_once_expression",
        "require_expression",
        "require_once_expression",
    ] {
        for node in find_nodes_by_type(root, node_type) {
            let text = normalize_ws(&node_text(node, source));
            if !text.is_empty() {
                imports.push(text);
            }
        }
    }
    imports
}

// ── File entry ──────────────────────────────────────────────────────────────

/// `parse_php_file` — walk 1 file, trả payload asdict-shape.
pub fn parse_php_file(
    path: &std::path::Path,
    root: &std::path::Path,
) -> Result<FilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let source_bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_php::LANGUAGE_PHP.into())
        .map_err(|e| e.to_string())?;
    let tree = parser
        .parse(&source_bytes, None)
        .ok_or_else(|| format!("parse failed: {rel_path}"))?;

    let snippet = decode_ignore(&source_bytes);
    let end_line = snippet.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment(tree.root_node(), &source_bytes);
    let file_summary = file_comment.clone();
    let file_note = build_note(&snippet, &file_comment, &file_summary);
    let imports = collect_imports(tree.root_node(), &source_bytes);
    let file_def = FileDef {
        file_path: rel_path.clone(),
        start_line: 1,
        end_line,
        code: snippet,
        comment: file_comment,
        summary: file_summary,
        note: file_note,
        imports,
        // `_collect_exports`/`_collect_jsx_tags` của PHP trả rỗng cố định.
        exports: Vec::new(),
        jsx_tags: Vec::new(),
        jsx_components: Vec::new(),
    };

    let mut state = WalkState {
        source: &source_bytes,
        rel_path,
        namespaces: Vec::new(),
        types: Vec::new(),
        functions: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
        skip_function_ranges: HashSet::new(),
    };
    // `exported_names` phía Python luôn rỗng (không có export PHP) nên nhánh
    // fixup exported sau walk là no-op — bỏ giữ nguyên hành vi.
    walk_tree(&mut state, tree.root_node(), &[], &[], false);
    Ok(FilePayload {
        functions: state.functions,
        calls: state.calls,
        types: state.types,
        namespaces: state.namespaces,
        relations: state.relations,
        file_def: Some(file_def),
    })
}

// ── Scan (port `_should_ignore_directory` + `_scan_php_files`) ──────────────

/// Ignore set của `_should_ignore_directory` (đã trộn sẵn COMMON_SCAN_EXCLUDE
/// qua nhánh import thành công phía Python). Pattern `.env.*.local` chỉ được
/// so exact (`in`) nên hiệu ứng là dead entry — giữ nguyên từng chữ.
const PHP_IGNORE_PATTERNS: [&str; 26] = [
    // Composer
    "vendor",
    // Build outputs & cache
    "var",
    "cache",
    "tmp",
    "temp",
    // Framework caches (Laravel, Symfony, ...)
    "storage/cache",
    "bootstrap/cache",
    // Testing
    "tests/output",
    ".phpunit.result.cache",
    "phpstan-cache",
    "psalm-cache",
    // IDE
    ".idea",
    ".vscode",
    // Version control
    ".git",
    ".svn",
    ".hg",
    // Node (mixed projects)
    "node_modules",
    "dist",
    "build",
    // Logs
    "storage/logs",
    "logs",
    // Environment
    ".env",
    ".env.local",
    ".env.*.local",
    // OS specific
    ".DS_Store",
    "Thumbs.db",
];

fn should_ignore_directory(dir_name: &str) -> bool {
    if PHP_IGNORE_PATTERNS.contains(&dir_name)
        || cortex_analyzer_framework::scan::COMMON_SCAN_EXCLUDE.contains(&dir_name)
    {
        return true;
    }
    if dir_name.ends_with(".swp") || dir_name.ends_with(".swo") {
        return true;
    }
    false
}

/// `_scan_php_files` — walk root, lọc ignored dirs + junk filenames, thu
/// `.php/.phtml/.inc/.php4/.php5`, sort theo path string (khớp `sorted()`).
pub fn scan_php_files(root: &std::path::Path) -> Vec<std::path::PathBuf> {
    let mut files = Vec::new();
    walk_php(root, &mut files);
    files.sort_by(|a, b| {
        a.to_string_lossy()
            .cmp(&b.to_string_lossy())
    });
    files
}

fn walk_php(dir: &std::path::Path, files: &mut Vec<std::path::PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<std::path::PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let file_type = match entry.file_type() {
            Ok(t) => t,
            Err(_) => continue,
        };
        if file_type.is_dir() {
            if !should_ignore_directory(&name)
                && !cortex_analyzer_framework::scan::matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        if name.ends_with(".swp") || name.ends_with(".swo") || name.ends_with(".cache") {
            continue;
        }
        if matches!(&name[..], ".DS_Store" | "Thumbs.db" | ".env" | ".env.local") {
            continue;
        }
        if [".php", ".phtml", ".inc", ".php4", ".php5"]
            .iter()
            .any(|ext| name.ends_with(ext))
        {
            files.push(path);
        }
    }
    for sub in subdirs {
        walk_php(&sub, files);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"<?php
namespace App\Billing;

use App\Contracts\Chargeable;

interface Priced {
    public function cost(): int;
}

class Invoice implements Priced {
    public function __construct(private $repo) { }
    public function cost(): int {
        return compute_total($this->items);
    }
}

function compute_total(array $items): int {
    $inv = new Invoice(null);
    return count($items);
}
"#;

    fn parse_sample() -> FilePayload {
        let dir = std::env::temp_dir().join(format!("analyzer_php_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("sample.php");
        std::fs::write(&path, SAMPLE).unwrap();
        let payload = parse_php_file(&path, &dir).unwrap();
        std::fs::remove_dir_all(&dir).ok();
        payload
    }

    #[test]
    fn parses_class_method_and_function() {
        let payload = parse_sample();
        let names: Vec<&str> = payload.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"cost"));
        assert!(names.contains(&"__construct"));
        assert!(names.contains(&"compute_total"));

        let cost = payload
            .functions
            .iter()
            .find(|f| f.name == "cost" && f.scope_name.as_deref() == Some("Invoice"))
            .expect("cost");
        assert_eq!(cost.kind, "method");
        // Semicolon-style `namespace X;`: declarations là SIBLING của
        // namespace_definition nên Python walk KHÔNG mang namespace_stack —
        // scope không có prefix. Quirk phải giữ nguyên.
        assert_eq!(cost.scope_name.as_deref(), Some("Invoice"));
        assert_eq!(cost.arity, 0);
        assert_eq!(cost.symbol_id, "Invoice::cost/0@sample.php");

        let total = payload
            .functions
            .iter()
            .find(|f| f.name == "compute_total")
            .expect("compute_total");
        assert_eq!(total.kind, "function");
        assert_eq!(total.scope_name, None);
        assert_eq!(total.arity, 1);

        let types: Vec<&str> = payload.types.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(types, vec!["Priced", "Invoice"]);
        assert_eq!(payload.types[0].kind, "interface");
        assert_eq!(payload.types[1].kind, "class");
        assert_eq!(payload.types[1].symbol_id, "Invoice");
        assert_eq!(
            payload.namespaces[0].symbol_id,
            "namespace::App\\Billing"
        );
    }

    #[test]
    fn extracts_calls_and_imports() {
        let payload = parse_sample();
        let calls: Vec<&str> = payload.calls.iter().map(|c| c.callee_name.as_str()).collect();
        // `$this->items` không phải call; compute_total là function call;
        // count(...) là function call; new Invoice là object_creation.
        assert!(calls.contains(&"compute_total"));
        assert!(calls.contains(&"count"));
        assert!(calls.contains(&"Invoice"));

        let imports = payload.file_def.as_ref().unwrap().imports.clone();
        // Declaration VÀ clause đều được thu (giống Python).
        assert_eq!(imports.len(), 2);
        assert!(imports[0].starts_with("use "));
        assert_eq!(imports[1], "App\\Contracts\\Chargeable");
    }

    #[test]
    fn note_format_matches_python() {
        let note = build_note("code", "comment", "summary");
        assert_eq!(note, "Summary:\nsummary\n\nComment:\ncomment\n\nCode:\ncode");
        assert_eq!(build_note("", "", ""), "");
    }

    #[test]
    fn normalize_call_name_matches_python() {
        assert_eq!(normalize_call_name("$this->cache"), "cache");
        assert_eq!(normalize_call_name("self::TAX"), "TAX");
        assert_eq!(normalize_call_name("$obj?->run"), "run");
        assert_eq!(normalize_call_name("Foo\\Bar::baz"), "baz");
        assert_eq!(normalize_call_name("Helper.bat"), "bat");
        assert_eq!(normalize_call_name("$f"), "f");
    }

    #[test]
    fn scan_php_files_respects_ignore_and_extensions() {
        let dir = std::env::temp_dir().join(format!("analyzer_php_scan_{}", std::process::id()));
        let sub = dir.join("vendor").join("pkg");
        std::fs::create_dir_all(&sub).unwrap();
        let keep = dir.join("src");
        std::fs::create_dir_all(&keep).unwrap();
        std::fs::write(sub.join("x.php"), "<?php\n").unwrap();
        std::fs::write(keep.join("a.php"), "<?php\n").unwrap();
        std::fs::write(keep.join("notes.txt"), "nope").unwrap();
        let found = scan_php_files(&dir);
        let rels: Vec<String> = found
            .iter()
            .map(|p| cortex_analyzer_framework::scan::rel_posix(&dir, p))
            .collect();
        assert_eq!(rels, vec!["src/a.php"]);
        std::fs::remove_dir_all(&dir).ok();
    }
}


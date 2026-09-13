//! Port phần parse của `tools/js/js_analyzer.py`: tree-sitter walk,
//! FunctionDef/TypeDef/NamespaceDef/FileDef, exported_names, JSX tag
//! collection, imports/exports, call extraction, symbol-id formats,
//! `_build_note`, error stats.
//!
//! Grammar dispatch (`_get_js_parser`): cả 2 nhánh (jsx/non-jsx) thử
//! `javascript` trước và env tham chiếu chỉ có grammar crate
//! `tree-sitter-javascript` (wheel `tree-sitter-languages` 1.10.2 vỡ với
//! tree-sitter ≥0.25) ⇒ mọi file parse bằng grammar javascript (JSX được
//! grammar gốc hỗ trợ sẵn).

use std::collections::BTreeSet;
use std::path::Path;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{Map, Value};

use cortex_analyzer_framework::ts::{
    decode_ignore, extract_file_comment, extract_leading_comment, find_nodes_by_type, node_snippet,
    node_text, normalize_ws,
};

pub type Row = Map<String, Value>;

pub const JS_SOURCE_EXTENSIONS: [&str; 4] = [".js", ".jsx", ".mjs", ".cjs"];

/// `_TYPE_NODE_KINDS`.
const TYPE_NODE_KINDS: [(&str, &str); 1] = [("class_declaration", "class")];

/// `_FUNCTION_NODE_KINDS`.
const FUNCTION_NODE_KINDS: [(&str, &str); 3] = [
    ("function_declaration", "function"),
    ("generator_function_declaration", "generator_function"),
    ("method_definition", "method"),
];

fn type_kind_for(node_kind: &str) -> Option<&'static str> {
    TYPE_NODE_KINDS
        .iter()
        .find(|(kind, _)| *kind == node_kind)
        .map(|(_, label)| *label)
}

fn function_kind_for(node_kind: &str) -> Option<&'static str> {
    FUNCTION_NODE_KINDS
        .iter()
        .find(|(kind, _)| *kind == node_kind)
        .map(|(_, label)| *label)
}

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

/// NamespaceDef — js walk không bao giờ emit (`_NAMESPACE_NODE_TYPES` rỗng)
/// nhưng giữ row shape cho faithfulness.
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
pub struct RelationEdge {
    pub source_id: String,
    pub target_id: String,
    pub rel_type: String,
}

#[derive(Debug, Clone)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub callee_name: String,
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

#[derive(Debug, Clone, Default)]
pub struct ParseMeta {
    pub has_error: bool,
    pub error_nodes: i64,
}

#[derive(Debug, Clone, Default)]
pub struct FilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub file_def: Option<FileDef>,
    pub parse_meta: ParseMeta,
}

// ── Helpers (port từng hàm `_` của js_analyzer.py) ──────────────────────────

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

/// `_first_identifier` — bộ kind nhận diện identifier của js_analyzer.
const FIRST_IDENTIFIER_KINDS: [&str; 4] = [
    "identifier",
    "property_identifier",
    "type_identifier",
    "namespace_identifier",
];

fn first_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if FIRST_IDENTIFIER_KINDS.contains(&node.kind()) {
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

/// `_normalize_call_name` — strip generic args, this./super./?./::, bracket
/// access tail, cuối cùng lấy segment sau dấu '.'.
pub fn normalize_call_name(text: &str) -> String {
    static GENERIC: OnceLock<Regex> = OnceLock::new();
    static BRACKET: OnceLock<Regex> = OnceLock::new();
    let generic = GENERIC.get_or_init(|| Regex::new(r"<[^<>]*>").expect("generic regex"));
    // Python dùng backreference `\1` (regex crate không hỗ trợ) — vì content
    // `[^'"]+` loại CẢ hai loại quote, 2 nhánh quote là tương đương chính xác.
    let bracket = BRACKET.get_or_init(|| {
        Regex::new(r#"\[\s*(?:'([^'"]+)'|"([^'"]+)")\s*\]\s*$"#).expect("bracket regex")
    });
    let cleaned = generic.replace_all(text, "").to_string();
    let cleaned = cleaned
        .replace("this.", "")
        .replace("super.", "")
        .replace("?.", ".")
        .replace("::", ".");
    let cleaned = cleaned.trim();
    if let Some(caps) = bracket.captures(cleaned) {
        if let Some(m) = caps.get(1) {
            return m.as_str().to_string();
        }
        if let Some(m) = caps.get(2) {
            return m.as_str().to_string();
        }
    }
    let mut cleaned = cleaned.to_string();
    if cleaned.contains('.') {
        cleaned = cleaned.rsplit('.').next().unwrap_or("").to_string();
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

/// `_symbol_id` — `qualified/arity@rel_path`.
pub fn symbol_id(scope: Option<&str>, name: &str, arity: i64, rel_path: &str) -> String {
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

/// `_type_id`.
fn type_id(qualified: &str) -> String {
    qualified.to_string()
}

/// `_namespace_id`.
fn namespace_id(name: &str) -> String {
    format!("namespace::{name}")
}

/// `_anonymous_name`.
fn anonymous_name(prefix: &str, node: tree_sitter::Node) -> String {
    format!(
        "Anonymous{prefix}@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

/// `_count_parameters` — field parameters/parameter_list, named != comment.
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

/// `_count_arguments` — field arguments/argument_list, named != comment.
fn count_arguments(node: tree_sitter::Node) -> i64 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| node.child_by_field_name("argument_list"));
    let Some(args) = args else { return 0 };
    args.children(&mut args.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

/// `_iter_calls` — TOÀN BỘ call_expression trước (document order), sau đó toàn
/// bộ new_expression (đúng thứ tự Python).
fn iter_calls(func_node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let mut calls = find_nodes_by_type(func_node, "call_expression");
    calls.extend(find_nodes_by_type(func_node, "new_expression"));
    calls
}

/// `_extract_call_name`.
fn extract_call_name(call_node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    let field = if call_node.kind() == "call_expression" {
        Some("function")
    } else if call_node.kind() == "new_expression" {
        Some("constructor")
    } else {
        None
    };
    if let Some(field) = field
        && let Some(expr) = call_node.child_by_field_name(field)
    {
        return Some(normalize_call_name(node_text(expr, source).trim()));
    }
    let mut text = node_text(call_node, source).trim().to_string();
    if let Some(stripped) = text.strip_prefix("new ") {
        text = stripped.to_string();
    }
    let text = text.split('(').next().unwrap_or("").trim();
    Some(normalize_call_name(text))
}

/// `_collect_imports` — import_statement + import_require_clause.
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_statement") {
        let text = normalize_ws(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "import_require_clause") {
        let text = normalize_ws(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    imports
}

/// `_collect_exports` — export_statement + export_default_declaration.
fn collect_exports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut exports = Vec::new();
    for node in find_nodes_by_type(root, "export_statement") {
        let text = normalize_ws(&node_text(node, source));
        if !text.is_empty() {
            exports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "export_default_declaration") {
        let text = normalize_ws(&node_text(node, source));
        if !text.is_empty() {
            exports.push(text);
        }
    }
    exports
}

/// `_jsx_name`.
fn jsx_name(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    let mut name_node = node.child_by_field_name("name");
    if name_node.is_none() {
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
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

/// `_collect_jsx_tags` — (tags, components), lowercase-first ⇒ tag.
fn collect_jsx_tags(root: tree_sitter::Node, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut tags: BTreeSet<String> = BTreeSet::new();
    let mut components: BTreeSet<String> = BTreeSet::new();
    let mut classify = |node: tree_sitter::Node| {
        let Some(name) = jsx_name(node, source) else {
            return;
        };
        if name.is_empty() {
            return;
        }
        let is_lower = name
            .chars()
            .next()
            .is_some_and(|c| c.is_lowercase());
        if is_lower {
            tags.insert(name);
        } else {
            components.insert(name);
        }
    };
    for node in find_nodes_by_type(root, "jsx_opening_element") {
        classify(node);
    }
    for node in find_nodes_by_type(root, "jsx_self_closing_element") {
        classify(node);
    }
    (tags.into_iter().collect(), components.into_iter().collect())
}

// ── Tree walk ───────────────────────────────────────────────────────────────

struct WalkState<'a> {
    source: &'a [u8],
    rel_path: String,
    functions: Vec<FunctionDef>,
    calls: Vec<CallEdge>,
    types: Vec<TypeDef>,
    namespaces: Vec<NamespaceDef>,
    relations: Vec<RelationEdge>,
}

fn capitalize(word: &str) -> String {
    let mut chars = word.chars();
    match chars.next() {
        Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

/// `_record_function` — name/kind override + calls_root/parameters_node
/// override cho function_variable (lexical_declaration/variable_declarator).
#[allow(clippy::too_many_arguments)]
fn record_function(
    state: &mut WalkState<'_>,
    node: tree_sitter::Node,
    namespace_stack: &[String],
    type_stack: &[String],
    name_override: Option<String>,
    kind_override: Option<String>,
    calls_root: Option<tree_sitter::Node>,
    parameters_node: Option<tree_sitter::Node>,
    exported: bool,
) {
    let source = state.source;
    let mut name = match name_override {
        Some(name) if !name.is_empty() => name,
        _ => extract_name_field(node, source).unwrap_or_default(),
    };
    let mut kind = match kind_override {
        Some(kind) => kind,
        None => function_kind_for(node.kind())
            .unwrap_or("function")
            .to_string(),
    };
    if name.is_empty() {
        name = anonymous_name("Function", node);
    }
    if kind == "method" && name == "constructor" {
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
        code: snippet,
        comment: comment.clone(),
        summary,
        note,
        exported,
    });
    if !type_stack.is_empty() {
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(type_stack);
        state.relations.push(RelationEdge {
            source_id: type_id(&full.join("::")),
            target_id: func_id.clone(),
            rel_type: "CONTAINS".to_string(),
        });
    } else if !namespace_stack.is_empty() {
        state.relations.push(RelationEdge {
            source_id: namespace_id(&namespace_stack.join("::")),
            target_id: func_id.clone(),
            rel_type: "CONTAINS".to_string(),
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

/// `_walk_tree`.
fn walk_tree(
    state: &mut WalkState<'_>,
    node: tree_sitter::Node,
    namespace_stack: &[String],
    type_stack: &[String],
    exported_context: bool,
    exported_names: &mut BTreeSet<String>,
) {
    let source = state.source;
    if matches!(node.kind(), "export_statement" | "export_default_declaration") {
        if let Some(decl) = node.child_by_field_name("declaration") {
            walk_tree(state, decl, namespace_stack, type_stack, true, exported_names);
            return;
        }
        for spec in find_nodes_by_type(node, "export_specifier") {
            let name_node = spec
                .child_by_field_name("name")
                .or_else(|| spec.child_by_field_name("value"));
            let Some(name_node) = name_node else { continue };
            let name = node_text(name_node, source).trim().to_string();
            if !name.is_empty() {
                exported_names.insert(name);
            }
        }
        return;
    }

    if let Some(base_kind) = type_kind_for(node.kind()) {
        let mut kind = base_kind.to_string();
        let mut name = extract_name_field(node, source).unwrap_or_default();
        if name.is_empty() {
            name = anonymous_name(&capitalize(&kind), node);
            kind = format!("anonymous_{kind}");
        }
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(type_stack);
        let qualified = if full.is_empty() {
            name.clone()
        } else {
            format!("{}::{name}", full.join("::"))
        };
        let node_type_id = type_id(&qualified);
        let (snippet, start_line, end_line) = node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        let display_name = qualified.rsplit("::").next().unwrap_or(&qualified).to_string();
        state.types.push(TypeDef {
            symbol_id: node_type_id.clone(),
            qualified_name: qualified,
            name: display_name,
            kind,
            file_path: state.rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment,
            summary,
            note,
            exported: exported_context,
        });
        if !namespace_stack.is_empty() {
            state.relations.push(RelationEdge {
                source_id: namespace_id(&namespace_stack.join("::")),
                target_id: node_type_id.clone(),
                rel_type: "CONTAINS".to_string(),
            });
        }
        if !type_stack.is_empty() {
            let mut parent: Vec<String> = namespace_stack.to_vec();
            parent.extend_from_slice(type_stack);
            state.relations.push(RelationEdge {
                source_id: type_id(&parent.join("::")),
                target_id: node_type_id.clone(),
                rel_type: "CONTAINS".to_string(),
            });
        }
        let mut child_stack: Vec<String> = type_stack.to_vec();
        child_stack.push(name);
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            walk_tree(state, child, namespace_stack, &child_stack, false, exported_names);
        }
        return;
    }

    if function_kind_for(node.kind()).is_some() {
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

    if matches!(node.kind(), "lexical_declaration" | "variable_declaration") {
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            if child.kind() != "variable_declarator" {
                continue;
            }
            let init = child
                .child_by_field_name("value")
                .or_else(|| child.child_by_field_name("initializer"));
            let Some(init) = init else { continue };
            if !matches!(
                init.kind(),
                "arrow_function" | "function" | "generator_function" | "function_expression"
            ) {
                continue;
            }
            let name = extract_name_field(child, source);
            record_function(
                state,
                child,
                namespace_stack,
                type_stack,
                name,
                Some("function_variable".to_string()),
                Some(init),
                Some(init),
                exported_context,
            );
        }
        // Python KHÔNG return — tiếp tục walk để bắt nested declarations.
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_tree(
            state,
            child,
            namespace_stack,
            type_stack,
            exported_context,
            exported_names,
        );
    }
}

/// `parse_js_file` — walk 1 file, trả payload asdict-shape.
pub fn parse_js_file(path: &Path, root: &Path) -> Result<FilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let ext = path
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| format!(".{ext}"))
        .unwrap_or_default()
        .to_lowercase();
    let _is_jsx = ext == ".jsx"; // dispatch _get_js_parser — cả 2 nhánh về javascript grammar
    let source_bytes = std::fs::read(path).map_err(|e| format!("[read] {rel_path}: {e}"))?;
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_javascript::LANGUAGE.into())
        .map_err(|e| e.to_string())?;
    let tree = parser
        .parse(&source_bytes, None)
        .ok_or_else(|| format!("parse failed: {rel_path}"))?;

    let has_error = tree.root_node().has_error();
    let error_nodes = find_nodes_by_type(tree.root_node(), "ERROR").len() as i64;
    let snippet = decode_ignore(&source_bytes);
    let end_line = snippet.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment(tree.root_node(), &source_bytes);
    let file_summary = file_comment.clone();
    let file_note = build_note(&snippet, &file_comment, &file_summary);
    let imports = collect_imports(tree.root_node(), &source_bytes);
    let exports = collect_exports(tree.root_node(), &source_bytes);
    let (jsx_tags, jsx_components) = collect_jsx_tags(tree.root_node(), &source_bytes);
    let file_def = FileDef {
        file_path: rel_path.clone(),
        start_line: 1,
        end_line,
        code: snippet,
        comment: file_comment,
        summary: file_summary,
        note: file_note,
        imports,
        exports,
        jsx_tags,
        jsx_components,
    };

    let mut state = WalkState {
        source: &source_bytes,
        rel_path,
        functions: Vec::new(),
        calls: Vec::new(),
        types: Vec::new(),
        namespaces: Vec::new(),
        relations: Vec::new(),
    };
    let mut exported_names: BTreeSet<String> = BTreeSet::new();
    walk_tree(
        &mut state,
        tree.root_node(),
        &[],
        &[],
        false,
        &mut exported_names,
    );
    if !exported_names.is_empty() {
        for func in &mut state.functions {
            if func.exported {
                continue;
            }
            if func.scope_name.is_none() && exported_names.contains(&func.name) {
                func.exported = true;
            }
        }
        for type_def in &mut state.types {
            if type_def.exported {
                continue;
            }
            if !type_def.qualified_name.contains("::") && exported_names.contains(&type_def.name) {
                type_def.exported = true;
            }
        }
    }
    Ok(FilePayload {
        functions: state.functions,
        calls: state.calls,
        types: state.types,
        namespaces: state.namespaces,
        relations: state.relations,
        file_def: Some(file_def),
        parse_meta: ParseMeta {
            has_error,
            error_nodes,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_call_name_variants() {
        assert_eq!(normalize_call_name("this.helper"), "helper");
        assert_eq!(normalize_call_name("super.run"), "run");
        assert_eq!(normalize_call_name("obj?.fn"), "fn");
        assert_eq!(normalize_call_name("A::B::run"), "run");
        assert_eq!(normalize_call_name("obj['quoted key']"), "quoted key");
        assert_eq!(normalize_call_name("obj['key'] "), "key");
        assert_eq!(normalize_call_name("Map<K,V>.set"), "set");
        assert_eq!(normalize_call_name("plain"), "plain");
        assert_eq!(normalize_call_name("a.b.c"), "c");
        assert_eq!(normalize_call_name("<T>"), "");
    }

    #[test]
    fn symbol_id_format() {
        assert_eq!(symbol_id(Some("A::B"), "go", 2, "src/a.js"), "A::B::go/2@src/a.js");
        assert_eq!(symbol_id(None, "go", 0, "a.js"), "go/0@a.js");
        assert_eq!(namespace_id("NS"), "namespace::NS");
        assert_eq!(type_id("A"), "A");
    }

    #[test]
    fn build_note_sections() {
        assert_eq!(build_note("code", "comment", "summary").split("\n\n").count(), 3);
        assert_eq!(build_note("", "", ""), "");
        assert_eq!(build_note("code", "", ""), "Code:\ncode");
    }

    fn parse_str(code: &[u8]) -> tree_sitter::Tree {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&tree_sitter_javascript::LANGUAGE.into())
            .expect("js language");
        parser.parse(code, None).expect("tree")
    }

    #[test]
    fn parses_class_method_and_arrow_variable() {
        let code: &[u8] = b"class Greeter {\n  // greet doc\n  greet(name) {\n    return helper(name);\n  }\n}\nconst run = (xs) => xs.map((x) => trim(x));\n";
        let tree = parse_str(code);
        let source: &[u8] = code;
        let mut state = WalkState {
            source,
            rel_path: "t.js".to_string(),
            functions: Vec::new(),
            calls: Vec::new(),
            types: Vec::new(),
            namespaces: Vec::new(),
            relations: Vec::new(),
        };
        let mut exported = BTreeSet::new();
        walk_tree(&mut state, tree.root_node(), &[], &[], false, &mut exported);
        assert_eq!(state.types.len(), 1);
        assert_eq!(state.types[0].name, "Greeter");
        assert_eq!(state.functions.len(), 2);
        assert_eq!(state.functions[0].kind, "method");
        assert_eq!(state.functions[0].symbol_id, "Greeter::greet/1@t.js");
        assert_eq!(state.functions[1].kind, "function_variable");
        assert_eq!(state.functions[1].name, "run");
        assert_eq!(state.functions[1].arity, 1);
        // calls: helper (method), map + trim (arrow — calls_root=init)
        let callee_names: Vec<&str> = state
            .calls
            .iter()
            .map(|call| call.callee_name.as_str())
            .collect();
        assert_eq!(callee_names, vec!["helper", "map", "trim"]);
        // Type CONTAINS method relation
        assert!(state.relations.iter().any(|rel| {
            rel.source_id == "Greeter"
                && rel.target_id == "Greeter::greet/1@t.js"
                && rel.rel_type == "CONTAINS"
        }));
    }
}

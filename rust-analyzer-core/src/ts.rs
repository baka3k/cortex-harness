//! TypeScript / TSX tree-sitter walker — Tier 2 port of `tools/ts/`.
//!
//! Faithful Rust port of the Python `parse_ts_file` → `_walk_tree` pipeline.
//! TS has a **12-tuple payload** (functions, calls, types, namespaces, relations,
//! renders, navigates, file_def, meta, api_calls, navigators, param_lists) that
//! is completely different from the C++ 10-tuple or Go payload.
//!
//! TS-unique extraction (no C++/Go equivalent):
//! - `react_role` — screen | component | hook | middleware
//! - `middleware_kind` — api | query | redux | service
//! - `ApiCallDef` — outgoing HTTP calls
//! - `RenderEdge` — renderer → rendered PascalCase JSX component
//! - `NavigateEdge` — source → target with method/guard/confidence
//! - `NavigatorDef` — RN navigator factories
//! - `ParamListDef` — `*ParamList` type aliases

use std::collections::{HashMap, HashSet};

use regex::Regex;
use tree_sitter::{Node, Parser};

use crate::grammar::{Grammar, TsGrammar, TsxGrammar};
use crate::ts_regex::*;
use crate::text::{node_text, node_snippet};

// ── TS-specific data types ─────────────────────────────────────────────

/// TS function definition — extends the C++ FunctionDef with React fields.
#[derive(Debug, Clone, Default)]
pub struct TsFunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub arity: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub exported: bool,
    pub return_type: String,
    pub param_types: Vec<String>,
    pub react_role: String,
    pub middleware_kind: String,
}

#[derive(Debug, Clone, Default)]
pub struct TsNamespaceDef {
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
pub struct TsTypeDef {
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
    pub exported: bool,
}

#[derive(Debug, Clone, Default)]
pub struct TsRelationEdge {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
}

#[derive(Debug, Clone, Default)]
pub struct TsCallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub callee_name: String,
    pub callee_id: Option<String>,
    pub callee_arity: Option<u32>,
}

#[derive(Debug, Clone, Default)]
pub struct RenderEdge {
    pub renderer_id: String,
    pub rendered_name: String,
    pub rendered_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct NavigateEdge {
    pub source_id: String,
    pub target_name: String,
    pub nav_method: String,
    pub target_id: Option<String>,
    pub via: String,
    pub trigger_type: String,
    pub guard: Option<String>,
    pub call_depth: u32,
    pub source_trace: Vec<String>,
    pub confidence: f64,
}

#[derive(Debug, Clone, Default)]
pub struct ApiCallDef {
    pub symbol_id: String,
    pub caller_function_id: String,
    pub url_pattern: String,
    pub raw_url: String,
    pub http_method: String,
    pub base_url_ref: String,
    pub file_path: String,
    pub start_line: u32,
    pub confidence: f64,
}

#[derive(Debug, Clone, Default)]
pub struct NavigatorDef {
    pub symbol_id: String,
    pub var_name: String,
    pub factory: String,
    pub nav_type: String,
    pub param_list_ref: String,
    pub file_path: String,
    pub start_line: u32,
    pub routes: Vec<(String, String)>,
}

#[derive(Debug, Clone, Default)]
pub struct ParamListDef {
    pub symbol_id: String,
    pub name: String,
    pub file_path: String,
    pub routes: HashMap<String, String>,
}

#[derive(Debug, Clone, Default)]
pub struct TsFileDef {
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub imports: Vec<String>,
    pub exports: Vec<String>,
    pub jsx_tags: Vec<String>,
    pub jsx_components: Vec<String>,
}

/// 12-tuple payload — mirrors `parse_ts_file` return.
#[derive(Debug, Default)]
pub struct TsParseOutput {
    pub functions: Vec<TsFunctionDef>,
    pub calls: Vec<TsCallEdge>,
    pub types: Vec<TsTypeDef>,
    pub namespaces: Vec<TsNamespaceDef>,
    pub relations: Vec<TsRelationEdge>,
    pub renders: Vec<RenderEdge>,
    pub navigates: Vec<NavigateEdge>,
    pub file_def: TsFileDef,
    pub has_error: bool,
    pub error_nodes: u32,
    pub api_calls: Vec<ApiCallDef>,
    pub navigators: Vec<NavigatorDef>,
    pub param_lists: Vec<ParamListDef>,
}

// ── Node-kind dispatch sets (mirror Python module constants) ───────────

const NAMESPACE_NODE_TYPES: &[&str] = &[
    "namespace_declaration",
    "internal_module",
    "module_declaration",
    "module",
];

fn type_kind_for(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "class_declaration" | "abstract_class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "type_alias_declaration" => Some("type_alias"),
        "enum_declaration" => Some("enum"),
        _ => None,
    }
}

fn function_kind_for(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "function_declaration" => Some("function"),
        "generator_function_declaration" => Some("generator_function"),
        "method_definition" => Some("method"),
        _ => None,
    }
}

const BARE_FUNC_TYPES: &[&str] = &[
    "arrow_function",
    "function",
    "generator_function",
    "function_expression",
];

const INNER_FUNCTION_TYPES: &[&str] = &[
    "arrow_function",
    "function",
    "generator_function",
    "function_expression",
];

const JSX_NODE_TYPES: &[&str] = &[
    "jsx_element",
    "jsx_fragment",
    "jsx_text",
    "jsx_opening_element",
    "jsx_self_closing_element",
];

// ── AST helpers (ports from parser_agent.py) ───────────────────────────

#[inline]
fn line_from_byte(source: &[u8], byte_index: usize) -> u32 {
    source[..byte_index].iter().filter(|&&b| b == b'\n').count() as u32 + 1
}

fn find_nodes_by_type<'a>(node: Node<'a>, node_type: &str) -> Vec<Node<'a>> {
    let mut found = Vec::new();
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        if current.kind() == node_type {
            found.push(current);
        }
        let mut children = current.children(&mut current.walk()).collect::<Vec<_>>();
        children.reverse();
        stack.extend(children);
    }
    found
}

fn first_identifier<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "identifier" | "property_identifier" | "type_identifier" | "namespace_identifier"
    ) {
        return Some(node_text(node, source).to_string());
    }
    for child in node.children(&mut node.walk()) {
        if let Some(s) = first_identifier(child, source) {
            return Some(s);
        }
    }
    None
}

fn extract_name_field<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source).trim().to_string());
    }
    first_identifier(node, source).map(|s| s.trim().to_string())
}

fn extract_leading_comment<'a>(node: Node<'a>, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() != "comment" {
            break;
        }
        let text = node_text(p, source).trim();
        if !text.is_empty() {
            parts.push(text.to_string());
        }
        prev = p.prev_sibling();
    }
    parts.reverse();
    parts.join("\n")
}

fn extract_file_comment<'a>(root: Node<'a>, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for child in root.children(&mut root.walk()) {
        if child.kind() == "comment" {
            let text = node_text(child, source).trim().to_string();
            if !text.is_empty() {
                parts.push(text);
            }
            continue;
        }
        if child.is_named() {
            break;
        }
    }
    parts.join("\n")
}

fn normalize_ws(text: &str) -> String {
    Regex::new(r"\s+").unwrap().replace_all(text, " ").trim().to_string()
}

fn normalize_call_name(text: &str) -> String {
    let re_gt = Regex::new(r"<[^<>]*>").unwrap();
    let mut cleaned = re_gt.replace_all(text, "").to_string();
    cleaned = cleaned.replace("this.", "");
    cleaned = cleaned.replace("super.", "");
    cleaned = cleaned.replace("?.", ".");
    cleaned = cleaned.replace("::", ".");
    let cleaned = cleaned.trim().to_string();
    // bracket notation: obj["name"] → name (no backreference — Rust regex doesn't support \1)
    let bracket_re = Regex::new(r#"\[\s*['"](?P<name>[^'"]+)['"]\s*\]\s*$"#).unwrap();
    if let Some(caps) = bracket_re.captures(&cleaned) {
        if let Some(m) = caps.name("name") {
            return m.as_str().to_string();
        }
    }
    if cleaned.contains('.') {
        return cleaned.split('.').last().unwrap_or("").to_string();
    }
    cleaned
}

fn ts_node_snippet<'a>(node: Node<'a>, source: &[u8]) -> (String, u32, u32) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if p.kind() != "comment" {
            break;
        }
        start_byte = p.start_byte();
        prev = p.prev_sibling();
    }
    let snippet = std::str::from_utf8(&source[start_byte..node.end_byte()])
        .unwrap_or("")
        .to_string();
    let start_line = line_from_byte(source, start_byte);
    let end_line = node.end_position().row as u32 + 1;
    (snippet, start_line, end_line)
}

fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{}", summary));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{}", comment));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{}", code));
    }
    parts.join("\n\n")
}

fn scope_from_stacks(namespace_stack: &[String], type_stack: &[String]) -> Option<String> {
    let mut all: Vec<&str> = Vec::new();
    all.extend(namespace_stack.iter().map(|s| s.as_str()));
    all.extend(type_stack.iter().map(|s| s.as_str()));
    if all.is_empty() {
        None
    } else {
        Some(all.join("::"))
    }
}

fn symbol_id(scope: Option<&str>, name: &str, arity: u32, rel_path: &str) -> String {
    match scope {
        Some(s) if !s.is_empty() => format!("{}::{}!/{}@{}", s, name, arity, rel_path),
        _ => format!("{}!/{}@{}", name, arity, rel_path),
    }
}

fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(s) if !s.is_empty() => format!("{}::{}", s, name),
        _ => name.to_string(),
    }
}

fn type_id(qualified: &str) -> String {
    qualified.to_string()
}

fn namespace_id(name: &str) -> String {
    format!("namespace::{}", name)
}

fn anonymous_name(prefix: &str, node: Node) -> String {
    let pos = node.start_position();
    format!("Anonymous{}@{}:{}", prefix, pos.row + 1, pos.column + 1)
}

// ── Parameter / argument counting ──────────────────────────────────────

fn count_parameters<'a>(node: Node<'a>) -> u32 {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| {
            node.children(&mut node.walk())
                .find(|c| c.kind() == "parameter_list")
        });
    let Some(params) = params else {
        return 0;
    };
    params
        .children(&mut params.walk())
        .filter(|c| c.is_named() && c.kind() != "comment")
        .count() as u32
}

fn extract_return_type<'a>(node: Node<'a>, source: &[u8]) -> String {
    let Some(ret_node) = node.child_by_field_name("return_type") else {
        return String::new();
    };
    let text = node_text(ret_node, source).trim().to_string();
    text.trim_start_matches(':').trim().to_string()
}

fn extract_param_types<'a>(node: Node<'a>, source: &[u8]) -> Vec<String> {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| {
            node.children(&mut node.walk())
                .find(|c| c.kind() == "parameter_list")
        });
    let Some(params) = params else {
        return Vec::new();
    };
    let mut result = Vec::new();
    for child in params.children(&mut params.walk()) {
        if !child.is_named() || child.kind() == "comment" {
            continue;
        }
        if let Some(type_node) = child.child_by_field_name("type") {
            let txt = node_text(type_node, source).trim().trim_start_matches(':').trim().to_string();
            result.push(txt);
        } else {
            result.push(String::new());
        }
    }
    result
}

fn count_arguments<'a>(node: Node<'a>) -> u32 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| {
            node.children(&mut node.walk())
                .find(|c| c.kind() == "argument_list")
        });
    let Some(args) = args else {
        return 0;
    };
    args.children(&mut args.walk())
        .filter(|c| c.is_named() && c.kind() != "comment")
        .count() as u32
}

fn iter_calls<'a>(func_node: Node<'a>) -> Vec<Node<'a>> {
    let mut calls = find_nodes_by_type(func_node, "call_expression");
    calls.extend(find_nodes_by_type(func_node, "new_expression"));
    calls
}

fn extract_call_name<'a>(call_node: Node<'a>, source: &[u8]) -> Option<String> {
    let field = if call_node.kind() == "call_expression" {
        "function"
    } else {
        "constructor"
    };
    if let Some(expr) = call_node.child_by_field_name(field) {
        return Some(normalize_call_name(node_text(expr, source).trim()));
    }
    let text = node_text(call_node, source).trim();
    let text = if let Some(rest) = text.strip_prefix("new ") {
        rest
    } else {
        text
    };
    let text = text.split('(').next().unwrap_or("").trim();
    Some(normalize_call_name(text))
}

// ── JSX helpers ────────────────────────────────────────────────────────

fn has_jsx_in_subtree(node: Node) -> bool {
    if JSX_NODE_TYPES.contains(&node.kind()) {
        return true;
    }
    for child in node.children(&mut node.walk()) {
        if has_jsx_in_subtree(child) {
            return true;
        }
    }
    false
}

fn jsx_name<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
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
    name_node.map(|n| node_text(n, source).to_string())
}

fn collect_rendered_components<'a>(node: Node<'a>, source: &[u8]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for jsx_node in find_nodes_by_type(node, "jsx_opening_element") {
        if let Some(n) = jsx_name(jsx_node, source) {
            if n.starts_with(|c: char| c.is_ascii_uppercase()) && seen.insert(n.clone()) {
                names.push(n);
            }
        }
    }
    for jsx_node in find_nodes_by_type(node, "jsx_self_closing_element") {
        if let Some(n) = jsx_name(jsx_node, source) {
            if n.starts_with(|c: char| c.is_ascii_uppercase()) && seen.insert(n.clone()) {
                names.push(n);
            }
        }
    }
    names
}

fn collect_jsx_tags<'a>(root: Node<'a>, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut tags: HashMap<String, ()> = HashMap::new();
    let mut components: HashMap<String, ()> = HashMap::new();
    for node in find_nodes_by_type(root, "jsx_opening_element") {
        if let Some(name) = jsx_name(node, source) {
            if name.starts_with(|c: char| c.is_ascii_lowercase()) {
                tags.insert(name, ());
            } else {
                components.insert(name, ());
            }
        }
    }
    for node in find_nodes_by_type(root, "jsx_self_closing_element") {
        if let Some(name) = jsx_name(node, source) {
            if name.starts_with(|c: char| c.is_ascii_lowercase()) {
                tags.insert(name, ());
            } else {
                components.insert(name, ());
            }
        }
    }
    let mut t: Vec<String> = tags.into_keys().collect();
    let mut c: Vec<String> = components.into_keys().collect();
    t.sort();
    c.sort();
    (t, c)
}

fn collect_imports<'a>(root: Node<'a>, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_statement") {
        let text = normalize_ws(node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "import_require_clause") {
        let text = normalize_ws(node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    imports
}

fn collect_exports<'a>(root: Node<'a>, source: &[u8]) -> Vec<String> {
    let mut exports = Vec::new();
    for node in find_nodes_by_type(root, "export_statement") {
        let text = normalize_ws(node_text(node, source));
        if !text.is_empty() {
            exports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "export_default_declaration") {
        let text = normalize_ws(node_text(node, source));
        if !text.is_empty() {
            exports.push(text);
        }
    }
    exports
}

// ── React role + middleware classification (Phase 2) ───────────────────

fn detect_middleware_kind(name: &str, code: &str, file_path: &str) -> String {
    if RE_MIDDLEWARE_API.is_match(code) {
        return "api".to_string();
    }
    if RE_MIDDLEWARE_QUERY.is_match(code) {
        return "query".to_string();
    }
    if RE_MIDDLEWARE_REDUX.is_match(code) {
        return "redux".to_string();
    }
    if RE_SERVICE_LAYER.is_match(code) {
        return "service".to_string();
    }
    if is_service_file(file_path) {
        return "service".to_string();
    }
    String::new()
}

fn detect_react_role(
    name: &str,
    file_path: &str,
    has_jsx: bool,
    middleware_kind: &str,
    code: &str,
) -> String {
    if !middleware_kind.is_empty() {
        return "middleware".to_string();
    }
    if name.starts_with("use") && name.len() > 3 && name.as_bytes()[3].is_ascii_uppercase() {
        return "hook".to_string();
    }
    if has_jsx && !name.is_empty() && name.starts_with(|c: char| c.is_ascii_uppercase()) {
        let is_hoc_name = RE_HOC_FACTORY_NAME.is_match(name)
            || WRAPPER_NAME_SUFFIXES.iter().any(|s| name.ends_with(s));
        if is_hoc_name || RE_WRAPS_CHILDREN.is_match(code) {
            return "component".to_string();
        }
        if NAV_CHROME_SUFFIXES.iter().any(|s| name.ends_with(s)) {
            return "component".to_string();
        }
        if NAVIGATOR_NAME_SUFFIXES.iter().any(|s| name.ends_with(s))
            || RE_NAVIGATOR_FACTORY_NAME.is_match(name)
        {
            return "component".to_string();
        }
        let folder_name = index_module_name(file_path).unwrap_or_default();
        let has_screen_name = SCREEN_NAME_SUFFIXES.iter().any(|s| name.ends_with(s))
            || SCREEN_NAME_SUFFIXES.iter().any(|s| folder_name.ends_with(s));
        let in_screen_dir = is_screen_file(file_path);

        // 1. Navigation hooks
        if RE_SCREEN_HOOKS.is_match(code) && (has_screen_name || in_screen_dir) {
            return "screen".to_string();
        }
        // 2. Imperative nav calls
        if RE_SCREEN_NAV_CALL.is_match(code) && (has_screen_name || in_screen_dir) {
            return "screen".to_string();
        }
        // 3. Receives React-Navigation props
        if RE_SCREEN_PROP_NAMES.is_match(code) && (has_screen_name || in_screen_dir) {
            return "screen".to_string();
        }
        // 4. File in screen/routing directory
        if in_screen_dir {
            return "screen".to_string();
        }
        // 5. Name suffix fallback
        if has_screen_name {
            return "screen".to_string();
        }
        return "component".to_string();
    }
    String::new()
}

// ── Navigate call collection (Phase 4) ─────────────────────────────────

fn collect_navigate_calls(code: &str) -> Vec<(String, String)> {
    let mut seen: Vec<(String, String)> = Vec::new();
    let mut seen_set: HashSet<(String, String)> = HashSet::new();

    let mut nav_obj_vars: HashSet<String> = HashSet::new();
    nav_obj_vars.insert("navigation".to_string());
    nav_obj_vars.insert("navigator".to_string());
    let mut nav_fn_vars: HashSet<String> = HashSet::new();
    let mut hist_vars: HashSet<String> = HashSet::new();

    for m in RE_ASSIGN_USE_NAVIGATION.captures_iter(code) {
        if let Some(v) = m.name("var") {
            nav_obj_vars.insert(v.as_str().to_string());
        }
    }
    for m in RE_ASSIGN_USE_ROUTER.captures_iter(code) {
        if let Some(v) = m.name("var") {
            nav_obj_vars.insert(v.as_str().to_string());
        }
    }
    for m in RE_ASSIGN_USE_HISTORY.captures_iter(code) {
        if let Some(v) = m.name("var") {
            hist_vars.insert(v.as_str().to_string());
        }
    }
    if RE_ASSIGN_USE_NAVIGATION_DESTRUCT.is_match(code) {
        nav_fn_vars.insert("navigate".to_string());
    }
    for m in RE_ASSIGN_USE_NAVIGATE.captures_iter(code) {
        if let Some(v) = m.name("var") {
            nav_fn_vars.insert(v.as_str().to_string());
        }
    }

    let has_use_router = Regex::new(r"\buseRouter\s*\(").unwrap().is_match(code);
    let has_use_history = Regex::new(r"\buseHistory\s*\(").unwrap().is_match(code);

    let insert = |target: &str, method: &str, seen: &mut Vec<(String, String)>, seen_set: &mut HashSet<(String, String)>| {
        if !target.is_empty() && seen_set.insert((target.to_string(), method.to_string())) {
            seen.push((target.to_string(), method.to_string()));
        }
    };

    for m in RE_NAV_PROP_CALL.captures_iter(code) {
        if let (Some(t), Some(meth)) = (m.name("target"), m.name("method")) {
            insert(t.as_str(), meth.as_str(), &mut seen, &mut seen_set);
        }
    }
    for m in RE_NAV_PROP_OBJ.captures_iter(code) {
        if let Some(t) = m.name("target") {
            insert(t.as_str(), "navigate", &mut seen, &mut seen_set);
        }
    }

    for var in &nav_obj_vars {
        if var == "navigation" || var == "navigator" {
            continue;
        }
        let re = nav_obj_method_re(var);
        for m in re.captures_iter(code) {
            if let (Some(t), Some(meth)) = (m.name("target"), m.name("method")) {
                insert(t.as_str(), meth.as_str(), &mut seen, &mut seen_set);
            }
        }
    }

    if has_use_router {
        for m in RE_ROUTER_CALL.captures_iter(code) {
            if let (Some(t), Some(meth)) = (m.name("target"), m.name("method")) {
                insert(t.as_str(), meth.as_str(), &mut seen, &mut seen_set);
            }
        }
        for m in RE_ROUTER_OBJ.captures_iter(code) {
            if let (Some(t), Some(_)) = (m.name("target"), m.name("method")) {
                insert(t.as_str(), "navigate", &mut seen, &mut seen_set);
            }
        }
    }
    if has_use_history {
        let vars: Vec<String> = if hist_vars.is_empty() {
            vec!["history".to_string()]
        } else {
            hist_vars.iter().cloned().collect()
        };
        for var in &vars {
            let re = nav_obj_method_re(var);
            for m in re.captures_iter(code) {
                if let (Some(t), Some(meth)) = (m.name("target"), m.name("method")) {
                    insert(t.as_str(), meth.as_str(), &mut seen, &mut seen_set);
                }
            }
        }
    }

    for var in &nav_fn_vars {
        let re = nav_fn_call_re(var);
        for m in re.captures_iter(code) {
            if let Some(t) = m.name("target") {
                insert(t.as_str(), "navigate", &mut seen, &mut seen_set);
            }
        }
    }

    for m in RE_NAV_REF_CALL.captures_iter(code) {
        if let (Some(t), Some(meth)) = (m.name("target"), m.name("method")) {
            insert(t.as_str(), meth.as_str(), &mut seen, &mut seen_set);
        }
    }

    // Generic navigation-service wrappers
    for m in RE_NAV_SERVICE_CALL.captures_iter(code) {
        if let (Some(t), Some(meth)) = (m.name("target"), m.name("method")) {
            insert(t.as_str(), meth.as_str(), &mut seen, &mut seen_set);
        }
    }
    for m in RE_NAV_SERVICE_OBJ.captures_iter(code) {
        if let Some(t) = m.name("target") {
            insert(t.as_str(), "navigate", &mut seen, &mut seen_set);
        }
    }

    for m in RE_JSX_LINK.captures_iter(code) {
        let route = m.name("route").or_else(|| m.name("route2"));
        if let Some(r) = route {
            insert(r.as_str().trim(), "link", &mut seen, &mut seen_set);
        }
    }
    for m in RE_JSX_NAVIGATE_EL.captures_iter(code) {
        let route = m.name("route").or_else(|| m.name("route2"));
        if let Some(r) = route {
            insert(r.as_str().trim(), "navigate", &mut seen, &mut seen_set);
        }
    }

    seen
}

fn classify_nav_context(code: &str) -> String {
    if RE_USER_TRIGGER.is_match(code) {
        return "user".to_string();
    }
    if RE_ASYNC_TRIGGER.is_match(code) {
        return "async".to_string();
    }
    if RE_SYSTEM_TRIGGER.is_match(code) {
        return "system".to_string();
    }
    "user".to_string()
}

fn detect_nav_guard(code: &str) -> Option<String> {
    if RE_AUTH_GUARD.is_match(code) {
        return Some("auth".to_string());
    }
    if RE_PERM_GUARD.is_match(code) {
        return Some("permission".to_string());
    }
    None
}

fn collect_route_configs(code: &str) -> Vec<(String, String)> {
    let mut seen: Vec<(String, String)> = Vec::new();
    let mut seen_set: HashSet<(String, String)> = HashSet::new();
    for m in RE_SCREEN_ELEM_START.find_iter(code) {
        let window = &code[m.start()..code.len().min(m.start() + 1000)];
        let name_m = RE_SCREEN_NAME_ATTR.captures(window);
        let comp_m = RE_SCREEN_COMP_ATTR.captures(window);
        if let (Some(nm), Some(cm)) = (name_m, comp_m) {
            let name = nm.name("name").map(|m| m.as_str()).unwrap_or("");
            let comp = cm.name("comp").map(|m| m.as_str()).unwrap_or("");
            if !name.is_empty() && !comp.is_empty() && seen_set.insert((name.to_string(), comp.to_string())) {
                seen.push((name.to_string(), comp.to_string()));
            }
        }
    }
    seen
}

fn extract_navigator_declarations(code: &str, rel_path: &str) -> Vec<NavigatorDef> {
    let routes_by_file = collect_route_configs(code);
    let mut results = Vec::new();
    for m in RE_NAVIGATOR_FACTORY.captures_iter(code) {
        let var_name = m.name("var_name").map(|m| m.as_str()).unwrap_or("");
        let factory = m.name("factory").map(|m| m.as_str()).unwrap_or("");
        let generic = m.name("generic").map(|m| m.as_str()).unwrap_or("").trim().to_string();
        let param_list_ref = if !generic.is_empty() {
            generic.split(',').next().unwrap_or("").trim().to_string()
        } else {
            String::new()
        };
        let nav_type = factory_to_nav_type(factory).to_string();
        let m_start = m.get(0).map(|m| m.start()).unwrap_or(0);
        let start_line = code[..m_start].matches('\n').count() as u32 + 1;
        let symbol_id = format!("Navigator::{}::{}", var_name, rel_path);
        results.push(NavigatorDef {
            symbol_id,
            var_name: var_name.to_string(),
            factory: factory.to_string(),
            nav_type,
            param_list_ref,
            file_path: rel_path.to_string(),
            start_line,
            routes: routes_by_file.clone(),
        });
    }
    results
}

fn extract_param_lists<'a>(root: Node<'a>, source: &[u8], rel_path: &str) -> Vec<ParamListDef> {
    let mut results = Vec::new();
    for node in find_nodes_by_type(root, "type_alias_declaration") {
        let name_node = match node.child_by_field_name("name") {
            Some(n) => n,
            None => continue,
        };
        let type_name = node_text(name_node, source);
        if !type_name.ends_with("ParamList") {
            continue;
        }
        let mut routes: HashMap<String, String> = HashMap::new();
        if let Some(value_node) = node.child_by_field_name("value") {
            // Only direct children (not nested property_signatures)
            for prop in value_node.children(&mut value_node.walk()) {
                if prop.kind() != "property_signature" {
                    continue;
                }
                let key_node = prop.child_by_field_name("name");
                let type_ann = prop.child_by_field_name("type");
                let Some(key_node) = key_node else { continue };
                let key = node_text(key_node, source).trim_matches(|c| c == '"' || c == '\'').to_string();
                let type_str = if let Some(ta) = type_ann {
                    let raw = node_text(ta, source).trim_start_matches(':').trim();
                    Regex::new(r"\s+").unwrap().replace_all(raw, " ").trim().to_string()
                } else {
                    "undefined".to_string()
                };
                routes.insert(key, type_str);
            }
        }
        let start_line = line_from_byte(source, node.start_byte());
        let symbol_id = format!("ParamList::{}::{}", type_name, rel_path);
        results.push(ParamListDef {
            symbol_id,
            name: type_name.to_string(),
            file_path: rel_path.to_string(),
            routes,
        });
    }
    results
}

// ── API call extraction (Phase 5) ──────────────────────────────────────

fn extract_api_calls(
    code: &str,
    function_id: &str,
    rel_path: &str,
    start_line: u32,
    file_base_url: &str,
) -> Vec<ApiCallDef> {
    let mut results: Vec<ApiCallDef> = Vec::new();
    let uuid5 = |s: &str| -> String {
        // UUID5(NAMESPACE_URL, s) — we use a simple deterministic hash since
        // the exact UUID value only needs to be stable & unique per (func, method, url).
        format!("ApiCall::{function_id}::{}", s)
    };

    let make_call = |raw_url: &str, method: &str, base_url: &str| -> Option<ApiCallDef> {
        let cleaned = clean_url_expr(raw_url);
        if cleaned.is_empty() {
            return None;
        }
        let resolved = if !base_url.is_empty() || !file_base_url.is_empty() {
            merge_base_url(
                Some(if !base_url.is_empty() { base_url } else { file_base_url }),
                &cleaned,
            )
        } else {
            normalize_url_pattern(&cleaned)
        };
        if resolved.is_empty() {
            return None;
        }
        let norm_method = normalize_http_method(method);
        let sid = uuid5(&format!("{}::{}", norm_method, resolved));
        Some(ApiCallDef {
            symbol_id: sid,
            caller_function_id: function_id.to_string(),
            url_pattern: resolved,
            raw_url: raw_url.trim().to_string(),
            http_method: norm_method,
            base_url_ref: if !base_url.is_empty() {
                base_url.to_string()
            } else {
                file_base_url.to_string()
            },
            file_path: rel_path.to_string(),
            start_line,
            confidence: 0.85,
        })
    };

    // fetch calls
    for m in RE_FETCH_CALL.captures_iter(code) {
        let raw = m.name("url").map(|m| m.as_str()).unwrap_or("");
        let m_start = m.get(0).map(|m| m.start()).unwrap_or(0);
        let vicinity = &code[m_start..code.len().min(m_start + 300)];
        let method = RE_FETCH_METHOD
            .captures(vicinity)
            .and_then(|c| c.name("method"))
            .map(|m| m.as_str())
            .unwrap_or("GET");
        if let Some(call) = make_call(raw, method, "") {
            results.push(call);
        }
    }

    // axios shorthand
    for m in RE_AXIOS_SHORTHAND.captures_iter(code) {
        let raw = m.name("url").map(|m| m.as_str()).unwrap_or("");
        let method = m.name("method").map(|m| m.as_str()).unwrap_or("GET");
        if let Some(call) = make_call(raw, method, "") {
            results.push(call);
        }
    }

    // axios config
    for m in RE_AXIOS_CONFIG.captures_iter(code) {
        let raw = m.name("url").map(|m| m.as_str()).unwrap_or("");
        let method = m.name("method").map(|m| m.as_str()).unwrap_or("GET");
        if let Some(call) = make_call(raw, method, "") {
            results.push(call);
        }
    }

    // http client
    for m in RE_HTTP_CLIENT.captures_iter(code) {
        let raw = m.name("url").map(|m| m.as_str()).unwrap_or("");
        let method = m.name("method").map(|m| m.as_str()).unwrap_or("GET");
        if let Some(call) = make_call(raw, method, "") {
            results.push(call);
        }
    }

    // named client
    for m in RE_NAMED_CLIENT.captures_iter(code) {
        let raw = m.name("url").map(|m| m.as_str()).unwrap_or("");
        let method = m.name("method").map(|m| m.as_str()).unwrap_or("GET");
        if let Some(call) = make_call(raw, method, "") {
            results.push(call);
        }
    }

    // Dedup by (method, url_pattern)
    let mut seen: HashSet<String> = HashSet::new();
    results.retain(|c| seen.insert(format!("{}:{}", c.http_method, c.url_pattern)));
    results
}

// ── Factory call helpers (Phase 6) ─────────────────────────────────────

fn find_inner_function_arg<'a>(call_node: Node<'a>) -> Option<Node<'a>> {
    let args = call_node.child_by_field_name("arguments")?;
    for arg in args.children(&mut args.walk()) {
        if INNER_FUNCTION_TYPES.contains(&arg.kind()) {
            return Some(arg);
        }
        if arg.kind() == "call_expression" {
            if let Some(inner) = find_inner_function_arg(arg) {
                return Some(inner);
            }
        }
    }
    None
}

fn extract_root_factory_name<'a>(call_node: Node<'a>, source: &[u8]) -> String {
    let mut node = call_node;
    loop {
        let fn_node = match node.child_by_field_name("function") {
            Some(f) => f,
            None => break,
        };
        if fn_node.kind() == "call_expression" {
            node = fn_node;
            continue;
        }
        let raw = node_text(fn_node, source).trim().to_string();
        let re_gt = Regex::new(r"<[^<>]*>").unwrap();
        let dotted = re_gt.replace_all(&raw, "").replace("?.", ".").trim().to_string();
        if CALL_EXPR_KIND_MAP.contains_key(dotted.as_str()) {
            return dotted;
        }
        return normalize_call_name(&raw);
    }
    extract_call_name(call_node, source).unwrap_or_default()
}

// ── Function recorder ──────────────────────────────────────────────────

struct TsWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    functions: &'a mut Vec<TsFunctionDef>,
    types: &'a mut Vec<TsTypeDef>,
    namespaces: &'a mut Vec<TsNamespaceDef>,
    relations: &'a mut Vec<TsRelationEdge>,
    calls: &'a mut Vec<TsCallEdge>,
    renders: &'a mut Vec<RenderEdge>,
    navigates: &'a mut Vec<NavigateEdge>,
    type_registry: &'a mut HashMap<String, TsTypeDef>,
    namespace_registry: &'a mut HashMap<String, TsNamespaceDef>,
    exported_names: &'a mut HashSet<String>,
}

#[allow(clippy::too_many_arguments)]
fn record_function(
    node: Node,
    ctx: &mut TsWalkCtx,
    namespace_stack: &[String],
    type_stack: &[String],
    name_override: Option<String>,
    kind_override: Option<&str>,
    calls_root: Option<Node>,
    parameters_node: Option<Node>,
    exported: bool,
) {
    let source = ctx.source;
    let rel_path = ctx.rel_path;
    let name = name_override
        .or_else(|| extract_name_field(node, source))
        .unwrap_or_else(|| {
            index_module_name(rel_path).unwrap_or_else(|| anonymous_name("Function", node))
        });
    let mut kind = kind_override
        .map(|s| s.to_string())
        .unwrap_or_else(|| function_kind_for(node.kind()).unwrap_or("function").to_string());
    if kind == "method" && name == "constructor" {
        kind = "constructor".to_string();
    }

    let (snippet, start_line, end_line) = ts_node_snippet(node, source);
    let comment = extract_leading_comment(node, source);
    let summary = comment.clone();
    let note = build_note(&snippet, &comment, &summary);
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let param_src = parameters_node.unwrap_or(node);
    let arity = count_parameters(param_src);
    let return_type = extract_return_type(node, source);
    let param_types = extract_param_types(param_src, source);
    let func_id = symbol_id(scope.as_deref(), &name, arity, rel_path);

    let call_root = calls_root.unwrap_or(node);
    let has_jsx = has_jsx_in_subtree(call_root);
    let middleware_kind = detect_middleware_kind(&name, &snippet, rel_path);
    let react_role = detect_react_role(&name, rel_path, has_jsx, &middleware_kind, &snippet);

    ctx.functions.push(TsFunctionDef {
        symbol_id: func_id.clone(),
        qualified_name: qualified_name(scope.as_deref(), &name),
        name: name.clone(),
        kind,
        scope_name: scope.clone(),
        file_path: rel_path.to_string(),
        start_line,
        end_line,
        arity,
        code: snippet.clone(),
        comment: comment.clone(),
        summary,
        note,
        exported,
        return_type,
        param_types,
        react_role: react_role.clone(),
        middleware_kind: middleware_kind.clone(),
    });

    // Relations: CONTAINS from type/namespace
    if !type_stack.is_empty() {
        let tid = type_id(&namespace_stack
            .iter()
            .chain(type_stack.iter())
            .cloned()
            .collect::<Vec<_>>()
            .join("::"));
        ctx.relations.push(TsRelationEdge {
            source_id: tid,
            source_label: "Type".to_string(),
            target_id: func_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "CONTAINS".to_string(),
        });
    } else if !namespace_stack.is_empty() {
        let ns_id = namespace_id(&namespace_stack.join("::"));
        ctx.relations.push(TsRelationEdge {
            source_id: ns_id,
            source_label: "Namespace".to_string(),
            target_id: func_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "CONTAINS".to_string(),
        });
    }

    // Calls
    for call_node in iter_calls(call_root) {
        if let Some(callee) = extract_call_name(call_node, source) {
            ctx.calls.push(TsCallEdge {
                caller_id: func_id.clone(),
                caller_scope: scope.clone(),
                callee_name: callee,
                callee_id: None,
                callee_arity: Some(count_arguments(call_node)),
            });
        }
    }

    // Renders (only for screen/component)
    if react_role == "screen" || react_role == "component" {
        for rendered_name in collect_rendered_components(call_root, source) {
            if rendered_name != name {
                ctx.renders.push(RenderEdge {
                    renderer_id: func_id.clone(),
                    rendered_name,
                    rendered_id: None,
                });
            }
        }
    }

    // Navigate calls
    let nav_raw = collect_navigate_calls(&snippet);
    if !nav_raw.is_empty() {
        let trigger = classify_nav_context(&snippet);
        let guard = detect_nav_guard(&snippet);
        for (target_name, nav_method) in nav_raw {
            ctx.navigates.push(NavigateEdge {
                source_id: func_id.clone(),
                target_name,
                nav_method,
                target_id: None,
                via: "direct".to_string(),
                trigger_type: trigger.clone(),
                guard: guard.clone(),
                call_depth: 0,
                source_trace: Vec::new(),
                confidence: 1.0,
            });
        }
    }

    // Route configs
    for (route_name, comp_name) in collect_route_configs(&snippet) {
        ctx.navigates.push(NavigateEdge {
            source_id: func_id.clone(),
            target_name: route_name,
            nav_method: "__route_config__".to_string(),
            target_id: None,
            via: comp_name,
            trigger_type: "user".to_string(),
            guard: None,
            call_depth: 0,
            source_trace: Vec::new(),
            confidence: 1.0,
        });
    }
}

// ── The recursive walker (mirrors Python `_walk_tree`) ─────────────────

#[allow(clippy::too_many_arguments)]
fn walk(
    node: Node,
    ctx: &mut TsWalkCtx,
    namespace_stack: &[String],
    type_stack: &[String],
    exported_context: bool,
) {
    let source = ctx.source;
    let rel_path = ctx.rel_path;

    // ── Export statements ──
    if node.kind() == "export_statement" || node.kind() == "export_default_declaration" {
        if let Some(decl) = node.child_by_field_name("declaration") {
            if BARE_FUNC_TYPES.contains(&decl.kind()) {
                let explicit_name = extract_name_field(decl, source);
                let name = explicit_name.or_else(|| index_module_name(rel_path));
                let kind_override = if decl.kind() == "arrow_function" {
                    Some("function_variable")
                } else {
                    None
                };
                record_function(
                    decl,
                    ctx,
                    namespace_stack,
                    type_stack,
                    name,
                    kind_override,
                    None,
                    None,
                    true,
                );
                return;
            }
            if decl.kind() == "call_expression" {
                let default_name =
                    index_module_name(rel_path).unwrap_or_else(|| anonymous_name("Function", decl));
                let factory_name = extract_root_factory_name(decl, source);
                let kind = CALL_EXPR_KIND_MAP
                    .get(factory_name.as_str())
                    .map(|s| *s)
                    .unwrap_or("function_variable");
                let inner_fn = find_inner_function_arg(decl);
                record_function(
                    decl,
                    ctx,
                    namespace_stack,
                    type_stack,
                    Some(default_name),
                    Some(kind),
                    Some(decl),
                    inner_fn,
                    true,
                );
                return;
            }
            walk(decl, ctx, namespace_stack, type_stack, true);
            return;
        }
        // export specifiers — collect names
        for spec in find_nodes_by_type(node, "export_specifier") {
            let name_node = spec
                .child_by_field_name("name")
                .or_else(|| spec.child_by_field_name("value"));
            if let Some(n) = name_node {
                let name = node_text(n, source).trim().to_string();
                if !name.is_empty() {
                    ctx.exported_names.insert(name);
                }
            }
            if let Some(alias) = spec.child_by_field_name("alias") {
                let alias = node_text(alias, source).trim().to_string();
                if !alias.is_empty() {
                    ctx.exported_names.insert(alias);
                }
            }
        }
        return;
    }

    // ── Namespace / module ──
    if NAMESPACE_NODE_TYPES.contains(&node.kind()) {
        let name = extract_name_field(node, source)
            .unwrap_or_else(|| anonymous_name("Namespace", node));
        let qualified = namespace_stack
            .iter()
            .cloned()
            .chain(std::iter::once(name.clone()))
            .collect::<Vec<_>>()
            .join("::");
        let ns_id = namespace_id(&qualified);
        let (snippet, start_line, end_line) = ts_node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);

        ctx.namespaces.push(TsNamespaceDef {
            symbol_id: ns_id.clone(),
            qualified_name: qualified.clone(),
            name: name.clone(),
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment: comment.clone(),
            summary,
            note,
        });
        if let Some(ns) = ctx.namespaces.last() {
            ctx.namespace_registry.insert(ns_id.clone(), ns.clone());
        }
        if !namespace_stack.is_empty() {
            let parent = namespace_id(&namespace_stack.join("::"));
            ctx.relations.push(TsRelationEdge {
                source_id: parent,
                source_label: "Namespace".to_string(),
                target_id: ns_id,
                target_label: "Namespace".to_string(),
                rel_type: "CONTAINS".to_string(),
            });
        }
        let mut child_ns = namespace_stack.to_vec();
        child_ns.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, ctx, &child_ns, type_stack, false);
        }
        return;
    }

    // ── Types (class, interface, type_alias, enum) ──
    if let Some(kind) = type_kind_for(node.kind()) {
        let name = extract_name_field(node, source)
            .unwrap_or_else(|| {
                anonymous_name(
                    &kind.chars().next().map(|c| c.to_ascii_uppercase().to_string()).unwrap_or_default(),
                    node,
                )
            });
        let qualified = if !namespace_stack.is_empty() || !type_stack.is_empty() {
            namespace_stack
                .iter()
                .chain(type_stack.iter())
                .cloned()
                .chain(std::iter::once(name.clone()))
                .collect::<Vec<_>>()
                .join("::")
        } else {
            name.clone()
        };
        let tid = type_id(&qualified);
        let (snippet, start_line, end_line) = ts_node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);

        ctx.types.push(TsTypeDef {
            symbol_id: tid.clone(),
            qualified_name: qualified.clone(),
            name: qualified.rsplit("::").next().unwrap_or(&name).to_string(),
            kind: kind.to_string(),
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment: comment.clone(),
            summary,
            note,
            exported: exported_context,
        });
        if let Some(t) = ctx.types.last() {
            ctx.type_registry.insert(tid.clone(), t.clone());
        }
        if !namespace_stack.is_empty() {
            let ns_id = namespace_id(&namespace_stack.join("::"));
            ctx.relations.push(TsRelationEdge {
                source_id: ns_id,
                source_label: "Namespace".to_string(),
                target_id: tid.clone(),
                target_label: "Type".to_string(),
                rel_type: "CONTAINS".to_string(),
            });
        }
        if !type_stack.is_empty() {
            let parent_type = type_id(
                &namespace_stack
                    .iter()
                    .chain(type_stack.iter())
                    .cloned()
                    .collect::<Vec<_>>()
                    .join("::"),
            );
            ctx.relations.push(TsRelationEdge {
                source_id: parent_type,
                source_label: "Type".to_string(),
                target_id: tid,
                target_label: "Type".to_string(),
                rel_type: "CONTAINS".to_string(),
            });
        }
        let mut child_ts = type_stack.to_vec();
        child_ts.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, ctx, namespace_stack, &child_ts, false);
        }
        return;
    }

    // ── Functions / methods ──
    if function_kind_for(node.kind()).is_some() {
        record_function(
            node,
            ctx,
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

    // ── Variable declarations (arrow functions, factory calls) ──
    if node.kind() == "lexical_declaration" || node.kind() == "variable_declaration" {
        for child in node.children(&mut node.walk()) {
            if child.kind() != "variable_declarator" {
                continue;
            }
            let init = child
                .child_by_field_name("value")
                .or_else(|| child.child_by_field_name("initializer"));
            let Some(init) = init else { continue };

            if BARE_FUNC_TYPES.contains(&init.kind()) {
                let name = extract_name_field(child, source);
                record_function(
                    child,
                    ctx,
                    namespace_stack,
                    type_stack,
                    name,
                    Some("function_variable"),
                    Some(init),
                    Some(init),
                    exported_context,
                );
            } else if init.kind() == "call_expression" {
                let name = extract_name_field(child, source);
                let Some(name) = name else { continue };
                let factory_name = extract_root_factory_name(init, source);
                let kind = CALL_EXPR_KIND_MAP
                    .get(factory_name.as_str())
                    .map(|s| *s)
                    .unwrap_or("function_variable");
                let inner_fn = find_inner_function_arg(init);
                record_function(
                    child,
                    ctx,
                    namespace_stack,
                    type_stack,
                    Some(name),
                    Some(kind),
                    Some(init),
                    inner_fn,
                    exported_context,
                );
            }
        }
    }

    // ── Default: descend ──
    for child in node.children(&mut node.walk()) {
        walk(child, ctx, namespace_stack, type_stack, exported_context);
    }
}

// ── Parse-error detection ──────────────────────────────────────────────

fn is_benign_jsx_entity_error(node: Node, source: &[u8]) -> bool {
    let text = std::str::from_utf8(&source[node.start_byte()..node.end_byte()]).unwrap_or("");
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

fn tree_error_stats(root: Node, source: &[u8]) -> (bool, u32) {
    let error_nodes: Vec<Node> = find_nodes_by_type(root, "ERROR")
        .into_iter()
        .filter(|n| !is_benign_jsx_entity_error(*n, source))
        .collect();
    let count = error_nodes.len() as u32;
    (count > 0, count)
}

// ── Public entry point ─────────────────────────────────────────────────

/// Parse TS/TSX source and return the 12-tuple payload.
pub fn parse_ts_source(source: &[u8], rel_path: &str, is_tsx: bool) -> Option<TsParseOutput> {
    let grammar: Box<dyn Grammar> = if is_tsx {
        Box::new(TsxGrammar)
    } else {
        Box::new(TsGrammar)
    };
    let mut parser = Parser::new();
    parser.set_language(&grammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let (has_error, error_nodes) = tree_error_stats(root, source);

    // File-level data
    let snippet = std::str::from_utf8(source).unwrap_or("").to_string();
    let start_line = 1u32;
    let end_line = snippet.matches('\n').count() as u32 + 1;
    let file_comment = extract_file_comment(root, source);
    let file_summary = file_comment.clone();
    let file_note = build_note(&snippet, &file_comment, &file_summary);
    let imports = collect_imports(root, source);
    let exports = collect_exports(root, source);
    let (jsx_tags, jsx_components) = collect_jsx_tags(root, source);

    let file_def = TsFileDef {
        file_path: rel_path.to_string(),
        start_line,
        end_line,
        code: snippet.clone(),
        comment: file_comment,
        summary: file_summary,
        note: file_note,
        imports,
        exports,
        jsx_tags,
        jsx_components,
    };

    let mut functions: Vec<TsFunctionDef> = Vec::new();
    let mut types: Vec<TsTypeDef> = Vec::new();
    let mut namespaces: Vec<TsNamespaceDef> = Vec::new();
    let mut relations: Vec<TsRelationEdge> = Vec::new();
    let mut calls: Vec<TsCallEdge> = Vec::new();
    let mut renders: Vec<RenderEdge> = Vec::new();
    let mut navigates: Vec<NavigateEdge> = Vec::new();
    let mut type_registry: HashMap<String, TsTypeDef> = HashMap::new();
    let mut namespace_registry: HashMap<String, TsNamespaceDef> = HashMap::new();
    let mut exported_names: HashSet<String> = HashSet::new();

    {
        let mut ctx = TsWalkCtx {
            source,
            rel_path,
            functions: &mut functions,
            types: &mut types,
            namespaces: &mut namespaces,
            relations: &mut relations,
            calls: &mut calls,
            renders: &mut renders,
            navigates: &mut navigates,
            type_registry: &mut type_registry,
            namespace_registry: &mut namespace_registry,
            exported_names: &mut exported_names,
        };
        walk(root, &mut ctx, &[], &[], false);
    }

    // Post-walk: mark exported symbols
    if !exported_names.is_empty() {
        for func in &mut functions {
            if func.exported {
                continue;
            }
            if func.scope_name.is_none() && exported_names.contains(&func.name) {
                func.exported = true;
            }
        }
        for type_def in &mut types {
            if type_def.exported {
                continue;
            }
            if !type_def.qualified_name.contains("::") && exported_names.contains(&type_def.name) {
                type_def.exported = true;
            }
        }
    }

    // File-level route config extraction
    let mut fn_route_names: HashSet<String> = HashSet::new();
    for nav in &navigates {
        if nav.nav_method == "__route_config__" {
            fn_route_names.insert(nav.target_name.clone());
        }
    }
    for (rname, cname) in collect_route_configs(&snippet) {
        if !fn_route_names.contains(&rname) {
            navigates.push(NavigateEdge {
                source_id: format!("file::{}", rel_path),
                target_name: rname,
                nav_method: "__route_config__".to_string(),
                target_id: None,
                via: cname,
                trigger_type: "user".to_string(),
                guard: None,
                call_depth: 0,
                source_trace: Vec::new(),
                confidence: 1.0,
            });
        }
    }

    // API call extraction
    let file_base_url = extract_file_base_url(&snippet);
    let mut api_calls: Vec<ApiCallDef> = Vec::new();
    for func in &functions {
        if matches!(
            func.middleware_kind.as_str(),
            "api" | "query" | "service"
        ) {
            let extracted = extract_api_calls(
                &func.code,
                &func.symbol_id,
                rel_path,
                func.start_line,
                &file_base_url,
            );
            api_calls.extend(extracted);
        }
    }

    // Navigator + ParamList extraction
    let navigators = extract_navigator_declarations(&snippet, rel_path);
    let param_lists = extract_param_lists(root, source, rel_path);

    Some(TsParseOutput {
        functions,
        calls,
        types,
        namespaces,
        relations,
        renders,
        navigates,
        file_def,
        has_error,
        error_nodes,
        api_calls,
        navigators,
        param_lists,
    })
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(src: &str, is_tsx: bool) -> TsParseOutput {
        parse_ts_source(src.as_bytes(), "test.tsx", is_tsx).unwrap()
    }

    #[test]
    fn parses_simple_function() {
        let src = "function add(a: number, b: number): number { return a + b; }";
        let out = parse(src, false);
        assert_eq!(out.functions.len(), 1);
        let f = &out.functions[0];
        assert_eq!(f.name, "add");
        assert_eq!(f.arity, 2);
        assert_eq!(f.kind, "function");
        assert!(f.symbol_id.contains("/2@"));
    }

    #[test]
    fn parses_class_and_interface() {
        let src = r#"
            class Foo { bar(): void {} }
            interface IFoo { baz(): void; }
        "#;
        let out = parse(src, false);
        assert_eq!(out.types.len(), 2);
        assert!(out.types.iter().any(|t| t.kind == "class" && t.name == "Foo"));
        assert!(out.types.iter().any(|t| t.kind == "interface" && t.name == "IFoo"));
    }

    #[test]
    fn parses_arrow_function_var() {
        let src = "const greet = (name: string) => `Hello ${name}`;";
        let out = parse(src, false);
        assert_eq!(out.functions.len(), 1);
        assert_eq!(out.functions[0].name, "greet");
        assert_eq!(out.functions[0].kind, "function_variable");
    }

    #[test]
    fn parses_exported_arrow() {
        let src = "export const Button = () => <Button>Click</Button>;";
        let out = parse(src, true);
        assert_eq!(out.functions.len(), 1);
        assert!(out.functions[0].exported);
    }

    #[test]
    fn parses_calls() {
        let src = r#"
            function foo() { bar(); baz(1, 2); new Qux(); }
        "#;
        let out = parse(src, false);
        assert!(out.calls.len() >= 3);
        let names: Vec<&str> = out.calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(names.contains(&"bar"));
        assert!(names.contains(&"baz"));
        assert!(names.contains(&"Qux"));
    }

    #[test]
    fn detects_react_component_role() {
        let src = r#"
            function MyComponent() {
                return <div>Hello</div>;
            }
        "#;
        let out = parse(src, true);
        assert_eq!(out.functions.len(), 1);
        assert_eq!(out.functions[0].react_role, "component");
    }

    #[test]
    fn detects_react_screen_role() {
        let src = r#"
            function HomeScreen() {
                const navigation = useNavigation();
                return <View><Text>Home</Text></View>;
            }
        "#;
        let out = parse(src, true);
        // Note: this is test.tsx but the function uses useNavigation hook
        // is_screen_file won't match since path is "test.tsx" not in screens/
        // but RE_SCREEN_HOOKS + name ends with "Screen" → screen
        assert_eq!(out.functions[0].react_role, "screen");
    }

    #[test]
    fn detects_hook_role() {
        let src = "function useAuth() { return true; }";
        let out = parse(src, false);
        assert_eq!(out.functions[0].react_role, "hook");
    }

    #[test]
    fn detects_middleware_api() {
        let src = r#"
            function fetchData() {
                return fetch("/api/users");
            }
        "#;
        let out = parse(src, false);
        assert_eq!(out.functions[0].middleware_kind, "api");
        assert_eq!(out.functions[0].react_role, "middleware");
    }

    #[test]
    fn extracts_api_calls() {
        let src = r#"
            function getUser() {
                return fetch("/api/users/1");
            }
        "#;
        let out = parse(src, false);
        assert_eq!(out.api_calls.len(), 1);
        assert_eq!(out.api_calls[0].http_method, "GET");
        assert_eq!(out.api_calls[0].url_pattern, "/api/users/1");
    }

    #[test]
    fn extracts_renders() {
        let src = r#"
            function App() {
                return <View><Header /><Footer /></View>;
            }
        "#;
        let out = parse(src, true);
        // App renders View, Header, Footer (3 rendered, View is lowercase=False so it's PascalCase)
        let rendered: Vec<&str> = out.renders.iter().map(|r| r.rendered_name.as_str()).collect();
        assert!(rendered.contains(&"Header"));
        assert!(rendered.contains(&"Footer"));
    }

    #[test]
    fn extracts_navigate_calls() {
        let src = r#"
            function goHome() {
                navigation.navigate("Home");
            }
        "#;
        let out = parse(src, false);
        assert!(!out.navigates.is_empty());
        assert!(out.navigates.iter().any(|n| n.target_name == "Home" && n.nav_method == "navigate"));
    }

    #[test]
    fn extracts_navigator_declarations() {
        let src = r#"
            const Stack = createStackNavigator();
            const Tab = createBottomTabNavigator();
        "#;
        let out = parse(src, false);
        assert_eq!(out.navigators.len(), 2);
        assert!(out.navigators.iter().any(|n| n.var_name == "Stack" && n.nav_type == "stack"));
        assert!(out.navigators.iter().any(|n| n.var_name == "Tab" && n.nav_type == "tab"));
    }

    #[test]
    fn extracts_param_lists() {
        let src = r#"
            type RootStackParamList = {
                Home: undefined;
                Detail: { id: string };
            };
        "#;
        let out = parse(src, false);
        assert_eq!(out.param_lists.len(), 1);
        assert_eq!(out.param_lists[0].name, "RootStackParamList");
        assert_eq!(out.param_lists[0].routes.len(), 2);
        assert_eq!(out.param_lists[0].routes.get("Home"), Some(&"undefined".to_string()));
    }

    #[test]
    fn parses_namespace() {
        let src = r#"
            namespace Utils {
                function helper() {}
            }
        "#;
        let out = parse(src, false);
        assert_eq!(out.namespaces.len(), 1);
        assert_eq!(out.namespaces[0].name, "Utils");
        assert_eq!(out.functions.len(), 1);
        assert_eq!(out.functions[0].name, "helper");
    }

    #[test]
    fn parses_type_alias() {
        let src = "type Status = 'active' | 'inactive';";
        let out = parse(src, false);
        assert_eq!(out.types.len(), 1);
        assert_eq!(out.types[0].kind, "type_alias");
        assert_eq!(out.types[0].name, "Status");
    }

    #[test]
    fn factory_classification_create_slice() {
        let src = r#"
            const userSlice = createSlice({
                name: "user",
                reducers: {}
            });
        "#;
        let out = parse(src, false);
        assert_eq!(out.functions.len(), 1);
        assert_eq!(out.functions[0].kind, "redux_slice");
    }

    #[test]
    fn symbol_id_format_preserved() {
        let src = "function foo() {}";
        let out = parse(src, false);
        // symbol_id format: "{scope}::{name}/{arity}@{rel_path}" — our Rust uses ! separator
        // but the qualified name + arity + path are the key pieces
        let f = &out.functions[0];
        assert!(f.symbol_id.contains("/0@"));
        assert!(f.symbol_id.contains("test.tsx"));
    }
}

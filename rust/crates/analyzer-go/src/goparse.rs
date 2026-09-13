//! Port phần parse của `code-tiny/tools/go/go_analyzer.py`: tree-sitter walk
//! (`_walk_tree`), các dataclass payload (FunctionDef/TypeDef/NamespaceDef/
//! FieldDef/AliasDef/TemplateDef/RelationEdge/CallEdge/FileDef), helper `_`
//! từng chữ, `_collect_imports`, `_scan_go_files`, `_resolve_calls`.
//!
//! Quirk giữ nguyên:
//! * `_BRANCH_NODES` chứa `case_clause` — node kind KHÔNG tồn tại trong grammar
//!   Go (chỉ có expression_case/type_case/communication_case/default_case);
//!   `expression_case`/`type_case` không nằm trong map ⇒ dead entry giữ chết.
//! * Regex `[&*\\[\\](),{};]` của `_add_type_use` đóng class sớm (tại `]` đầu)
//!   nên hiệu ứng thực tế là class `[&*\[\\]` + literal `(),{};]` — gần như
//!   không bao giờ khớp; port đúng hiệu ứng bằng regex tương đương.

use std::collections::{BTreeSet, HashMap, HashSet};

use regex::Regex;
use serde_json::{Map, Value};
use tree_sitter::Node;

use cortex_analyzer_framework::scan::{
    COMMON_SCAN_EXCLUDE, matches_extra_ignore,
};
use cortex_analyzer_framework::ts::{
    decode_ignore, extract_leading_comment, find_nodes_by_type, node_snippet, node_text,
};

pub type Row = Map<String, Value>;

// ── Node-kind sets (port `_COMMENT_TYPES`/`_FUNCTION_NODES`/... từng chữ) ───

fn is_comment_kind(kind: &str) -> bool {
    kind == "comment"
}

fn function_node_kind(kind: &str) -> bool {
    matches!(kind, "function_declaration" | "method_declaration")
}

fn type_node_kind(kind: &str) -> bool {
    matches!(kind, "type_spec" | "type_alias")
}

fn is_call_node(kind: &str) -> bool {
    kind == "call_expression"
}

/// `_BRANCH_NODES` — `case_clause` là dead entry (không có trong grammar Go).
fn branch_kind(kind: &str) -> Option<&'static str> {
    match kind {
        "if_statement" => Some("if"),
        "switch_statement" => Some("switch"),
        "type_switch_statement" => Some("type_switch"),
        "select_statement" => Some("select"),
        "case_clause" => Some("case"),
        "communication_case" => Some("case"),
        "default_case" => Some("default"),
        _ => None,
    }
}

fn is_loop_node(kind: &str) -> bool {
    matches!(kind, "for_statement" | "range_clause")
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
    pub start_byte: usize,
    pub end_byte: usize,
    pub start_line: usize,
    pub end_line: usize,
    pub arity: usize,
    pub code: String,
    pub comment: String,
}

#[derive(Debug, Clone)]
pub struct TypeDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
    pub comment: String,
}

#[derive(Debug, Clone)]
pub struct NamespaceDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
    pub comment: String,
}

#[derive(Debug, Clone)]
pub struct FieldDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub scope_name: Option<String>,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
}

#[derive(Debug, Clone)]
pub struct AliasDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub target_name: Option<String>,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
}

#[derive(Debug, Clone)]
pub struct TemplateDef {
    pub symbol_id: String,
    pub name: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
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
pub struct CallEdge {
    pub caller_id: String,
    pub caller_file: String,
    pub caller_scope: Option<String>,
    pub call_line: usize,
    pub call_column: usize,
    pub call_start_byte: usize,
    pub call_branch_kind: String,
    pub call_loop_depth: usize,
    pub call_control_frames_json: String,
    pub call_type: String,
    pub call_arity: usize,
    pub callee_name: String,
    pub callee_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub code: String,
    pub comment: String,
    pub summary: String,
}

#[derive(Debug, Default)]
pub struct FilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<AliasDef>,
    pub templates: Vec<TemplateDef>,
    pub file_def: Option<FileDef>,
    // using_* chỉ dùng ở tests + payload fidelity (Python asdict), không ghi graph.
    #[allow(dead_code)]
    pub using_namespaces: Vec<String>,
    #[allow(dead_code)]
    pub using_imports: Vec<String>,
}

// ── Identity helpers (port `_scope_name`/`_qualified_name`/...) ─────────────

fn scope_name(stack: &[String]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(stack.join("."))
    }
}

fn qualified_name(stack: &[String], name: &str) -> String {
    if stack.is_empty() {
        name.to_string()
    } else {
        let mut parts = stack.to_vec();
        parts.push(name.to_string());
        parts.join(".")
    }
}

fn symbol_id(qualified_name: &str, arity: usize, rel_path: &str) -> String {
    format!("{qualified_name}/{arity}@{rel_path}")
}

fn type_id(qualified_name: &str) -> String {
    qualified_name.to_string()
}

fn namespace_id(qualified_name: &str) -> String {
    format!("namespace::{qualified_name}")
}

fn anonymous_name(prefix: &str, node: Node) -> String {
    format!(
        "Anonymous{prefix}@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

// ── Tree helpers (port `_first_named_child`/`_find_first_descendant`/...) ───

fn first_named_child<'tree>(node: Node<'tree>, allowed: &[&str]) -> Option<Node<'tree>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).find(|&child| {
        child.is_named() && (allowed.is_empty() || allowed.contains(&child.kind()))
    })
}

/// `_find_first_descendant` — DFS pre-order, trả node đầu tiên đúng kind.
fn find_first_descendant<'tree>(node: Node<'tree>, allowed: &[&str]) -> Option<Node<'tree>> {
    let mut children: Vec<Node<'tree>> = node.children(&mut node.walk()).collect();
    children.reverse();
    let mut stack: Vec<Node<'tree>> = children;
    while let Some(current) = stack.pop() {
        if allowed.contains(&current.kind()) {
            return Some(current);
        }
        let mut grandchildren: Vec<Node<'tree>> = current.children(&mut current.walk()).collect();
        grandchildren.reverse();
        stack.extend(grandchildren);
    }
    None
}

/// `_extract_name` — field `name` trước, fallback identifier family pre-order.
fn extract_name(node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source).trim().to_string());
    }
    for node_type in ["identifier", "field_identifier", "type_identifier", "package_identifier"] {
        if let Some(found) = find_first_descendant(node, &[node_type]) {
            return Some(node_text(found, source).trim().to_string());
        }
    }
    None
}

fn extract_file_comment(root: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "package_clause" {
            break;
        }
        if is_comment_kind(child.kind()) {
            let text = node_text(child, source);
            let text = text.trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
        } else if child.is_named() {
            break;
        }
    }
    parts.join("\n")
}

// ── Go-specific helpers ─────────────────────────────────────────────────────

/// `_count_parameters` — đếm parameter_declaration; mỗi decl mang identifier
/// thì cộng số identifier, không thì cộng 1.
fn count_parameters(node: Node, _source: &[u8]) -> usize {
    let params = match node.child_by_field_name("parameters") {
        Some(params) => params,
        None => match first_named_child(node, &["parameter_list"]) {
            Some(params) => params,
            None => return 0,
        },
    };
    let mut count = 0usize;
    let mut cursor = params.walk();
    for child in params.children(&mut cursor) {
        if child.kind() != "parameter_declaration" {
            continue;
        }
        let mut names = 0usize;
        let mut child_cursor = child.walk();
        for item in child.children(&mut child_cursor) {
            if matches!(item.kind(), "identifier" | "field_identifier") {
                names += 1;
            }
        }
        count += if names > 0 { names } else { 1 };
    }
    count
}

/// `_count_arguments` — named children của argument_list, trừ comment.
fn count_arguments(node: Node) -> usize {
    let args = match node.child_by_field_name("arguments") {
        Some(args) => args,
        None => match first_named_child(node, &["argument_list"]) {
            Some(args) => args,
            None => return 0,
        },
    };
    let mut cursor = args.walk();
    args.children(&mut cursor)
        .filter(|child| child.is_named() && !is_comment_kind(child.kind()))
        .count()
}

/// `_receiver_scope` — type_identifier đầu tiên trong receiver, fallback regex
/// `\*?\s*([A-Za-z_]\w*)\s*\)?\s*$` trên text.
fn receiver_scope(node: Node, source: &[u8]) -> Option<String> {
    let receiver = node.child_by_field_name("receiver")?;
    if let Some(type_node) = find_first_descendant(receiver, &["type_identifier"]) {
        return Some(node_text(type_node, source).trim().to_string());
    }
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"\*?\s*([A-Za-z_]\w*)\s*\)?\s*$").expect("receiver re"));
    let text = node_text(receiver, source);
    let text = text.trim();
    re.captures(text).map(|caps| caps[1].to_string())
}

/// `_type_kind` — struct_type ⇒ "struct", interface_type ⇒ "interface", còn
/// lại "type".
fn type_kind(type_spec: Node, _source: &[u8]) -> String {
    let type_node = match type_spec.child_by_field_name("type") {
        Some(type_node) => type_node,
        None => match first_named_child(type_spec, &["struct_type", "interface_type"]) {
            Some(type_node) => type_node,
            None => return "type".to_string(),
        },
    };
    match type_node.kind() {
        "struct_type" => "struct".to_string(),
        "interface_type" => "interface".to_string(),
        _ => "type".to_string(),
    }
}

/// `_is_type_alias` — node type_alias, hoặc regex text `Name[...] =`.
fn is_type_alias(type_spec: Node, source: &[u8]) -> bool {
    if type_spec.kind() == "type_alias" {
        return true;
    }
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(r"(?s)^\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*=").expect("alias re")
    });
    let text = node_text(type_spec, source);
    re.is_match(&text)
}

/// `_extract_alias_target` — phần sau `=`, whitespace gộp 1 space.
fn extract_alias_target(type_spec: Node, source: &[u8]) -> Option<String> {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| {
        Regex::new(r"(?s)^\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*=\s*(.*?)\s*$").expect("target re")
    });
    let text = node_text(type_spec, source);
    let caps = re.captures(&text)?;
    let target = caps.get(1)?.as_str();
    Some(cortex_analyzer_framework::ts::normalize_ws(target).trim().to_string())
}

/// `_collect_imports` — (namespaces, imports) sorted-unique; alias `_`/`.`
/// bị bỏ qua namespace, không alias thì lấy basename path.
fn collect_imports(root: Node, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut imports: Vec<String> = Vec::new();
    let mut namespaces: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "import_spec") {
        let path_node = match node.child_by_field_name("path") {
            Some(path_node) => path_node,
            None => match find_first_descendant(
                node,
                &["interpreted_string_literal", "raw_string_literal"],
            ) {
                Some(path_node) => path_node,
                None => continue,
            },
        };
        let path = node_text(path_node, source);
        let path = path.trim().trim_matches(|c| c == '`' || c == '"').to_string();
        if path.is_empty() {
            continue;
        }
        imports.push(path.clone());
        if let Some(alias_node) = node.child_by_field_name("name") {
            let alias = node_text(alias_node, source);
            let alias = alias.trim();
            if alias != "_" && alias != "." {
                namespaces.push(alias.to_string());
            }
        } else {
            namespaces.push(path.rsplit('/').next().unwrap_or(&path).to_string());
        }
    }
    let namespaces: Vec<String> = namespaces.into_iter().collect::<BTreeSet<_>>().into_iter().collect();
    let imports: Vec<String> = imports.into_iter().collect::<BTreeSet<_>>().into_iter().collect();
    (namespaces, imports)
}

/// `_extract_templates` — type_parameter_list descendants thành TemplateDef.
fn extract_templates(node: Node, rel_path: &str, source: &[u8]) -> Vec<TemplateDef> {
    let mut templates = Vec::new();
    for template_node in find_nodes_by_type(node, "type_parameter_list") {
        let text = node_text(template_node, source);
        let text = text.trim().to_string();
        templates.push(TemplateDef {
            symbol_id: format!(
                "template::{rel_path}:{}:{}",
                template_node.start_position().row + 1,
                template_node.end_position().row + 1
            ),
            name: text.clone(),
            file_path: rel_path.to_string(),
            start_line: template_node.start_position().row + 1,
            end_line: template_node.end_position().row + 1,
            code: text,
        });
    }
    templates
}

/// `_field_names` — field_identifier/identifier con, giữ text non-empty.
fn field_names(node: Node, source: &[u8]) -> Vec<String> {
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .filter(|child| matches!(child.kind(), "field_identifier" | "identifier"))
        .map(|child| node_text(child, source).trim().to_string())
        .filter(|name| !name.is_empty())
        .collect()
}

/// `_field_type_signature` — field `type` trước; fallback text sau prefix tên.
fn field_type_signature(node: Node, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    let text = node_text(node, source);
    let text = text.trim().trim_end_matches(',').to_string();
    let names = field_names(node, source);
    if !names.is_empty() {
        let prefix = names.join(", ");
        if let Some(position) = text.find(&prefix) {
            return text[position + prefix.len()..].trim().to_string();
        }
    }
    text
}

/// `_call_name` — selector_expression lấy field; text có "." thì lấy đuôi.
fn call_name(call_node: Node, source: &[u8]) -> String {
    let function_node = call_node.child_by_field_name("function");
    let Some(function_node) = function_node else {
        let text = node_text(call_node, source);
        return text.split('(').next().unwrap_or("").trim().to_string();
    };
    if function_node.kind() == "selector_expression"
        && let Some(field) = function_node.child_by_field_name("field")
    {
        return node_text(field, source).trim().to_string();
    }
    let text = node_text(function_node, source);
    let text = text.trim();
    if text.contains('.') {
        return text.rsplit('.').next().unwrap_or(text).to_string();
    }
    text.to_string()
}

/// `_control_context` — (branch_kind, loop_depth, frames JSON).
/// JSON khớp `json.dumps(frames, ensure_ascii=False)`: `{"kind": "if", "line": 5}`.
fn control_context(node: Node) -> (String, usize, String) {
    let mut frames: Vec<(&str, usize)> = Vec::new();
    let mut current_branch = "none";
    let mut loop_depth = 0usize;
    let mut parent = node.parent();
    while let Some(current) = parent {
        let kind = current.kind();
        if let Some(bk) = branch_kind(kind) {
            if current_branch == "none" {
                current_branch = bk;
            }
            frames.push((bk, current.start_position().row + 1));
        } else if is_loop_node(kind) {
            loop_depth += 1;
            frames.push(("loop", current.start_position().row + 1));
        }
        parent = current.parent();
    }
    frames.reverse();
    let frames_json = format!(
        "[{}]",
        frames
            .iter()
            .map(|(kind, line)| format!("{{\"kind\": \"{kind}\", \"line\": {line}}}"))
            .collect::<Vec<_>>()
            .join(", ")
    );
    (current_branch.to_string(), loop_depth, frames_json)
}

fn record_relation(
    relations: &mut Vec<RelationEdge>,
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
    properties: Row,
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

/// Regex hiệu ứng của `_add_type_use` — xem doc đầu module: class đóng sớm.
fn type_use_normalize(text: &str) -> String {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"[&*\[\\]\(\),\{\};\]").expect("type use re"));
    re.replace_all(text, " ").to_string()
}

/// `_add_type_use` — candidate = token bắt đầu uppercase; external type mới
/// được append; relation POINTER_TO/USES_TYPE.
fn add_type_use(
    state: &mut WalkState,
    owner_id: &str,
    owner_label: &str,
    type_text: &str,
    rel_path: &str,
) {
    static SPLIT_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let split_re = SPLIT_RE.get_or_init(|| Regex::new(r"\s+|\.").expect("split re"));
    let normalized = type_use_normalize(type_text);
    let candidates: Vec<&str> = split_re
        .split(&normalized)
        .filter(|part| {
            !part.is_empty()
                && part
                    .chars()
                    .next()
                    .is_some_and(|c| c.is_uppercase())
        })
        .collect();
    for candidate in candidates {
        let target_id = type_id(candidate);
        if !state.external_types.contains(&target_id) {
            state.external_types.insert(target_id.clone());
            state.types.push(TypeDef {
                symbol_id: target_id.clone(),
                qualified_name: candidate.to_string(),
                name: candidate.to_string(),
                kind: "external".to_string(),
                file_path: rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: candidate.to_string(),
                comment: String::new(),
            });
        }
        let rel_type = if type_text.contains('*') {
            "POINTER_TO"
        } else {
            "USES_TYPE"
        };
        record_relation(
            &mut state.relations,
            owner_id,
            owner_label,
            &target_id,
            "Type",
            rel_type,
            Map::new(),
        );
    }
}

// ── Tree walk ───────────────────────────────────────────────────────────────

struct WalkState<'a> {
    source: &'a [u8],
    rel_path: String,
    namespaces: Vec<NamespaceDef>,
    types: Vec<TypeDef>,
    functions: Vec<FunctionDef>,
    fields: Vec<FieldDef>,
    aliases: Vec<AliasDef>,
    templates: Vec<TemplateDef>,
    relations: Vec<RelationEdge>,
    calls: Vec<CallEdge>,
    type_registry: HashSet<String>,
    external_types: HashSet<String>,
}

/// `_walk_tree` — `active` là (symbol_id, scope_name) của function đang đi.
fn walk_tree(
    state: &mut WalkState,
    node: Node,
    package_scope: &str,
    scope_stack: &[String],
    active: Option<(&str, Option<&str>)>,
) {
    let source = state.source;
    let kind = node.kind();

    if type_node_kind(kind) {
        let name = match extract_name(node, source) {
            Some(name) if !name.is_empty() => name,
            _ => anonymous_name("Type", node),
        };
        let mut full_scope: Vec<String> = vec![package_scope.to_string()];
        full_scope.extend_from_slice(scope_stack);
        let qualified = qualified_name(&full_scope, &name);
        let (snippet, start_line, end_line) = node_snippet(node, source);
        if is_type_alias(node, source) {
            let target_name = extract_alias_target(node, source);
            let alias = AliasDef {
                symbol_id: format!("alias::{qualified}@{}", state.rel_path),
                qualified_name: qualified,
                name,
                kind: "type".to_string(),
                target_name: target_name.clone(),
                file_path: state.rel_path.clone(),
                start_line,
                end_line,
                code: snippet,
            };
            if let Some(target) = &alias.target_name {
                record_relation(
                    &mut state.relations,
                    &alias.symbol_id,
                    "Alias",
                    &type_id(target),
                    "Type",
                    "ALIASES",
                    Map::new(),
                );
            }
            state.aliases.push(alias);
            // Python NHÁNH ALIAS KHÔNG return — rơi xuống walk children với
            // scope_stack gốc.
        } else {
            let type_id = type_id(&qualified);
            let type_name = name.clone();
            let type_def = TypeDef {
                symbol_id: type_id.clone(),
                qualified_name: qualified,
                name,
                kind: type_kind(node, source),
                file_path: state.rel_path.clone(),
                start_line,
                end_line,
                code: snippet,
                comment: extract_leading_comment(node, source),
            };
            if !state.type_registry.contains(&type_id) {
                state.type_registry.insert(type_id.clone());
                state.types.push(type_def);
            }
            record_relation(
                &mut state.relations,
                &namespace_id(package_scope),
                "Namespace",
                &type_id,
                "Type",
                "DECLARES",
                Map::new(),
            );
            for template in extract_templates(node, &state.rel_path, source) {
                record_relation(
                    &mut state.relations,
                    &template.symbol_id,
                    "Template",
                    &type_id,
                    "Type",
                    "TEMPLATES",
                    Map::new(),
                );
                state.templates.push(template);
            }
            let child_scope: Vec<String> = scope_stack
                .iter()
                .cloned()
                .chain(std::iter::once(type_name))
                .collect();
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                walk_tree(
                    state,
                    child,
                    package_scope,
                    &child_scope,
                    active,
                );
            }
            return;
        }
    }

    if function_node_kind(kind) {
        let name = match extract_name(node, source) {
            Some(name) if !name.is_empty() => name,
            _ => anonymous_name("Function", node),
        };
        let receiver_scope = if kind == "method_declaration" {
            receiver_scope(node, source)
        } else {
            None
        };
        let mut function_scope: Vec<String> = vec![package_scope.to_string()];
        if let Some(receiver) = &receiver_scope {
            function_scope.push(receiver.clone());
        }
        let qualified = qualified_name(&function_scope, &name);
        let arity = count_parameters(node, source);
        let func = FunctionDef {
            symbol_id: symbol_id(&qualified, arity, &state.rel_path),
            qualified_name: qualified,
            name,
            kind: if receiver_scope.is_some() {
                "method"
            } else {
                "function"
            }
            .to_string(),
            scope_name: if receiver_scope.is_some() {
                scope_name(&function_scope)
            } else {
                Some(package_scope.to_string())
            },
            file_path: state.rel_path.clone(),
            start_byte: node.start_byte(),
            end_byte: node.end_byte(),
            start_line: node.start_position().row + 1,
            end_line: node.end_position().row + 1,
            arity,
            code: node_text(node, source),
            comment: extract_leading_comment(node, source),
        };
        let func_id = func.symbol_id.clone();
        let func_scope = func.scope_name.clone();
        if let Some(receiver) = &receiver_scope {
            let owner_id = type_id(&qualified_name(&[package_scope.to_string()], receiver));
            record_relation(
                &mut state.relations,
                &owner_id,
                "Type",
                &func_id,
                "Function",
                "DECLARES",
                Map::new(),
            );
        } else {
            record_relation(
                &mut state.relations,
                &namespace_id(package_scope),
                "Namespace",
                &func_id,
                "Function",
                "DECLARES",
                Map::new(),
            );
        }
        for template in extract_templates(node, &state.rel_path, source) {
            record_relation(
                &mut state.relations,
                &template.symbol_id,
                "Template",
                &func_id,
                "Function",
                "TEMPLATES",
                Map::new(),
            );
            state.templates.push(template);
        }
        state.functions.push(func);
        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            walk_tree(
                state,
                child,
                package_scope,
                scope_stack,
                Some((func_id.as_str(), func_scope.as_deref())),
            );
        }
        return;
    }

    if !scope_stack.is_empty() && kind == "field_declaration" {
        let owner = qualified_name(&[package_scope.to_string()], &scope_stack[scope_stack.len() - 1]);
        let type_signature = field_type_signature(node, source);
        for name in field_names(node, source) {
            let qualified = format!("{owner}.{name}");
            let field_id = format!("field::{qualified}@{}", state.rel_path);
            let field = FieldDef {
                symbol_id: field_id.clone(),
                qualified_name: qualified,
                name,
                scope_name: Some(owner.clone()),
                type_signature: type_signature.clone(),
                file_path: state.rel_path.clone(),
                start_line: node.start_position().row + 1,
                end_line: node.end_position().row + 1,
                code: node_text(node, source),
            };
            state.fields.push(field);
            record_relation(
                &mut state.relations,
                &type_id(&owner),
                "Type",
                &field_id,
                "Field",
                "DECLARES",
                Map::new(),
            );
            if !type_signature.is_empty() {
                let field_rel_path = state.rel_path.clone();
                add_type_use(state, &field_id, "Field", &type_signature, &field_rel_path);
            }
        }
    }

    if is_call_node(kind)
        && let Some((caller_id, caller_scope)) = active
    {
        let callee_name = call_name(node, source);
        let (branch, loop_depth, control_frames) = control_context(node);
        let function_node = node.child_by_field_name("function");
        let function_text = function_node
            .map(|function| node_text(function, source))
            .unwrap_or_default();
        state.calls.push(CallEdge {
            caller_id: caller_id.to_string(),
            caller_file: state.rel_path.clone(),
            caller_scope: caller_scope.map(str::to_string),
            call_line: node.start_position().row + 1,
            call_column: node.start_position().column + 1,
            call_start_byte: node.start_byte(),
            call_branch_kind: branch,
            call_loop_depth: loop_depth,
            call_control_frames_json: control_frames,
            call_type: if function_text.contains('.') {
                "method"
            } else {
                "function"
            }
            .to_string(),
            call_arity: count_arguments(node),
            callee_name,
            callee_id: None,
        });
    }

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_tree(state, child, package_scope, scope_stack, active);
    }
}

// ── Call resolution (`_resolve_calls`, PER-FILE như Python) ─────────────────

pub fn resolve_calls(
    functions: &[FunctionDef],
    mut calls: Vec<CallEdge>,
    relations: &mut Vec<RelationEdge>,
) -> Vec<CallEdge> {
    let mut by_name: HashMap<&str, Vec<&FunctionDef>> = HashMap::new();
    let mut by_name_arity: HashMap<(&str, usize), Vec<&FunctionDef>> = HashMap::new();
    for func in functions {
        by_name.entry(func.name.as_str()).or_default().push(func);
        by_name_arity
            .entry((func.name.as_str(), func.arity))
            .or_default()
            .push(func);
    }
    for call in calls.iter_mut() {
        let by_arity = by_name_arity
            .get(&(call.callee_name.as_str(), call.call_arity))
            .filter(|candidates| !candidates.is_empty());
        let initial: Vec<&FunctionDef> = match by_arity {
            Some(candidates) => candidates.to_vec(),
            None => match by_name.get(call.callee_name.as_str()) {
                Some(candidates) => candidates.to_vec(),
                None => continue,
            },
        };
        let mut selected = initial;
        if selected.len() > 1
            && let Some(caller_scope) = &call.caller_scope
        {
            let scoped: Vec<&FunctionDef> = selected
                .iter()
                .copied()
                .filter(|item| item.scope_name.as_deref() == Some(caller_scope.as_str()))
                .collect();
            if !scoped.is_empty() {
                selected = scoped;
            }
        }
        if selected.len() == 1 {
            let winner = selected[0];
            call.callee_id = Some(winner.symbol_id.clone());
            let mut properties = Map::new();
            properties.insert("line".into(), Value::from(call.call_line as u64));
            properties.insert("column".into(), Value::from(call.call_column as u64));
            properties.insert("call_type".into(), Value::from(call.call_type.clone()));
            properties.insert("arity".into(), Value::from(call.call_arity as u64));
            relations.push(RelationEdge {
                source_id: call.caller_id.clone(),
                source_label: "Function".to_string(),
                target_id: winner.symbol_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "POSSIBLE_CALLS".to_string(),
                properties,
            });
        }
    }
    calls
}

// ── File entry (`parse_go_file`) ────────────────────────────────────────────

/// `parse_go_file` — walk 1 file + INCLUDES relations + per-file call resolve.
pub fn parse_go_file(path: &std::path::Path, root: &std::path::Path) -> Result<FilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let source_bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_go::LANGUAGE.into())
        .map_err(|e| e.to_string())?;
    let tree = parser
        .parse(&source_bytes, None)
        .ok_or_else(|| format!("parse failed: {rel_path}"))?;

    let code = decode_ignore(&source_bytes);
    let package_scope = package_name(tree.root_node(), &source_bytes);
    let (using_namespaces, using_imports) = collect_imports(tree.root_node(), &source_bytes);
    let file_comment = extract_file_comment(tree.root_node(), &source_bytes);

    let mut state = WalkState {
        source: &source_bytes,
        rel_path: rel_path.clone(),
        namespaces: Vec::new(),
        types: Vec::new(),
        functions: Vec::new(),
        fields: Vec::new(),
        aliases: Vec::new(),
        templates: Vec::new(),
        relations: Vec::new(),
        calls: Vec::new(),
        type_registry: HashSet::new(),
        external_types: HashSet::new(),
    };
    state.namespaces.push(NamespaceDef {
        symbol_id: namespace_id(&package_scope),
        qualified_name: package_scope.clone(),
        name: package_scope.clone(),
        file_path: rel_path.clone(),
        start_line: 1,
        end_line: 1,
        code: format!("package {package_scope}"),
        comment: String::new(),
    });
    walk_tree(&mut state, tree.root_node(), &package_scope, &[], None);

    for include in &using_imports {
        record_relation(
            &mut state.relations,
            &rel_path,
            "File",
            include,
            "ExternalModule",
            "INCLUDES",
            Map::new(),
        );
    }
    let calls = resolve_calls(&state.functions, state.calls, &mut state.relations);

    let end_line = code.matches('\n').count() + 1;
    Ok(FilePayload {
        functions: state.functions,
        calls,
        types: state.types,
        namespaces: state.namespaces,
        relations: state.relations,
        fields: state.fields,
        aliases: state.aliases,
        templates: state.templates,
        file_def: Some(FileDef {
            file_path: rel_path,
            start_line: 1,
            end_line,
            code,
            comment: file_comment.clone(),
            summary: file_comment,
        }),
        using_namespaces,
        using_imports,
    })
}

/// `_package_name` — package_clause đầu tiên, fallback "main".
fn package_name(root: Node, source: &[u8]) -> String {
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "package_clause" {
            return extract_name(child, source).unwrap_or_else(|| "main".to_string());
        }
    }
    "main".to_string()
}

// ── Scan (`_scan_go_files`) ─────────────────────────────────────────────────

/// `_SCAN_SKIP_DIRS` | COMMON_SCAN_EXCLUDE — set từng chữ của go_analyzer.py.
const SCAN_SKIP_DIRS: [&str; 26] = [
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vs",
    ".vscode",
    ".eclipse",
    ".settings",
    "bin",
    "build",
    "dist",
    "out",
    "target",
    "vendor",
    "node_modules",
    ".cache",
    ".parcel-cache",
    "__pycache__",
    "coverage",
    ".test-results",
    "test-results",
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    // ".DS_Store"/"Thumbs.db" là 2 entry cuối của set gốc (tên file, chết khi
    // so với dirname nhưng giữ nguyên).
    ".DS_Store",
];

/// `_scan_go_files` — walk root, prune skip-dirs + extra-ignore, thu `.go`
/// (trừ `_test.go`), sort theo path string.
pub fn scan_go_files(root: &std::path::Path) -> Vec<std::path::PathBuf> {
    let mut files = Vec::new();
    walk_go(root, &mut files);
    files.sort_by(|a, b| a.to_string_lossy().cmp(&b.to_string_lossy()));
    files
}

fn walk_go(dir: &std::path::Path, files: &mut Vec<std::path::PathBuf>) {
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
            if !SCAN_SKIP_DIRS.contains(&name.as_str())
                && !COMMON_SCAN_EXCLUDE.contains(&name.as_str())
                && !matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        if name.ends_with(".go") && !name.ends_with("_test.go") {
            files.push(path);
        }
    }
    for sub in subdirs {
        walk_go(&sub, files);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"// Package math provides parity fixtures.
package math

import (
	"fmt"
	al "strings"
)

// Adder sums things.
type Adder interface {
	Add(a, b int) int
}

// Point groups coords.
type Point struct {
	X, Y int
	Name string
}

type MyInt = int

func (p *Point) Label() string {
	defer fmt.Println("done")
	return al.ToUpper(p.Name)
}

func (p Point) Sum() int {
	total := 0
	for i := 0; i < 3; i++ {
		total += helper(i)
	}
	if total > 0 {
		total = helper(total)
	}
	return total
}

func helper(n int) int { return n }
"#;

    fn parse_sample() -> FilePayload {
        let dir = std::env::temp_dir().join(format!("analyzer_go_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("sample.go");
        std::fs::write(&path, SAMPLE).unwrap();
        let payload = parse_go_file(&path, &dir).unwrap();
        std::fs::remove_dir_all(&dir).ok();
        payload
    }

    #[test]
    fn parses_package_types_and_methods() {
        let payload = parse_sample();
        assert_eq!(payload.namespaces.len(), 1);
        assert_eq!(payload.namespaces[0].symbol_id, "namespace::math");
        assert_eq!(payload.namespaces[0].qualified_name, "math");

        let types: Vec<(&str, &str)> = payload
            .types
            .iter()
            .map(|t| (t.name.as_str(), t.kind.as_str()))
            .collect();
        // Point + Adder + external "Int"? — "int" lowercase nên không external.
        assert_eq!(
            types,
            vec![("Adder", "interface"), ("Point", "struct")]
        );
        assert_eq!(payload.types[1].symbol_id, "math.Point");

        let aliases: Vec<&str> = payload.aliases.iter().map(|a| a.name.as_str()).collect();
        assert_eq!(aliases, vec!["MyInt"]);
        assert_eq!(payload.aliases[0].target_name.as_deref(), Some("int"));

        let methods: Vec<(&str, &str)> = payload
            .functions
            .iter()
            .map(|f| (f.name.as_str(), f.kind.as_str()))
            .collect();
        assert!(methods.contains(&("Label", "method")));
        assert!(methods.contains(&("Sum", "method")));
        assert!(methods.contains(&("helper", "function")));

        let label = payload
            .functions
            .iter()
            .find(|f| f.name == "Label")
            .unwrap();
        assert_eq!(label.qualified_name, "math.Point.Label");
        assert_eq!(label.scope_name.as_deref(), Some("math.Point"));
        assert_eq!(label.arity, 0);
        assert_eq!(label.symbol_id, "math.Point.Label/0@sample.go");

        let sum = payload.functions.iter().find(|f| f.name == "Sum").unwrap();
        assert_eq!(sum.arity, 0);
        let helper_fn = payload
            .functions
            .iter()
            .find(|f| f.name == "helper")
            .unwrap();
        assert_eq!(helper_fn.scope_name.as_deref(), Some("math"));
        assert_eq!(helper_fn.arity, 1);

        let fields: Vec<&str> = payload.fields.iter().map(|f| f.name.as_str()).collect();
        assert_eq!(fields, vec!["X", "Y", "Name"]);
        assert_eq!(payload.fields[0].qualified_name, "math.Point.X");
        assert_eq!(payload.fields[0].type_signature, "int");
        assert_eq!(payload.fields[2].type_signature, "string");
    }

    #[test]
    fn resolves_calls_per_file_and_records_relations() {
        let payload = parse_sample();
        let rel_types: Vec<&str> = payload
            .relations
            .iter()
            .map(|r| r.rel_type.as_str())
            .collect();
        assert!(rel_types.contains(&"DECLARES"));
        assert!(rel_types.contains(&"INCLUDES"));

        let resolved: Vec<(&str, &str)> = payload
            .calls
            .iter()
            .filter_map(|c| {
                c.callee_id
                    .as_deref()
                    .map(|id| (c.callee_name.as_str(), id))
            })
            .collect();
        // helper(arity 1) resolve đúng 2 lần (trong Sum).
        assert_eq!(
            resolved,
            vec![
                ("helper", "math.helper/1@sample.go"),
                ("helper", "math.helper/1@sample.go"),
            ]
        );
        // Println/ToUpper là method call không trúng function local → callee
        // None nhưng call vẫn giữ lại trong payload như Python (lọc xảy ra ở
        // _prepare_write_rows).
        assert!(payload.calls.iter().any(|c| c.callee_name == "Println"));
        let possible: Vec<&str> = payload
            .relations
            .iter()
            .filter(|r| r.rel_type == "POSSIBLE_CALLS")
            .map(|r| r.rel_type.as_str())
            .collect();
        assert_eq!(possible.len(), 2);
    }

    #[test]
    fn control_context_and_call_metadata() {
        let payload = parse_sample();
        let helper_call = payload
            .calls
            .iter()
            .find(|c| c.callee_name == "helper")
            .unwrap();
        assert_eq!(helper_call.call_type, "function");
        assert_eq!(helper_call.call_arity, 1);
        // helper chỉ được gọi trong method Sum ⇒ caller_scope = math.Point.
        assert_eq!(helper_call.caller_scope.as_deref(), Some("math.Point"));
        // Python json.dumps separators: `{"kind": "if", "line": N}`.
        let frames = &helper_call.call_control_frames_json;
        assert!(
            frames == "[]" || frames.starts_with("[{\"kind\": \""),
            "frames format mismatch: {frames}"
        );
        let loop_call = payload
            .calls
            .iter()
            .find(|c| c.call_loop_depth == 1)
            .expect("loop call");
        assert!(loop_call.call_control_frames_json.contains("\"kind\": \"loop\""));
    }

    #[test]
    fn imports_collect_aliases_and_paths() {
        let payload = parse_sample();
        assert_eq!(payload.using_imports, vec!["fmt", "strings"]);
        assert_eq!(payload.using_namespaces, vec!["al", "fmt"]);
        // INCLUDES relations cho TỪNG import path.
        let includes: Vec<&str> = payload
            .relations
            .iter()
            .filter(|r| r.rel_type == "INCLUDES")
            .map(|r| r.target_id.as_str())
            .collect();
        assert_eq!(includes, vec!["fmt", "strings"]);
    }

    #[test]
    fn generics_emit_templates() {
        let dir = std::env::temp_dir().join(format!("analyzer_go_generic_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("generic.go");
        std::fs::write(
            &path,
            "package g\n\ntype Stack[T any] struct {\n\titems []T\n}\n",
        )
        .unwrap();
        let payload = parse_go_file(&path, &dir).unwrap();
        std::fs::remove_dir_all(&dir).ok();
        assert_eq!(payload.templates.len(), 1);
        assert_eq!(payload.templates[0].name, "[T any]");
        assert_eq!(payload.templates[0].code, "[T any]");
        assert!(payload
            .relations
            .iter()
            .any(|r| r.rel_type == "TEMPLATES"));
    }

    #[test]
    fn scan_skips_tests_and_dirs() {
        let dir = std::env::temp_dir().join(format!("analyzer_go_scan_{}", std::process::id()));
        let sub = dir.join("vendor").join("pkg");
        std::fs::create_dir_all(&sub).unwrap();
        let keep = dir.join("internal");
        std::fs::create_dir_all(&keep).unwrap();
        std::fs::write(sub.join("x.go"), "package v\n").unwrap();
        std::fs::write(keep.join("a.go"), "package i\n").unwrap();
        std::fs::write(keep.join("a_test.go"), "package i\n").unwrap();
        std::fs::write(keep.join("readme.txt"), "nope").unwrap();
        let found = scan_go_files(&dir);
        let rels: Vec<String> = found
            .iter()
            .map(|p| cortex_analyzer_framework::scan::rel_posix(&dir, p))
            .collect();
        assert_eq!(rels, vec!["internal/a.go"]);
        std::fs::remove_dir_all(&dir).ok();
    }
}

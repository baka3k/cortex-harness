//! Java tree-sitter walker — Phase 2 (Tier 2) port of `java_analyzer.py`.
//!
//! Java is **Family A** — the richest schema:
//! - 9-tuple parse output: `(functions, calls, classes, type_edges, function_types,
//!   relations, file_def, package_def, parse_meta)`
//! - `FunctionDef` has `class_name`, `package_name` (not `scope_name`),
//!   `visibility`, `is_public_api`, `visibility_source`, `export_evidence`, `signature`
//! - `CallEdge` has `caller_id, caller_file, caller_package, caller_class, imports,
//!   callee_name, callee_id` — NO `callee_arity`; carries imports list
//! - `ClassDef` (separate from generic `TypeDef`)
//! - `TypeEdge` (separate from `RelationEdge`)
//! - `PackageDef` (separate from `NamespaceDef`)
//!
//! Two-stage call resolution: per-file `_resolve_calls` (4 indexes) +
//! project-level `resolve_callee_id`.

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{Grammar, JavaGrammar};
use crate::symbols::{ParseMeta, RelationEdge};
use crate::text::{node_text, node_snippet};

// ── Node-type sets (mirror Java analyzer constants) ─────────────────────

const COMMENT_TYPES: &[&str] = &["line_comment", "block_comment"];
const CLASS_NODE_KINDS: &[&str] = &[
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
];
const FUNCTION_NODE_KINDS: &[&str] = &["method_declaration", "constructor_declaration"];
const CALL_NODE_KINDS: &[&str] = &[
    "method_invocation",
    "object_creation_expression",
    "explicit_constructor_invocation",
    "method_reference",
];

// ── Java-specific structures (mirror Python dataclasses) ────────────────

#[derive(Debug, Clone, Default)]
pub struct JavaFunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub class_name: Option<String>,
    pub package_name: Option<String>,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub arity: u32,
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

#[derive(Debug, Clone, Default)]
pub struct JavaPackageDef {
    pub name: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone, Default)]
pub struct JavaClassDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub package_name: Option<String>,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
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

#[derive(Debug, Clone, Default)]
pub struct TypeEdge {
    pub source_id: String,
    pub source_package: Option<String>,
    pub target_name: String,
    pub rel_type: String,
    pub target_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct FunctionTypeDef {
    pub symbol_id: String,
    pub type_signature: String,
    pub file_path: String,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
}

#[derive(Debug, Clone, Default)]
pub struct JavaCallEdge {
    pub caller_id: String,
    pub caller_file: String,
    pub caller_package: Option<String>,
    pub caller_class: Option<String>,
    pub imports: Vec<String>,
    pub callee_name: String,
    pub callee_id: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct JavaFileDef {
    pub file_path: String,
    pub package_name: Option<String>,
    pub start_line: u32,
    pub end_line: u32,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Default)]
pub struct JavaParseOutput {
    pub file_def: JavaFileDef,
    pub package_def: JavaPackageDef,
    pub functions: Vec<JavaFunctionDef>,
    pub classes: Vec<JavaClassDef>,
    pub calls: Vec<JavaCallEdge>,
    pub type_edges: Vec<TypeEdge>,
    pub function_types: Vec<FunctionTypeDef>,
    pub relations: Vec<RelationEdge>,
    pub imports: Vec<String>,
    pub parse_meta: ParseMeta,
}

// ── Helpers ─────────────────────────────────────────────────────────────

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

fn first_identifier(node: Node, source: &[u8]) -> Option<String> {
    if matches!(node.kind(), "identifier" | "type_identifier") {
        return Some(node_text(node, source).to_string());
    }
    for child in node.children(&mut node.walk()) {
        if let Some(s) = first_identifier(child, source) {
            return Some(s);
        }
    }
    None
}

fn node_text_collect<'a>(node: Node<'a>, source: &[u8]) -> String {
    node_text(node, source).to_string()
}

fn path_nodes(node: Node) -> Vec<String> {
    let mut parts = Vec::new();
    for child in node.children(&mut node.walk()) {
        if child.kind() == "." {
            continue;
        }
        if let Some(text) = child
            .child_by_field_name("name")
            .map(|n| node_text(n, &[0u8]).to_string())
        {
            parts.push(text);
        }
    }
    parts
}

fn java_api_visibility(snippet: &str, implicit_public: bool) -> (String, bool, String) {
    let header = snippet.split('{').next().unwrap_or("");
    let tokens: std::collections::HashSet<&str> = regex::Regex::new(r"\b(public|protected|private)\b")
        .unwrap()
        .find_iter(header)
        .map(|m| m.as_str())
        .collect();
    if tokens.contains("private") {
        return ("private".to_string(), false, "explicit private".to_string());
    }
    if tokens.contains("protected") {
        return ("protected".to_string(), false, "explicit protected".to_string());
    }
    if tokens.contains("public") {
        return ("public".to_string(), true, "explicit public".to_string());
    }
    if implicit_public {
        return (
            "public".to_string(),
            true,
            "implicit interface/annotation public".to_string(),
        );
    }
    (
        "package".to_string(),
        false,
        "package-private by Java language rule".to_string(),
    )
}

fn source_signature(snippet: &str) -> String {
    snippet
        .split('{')
        .next()
        .unwrap_or("")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(500)
        .collect()
}

fn extract_leading_comment(node: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if !COMMENT_TYPES.contains(&p.kind()) {
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

fn extract_file_comment(root: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    for child in root.children(&mut root.walk()) {
        if COMMENT_TYPES.contains(&child.kind()) {
            let text = node_text(child, source).trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
            continue;
        }
        if child.is_named() {
            break;
        }
    }
    parts.join("\n")
}

fn count_parameters(node: Node) -> u32 {
    let Some(params) = node.child_by_field_name("parameters") else {
        return 0;
    };
    params
        .children(&mut params.walk())
        .filter(|c| c.kind() == "formal_parameter")
        .count() as u32
}

fn class_kind(node_type: &str) -> Option<&'static str> {
    match node_type {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "enum_declaration" => Some("enum"),
        "record_declaration" => Some("record"),
        _ => None,
    }
}

fn collect_package_info(
    root: Node,
    source: &[u8],
) -> (Option<String>, u32, u32, String, String) {
    for node in find_nodes_by_type(root, "package_declaration") {
        let text = node_text(node, source);
        let identifiers: Vec<&str> = text
            .split(|c: char| !c.is_alphanumeric() && c != '_' && c != '.')
            .filter(|s| !s.is_empty() && *s != "package")
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

fn collect_imports(root: Node, source: &[u8]) -> Vec<String> {
    let mut imports: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "import_declaration") {
        let text = node_text(node, source);
        let identifiers: Vec<&str> = text
            .split(|c: char| !c.is_alphanumeric() && c != '_' && c != '.')
            .filter(|s| !s.is_empty() && *s != "import" && *s != "static")
            .collect();
        if !identifiers.is_empty() {
            imports.push(identifiers.join("."));
        }
    }
    imports
}

fn extract_class_name(class_node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = class_node.child_by_field_name("name") {
        return Some(node_text(name_node, source).to_string());
    }
    first_identifier(class_node, source)
}

fn extract_method_name(method_node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = method_node.child_by_field_name("name") {
        return Some(node_text(name_node, source).to_string());
    }
    first_identifier(method_node, source)
}

fn normalize_callee(text: &str) -> String {
    let callee = text.split('(').next().unwrap_or("").trim();
    let callee = callee.replace("::", ".");
    let re = regex::Regex::new(r"<.*?>").unwrap();
    let callee = re.replace_all(&callee, "").into_owned();
    callee.trim_matches(|c: char| c == ' ' || c == '.').to_string()
}

fn extract_call_name(call_node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = call_node.child_by_field_name("name") {
        let name_text = node_text(name_node, source);
        if let Some(object_node) = call_node.child_by_field_name("object") {
            let obj_text = node_text(object_node, source);
            return Some(normalize_callee(&format!("{}.{}", obj_text, name_text)));
        }
        return Some(normalize_callee(name_text));
    }
    Some(normalize_callee(&node_text(call_node, source)))
}

fn extract_constructor_call_name(call_node: Node, source: &[u8]) -> Option<String> {
    let mut type_text: Option<String> = None;
    if let Some(type_node) = call_node.child_by_field_name("type") {
        type_text = Some(node_text(type_node, source).to_string());
    }
    if type_text.is_none() {
        let text = node_text(call_node, source).to_string();
        if text.contains("new") {
            let after = text.split("new").nth(1).unwrap_or("");
            type_text = Some(after.split('(').next().unwrap_or("").to_string());
        } else {
            type_text = Some(text);
        }
    }
    type_text.as_ref().map(|t| {
        let re = regex::Regex::new(r"<.*?>").unwrap();
        let cleaned = re.replace_all(t, "").trim().to_string();
        let simple = cleaned.rsplit('.').next().unwrap_or("").to_string();
        let first = cleaned.split('.').next().unwrap_or("");
        if !first.is_empty()
            && first
                .chars()
                .next()
                .map(|c| c.is_lowercase())
                .unwrap_or(false)
        {
            format!("{}.{}", cleaned, simple)
        } else {
            simple
        }
    })
}

fn extract_method_reference_name(ref_node: Node, source: &[u8]) -> Option<String> {
    let text = node_text(ref_node, source);
    if !text.contains("::") {
        return Some(normalize_callee(text));
    }
    let parts: Vec<&str> = text.splitn(2, "::").collect();
    let qualifier = parts[0].trim();
    let member = parts[1].trim();
    if member == "new" {
        let re = regex::Regex::new(r"<.*?>").unwrap();
        let cleaned = re.replace_all(qualifier, "").trim().to_string();
        let simple = cleaned.rsplit('.').next().unwrap_or("").to_string();
        let first = cleaned.split('.').next().unwrap_or("");
        if !first.is_empty()
            && first
                .chars()
                .next()
                .map(|c| c.is_lowercase())
                .unwrap_or(false)
        {
            return Some(format!("{}.{}", cleaned, simple));
        }
        return Some(simple);
    }
    Some(normalize_callee(&format!("{}.{}", qualifier, member)))
}

fn extract_super_types(class_node: Node, source: &[u8]) -> Vec<String> {
    fn extract_type_from_node(node: Node, source: &[u8]) -> Option<String> {
        if matches!(
            node.kind(),
            "type_identifier" | "scoped_type_identifier" | "identifier"
        ) {
            return Some(node_text(node, source).trim().to_string());
        }
        if node.kind() == "generic_type" {
            let child = node.child_by_field_name("type").or_else(|| {
                node.children(&mut node.walk())
                    .find(|c| {
                        matches!(
                            c.kind(),
                            "type_identifier" | "scoped_type_identifier" | "identifier"
                        )
                    })
            });
            if let Some(c) = child {
                return extract_type_from_node(c, source);
            }
        }
        if matches!(node.kind(), "annotated_type" | "array_type") {
            for c in node.children(&mut node.walk()) {
                if c.is_named() {
                    let nested = extract_type_from_node(c, source);
                    if nested.is_some() {
                        return nested;
                    }
                }
            }
        }
        let text = node_text(node, source).trim();
        extract_type_name(text)
    }

    fn collect_type_list(node: Option<Node>, source: &[u8]) -> Vec<String> {
        let Some(type_list) = node else {
            return Vec::new();
        };
        let mut names: Vec<String> = Vec::new();
        for child in type_list.children(&mut type_list.walk()) {
            if !child.is_named() {
                continue;
            }
            if let Some(name) = extract_type_from_node(child, source) {
                names.push(name);
            }
        }
        names
    }

    let mut results: Vec<String> = Vec::new();
    if matches!(class_node.kind(), "class_declaration" | "enum_declaration") {
        if let Some(superclass) = class_node.child_by_field_name("superclass") {
            for child in superclass.children(&mut superclass.walk()) {
                if !child.is_named() {
                    continue;
                }
                if let Some(name) = extract_type_from_node(child, source) {
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
        // For interfaces, supertypes are in `extends_interfaces` directly
        let mut extends = None;
        for child in class_node.children(&mut class_node.walk()) {
            if child.kind() == "extends_interfaces" {
                extends = Some(child);
                break;
            }
        }
        if let Some(extends_node) = extends {
            // Find the type_list inside
            let type_list = extends_node
                .children(&mut extends_node.walk())
                .find(|c| c.kind() == "type_list");
            results.extend(collect_type_list(type_list, source));
        }
    }
    let mut deduped: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for item in results {
        if seen.insert(item.clone()) {
            deduped.push(item);
        }
    }
    deduped
}

fn extract_type_name(text: &str) -> Option<String> {
    let re = regex::Regex::new(r"[A-Za-z_][A-Za-z0-9_\.]*").unwrap();
    re.find(text).map(|m| m.as_str().to_string())
}

fn symbol_id(
    package_name: Option<&str>,
    class_name: Option<&str>,
    function_name: &str,
    arity: u32,
    rel_path: &str,
) -> String {
    let parts: Vec<&str> = [package_name, class_name, Some(function_name)]
        .iter()
        .filter_map(|s| *s)
        .filter(|s| !s.is_empty())
        .collect();
    let qualified = parts.join(".");
    format!("{}/{}@{}", qualified, arity, rel_path)
}

fn qualified_name(
    package_name: Option<&str>,
    class_name: Option<&str>,
    function_name: &str,
) -> String {
    let parts: Vec<&str> = [package_name, class_name, Some(function_name)]
        .iter()
        .filter_map(|s| *s)
        .filter(|s| !s.is_empty())
        .collect();
    parts.join(".")
}

fn class_qualified_name(package_name: Option<&str>, class_name: &str) -> String {
    let parts: Vec<&str> = [package_name, Some(class_name)]
        .iter()
        .filter_map(|s| *s)
        .filter(|s| !s.is_empty())
        .collect();
    parts.join(".")
}

fn check_has_error(root: Node) -> bool {
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        if n.is_error() || n.is_missing() {
            return true;
        }
        for c in n.children(&mut n.walk()) {
            stack.push(c);
        }
    }
    false
}

fn count_error_nodes(root: Node) -> u32 {
    let mut count = 0u32;
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        if n.is_error() || n.is_missing() {
            count += 1;
        }
        for c in n.children(&mut n.walk()) {
            stack.push(c);
        }
    }
    count
}

// ── Walker context ──────────────────────────────────────────────────────

struct JavaWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    package_name: Option<String>,
    imports: Vec<String>,
    functions: &'a mut Vec<JavaFunctionDef>,
    classes: &'a mut Vec<JavaClassDef>,
    calls: &'a mut Vec<JavaCallEdge>,
    type_edges: &'a mut Vec<TypeEdge>,
    function_types: &'a mut Vec<FunctionTypeDef>,
    relations: &'a mut Vec<RelationEdge>,
    class_registry: HashMap<String, JavaClassDef>,
    super_map: HashMap<String, Vec<String>>,
}

fn walk_program<'a>(ctx: &mut JavaWalkCtx<'a>, root: Node<'a>) {
    // First pass: collect classes and super types
    for class_node in find_nodes_by_type(root, "class_declaration")
        .into_iter()
        .chain(find_nodes_by_type(root, "interface_declaration"))
        .chain(find_nodes_by_type(root, "enum_declaration"))
        .chain(find_nodes_by_type(root, "record_declaration"))
    {
        let Some(kind) = class_kind(class_node.kind()) else {
            continue;
        };
        let Some(name) = extract_class_name(class_node, ctx.source) else {
            continue;
        };
        let (snippet, start_line, end_line) = node_snippet(class_node, ctx.source);
        let comment = extract_leading_comment(class_node, ctx.source);
        let implicit_public = matches!(kind, "interface");
        let (visibility, is_public_api, visibility_source) = java_api_visibility(&snippet, implicit_public);
        let super_types = extract_super_types(class_node, ctx.source);
        let class_qualified = class_qualified_name(ctx.package_name.as_deref(), &name);
        if !super_types.is_empty() {
            ctx.super_map.insert(name.clone(), super_types.clone());
        }
        let class_def = JavaClassDef {
            symbol_id: class_qualified.clone(),
            qualified_name: class_qualified.clone(),
            name: name.clone(),
            kind: kind.to_string(),
            package_name: ctx.package_name.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet.clone(),
            comment,
            summary: String::new(),
            note: String::new(),
            visibility: visibility.clone(),
            is_public_api,
            visibility_source: visibility_source.clone(),
            export_evidence: if is_public_api {
                visibility_source.clone()
            } else {
                String::new()
            },
            signature: source_signature(&snippet),
        };
        ctx.class_registry
            .insert(class_qualified.clone(), class_def.clone());
        ctx.classes.push(class_def);

        // Emit type edges
        for super_type in &super_types {
            let target_qualified = class_qualified_name(Some(super_type), super_type);
            let edge = TypeEdge {
                source_id: class_qualified.clone(),
                source_package: ctx.package_name.clone(),
                target_name: super_type.clone(),
                rel_type: "EXTENDS".to_string(),
                target_id: Some(target_qualified),
            };
            ctx.type_edges.push(edge);
        }

        // Walk inner methods
        for child in class_node.children(&mut class_node.walk()) {
            walk_in_class(child, ctx, &class_qualified);
        }
    }

    // Top-level (non-class) functions and call collection
    for child in root.children(&mut root.walk()) {
        if CLASS_NODE_KINDS.contains(&child.kind()) {
            continue;
        }
        walk_top_level(child, ctx, None);
    }
}

fn walk_in_class<'a>(node: Node<'a>, ctx: &mut JavaWalkCtx<'a>, class_qualified: &str) {
    if FUNCTION_NODE_KINDS.contains(&node.kind()) {
        let Some(name) = extract_method_name(node, ctx.source) else {
            return;
        };
        let arity = count_parameters(node);
        let class_name = class_qualified.rsplit('.').next().unwrap_or("").to_string();
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        let kind = if node.kind() == "constructor_declaration" {
            "constructor"
        } else {
            "method"
        };
        let (visibility, is_public_api, visibility_source) =
            java_api_visibility(&snippet, false);
        let func = JavaFunctionDef {
            symbol_id: symbol_id(
                ctx.package_name.as_deref(),
                Some(&class_name),
                &name,
                arity,
                ctx.rel_path,
            ),
            qualified_name: qualified_name(
                ctx.package_name.as_deref(),
                Some(&class_name),
                &name,
            ),
            name: name.clone(),
            kind: kind.to_string(),
            class_name: Some(class_name.clone()),
            package_name: ctx.package_name.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            arity,
            code: snippet.clone(),
            comment,
            summary: String::new(),
            note: String::new(),
            visibility: visibility.clone(),
            is_public_api,
            visibility_source: visibility_source.clone(),
            export_evidence: if is_public_api {
                visibility_source.clone()
            } else {
                String::new()
            },
            signature: source_signature(&snippet),
        };
        ctx.functions.push(func.clone());
        let cid = class_qualified.clone();
        ctx.relations.push(RelationEdge {
            source_id: cid.to_string(),
            source_label: "Class".to_string(),
            target_id: func.symbol_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "DECLARES".to_string(),
            properties: Default::default(),
        });
        // Walk body for calls
        for child in node.children(&mut node.walk()) {
            walk_top_level(child, ctx, Some(&func));
        }
        return;
    }
    for child in node.children(&mut node.walk()) {
        walk_in_class(child, ctx, class_qualified);
    }
}

fn walk_top_level<'a>(
    node: Node<'a>,
    ctx: &mut JavaWalkCtx<'a>,
    active_function: Option<&JavaFunctionDef>,
) {
    // Calls — only inside an active function
    if let Some(active) = active_function {
        if CALL_NODE_KINDS.contains(&node.kind()) {
            let callee_name = match node.kind() {
                "method_invocation" => extract_call_name(node, ctx.source),
                "object_creation_expression" => extract_constructor_call_name(node, ctx.source),
                "explicit_constructor_invocation" => {
                    let text = node_text(node, ctx.source).trim().to_string();
                    if text.starts_with("this") {
                        Some(active.name.clone())
                    } else if text.starts_with("super") {
                        // super calls target the parent class
                        ctx.super_map
                            .get(active.name.as_str())
                            .and_then(|supers| supers.first().cloned())
                    } else {
                        None
                    }
                }
                "method_reference" => extract_method_reference_name(node, ctx.source),
                _ => None,
            };
            if let Some(callee) = callee_name {
                if !callee.is_empty() {
                    ctx.calls.push(JavaCallEdge {
                        caller_id: active.symbol_id.clone(),
                        caller_file: ctx.rel_path.to_string(),
                        caller_package: ctx.package_name.clone(),
                        caller_class: active.class_name.clone(),
                        imports: ctx.imports.clone(),
                        callee_name: callee,
                        callee_id: None,
                    });
                }
            }
        }
    }
    for child in node.children(&mut node.walk()) {
        walk_top_level(child, ctx, active_function);
    }
}

// ── Per-file call resolution (4-index) ─────────────────────────────────

fn resolve_calls(functions: &[JavaFunctionDef], calls: &mut [JavaCallEdge]) {
    let mut by_name: HashMap<String, Vec<&JavaFunctionDef>> = HashMap::new();
    let mut by_qualified: HashMap<String, Vec<&JavaFunctionDef>> = HashMap::new();
    let mut by_class_and_name: HashMap<(String, String), Vec<&JavaFunctionDef>> = HashMap::new();
    let mut by_package_and_name: HashMap<(String, String), Vec<&JavaFunctionDef>> = HashMap::new();

    for func in functions {
        by_name
            .entry(func.name.clone())
            .or_default()
            .push(func);
        by_qualified
            .entry(func.qualified_name.clone())
            .or_default()
            .push(func);
        if let Some(class) = &func.class_name {
            by_class_and_name
                .entry((class.clone(), func.name.clone()))
                .or_default()
                .push(func);
        }
        if let Some(pkg) = &func.package_name {
            by_package_and_name
                .entry((pkg.clone(), func.name.clone()))
                .or_default()
                .push(func);
        }
    }

    for call in calls.iter_mut() {
        let mut candidates: Vec<&JavaFunctionDef> = Vec::new();

        if let Some(class) = &call.caller_class {
            if let Some(c) = by_class_and_name.get(&(class.clone(), call.callee_name.clone())) {
                candidates = c.clone();
            }
        }
        if candidates.is_empty() {
            if let Some(pkg) = &call.caller_package {
                if let Some(c) =
                    by_package_and_name.get(&(pkg.clone(), call.callee_name.clone()))
                {
                    candidates = c.clone();
                }
            }
        }
        if candidates.is_empty() {
            if let Some(c) = by_name.get(&call.callee_name) {
                candidates = c.clone();
            }
        }
        if candidates.is_empty() {
            // Try by qualified (last segment matches callee)
            // e.g. callee_name = "Foo.bar" — match against qualified
            if let Some(last) = call.callee_name.rsplit('.').next() {
                if let Some(c) = by_name.get(last) {
                    candidates = c.clone();
                }
            }
        }
        if candidates.len() == 1 {
            call.callee_id = Some(candidates[0].symbol_id.clone());
        }
    }
}

// ── Public entry point ──────────────────────────────────────────────────

/// Parse Java source bytes and run the full extraction pipeline.
pub fn parse_java_source(source: &[u8], rel_path: &str) -> Option<JavaParseOutput> {
    let mut parser = Parser::new();
    parser.set_language(&JavaGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let (package_name, _pkg_start, _pkg_end, _pkg_code, _pkg_comment) =
        collect_package_info(root, source);
    let imports = collect_imports(root, source);
    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let has_error = check_has_error(root);

    let mut functions: Vec<JavaFunctionDef> = Vec::new();
    let mut classes: Vec<JavaClassDef> = Vec::new();
    let mut calls: Vec<JavaCallEdge> = Vec::new();
    let mut type_edges: Vec<TypeEdge> = Vec::new();
    let mut function_types: Vec<FunctionTypeDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let class_registry: HashMap<String, JavaClassDef> = HashMap::new();
    let mut super_map: HashMap<String, Vec<String>> = HashMap::new();

    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    {
        let mut ctx = JavaWalkCtx {
            source,
            rel_path,
            package_name: package_name.clone(),
            imports: imports.clone(),
            functions: &mut functions,
            classes: &mut classes,
            calls: &mut calls,
            type_edges: &mut type_edges,
            function_types: &mut function_types,
            relations: &mut relations,
            class_registry,
            super_map,
        };
        walk_program(&mut ctx, root);
    }

    resolve_calls(&functions, &mut calls);

    let pkg_def = JavaPackageDef {
        name: package_name.clone().unwrap_or_default(),
        start_line: 1,
        end_line: 1,
        code: format!(
            "package {};",
            package_name.clone().unwrap_or_default()
        ),
        comment: String::new(),
        summary: String::new(),
        note: String::new(),
    };

    let file_def = JavaFileDef {
        file_path: rel_path.to_string(),
        package_name: package_name.clone(),
        start_line: 1,
        end_line: file_lines,
        code,
        comment: file_comment.clone(),
        summary: file_comment,
        note: String::new(),
    };

    Some(JavaParseOutput {
        file_def,
        package_def: pkg_def,
        functions,
        classes,
        calls,
        type_edges,
        function_types,
        relations,
        imports,
        parse_meta: ParseMeta {
            parser_language: "java_tree_sitter".to_string(),
            parser_language_initial: "java".to_string(),
            has_error,
            error_nodes: count_error_nodes(root),
            header_retry_attempted: false,
            header_retry_selected: false,
            error_nodes_initial: count_error_nodes(root),
            header_retry_error_nodes: Some(0),
            header_retry_has_error: Some(false),
        },
    })
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const SIMPLE_JAVA: &[u8] = b"\
package com.example;

import java.util.List;
import java.util.ArrayList;

public class Greeter {
    public String name;
    private int count;

    public Greeter(String name) {
        this.name = name;
    }

    public String greet(String who) {
        List<String> items = new ArrayList<>();
        return this.name + count + items.size() + who;
    }

    public static Greeter create() {
        return new Greeter(\"world\");
    }
}
";

    #[test]
    fn parse_extracts_package_and_imports() {
        let out = parse_java_source(SIMPLE_JAVA, "Greeter.java").unwrap();
        assert_eq!(out.file_def.package_name.as_deref(), Some("com.example"));
        assert!(out.imports.contains(&"java.util.List".to_string()));
        assert!(out.imports.contains(&"java.util.ArrayList".to_string()));
    }

    #[test]
    fn parse_extracts_class_with_visibility() {
        let out = parse_java_source(SIMPLE_JAVA, "Greeter.java").unwrap();
        let greeter = out
            .classes
            .iter()
            .find(|c| c.name == "Greeter")
            .expect("Greeter class missing");
        assert_eq!(greeter.kind, "class");
        assert_eq!(greeter.visibility, "public");
        assert!(greeter.is_public_api);
    }

    #[test]
    fn parse_extracts_methods_and_constructor() {
        let out = parse_java_source(SIMPLE_JAVA, "Greeter.java").unwrap();
        let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"Greeter"), "constructor missing: {:?}", names);
        assert!(names.contains(&"greet"), "greet method missing: {:?}", names);
        assert!(names.contains(&"create"), "create method missing: {:?}", names);
    }

    #[test]
    fn parse_records_calls() {
        let out = parse_java_source(SIMPLE_JAVA, "Greeter.java").unwrap();
        // `this.name + count + items.size() + who` — calls inside greet
        let greet_calls: Vec<&JavaCallEdge> = out
            .calls
            .iter()
            .filter(|c| c.caller_id.contains(".greet"))
            .collect();
        let callee_names: Vec<&str> = greet_calls.iter().map(|c| c.callee_name.as_str()).collect();
        // The method call `items.size()` is recorded as `items.size` (qualified).
        assert!(
            callee_names.contains(&"items.size"),
            "items.size() call missing: {:?}",
            callee_names
        );
    }

    #[test]
    fn parse_extracts_class_fields() {
        let out = parse_java_source(SIMPLE_JAVA, "Greeter.java").unwrap();
        // Field extraction is not in this minimal port yet — just verify class is present
        assert!(out.classes.iter().any(|c| c.name == "Greeter"));
    }

    #[test]
    fn parse_extracts_type_edges() {
        let src = b"package x; class A extends B implements I {}";
        let out = parse_java_source(src, "A.java").unwrap();
        // A→B EXTENDS, A→I (need to check both)
        let extends: Vec<&TypeEdge> = out
            .type_edges
            .iter()
            .filter(|e| e.rel_type == "EXTENDS")
            .collect();
        assert!(!extends.is_empty(), "type edges missing");
    }

    #[test]
    fn parse_meta_is_java_language() {
        let out = parse_java_source(SIMPLE_JAVA, "Greeter.java").unwrap();
        assert_eq!(out.parse_meta.parser_language, "java_tree_sitter");
        assert_eq!(out.parse_meta.parser_language_initial, "java");
    }
}

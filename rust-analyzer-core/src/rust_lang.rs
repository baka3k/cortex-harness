//! Rust tree-sitter walker — Tier 1 port of `rust_analyzer.py`.
//!
//! Faithful Rust port of the Python `parse_rust_file` → `_walk_tree` pipeline.
//! Rust (the language) shares the C++ payload *keys* but the scalar fields
//! differ in type (list-typed like Go):
//!
//! | field           | C++ (`ParseOutput`)   | Rust (this module)       |
//! |-----------------|-----------------------|--------------------------|
//! | `using_imports` | `HashMap<String,Str>` | `Vec<String>` (list)     |
//! | `includes`      | `Vec<String>`         | `Vec<String>`            |
//! | `macros`        | `HashMap<String,Str>` | `Vec<String>` (list)     |
//!
//! That's why Rust has its own `RustParseOutput` and `build_rust_payload`
//! rather than reusing the C++ builder.

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{Grammar, RustGrammar};
use crate::symbols::{
    AliasDef, CallEdge, FieldDef, FileDef, FunctionDef, NamespaceDef, ParseMeta, RelationEdge,
    TemplateDef, TypeDef,
};
use crate::text::{extract_file_comment, node_text};

// ── Node-type sets (mirror the Python module constants) ─────────────────

const COMMENT_TYPES: &[&str] = &["line_comment", "block_comment"];

/// Compute snippet + adjusted start_line + end_line, including preceding
/// contiguous comments. Mirrors Python `_node_snippet`.
fn node_snippet_rust<'a>(node: Node<'a>, source: &[u8]) -> (String, u32, u32) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if COMMENT_TYPES.contains(&p.kind()) {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = std::str::from_utf8(&source[start_byte..node.end_byte()])
        .unwrap_or("")
        .to_string();
    let start_line = line_from_byte(source, start_byte);
    let end_line = node.end_position().row as u32 + 1;
    (snippet, start_line, end_line)
}

/// Maps tree-sitter node kind → semantic type kind (struct/enum/union/trait).
fn type_kind_for(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "struct_item" => Some("struct"),
        "enum_item" => Some("enum"),
        "union_item" => Some("union"),
        "trait_item" => Some("interface"),
        _ => None,
    }
}

const FUNCTION_NODES: &[&str] = &["function_item", "function_signature_item"];
const MODULE_NODES: &[&str] = &["mod_item"];
const IMPL_NODES: &[&str] = &["impl_item"];
const ALIAS_NODES: &[&str] = &["type_item"];
const CALL_NODES: &[&str] = &["call_expression", "method_call_expression", "macro_invocation"];

fn branch_kind_of(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "if_expression" => Some("if"),
        "match_expression" => Some("match"),
        "match_arm" => Some("match_arm"),
        _ => None,
    }
}

fn is_loop_node(node_kind: &str) -> bool {
    matches!(
        node_kind,
        "loop_expression" | "while_expression" | "for_expression"
    )
}

// ── Rust-specific ParseOutput (list-typed using_imports/macros) ──────────

/// Rust payload — mirrors the dict shape returned by Python `parse_rust_file`.
#[derive(Debug, Default)]
pub struct RustParseOutput {
    pub file_def: FileDef,
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub function_types: Vec<crate::symbols::FunctionTypeDef>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<AliasDef>,
    pub templates: Vec<TemplateDef>,
    pub using_namespaces: Vec<String>,
    pub using_imports: Vec<String>,
    pub includes: Vec<String>,
    pub macros: Vec<String>,
    pub parse_meta: ParseMeta,
}

// ── Helpers (ports of the Python `_foo` helpers) ────────────────────────

#[inline]
fn line_from_byte(source: &[u8], byte_index: usize) -> u32 {
    source[..byte_index]
        .iter()
        .filter(|&&b| b == b'\n')
        .count() as u32
        + 1
}

fn extract_name<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source).trim().to_string());
    }
    first_identifier(node, source)
}

/// Recursive first-identifier walk — mirrors Python `_first_identifier`.
fn first_identifier<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    match node.kind() {
        "identifier"
        | "type_identifier"
        | "field_identifier"
        | "scoped_identifier"
        | "scoped_type_identifier" => {
            Some(node_text(node, source).trim().to_string())
        }
        _ => {
            for child in node.children(&mut node.walk()) {
                if let Some(s) = first_identifier(child, source) {
                    return Some(s);
                }
            }
            None
        }
    }
}

fn find_nodes_by_type<'a>(node: Node<'a>, node_type: &str) -> Vec<Node<'a>> {
    let mut found = Vec::new();
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        if current.kind() == node_type {
            found.push(current);
        }
        let mut children = current
            .children(&mut current.walk())
            .collect::<Vec<_>>();
        children.reverse();
        stack.extend(children);
    }
    found
}

fn first_named_child_of_type<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    let allowed: std::collections::HashSet<&str> = types.iter().copied().collect();
    for child in node.children(&mut node.walk()) {
        if !child.is_named() {
            continue;
        }
        if allowed.contains(child.kind()) {
            return Some(child);
        }
    }
    None
}

/// Scope separator for Rust is `::` (unlike Go's `.`).
#[inline]
fn qualified_name(scope_stack: &[String], name: &str) -> String {
    if scope_stack.is_empty() {
        name.to_string()
    } else {
        let mut parts: Vec<&str> = scope_stack.iter().map(|s| s.as_str()).collect();
        parts.push(name);
        parts.join("::")
    }
}

#[inline]
fn scope_name_of(scope_stack: &[String]) -> Option<String> {
    if scope_stack.is_empty() {
        None
    } else {
        Some(scope_stack.join("::"))
    }
}

fn symbol_id(qualified_name: &str, arity: u32, rel_path: &str) -> String {
    format!("{}/{}@{}", qualified_name, arity, rel_path)
}

fn type_id(qualified_name: &str) -> String {
    qualified_name.to_string()
}

fn namespace_id(qualified_name: &str) -> String {
    format!("namespace::{}", qualified_name)
}

fn anonymous_name(prefix: &str, node: Node) -> String {
    let pos = node.start_position();
    format!(
        "Anonymous{}@{}:{}",
        prefix,
        pos.row as u32 + 1,
        pos.column as u32 + 1
    )
}

fn extract_comment<'a>(node: Node<'a>, source: &[u8]) -> String {
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

fn count_parameters<'a>(node: Node<'a>) -> u32 {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| first_named_child_of_type(node, &["parameters", "parameter_list"]));
    let Some(params) = params else {
        return 0;
    };
    params
        .children(&mut params.walk())
        .filter(|c| c.is_named() && !COMMENT_TYPES.contains(&c.kind()))
        .count() as u32
}

fn count_arguments<'a>(node: Node<'a>) -> u32 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| first_named_child_of_type(node, &["arguments", "argument_list"]));
    let Some(args) = args else {
        return 0;
    };
    args.children(&mut args.walk())
        .filter(|c| c.is_named() && !COMMENT_TYPES.contains(&c.kind()))
        .count() as u32
}

fn extract_type_signature<'a>(node: Node<'a>, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    let text = node_text(node, source).trim().to_string();
    if let Some(colon_idx) = text.find(':') {
        return text[colon_idx + 1..].trim().trim_end_matches(',').to_string();
    }
    String::new()
}

fn extract_alias_target<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if let Some(type_node) = node.child_by_field_name("type") {
        return Some(node_text(type_node, source).trim().to_string());
    }
    let text = node_text(node, source);
    let re = regex::Regex::new(r"=\s*(.*?)\s*;").ok()?;
    let caps = re.captures(&text)?;
    let target = caps.get(1)?.as_str();
    Some(
        regex::Regex::new(r"\s+")
            .unwrap()
            .replace_all(target, " ")
            .trim()
            .to_string(),
    )
}

fn extract_use_path<'a>(node: Node<'a>, source: &[u8]) -> String {
    let text = node_text(node, source).trim();
    let text = regex::Regex::new(r"^pub\s+")
        .unwrap()
        .replace(text, "");
    let text = regex::Regex::new(r"^use\s+")
        .unwrap()
        .replace(&text, "");
    text.trim_end_matches(';').trim().to_string()
}

fn call_name<'a>(call_node: Node<'a>, source: &[u8]) -> String {
    if call_node.kind() == "method_call_expression" {
        if let Some(name_node) = call_node.child_by_field_name("name") {
            return node_text(name_node, source).trim().to_string();
        }
    }
    if call_node.kind() == "macro_invocation" {
        return extract_name(call_node, source)
            .unwrap_or_else(|| anonymous_name("Macro", call_node));
    }
    if let Some(function_node) = call_node.child_by_field_name("function") {
        let text = node_text(function_node, source).trim();
        return text
            .split("::")
            .last()
            .unwrap_or(text)
            .split('.')
            .last()
            .unwrap_or(text)
            .to_string();
    }
    let text = node_text(call_node, source).trim();
    text.split('(')
        .next()
        .unwrap_or("")
        .trim()
        .split("::")
        .last()
        .unwrap_or("")
        .to_string()
}

fn control_context<'a>(node: Node<'a>) -> (String, u32, String) {
    let mut frames: Vec<HashMap<String, serde_json::Value>> = Vec::new();
    let mut branch_kind = "none".to_string();
    let mut loop_depth = 0u32;
    let mut parent = node.parent();
    while let Some(p) = parent {
        if let Some(kind) = branch_kind_of(p.kind()) {
            if branch_kind == "none" {
                branch_kind = kind.to_string();
            }
            let mut frame = HashMap::new();
            frame.insert(
                "kind".to_string(),
                serde_json::Value::String(kind.to_string()),
            );
            frame.insert(
                "line".to_string(),
                serde_json::Value::Number(serde_json::Number::from(
                    p.start_position().row as u32 + 1,
                )),
            );
            frames.push(frame);
        } else if is_loop_node(p.kind()) {
            loop_depth += 1;
            let mut frame = HashMap::new();
            frame.insert(
                "kind".to_string(),
                serde_json::Value::String("loop".to_string()),
            );
            frame.insert(
                "line".to_string(),
                serde_json::Value::Number(serde_json::Number::from(
                    p.start_position().row as u32 + 1,
                )),
            );
            frames.push(frame);
        }
        parent = p.parent();
    }
    frames.reverse();
    let frames_json: Vec<serde_json::Value> = frames
        .into_iter()
        .map(|f| serde_json::Value::Object(f.into_iter().collect()))
        .collect();
    let compact = serde_json::Value::Array(frames_json).to_string();
    // Match Python json.dumps default formatting (spaces after : and ,)
    let spaced = compact.replace(":", ": ").replace(",", ", ");
    (branch_kind, loop_depth, spaced)
}

fn record_relation(
    relations: &mut Vec<RelationEdge>,
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
) {
    relations.push(RelationEdge {
        source_id: source_id.to_string(),
        source_label: source_label.to_string(),
        target_id: target_id.to_string(),
        target_label: target_label.to_string(),
        rel_type: rel_type.to_string(),
        properties: HashMap::new(),
    });
}

fn add_type_use(
    owner_id: &str,
    owner_label: &str,
    type_text: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    relations: &mut Vec<RelationEdge>,
    external_types: &mut HashMap<String, TypeDef>,
) {
    // Strip Rust pointer/reference markers and punctuation.
    let cleaned = regex::Regex::new(r"[<&*\[\](),;]")
        .unwrap()
        .replace_all(type_text, " ");
    let cleaned = regex::Regex::new(
        r"\b(mut|ref|pub|crate|self|Self|where|dyn|impl)\b",
    )
    .unwrap()
    .replace_all(&cleaned, " ");

    let candidates: Vec<String> = cleaned
        .split(|c: char| c.is_whitespace() || c == ':')
        .filter(|s| {
            if s.is_empty() {
                return false;
            }
            s.chars().next().map(|c| c.is_uppercase()).unwrap_or(false)
        })
        .map(|s| s.to_string())
        .collect();

    let is_pointer = type_text.contains('&')
        || type_text.contains("*const")
        || type_text.contains("*mut");
    let rel_type = if is_pointer {
        "POINTER_TO"
    } else {
        "USES_TYPE"
    };

    for candidate in candidates {
        let target = type_id(&candidate);
        if !external_types.contains_key(&target) {
            let ext = TypeDef {
                symbol_id: target.clone(),
                qualified_name: candidate.clone(),
                name: candidate.clone(),
                kind: "external".to_string(),
                file_path: rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: candidate,
                ..Default::default()
            };
            external_types.insert(target.clone(), ext.clone());
            types.push(ext);
        }
        record_relation(
            relations,
            owner_id,
            owner_label,
            &target,
            "Type",
            rel_type,
        );
    }
}

fn extract_templates<'a>(node: Node<'a>, rel_path: &str, source: &[u8]) -> Vec<TemplateDef> {
    let mut templates = Vec::new();
    for template_node in find_nodes_by_type(node, "type_parameters") {
        let text = node_text(template_node, source).trim().to_string();
        let start_line = template_node.start_position().row as u32 + 1;
        let end_line = template_node.end_position().row as u32 + 1;
        templates.push(TemplateDef {
            symbol_id: format!("template::{}:{}:{}", rel_path, start_line, end_line),
            name: text.clone(),
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            code: text,
        });
    }
    templates
}

// ── The recursive walker (mirrors Python `_walk_tree`) ──────────────────

/// Mutable state threaded through the Rust walk.
struct RustWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    functions: &'a mut Vec<FunctionDef>,
    calls: &'a mut Vec<CallEdge>,
    types: &'a mut Vec<TypeDef>,
    namespaces: &'a mut Vec<NamespaceDef>,
    relations: &'a mut Vec<RelationEdge>,
    fields: &'a mut Vec<FieldDef>,
    aliases: &'a mut Vec<AliasDef>,
    templates: &'a mut Vec<TemplateDef>,
    namespace_registry: &'a mut HashMap<String, NamespaceDef>,
    type_registry: &'a mut HashMap<String, TypeDef>,
    external_types: &'a mut HashMap<String, TypeDef>,
}

#[allow(clippy::too_many_arguments)]
fn walk<'a>(
    node: Node<'a>,
    scope_stack: &[String],
    active_function: Option<&FunctionDef>,
    ctx: &mut RustWalkCtx<'a>,
) {
    let kind = node.kind();

    // ── Modules (mod_item) → NamespaceDef ──
    if MODULE_NODES.contains(&kind) {
        let name =
            extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Module", node));
        let qualified = qualified_name(scope_stack, &name);
        let ns_id = namespace_id(&qualified);
        let (snippet, start_line, end_line) = node_snippet_rust(node, ctx.source);
        let comment = extract_comment(node, ctx.source);
        let namespace = NamespaceDef {
            symbol_id: ns_id.clone(),
            qualified_name: qualified.clone(),
            name: name.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment,
            ..Default::default()
        };
        if !ctx.namespace_registry.contains_key(&ns_id) {
            ctx.namespace_registry.insert(ns_id.clone(), namespace.clone());
            ctx.namespaces.push(namespace);
        }
        if let Some(owner_scope) = scope_name_of(scope_stack) {
            record_relation(
                ctx.relations,
                &namespace_id(&owner_scope),
                "Namespace",
                &ns_id,
                "Namespace",
                "CONTAINS",
            );
        }
        let mut child_scope = scope_stack.to_vec();
        child_scope.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, &child_scope, active_function, ctx);
        }
        return;
    }

    // ── Types (struct_item / enum_item / union_item / trait_item) ──
    if let Some(type_kind) = type_kind_for(kind) {
        let name =
            extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Type", node));
        let qualified = qualified_name(scope_stack, &name);
        let tid = type_id(&qualified);
        let (snippet, start_line, end_line) = node_snippet_rust(node, ctx.source);
        let comment = extract_comment(node, ctx.source);
        let type_def = TypeDef {
            symbol_id: tid.clone(),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: type_kind.to_string(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment: comment.clone(),
            ..Default::default()
        };
        if !ctx.type_registry.contains_key(&tid) {
            ctx.type_registry.insert(tid.clone(), type_def.clone());
            ctx.types.push(type_def);
        }
        if let Some(owner_scope) = scope_name_of(scope_stack) {
            let owner_id = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                type_id(&owner_scope)
            } else {
                namespace_id(&owner_scope)
            };
            let owner_label = if ctx
                .type_registry
                .contains_key(&type_id(&owner_scope))
            {
                "Type"
            } else {
                "Namespace"
            };
            record_relation(
                ctx.relations,
                &owner_id,
                owner_label,
                &tid,
                "Type",
                "DECLARES",
            );
        }
        // trait_item: record supertrait bounds as type uses.
        if kind == "trait_item" {
            for bound in find_nodes_by_type(node, "trait_bounds") {
                add_type_use(
                    &tid,
                    "Type",
                    node_text(bound, ctx.source),
                    ctx.rel_path,
                    ctx.types,
                    ctx.relations,
                    ctx.external_types,
                );
            }
        }
        for template in extract_templates(node, ctx.rel_path, ctx.source) {
            record_relation(
                ctx.relations,
                &template.symbol_id,
                "Template",
                &tid,
                "Type",
                "TEMPLATES",
            );
            ctx.templates.push(template);
        }
        let mut child_scope = scope_stack.to_vec();
        child_scope.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, &child_scope, active_function, ctx);
        }
        return;
    }

    // ── Impl blocks (impl_item) → push scope, descend ──
    if IMPL_NODES.contains(&kind) {
        let impl_name = extract_name(node, ctx.source);
        let mut child_scope = scope_stack.to_vec();
        if let Some(ref name) = impl_name {
            child_scope.push(name.clone());
        }
        for child in node.children(&mut node.walk()) {
            walk(child, &child_scope, active_function, ctx);
        }
        return;
    }

    // ── Functions (function_item / function_signature_item) ──
    if FUNCTION_NODES.contains(&kind) {
        let name =
            extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Function", node));
        let arity = count_parameters(node);
        let qualified = qualified_name(scope_stack, &name);
        let func_kind = if kind == "function_signature_item" {
            "declaration"
        } else {
            "function"
        };
        let func = FunctionDef {
            symbol_id: symbol_id(&qualified, arity, ctx.rel_path),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: func_kind.to_string(),
            scope_name: scope_name_of(scope_stack),
            file_path: ctx.rel_path.to_string(),
            start_byte: node.start_byte() as u32,
            end_byte: node.end_byte() as u32,
            start_line: node.start_position().row as u32 + 1,
            end_line: node.end_position().row as u32 + 1,
            arity,
            code: node_text(node, ctx.source).to_string(),
            comment: extract_comment(node, ctx.source),
            ..Default::default()
        };
        ctx.functions.push(func.clone());
        if let Some(owner_scope) = scope_name_of(scope_stack) {
            let owner_id = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                type_id(&owner_scope)
            } else {
                namespace_id(&owner_scope)
            };
            let owner_label = if ctx
                .type_registry
                .contains_key(&type_id(&owner_scope))
            {
                "Type"
            } else {
                "Namespace"
            };
            record_relation(
                ctx.relations,
                &owner_id,
                owner_label,
                &func.symbol_id,
                "Function",
                "DECLARES",
            );
        }
        for child in node.children(&mut node.walk()) {
            walk(child, scope_stack, Some(&func), ctx);
        }
        return;
    }

    // ── Type aliases (type_item) ──
    if ALIAS_NODES.contains(&kind) {
        let name =
            extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Alias", node));
        let qualified = qualified_name(scope_stack, &name);
        let target = extract_alias_target(node, ctx.source);
        let alias = AliasDef {
            symbol_id: format!("alias::{}@{}", qualified, ctx.rel_path),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: "type".to_string(),
            target_name: target.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line: node.start_position().row as u32 + 1,
            end_line: node.end_position().row as u32 + 1,
            code: node_text(node, ctx.source).to_string(),
            ..Default::default()
        };
        ctx.aliases.push(alias.clone());
        if let Some(ref target) = target {
            let target_id = type_id(target);
            record_relation(
                ctx.relations,
                &alias.symbol_id,
                "Alias",
                &target_id,
                "Type",
                "ALIASES",
            );
            add_type_use(
                &alias.symbol_id,
                "Alias",
                target,
                ctx.rel_path,
                ctx.types,
                ctx.relations,
                ctx.external_types,
            );
        }
        // Fall through — aliases don't return so we descend into children.
    }

    // ── Calls (only inside an active function) ──
    if let Some(active) = active_function {
        if CALL_NODES.contains(&kind) {
            let callee_name = call_name(node, ctx.source);
            let (branch_kind_val, loop_depth, control_frames) = control_context(node);
            let call_type = if kind == "macro_invocation" {
                "macro"
            } else if kind == "method_call_expression" {
                "method"
            } else {
                "function"
            };
            ctx.calls.push(CallEdge {
                caller_id: active.symbol_id.clone(),
                caller_file: ctx.rel_path.to_string(),
                caller_scope: active.scope_name.clone(),
                call_line: node.start_position().row as u32 + 1,
                call_column: node.start_position().column as u32 + 1,
                call_start_byte: node.start_byte() as u32,
                call_branch_kind: branch_kind_val,
                call_loop_depth: loop_depth,
                call_control_frames_json: control_frames,
                call_type: call_type.to_string(),
                call_arity: count_arguments(node),
                callee_name,
                callee_id: None,
            });
        }
    }

    // ── Fields (inside a type scope) ──
    if !scope_stack.is_empty() && kind == "field_declaration" {
        if let Some(name) = extract_name(node, ctx.source) {
            let owner = scope_name_of(scope_stack).unwrap_or_default();
            let qualified = if owner.is_empty() {
                name.clone()
            } else {
                format!("{}::{}", owner, name)
            };
            let type_signature = extract_type_signature(node, ctx.source);
            let field_id = format!("field::{}@{}", qualified, ctx.rel_path);
            ctx.fields.push(FieldDef {
                symbol_id: field_id.clone(),
                qualified_name: qualified,
                name: name.clone(),
                scope_name: Some(owner.clone()),
                type_signature: type_signature.clone(),
                file_path: ctx.rel_path.to_string(),
                start_line: node.start_position().row as u32 + 1,
                end_line: node.end_position().row as u32 + 1,
                code: node_text(node, ctx.source).to_string(),
            });
            if !owner.is_empty() {
                let owner_type_id = type_id(&owner);
                record_relation(
                    ctx.relations,
                    &owner_type_id,
                    "Type",
                    &field_id,
                    "Field",
                    "DECLARES",
                );
            }
            if !type_signature.is_empty() {
                add_type_use(
                    &field_id,
                    "Field",
                    &type_signature,
                    ctx.rel_path,
                    ctx.types,
                    ctx.relations,
                    ctx.external_types,
                );
            }
        }
    }

    // ── Default: descend into children with same state ──
    for child in node.children(&mut node.walk()) {
        walk(child, scope_stack, active_function, ctx);
    }
}

// ── Call resolution (mirrors Python `_resolve_calls`) ───────────────────

fn resolve_calls(
    functions: &[FunctionDef],
    calls: &mut [CallEdge],
    relations: &mut Vec<RelationEdge>,
) {
    let mut by_name: HashMap<String, Vec<&FunctionDef>> = HashMap::new();
    let mut by_name_arity: HashMap<(String, u32), Vec<&FunctionDef>> = HashMap::new();
    for func in functions {
        by_name.entry(func.name.clone()).or_default().push(func);
        by_name_arity
            .entry((func.name.clone(), func.arity))
            .or_default()
            .push(func);
    }

    for call in calls.iter_mut() {
        let key = (call.callee_name.clone(), call.call_arity);
        let mut candidates: Vec<&FunctionDef> = by_name_arity
            .get(&key)
            .cloned()
            .or_else(|| by_name.get(&call.callee_name).cloned())
            .unwrap_or_default();
        if candidates.is_empty() {
            continue;
        }
        if candidates.len() > 1 {
            if let Some(ref scope) = call.caller_scope {
                let scoped: Vec<&FunctionDef> = candidates
                    .iter()
                    .copied()
                    .filter(|f| f.scope_name.as_deref() == Some(scope.as_str()))
                    .collect();
                if !scoped.is_empty() {
                    candidates = scoped;
                }
            }
        }
        if candidates.len() == 1 {
            let callee_id = candidates[0].symbol_id.clone();
            call.callee_id = Some(callee_id.clone());
            record_relation(
                relations,
                &call.caller_id,
                "Function",
                &callee_id,
                "Function",
                "POSSIBLE_CALLS",
            );
        }
    }
}

// ── Import / macro / includes collection ────────────────────────────────

/// Collect `use` declarations — returns `(namespaces, imports)`.
fn collect_imports<'a>(root: Node<'a>, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut using_imports: Vec<String> = Vec::new();
    let mut using_namespaces: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "use_declaration") {
        let path = extract_use_path(node, source);
        if path.is_empty() {
            continue;
        }
        using_imports.push(path.clone());
        if path.ends_with("::*") {
            using_namespaces.push(path[..path.len() - 3].to_string());
        }
    }
    (using_namespaces, using_imports)
}

fn collect_macros<'a>(root: Node<'a>, source: &[u8]) -> Vec<String> {
    let mut macros: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "macro_invocation") {
        if let Some(name) = extract_name(node, source) {
            if !macros.contains(&name) {
                macros.push(name);
            }
        }
    }
    macros
}

fn collect_includes<'a>(root: Node<'a>, source: &[u8]) -> Vec<String> {
    let mut includes: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "extern_crate_declaration") {
        if let Some(name) = extract_name(node, source) {
            includes.push(name);
        }
    }
    includes
}

// ── Error scanning helpers ──────────────────────────────────────────────

fn check_has_error<'a>(root: Node<'a>) -> bool {
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

fn count_error_nodes<'a>(root: Node<'a>) -> u32 {
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

// ── Public entry point ──────────────────────────────────────────────────

/// Parse Rust source bytes and run the full extraction pipeline.
///
/// This is the Rust equivalent of Python `parse_rust_file(path, root)` minus
/// file I/O — callers pass the already-read `source` and the `rel_path` that
/// should appear in `file_def.file_path`.
pub fn parse_rust_source(source: &[u8], rel_path: &str) -> Option<RustParseOutput> {
    let mut parser = Parser::new();
    parser.set_language(&RustGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let (using_namespaces, using_imports) = collect_imports(root, source);
    let includes = collect_includes(root, source);
    let macros = collect_macros(root, source);
    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let has_error = check_has_error(root);

    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut types: Vec<TypeDef> = Vec::new();
    let mut namespaces: Vec<NamespaceDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let mut fields: Vec<FieldDef> = Vec::new();
    let mut aliases: Vec<AliasDef> = Vec::new();
    let mut templates: Vec<TemplateDef> = Vec::new();
    let mut namespace_registry: HashMap<String, NamespaceDef> = HashMap::new();
    let mut type_registry: HashMap<String, TypeDef> = HashMap::new();
    let mut external_types: HashMap<String, TypeDef> = HashMap::new();

    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    {
        let mut ctx = RustWalkCtx {
            source,
            rel_path,
            functions: &mut functions,
            calls: &mut calls,
            types: &mut types,
            namespaces: &mut namespaces,
            relations: &mut relations,
            fields: &mut fields,
            aliases: &mut aliases,
            templates: &mut templates,
            namespace_registry: &mut namespace_registry,
            type_registry: &mut type_registry,
            external_types: &mut external_types,
        };
        for child in root.children(&mut root.walk()) {
            walk(child, &[], None, &mut ctx);
        }
    }

    resolve_calls(&functions, &mut calls, &mut relations);

    let file_def = FileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: file_lines,
        code,
        comment: file_comment.clone(),
        summary: file_comment,
        note: String::new(),
    };

    Some(RustParseOutput {
        file_def,
        functions,
        calls,
        types,
        namespaces,
        relations,
        function_types: Vec::new(),
        fields,
        aliases,
        templates,
        using_namespaces,
        using_imports,
        includes,
        macros,
        parse_meta: ParseMeta {
            parser_language: "rust_tree_sitter".to_string(),
            parser_language_initial: "rust".to_string(),
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

    const SIMPLE_RUST: &[u8] = b"\
use std::fmt;

struct Greeter {
    name: String,
}

impl Greeter {
    fn hello(&self, who: &str) -> String {
        format!(\"Hello, {}\", who)
    }
}

fn main() {
    let g = Greeter { name: \"world\".to_string() };
    g.hello(&g.name);
}
";

    #[test]
    fn parse_extracts_imports() {
        let out = parse_rust_source(SIMPLE_RUST, "main.rs").unwrap();
        assert!(
            out.using_imports.contains(&"std::fmt".to_string()),
            "using_imports = {:?}",
            out.using_imports
        );
    }

    #[test]
    fn parse_extracts_struct_and_method_and_function() {
        let out = parse_rust_source(SIMPLE_RUST, "main.rs").unwrap();
        let has_greeter = out
            .types
            .iter()
            .any(|t| t.name == "Greeter" && t.kind == "struct");
        assert!(
            has_greeter,
            "Greeter struct missing; types = {:?}",
            out.types.iter().map(|t| &t.name).collect::<Vec<_>>()
        );
        let has_hello = out
            .functions
            .iter()
            .any(|f| f.name == "hello");
        let has_main = out
            .functions
            .iter()
            .any(|f| f.name == "main");
        assert!(has_hello, "hello method missing");
        assert!(has_main, "main function missing");
    }

    #[test]
    fn parse_extracts_fields() {
        let out = parse_rust_source(SIMPLE_RUST, "main.rs").unwrap();
        let has_name = out
            .fields
            .iter()
            .any(|f| f.name == "name" && f.type_signature.contains("String"));
        assert!(
            has_name,
            "name field missing; fields = {:?}",
            out.fields
        );
    }

    #[test]
    fn parse_extracts_calls_and_resolves() {
        let out = parse_rust_source(SIMPLE_RUST, "main.rs").unwrap();
        // main calls g.hello → callee_name "hello"
        let calls_in_main: Vec<&CallEdge> = out
            .calls
            .iter()
            .filter(|c| c.caller_id.ends_with("@main.rs"))
            .collect();
        assert!(
            !calls_in_main.is_empty(),
            "no calls recorded; calls = {:?}",
            out.calls
        );
        let has_hello_call = out.calls.iter().any(|c| c.callee_name == "hello");
        assert!(has_hello_call, "hello call missing");
        // After resolve_calls, hello should have a callee_id since it's unique
        let hello_call = out
            .calls
            .iter()
            .find(|c| c.callee_name == "hello")
            .unwrap();
        assert!(
            hello_call.callee_id.is_some(),
            "hello call not resolved"
        );
    }

    #[test]
    fn parse_extracts_enum_and_trait() {
        let src = b"\
enum Color { Red, Green, Blue }

trait Drawable {
    fn draw(&self);
}
";
        let out = parse_rust_source(src, "types.rs").unwrap();
        let has_color = out.types.iter().any(|t| t.name == "Color" && t.kind == "enum");
        assert!(has_color, "Color enum missing");
        let has_drawable = out
            .types
            .iter()
            .any(|t| t.name == "Drawable" && t.kind == "interface");
        assert!(has_drawable, "Drawable trait missing");
    }

    #[test]
    fn parse_extracts_type_alias() {
        let src = b"type MyInt = u64;\n";
        let out = parse_rust_source(src, "alias.rs").unwrap();
        let has_alias = out.aliases.iter().any(|a| a.name == "MyInt");
        assert!(
            has_alias,
            "MyInt alias missing; aliases = {:?}",
            out.aliases
        );
    }

    #[test]
    fn parse_extracts_macros() {
        let out = parse_rust_source(SIMPLE_RUST, "main.rs").unwrap();
        // format! macro invocation should be collected
        assert!(
            out.macros.contains(&"format".to_string()),
            "format macro missing; macros = {:?}",
            out.macros
        );
    }

    #[test]
    fn parse_meta_is_rust_language() {
        let out = parse_rust_source(SIMPLE_RUST, "main.rs").unwrap();
        assert_eq!(out.parse_meta.parser_language, "rust_tree_sitter");
        assert_eq!(out.parse_meta.parser_language_initial, "rust");
    }

    #[test]
    fn parse_extracts_module() {
        let src = b"\
mod network {
    pub fn connect() {}
}
";
        let out = parse_rust_source(src, "mod.rs").unwrap();
        let has_ns = out
            .namespaces
            .iter()
            .any(|n| n.name == "network");
        assert!(
            has_ns,
            "network namespace missing; namespaces = {:?}",
            out.namespaces
        );
    }
}

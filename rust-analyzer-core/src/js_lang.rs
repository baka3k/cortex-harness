//! JavaScript tree-sitter walker — Phase 2 (Tier 2) port of `js_analyzer.py`.
//!
//! JavaScript is **Family B** — Namespace/Type model:
//! - 7-tuple parse output: `(functions, calls, types, namespaces, relations, file_def, parse_meta)`
//! - `FunctionDef` has `exported: bool` (no byte offsets, no visibility)
//! - `CallEdge` has `call_arity` (Family B marker)
//! - `FileDef` carries `imports`, `exports`, `jsx_tags`, `jsx_components` (JS-only)
//! - Single-stage call resolution at project level (no per-file resolver)
//!
//! Node sets (mirror Python `_TYPE_NODE_KINDS` / `_FUNCTION_NODE_KINDS`):
//! - Types: `class_declaration → class`
//! - Functions: `function_declaration → function`, `generator_function_declaration`,
//!   `method_definition → method`
//! - Variables: `lexical_declaration`, `variable_declaration` (when init is arrow/function)
//! - Calls: `call_expression`, `new_expression`
//! - Exports: `export_statement`, `export_default_declaration`

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{Grammar, JsGrammar};
use crate::symbols::{CallEdge, NamespaceDef, ParseMeta, RelationEdge, TypeDef};
use crate::text::{node_text, node_snippet};

// ── Node-type sets ──────────────────────────────────────────────────────

const COMMENT_TYPES: &[&str] = &["comment"];
const CLASS_NODE_KINDS: &[&str] = &["class_declaration"];
const FUNCTION_NODE_KINDS: &[&str] = &[
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
];
const VARIABLE_NODE_KINDS: &[&str] = &["lexical_declaration", "variable_declaration"];
const CALL_NODE_KINDS: &[&str] = &["call_expression", "new_expression"];
const EXPORT_NODE_KINDS: &[&str] = &["export_statement", "export_default_declaration"];

// ── JS-specific ParseOutput (Family B) ──────────────────────────────────

#[derive(Debug, Clone, Default)]
pub struct JsFunctionDef {
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
}

#[derive(Debug, Clone, Default)]
pub struct JsFileDef {
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

#[derive(Debug, Default)]
pub struct JsParseOutput {
    pub file_def: JsFileDef,
    pub functions: Vec<JsFunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
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

fn extract_name(node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source).to_string());
    }
    for child in node.children(&mut node.walk()) {
        if matches!(child.kind(), "identifier" | "property_identifier") {
            return Some(node_text(child, source).to_string());
        }
    }
    None
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
        .filter(|c| c.is_named() && !COMMENT_TYPES.contains(&c.kind()))
        .count() as u32
}

fn count_arguments(node: Node) -> u32 {
    let Some(args) = node.child_by_field_name("arguments") else {
        return 0;
    };
    args.children(&mut args.walk())
        .filter(|c| c.is_named() && !COMMENT_TYPES.contains(&c.kind()))
        .count() as u32
}

fn normalize_ws(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn collect_imports(root: Node, source: &[u8]) -> Vec<String> {
    let mut imports: Vec<String> = Vec::new();
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

fn collect_exports(root: Node, source: &[u8]) -> Vec<String> {
    let mut exports: Vec<String> = Vec::new();
    for kind in &["export_statement", "export_default_declaration"] {
        for node in find_nodes_by_type(root, kind) {
            let text = normalize_ws(&node_text(node, source));
            if !text.is_empty() {
                exports.push(text);
            }
        }
    }
    exports
}

fn jsx_name(node: Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source).to_string());
    }
    for child in node.children(&mut node.walk()) {
        if matches!(
            child.kind(),
            "jsx_identifier" | "jsx_member_expression" | "jsx_namespaced_name"
        ) {
            return Some(node_text(child, source).to_string());
        }
    }
    None
}

fn collect_jsx_tags(root: Node, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut tags: Vec<String> = Vec::new();
    let mut components: Vec<String> = Vec::new();
    let mut tag_seen: HashMap<String, ()> = HashMap::new();
    let mut comp_seen: HashMap<String, ()> = HashMap::new();
    for kind in &[
        "jsx_opening_element",
        "jsx_self_closing_element",
    ] {
        for node in find_nodes_by_type(root, kind) {
            let Some(name) = jsx_name(node, source) else {
                continue;
            };
            if name.is_empty() {
                continue;
            }
            if name.chars().next().map(|c| c.is_lowercase()).unwrap_or(false) {
                tag_seen.entry(name).or_insert(());
            } else {
                comp_seen.entry(name).or_insert(());
            }
        }
    }
    tags.extend(tag_seen.into_keys());
    tags.sort();
    components.extend(comp_seen.into_keys());
    components.sort();
    (tags, components)
}

fn scope_name_from_stacks(namespace_stack: &[String], type_stack: &[String]) -> Option<String> {
    let mut all: Vec<&str> = Vec::new();
    all.extend(namespace_stack.iter().map(|s| s.as_str()));
    all.extend(type_stack.iter().map(|s| s.as_str()));
    if all.is_empty() {
        None
    } else {
        Some(all.join("::"))
    }
}

fn extract_call_name(call_node: Node, source: &[u8]) -> Option<String> {
    if let Some(function_node) = call_node.child_by_field_name("function") {
        let text = node_text(function_node, source).to_string();
        if let Some(idx) = text.rfind('.') {
            return Some(text[idx + 1..].to_string());
        }
        return Some(text);
    }
    let text = node_text(call_node, source);
    Some(text.split('(').next().unwrap_or("").trim().to_string())
}

fn symbol_id(scope_name: Option<&str>, name: &str, arity: u32, rel_path: &str) -> String {
    let qualified = scope_name
        .map(|s| format!("{}::{}", s, name))
        .unwrap_or_else(|| name.to_string());
    format!("{}/{}@{}", qualified, arity, rel_path)
}

fn qualified_name_fn(scope_name: Option<&str>, name: &str) -> String {
    scope_name
        .map(|s| format!("{}::{}", s, name))
        .unwrap_or_else(|| name.to_string())
}

fn type_id(qualified: &str) -> String {
    qualified.to_string()
}

fn namespace_id(name: &str) -> String {
    format!("namespace::{}", name)
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

// ── Walker ──────────────────────────────────────────────────────────────

struct JsWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    functions: &'a mut Vec<JsFunctionDef>,
    calls: &'a mut Vec<CallEdge>,
    types: &'a mut Vec<TypeDef>,
    namespaces: &'a mut Vec<NamespaceDef>,
    relations: &'a mut Vec<RelationEdge>,
    type_registry: HashMap<String, TypeDef>,
}

fn walk<'a>(
    node: Node<'a>,
    namespace_stack: &[String],
    type_stack: &[String],
    exported: bool,
    ctx: &mut JsWalkCtx<'a>,
) {
    // ── Classes (type_declaration → class) ──
    if CLASS_NODE_KINDS.contains(&node.kind()) {
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Type", node));
        let mut scope = namespace_stack.to_vec();
        scope.extend(type_stack.iter().cloned());
        let qualified = scope_name_from_stacks(&scope, &[])
            .map(|s| format!("{}::{}", s, name))
            .unwrap_or_else(|| name.clone());
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let kind = "class";
        let type_def = TypeDef {
            symbol_id: type_id(&qualified),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: kind.to_string(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment: extract_leading_comment(node, ctx.source),
            ..Default::default()
        };
        if !ctx.type_registry.contains_key(&qualified) {
            ctx.type_registry.insert(qualified.clone(), type_def.clone());
            ctx.types.push(type_def);
        }
        let mut new_type_stack = type_stack.to_vec();
        new_type_stack.push(name.clone());
        for child in node.children(&mut node.walk()) {
            walk(child, namespace_stack, &new_type_stack, exported, ctx);
        }
        return;
    }

    // ── Functions / methods ──
    if FUNCTION_NODE_KINDS.contains(&node.kind()) {
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Function", node));
        let kind = match node.kind() {
            "method_definition" => {
                if name == "constructor" {
                    "constructor"
                } else {
                    "method"
                }
            }
            "generator_function_declaration" => "generator_function",
            _ => "function",
        };
        let scope = scope_name_from_stacks(namespace_stack, type_stack);
        let arity = count_parameters(node);
        let func_id = symbol_id(scope.as_deref(), &name, arity, ctx.rel_path);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        let func = JsFunctionDef {
            symbol_id: func_id.clone(),
            qualified_name: qualified_name_fn(scope.as_deref(), &name),
            name: name.clone(),
            kind: kind.to_string(),
            scope_name: scope.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            arity,
            code: snippet,
            comment: comment.clone(),
            summary: comment,
            note: String::new(),
            exported,
        };
        ctx.functions.push(func.clone());

        // Push relation
        if !type_stack.is_empty() {
            let owner = type_id(&scope_name_from_stacks(namespace_stack, type_stack).unwrap_or_default());
            ctx.relations.push(RelationEdge {
                source_id: owner,
                source_label: "Type".to_string(),
                target_id: func_id,
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        } else if !namespace_stack.is_empty() {
            let ns_id = namespace_id(&namespace_stack.join("::"));
            ctx.relations.push(RelationEdge {
                source_id: ns_id,
                source_label: "Namespace".to_string(),
                target_id: func_id,
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }

        // Walk body for calls
        for child in node.children(&mut node.walk()) {
            walk_calls(child, Some(&func), ctx);
        }
        return;
    }

    // ── Variable declarations with arrow/function init ──
    if VARIABLE_NODE_KINDS.contains(&node.kind()) {
        // Look for `name = arrow_function` or `name = function`
        for declarator in node.children(&mut node.walk()) {
            if !matches!(declarator.kind(), "variable_declarator" | "assignment_expression") {
                continue;
            }
            let name_node = declarator.child_by_field_name("name");
            let value_node = declarator.child_by_field_name("value");
            if let (Some(name_node), Some(value_node)) = (name_node, value_node) {
                if matches!(value_node.kind(), "arrow_function" | "function" | "function_expression") {
                    let name = node_text(name_node, ctx.source).to_string();
                    let kind = if value_node.kind() == "arrow_function" {
                        "arrow_function"
                    } else {
                        "function"
                    };
                    let scope = scope_name_from_stacks(namespace_stack, type_stack);
                    let arity = count_parameters(value_node);
                    let func_id = symbol_id(scope.as_deref(), &name, arity, ctx.rel_path);
                    let (snippet, start_line, end_line) = node_snippet(declarator, ctx.source);
                    let comment = extract_leading_comment(declarator, ctx.source);
                    let func = JsFunctionDef {
                        symbol_id: func_id.clone(),
                        qualified_name: qualified_name_fn(scope.as_deref(), &name),
                        name,
                        kind: kind.to_string(),
                        scope_name: scope,
                        file_path: ctx.rel_path.to_string(),
                        start_line,
                        end_line,
                        arity,
                        code: snippet,
                        comment: comment.clone(),
                        summary: comment,
                        note: String::new(),
                        exported,
                    };
                    ctx.functions.push(func.clone());
                    for child in value_node.children(&mut value_node.walk()) {
                        walk_calls(child, Some(&func), ctx);
                    }
                }
            }
        }
        // Continue descent
    }

    // ── Default: descend ──
    for child in node.children(&mut node.walk()) {
        walk(child, namespace_stack, type_stack, exported, ctx);
    }
}

fn walk_calls<'a>(node: Node<'a>, active: Option<&JsFunctionDef>, ctx: &mut JsWalkCtx<'a>) {
    if let Some(active) = active {
        if CALL_NODE_KINDS.contains(&node.kind()) {
            let callee_name = extract_call_name(node, ctx.source).unwrap_or_default();
            let call_type = if node.kind() == "new_expression" {
                "new_expression"
            } else {
                "call_expression"
            };
            ctx.calls.push(CallEdge {
                caller_id: active.symbol_id.clone(),
                caller_file: ctx.rel_path.to_string(),
                caller_scope: active.scope_name.clone(),
                call_line: node.start_position().row as u32 + 1,
                call_column: node.start_position().column as u32 + 1,
                call_start_byte: node.start_byte() as u32,
                call_branch_kind: "none".to_string(),
                call_loop_depth: 0,
                call_control_frames_json: "[]".to_string(),
                call_type: call_type.to_string(),
                call_arity: count_arguments(node),
                callee_name,
                callee_id: None,
            });
        }
    }
    for child in node.children(&mut node.walk()) {
        walk_calls(child, active, ctx);
    }
}

// ── Public entry point ──────────────────────────────────────────────────

/// Parse JavaScript source bytes and run the full extraction pipeline.
pub fn parse_js_source(source: &[u8], rel_path: &str) -> Option<JsParseOutput> {
    let mut parser = Parser::new();
    parser.set_language(&JsGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let imports = collect_imports(root, source);
    let exports = collect_exports(root, source);
    let (jsx_tags, jsx_components) = collect_jsx_tags(root, source);
    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let has_error = check_has_error(root);

    let mut functions: Vec<JsFunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut types: Vec<TypeDef> = Vec::new();
    let mut namespaces: Vec<NamespaceDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let type_registry: HashMap<String, TypeDef> = HashMap::new();

    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    {
        let mut ctx = JsWalkCtx {
            source,
            rel_path,
            functions: &mut functions,
            calls: &mut calls,
            types: &mut types,
            namespaces: &mut namespaces,
            relations: &mut relations,
            type_registry,
        };
        let namespace_stack: Vec<String> = Vec::new();
        let type_stack: Vec<String> = Vec::new();
        for child in root.children(&mut root.walk()) {
            // Detect exported flag at top level
            let is_export = EXPORT_NODE_KINDS.contains(&child.kind());
            walk(child, &namespace_stack, &type_stack, is_export, &mut ctx);
            // If this is an export of a function/variable, recurse with exported=true
            if is_export {
                for grandchild in child.children(&mut child.walk()) {
                    walk(
                        grandchild,
                        &namespace_stack,
                        &type_stack,
                        true,
                        &mut ctx,
                    );
                }
            }
        }
    }

    let file_def = JsFileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: file_lines,
        code,
        comment: file_comment.clone(),
        summary: file_comment,
        note: String::new(),
        imports,
        exports,
        jsx_tags,
        jsx_components,
    };

    Some(JsParseOutput {
        file_def,
        functions,
        calls,
        types,
        namespaces,
        relations,
        parse_meta: ParseMeta {
            parser_language: "javascript_tree_sitter".to_string(),
            parser_language_initial: "javascript".to_string(),
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

    const SIMPLE_JS: &[u8] = b"\
import React from 'react';
import { useState } from 'react';

export function greet(name) {
    return 'Hello, ' + name;
}

const helper = (x) => x * 2;

class Greeter {
    constructor(name) {
        this.name = name;
    }
    hi() {
        return greet(this.name);
    }
}

export default Greeter;
";

    #[test]
    fn parse_extracts_imports_and_exports() {
        let out = parse_js_source(SIMPLE_JS, "app.js").unwrap();
        assert!(out.file_def.imports.len() >= 2, "imports missing");
        assert!(out.file_def.exports.len() >= 2, "exports missing");
    }

    #[test]
    fn parse_extracts_function_and_arrow() {
        let out = parse_js_source(SIMPLE_JS, "app.js").unwrap();
        let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"greet"), "greet missing: {:?}", names);
        assert!(names.contains(&"helper"), "helper missing: {:?}", names);
    }

    #[test]
    fn parse_extracts_class_and_methods() {
        let out = parse_js_source(SIMPLE_JS, "app.js").unwrap();
        let has_class = out.types.iter().any(|t| t.name == "Greeter" && t.kind == "class");
        assert!(has_class, "Greeter class missing");
        let has_constructor = out
            .functions
            .iter()
            .any(|f| f.name == "constructor" && f.kind == "constructor");
        assert!(has_constructor, "constructor missing");
        let has_hi = out.functions.iter().any(|f| f.name == "hi");
        assert!(has_hi, "hi method missing");
    }

    #[test]
    fn parse_records_calls() {
        let out = parse_js_source(SIMPLE_JS, "app.js").unwrap();
        // `greet(this.name)` is a call inside `hi`
        let hi_calls: Vec<&CallEdge> =
            out.calls.iter().filter(|c| c.caller_id.contains("hi/")).collect();
        let callee_names: Vec<&str> = hi_calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(callee_names.contains(&"greet"), "greet call missing: {:?}", callee_names);
    }

    #[test]
    fn parse_meta_is_javascript_language() {
        let out = parse_js_source(SIMPLE_JS, "app.js").unwrap();
        assert_eq!(out.parse_meta.parser_language, "javascript_tree_sitter");
        assert_eq!(out.parse_meta.parser_language_initial, "javascript");
    }

    #[test]
    fn parse_extracts_jsx_tags() {
        let src = b"const x = <div className=\"a\"><span>hi</span></div>;";
        let out = parse_js_source(src, "x.js").unwrap();
        // JSX tags should be detected
        assert!(!out.file_def.jsx_tags.is_empty(), "jsx tags missing");
    }
}

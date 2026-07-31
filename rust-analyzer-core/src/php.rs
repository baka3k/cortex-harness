//! PHP tree-sitter walker — Phase 2 (Tier 2) port of `php_analyzer.py`.
//!
//! PHP is **Family B** (sibling to JS) — Namespace/Type model:
//! - 6-tuple parse output: `(functions, calls, types, namespaces, relations, file_def)`
//!   NO `parse_meta` dict (PHP is the only Family B variant that omits it in payload).
//! - `FunctionDef` has `exported: bool` (same as JS).
//! - `FileDef` carries `imports`, `exports`, `jsx_tags`, `jsx_components` (mostly stubs).
//! - `CallEdge` has `call_arity` (same as JS).
//!
//! Unique features in PHP:
//! - Anonymous functions: `arrow_function`, `anonymous_function` (closures)
//! - `skip_function_ranges` dedup logic
//! - `trait` kind is a recognized type
//! - 5 call kinds: `function_call_expression`, `method_call_expression`, `scoped_call_expression`,
//!   `call_expression`, `object_creation_expression`
//! - Imports also include `include_expression`, `require_expression`

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{Grammar, PhpGrammar};
use crate::symbols::{CallEdge, NamespaceDef, ParseMeta, RelationEdge, TypeDef};
use crate::text::{node_text, node_snippet};

// ── Node-type sets ──────────────────────────────────────────────────────

const COMMENT_TYPES: &[&str] = &["comment"];
const NAMESPACE_NODE_KINDS: &[&str] = &["namespace_definition", "namespace_declaration"];
const TYPE_NODE_KINDS: &[&str] = &[
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
];
const FUNCTION_NODE_KINDS: &[&str] = &["function_definition", "method_declaration"];
const ANON_FUNCTION_NODE_KINDS: &[&str] = &["arrow_function", "anonymous_function"];
const CALL_NODE_KINDS: &[&str] = &[
    "function_call_expression",
    "method_call_expression",
    "scoped_call_expression",
    "call_expression",
    "object_creation_expression",
];

// ── PHP-specific structures (Family B, 6-tuple) ────────────────────────

#[derive(Debug, Clone, Default)]
pub struct PhpFunctionDef {
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
pub struct PhpFileDef {
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
pub struct PhpParseOutput {
    pub file_def: PhpFileDef,
    pub functions: Vec<PhpFunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    // PHP does NOT have a separate parse_meta dictionary in its payload.
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
        if matches!(child.kind(), "name" | "identifier" | "variable_name" | "string") {
            let text = node_text(child, source).to_string();
            if !text.is_empty() {
                return Some(text.trim_matches('$').to_string());
            }
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
        .filter(|c| matches!(c.kind(), "simple_parameter" | "parameter" | "variadic_parameter"))
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
    for kind in &[
        "namespace_use_declaration",
        "include_expression",
        "require_expression",
        "include_once_expression",
        "require_once_expression",
    ] {
        for node in find_nodes_by_type(root, kind) {
            let text = normalize_ws(&node_text(node, source));
            if !text.is_empty() {
                imports.push(text);
            }
        }
    }
    imports
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

fn type_kind(node_type: &str) -> Option<&'static str> {
    match node_type {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "trait_declaration" => Some("trait"),
        "enum_declaration" => Some("enum"),
        _ => None,
    }
}

fn extract_call_name(call_node: Node, source: &[u8]) -> Option<String> {
    if let Some(function_node) = call_node.child_by_field_name("function") {
        let text = node_text(function_node, source).to_string();
        if let Some(idx) = text.rfind("::") {
            return Some(text[idx + 2..].to_string());
        }
        if let Some(idx) = text.rfind("->") {
            return Some(text[idx + 2..].to_string());
        }
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

struct PhpWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    functions: &'a mut Vec<PhpFunctionDef>,
    calls: &'a mut Vec<CallEdge>,
    types: &'a mut Vec<TypeDef>,
    namespaces: &'a mut Vec<NamespaceDef>,
    relations: &'a mut Vec<RelationEdge>,
    type_registry: HashMap<String, TypeDef>,
    namespace_registry: HashMap<String, NamespaceDef>,
    /// Ranges (start_byte, end_byte) for functions to skip (dedup anonymous->named mapping).
    skip_function_ranges: Vec<(usize, usize)>,
}

fn walk<'a>(
    node: Node<'a>,
    namespace_stack: &[String],
    type_stack: &[String],
    ctx: &mut PhpWalkCtx<'a>,
) {
    // ── Namespace ──
    if NAMESPACE_NODE_KINDS.contains(&node.kind()) {
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Namespace", node));
        let qualified = if namespace_stack.is_empty() {
            name.clone()
        } else {
            format!("{}::{}", namespace_stack.join("::"), name)
        };
        let ns_id = namespace_id(&qualified);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        let ns_def = NamespaceDef {
            symbol_id: ns_id.clone(),
            qualified_name: qualified.clone(),
            name: name.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment: comment.clone(),
            summary: comment,
            note: String::new(),
        };
        ctx.namespace_registry
            .insert(ns_id.clone(), ns_def.clone());
        ctx.namespaces.push(ns_def);
        if !namespace_stack.is_empty() {
            let parent = namespace_id(&namespace_stack.join("::"));
            ctx.relations.push(RelationEdge {
                source_id: parent,
                source_label: "Namespace".to_string(),
                target_id: ns_id,
                target_label: "Namespace".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }
        let mut new_ns = namespace_stack.to_vec();
        new_ns.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, &new_ns, type_stack, ctx);
        }
        return;
    }

    // ── Type ──
    if TYPE_NODE_KINDS.contains(&node.kind()) {
        let kind = type_kind(node.kind()).unwrap_or("type").to_string();
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Type", node));
        let mut scope = namespace_stack.to_vec();
        scope.extend(type_stack.iter().cloned());
        let qualified = if scope.is_empty() {
            name.clone()
        } else {
            format!("{}::{}", scope.join("::"), name)
        };
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        let type_def = TypeDef {
            symbol_id: type_id(&qualified),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: kind.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment,
            ..Default::default()
        };
        if !ctx.type_registry.contains_key(&qualified) {
            ctx.type_registry.insert(qualified.clone(), type_def.clone());
            ctx.types.push(type_def);
        }
        let mut new_type_stack = type_stack.to_vec();
        new_type_stack.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, namespace_stack, &new_type_stack, ctx);
        }
        return;
    }

    // ── Function / method ──
    if FUNCTION_NODE_KINDS.contains(&node.kind()) {
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Function", node));
        let kind = if node.kind() == "method_declaration" {
            "method"
        } else {
            "function"
        };
        let scope = scope_name_from_stacks(namespace_stack, type_stack);
        let arity = count_parameters(node);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        let func = PhpFunctionDef {
            symbol_id: symbol_id(scope.as_deref(), &name, arity, ctx.rel_path),
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
            exported: false,
        };
        ctx.functions.push(func.clone());

        // Add to skip ranges (don't double-count anonymous embedded inside)
        ctx.skip_function_ranges
            .push((node.start_byte(), node.end_byte()));

        // CONTAINS relation
        if !type_stack.is_empty() {
            let owner = type_id(
                &scope_name_from_stacks(namespace_stack, type_stack).unwrap_or_default(),
            );
            ctx.relations.push(RelationEdge {
                source_id: owner,
                source_label: "Type".to_string(),
                target_id: func.symbol_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        } else if !namespace_stack.is_empty() {
            let ns_id = namespace_id(&namespace_stack.join("::"));
            ctx.relations.push(RelationEdge {
                source_id: ns_id,
                source_label: "Namespace".to_string(),
                target_id: func.symbol_id.clone(),
                target_label: "Function".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }

        for child in node.children(&mut node.walk()) {
            walk_calls(child, Some(&func), ctx);
        }
        return;
    }

    // ── Anonymous function ──
    if ANON_FUNCTION_NODE_KINDS.contains(&node.kind()) {
        let range = (node.start_byte(), node.end_byte());
        if ctx.skip_function_ranges.contains(&range) {
            return;
        }
        let kind = if node.kind() == "arrow_function" {
            "arrow_function"
        } else {
            "anonymous_function"
        };
        let name = anonymous_name("Function", node);
        let scope = scope_name_from_stacks(namespace_stack, type_stack);
        let arity = count_parameters(node);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let func = PhpFunctionDef {
            symbol_id: symbol_id(scope.as_deref(), &name, arity, ctx.rel_path),
            qualified_name: qualified_name_fn(scope.as_deref(), &name),
            name,
            kind: kind.to_string(),
            scope_name: scope,
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            arity,
            code: snippet,
            comment: String::new(),
            summary: String::new(),
            note: String::new(),
            exported: false,
        };
        ctx.functions.push(func.clone());
        for child in node.children(&mut node.walk()) {
            walk_calls(child, Some(&func), ctx);
        }
        return;
    }

    // ── Default: descend ──
    for child in node.children(&mut node.walk()) {
        walk(child, namespace_stack, type_stack, ctx);
    }
}

fn walk_calls<'a>(node: Node<'a>, active: Option<&PhpFunctionDef>, ctx: &mut PhpWalkCtx<'a>) {
    if let Some(active) = active {
        if CALL_NODE_KINDS.contains(&node.kind()) {
            let callee_name = extract_call_name(node, ctx.source).unwrap_or_default();
            if callee_name.is_empty() {
                return;
            }
            let call_type = match node.kind() {
                "object_creation_expression" => "new_expression",
                "method_call_expression" => "method_call",
                "scoped_call_expression" => "qualified_call",
                "function_call_expression" => "function_call",
                _ => "call_expression",
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

pub fn parse_php_source(source: &[u8], rel_path: &str) -> Option<PhpParseOutput> {
    let mut parser = Parser::new();
    parser.set_language(&PhpGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let imports = collect_imports(root, source);
    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    let mut functions: Vec<PhpFunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut types: Vec<TypeDef> = Vec::new();
    let mut namespaces: Vec<NamespaceDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let type_registry: HashMap<String, TypeDef> = HashMap::new();
    let namespace_registry: HashMap<String, NamespaceDef> = HashMap::new();

    {
        let mut ctx = PhpWalkCtx {
            source,
            rel_path,
            functions: &mut functions,
            calls: &mut calls,
            types: &mut types,
            namespaces: &mut namespaces,
            relations: &mut relations,
            type_registry,
            namespace_registry,
            skip_function_ranges: Vec::new(),
        };
        let namespace_stack: Vec<String> = Vec::new();
        let type_stack: Vec<String> = Vec::new();
        for child in root.children(&mut root.walk()) {
            walk(child, &namespace_stack, &type_stack, &mut ctx);
        }
    }

    let file_def = PhpFileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: file_lines,
        code,
        comment: file_comment.clone(),
        summary: file_comment,
        note: String::new(),
        imports,
        exports: Vec::new(),
        jsx_tags: Vec::new(),
        jsx_components: Vec::new(),
    };

    Some(PhpParseOutput {
        file_def,
        functions,
        calls,
        types,
        namespaces,
        relations,
    })
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const SIMPLE_PHP: &[u8] = b"\
<?php
namespace MyApp;

use MyApp\\Utils\\Helper;

class Greeter {
    private string $name;

    public function __construct(string $name) {
        $this->name = $name;
    }

    public function greet(string $who): string {
        return Helper::format($this->name . ' ' . $who);
    }
}

trait Loggable {
    public function log(string $msg): void {
        Helper::log($msg);
    }
}

function helper_func(int $x): int {
    return $x * 2;
}

$arrow = fn($x) => $x + 1;
$greet = function($name) { return \"Hello, $name\"; };
";

    #[test]
    fn parse_extracts_namespace() {
        let out = parse_php_source(SIMPLE_PHP, "app.php").unwrap();
        assert!(out.namespaces.iter().any(|n| n.name == "MyApp"));
    }

    #[test]
    fn parse_extracts_class_and_trait() {
        let out = parse_php_source(SIMPLE_PHP, "app.php").unwrap();
        let has_class = out.types.iter().any(|t| t.name == "Greeter" && t.kind == "class");
        let has_trait = out.types.iter().any(|t| t.name == "Loggable" && t.kind == "trait");
        assert!(has_class, "Greeter class missing");
        assert!(has_trait, "Loggable trait missing");
    }

    #[test]
    fn parse_extracts_methods_and_function() {
        let out = parse_php_source(SIMPLE_PHP, "app.php").unwrap();
        let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        // Should include __construct, greet, log, helper_func, Arrow, Anonymous
        let has_construct = names.contains(&"__construct");
        let has_greet = names.contains(&"greet");
        let has_helper = names.contains(&"helper_func");
        let has_log = names.contains(&"log");
        assert!(has_construct, "constructor missing: {:?}", names);
        assert!(has_greet, "greet method missing: {:?}", names);
        assert!(has_helper, "helper_func missing: {:?}", names);
        assert!(has_log, "log method missing: {:?}", names);
    }

    #[test]
    fn parse_extracts_anonymous_functions() {
        let out = parse_php_source(SIMPLE_PHP, "app.php").unwrap();
        let has_arrow = out
            .functions
            .iter()
            .any(|f| f.kind == "arrow_function");
        let has_anon = out
            .functions
            .iter()
            .any(|f| f.kind == "anonymous_function");
        assert!(has_arrow, "arrow function missing");
        assert!(has_anon, "anonymous function missing");
    }

    #[test]
    fn parse_records_calls() {
        let out = parse_php_source(SIMPLE_PHP, "app.php").unwrap();
        // Helper::format(...) is a scoped call inside greet
        let greet_calls: Vec<&CallEdge> = out
            .calls
            .iter()
            .filter(|c| c.caller_id.contains("greet"))
            .collect();
        let callee_names: Vec<&str> = greet_calls.iter().map(|c| c.callee_name.as_str()).collect();
        // Scoped call `Helper::format(...)` is recorded as `Helper::format` (qualified).
        let has_format = callee_names
            .iter()
            .any(|n| n.contains("format"));
        assert!(has_format, "format call missing: {:?}", callee_names);
    }

    #[test]
    fn parse_collects_imports() {
        let out = parse_php_source(SIMPLE_PHP, "app.php").unwrap();
        let joined = out.file_def.imports.join(" ");
        assert!(joined.contains("MyApp\\Utils\\Helper"), "use import missing: {:?}", out.file_def.imports);
    }
}

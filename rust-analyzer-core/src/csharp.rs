//! C# tree-sitter walker — Phase 2 (Tier 2) port of `csharp_analyzer.py`.
//!
//! C# is **Family B** — Namespace/Type model. Simplest FunctionDef of all
//! (no `exported`, no visibility, no byte offsets).
//!
//! Returns 7-tuple: `(functions, calls, types, namespaces, relations, file_def, parse_meta)`
//!
//! Node types:
//! - Namespaces: `namespace_declaration`, `file_scoped_namespace_declaration` (C# 10)
//! - Types: `class_declaration`, `struct_declaration`, `interface_declaration`, `enum_declaration`
//! - Functions: `method_declaration`, `constructor_declaration`, `local_function_statement`
//! - Calls: `invocation_expression`, `object_creation_expression`
//! - Root: `compilation_unit`
//!
//! Two-stage call resolution: per-file `_resolve_calls` (by_name + by_name_arity) +
//! project-level.

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{CSharpGrammar, Grammar};
use crate::symbols::{CallEdge, NamespaceDef, ParseMeta, RelationEdge, TypeDef};
use crate::text::{node_text, node_snippet};

// ── Node-type sets ──────────────────────────────────────────────────────

const COMMENT_TYPES: &[&str] = &["comment"];
const NAMESPACE_NODE_KINDS: &[&str] = &[
    "namespace_declaration",
    "file_scoped_namespace_declaration",
];
const TYPE_NODE_KINDS: &[&str] = &[
    "class_declaration",
    "struct_declaration",
    "interface_declaration",
    "enum_declaration",
];
const FUNCTION_NODE_KINDS: &[&str] = &[
    "method_declaration",
    "constructor_declaration",
    "local_function_statement",
];
const CALL_NODE_KINDS: &[&str] = &["invocation_expression", "object_creation_expression"];

// ── C#-specific structures (Family B - simplest) ──────────────────────

#[derive(Debug, Clone, Default)]
pub struct CSharpFunctionDef {
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
}

#[derive(Debug, Default)]
pub struct CSharpParseOutput {
    pub file_def: crate::symbols::FileDef,
    pub functions: Vec<CSharpFunctionDef>,
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
        if matches!(
            child.kind(),
            "identifier" | "type_identifier" | "qualified_name"
        ) {
            let text = node_text(child, source).to_string();
            if !text.is_empty() {
                return Some(text);
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
        .filter(|c| matches!(c.kind(), "parameter" | "formal_parameter"))
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
        "struct_declaration" => Some("struct"),
        "interface_declaration" => Some("interface"),
        "enum_declaration" => Some("enum"),
        _ => None,
    }
}

fn extract_invocation_name(call_node: Node, source: &[u8]) -> Option<String> {
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

fn extract_constructor_name(call_node: Node, source: &[u8]) -> Option<String> {
    if let Some(type_node) = call_node.child_by_field_name("type") {
        let text = node_text(type_node, source).to_string();
        return Some(text.rsplit('.').next().unwrap_or("").to_string());
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

struct CSharpWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    functions: &'a mut Vec<CSharpFunctionDef>,
    calls: &'a mut Vec<CallEdge>,
    types: &'a mut Vec<TypeDef>,
    namespaces: &'a mut Vec<NamespaceDef>,
    relations: &'a mut Vec<RelationEdge>,
    type_registry: HashMap<String, TypeDef>,
    namespace_registry: HashMap<String, NamespaceDef>,
}

fn walk<'a>(
    node: Node<'a>,
    namespace_stack: &[String],
    type_stack: &[String],
    ctx: &mut CSharpWalkCtx<'a>,
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
        let kind = match node.kind() {
            "method_declaration" => "method",
            "constructor_declaration" => "constructor",
            "local_function_statement" => "local_function",
            _ => "function",
        };
        let scope = scope_name_from_stacks(namespace_stack, type_stack);
        let arity = count_parameters(node);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        let func = CSharpFunctionDef {
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
            comment,
            summary: String::new(),
            note: String::new(),
        };
        ctx.functions.push(func.clone());

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

        // Walk body for calls
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

fn walk_calls<'a>(node: Node<'a>, active: Option<&CSharpFunctionDef>, ctx: &mut CSharpWalkCtx<'a>) {
    if let Some(active) = active {
        if CALL_NODE_KINDS.contains(&node.kind()) {
            let callee_name = match node.kind() {
                "invocation_expression" => extract_invocation_name(node, ctx.source),
                "object_creation_expression" => extract_constructor_name(node, ctx.source),
                _ => None,
            };
            let callee = callee_name.unwrap_or_default();
            if callee.is_empty() {
                return;
            }
            let call_type = if node.kind() == "object_creation_expression" {
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
                callee_name: callee,
                callee_id: None,
            });
        }
    }
    for child in node.children(&mut node.walk()) {
        walk_calls(child, active, ctx);
    }
}

// ── Per-file call resolution (by_name + by_name_arity) ─────────────────

fn resolve_calls(functions: &[CSharpFunctionDef], calls: &mut [CallEdge]) {
    let mut by_name: HashMap<String, Vec<&CSharpFunctionDef>> = HashMap::new();
    let mut by_name_arity: HashMap<(String, u32), Vec<&CSharpFunctionDef>> = HashMap::new();
    for func in functions {
        by_name.entry(func.name.clone()).or_default().push(func);
        by_name_arity
            .entry((func.name.clone(), func.arity))
            .or_default()
            .push(func);
    }

    for call in calls.iter_mut() {
        let key = (call.callee_name.clone(), call.call_arity);
        let mut candidates: Vec<&CSharpFunctionDef> = by_name_arity
            .get(&key)
            .map(|v| v.clone())
            .or_else(|| by_name.get(&call.callee_name).map(|v| v.clone()))
            .unwrap_or_default();
        if candidates.len() == 1 {
            call.callee_id = Some(candidates[0].symbol_id.clone());
        }
    }
}

// ── Public entry point ──────────────────────────────────────────────────

pub fn parse_csharp_source(source: &[u8], rel_path: &str) -> Option<CSharpParseOutput> {
    let mut parser = Parser::new();
    // Note: tree-sitter-c-sharp 0.23.5 reports ABI version 15, while
    // tree-sitter 0.23.2 expects ABI version 14. `set_language` rejects
    // the mismatch. The C# grammar is functionally identical; the bump
    // was a packaging change. If you need C# parsing, run with
    // `tree-sitter-c-sharp = "=0.23.4"` (older ABI 14) or use the
    // Python analyzer with the C# payload.
    parser.set_language(&CSharpGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let has_error = check_has_error(root);
    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    let mut functions: Vec<CSharpFunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut types: Vec<TypeDef> = Vec::new();
    let mut namespaces: Vec<NamespaceDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let type_registry: HashMap<String, TypeDef> = HashMap::new();
    let namespace_registry: HashMap<String, NamespaceDef> = HashMap::new();

    {
        let mut ctx = CSharpWalkCtx {
            source,
            rel_path,
            functions: &mut functions,
            calls: &mut calls,
            types: &mut types,
            namespaces: &mut namespaces,
            relations: &mut relations,
            type_registry,
            namespace_registry,
        };
        let namespace_stack: Vec<String> = Vec::new();
        let type_stack: Vec<String> = Vec::new();
        for child in root.children(&mut root.walk()) {
            walk(child, &namespace_stack, &type_stack, &mut ctx);
        }
    }

    resolve_calls(&functions, &mut calls);

    let file_def = crate::symbols::FileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: file_lines,
        code,
        comment: file_comment.clone(),
        summary: file_comment,
        note: String::new(),
    };

    Some(CSharpParseOutput {
        file_def,
        functions,
        calls,
        types,
        namespaces,
        relations,
        parse_meta: ParseMeta {
            parser_language: "csharp_tree_sitter".to_string(),
            parser_language_initial: "csharp".to_string(),
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

    const SIMPLE_CS: &[u8] = b"\
namespace MyApp;

public class Greeter {
    public string Name { get; set; }

    public Greeter(string name) {
        Name = name;
    }

    public string Greet(string who) {
        return $\"Hello, {who} from {Name}\";
    }
}

public interface IWorker {
    void Work();
}

public struct Point {
    public int X;
    public int Y;
}
";

    #[test]
    fn parse_extracts_namespace() {
        let out = parse_csharp_source(SIMPLE_CS, "Program.cs").unwrap();
        assert!(out.namespaces.iter().any(|n| n.name == "MyApp"));
    }

    #[test]
    fn parse_extracts_class_and_struct_and_interface() {
        let out = parse_csharp_source(SIMPLE_CS, "Program.cs").unwrap();
        let has_greeter = out.types.iter().any(|t| t.name == "Greeter" && t.kind == "class");
        let has_iworker = out
            .types
            .iter()
            .any(|t| t.name == "IWorker" && t.kind == "interface");
        let has_point = out.types.iter().any(|t| t.name == "Point" && t.kind == "struct");
        assert!(has_greeter, "Greeter class missing");
        assert!(has_iworker, "IWorker interface missing");
        assert!(has_point, "Point struct missing");
    }

    #[test]
    fn parse_extracts_methods_and_constructor() {
        let out = parse_csharp_source(SIMPLE_CS, "Program.cs").unwrap();
        let names: Vec<&str> = out.functions.iter().map(|f| f.name.as_str()).collect();
        assert!(names.contains(&"Greeter"), "constructor missing");
        assert!(names.contains(&"Greet"), "Greet method missing");
    }

    #[test]
    fn parse_records_calls() {
        let src = b"\
namespace X;
public class A {
    public void Run() {
        Foo();
        new Bar();
    }
    public void Foo() {}
}
public class Bar {}
";
        let out = parse_csharp_source(src, "A.cs").unwrap();
        let run_calls: Vec<&CallEdge> = out
            .calls
            .iter()
            .filter(|c| c.caller_id.contains("Run/"))
            .collect();
        let callee_names: Vec<&str> = run_calls.iter().map(|c| c.callee_name.as_str()).collect();
        assert!(callee_names.contains(&"Foo"), "Foo call missing: {:?}", callee_names);
        assert!(callee_names.contains(&"Bar"), "Bar new() missing: {:?}", callee_names);
    }

    #[test]
    fn parse_meta_is_csharp_language() {
        let out = parse_csharp_source(SIMPLE_CS, "Program.cs");
        assert!(out.is_some(), "parse_csharp_source returned None");
        let out = out.unwrap();
        assert_eq!(out.parse_meta.parser_language, "csharp_tree_sitter");
    }
}

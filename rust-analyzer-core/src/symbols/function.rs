//! Function extraction — port of the `function_definition` and
//! `function_declaration` branches of `_walk_tree`.

use tree_sitter::Node;

use crate::symbols::{
    declarator_arity, extract_declarator_scope, extract_function_name, function_symbol_id,
    namespace_id, qualified_name, scope_from_stacks, type_id, AliasDef, FieldDef, FunctionDef,
    RelationEdge,
};
use crate::text::{extract_leading_comment, node_snippet};
use crate::walker::WalkContext;

pub fn extract_function_definition(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let mut scope = scope_from_stacks(namespace_stack, type_stack);
    let declarator = node.child_by_field_name("declarator");
    if scope.is_none() {
        scope = extract_declarator_scope(declarator, ctx.source);
    }
    let Some(name) = extract_function_name(declarator, ctx.source) else {
        return;
    };
    let arity = declarator_arity(declarator);
    let symbol_id = function_symbol_id(scope.as_deref(), &name, arity, ctx.rel_path);
    let qualified = qualified_name(scope.as_deref(), &name);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let comment = extract_leading_comment(node, ctx.source);
    let summary = comment.clone();
    let note = String::new();
    let kind = if name.starts_with('~') {
        "destructor"
    } else if !type_stack.is_empty() && name == type_stack[type_stack.len() - 1] {
        "constructor"
    } else {
        "function"
    };

    ctx.functions.push(FunctionDef {
        symbol_id: symbol_id.clone(),
        qualified_name: qualified,
        name: name.clone(),
        kind: kind.to_string(),
        scope_name: scope.clone(),
        file_path: ctx.rel_path.to_string(),
        start_byte: node.start_byte() as u32,
        end_byte: node.end_byte() as u32,
        start_line,
        end_line,
        arity,
        code: snippet,
        comment,
        summary,
        note,
    });

    if !namespace_stack.is_empty() {
        let ns_id = namespace_id(&namespace_stack.join("::"));
        ctx.relations.push(RelationEdge {
            source_id: ns_id,
            source_label: "Namespace".to_string(),
            target_id: symbol_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }

    let declaring_type_id: Option<String> = if !type_stack.is_empty() {
        Some(type_id(
            None,
            &scope_from_stacks(namespace_stack, type_stack).unwrap_or_default(),
        ))
    } else if let Some(s) = scope.as_ref() {
        let candidate = type_id(None, s);
        if ctx.type_registry.contains_key(&candidate) {
            Some(candidate)
        } else {
            None
        }
    } else {
        None
    };
    if let Some(dt_id) = declaring_type_id {
        ctx.relations.push(RelationEdge {
            source_id: dt_id,
            source_label: "Type".to_string(),
            target_id: symbol_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "DECLARES".to_string(),
            properties: Default::default(),
        });
    }

    crate::calls::extract_calls_in_node(ctx, node, &symbol_id, scope.as_deref());
}

pub fn extract_function_declaration(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let mut scope = scope_from_stacks(namespace_stack, type_stack);
    let declarator = node.child_by_field_name("declarator");
    if scope.is_none() {
        scope = extract_declarator_scope(declarator, ctx.source);
    }
    let Some(name) = extract_function_name(declarator, ctx.source) else {
        return;
    };
    let arity = declarator_arity(declarator);
    let symbol_id = function_symbol_id(scope.as_deref(), &name, arity, ctx.rel_path);
    let qualified = qualified_name(scope.as_deref(), &name);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let comment = extract_leading_comment(node, ctx.source);
    let summary = comment.clone();

    ctx.functions.push(FunctionDef {
        symbol_id,
        qualified_name: qualified,
        name,
        kind: "declaration".to_string(),
        scope_name: scope.clone(),
        file_path: ctx.rel_path.to_string(),
        start_byte: node.start_byte() as u32,
        end_byte: node.end_byte() as u32,
        start_line,
        end_line,
        arity,
        code: snippet,
        comment,
        summary,
        note: String::new(),
    });
}

pub fn method_declaration_for_field(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let declarator = node.child_by_field_name("declarator");
    let Some(name) = extract_function_name(declarator, ctx.source) else {
        return;
    };
    let arity = declarator_arity(declarator);
    let symbol_id = function_symbol_id(scope.as_deref(), &name, arity, ctx.rel_path);
    let qualified = qualified_name(scope.as_deref(), &name);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);

    ctx.functions.push(FunctionDef {
        symbol_id,
        qualified_name: qualified,
        name,
        kind: "declaration".to_string(),
        scope_name: scope.clone(),
        file_path: ctx.rel_path.to_string(),
        start_byte: node.start_byte() as u32,
        end_byte: node.end_byte() as u32,
        start_line,
        end_line,
        arity,
        code: snippet,
        comment: String::new(),
        summary: String::new(),
        note: String::new(),
    });
}

pub fn declaration_function(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
    has_extern: bool,
) {
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let mut cursor = node.walk();
    let mut seen: std::collections::HashSet<(String, u32)> = Default::default();
    for child in node.children(&mut cursor) {
        if !is_declarator_kind(child.kind()) {
            continue;
        }
        let Some(name) = extract_function_name(Some(child), ctx.source) else {
            continue;
        };
        let arity = declarator_arity(Some(child));
        let key = (name.clone(), arity);
        if !seen.insert(key) {
            continue;
        }
        let symbol_id = function_symbol_id(scope.as_deref(), &name, arity, ctx.rel_path);
        let qualified = qualified_name(scope.as_deref(), &name);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
        let comment = extract_leading_comment(node, ctx.source);
        ctx.functions.push(FunctionDef {
            symbol_id,
            qualified_name: qualified,
            name,
            kind: if has_extern {
                "extern_declaration".to_string()
            } else {
                "declaration".to_string()
            },
            scope_name: scope.clone(),
            file_path: ctx.rel_path.to_string(),
            start_byte: node.start_byte() as u32,
            end_byte: node.end_byte() as u32,
            start_line,
            end_line,
            arity,
            code: snippet,
            comment,
            summary: String::new(),
            note: String::new(),
        });
    }
    // Mark unused import suppressed.
    let _ = AliasDef::default;
    let _ = FieldDef::default;
}

fn is_declarator_kind(kind: &str) -> bool {
    matches!(
        kind,
        "function_declarator"
            | "pointer_declarator"
            | "array_declarator"
            | "reference_declarator"
            | "init_declarator"
            | "declarator"
    )
}

//! Type (class/struct/union/enum) extraction.

use tree_sitter::Node;

use crate::symbols::{
    anonymous_name, extract_base_types, namespace_id, scope_from_stacks, type_id, FunctionDef,
    RelationEdge, TypeDef,
};
use crate::text::{extract_leading_comment, node_snippet};
use crate::walker::WalkContext;

const KIND_MAP: &[(&str, &str)] = &[
    ("class_specifier", "class"),
    ("struct_specifier", "struct"),
    ("union_specifier", "union"),
    ("enum_specifier", "enum"),
];

pub fn extract_type(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let kind_raw = match KIND_MAP.iter().find(|(k, _)| *k == node.kind()) {
        Some((_, v)) => *v,
        None => return,
    };

    let mut name = crate::symbols::first_identifier_node(node, ctx.source).unwrap_or_default();
    let mut kind = kind_raw.to_string();
    if name.is_empty() {
        name = anonymous_name(&kind_raw.chars().next().unwrap().to_uppercase().collect::<String>(), node);
        kind = format!("anonymous_{}", kind_raw);
    }

    let qualified = if !namespace_stack.is_empty() || !type_stack.is_empty() {
        let mut parts: Vec<&str> = Vec::new();
        parts.extend(namespace_stack.iter().map(|s| s.as_str()));
        parts.extend(type_stack.iter().map(|s| s.as_str()));
        parts.push(&name);
        parts.join("::")
    } else {
        name.clone()
    };

    let tid = type_id(None, &qualified);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let comment = extract_leading_comment(node, ctx.source);

    ctx.types.push(TypeDef {
        symbol_id: tid.clone(),
        qualified_name: qualified.clone(),
        name: qualified.split("::").last().unwrap_or(&name).to_string(),
        kind,
        file_path: ctx.rel_path.to_string(),
        start_line,
        end_line,
        code: snippet,
        comment: comment.clone(),
        summary: comment,
        note: String::new(),
    });

    if let Some(td) = ctx.types.last() {
        ctx.type_registry.insert(tid.clone(), td.clone());
    }

    if !namespace_stack.is_empty() {
        let ns_id = namespace_id(&namespace_stack.join("::"));
        ctx.relations.push(RelationEdge {
            source_id: ns_id,
            source_label: "Namespace".to_string(),
            target_id: tid.clone(),
            target_label: "Type".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }
    if !type_stack.is_empty() {
        let parent_type = type_id(None, &scope_from_stacks(namespace_stack, type_stack).unwrap_or_default());
        ctx.relations.push(RelationEdge {
            source_id: parent_type,
            source_label: "Type".to_string(),
            target_id: tid.clone(),
            target_label: "Type".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }

    if matches!(kind_raw, "class" | "struct") {
        for base in extract_base_types(node, ctx.source) {
            let base_id = type_id(None, &base);
            if !ctx.type_registry.contains_key(&base_id) {
                ctx.types.push(TypeDef {
                    symbol_id: base_id.clone(),
                    qualified_name: base.clone(),
                    name: base.split("::").last().unwrap_or(&base).to_string(),
                    kind: "external".to_string(),
                    file_path: ctx.rel_path.to_string(),
                    start_line: 0,
                    end_line: 0,
                    code: base.clone(),
                    comment: String::new(),
                    summary: String::new(),
                    note: String::new(),
                });
                if let Some(t) = ctx.types.last() {
                    ctx.type_registry.insert(base_id.clone(), t.clone());
                }
            }
            ctx.relations.push(RelationEdge {
                source_id: tid.clone(),
                source_label: "Type".to_string(),
                target_id: base_id,
                target_label: "Type".to_string(),
                rel_type: "EXTENDS".to_string(),
                properties: Default::default(),
            });
        }
    }

    let _ = FunctionDef::default;
}

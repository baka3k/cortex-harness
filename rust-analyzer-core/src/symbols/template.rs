//! Template declaration extraction.

use tree_sitter::Node;

use crate::symbols::{
    anonymous_name, extract_function_name, first_identifier_node, function_symbol_id, scope_from_stacks,
    template_id, type_id, RelationEdge, TemplateDef,
};
use crate::text::node_snippet;
use crate::walker::WalkContext;

const TEMPLATE_CHILD_TYPES: &[&str] = &[
    "class_specifier",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "function_definition",
    "function_declaration",
];

pub fn extract_template(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) -> Option<Vec<String>> {
    let name = anonymous_name("Template", node);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let template_id_str = template_id(ctx.rel_path, start_line, end_line);

    ctx.templates.push(TemplateDef {
        symbol_id: template_id_str.clone(),
        name,
        file_path: ctx.rel_path.to_string(),
        start_line,
        end_line,
        code: snippet,
    });

    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if !TEMPLATE_CHILD_TYPES.contains(&child.kind()) {
            continue;
        }
        let mut target_id: Option<String> = None;
        let mut target_label: Option<String> = None;

        if matches!(child.kind(), "function_definition" | "function_declaration") {
            let declarator = child.child_by_field_name("declarator");
            let Some(target_name) = extract_function_name(declarator, ctx.source) else {
                continue;
            };
            let scope = scope_from_stacks(namespace_stack, type_stack);
            let arity = crate::symbols::declarator_arity(declarator);
            target_id = Some(function_symbol_id(
                scope.as_deref(),
                &target_name,
                arity,
                ctx.rel_path,
            ));
            target_label = Some("Function".to_string());
        } else {
            let tname = first_identifier_node(child, ctx.source)
                .unwrap_or_else(|| anonymous_name("Type", child));
            let mut parts: Vec<&str> = Vec::new();
            parts.extend(namespace_stack.iter().map(|s| s.as_str()));
            parts.extend(type_stack.iter().map(|s| s.as_str()));
            parts.push(&tname);
            let qn = parts.join("::");
            target_id = Some(type_id(None, &qn));
            target_label = Some("Type".to_string());
        }

        if let (Some(tid), Some(label)) = (target_id, target_label) {
            ctx.relations.push(RelationEdge {
                source_id: template_id_str.clone(),
                source_label: "Template".to_string(),
                target_id: tid,
                target_label: label,
                rel_type: "TEMPLATES".to_string(),
                properties: Default::default(),
            });
        }
        // Only the first matching child triggers TEMPLATES edge.
        break;
    }
    None
}

//! Field extraction — `field_declaration` and `declaration` non-function branches.

use tree_sitter::Node;

use crate::symbols::{
    field_id, namespace_id, qualified_name, scope_from_stacks, type_id, FieldDef, RelationEdge,
};
use crate::text::{extract_leading_comment, node_snippet, node_text, normalize_type_signature};
use crate::walker::WalkContext;

/// Pull every declarator out of a field_declaration, yielding each as a field.
/// Mirrors `_iter_field_declarators` + the data-only branch.
pub fn extract_field_declaration(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let type_node = node.child_by_field_name("type");
    let type_text = type_node
        .map(|n| node_text(n, ctx.source).trim().to_string())
        .unwrap_or_default();
    let comment = extract_leading_comment(node, ctx.source);

    let declarators = collect_field_declarators(node);
    if declarators.is_empty() {
        return;
    }

    let mut seen: std::collections::HashSet<String> = Default::default();
    for declarator in declarators {
        let field_name = field_name_from_declarator(declarator, ctx.source)
            .or_else(|| crate::symbols::first_identifier_node(declarator, ctx.source));
        let Some(name) = field_name else {
            continue;
        };
        if !seen.insert(name.clone()) {
            continue;
        }
        let decl_text = node_text(declarator, ctx.source).trim().to_string();
        let mut sig = normalize_type_signature(&format!("{} {}", type_text, decl_text).trim().to_string());
        if sig.is_empty() {
            sig = normalize_type_signature(&node_text(node, ctx.source).to_string());
        }
        let fid = field_id(scope.as_deref(), &name, ctx.rel_path);
        let qn = qualified_name(scope.as_deref(), &name);
        ctx.fields.push(FieldDef {
            symbol_id: fid.clone(),
            qualified_name: qn,
            name: name.clone(),
            scope_name: scope.clone(),
            type_signature: sig.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet.clone(),
        });
        if !type_stack.is_empty() {
            let tid = type_id(None, &scope_from_stacks(namespace_stack, type_stack).unwrap_or_default());
            ctx.relations.push(RelationEdge {
                source_id: tid,
                source_label: "Type".to_string(),
                target_id: fid,
                target_label: "Field".to_string(),
                rel_type: "DECLARES".to_string(),
                properties: Default::default(),
            });
        }
        let _ = comment.clone();
    }
}

fn collect_field_declarators(node: Node) -> Vec<Node> {
    let mut out = Vec::new();
    let mut seen_ids: std::collections::HashSet<(usize, usize, String)> = Default::default();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if !child.is_named() {
            continue;
        }
        match child.kind() {
            "field_declarator" | "init_declarator" => {
                let key = (
                    child.start_byte(),
                    child.end_byte(),
                    node_text(child, &[] as &[u8]).to_string(),
                );
                if seen_ids.insert(key) {
                    out.push(child);
                }
            }
            _ => {}
        }
    }
    out
}

pub fn field_name_from_declarator(node: Node, source: &[u8]) -> Option<String> {
    if let Some(d) = node.child_by_field_name("declarator") {
        return crate::symbols::first_identifier_node(d, source);
    }
    if matches!(
        node.kind(),
        "field_declarator" | "init_declarator" | "declarator"
    ) {
        return crate::symbols::first_identifier_node(node, source);
    }
    None
}

/// Pull a non-function declarator out of a top-level `declaration`.
pub fn extract_declaration_variable(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
    decl_type_text: &str,
    has_extern: bool,
) {
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let comment = extract_leading_comment(node, ctx.source);

    let mut seen: std::collections::HashSet<String> = Default::default();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if !is_declarator_kind(child.kind()) {
            continue;
        }
        let name = field_name_from_declarator(child, ctx.source)
            .or_else(|| crate::symbols::first_identifier_node(child, ctx.source));
        let Some(name) = name else { continue };
        if !seen.insert(name.clone()) {
            continue;
        }
        let decl_text = node_text(child, ctx.source).trim().to_string();
        let mut sig = normalize_type_signature(&format!("{} {}", decl_type_text, decl_text).trim().to_string());
        if sig.is_empty() {
            sig = normalize_type_signature(&node_text(node, ctx.source).to_string());
        }
        let fid = field_id(scope.as_deref(), &name, ctx.rel_path);
        let qn = qualified_name(scope.as_deref(), &name);
        ctx.fields.push(FieldDef {
            symbol_id: fid.clone(),
            qualified_name: qn,
            name,
            scope_name: scope.clone(),
            type_signature: sig,
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet.clone(),
        });
        if !namespace_stack.is_empty() {
            let ns_id = namespace_id(&namespace_stack.join("::"));
            ctx.relations.push(RelationEdge {
                source_id: ns_id,
                source_label: "Namespace".to_string(),
                target_id: fid,
                target_label: "Field".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: if has_extern {
                    [("storage".to_string(), "extern".to_string())]
                        .into_iter()
                        .collect()
                } else {
                    Default::default()
                },
            });
        }
        let _ = comment.clone();
    }
}

fn is_declarator_kind(kind: &str) -> bool {
    matches!(
        kind,
        "init_declarator" | "declarator" | "field_declarator"
    )
}

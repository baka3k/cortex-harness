//! Namespace extraction.

use tree_sitter::Node;

use crate::symbols::{namespace_id, NamespaceDef, RelationEdge};
use crate::text::{extract_leading_comment, node_snippet};
use crate::walker::WalkContext;

pub fn extract_namespace(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
) -> Option<Vec<String>> {
    let name = crate::symbols::first_identifier_node(node, ctx.source)
        .unwrap_or_else(|| crate::symbols::anonymous_name("Namespace", node));

    let mut parts: Vec<&str> = namespace_stack.iter().map(|s| s.as_str()).collect();
    parts.push(&name);
    let qualified = parts.join("::");

    let ns_id = namespace_id(&qualified);
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let comment = extract_leading_comment(node, ctx.source);

    ctx.namespaces.push(NamespaceDef {
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
    });

    if let Some(nsd) = ctx.namespaces.last() {
        ctx.namespace_registry.insert(ns_id.clone(), nsd.clone());
    }

    if !namespace_stack.is_empty() {
        let parent_id = namespace_id(&namespace_stack.join("::"));
        ctx.relations.push(RelationEdge {
            source_id: parent_id,
            source_label: "Namespace".to_string(),
            target_id: ns_id,
            target_label: "Namespace".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }

    let mut new_stack = namespace_stack.to_vec();
    new_stack.push(name);
    Some(new_stack)
}

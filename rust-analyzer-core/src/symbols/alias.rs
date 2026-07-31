//! Type alias / namespace alias extraction.

use tree_sitter::Node;

use crate::symbols::{
    alias_id, anonymous_name, extract_base_type, namespace_id, qualified_name, scope_from_stacks,
    AliasDef, NamespaceDef, RelationEdge, TypeDef,
};
use crate::text::{node_snippet, node_text};
use crate::walker::WalkContext;

pub fn extract_type_alias(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let kind = if node.kind() == "type_definition" {
        "typedef"
    } else {
        "using"
    };
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let alias_name = crate::symbols::first_identifier_node(node, ctx.source)
        .unwrap_or_else(|| anonymous_name("Alias", node));
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let aid = alias_id(scope.as_deref(), &alias_name, ctx.rel_path);
    let qn = qualified_name(scope.as_deref(), &alias_name);

    let text = node_text(node, ctx.source);
    let target_name = extract_base_type(&text);

    ctx.aliases.push(AliasDef {
        symbol_id: aid.clone(),
        qualified_name: qn,
        name: alias_name,
        kind: kind.to_string(),
        target_name: target_name.clone(),
        file_path: ctx.rel_path.to_string(),
        start_line,
        end_line,
        code: snippet,
    });

    if let Some(target) = target_name {
        let target_id = crate::symbols::type_id(None, &target);
        if !ctx.type_registry.contains_key(&target_id) {
            ctx.types.push(TypeDef {
                symbol_id: target_id.clone(),
                qualified_name: target.clone(),
                name: target.split("::").last().unwrap_or(&target).to_string(),
                kind: "external".to_string(),
                file_path: ctx.rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: target.clone(),
                comment: String::new(),
                summary: String::new(),
                note: String::new(),
            });
            if let Some(td) = ctx.types.last() {
                ctx.type_registry.insert(target_id.clone(), td.clone());
            }
        }
        ctx.relations.push(RelationEdge {
            source_id: aid,
            source_label: "Alias".to_string(),
            target_id: target_id,
            target_label: "Type".to_string(),
            rel_type: "ALIASES".to_string(),
            properties: [("kind".to_string(), kind.to_string())]
                .into_iter()
                .collect(),
        });
    }
}

pub fn extract_namespace_alias(
    ctx: &mut WalkContext,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
) {
    let (snippet, start_line, end_line) = node_snippet(node, ctx.source);
    let alias_name = crate::symbols::first_identifier_node(node, ctx.source)
        .unwrap_or_else(|| anonymous_name("NamespaceAlias", node));
    let scope = scope_from_stacks(namespace_stack, type_stack);
    let aid = alias_id(scope.as_deref(), &alias_name, ctx.rel_path);
    let qn = qualified_name(scope.as_deref(), &alias_name);
    let text = node_text(node, ctx.source);
    let mut target_name: Option<String> = None;
    if let Some((_, rhs)) = text.split_once('=') {
        let rhs = rhs.trim();
        let trimmed: String = rhs
            .chars()
            .take_while(|c| *c != ';' && *c != '{' && *c != '}')
            .collect();
        if let Some(first) = crate::text::first_identifier_str(&trimmed) {
            target_name = Some(first.to_string());
        }
    }

    ctx.aliases.push(AliasDef {
        symbol_id: aid.clone(),
        qualified_name: qn,
        name: alias_name,
        kind: "namespace_alias".to_string(),
        target_name: target_name.clone(),
        file_path: ctx.rel_path.to_string(),
        start_line,
        end_line,
        code: snippet,
    });

    if let Some(target) = target_name {
        let ns_id = namespace_id(&target);
        if !ctx.namespace_registry.contains_key(&ns_id) {
            ctx.namespaces.push(NamespaceDef {
                symbol_id: ns_id.clone(),
                qualified_name: target.clone(),
                name: target.split("::").last().unwrap_or(&target).to_string(),
                file_path: ctx.rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: target.clone(),
                comment: String::new(),
                summary: String::new(),
                note: String::new(),
            });
            if let Some(nsd) = ctx.namespaces.last() {
                ctx.namespace_registry.insert(ns_id.clone(), nsd.clone());
            }
        }
        ctx.relations.push(RelationEdge {
            source_id: aid,
            source_label: "Alias".to_string(),
            target_id: ns_id,
            target_label: "Namespace".to_string(),
            rel_type: "ALIASES".to_string(),
            properties: [("kind".to_string(), "namespace_alias".to_string())]
                .into_iter()
                .collect(),
        });
    }
}

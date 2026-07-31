//! Relation extraction helper utilities (mostly USES_TYPE edges during
//! parameter type registration — mirrors `_register_type_usage`).

use tree_sitter::Node;

use crate::symbols::{
    extract_base_type, normalize_type_signature, type_id, NamespaceDef, RelationEdge, TypeDef,
};
use crate::text::node_text;
use crate::walker::WalkContext;

/// Register a USES_TYPE relation for any type names found in `param_text`,
/// creating external `TypeDef` placeholders as needed.
pub fn register_type_usage(
    ctx: &mut WalkContext,
    owner_id: &str,
    owner_label: &str,
    param_text: &str,
    _rel_path: &str,
) {
    // Strip pointer/ref qualifiers and namespace prefixes
    let normalized = normalize_type_signature(param_text);
    let mut cursor_pos = 0usize;
    for word in normalized.split(|c: char| !c.is_alphanumeric() && c != '_' && c != ':') {
        if word.is_empty() {
            continue;
        }
        cursor_pos += 1;
        if matches!(
            word,
            "const" | "volatile" | "mutable" | "static" | "extern" | "register"
            | "inline" | "struct" | "class" | "enum" | "typename" | "void" | "auto"
        ) {
            continue;
        }
        if let Some(base) = extract_base_type(word) {
            let tid = type_id(None, &base);
            if !ctx.type_registry.contains_key(&tid) {
                ctx.types.push(TypeDef {
                    symbol_id: tid.clone(),
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
                if let Some(td) = ctx.types.last() {
                    ctx.type_registry.insert(tid.clone(), td.clone());
                }
            }
            ctx.relations.push(RelationEdge {
                source_id: owner_id.to_string(),
                source_label: owner_label.to_string(),
                target_id: tid,
                target_label: "Type".to_string(),
                rel_type: "USES_TYPE".to_string(),
                properties: Default::default(),
            });
        }
        let _ = cursor_pos;
    }
}

/// Iterate `parameter_declaration` children of a declarator's parameter_list.
pub fn iter_parameter_declarations(declarator: Option<Node>) -> Vec<Node> {
    let Some(decl) = declarator else {
        return Vec::new();
    };
    let mut out = Vec::new();
    let mut cursor = decl.walk();
    for child in decl.children(&mut cursor) {
        if child.kind() != "parameter_list" {
            continue;
        }
        let mut pc = child.walk();
        for grand in child.children(&mut pc) {
            if grand.kind() == "parameter_declaration" {
                out.push(grand);
            }
        }
        break;
    }
    out
}

/// Build a USES_TYPE edge from a parameter declaration's text.
pub fn register_param_type_usage(
    ctx: &mut WalkContext,
    owner_id: &str,
    owner_label: &str,
    param: Node,
) {
    let text = node_text(param, ctx.source);
    register_type_usage(ctx, owner_id, owner_label, text, ctx.rel_path);
    let _ = NamespaceDef::default; // suppress unused
}

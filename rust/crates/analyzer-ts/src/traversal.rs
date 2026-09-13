//! Port `tools/ts/agents/traversal_agent.py` — AST walker + function recorder.

use regex::Regex;
use tree_sitter::Node;

use crate::ast::{
    CallEdge, FunctionDef, NamespaceDef, NavigateEdge, RelationEdge, RenderEdge, TypeDef,
};
use crate::parser::{
    anonymous_name, count_arguments, count_parameters, extract_call_name, extract_name_field,
    extract_leading_comment, extract_return_type, extract_param_types, extract_scope_stack,
    find_nodes_by_type, iter_calls, namespace_id, node_snippet, node_text, qualified_name,
    symbol_id, type_id,
};
use crate::regexes::regexes;
use crate::symbol::{
    collect_navigate_calls, collect_rendered_components, classify_nav_context,
    detect_middleware_kind, detect_nav_guard, detect_react_role, has_jsx_in_subtree,
    index_module_name, collect_route_configs,
};

pub const NAMESPACE_NODE_TYPES: [&str; 4] =
    ["namespace_declaration", "internal_module", "module_declaration", "module"];

pub const TYPE_NODE_KINDS: [(&str, &str); 5] = [
    ("class_declaration", "class"),
    ("abstract_class_declaration", "class"),
    ("interface_declaration", "interface"),
    ("type_alias_declaration", "type_alias"),
    ("enum_declaration", "enum"),
];

pub const FUNCTION_NODE_KINDS: [(&str, &str); 3] = [
    ("function_declaration", "function"),
    ("generator_function_declaration", "generator_function"),
    ("method_definition", "method"),
];

pub const INNER_FUNCTION_TYPES: [&str; 4] =
    ["arrow_function", "function", "generator_function", "function_expression"];

pub struct WalkState<'a> {
    pub source: &'a [u8],
    pub rel_path: String,
    pub namespaces: Vec<NamespaceDef>,
    pub types: Vec<TypeDef>,
    pub functions: Vec<FunctionDef>,
    pub relations: Vec<RelationEdge>,
    pub calls: Vec<CallEdge>,
    pub renders: Vec<RenderEdge>,
    pub navigates: Vec<NavigateEdge>,
}

pub fn type_kind_of(kind: &str) -> Option<&'static str> {
    TYPE_NODE_KINDS
        .iter()
        .find(|(node_kind, _)| *node_kind == kind)
        .map(|(_, kind)| *kind)
}

pub fn function_kind_of(kind: &str) -> Option<&'static str> {
    FUNCTION_NODE_KINDS
        .iter()
        .find(|(node_kind, _)| *node_kind == kind)
        .map(|(_, kind)| *kind)
}

/// `_find_inner_function_arg`.
pub fn find_inner_function_arg(call_node: Node) -> Option<Node> {
    let args = call_node.child_by_field_name("arguments")?;
    for arg in args.children(&mut args.walk()) {
        if INNER_FUNCTION_TYPES.contains(&arg.kind()) {
            return Some(arg);
        }
        if arg.kind() == "call_expression"
            && let Some(inner) = find_inner_function_arg(arg) {
                return Some(inner);
            }
    }
    None
}

/// `_extract_root_factory_name`.
pub fn extract_root_factory_name(call_node: Node, source: &[u8]) -> String {
    static GENERICS_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let generics_re = GENERICS_RE.get_or_init(|| Regex::new(r"<[^<>]*>").unwrap());
    let mut node = call_node;
    while let Some(fn_node) = node.child_by_field_name("function") {
        if fn_node.kind() == "call_expression" {
            node = fn_node;
            continue;
        }
        let raw = node_text(fn_node, source).trim().to_string();
        let dotted = generics_re
            .replace_all(&raw, "")
            .replace("?.", ".")
            .trim()
            .to_string();
        let normalized = extract_call_name(call_node, source).unwrap_or_default();
        if regexes().call_expr_kind_map.contains_key(dotted.as_str()) {
            return dotted;
        }
        return normalized;
    }
    extract_call_name(call_node, source).unwrap_or_default()
}

/// `_record_function`.
#[allow(clippy::too_many_arguments)]
pub fn record_function(
    state: &mut WalkState<'_>,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
    name_override: Option<String>,
    kind_override: Option<String>,
    calls_root: Option<Node>,
    parameters_node: Option<Node>,
    exported: bool,
) {
    let source = state.source;
    // Python `name_override or _extract_name_field(...)`: override rỗng là
    // falsy → fallback về extract_name_field.
    let mut name = match name_override.filter(|s| !s.is_empty()) {
        Some(name) => name,
        None => extract_name_field(node, source).unwrap_or_default(),
    };
    let kind = match kind_override.filter(|s| !s.is_empty()) {
        Some(kind) => kind,
        None => function_kind_of(node.kind()).unwrap_or("function").to_string(),
    };
    if name.is_empty() {
        name = index_module_name(&state.rel_path)
            .unwrap_or_else(|| anonymous_name("Function", node));
    }
    let kind = if kind == "method" && name == "constructor" {
        "constructor".to_string()
    } else {
        kind
    };

    let (snippet, start_line, end_line) = node_snippet(node, source);
    let comment = extract_leading_comment(node, source);
    let summary = comment.clone();
    let note = build_note(&snippet, &comment, &summary);
    let mut scope_stack: Vec<String> = namespace_stack.to_vec();
    scope_stack.extend_from_slice(type_stack);
    let scope_name = extract_scope_stack(&scope_stack);
    let param_src = parameters_node.unwrap_or(node);
    let arity = count_parameters(param_src);
    let return_type = extract_return_type(node, source);
    let param_types = extract_param_types(param_src, source);
    let func_id = symbol_id(scope_name.as_deref(), &name, arity, &state.rel_path);

    let call_root = calls_root.unwrap_or(node);
    let has_jsx = has_jsx_in_subtree(call_root);
    let middleware_kind =
        detect_middleware_kind(&name, &snippet, &state.rel_path);
    let react_role = detect_react_role(
        &name,
        &state.rel_path,
        has_jsx,
        &middleware_kind,
        &snippet,
    );

    state.functions.push(FunctionDef {
        symbol_id: func_id.clone(),
        qualified_name: qualified_name(scope_name.as_deref(), &name),
        name,
        kind,
        scope_name: scope_name.clone(),
        file_path: state.rel_path.clone(),
        start_line: start_line as i64,
        end_line: end_line as i64,
        arity,
        code: snippet.clone(),
        comment,
        summary,
        note,
        exported,
        return_type,
        param_types,
        intent: String::new(),
        inferred_doc: false,
        doc_confidence: 0.0,
        signals: Default::default(),
        side_effect: false,
        react_role,
        middleware_kind,
    });

    if !type_stack.is_empty() {
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(type_stack);
        state.relations.push(RelationEdge {
            source_id: type_id(&full.join("::")),
            source_label: "Type".to_string(),
            target_id: func_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    } else if !namespace_stack.is_empty() {
        state.relations.push(RelationEdge {
            source_id: namespace_id(&namespace_stack.join("::")),
            source_label: "Namespace".to_string(),
            target_id: func_id.clone(),
            target_label: "Function".to_string(),
            rel_type: "CONTAINS".to_string(),
            properties: Default::default(),
        });
    }

    for call_node in iter_calls(call_root) {
        let Some(callee) = extract_call_name(call_node, source) else {
            continue;
        };
        if callee.is_empty() {
            continue;
        }
        state.calls.push(CallEdge {
            caller_id: func_id.clone(),
            caller_scope: scope_name.clone(),
            callee_name: callee,
            callee_id: None,
            callee_arity: Some(count_arguments(call_node)),
        });
    }

    let role_snapshot = state.functions.last().unwrap().react_role.clone();
    let func_name = state.functions.last().unwrap().name.clone();
    if role_snapshot == "screen" || role_snapshot == "component" {
        for rendered_name in collect_rendered_components(call_root, source) {
            if rendered_name != func_name {
                state.renders.push(RenderEdge {
                    renderer_id: func_id.clone(),
                    rendered_name,
                });
            }
        }
    }

    let nav_raw = collect_navigate_calls(&snippet);
    if !nav_raw.is_empty() {
        let trigger = classify_nav_context(&snippet);
        let guard = detect_nav_guard(&snippet);
        for (target_name, nav_method) in nav_raw {
            state.navigates.push(NavigateEdge {
                source_id: func_id.clone(),
                target_name,
                nav_method,
                via: "direct".to_string(),
                trigger_type: trigger.clone(),
                guard: guard.clone(),
            });
        }
    }

    for (route_name, comp_name) in collect_route_configs(&snippet) {
        state.navigates.push(NavigateEdge {
            source_id: func_id.clone(),
            target_name: route_name,
            nav_method: "__route_config__".to_string(),
            via: comp_name,
            trigger_type: "user".to_string(),
            guard: None,
        });
    }
}

/// `_build_note` (parser_agent version = python_analyzer version).
pub fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

/// `_walk_tree`.
pub fn walk_tree(
    state: &mut WalkState<'_>,
    node: Node,
    namespace_stack: &[String],
    type_stack: &[String],
    exported_context: bool,
    exported_names: &mut std::collections::BTreeSet<String>,
) {
    let source = state.source;

    if node.kind() == "export_statement" || node.kind() == "export_default_declaration" {
        if let Some(decl) = node.child_by_field_name("declaration") {
            let bare = INNER_FUNCTION_TYPES.contains(&decl.kind());
            if bare {
                let explicit_name = extract_name_field(decl, source);
                record_function(
                    state,
                    decl,
                    namespace_stack,
                    type_stack,
                    Some(explicit_name.unwrap_or_else(|| {
                        index_module_name(&state.rel_path)
                            .unwrap_or_default()
                    })),
                    Some(
                        if decl.kind() == "arrow_function" {
                            "function_variable".to_string()
                        } else {
                            String::new()
                        },
                    ),
                    None,
                    None,
                    true,
                );
                return;
            }
            if decl.kind() == "call_expression" {
                let default_name = index_module_name(&state.rel_path)
                    .unwrap_or_else(|| anonymous_name("Function", decl));
                let factory_name = extract_root_factory_name(decl, source);
                let kind = regexes()
                    .call_expr_kind_map
                    .get(factory_name.as_str())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| "function_variable".to_string());
                let inner_fn = find_inner_function_arg(decl);
                record_function(
                    state,
                    decl,
                    namespace_stack,
                    type_stack,
                    Some(default_name),
                    Some(kind),
                    Some(decl),
                    inner_fn,
                    true,
                );
                return;
            }
            walk_tree(state, decl, namespace_stack, type_stack, true, exported_names);
            return;
        }
        for spec in find_nodes_by_type(node, "export_specifier") {
            let name_node = spec
                .child_by_field_name("name")
                .or_else(|| spec.child_by_field_name("value"));
            let Some(name_node) = name_node else { continue };
            let name = node_text(name_node, source).trim().to_string();
            if !name.is_empty() {
                exported_names.insert(name);
            }
            if let Some(alias_node) = spec.child_by_field_name("alias") {
                let alias = node_text(alias_node, source).trim().to_string();
                if !alias.is_empty() {
                    exported_names.insert(alias);
                }
            }
        }
        return;
    }

    if NAMESPACE_NODE_TYPES.contains(&node.kind()) {
        let mut name = extract_name_field(node, source).unwrap_or_default();
        if name.is_empty() {
            name = anonymous_name("Namespace", node);
        }
        let mut qualified_stack: Vec<String> = namespace_stack.to_vec();
        qualified_stack.push(name.clone());
        let qualified = qualified_stack.join("::");
        let ns_id = namespace_id(&qualified);
        let (snippet, start_line, end_line) = node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        state.namespaces.push(NamespaceDef {
            symbol_id: ns_id.clone(),
            qualified_name: qualified,
            name: name.clone(),
            file_path: state.rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment,
            summary,
            note,
        });
        if !namespace_stack.is_empty() {
            state.relations.push(RelationEdge {
                source_id: namespace_id(&namespace_stack.join("::")),
                source_label: "Namespace".to_string(),
                target_id: ns_id.clone(),
                target_label: "Namespace".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }
        let mut child_ns: Vec<String> = namespace_stack.to_vec();
        child_ns.push(name);
        for child in node.children(&mut node.walk()) {
            walk_tree(state, child, &child_ns, type_stack, false, exported_names);
        }
        return;
    }

    if let Some(kind) = type_kind_of(node.kind()) {
        let mut kind = kind.to_string();
        let mut name = extract_name_field(node, source).unwrap_or_default();
        if name.is_empty() {
            let capitalized = format!(
                "{}{}", kind.get(..1).unwrap().to_uppercase(), &kind[1..]
            );
            name = anonymous_name(&capitalized, node);
            kind = format!("anonymous_{kind}");
        }
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(type_stack);
        let qualified = if full.is_empty() {
            name.clone()
        } else {
            format!("{}::{name}", full.join("::"))
        };
        let tid = type_id(&qualified);
        let (snippet, start_line, end_line) = node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let summary = comment.clone();
        let note = build_note(&snippet, &comment, &summary);
        state.types.push(TypeDef {
            symbol_id: tid.clone(),
            qualified_name: qualified.clone(),
            name: qualified.rsplit("::").next().unwrap_or(&qualified).to_string(),
            kind,
            file_path: state.rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment,
            summary,
            note,
            exported: exported_context,
        });
        if !namespace_stack.is_empty() {
            state.relations.push(RelationEdge {
                source_id: namespace_id(&namespace_stack.join("::")),
                source_label: "Namespace".to_string(),
                target_id: tid.clone(),
                target_label: "Type".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }
        if !type_stack.is_empty() {
            let mut parent: Vec<String> = namespace_stack.to_vec();
            parent.extend_from_slice(type_stack);
            state.relations.push(RelationEdge {
                source_id: type_id(&parent.join("::")),
                source_label: "Type".to_string(),
                target_id: tid.clone(),
                target_label: "Type".to_string(),
                rel_type: "CONTAINS".to_string(),
                properties: Default::default(),
            });
        }
        let mut child_types: Vec<String> = type_stack.to_vec();
        child_types.push(name);
        for child in node.children(&mut node.walk()) {
            walk_tree(state, child, namespace_stack, &child_types, false, exported_names);
        }
        return;
    }

    if FUNCTION_NODE_KINDS.iter().any(|(kind, _)| *kind == node.kind()) {
        record_function(
            state, node, namespace_stack, type_stack, None, None, None, None,
            exported_context,
        );
        return;
    }

    if node.kind() == "lexical_declaration" || node.kind() == "variable_declaration" {
        for child in node.children(&mut node.walk()) {
            if child.kind() != "variable_declarator" {
                continue;
            }
            let init = child
                .child_by_field_name("value")
                .or_else(|| child.child_by_field_name("initializer"));
            let Some(init) = init else { continue };
            if INNER_FUNCTION_TYPES.contains(&init.kind()) {
                let name = extract_name_field(child, source);
                record_function(
                    state,
                    child,
                    namespace_stack,
                    type_stack,
                    name,
                    Some("function_variable".to_string()),
                    Some(init),
                    Some(init),
                    exported_context,
                );
            } else if init.kind() == "call_expression" {
                let name = extract_name_field(child, source);
                if name.as_deref().map(str::is_empty).unwrap_or(true) {
                    continue;
                }
                let factory_name = extract_root_factory_name(init, source);
                let kind = regexes()
                    .call_expr_kind_map
                    .get(factory_name.as_str())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| "function_variable".to_string());
                let inner_fn = find_inner_function_arg(init);
                record_function(
                    state,
                    child,
                    namespace_stack,
                    type_stack,
                    name,
                    Some(kind),
                    Some(init),
                    inner_fn,
                    exported_context,
                );
            }
        }
        // fall through: continue walking children như Python
    }

    for child in node.children(&mut node.walk()) {
        walk_tree(state, child, namespace_stack, type_stack, exported_context, exported_names);
    }
}

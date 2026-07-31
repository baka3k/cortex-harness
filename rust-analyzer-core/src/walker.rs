//! Iterative AST walker — direct port of `_walk_tree` in cplus_analyzer.py.
//!
//! Uses an explicit work stack (VecDeque) to avoid stack overflow on deep
//! templates. Each frame carries the per-branch state
//! (namespace_stack, type_stack, using_namespaces, using_imports).

use std::collections::VecDeque;

use tree_sitter::Node;

use crate::symbols::{
    extract_using_namespace, extract_using_qualified, CallEdge, FieldDef, FunctionDef, NamespaceDef,
    RelationEdge, TemplateDef, TypeDef,
};
use crate::text::{extract_includes, extract_macros, node_text};
use crate::symbols::{self, ParseMeta, ParseOutput};

use std::collections::HashMap;

/// Shared mutable state used by every walker frame.
pub struct WalkContext {
    pub source: &'static [u8],
    pub rel_path: &'static str,
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<crate::symbols::AliasDef>,
    pub templates: Vec<TemplateDef>,
    pub function_types: HashMap<String, crate::symbols::FunctionTypeDef>,
    pub type_registry: HashMap<String, TypeDef>,
    pub namespace_registry: HashMap<String, NamespaceDef>,
    pub using_namespaces: Vec<String>,
    pub using_imports: HashMap<String, String>,
}

/// Stack frame — all state passed into a child visit.
pub struct Frame<'a> {
    pub node: Node<'a>,
    pub namespace_stack: Vec<String>,
    pub type_stack: Vec<String>,
    pub using_namespaces: Vec<String>,
    pub using_imports: HashMap<String, String>,
}

/// Run the walker over a parsed tree, returning a populated ParseOutput.
///
/// **Note:** this is the original C++-specific entry point retained for the
/// pilot's parity tests. Phase 1+ work should prefer `profile::walk_with_profile`
/// with a `LanguageProfile` so the walker can be reused across languages.
pub fn walk_tree(root: Node, source: &'static [u8], rel_path: &'static str) -> ParseOutput {
    let mut ctx = new_walk_context(source, rel_path);
    let mut work: VecDeque<Frame<'_>> = VecDeque::new();
    work.push_back(Frame {
        node: root,
        namespace_stack: Vec::new(),
        type_stack: Vec::new(),
        using_namespaces: Vec::new(),
        using_imports: HashMap::new(),
    });

    while let Some(frame) = work.pop_front() {
        process_frame(&mut ctx, frame, &mut work);
    }

    finalize_output(ctx, "cpp")
}

/// Build a fresh `WalkContext` for a (source, rel_path) pair. Used by both
/// `walk_tree` and `profile::walk_with_profile`.
pub fn new_walk_context(source: &'static [u8], rel_path: &'static str) -> WalkContext {
    WalkContext {
        source,
        rel_path,
        functions: Vec::new(),
        calls: Vec::new(),
        types: Vec::new(),
        namespaces: Vec::new(),
        relations: Vec::new(),
        fields: Vec::new(),
        aliases: Vec::new(),
        templates: Vec::new(),
        function_types: HashMap::new(),
        type_registry: HashMap::new(),
        namespace_registry: HashMap::new(),
        using_namespaces: Vec::new(),
        using_imports: HashMap::new(),
    }
}

fn process_frame<'a>(ctx: &mut WalkContext, frame: Frame<'a>, work: &mut VecDeque<Frame<'a>>) {
    let node = frame.node;
    let namespace_stack = frame.namespace_stack;
    let type_stack = frame.type_stack;
    let using_namespaces = frame.using_namespaces;
    let using_imports = frame.using_imports;

    match node.kind() {
        "using_directive" | "using_declaration" => {
            let text = node_text(node, ctx.source);
            if let Some(ns) = extract_using_namespace(&text) {
                ctx.using_namespaces.push(ns);
                return;
            }
            if let Some(q) = extract_using_qualified(&text) {
                if q.contains("::") {
                    let short = q.split("::").last().unwrap_or(&q).to_string();
                    ctx.using_imports.insert(short, q);
                }
            }
            return;
        }
        "namespace_alias_definition" => {
            crate::symbols::alias::extract_namespace_alias(ctx, node, &namespace_stack, &type_stack);
            return;
        }
        "template_declaration" => {
            crate::symbols::template::extract_template(ctx, node, &namespace_stack, &type_stack);
            // Recurse into the templated child with the same stacks so the
            // wrapped function/type is also extracted normally.
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if matches!(
                    child.kind(),
                    "class_specifier"
                        | "struct_specifier"
                        | "union_specifier"
                        | "enum_specifier"
                ) {
                    process_frame(
                        ctx,
                        Frame {
                            node: child,
                            namespace_stack: namespace_stack.clone(),
                            type_stack: type_stack.clone(),
                            using_namespaces: using_namespaces.clone(),
                            using_imports: using_imports.clone(),
                        },
                        work,
                    );
                    break;
                }
                if matches!(child.kind(), "function_definition" | "function_declaration") {
                    // Extract as a function with current scope; then walk its body.
                    if child.kind() == "function_definition" {
                        crate::symbols::function::extract_function_definition(
                            ctx, child, &namespace_stack, &type_stack,
                        );
                    } else {
                        crate::symbols::function::extract_function_declaration(
                            ctx, child, &namespace_stack, &type_stack,
                        );
                    }
                    push_children(
                        work,
                        child,
                        &namespace_stack,
                        &type_stack,
                        &using_namespaces,
                        &using_imports,
                    );
                    break;
                }
            }
            return;
        }
        "namespace_definition" => {
            if let Some(new_stack) =
                crate::symbols::namespace::extract_namespace(ctx, node, &namespace_stack)
            {
                push_children(work, node, &new_stack, &type_stack, &using_namespaces, &using_imports);
            }
            return;
        }
        "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier" => {
            crate::symbols::type_def::extract_type(ctx, node, &namespace_stack, &type_stack);
            // Recurse with extended type stack
            let name = symbols::first_identifier_node(node, ctx.source)
                .unwrap_or_else(|| symbols::anonymous_name("Type", node));
            let mut new_ty = type_stack.clone();
            new_ty.push(name);
            push_children(work, node, &namespace_stack, &new_ty, &using_namespaces, &using_imports);
            return;
        }
        "function_definition" => {
            crate::symbols::function::extract_function_definition(
                ctx,
                node,
                &namespace_stack,
                &type_stack,
            );
            return;
        }
        "function_declaration" => {
            crate::symbols::function::extract_function_declaration(
                ctx,
                node,
                &namespace_stack,
                &type_stack,
            );
            return;
        }
        "declaration" => {
            let parent_type = node.parent().map(|p| p.kind()).unwrap_or("");
            if !matches!(parent_type, "translation_unit" | "declaration_list") {
                push_children(work, node, &namespace_stack, &type_stack, &using_namespaces, &using_imports);
                return;
            }
            let scope = symbols::scope_from_stacks(&namespace_stack, &type_stack);
            // Look for function declarators first
            let mut any_function_declarator = false;
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if matches!(
                    child.kind(),
                    "function_declarator" | "pointer_declarator" | "array_declarator"
                ) {
                    any_function_declarator = true;
                    break;
                }
            }
            if any_function_declarator {
                let has_extern = node.children(&mut node.walk()).any(|c| {
                    c.kind() == "storage_class_specifier"
                        && node_text(c, ctx.source).trim() == "extern"
                });
                crate::symbols::function::declaration_function(
                    ctx,
                    node,
                    &namespace_stack,
                    &type_stack,
                    has_extern,
                );
            } else {
                let decl_type_text = declaration_type_text(node, ctx.source);
                let has_extern = node.children(&mut node.walk()).any(|c| {
                    c.kind() == "storage_class_specifier"
                        && node_text(c, ctx.source).trim() == "extern"
                });
                crate::symbols::field::extract_declaration_variable(
                    ctx,
                    node,
                    &namespace_stack,
                    &type_stack,
                    &decl_type_text,
                    has_extern,
                );
            }
            let _ = scope;
            return;
        }
        "field_declaration" => {
            // First check if this is a method declaration
            let mut is_method = false;
            let mut cursor = node.walk();
            for child in node.children(&mut cursor) {
                if matches!(
                    child.kind(),
                    "function_declarator" | "pointer_declarator" | "array_declarator"
                ) {
                    is_method = true;
                    break;
                }
            }
            if is_method {
                crate::symbols::function::method_declaration_for_field(
                    ctx,
                    node,
                    &namespace_stack,
                    &type_stack,
                );
            } else {
                crate::symbols::field::extract_field_declaration(
                    ctx,
                    node,
                    &namespace_stack,
                    &type_stack,
                );
            }
            return;
        }
        "type_definition" | "alias_declaration" | "type_alias_declaration" => {
            crate::symbols::alias::extract_type_alias(ctx, node, &namespace_stack, &type_stack);
            return;
        }
        _ => {}
    }

    // Default: push all children with the current state.
    push_children(work, node, &namespace_stack, &type_stack, &using_namespaces, &using_imports);
}

pub fn push_children<'a>(
    work: &mut VecDeque<Frame<'a>>,
    node: Node<'a>,
    namespace_stack: &[String],
    type_stack: &[String],
    using_namespaces: &[String],
    using_imports: &HashMap<String, String>,
) {
    let mut cursor = node.walk();
    let children: Vec<Node> = node.children(&mut cursor).collect();
    // The Python walker uses `reversed(node.children)`; mirror that.
    for child in children.into_iter().rev() {
        work.push_back(Frame {
            node: child,
            namespace_stack: namespace_stack.to_vec(),
            type_stack: type_stack.to_vec(),
            using_namespaces: using_namespaces.to_vec(),
            using_imports: using_imports.clone(),
        });
    }
}

pub(crate) fn declaration_type_text(node: Node, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if !child.is_named() {
            // include type specifiers / qualifiers (often unnamed tokens)
            let text = node_text(child, source).trim();
            if !text.is_empty() && text != "," && text != ";" {
                parts.push(text.to_string());
            }
            continue;
        }
        match child.kind() {
            "type_identifier" | "primitive_type" | "struct_specifier" | "class_specifier"
            | "enum_specifier" | "union_specifier" | "template_type" | "qualified_identifier"
            | "auto" | "decltype" | "dependent_type" | "type_qualifier" => {
                parts.push(node_text(child, source).trim().to_string());
            }
            _ => {}
        }
    }
    parts.join(" ")
}

pub(crate) fn finalize_output(ctx: WalkContext, parser_language: &str) -> ParseOutput {
    let file_code = String::from_utf8_lossy(ctx.source).into_owned();
    let file_lines = ctx.source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    let file_includes = extract_includes(&file_code);
    let file_macros = extract_macros(&file_code);

    ParseOutput {
        file_def: crate::symbols::FileDef {
            file_path: ctx.rel_path.to_string(),
            start_line: 1,
            end_line: file_lines,
            code: file_code,
            comment: String::new(),
            summary: String::new(),
            note: String::new(),
        },
        functions: ctx.functions,
        calls: ctx.calls,
        types: ctx.types,
        namespaces: ctx.namespaces,
        relations: ctx.relations,
        function_types: ctx.function_types.values().cloned().collect(),
        fields: ctx.fields,
        aliases: ctx.aliases,
        templates: ctx.templates,
        using_namespaces: ctx.using_namespaces,
        using_imports: ctx.using_imports,
        includes: file_includes,
        macros: file_macros,
        parse_meta: ParseMeta {
            parser_language: parser_language.to_string(),
            parser_language_initial: parser_language.to_string(),
            header_retry_attempted: false,
            header_retry_selected: false,
            has_error: false,
            error_nodes: 0,
            error_nodes_initial: 0,
            header_retry_error_nodes: None,
            header_retry_has_error: None,
        },
    }
}

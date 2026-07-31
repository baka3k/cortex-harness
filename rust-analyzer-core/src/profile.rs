//! `LanguageProfile` — dispatch-table abstraction for per-language walkers.
//!
//! Phase 1 of the multi-language Rust extraction plan refactors the
//! C++-specific `walker::process_frame` match into a trait that maps
//! `node.kind()` → a per-language handler. Adding a new language = writing
//! a `LanguageProfile` impl, not copying the walker. The iterative DFS core,
//! rayon batch, and PyO3 crossing stay shared.
//!
//! Design:
//! - `NodeHandler` is a `fn` pointer (not a closure) so the dispatch table
//!   is plain data — `HashMap<&'static str, NodeHandler>`.
//! - Handlers mutate `WalkContext` and may push child frames onto the work
//!   queue. Handlers that *don't* match a node kind must return `None` from
//!   the dispatch lookup, which the driver treats as "default: push all
//!   children with current state."

use std::collections::{HashMap, VecDeque};

use tree_sitter::Node;

use crate::walker::{push_children, Frame, WalkContext};
use crate::symbols::ParseOutput;

/// Per-node-type handler signature.
///
/// The handler receives a mutable reference to the `WalkContext`, the
/// `Node` being processed, the work queue (so it can push child frames),
/// and the current `Frame` (carrying namespace / type / using stacks).
///
/// Note: this uses lifetime parameters instead of HRTB so the lifetime
/// unifies cleanly with the `Frame<'tree>` carried in the work queue.
pub type NodeHandler<'a> = fn(
    &mut WalkContext,
    Node<'a>,
    &mut VecDeque<Frame<'a>>,
    Frame<'a>,
);

/// Trait every language implements to plug into the shared DFS core.
pub trait LanguageProfile: Sync + Send {
    /// Stable id used by `grammar::registry` (e.g. "cpp", "go", "java").
    fn id(&self) -> &'static str;

    /// `parser_language` written into `ParseMeta`.
    fn parser_language(&self) -> &'static str;

    /// Lookup handler for a node kind. Returning `None` falls through to the
    /// default "push children" path.
    fn dispatch<'a>(&self, node_kind: &str) -> Option<NodeHandler<'a>>;
}

/// Drive the iterative DFS over a tree using the given profile.
pub fn walk_with_profile<P: LanguageProfile>(
    profile: &P,
    root: Node,
    source: &'static [u8],
    rel_path: &'static str,
) -> ParseOutput {
    let mut ctx = crate::walker::new_walk_context(source, rel_path);
    let mut work: VecDeque<Frame> = VecDeque::new();

    work.push_back(Frame {
        node: root,
        namespace_stack: Vec::new(),
        type_stack: Vec::new(),
        using_namespaces: Vec::new(),
        using_imports: HashMap::new(),
    });

    while let Some(frame) = work.pop_front() {
        let handler = profile.dispatch(frame.node.kind());
        match handler {
            Some(h) => h(&mut ctx, frame.node, &mut work, frame),
            None => push_children(
                &mut work,
                frame.node,
                &frame.namespace_stack,
                &frame.type_stack,
                &frame.using_namespaces,
                &frame.using_imports,
            ),
        }
    }

    crate::walker::finalize_output(ctx, profile.parser_language())
}

// ── C++ profile (Phase 1 refactor) ──────────────────────────────────────

/// The C++ profile — extract of the original `process_frame` match arms.
pub struct CppProfile;

impl LanguageProfile for CppProfile {
    fn id(&self) -> &'static str { "cpp" }
    fn parser_language(&self) -> &'static str { "cpp" }

    fn dispatch<'a>(&self, node_kind: &str) -> Option<NodeHandler<'a>> {
        match node_kind {
            "using_directive" | "using_declaration" => Some(handle_using),
            "namespace_alias_definition" => Some(handle_namespace_alias),
            "template_declaration" => Some(handle_template_declaration),
            "namespace_definition" => Some(handle_namespace_definition),
            "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier"
                => Some(handle_type_specifier),
            "function_definition" => Some(handle_function_definition),
            "function_declaration" => Some(handle_function_declaration),
            "declaration" => Some(handle_declaration),
            "field_declaration" => Some(handle_field_declaration),
            "type_definition" | "alias_declaration" | "type_alias_declaration"
                => Some(handle_alias),
            _ => None,
        }
    }
}

// ── Handlers (Phase 1: extracted verbatim from walker.rs) ───────────────

use crate::text::node_text;
use crate::symbols;

fn handle_using<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    _work: &mut VecDeque<Frame<'a>>,
    _frame: Frame<'a>,
) {
    let text = node_text(node, ctx.source);
    if let Some(ns) = symbols::extract_using_namespace(&text) {
        ctx.using_namespaces.push(ns);
        return;
    }
    if let Some(q) = symbols::extract_using_qualified(&text) {
        if q.contains("::") {
            let short = q.split("::").last().unwrap_or(&q).to_string();
            ctx.using_imports.insert(short, q);
        }
    }
}

fn handle_namespace_alias<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    _work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    symbols::alias::extract_namespace_alias(
        ctx,
        node,
        &frame.namespace_stack,
        &frame.type_stack,
    );
}

fn handle_template_declaration<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    symbols::template::extract_template(
        ctx,
        node,
        &frame.namespace_stack,
        &frame.type_stack,
    );
    // Recurse into the templated child with the same stacks.
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if matches!(
            child.kind(),
            "class_specifier" | "struct_specifier" | "union_specifier" | "enum_specifier"
        ) {
            push_children(
                work,
                child,
                &frame.namespace_stack,
                &frame.type_stack,
                &frame.using_namespaces,
                &frame.using_imports,
            );
            break;
        }
        if matches!(child.kind(), "function_definition" | "function_declaration") {
            if child.kind() == "function_definition" {
                symbols::function::extract_function_definition(
                    ctx,
                    child,
                    &frame.namespace_stack,
                    &frame.type_stack,
                );
            } else {
                symbols::function::extract_function_declaration(
                    ctx,
                    child,
                    &frame.namespace_stack,
                    &frame.type_stack,
                );
            }
            push_children(
                work,
                child,
                &frame.namespace_stack,
                &frame.type_stack,
                &frame.using_namespaces,
                &frame.using_imports,
            );
            break;
        }
    }
}

fn handle_namespace_definition<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    if let Some(new_stack) =
        symbols::namespace::extract_namespace(ctx, node, &frame.namespace_stack)
    {
        push_children(
            work,
            node,
            &new_stack,
            &frame.type_stack,
            &frame.using_namespaces,
            &frame.using_imports,
        );
    }
}

fn handle_type_specifier<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    symbols::type_def::extract_type(
        ctx,
        node,
        &frame.namespace_stack,
        &frame.type_stack,
    );
    let name = symbols::first_identifier_node(node, ctx.source)
        .unwrap_or_else(|| symbols::anonymous_name("Type", node));
    let mut new_ty = frame.type_stack.clone();
    new_ty.push(name);
    push_children(
        work,
        node,
        &frame.namespace_stack,
        &new_ty,
        &frame.using_namespaces,
        &frame.using_imports,
    );
}

fn handle_function_definition<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    _work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    symbols::function::extract_function_definition(
        ctx,
        node,
        &frame.namespace_stack,
        &frame.type_stack,
    );
}

fn handle_function_declaration<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    _work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    symbols::function::extract_function_declaration(
        ctx,
        node,
        &frame.namespace_stack,
        &frame.type_stack,
    );
}

fn handle_declaration<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    let parent_type = node.parent().map(|p| p.kind()).unwrap_or("");
    if !matches!(parent_type, "translation_unit" | "declaration_list") {
        push_children(
            work,
            node,
            &frame.namespace_stack,
            &frame.type_stack,
            &frame.using_namespaces,
            &frame.using_imports,
        );
        return;
    }
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
    let has_extern = node.children(&mut node.walk()).any(|c| {
        c.kind() == "storage_class_specifier"
            && node_text(c, ctx.source).trim() == "extern"
    });
    if any_function_declarator {
        symbols::function::declaration_function(
            ctx,
            node,
            &frame.namespace_stack,
            &frame.type_stack,
            has_extern,
        );
    } else {
        let decl_type_text = crate::walker::declaration_type_text(node, ctx.source);
        symbols::field::extract_declaration_variable(
            ctx,
            node,
            &frame.namespace_stack,
            &frame.type_stack,
            &decl_type_text,
            has_extern,
        );
    }
}

fn handle_field_declaration<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    _work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
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
        symbols::function::method_declaration_for_field(
            ctx,
            node,
            &frame.namespace_stack,
            &frame.type_stack,
        );
    } else {
        symbols::field::extract_field_declaration(
            ctx,
            node,
            &frame.namespace_stack,
            &frame.type_stack,
        );
    }
}

fn handle_alias<'a>(
    ctx: &mut WalkContext,
    node: Node<'a>,
    _work: &mut VecDeque<Frame<'a>>,
    frame: Frame<'a>,
) {
    symbols::alias::extract_type_alias(
        ctx,
        node,
        &frame.namespace_stack,
        &frame.type_stack,
    );
}

// ── Go profile (Phase 2 scaffold) ───────────────────────────────────────
//
// Go shares the cplus payload schema (Tier 1 — schema-compatible), so the
// payload builder does not need a new function. What we need is:
//
// 1. Add `tree-sitter-go = "0.23"` to Cargo.toml.
// 2. Add a `GoGrammar` impl in `grammar.rs` returning that language.
// 3. Fill in the dispatch table below for go's node types (struct_type,
//    interface_type, method_declaration, field_declaration, function_declaration,
//    import_declaration, package_clause, call_expression, etc.).
// 4. Wire `parse_go_source(...)` in `lib.rs` and an entry in `extract_batch`
//    that picks `GoProfile` for the `"go"` language id.
//
// The dispatch table is the only per-language code that has to be written —
// the iterative DFS, rayon batch, and payload builder are reused verbatim.

pub struct GoProfile;

impl LanguageProfile for GoProfile {
    fn id(&self) -> &'static str { "go" }
    fn parser_language(&self) -> &'static str { "go" }

    fn dispatch<'a>(&self, node_kind: &str) -> Option<NodeHandler<'a>> {
        // TODO(Phase 2): populate with go node types once tree-sitter-go is
        // added. For now, every node falls through to the default
        // "push children" path — that produces an empty ParseOutput, which is
        // enough to verify the trait dispatch wiring works for non-cplus
        // languages.
        let _ = node_kind;
        None
    }
}

// ── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::grammar::registry;

    #[test]
    fn cpp_profile_covers_pilot_node_kinds() {
        let p = CppProfile;
        for k in &[
            "using_directive", "using_declaration", "namespace_alias_definition",
            "template_declaration", "namespace_definition", "class_specifier",
            "struct_specifier", "union_specifier", "enum_specifier",
            "function_definition", "function_declaration", "declaration",
            "field_declaration", "type_definition", "alias_declaration",
            "type_alias_declaration",
        ] {
            assert!(
                p.dispatch(k).is_some(),
                "CppProfile missing handler for {:?}",
                k,
            );
        }
        assert!(p.dispatch("translation_unit").is_none());
        assert!(p.dispatch("identifier").is_none());
    }

    #[test]
    fn cpp_profile_ids_match_grammar_registry() {
        let p = CppProfile;
        let ids: Vec<&str> = registry().iter().map(|g| g.id()).collect();
        assert!(
            ids.contains(&p.id()),
            "CppProfile id {:?} not in grammar registry {:?}",
            p.id(),
            ids,
        );
    }

    #[test]
    fn walk_with_profile_emits_parser_language() {
        // Smoke: walk an empty root and confirm ParseMeta.parser_language
        // comes from the profile, not the walker.
        let p = CppProfile;
        let src: Vec<u8> = b"int main() { return 0; }".to_vec();
        let src_static: &'static [u8] = Box::leak(src.into_boxed_slice());
        let rel: &'static str = Box::leak("test.c".to_string().into_boxed_str());

        // Build a tree using the parser module.
        let tree = crate::parser::parse_source(src_static, true).expect("parse failed");
        let out = walk_with_profile(&p, tree.root_node(), src_static, rel);
        assert_eq!(out.parse_meta.parser_language, "cpp");
    }

    #[test]
    fn go_profile_scaffold_dispatches_empty() {
        // Phase 2 scaffold: the GoProfile exists and routes to a non-cplus
        // parser_language, but its dispatch table is empty until the
        // tree-sitter-go grammar is wired in. This test guards against the
        // scaffold being silently removed.
        let p = GoProfile;
        assert_eq!(p.id(), "go");
        assert_eq!(p.parser_language(), "go");
        assert!(p.dispatch("function_declaration").is_none());
    }
}
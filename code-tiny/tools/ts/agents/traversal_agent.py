"""TraversalAgent — AST tree walker and function recorder.

Responsibilities:
- Walk tree-sitter AST nodes and dispatch to type/namespace/function recorders.
- ``_record_function``: extract a FunctionDef with all edges (CALLS, RENDERS, NAVIGATE).
- ``_walk_tree``: recursive dispatcher over the full AST.

This module imports helpers from parser_agent and symbol_agent to avoid
circular dependencies (parser_agent ← symbol_agent ← traversal_agent).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from tools.ts.types.ast_types import (
    FunctionDef, RenderEdge, NavigateEdge,
)
from tools.ts.types.graph_types import (
    NamespaceDef, TypeDef, RelationEdge, CallEdge,
)
from tools.ts.utils.id_utils import (
    _symbol_id, _qualified_name, _type_id, _namespace_id, _anonymous_name,
)
from tools.ts.utils.file_utils import _index_module_name
from tools.ts.utils.regex_patterns import _CALL_EXPR_KIND_MAP
from tools.ts.agents.parser_agent import (
    _node_text, _extract_name_field, _first_identifier,
    _extract_scope_stack, _count_parameters, _extract_return_type,
    _extract_param_types, _count_arguments, _iter_calls, _extract_call_name,
    _node_snippet, _extract_leading_comment, _build_note,
    _find_nodes_by_type, _normalize_call_name,
)
from tools.ts.agents.symbol_agent import (
    _has_jsx_in_subtree, _detect_middleware_kind, _detect_react_role,
    _collect_rendered_components, _collect_navigate_calls,
    _classify_nav_context, _detect_nav_guard, _collect_route_configs,
)


# ─── Node kind constants ──────────────────────────────────────────────────────

_NAMESPACE_NODE_TYPES: Set[str] = {
    "namespace_declaration",
    "internal_module",
    "module_declaration",
    "module",
}

_TYPE_NODE_KINDS: Dict[str, str] = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type_alias",
    "enum_declaration": "enum",
}

_FUNCTION_NODE_KINDS: Dict[str, str] = {
    "function_declaration": "function",
    "generator_function_declaration": "generator_function",
    "method_definition": "method",
}

_INNER_FUNCTION_TYPES: Set[str] = {
    "arrow_function", "function", "generator_function", "function_expression",
}


# ─── Factory call helpers ─────────────────────────────────────────────────────

def _find_inner_function_arg(call_node: Any) -> Optional[Any]:
    """Find the first arrow/function expression among the arguments of a call."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for arg in args.children:
        if arg.type in _INNER_FUNCTION_TYPES:
            return arg
        if arg.type == "call_expression":
            inner = _find_inner_function_arg(arg)
            if inner is not None:
                return inner
    return None


def _extract_root_factory_name(call_node: Any, source_bytes: bytes) -> str:
    """Extract the root factory name from potentially chained calls."""
    node = call_node
    while True:
        fn = node.child_by_field_name("function")
        if fn is None:
            break
        if fn.type == "call_expression":
            node = fn
            continue
        raw = _node_text(fn, source_bytes).strip()
        dotted = re.sub(r"<[^<>]*>", "", raw).replace("?.", ".").strip()
        normalized = _normalize_call_name(raw)
        if dotted in _CALL_EXPR_KIND_MAP:
            return dotted
        return normalized
    return _extract_call_name(call_node, source_bytes) or ""


# ─── Function recorder ────────────────────────────────────────────────────────

def _record_function(
    node: Any,
    source_bytes: bytes,
    rel_path: str,
    namespace_stack: List[str],
    type_stack: List[str],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    renders: List[RenderEdge],
    navigates: List[NavigateEdge],
    name_override: Optional[str] = None,
    kind_override: Optional[str] = None,
    calls_root: Optional[Any] = None,
    parameters_node: Optional[Any] = None,
    exported: bool = False,
) -> None:
    name = name_override or _extract_name_field(node, source_bytes)
    kind = kind_override or _FUNCTION_NODE_KINDS.get(node.type, "function")
    if not name:
        name = _index_module_name(rel_path) or _anonymous_name("Function", node)
    if kind == "method" and name == "constructor":
        kind = "constructor"

    snippet, start_line, end_line = _node_snippet(node, source_bytes)
    comment = _extract_leading_comment(node, source_bytes)
    summary = comment
    note = _build_note(snippet, comment, summary)
    scope_stack = namespace_stack + type_stack
    scope_name = _extract_scope_stack(scope_stack)
    _param_src = parameters_node or node
    arity = _count_parameters(_param_src)
    return_type = _extract_return_type(node, source_bytes)
    param_types = _extract_param_types(_param_src, source_bytes)
    func_id = _symbol_id(scope_name, name, arity, rel_path)

    call_root = calls_root or node
    has_jsx = _has_jsx_in_subtree(call_root)
    middleware_kind = _detect_middleware_kind(name, snippet, rel_path)
    react_role = _detect_react_role(name, rel_path, has_jsx, middleware_kind, code=snippet)

    functions.append(FunctionDef(
        symbol_id=func_id,
        qualified_name=_qualified_name(scope_name, name),
        name=name,
        kind=kind,
        scope_name=scope_name,
        file_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        arity=arity,
        code=snippet,
        comment=comment,
        summary=summary,
        note=note,
        exported=exported,
        return_type=return_type,
        param_types=param_types,
        react_role=react_role,
        middleware_kind=middleware_kind,
    ))

    if type_stack:
        relations.append(RelationEdge(
            source_id=_type_id("::".join(namespace_stack + type_stack)),
            source_label="Type",
            target_id=func_id,
            target_label="Function",
            rel_type="CONTAINS",
            properties={},
        ))
    elif namespace_stack:
        relations.append(RelationEdge(
            source_id=_namespace_id("::".join(namespace_stack)),
            source_label="Namespace",
            target_id=func_id,
            target_label="Function",
            rel_type="CONTAINS",
            properties={},
        ))

    call_root_node = calls_root or node
    for call_node in _iter_calls(call_root_node):
        callee = _extract_call_name(call_node, source_bytes)
        if not callee:
            continue
        calls.append(CallEdge(
            caller_id=func_id,
            caller_scope=scope_name,
            callee_name=callee,
            callee_id=None,
            callee_arity=_count_arguments(call_node),
        ))

    if react_role in {"screen", "component"}:
        for rendered_name in _collect_rendered_components(call_root_node, source_bytes):
            if rendered_name != name:
                renders.append(RenderEdge(renderer_id=func_id, rendered_name=rendered_name))

    _nav_raw = _collect_navigate_calls(snippet)
    if _nav_raw:
        _trigger = _classify_nav_context(snippet)
        _guard = _detect_nav_guard(snippet)
        for target_name, nav_method in _nav_raw:
            navigates.append(NavigateEdge(
                source_id=func_id,
                target_name=target_name,
                nav_method=nav_method,
                trigger_type=_trigger,
                guard=_guard,
            ))

    for route_name, comp_name in _collect_route_configs(snippet):
        navigates.append(NavigateEdge(
            source_id=func_id,
            target_name=route_name,
            nav_method="__route_config__",
            via=comp_name,
        ))


# ─── Tree walker ──────────────────────────────────────────────────────────────

def _walk_tree(
    node: Any,
    source_bytes: bytes,
    rel_path: str,
    namespace_stack: List[str],
    type_stack: List[str],
    namespaces: List[NamespaceDef],
    types: List[TypeDef],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    renders: List[RenderEdge],
    navigates: List[NavigateEdge],
    namespace_registry: Dict[str, NamespaceDef],
    type_registry: Dict[str, TypeDef],
    exported_context: bool,
    exported_names: Set[str],
) -> None:
    if node.type in {"export_statement", "export_default_declaration"}:
        decl = node.child_by_field_name("declaration")
        if decl is not None:
            _BARE_FUNC_TYPES = {
                "arrow_function", "function", "generator_function", "function_expression",
            }
            if decl.type in _BARE_FUNC_TYPES:
                explicit_name = _extract_name_field(decl, source_bytes)
                _record_function(
                    decl, source_bytes, rel_path,
                    namespace_stack, type_stack,
                    functions, relations, calls, renders, navigates,
                    name_override=explicit_name or _index_module_name(rel_path),
                    kind_override=(
                        "function_variable" if decl.type == "arrow_function" else None
                    ),
                    exported=True,
                )
                return
            if decl.type == "call_expression":
                default_name = _index_module_name(rel_path) or _anonymous_name("Function", decl)
                factory_name = _extract_root_factory_name(decl, source_bytes)
                kind = _CALL_EXPR_KIND_MAP.get(factory_name, "function_variable")
                inner_fn = _find_inner_function_arg(decl)
                _record_function(
                    decl, source_bytes, rel_path,
                    namespace_stack, type_stack,
                    functions, relations, calls, renders, navigates,
                    name_override=default_name,
                    kind_override=kind,
                    calls_root=decl,
                    parameters_node=inner_fn,
                    exported=True,
                )
                return
            _walk_tree(
                decl, source_bytes, rel_path,
                namespace_stack, type_stack,
                namespaces, types, functions, relations, calls, renders, navigates,
                namespace_registry, type_registry, True, exported_names,
            )
            return
        for spec in _find_nodes_by_type(node, "export_specifier"):
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                name_node = spec.child_by_field_name("value")
            if name_node is None:
                continue
            name = _node_text(name_node, source_bytes).strip()
            if name:
                exported_names.add(name)
            alias_node = spec.child_by_field_name("alias")
            if alias_node is not None:
                alias = _node_text(alias_node, source_bytes).strip()
                if alias:
                    exported_names.add(alias)
        return

    if node.type in _NAMESPACE_NODE_TYPES:
        name = _extract_name_field(node, source_bytes)
        if not name:
            name = _anonymous_name("Namespace", node)
        qualified = "::".join(namespace_stack + [name])
        ns_id = _namespace_id(qualified)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        comment = _extract_leading_comment(node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        namespaces.append(NamespaceDef(
            symbol_id=ns_id,
            qualified_name=qualified,
            name=name,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            code=snippet,
            comment=comment,
            summary=summary,
            note=note,
        ))
        namespace_registry[ns_id] = namespaces[-1]
        if namespace_stack:
            parent = _namespace_id("::".join(namespace_stack))
            relations.append(RelationEdge(
                source_id=parent,
                source_label="Namespace",
                target_id=ns_id,
                target_label="Namespace",
                rel_type="CONTAINS",
                properties={},
            ))
        for child in node.children:
            _walk_tree(
                child, source_bytes, rel_path,
                namespace_stack + [name], type_stack,
                namespaces, types, functions, relations, calls, renders, navigates,
                namespace_registry, type_registry, False, exported_names,
            )
        return

    if node.type in _TYPE_NODE_KINDS:
        kind = _TYPE_NODE_KINDS[node.type]
        name = _extract_name_field(node, source_bytes)
        if not name:
            name = _anonymous_name(kind.capitalize(), node)
            kind = f"anonymous_{kind}"
        qualified = (
            "::".join(namespace_stack + type_stack + [name])
            if (namespace_stack or type_stack)
            else name
        )
        tid = _type_id(qualified)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        comment = _extract_leading_comment(node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        types.append(TypeDef(
            symbol_id=tid,
            qualified_name=qualified,
            name=qualified.split("::")[-1],
            kind=kind,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            code=snippet,
            comment=comment,
            summary=summary,
            note=note,
            exported=exported_context,
        ))
        type_registry[tid] = types[-1]
        if namespace_stack:
            ns_id = _namespace_id("::".join(namespace_stack))
            relations.append(RelationEdge(
                source_id=ns_id,
                source_label="Namespace",
                target_id=tid,
                target_label="Type",
                rel_type="CONTAINS",
                properties={},
            ))
        if type_stack:
            parent_type = _type_id("::".join(namespace_stack + type_stack))
            relations.append(RelationEdge(
                source_id=parent_type,
                source_label="Type",
                target_id=tid,
                target_label="Type",
                rel_type="CONTAINS",
                properties={},
            ))
        for child in node.children:
            _walk_tree(
                child, source_bytes, rel_path,
                namespace_stack, type_stack + [name],
                namespaces, types, functions, relations, calls, renders, navigates,
                namespace_registry, type_registry, False, exported_names,
            )
        return

    if node.type in _FUNCTION_NODE_KINDS:
        _record_function(
            node, source_bytes, rel_path,
            namespace_stack, type_stack,
            functions, relations, calls, renders, navigates,
            exported=exported_context,
        )
        return

    if node.type in {"lexical_declaration", "variable_declaration"}:
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            init = child.child_by_field_name("value") or child.child_by_field_name("initializer")
            if init is None:
                continue
            if init.type in {"arrow_function", "function", "generator_function", "function_expression"}:
                name = _extract_name_field(child, source_bytes)
                _record_function(
                    child, source_bytes, rel_path,
                    namespace_stack, type_stack,
                    functions, relations, calls, renders, navigates,
                    name_override=name,
                    kind_override="function_variable",
                    calls_root=init,
                    parameters_node=init,
                    exported=exported_context,
                )
            elif init.type == "call_expression":
                name = _extract_name_field(child, source_bytes)
                if not name:
                    continue
                factory_name = _extract_root_factory_name(init, source_bytes)
                kind = _CALL_EXPR_KIND_MAP.get(factory_name, "function_variable")
                inner_fn = _find_inner_function_arg(init)
                _record_function(
                    child, source_bytes, rel_path,
                    namespace_stack, type_stack,
                    functions, relations, calls, renders, navigates,
                    name_override=name,
                    kind_override=kind,
                    calls_root=init,
                    parameters_node=inner_fn,
                    exported=exported_context,
                )

    for child in node.children:
        _walk_tree(
            child, source_bytes, rel_path,
            namespace_stack, type_stack,
            namespaces, types, functions, relations, calls, renders, navigates,
            namespace_registry, type_registry, exported_context, exported_names,
        )


# ─── TraversalAgent class facade ─────────────────────────────────────────────

class TraversalAgent:
    """Object-oriented facade over the module-level AST traversal functions."""

    def walk_tree(
        self,
        node: Any,
        source_bytes: bytes,
        rel_path: str,
        namespace_stack: List[str],
        type_stack: List[str],
        namespaces: List[NamespaceDef],
        types: List[TypeDef],
        functions: List[FunctionDef],
        relations: List[RelationEdge],
        calls: List[CallEdge],
        renders: List[RenderEdge],
        navigates: List[NavigateEdge],
        namespace_registry: Dict[str, NamespaceDef],
        type_registry: Dict[str, TypeDef],
        exported_context: bool,
        exported_names: Set[str],
    ) -> None:
        _walk_tree(
            node, source_bytes, rel_path,
            namespace_stack, type_stack,
            namespaces, types, functions, relations, calls, renders, navigates,
            namespace_registry, type_registry, exported_context, exported_names,
        )

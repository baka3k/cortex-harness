from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tree_sitter import Language, Parser

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

try:
    from tools.common.git_diff import load_manifest_paths
except Exception:  # pragma: no cover
    load_manifest_paths = None

from tools.common.incremental_cleanup import cleanup_neo4j_for_files
from tools.common.primary_vector_sync import (
    documents_from_rows,
    sync_vector_documents,
    vector_configured,
)
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args
from tools.graph.writer.language_writer import LanguageCodeWriter

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:  # pragma: no cover
    ts_get_parser = None


_GO_SOURCE_EXTENSIONS = (".go",)
_COMMENT_TYPES = {"comment"}
_FUNCTION_NODES = {"function_declaration", "method_declaration"}
_TYPE_NODES = {"type_spec", "type_alias"}
_CALL_NODES = {"call_expression"}
_BRANCH_NODES = {
    "if_statement": "if",
    "switch_statement": "switch",
    "type_switch_statement": "type_switch",
    "select_statement": "select",
    "case_clause": "case",
    "communication_case": "case",
    "default_case": "default",
}
_LOOP_NODES = {"for_statement", "range_clause"}
try:
    from tools.common.scan_ignore import COMMON_SCAN_EXCLUDE
except Exception:
    COMMON_SCAN_EXCLUDE = frozenset()

_SCAN_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vs",
    ".vscode",
    ".eclipse",
    ".settings",
    "bin",
    "build",
    "dist",
    "out",
    "target",
    "vendor",
    "node_modules",
    ".cache",
    ".parcel-cache",
    "__pycache__",
    "coverage",
    ".test-results",
    "test-results",
    "tmp",
    "temp",
    ".tmp",
    "tmpdir",
    ".DS_Store",
    "Thumbs.db",
} | COMMON_SCAN_EXCLUDE


@dataclass
class FileDef:
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    includes: List[str] = field(default_factory=list)
    using_namespaces: List[str] = field(default_factory=list)
    using_imports: List[str] = field(default_factory=list)
    macros: List[str] = field(default_factory=list)
    parse_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FunctionDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    scope_name: Optional[str]
    file_path: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    arity: int
    code: str
    comment: str = ""


@dataclass
class TypeDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""


@dataclass
class NamespaceDef:
    symbol_id: str
    qualified_name: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""


@dataclass
class FieldDef:
    symbol_id: str
    qualified_name: str
    name: str
    scope_name: Optional[str]
    type_signature: str
    file_path: str
    start_line: int
    end_line: int
    code: str


@dataclass
class AliasDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    target_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    code: str = ""


@dataclass
class TemplateDef:
    symbol_id: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    code: str = ""


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CallEdge:
    caller_id: str
    caller_file: str
    caller_scope: Optional[str]
    call_line: int
    call_column: int
    call_start_byte: int
    call_branch_kind: str
    call_loop_depth: int
    call_control_frames_json: str
    call_type: str
    call_arity: int
    callee_name: str
    callee_id: Optional[str] = None


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _line_from_byte(source_bytes: bytes, byte_index: int) -> int:
    return source_bytes[:byte_index].count(b"\n") + 1


def _node_snippet(node, source_bytes: bytes) -> Tuple[str, int, int]:
    start_byte = node.start_byte
    prev = node.prev_sibling
    while prev is not None and prev.type in _COMMENT_TYPES:
        start_byte = prev.start_byte
        prev = prev.prev_sibling
    return (
        source_bytes[start_byte : node.end_byte].decode("utf-8", errors="ignore"),
        _line_from_byte(source_bytes, start_byte),
        node.end_point[0] + 1,
    )


def _extract_comment(node, source_bytes: bytes) -> str:
    parts: List[str] = []
    prev = node.prev_sibling
    while prev is not None and prev.type in _COMMENT_TYPES:
        text = _node_text(prev, source_bytes).strip()
        if text:
            parts.append(text)
        prev = prev.prev_sibling
    return "\n".join(reversed(parts))


def _extract_file_comment(tree, source_bytes: bytes) -> str:
    parts: List[str] = []
    for child in tree.root_node.children:
        if child.type == "package_clause":
            break
        if child.type in _COMMENT_TYPES:
            parts.append(_node_text(child, source_bytes).strip())
        elif child.is_named:
            break
    return "\n".join(part for part in parts if part)


def _first_named_child(node, types: Optional[Iterable[str]] = None):
    allowed = set(types or [])
    for child in node.children:
        if child.is_named and (not allowed or child.type in allowed):
            return child
    return None


def _find_nodes_by_type(node, node_type: str) -> List[Any]:
    found: List[Any] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == node_type:
            found.append(current)
        stack.extend(reversed(current.children))
    return found


def _find_first_descendant(node, types: Iterable[str]):
    allowed = set(types)
    stack = list(reversed(node.children))
    while stack:
        current = stack.pop()
        if current.type in allowed:
            return current
        stack.extend(reversed(current.children))
    return None


def _field_text(node, field_name: str, source_bytes: bytes) -> str:
    child = node.child_by_field_name(field_name)
    return _node_text(child, source_bytes).strip() if child is not None else ""


def _extract_name(node, source_bytes: bytes) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes).strip()
    for node_type in ("identifier", "field_identifier", "type_identifier", "package_identifier"):
        found = _find_first_descendant(node, {node_type})
        if found is not None:
            return _node_text(found, source_bytes).strip()
    return None


def _scope_name(scope_stack: List[str]) -> Optional[str]:
    return ".".join(scope_stack) if scope_stack else None


def _qualified_name(scope_stack: List[str], name: str) -> str:
    return ".".join(scope_stack + [name]) if scope_stack else name


def _symbol_id(qualified_name: str, arity: int, rel_path: str) -> str:
    return f"{qualified_name}/{arity}@{rel_path}"


def _type_id(qualified_name: str) -> str:
    return qualified_name


def _namespace_id(qualified_name: str) -> str:
    return f"namespace::{qualified_name}"


def _anonymous_name(prefix: str, node) -> str:
    return f"Anonymous{prefix}@{node.start_point[0] + 1}:{node.start_point[1] + 1}"


def _set_parser_language(parser: Parser, language: Language) -> None:
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language


def _get_parser() -> Parser:
    try:
        from tree_sitter_go import language as go_language

        language = go_language()
        if not isinstance(language, Language):
            language = Language(language)
        parser = Parser()
        _set_parser_language(parser, language)
        return parser
    except Exception as go_exc:
        if ts_get_parser is not None:
            try:
                return ts_get_parser("go")
            except Exception:
                pass
        raise RuntimeError(
            "Go parser unavailable. Install 'tree-sitter-go' or a compatible "
            "'tree-sitter-languages' package."
        ) from go_exc


def _parse_file(path: str):
    parser = _get_parser()
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    return parser.parse(source_bytes), source_bytes


def _tree_error_stats(tree) -> Tuple[bool, int]:
    error_nodes = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_error or node.is_missing:
            error_nodes += 1
        stack.extend(node.children)
    return error_nodes > 0 or tree.root_node.has_error, error_nodes


def _package_name(tree, source_bytes: bytes) -> str:
    for child in tree.root_node.children:
        if child.type == "package_clause":
            name = _extract_name(child, source_bytes)
            return name or "main"
    return "main"


def _count_parameters(node, source_bytes: bytes) -> int:
    params = node.child_by_field_name("parameters")
    if params is None:
        params = _first_named_child(node, {"parameter_list"})
    if params is None:
        return 0
    count = 0
    for child in params.children:
        if child.type != "parameter_declaration":
            continue
        names = [
            item
            for item in child.children
            if item.type in {"identifier", "field_identifier"}
        ]
        count += len(names) if names else 1
    return count


def _count_arguments(node) -> int:
    args = node.child_by_field_name("arguments")
    if args is None:
        args = _first_named_child(node, {"argument_list"})
    if args is None:
        return 0
    return sum(
        1
        for child in args.children
        if child.is_named and child.type not in _COMMENT_TYPES
    )


def _receiver_scope(node, source_bytes: bytes) -> Optional[str]:
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    type_node = _find_first_descendant(receiver, {"type_identifier"})
    if type_node is not None:
        return _node_text(type_node, source_bytes).strip()
    text = _node_text(receiver, source_bytes)
    match = re.search(r"\*?\s*([A-Za-z_]\w*)\s*\)?\s*$", text.strip())
    return match.group(1) if match else None


def _type_kind(type_spec, source_bytes: bytes) -> str:
    type_node = type_spec.child_by_field_name("type")
    if type_node is None:
        type_node = _first_named_child(type_spec, {"struct_type", "interface_type"})
    if type_node is None:
        return "type"
    if type_node.type == "struct_type":
        return "struct"
    if type_node.type == "interface_type":
        return "interface"
    return "type"


def _is_type_alias(type_spec, source_bytes: bytes) -> bool:
    if type_spec.type == "type_alias":
        return True
    text = _node_text(type_spec, source_bytes)
    return bool(re.match(r"\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*=", text, re.DOTALL))


def _extract_alias_target(type_spec, source_bytes: bytes) -> Optional[str]:
    text = _node_text(type_spec, source_bytes)
    match = re.match(r"\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*=\s*(.*?)\s*$", text, re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def _extract_type_signature(node, source_bytes: bytes) -> str:
    type_node = node.child_by_field_name("type")
    if type_node is not None:
        return _node_text(type_node, source_bytes).strip()
    text = _node_text(node, source_bytes).strip().rstrip(",")
    parts = text.split()
    return parts[-1] if len(parts) > 1 else ""


def _collect_imports(tree, source_bytes: bytes) -> Tuple[List[str], List[str]]:
    imports: List[str] = []
    namespaces: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "import_spec"):
        path_node = node.child_by_field_name("path")
        if path_node is None:
            path_node = _find_first_descendant(node, {"interpreted_string_literal", "raw_string_literal"})
        if path_node is None:
            continue
        path = _node_text(path_node, source_bytes).strip().strip("`\"")
        if not path:
            continue
        imports.append(path)
        alias_node = node.child_by_field_name("name")
        if alias_node is not None:
            alias = _node_text(alias_node, source_bytes).strip()
            if alias not in {"_", "."}:
                namespaces.append(alias)
        else:
            namespaces.append(path.rsplit("/", 1)[-1])
    return sorted(set(namespaces)), sorted(set(imports))


def _extract_templates(node, rel_path: str, source_bytes: bytes) -> List[TemplateDef]:
    templates: List[TemplateDef] = []
    for template_node in _find_nodes_by_type(node, "type_parameter_list"):
        text = _node_text(template_node, source_bytes).strip()
        templates.append(
            TemplateDef(
                symbol_id=f"template::{rel_path}:{template_node.start_point[0] + 1}:{template_node.end_point[0] + 1}",
                name=text,
                file_path=rel_path,
                start_line=template_node.start_point[0] + 1,
                end_line=template_node.end_point[0] + 1,
                code=text,
            )
        )
    return templates


def _field_names(node, source_bytes: bytes) -> List[str]:
    names = [
        _node_text(child, source_bytes).strip()
        for child in node.children
        if child.type in {"field_identifier", "identifier"}
    ]
    return [name for name in names if name]


def _field_type_signature(node, source_bytes: bytes) -> str:
    type_node = node.child_by_field_name("type")
    if type_node is not None:
        return _node_text(type_node, source_bytes).strip()
    text = _node_text(node, source_bytes).strip().rstrip(",")
    names = _field_names(node, source_bytes)
    if names:
        prefix = ", ".join(names)
        return text[len(prefix) :].strip()
    return text


def _call_name(call_node, source_bytes: bytes) -> str:
    function_node = call_node.child_by_field_name("function")
    if function_node is None:
        return _node_text(call_node, source_bytes).split("(", 1)[0].strip()
    if function_node.type == "selector_expression":
        field = function_node.child_by_field_name("field")
        if field is not None:
            return _node_text(field, source_bytes).strip()
    text = _node_text(function_node, source_bytes).strip()
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _control_context(node) -> Tuple[str, int, str]:
    frames: List[Dict[str, Any]] = []
    branch_kind = "none"
    loop_depth = 0
    parent = node.parent
    while parent is not None:
        if parent.type in _BRANCH_NODES:
            kind = _BRANCH_NODES[parent.type]
            if branch_kind == "none":
                branch_kind = kind
            frames.append({"kind": kind, "line": parent.start_point[0] + 1})
        elif parent.type in _LOOP_NODES:
            loop_depth += 1
            frames.append({"kind": "loop", "line": parent.start_point[0] + 1})
        parent = parent.parent
    frames.reverse()
    return branch_kind, loop_depth, json.dumps(frames, ensure_ascii=False)


def _record_relation(
    relations: List[RelationEdge],
    source_id: str,
    source_label: str,
    target_id: str,
    target_label: str,
    rel_type: str,
    properties: Optional[Dict[str, Any]] = None,
) -> None:
    relations.append(
        RelationEdge(
            source_id=source_id,
            source_label=source_label,
            target_id=target_id,
            target_label=target_label,
            rel_type=rel_type,
            properties=properties or {},
        )
    )


def _add_type_use(
    owner_id: str,
    owner_label: str,
    type_text: str,
    rel_path: str,
    types: List[TypeDef],
    relations: List[RelationEdge],
    external_types: Dict[str, TypeDef],
) -> None:
    normalized = re.sub(r"[&*\\[\\](),{};]", " ", type_text)
    candidates = [
        part
        for part in re.split(r"\s+|\.", normalized)
        if part and part[:1].isupper()
    ]
    for candidate in candidates:
        target_id = _type_id(candidate)
        if target_id not in external_types:
            external_types[target_id] = TypeDef(
                symbol_id=target_id,
                qualified_name=candidate,
                name=candidate,
                kind="external",
                file_path=rel_path,
                start_line=0,
                end_line=0,
                code=candidate,
            )
            types.append(external_types[target_id])
        rel_type = "POINTER_TO" if "*" in type_text else "USES_TYPE"
        _record_relation(relations, owner_id, owner_label, target_id, "Type", rel_type, {})


def _walk_tree(
    node,
    source_bytes: bytes,
    rel_path: str,
    package_scope: str,
    scope_stack: List[str],
    namespaces: List[NamespaceDef],
    types: List[TypeDef],
    functions: List[FunctionDef],
    fields: List[FieldDef],
    aliases: List[AliasDef],
    templates: List[TemplateDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    type_registry: Dict[str, TypeDef],
    external_types: Dict[str, TypeDef],
    active_function: Optional[FunctionDef] = None,
) -> None:
    if node.type in _TYPE_NODES:
        name = _extract_name(node, source_bytes) or _anonymous_name("Type", node)
        qualified = _qualified_name([package_scope] + scope_stack, name)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        if _is_type_alias(node, source_bytes):
            alias = AliasDef(
                symbol_id=f"alias::{qualified}@{rel_path}",
                qualified_name=qualified,
                name=name,
                kind="type",
                target_name=_extract_alias_target(node, source_bytes),
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=snippet,
            )
            aliases.append(alias)
            if alias.target_name:
                _record_relation(relations, alias.symbol_id, "Alias", _type_id(alias.target_name), "Type", "ALIASES")
        else:
            type_id = _type_id(qualified)
            type_def = TypeDef(
                symbol_id=type_id,
                qualified_name=qualified,
                name=name,
                kind=_type_kind(node, source_bytes),
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=snippet,
                comment=_extract_comment(node, source_bytes),
            )
            if type_id not in type_registry:
                type_registry[type_id] = type_def
                types.append(type_def)
            _record_relation(relations, _namespace_id(package_scope), "Namespace", type_id, "Type", "DECLARES")
            for template in _extract_templates(node, rel_path, source_bytes):
                templates.append(template)
                _record_relation(relations, template.symbol_id, "Template", type_id, "Type", "TEMPLATES")
            child_scope = scope_stack + [name]
            for child in node.children:
                _walk_tree(
                    child,
                    source_bytes,
                    rel_path,
                    package_scope,
                    child_scope,
                    namespaces,
                    types,
                    functions,
                    fields,
                    aliases,
                    templates,
                    relations,
                    calls,
                    type_registry,
                    external_types,
                    active_function,
                )
            return

    if node.type in _FUNCTION_NODES:
        name = _extract_name(node, source_bytes) or _anonymous_name("Function", node)
        receiver_scope = _receiver_scope(node, source_bytes) if node.type == "method_declaration" else None
        function_scope = [package_scope] + ([receiver_scope] if receiver_scope else [])
        qualified = _qualified_name(function_scope, name)
        arity = _count_parameters(node, source_bytes)
        func = FunctionDef(
            symbol_id=_symbol_id(qualified, arity, rel_path),
            qualified_name=qualified,
            name=name,
            kind="method" if receiver_scope else "function",
            scope_name=".".join(function_scope) if receiver_scope else package_scope,
            file_path=rel_path,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            arity=arity,
            code=_node_text(node, source_bytes),
            comment=_extract_comment(node, source_bytes),
        )
        functions.append(func)
        if receiver_scope:
            owner_id = _type_id(_qualified_name([package_scope], receiver_scope))
            _record_relation(relations, owner_id, "Type", func.symbol_id, "Function", "DECLARES")
        else:
            _record_relation(relations, _namespace_id(package_scope), "Namespace", func.symbol_id, "Function", "DECLARES")
        for template in _extract_templates(node, rel_path, source_bytes):
            templates.append(template)
            _record_relation(relations, template.symbol_id, "Template", func.symbol_id, "Function", "TEMPLATES")
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
                package_scope,
                scope_stack,
                namespaces,
                types,
                functions,
                fields,
                aliases,
                templates,
                relations,
                calls,
                type_registry,
                external_types,
                func,
            )
        return

    if scope_stack and node.type == "field_declaration":
        owner = _qualified_name([package_scope], scope_stack[-1])
        type_signature = _field_type_signature(node, source_bytes)
        for name in _field_names(node, source_bytes):
            qualified = f"{owner}.{name}"
            field_id = f"field::{qualified}@{rel_path}"
            fields.append(
                FieldDef(
                    symbol_id=field_id,
                    qualified_name=qualified,
                    name=name,
                    scope_name=owner,
                    type_signature=type_signature,
                    file_path=rel_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    code=_node_text(node, source_bytes),
                )
            )
            _record_relation(relations, _type_id(owner), "Type", field_id, "Field", "DECLARES")
            if type_signature:
                _add_type_use(field_id, "Field", type_signature, rel_path, types, relations, external_types)

    if active_function is not None and node.type in _CALL_NODES:
        callee_name = _call_name(node, source_bytes)
        branch_kind, loop_depth, control_frames = _control_context(node)
        function_node = node.child_by_field_name("function")
        function_text = _node_text(function_node, source_bytes) if function_node is not None else ""
        calls.append(
            CallEdge(
                caller_id=active_function.symbol_id,
                caller_file=rel_path,
                caller_scope=active_function.scope_name,
                call_line=node.start_point[0] + 1,
                call_column=node.start_point[1] + 1,
                call_start_byte=node.start_byte,
                call_branch_kind=branch_kind,
                call_loop_depth=loop_depth,
                call_control_frames_json=control_frames,
                call_type="method" if "." in function_text else "function",
                call_arity=_count_arguments(node),
                callee_name=callee_name,
            )
        )

    for child in node.children:
        _walk_tree(
            child,
            source_bytes,
            rel_path,
            package_scope,
            scope_stack,
            namespaces,
            types,
            functions,
            fields,
            aliases,
            templates,
            relations,
            calls,
            type_registry,
            external_types,
            active_function,
        )


def _resolve_calls(functions: List[FunctionDef], calls: List[CallEdge], relations: List[RelationEdge]) -> None:
    by_name: Dict[str, List[FunctionDef]] = {}
    by_name_arity: Dict[Tuple[str, int], List[FunctionDef]] = {}
    for func in functions:
        by_name.setdefault(func.name, []).append(func)
        by_name_arity.setdefault((func.name, func.arity), []).append(func)
    for call in calls:
        candidates = by_name_arity.get((call.callee_name, call.call_arity)) or by_name.get(call.callee_name)
        if not candidates:
            continue
        if len(candidates) > 1 and call.caller_scope:
            scoped = [item for item in candidates if item.scope_name == call.caller_scope]
            if scoped:
                candidates = scoped
        if len(candidates) == 1:
            call.callee_id = candidates[0].symbol_id
            _record_relation(
                relations,
                call.caller_id,
                "Function",
                call.callee_id,
                "Function",
                "POSSIBLE_CALLS",
                {
                    "line": call.call_line,
                    "column": call.call_column,
                    "call_type": call.call_type,
                    "arity": call.call_arity,
                },
            )


def parse_go_file(path: str, root: Optional[str] = None) -> Dict[str, Any]:
    if root is None:
        root = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    rel_path = os.path.relpath(path, root).replace("\\", "/")
    tree, source_bytes = _parse_file(path)
    has_error, error_nodes = _tree_error_stats(tree)
    code = source_bytes.decode("utf-8", errors="ignore")
    package_scope = _package_name(tree, source_bytes)
    using_namespaces, using_imports = _collect_imports(tree, source_bytes)
    parse_meta = {
        "parser_language": "go_tree_sitter",
        "parser_language_initial": "go",
        "has_error": has_error,
        "error_nodes": error_nodes,
        "header_retry_attempted": False,
        "header_retry_selected": False,
        "header_retry_error_nodes": 0,
    }
    file_def = FileDef(
        file_path=rel_path,
        start_line=1,
        end_line=code.count("\n") + 1,
        code=code,
        comment=_extract_file_comment(tree, source_bytes),
        summary=_extract_file_comment(tree, source_bytes),
        includes=using_imports,
        using_namespaces=using_namespaces,
        using_imports=using_imports,
        macros=[],
        parse_meta=parse_meta,
    )
    namespace = NamespaceDef(
        symbol_id=_namespace_id(package_scope),
        qualified_name=package_scope,
        name=package_scope,
        file_path=rel_path,
        start_line=1,
        end_line=1,
        code=f"package {package_scope}",
        comment="",
    )
    namespaces: List[NamespaceDef] = [namespace]
    types: List[TypeDef] = []
    functions: List[FunctionDef] = []
    fields: List[FieldDef] = []
    aliases: List[AliasDef] = []
    templates: List[TemplateDef] = []
    relations: List[RelationEdge] = []
    calls: List[CallEdge] = []
    _walk_tree(
        tree.root_node,
        source_bytes,
        rel_path,
        package_scope,
        [],
        namespaces,
        types,
        functions,
        fields,
        aliases,
        templates,
        relations,
        calls,
        {},
        {},
    )
    for include in using_imports:
        _record_relation(
            relations,
            rel_path,
            "File",
            include,
            "ExternalModule",
            "INCLUDES",
            {},
        )
    _resolve_calls(functions, calls, relations)
    return {
        "functions": [asdict(item) for item in functions],
        "calls": [asdict(item) for item in calls],
        "types": [asdict(item) for item in types],
        "namespaces": [asdict(item) for item in namespaces],
        "relations": [asdict(item) for item in relations],
        "function_types": [],
        "fields": [asdict(item) for item in fields],
        "aliases": [asdict(item) for item in aliases],
        "templates": [asdict(item) for item in templates],
        "file_def": asdict(file_def),
        "using_namespaces": using_namespaces,
        "using_imports": using_imports,
        "includes": using_imports,
        "macros": [],
        "parse_meta": parse_meta,
    }


def _cache_key(path: str, root: str) -> str:
    stat = os.stat(path)
    value = f"{os.path.abspath(path)}:{os.path.abspath(root)}:{stat.st_mtime_ns}:{stat.st_size}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_or_parse_payload(
    path: str,
    root: str,
    cache_dir: Optional[str] = None,
    ignore_cache: bool = False,
) -> Dict[str, Any]:
    if not cache_dir:
        return parse_go_file(path, root)
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{_cache_key(path, root)}.json")
    if not ignore_cache and os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    payload = parse_go_file(path, root)
    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return payload


def _scan_go_files(root: str, selected_rel_paths: Optional[Iterable[str]] = None) -> List[str]:
    selected = {item.replace("\\", "/") for item in selected_rel_paths or [] if item}
    paths: List[str] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [item for item in dirnames if item not in _SCAN_SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(_GO_SOURCE_EXTENSIONS) or filename.endswith("_test.go"):
                continue
            path = os.path.join(current_root, filename)
            rel_path = os.path.relpath(path, root).replace("\\", "/")
            if selected and rel_path not in selected:
                continue
            paths.append(path)
    return sorted(paths)


def build_call_graph(
    root: str,
    output: Optional[str] = None,
    selected_rel_paths: Optional[Iterable[str]] = None,
    cache_dir: Optional[str] = None,
    ignore_cache: bool = False,
) -> Dict[str, Any]:
    files = _scan_go_files(root, selected_rel_paths)
    payloads = [
        _load_or_parse_payload(path, root, cache_dir=cache_dir, ignore_cache=ignore_cache)
        for path in files
    ]
    result = {"root": root, "files": payloads}
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    return result


def _build_note(code: str, comment: str = "", summary: str = "") -> str:
    parts: List[str] = []
    if summary:
        parts.append(f"Summary:\n{summary}")
    if comment:
        parts.append(f"Comment:\n{comment}")
    if code:
        parts.append(f"Code:\n{code}")
    return "\n\n".join(parts)


def _repo_name(project_name: str, root: str) -> str:
    return f"{project_name}/{os.path.basename(os.path.abspath(root))}"


def _with_common_fields(
    row: Dict[str, Any],
    *,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
) -> Dict[str, Any]:
    row.setdefault("summary", row.get("comment", ""))
    row.setdefault("note", _build_note(row.get("code", ""), row.get("comment", ""), row.get("summary", "")))
    row["project_id"] = project_id
    row["project_name"] = project_name
    row["language"] = language
    row["repo"] = repo
    row["build_system"] = build_system
    return row


def _prepare_write_rows(
    payloads: List[Dict[str, Any]],
    *,
    root: str,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
) -> Dict[str, List[Dict[str, Any]]]:
    del root
    files: List[Dict[str, Any]] = []
    namespaces: List[Dict[str, Any]] = []
    types: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    fields: List[Dict[str, Any]] = []
    aliases: List[Dict[str, Any]] = []
    templates: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    calls: List[Dict[str, Any]] = []

    for payload in payloads:
        file_def = dict(payload.get("file_def") or {})
        rel_path = (file_def.get("file_path") or "").replace("\\", "/")
        file_row = {
            "id": rel_path,
            "path": rel_path,
            "start_line": file_def.get("start_line", 1),
            "end_line": file_def.get("end_line", 1),
            "code": file_def.get("code", ""),
            "comment": file_def.get("comment", ""),
            "summary": file_def.get("summary", ""),
        }
        files.append(
            _with_common_fields(
                file_row,
                project_id=project_id,
                project_name=project_name,
                language=language,
                repo=repo,
                build_system=build_system,
            )
        )

        for item in payload.get("namespaces") or []:
            row = dict(item)
            row["id"] = row.pop("symbol_id", row.get("id", row.get("qualified_name")))
            namespaces.append(
                _with_common_fields(
                    row,
                    project_id=project_id,
                    project_name=project_name,
                    language=language,
                    repo=repo,
                    build_system=build_system,
                )
            )

        for item in payload.get("types") or []:
            row = dict(item)
            row["id"] = row.pop("symbol_id", row.get("id", row.get("qualified_name")))
            types.append(
                _with_common_fields(
                    row,
                    project_id=project_id,
                    project_name=project_name,
                    language=language,
                    repo=repo,
                    build_system=build_system,
                )
            )

        for item in payload.get("functions") or []:
            row = dict(item)
            row["id"] = row.pop("symbol_id", row.get("id", row.get("qualified_name")))
            row.setdefault("class_name", row.get("scope_name"))
            row.setdefault("package_name", row.get("scope_name"))
            functions.append(
                _with_common_fields(
                    row,
                    project_id=project_id,
                    project_name=project_name,
                    language=language,
                    repo=repo,
                    build_system=build_system,
                )
            )

        for item in payload.get("fields") or []:
            row = dict(item)
            row["id"] = row.pop("symbol_id", row.get("id", row.get("qualified_name")))
            fields.append(
                _with_common_fields(
                    row,
                    project_id=project_id,
                    project_name=project_name,
                    language=language,
                    repo=repo,
                    build_system=build_system,
                )
            )

        for item in payload.get("aliases") or []:
            row = dict(item)
            row["id"] = row.pop("symbol_id", row.get("id", row.get("qualified_name")))
            row.setdefault("code", "")
            aliases.append(
                _with_common_fields(
                    row,
                    project_id=project_id,
                    project_name=project_name,
                    language=language,
                    repo=repo,
                    build_system=build_system,
                )
            )

        for item in payload.get("templates") or []:
            row = dict(item)
            row["id"] = row.pop("symbol_id", row.get("id", row.get("name")))
            row.setdefault("code", row.get("name", ""))
            templates.append(
                _with_common_fields(
                    row,
                    project_id=project_id,
                    project_name=project_name,
                    language=language,
                    repo=repo,
                    build_system=build_system,
                )
            )

        for item in payload.get("relations") or []:
            relations.append(dict(item))
        for item in payload.get("calls") or []:
            row = dict(item)
            if row.get("callee_id"):
                calls.append(row)

    return {
        "files": files,
        "namespaces": namespaces,
        "types": types,
        "functions": functions,
        "fields": fields,
        "aliases": aliases,
        "templates": templates,
        "relations": relations,
        "calls": calls,
    }


async def _write_graph(args: argparse.Namespace, payloads: List[Dict[str, Any]]) -> Dict[str, int]:
    if not prepare_graph_args(args):
        if args.verbose:
            print("[graph] disabled; missing graph connection settings")
        return {}

    driver = await create_graph_driver_from_args(args)
    if driver is None:
        return {}
    try:
        writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )
        project_id = args.project_id or os.path.basename(os.path.abspath(args.root)) or "go-project"
        project_name = args.project_name or project_id
        language = args.language or "go"
        repo = args.repo or _repo_name(project_name, args.root)
        rows = _prepare_write_rows(
            payloads,
            root=args.root,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=args.build_system or "",
        )
        cleanup_targets = sorted(
            set(getattr(args, "_selected_rel_paths", []) or []) | set(getattr(args, "_deleted_rel_paths", []) or [])
        )
        if args.incremental and cleanup_targets:
            await cleanup_neo4j_for_files(
                driver=driver,
                database=args.neo4j_db,
                project_id=project_id,
                file_paths=cleanup_targets,
                verbose=args.verbose,
            )
        return await writer.write_all(
            namespaces=rows["namespaces"],
            files=rows["files"],
            types=rows["types"],
            functions=rows["functions"],
            fields=rows["fields"],
            aliases=rows["aliases"],
            templates=rows["templates"],
            relations=rows["relations"],
            calls=rows["calls"],
            use_full_writers=True,
        )
    finally:
        close = getattr(driver, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result


def _sync_vectors(args: argparse.Namespace, payloads: List[Dict[str, Any]]) -> int:
    if not vector_configured(args.qdrant_url):
        return 0
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root)) or "go-project"
    project_name = args.project_name or project_id
    repo = args.repo or _repo_name(project_name, args.root)
    rows = _prepare_write_rows(
        payloads,
        root=args.root,
        project_id=project_id,
        project_name=project_name,
        language=args.language or "go",
        repo=repo,
        build_system=args.build_system or "",
    )
    documents = documents_from_rows(rows, parser="go", root_scope=repo, max_chars=args.max_embed_chars)
    cleanup_targets = sorted(
        set(getattr(args, "_selected_rel_paths", []) or [])
        | set(getattr(args, "_deleted_rel_paths", []) or [])
    )
    if not getattr(args, "_scanned_directory", False) and not cleanup_targets:
        cleanup_targets = sorted({item.payload["file_path"] for item in documents})
    return sync_vector_documents(
        documents,
        url=args.qdrant_url,
        collection=args.qdrant_collection,
        model_name=args.embed_model or "jinaai/jina-embeddings-v3",
        device=args.device,
        embed_batch_size=args.batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        parser="go",
        project_id=project_id,
        root_scope=repo,
        cleanup_paths=cleanup_targets,
        full_replace=not args.incremental and getattr(args, "_scanned_directory", False),
        timeout=args.qdrant_timeout,
        retries=args.qdrant_retries,
        retry_sleep=args.qdrant_retry_sleep,
        verbose=args.verbose,
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Go tree-sitter parser/extractor")
    parser.add_argument("path", nargs="?", help="Go source file or directory")
    parser.add_argument("--root", default=None, help="Project root for relative file paths")
    parser.add_argument("--output", "-o", default=None, help="Write JSON payload to this file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    add_graph_provider_args(parser)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_CODE_PATH"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "go_functions"))
    parser.add_argument("--embed-model", default=os.environ.get("CODE_EMBEDDING_MODEL", ""))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", "8")))
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", "4000")))
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.add_argument("--cache-dir", default=os.environ.get("GO_ANALYZER_CACHE_DIR"))
    parser.set_defaults(enable_message_scan=False)
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION", ""))
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE", "go"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", "go"))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--incremental", action="store_true", help="Enable incremental scan mode")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not args.path and not args.root:
        print("Either path or --root is required", file=sys.stderr)
        return 2
    path = os.path.abspath(args.path or args.root)
    args._scanned_directory = os.path.isdir(path)
    if os.path.isdir(path):
        args.root = os.path.abspath(args.root or path)
        selected_rel_paths: Optional[List[str]] = None
        deleted_rel_paths: List[str] = []
        if args.incremental and args.changed_files_manifest:
            if load_manifest_paths is None:
                print("Incremental mode requires tools.common.git_diff", file=sys.stderr)
                return 2
            selected_rel_paths = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
            selected_rel_paths = [item for item in selected_rel_paths if item.endswith(_GO_SOURCE_EXTENSIONS)]
        if args.incremental and args.deleted_files_manifest:
            if load_manifest_paths is None:
                print("Incremental mode requires tools.common.git_diff", file=sys.stderr)
                return 2
            deleted_rel_paths = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
            deleted_rel_paths = [item for item in deleted_rel_paths if item.endswith(_GO_SOURCE_EXTENSIONS)]
        args._selected_rel_paths = selected_rel_paths or []
        args._deleted_rel_paths = deleted_rel_paths
        result = build_call_graph(
            args.root,
            args.output,
            selected_rel_paths=selected_rel_paths,
            cache_dir=args.cache_dir,
            ignore_cache=args.ignore_cache,
        )
        payloads = result["files"]
    else:
        root = os.path.abspath(args.root) if args.root else os.path.dirname(path)
        args.root = root
        result = _load_or_parse_payload(
            path,
            root,
            cache_dir=args.cache_dir,
            ignore_cache=args.ignore_cache,
        )
        payloads = [result]
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    try:
        counts = await _write_graph(args, payloads)
    except Exception as exc:
        print(f"Go graph persistence failed: {exc}", file=sys.stderr)
        return 3
    if counts and args.verbose:
        print(f"[graph] written {counts}")
    try:
        vector_count = _sync_vectors(args, payloads)
    except Exception as exc:
        print(f"Go vector persistence failed: {exc}", file=sys.stderr)
        return 4
    if args.pretty or not counts:
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    total_functions = sum(len(payload.get("functions") or []) for payload in payloads)
    total_types = sum(len(payload.get("types") or []) for payload in payloads)
    print(
        f"[SCAN_RESULT] parser=go files={len(payloads)} functions={total_functions} classes={total_types} "
        f"vectors={vector_count} vector_status={'success' if vector_configured(args.qdrant_url) else 'disabled'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

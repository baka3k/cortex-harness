from __future__ import annotations

import argparse
import asyncio
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

from tools.common.git_diff import load_manifest_paths
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


_SWIFT_SOURCE_EXTENSIONS = (".swift",)
_COMMENT_TYPES = {"comment", "multiline_comment"}
_TYPE_NODES = {"class_declaration", "protocol_declaration"}
_FUNCTION_NODES = {
    "function_declaration",
    "protocol_function_declaration",
    "init_declaration",
    "deinit_declaration",
    "subscript_declaration",
}
_ALIAS_NODES = {"typealias_declaration", "associatedtype_declaration"}
_CALL_NODES = {"call_expression", "constructor_expression", "macro_invocation"}
_BRANCH_NODES = {
    "if_statement": "if",
    "guard_statement": "guard",
    "switch_statement": "switch",
    "catch_block": "catch",
    "do_statement": "do",
}
_LOOP_NODES = {"for_statement", "while_statement", "repeat_while_statement", "repeat"}

_SCAN_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    # Python virtual environments
    ".venv", "venv", "env", ".env", "virtualenv",
    "env.bak", "venv.bak", ".env.bak", ".venv.bak",
    "site-packages",
    ".idea",
    ".vs",
    ".vscode",
    ".eclipse",
    ".settings",
    ".build",
    "build",
    "out",
    "bin",
    "obj",
    "DerivedData",
    ".swiftpm",
    "Packages",
    "node_modules",
    "dist",
    "target",
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
}


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
    snippet = source_bytes[start_byte : node.end_byte].decode("utf-8", errors="ignore")
    return snippet, _line_from_byte(source_bytes, start_byte), node.end_point[0] + 1


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
        if child.type in _COMMENT_TYPES:
            text = _node_text(child, source_bytes).strip()
            if text:
                parts.append(text)
            continue
        if child.is_named:
            break
    return "\n".join(parts)


def _find_nodes_by_type(node, node_type: str) -> Iterable:
    cursor = node.walk()
    while True:
        if cursor.node.type == node_type:
            yield cursor.node
        if cursor.goto_first_child():
            continue
        if cursor.goto_next_sibling():
            continue
        while cursor.goto_parent():
            if cursor.goto_next_sibling():
                break
        else:
            return


def _first_named_child(node, types: Iterable[str]):
    wanted = set(types)
    for child in node.children:
        if child.is_named and child.type in wanted:
            return child
    return None


def _first_descendant(node, types: Iterable[str]):
    wanted = set(types)
    for child in node.children:
        if child.is_named and child.type in wanted:
            return child
        found = _first_descendant(child, wanted)
        if found is not None:
            return found
    return None


def _tree_error_stats(tree) -> Tuple[bool, int]:
    count = 0
    for node in _find_nodes_by_type(tree.root_node, "ERROR"):
        count += 1
    return bool(getattr(tree.root_node, "has_error", False) or count), count


def _extract_name(node, source_bytes: bytes) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        if name_node.type == "pattern":
            bound = name_node.child_by_field_name("bound_identifier")
            if bound is not None:
                return _node_text(bound, source_bytes).strip()
        text = _node_text(name_node, source_bytes).strip()
        if text:
            return _clean_identifier(text)

    bound = node.child_by_field_name("bound_identifier")
    if bound is not None:
        return _node_text(bound, source_bytes).strip()

    found = _first_named_child(node, {"type_identifier", "simple_identifier", "identifier"})
    if found is None:
        found = _first_descendant(node, {"type_identifier", "simple_identifier", "identifier"})
    if found is not None:
        return _clean_identifier(_node_text(found, source_bytes).strip())
    return None


def _clean_identifier(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if "." in text:
        text = text.split(".")[-1]
    return text.strip("`")


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
    elif hasattr(parser, "language"):
        parser.language = language
    else:
        raise RuntimeError("Unsupported tree_sitter Parser API")


def _get_parser() -> Parser:
    if ts_get_parser is not None:
        try:
            return ts_get_parser("swift")
        except Exception:
            pass
    try:
        from tree_sitter_swift import language as swift_language
    except Exception as exc:
        raise RuntimeError(
            "Swift parser unavailable. Install 'tree-sitter-swift' or a compatible "
            "'tree-sitter-languages' package."
        ) from exc
    language = swift_language()
    if not isinstance(language, Language):
        language = Language(language)
    parser = Parser()
    _set_parser_language(parser, language)
    return parser


def _parse_file(path: str):
    parser = _get_parser()
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    return parser.parse(source_bytes), source_bytes


def _count_parameters(node) -> int:
    params = [child for child in node.children if child.is_named and child.type == "parameter"]
    return len(params)


def _top_level_argument_count(text: str) -> int:
    text = text.strip()
    if not text:
        return 0
    depth = 0
    count = 1
    in_string: Optional[str] = None
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in {"'", '"'}:
            in_string = char
            continue
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            count += 1
    return count


def _count_arguments(node, source_bytes: bytes) -> int:
    text = _node_text(node, source_bytes)
    match = re.search(r"\((.*)\)\s*$", text, re.DOTALL)
    if not match:
        suffix = _first_named_child(node, {"call_suffix", "constructor_suffix"})
        if suffix is not None:
            match = re.search(r"\((.*)\)", _node_text(suffix, source_bytes), re.DOTALL)
    return _top_level_argument_count(match.group(1)) if match else 0


def _extract_type_signature(node, source_bytes: bytes) -> str:
    type_node = node.child_by_field_name("type")
    if type_node is not None:
        return _node_text(type_node, source_bytes).strip()
    annotation = _first_named_child(node, {"type_annotation"})
    if annotation is not None:
        type_node = annotation.child_by_field_name("type") or annotation.child_by_field_name("name")
        if type_node is not None:
            return _node_text(type_node, source_bytes).strip()
        text = _node_text(annotation, source_bytes).strip()
        if ":" in text:
            return text.split(":", 1)[1].strip()
    text = _node_text(node, source_bytes).strip()
    if ":" in text:
        return text.split(":", 1)[1].split("=", 1)[0].strip()
    return ""


def _extract_alias_target(node, source_bytes: bytes) -> Optional[str]:
    value_node = node.child_by_field_name("value")
    if value_node is not None:
        return _node_text(value_node, source_bytes).strip()
    text = _node_text(node, source_bytes)
    match = re.search(r"=\s*(.*)$", text, re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return None


def _extract_decl_kind(node, source_bytes: bytes) -> str:
    kind_node = node.child_by_field_name("declaration_kind")
    if kind_node is not None:
        text = _node_text(kind_node, source_bytes).strip()
        if text:
            return text
    text = _node_text(node, source_bytes)
    match = re.search(r"\b(actor|class|struct|enum|extension|protocol)\b", text)
    if match:
        return match.group(1)
    return "record"


def _normalize_type_kind(kind: str) -> str:
    if kind == "protocol":
        return "interface"
    if kind == "actor":
        return "class"
    if kind == "extension":
        return "record"
    if kind in {"class", "struct", "enum"}:
        return kind
    return "record"


def _collect_imports(tree, source_bytes: bytes) -> Tuple[List[str], List[str]]:
    using_imports: List[str] = []
    using_namespaces: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "import_declaration"):
        identifiers = [
            _node_text(child, source_bytes).strip()
            for child in node.children
            if child.is_named and child.type == "identifier"
        ]
        path = ".".join(part for part in identifiers if part)
        if not path:
            text = _node_text(node, source_bytes).strip()
            path = re.sub(r"^import\s+(class|struct|enum|protocol|func|var|typealias)?\s*", "", text).strip()
        if path:
            using_imports.append(path)
            using_namespaces.append(path.split(".")[0])
    return sorted(set(using_namespaces)), using_imports


def _collect_macros(tree, source_bytes: bytes) -> List[str]:
    macros: List[str] = []
    for node_type in ("macro_declaration", "macro_invocation"):
        for node in _find_nodes_by_type(tree.root_node, node_type):
            name = _extract_name(node, source_bytes) or _call_name(node, source_bytes)
            if name and name not in macros:
                macros.append(name)
    return macros


def _call_name(call_node, source_bytes: bytes) -> str:
    if call_node.type == "macro_invocation":
        text = _node_text(call_node, source_bytes).strip()
        match = re.match(r"#?([A-Za-z_][A-Za-z0-9_]*)", text)
        return match.group(1) if match else _anonymous_name("Macro", call_node)
    if call_node.type == "constructor_expression":
        name = _extract_name(call_node, source_bytes)
        if name:
            return name
    text = _node_text(call_node, source_bytes).strip()
    text = re.sub(r"\s+", " ", text)
    before_paren = text.split("(", 1)[0].strip()
    before_paren = before_paren.split("?")[-1].split("!")[-1]
    if "." in before_paren:
        before_paren = before_paren.split(".")[-1]
    before_paren = before_paren.strip("`#")
    return before_paren or _anonymous_name("Call", call_node)


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
    rel_type: str = "USES_TYPE",
) -> None:
    type_name = re.sub(r"[<>\[\](),:?!=&|]", " ", type_text)
    type_name = re.sub(r"\b(any|some|inout|async|throws|rethrows|where|Self|self)\b", " ", type_name)
    candidates = [part for part in re.split(r"\s+|\.", type_name) if part and part[:1].isupper()]
    for candidate in candidates:
        target_id = _type_id(candidate)
        type_exists = any(item.symbol_id == target_id for item in types)
        if target_id not in external_types and not type_exists:
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
        _record_relation(relations, owner_id, owner_label, target_id, "Type", rel_type, {})


def _record_templates(
    node,
    source_bytes: bytes,
    rel_path: str,
    owner_id: str,
    owner_label: str,
    templates: List[TemplateDef],
    relations: List[RelationEdge],
) -> None:
    for template_node in _find_nodes_by_type(node, "type_parameters"):
        template_id = f"template::{rel_path}:{template_node.start_point[0] + 1}:{template_node.end_point[0] + 1}"
        templates.append(
            TemplateDef(
                symbol_id=template_id,
                name=_node_text(template_node, source_bytes).strip(),
                file_path=rel_path,
                start_line=template_node.start_point[0] + 1,
                end_line=template_node.end_point[0] + 1,
                code=_node_text(template_node, source_bytes).strip(),
            )
        )
        _record_relation(relations, template_id, "Template", owner_id, owner_label, "TEMPLATES")


def _walk_tree(
    node,
    source_bytes: bytes,
    rel_path: str,
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
        qualified = _qualified_name(scope_stack, name)
        type_id = _type_id(qualified)
        kind = "protocol" if node.type == "protocol_declaration" else _extract_decl_kind(node, source_bytes)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        type_def = TypeDef(
            symbol_id=type_id,
            qualified_name=qualified,
            name=name,
            kind=_normalize_type_kind(kind),
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            code=snippet,
            comment=_extract_comment(node, source_bytes),
        )
        if type_id not in type_registry:
            type_registry[type_id] = type_def
            types.append(type_def)
        owner_scope = _scope_name(scope_stack)
        if owner_scope:
            owner_id = _type_id(owner_scope) if owner_scope in type_registry else _namespace_id(owner_scope)
            owner_label = "Type" if owner_scope in type_registry else "Namespace"
            _record_relation(relations, owner_id, owner_label, type_id, "Type", "DECLARES")
        for inherit in _find_nodes_by_type(node, "inheritance_specifier"):
            inherit_node = inherit.child_by_field_name("inherits_from") or inherit
            _add_type_use(type_id, "Type", _node_text(inherit_node, source_bytes), rel_path, types, relations, external_types, "EXTENDS")
        _record_templates(node, source_bytes, rel_path, type_id, "Type", templates, relations)
        child_scope = scope_stack + [name]
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
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
        if node.type == "init_declaration":
            name = "init"
            kind = "constructor"
        elif node.type == "deinit_declaration":
            name = "deinit"
            kind = "destructor"
        elif node.type == "subscript_declaration":
            name = "subscript"
            kind = "method" if scope_stack else "function"
        else:
            name = _extract_name(node, source_bytes) or _anonymous_name("Function", node)
            kind = "declaration" if node.type == "protocol_function_declaration" else "method" if scope_stack else "function"
        arity = _count_parameters(node)
        qualified = _qualified_name(scope_stack, name)
        func = FunctionDef(
            symbol_id=_symbol_id(qualified, arity, rel_path),
            qualified_name=qualified,
            name=name,
            kind=kind,
            scope_name=_scope_name(scope_stack),
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
        owner_scope = _scope_name(scope_stack)
        if owner_scope:
            owner_id = _type_id(owner_scope) if owner_scope in type_registry else _namespace_id(owner_scope)
            owner_label = "Type" if owner_scope in type_registry else "Namespace"
            _record_relation(relations, owner_id, owner_label, func.symbol_id, "Function", "DECLARES")
        return_type = node.child_by_field_name("return_type")
        if return_type is not None:
            _add_type_use(func.symbol_id, "Function", _node_text(return_type, source_bytes), rel_path, types, relations, external_types)
        for param in [child for child in node.children if child.is_named and child.type == "parameter"]:
            param_type = _extract_type_signature(param, source_bytes)
            if param_type:
                _add_type_use(func.symbol_id, "Function", param_type, rel_path, types, relations, external_types)
        _record_templates(node, source_bytes, rel_path, func.symbol_id, "Function", templates, relations)
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
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

    if node.type in _ALIAS_NODES:
        name = _extract_name(node, source_bytes) or _anonymous_name("Alias", node)
        qualified = _qualified_name(scope_stack, name)
        target = _extract_alias_target(node, source_bytes)
        alias = AliasDef(
            symbol_id=f"alias::{qualified}@{rel_path}",
            qualified_name=qualified,
            name=name,
            kind="type",
            target_name=target,
            file_path=rel_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            code=_node_text(node, source_bytes),
        )
        aliases.append(alias)
        owner_scope = _scope_name(scope_stack)
        if owner_scope:
            owner_id = _type_id(owner_scope) if owner_scope in type_registry else _namespace_id(owner_scope)
            owner_label = "Type" if owner_scope in type_registry else "Namespace"
            _record_relation(relations, owner_id, owner_label, alias.symbol_id, "Alias", "DECLARES")
        if target:
            _record_relation(relations, alias.symbol_id, "Alias", _type_id(target), "Type", "ALIASES")
            _add_type_use(alias.symbol_id, "Alias", target, rel_path, types, relations, external_types)

    if active_function is not None and node.type in _CALL_NODES:
        branch_kind, loop_depth, control_frames = _control_context(node)
        callee_name = _call_name(node, source_bytes)
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
                call_type="macro" if node.type == "macro_invocation" else "constructor" if node.type == "constructor_expression" else "function",
                call_arity=_count_arguments(node, source_bytes),
                callee_name=callee_name,
            )
        )

    if active_function is None and scope_stack and node.type in {"property_declaration", "protocol_property_declaration"}:
        name = _extract_name(node, source_bytes)
        if name:
            owner = _scope_name(scope_stack)
            qualified = f"{owner}.{name}" if owner else name
            field_id = f"field::{qualified}@{rel_path}"
            type_signature = _extract_type_signature(node, source_bytes)
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
            if owner:
                _record_relation(relations, _type_id(owner), "Type", field_id, "Field", "DECLARES")
            if type_signature:
                _add_type_use(field_id, "Field", type_signature, rel_path, types, relations, external_types)

    for child in node.children:
        _walk_tree(
            child,
            source_bytes,
            rel_path,
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


def parse_swift_file(path: str, root: Optional[str] = None) -> Dict[str, Any]:
    if root is None:
        root = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    from tools.common._rust_accel import extract as _rust_extract

    payload = _rust_extract("swift", path, root)
    if payload is not None:
        return payload
    return _python_parse_swift_file(path, root)


def _python_parse_swift_file(path: str, root: Optional[str] = None) -> Dict[str, Any]:
    if root is None:
        root = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    rel_path = os.path.relpath(path, root).replace("\\", "/")
    tree, source_bytes = _parse_file(path)
    has_error, error_nodes = _tree_error_stats(tree)
    code = source_bytes.decode("utf-8", errors="ignore")
    comment = _extract_file_comment(tree, source_bytes)
    using_namespaces, using_imports = _collect_imports(tree, source_bytes)
    includes = list(using_imports)
    macros = _collect_macros(tree, source_bytes)
    parse_meta = {
        "parser_language": "swift_tree_sitter",
        "parser_language_initial": "swift",
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
        comment=comment,
        summary=comment,
        includes=includes,
        using_namespaces=using_namespaces,
        using_imports=using_imports,
        macros=macros,
        parse_meta=parse_meta,
    )
    namespaces: List[NamespaceDef] = []
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
        "includes": includes,
        "macros": macros,
        "parse_meta": parse_meta,
    }


def _scan_swift_files(root: str, selected_rel_paths: Optional[Iterable[str]] = None) -> List[str]:
    selected = {item.replace("\\", "/") for item in selected_rel_paths or [] if item}
    paths: List[str] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [item for item in dirnames if item not in _SCAN_SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(_SWIFT_SOURCE_EXTENSIONS):
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
) -> Dict[str, Any]:
    files = _scan_swift_files(root, selected_rel_paths)
    payloads = [parse_swift_file(path, root) for path in files]
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
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
) -> Dict[str, List[Dict[str, Any]]]:
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
        files.append(
            _with_common_fields(
                {
                    "id": rel_path,
                    "path": rel_path,
                    "start_line": file_def.get("start_line", 1),
                    "end_line": file_def.get("end_line", 1),
                    "code": file_def.get("code", ""),
                    "comment": file_def.get("comment", ""),
                    "summary": file_def.get("summary", ""),
                },
                project_id=project_id,
                project_name=project_name,
                language=language,
                repo=repo,
                build_system=build_system,
            )
        )

        for collection_name, target in (
            ("namespaces", namespaces),
            ("types", types),
            ("functions", functions),
            ("fields", fields),
            ("aliases", aliases),
            ("templates", templates),
        ):
            for item in payload.get(collection_name) or []:
                row = dict(item)
                row["id"] = row.pop("symbol_id", row.get("id", row.get("qualified_name", row.get("name"))))
                if collection_name == "functions":
                    row.setdefault("class_name", row.get("scope_name"))
                    row.setdefault("package_name", None)
                if collection_name in {"aliases", "templates"}:
                    row.setdefault("code", row.get("name", ""))
                target.append(
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
        project_id = args.project_id or os.path.basename(os.path.abspath(args.root)) or "swift-project"
        project_name = args.project_name or project_id
        language = args.language or "swift"
        repo = args.repo or _repo_name(project_name, args.root)
        rows = _prepare_write_rows(
            payloads,
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
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root)) or "swift-project"
    project_name = args.project_name or project_id
    repo = args.repo or _repo_name(project_name, args.root)
    rows = _prepare_write_rows(
        payloads,
        project_id=project_id,
        project_name=project_name,
        language=args.language or "swift",
        repo=repo,
        build_system=args.build_system or "",
    )
    documents = documents_from_rows(rows, parser="swift", root_scope=repo, max_chars=args.max_embed_chars)
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
        parser="swift",
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
    parser = argparse.ArgumentParser(description="Swift tree-sitter parser/extractor")
    parser.add_argument("path", nargs="?", help="Swift source file or directory")
    parser.add_argument("--root", default=None, help="Project root for relative file paths")
    parser.add_argument("--output", "-o", default=None, help="Write JSON payload to this file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON to stdout")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    add_graph_provider_args(parser)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "swift_functions"))
    parser.add_argument("--embed-model", default=os.environ.get("CODE_EMBEDDING_MODEL", ""))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", "4")))
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", "4000")))
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.set_defaults(enable_message_scan=False)
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION", ""))
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE", "swift"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", "swiftpm"))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true", help="Enable incremental ingestion mode")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--ignore-cache", action="store_true")
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
        if args.incremental:
            if args.changed_files_manifest:
                selected_rel_paths = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
            if args.deleted_files_manifest:
                deleted_rel_paths = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
            if selected_rel_paths is not None:
                selected_rel_paths = [item for item in selected_rel_paths if item.endswith(_SWIFT_SOURCE_EXTENSIONS)]
            deleted_rel_paths = [item for item in deleted_rel_paths if item.endswith(_SWIFT_SOURCE_EXTENSIONS)]
        args._selected_rel_paths = selected_rel_paths or []
        args._deleted_rel_paths = deleted_rel_paths
        result = build_call_graph(args.root, args.output, selected_rel_paths)
        payloads = result["files"]
    else:
        root = os.path.abspath(args.root) if args.root else os.path.dirname(path)
        args.root = root
        result = parse_swift_file(path, root)
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
        print(f"Swift graph persistence failed: {exc}", file=sys.stderr)
        return 3
    if counts and args.verbose:
        print(f"[graph] written {counts}")
    try:
        vector_count = _sync_vectors(args, payloads)
    except Exception as exc:
        print(f"Swift vector persistence failed: {exc}", file=sys.stderr)
        return 4
    if args.pretty or not counts:
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    scan_functions = sum(len(payload.get("functions") or []) for payload in payloads)
    scan_types = sum(len(payload.get("types") or []) for payload in payloads)
    print(
        f"[SCAN_RESULT] parser=swift files={len(payloads)} functions={scan_functions} classes={scan_types} "
        f"vectors={vector_count} vector_status={'success' if vector_configured(args.qdrant_url) else 'disabled'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

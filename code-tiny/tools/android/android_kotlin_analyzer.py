from __future__ import annotations

import argparse
import asyncio
import json
import hashlib
import os
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import torch
from transformers import AutoModel, AutoTokenizer
from tree_sitter import Language, Parser

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.analyzer_cache import (
    file_signature,
    load_parse_cache,
    safe_cache_root,
    write_parse_cache,
)
from tools.common.cloc_stats import collect_cloc_stats, normalize_cloc_payload
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.writer.language_writer import LanguageCodeWriter

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:
    ts_get_parser = None


@dataclass
class FunctionDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    class_name: Optional[str]
    package_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    arity: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class PackageDef:
    name: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


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
    summary: str = ""
    note: str = ""


@dataclass
class FileDef:
    file_path: str
    package_name: Optional[str]
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class ClassDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    package_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class FunctionTypeDef:
    symbol_id: str
    type_signature: str
    file_path: str
    start_line: int
    end_line: int
    code: str


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, str]


@dataclass
class TypeEdge:
    source_id: str
    source_package: Optional[str]
    target_name: str
    rel_type: str
    target_id: Optional[str]


@dataclass
class CallEdge:
    caller_id: str
    caller_file: str
    caller_package: Optional[str]
    caller_class: Optional[str]
    imports: List[str]
    call_line: int
    call_column: int
    call_type: str
    callee_name: str
    callee_id: Optional[str]


@dataclass
class AndroidManifestDef:
    symbol_id: str
    package_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    code: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidComponentDef:
    symbol_id: str
    name: str
    component_type: str
    class_name: Optional[str]
    exported: Optional[bool]
    process: Optional[str]
    permission: Optional[str]
    enabled: Optional[bool]
    direct_boot_aware: Optional[bool]
    target_activity: Optional[str]
    intent_actions: List[str]
    intent_categories: List[str]
    intent_data: List[str]
    file_path: str
    start_line: int
    end_line: int
    code: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidResourceDef:
    symbol_id: str
    name: str
    res_type: str
    file_path: str
    qualifier: str
    summary: str = ""
    note: str = ""


@dataclass
class GradleModuleDef:
    symbol_id: str
    name: str
    module_path: str
    module_type: str
    namespace: Optional[str]
    application_id: Optional[str]
    file_path: str
    summary: str = ""
    note: str = ""


@dataclass
class GradleDependencyDef:
    symbol_id: str
    coordinate: str
    group: Optional[str]
    artifact: Optional[str]
    version: Optional[str]
    summary: str = ""
    note: str = ""


@dataclass
class AndroidAnnotationDef:
    symbol_id: str
    name: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidNavRouteDef:
    symbol_id: str
    route: str
    file_path: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidIntentActionDef:
    symbol_id: str
    action: str
    summary: str = ""
    note: str = ""


@dataclass
class AndroidHandlerMessageDef:
    symbol_id: str
    token: str
    summary: str = ""
    note: str = ""


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _extract_leading_comment(node, source_bytes: bytes) -> str:
    comment_parts: List[str] = []
    prev = node.prev_sibling
    while prev is not None and prev.type in {"line_comment", "block_comment", "multiline_comment"}:
        text = _node_text(prev, source_bytes).strip()
        if text:
            comment_parts.append(text)
        prev = prev.prev_sibling
    if not comment_parts:
        return ""
    return "\n".join(reversed(comment_parts))


def _extract_file_comment(tree, source_bytes: bytes) -> str:
    comment_parts: List[str] = []
    for child in tree.root_node.children:
        if child.type in {"line_comment", "block_comment", "multiline_comment"}:
            text = _node_text(child, source_bytes).strip()
            if text:
                comment_parts.append(text)
            continue
        if child.is_named:
            break
    if not comment_parts:
        return ""
    return "\n".join(comment_parts)


def _build_note(code: str, comment: str, summary: str) -> str:
    parts: List[str] = []
    if summary:
        parts.append(f"Summary:\n{summary}")
    if comment:
        parts.append(f"Comment:\n{comment}")
    if code:
        parts.append(f"Code:\n{code}")
    return "\n\n".join(parts)


def _extract_identifiers(text: str) -> List[str]:
    return [part for part in re.split(r"[^A-Za-z0-9_]+", text) if part]


def _line_from_byte(source_bytes: bytes, byte_index: int) -> int:
    return source_bytes[:byte_index].count(b"\n") + 1


def _line_col_from_byte(source_bytes: bytes, byte_index: int) -> Tuple[int, int]:
    line = _line_from_byte(source_bytes, byte_index)
    last_newline = source_bytes.rfind(b"\n", 0, byte_index)
    if last_newline == -1:
        column = byte_index + 1
    else:
        column = byte_index - last_newline
    return line, column


def _node_snippet(node, source_bytes: bytes) -> Tuple[str, int, int]:
    start_byte = node.start_byte
    prev = node.prev_sibling
    while prev is not None and prev.type in {"line_comment", "block_comment", "multiline_comment"}:
        start_byte = prev.start_byte
        prev = prev.prev_sibling
    snippet = source_bytes[start_byte : node.end_byte].decode("utf-8", errors="ignore")
    start_line = _line_from_byte(source_bytes, start_byte)
    end_line = node.end_point[0] + 1
    return snippet, start_line, end_line


def _normalize_type_signature(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _first_identifier(node, source_bytes: bytes) -> Optional[str]:
    if node is None:
        return None
    if node.type in {"simple_identifier", "identifier", "type_identifier"}:
        return _node_text(node, source_bytes)
    for child in node.children:
        result = _first_identifier(child, source_bytes)
        if result:
            return result
    return None


def _find_nodes_by_type(node, node_type: str) -> Iterable:
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from _find_nodes_by_type(child, node_type)


def _find_nodes_by_types(node, node_types: set[str]) -> Iterable:
    if node.type in node_types:
        yield node
    for child in node.children:
        yield from _find_nodes_by_types(child, node_types)


def _collect_package_info(tree, source_bytes: bytes) -> Tuple[Optional[str], int, int, str, str]:
    for node in _find_nodes_by_type(tree.root_node, "package_header"):
        text = _node_text(node, source_bytes)
        identifiers = [token for token in _extract_identifiers(text) if token != "package"]
        if identifiers:
            snippet, start_line, end_line = _node_snippet(node, source_bytes)
            comment = _extract_leading_comment(node, source_bytes)
            return ".".join(identifiers), start_line, end_line, snippet, comment
    return None, 0, 0, "", ""


def _collect_imports(tree, source_bytes: bytes) -> List[str]:
    imports: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "import_header"):
        text = _node_text(node, source_bytes)
        identifiers = [
            token
            for token in _extract_identifiers(text)
            if token not in {"import", "as"}
        ]
        if identifiers:
            imports.append(".".join(identifiers))
    return imports


def _count_parameters(function_node) -> int:
    param_list = function_node.child_by_field_name("parameters")
    if param_list is None:
        return 0
    return sum(1 for child in param_list.children if child.type == "parameter")


def _iter_function_parameters(function_node) -> Iterable:
    param_list = function_node.child_by_field_name("parameters")
    if param_list is None:
        return
    for child in param_list.children:
        if child.type == "parameter":
            yield child


def _extract_function_name(function_node, source_bytes: bytes) -> Optional[str]:
    name_node = function_node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes)
    return _first_identifier(function_node, source_bytes)


def _extract_class_name(class_node, source_bytes: bytes) -> Optional[str]:
    name_node = class_node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes)
    return _first_identifier(class_node, source_bytes)


def _normalize_callee(text: str) -> str:
    callee = text.split("(", 1)[0].strip()
    callee = callee.replace("?.", ".").replace("::", ".")
    callee = re.sub(r"<.*?>", "", callee)
    callee = callee.strip(" .")
    return callee


_CALLABLE_REFERENCE_RE = re.compile(
    r"(?P<qual>[A-Za-z_][A-Za-z0-9_.]*)?\s*::\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)


def _normalize_callable_reference(qualifier: Optional[str], name: str) -> str:
    if qualifier:
        qualifier = re.sub(r"<.*?>", "", qualifier).strip(" .")
        if qualifier:
            return f"{qualifier}.{name}"
    return name


def _iter_callable_reference_matches(text: str) -> Iterable[Tuple[str, int]]:
    for match in _CALLABLE_REFERENCE_RE.finditer(text):
        name = match.group("name")
        if name == "class":
            continue
        qualifier = match.group("qual")
        callee = _normalize_callable_reference(qualifier, name)
        if callee:
            yield callee, match.start()


def _extract_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    function_node = call_node.child_by_field_name("function")
    if function_node is not None:
        text = _node_text(function_node, source_bytes)
        return _normalize_callee(text)
    text = _node_text(call_node, source_bytes)
    return _normalize_callee(text)


def _extract_parameter_info(param_node, source_bytes: bytes) -> Tuple[Optional[str], Optional[str], Optional[Tuple[int, int, str]]]:
    name_node = param_node.child_by_field_name("name")
    type_node = param_node.child_by_field_name("type")
    name = _node_text(name_node, source_bytes) if name_node is not None else None
    type_text = _node_text(type_node, source_bytes) if type_node is not None else None
    if type_node is not None:
        snippet, start_line, end_line = _node_snippet(type_node, source_bytes)
        return name, type_text, (start_line, end_line, snippet)
    return name, type_text, None


def _symbol_id(
    package_name: Optional[str],
    class_name: Optional[str],
    function_name: str,
    arity: int,
    rel_path: str,
) -> str:
    parts = [part for part in [package_name, class_name, function_name] if part]
    qualified = ".".join(parts)
    return f"{qualified}/{arity}@{rel_path}"


def _qualified_name(
    package_name: Optional[str],
    class_name: Optional[str],
    function_name: str,
) -> str:
    parts = [part for part in [package_name, class_name, function_name] if part]
    return ".".join(parts)


def _class_qualified_name(package_name: Optional[str], class_name: str) -> str:
    parts = [part for part in [package_name, class_name] if part]
    return ".".join(parts)


def _class_id(package_name: Optional[str], class_name: str) -> str:
    return _class_qualified_name(package_name, class_name)


def _class_kind(node_type: str) -> Optional[str]:
    mapping = {
        "class_declaration": "class",
        "object_declaration": "object",
        "interface_declaration": "interface",
        "enum_class": "enum",
        "enum_declaration": "enum",
    }
    return mapping.get(node_type)


def _find_child(node, node_type: str):
    for child in node.children:
        if child.type == node_type:
            return child
    return None


def _iter_type_decls(tree, source_bytes: bytes) -> Iterable[Tuple]:
    stack: List[Tuple] = [(tree.root_node, [])]
    while stack:
        node, class_stack = stack.pop()
        kind = _class_kind(node.type)
        if kind:
            class_name = _extract_class_name(node, source_bytes)
            next_stack = list(class_stack)
            if class_name:
                next_stack.append(class_name)
                class_path = ".".join(next_stack)
                yield node, class_path, kind
            for child in node.children:
                stack.append((child, next_stack))
            continue
        if node.type == "object_literal":
            class_body = _find_child(node, "class_body")
            if class_body is not None:
                anonymous_name = f"Anonymous@{node.start_point[0] + 1}:{node.start_point[1] + 1}"
                next_stack = list(class_stack)
                next_stack.append(anonymous_name)
                class_path = ".".join(next_stack)
                yield class_body, class_path, "anonymous"
                for child in class_body.children:
                    stack.append((child, next_stack))
                continue
        for child in node.children:
            stack.append((child, class_stack))


def _extract_type_name(text: str) -> Optional[str]:
    match = re.search(r"[A-Za-z_][A-Za-z0-9_\.]*", text)
    if match:
        return match.group(0)
    return None


def _extract_super_types(class_node, source_bytes: bytes) -> List[str]:
    text = _node_text(class_node, source_bytes)
    if ":" not in text:
        return []
    after = text.split(":", 1)[1]
    stop_at = len(after)
    for token in ["{", "where", "\n"]:
        pos = after.find(token)
        if pos != -1:
            stop_at = min(stop_at, pos)
    segment = after[:stop_at]
    parts = [part.strip() for part in segment.split(",") if part.strip()]
    results: List[str] = []
    for part in parts:
        part = re.sub(r"<.*?>", "", part)
        part = re.sub(r"\(.*?\)", "", part)
        part = re.split(r"\s+by\s+", part)[0]
        name = _extract_type_name(part)
        if name:
            results.append(name)
    return results


def _iter_functions(tree, source_bytes: bytes) -> Iterable[Tuple]:
    stack: List[Tuple] = [(tree.root_node, [])]
    while stack:
        node, class_stack = stack.pop()
        kind = _class_kind(node.type)
        if kind:
            class_name = _extract_class_name(node, source_bytes)
            next_stack = list(class_stack)
            if class_name:
                next_stack.append(class_name)
            for child in node.children:
                stack.append((child, next_stack))
            continue
        if node.type == "object_literal":
            class_body = _find_child(node, "class_body")
            if class_body is not None:
                anonymous_name = f"Anonymous@{node.start_point[0] + 1}:{node.start_point[1] + 1}"
                next_stack = list(class_stack)
                next_stack.append(anonymous_name)
                for child in class_body.children:
                    stack.append((child, next_stack))
                continue
        if node.type == "function_declaration":
            active_class = ".".join(class_stack) if class_stack else None
            yield node, active_class
        for child in node.children:
            stack.append((child, class_stack))


def _iter_calls(function_node) -> Iterable:
    for node in _find_nodes_by_type(function_node, "call_expression"):
        yield node


def _get_kotlin_parser(function_node) -> Iterable:
    for node in _find_nodes_by_type(function_node, "call_expression"):
        yield node


# def _get_kotlin_parser(function_node) -> Iterable:
#     for node in _find_nodes_by_type(function_node, "call_expression"):
#         yield node


def _get_kotlin_parser() -> Parser:
    if ts_get_parser is not None:
        try:
            return ts_get_parser("kotlin")
        except TypeError:
            # Likely tree_sitter_languages vs tree_sitter API mismatch.
            pass
        except Exception:
            pass
    try:
        from tree_sitter_kotlin import language as kotlin_language
    except Exception as exc:
        raise RuntimeError(
            "Kotlin parser unavailable. Install 'tree-sitter-kotlin' or pin tree-sitter==0.20.x."
        ) from exc
    language = kotlin_language()
    if not isinstance(language, Language):
        try:
            language = Language(language)
        except Exception as exc:
            raise RuntimeError("Invalid Kotlin language capsule for tree_sitter") from exc
    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    elif hasattr(parser, "language"):
        parser.language = language
    else:
        try:
            parser = Parser(language)
        except Exception as exc:
            raise RuntimeError("Unsupported tree_sitter Parser API") from exc
    return parser


def parse_kotlin_file(
    path: str,
    root: str,
) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[ClassDef],
    List[TypeEdge],
    List[FunctionTypeDef],
    List[RelationEdge],
    FileDef,
    Optional[PackageDef],
    Dict[str, Any],
]:
    parser = _get_kotlin_parser()
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    tree = parser.parse(source_bytes)
    rel_path = os.path.relpath(path, root)
    package_name, pkg_start, pkg_end, pkg_snippet, pkg_comment = _collect_package_info(tree, source_bytes)
    imports = _collect_imports(tree, source_bytes)
    functions: List[FunctionDef] = []
    calls: List[CallEdge] = []
    classes: List[ClassDef] = []
    type_edges: List[TypeEdge] = []
    function_types: List[FunctionTypeDef] = []
    relation_edges: List[RelationEdge] = []
    compose_routes = _extract_compose_routes_from_tree(tree, source_bytes)

    package_def = None
    if package_name:
        pkg_summary = pkg_comment
        pkg_note = _build_note(pkg_snippet, pkg_comment, pkg_summary)
        package_def = PackageDef(
            name=package_name,
            start_line=pkg_start,
            end_line=pkg_end,
            code=pkg_snippet,
            comment=pkg_comment,
            summary=pkg_summary,
            note=pkg_note,
        )

    file_code = source_bytes.decode("utf-8", errors="ignore")
    file_lines = file_code.count("\n") + 1
    file_comment = _extract_file_comment(tree, source_bytes)
    file_summary = file_comment
    file_note = _build_note(file_code, file_comment, file_summary)
    file_def = FileDef(
        file_path=rel_path,
        package_name=package_name,
        start_line=1,
        end_line=file_lines,
        code=file_code,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
    )

    for class_node, class_path, kind in _iter_type_decls(tree, source_bytes):
        snippet, start_line, end_line = _node_snippet(class_node, source_bytes)
        comment = _extract_leading_comment(class_node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        class_id = _class_id(package_name, class_path)
        qualified = _class_qualified_name(package_name, class_path)
        classes.append(
            ClassDef(
                symbol_id=class_id,
                qualified_name=qualified,
                name=class_path,
                kind=kind,
                package_name=package_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=snippet,
                comment=comment,
                summary=summary,
                note=note,
            )
        )
        super_types = _extract_super_types(class_node, source_bytes)
        if super_types:
            if kind in {"class", "object", "enum"}:
                first, rest = super_types[0], super_types[1:]
                type_edges.append(
                    TypeEdge(
                        source_id=class_id,
                        source_package=package_name,
                        target_name=first,
                        rel_type="EXTENDS",
                        target_id=None,
                    )
                )
                for item in rest:
                    type_edges.append(
                        TypeEdge(
                            source_id=class_id,
                            source_package=package_name,
                            target_name=item,
                            rel_type="IMPLEMENTS",
                            target_id=None,
                        )
                    )
            elif kind == "interface":
                for item in super_types:
                    type_edges.append(
                        TypeEdge(
                            source_id=class_id,
                            source_package=package_name,
                            target_name=item,
                            rel_type="EXTENDS",
                            target_id=None,
                        )
                    )

    android_events: List[Dict[str, Any]] = []
    for func_node, class_name in _iter_functions(tree, source_bytes):
        func_name = _extract_function_name(func_node, source_bytes)
        if not func_name:
            continue
        arity = _count_parameters(func_node)
        symbol_id = _symbol_id(package_name, class_name, func_name, arity, rel_path)
        qualified = _qualified_name(package_name, class_name, func_name)
        snippet, start_line, end_line = _node_snippet(func_node, source_bytes)
        comment = _extract_leading_comment(func_node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        functions.append(
            FunctionDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=func_name,
                kind="function",
                class_name=class_name,
                package_name=package_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                arity=arity,
                code=snippet,
                comment=comment,
                summary=summary,
                note=note,
            )
        )
        android_events.extend(_collect_android_events(func_node, source_bytes, symbol_id))

        for param_node in _iter_function_parameters(func_node):
            param_name, type_text, type_info = _extract_parameter_info(param_node, source_bytes)
            if not type_text:
                continue
            if "->" in type_text:
                type_signature = _normalize_type_signature(type_text)
                type_id = f"functype::{type_signature}"
                if type_info is None:
                    start_line_t = start_line
                    end_line_t = end_line
                    snippet_t = type_signature
                else:
                    start_line_t, end_line_t, snippet_t = type_info
                function_types.append(
                    FunctionTypeDef(
                        symbol_id=type_id,
                        type_signature=type_signature,
                        file_path=rel_path,
                        start_line=start_line_t,
                        end_line=end_line_t,
                        code=snippet_t,
                    )
                )
                relation_edges.append(
                    RelationEdge(
                        source_id=symbol_id,
                        source_label="Function",
                        target_id=type_id,
                        target_label="FunctionType",
                        rel_type="TAKES_FUNCTION",
                        properties={
                            "parameter_name": param_name or "",
                            "parameter_type": type_signature,
                        },
                    )
                )

        for call_node in _iter_calls(func_node):
            callee = _extract_call_name(call_node, source_bytes)
            if not callee:
                continue
            call_line = call_node.start_point[0] + 1
            call_column = call_node.start_point[1] + 1
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_package=package_name,
                    caller_class=class_name,
                    imports=imports,
                    call_line=call_line,
                    call_column=call_column,
                    call_type="call_expression",
                    callee_name=callee,
                    callee_id=None,
                )
            )

        lambda_nodes = list(
            _find_nodes_by_types(func_node, {"lambda_literal", "lambda_expression"})
        )
        if lambda_nodes:
            lambda_ranges = [
                (node.start_byte, node.end_byte) for node in lambda_nodes
            ]
        else:
            lambda_ranges = []
        function_text = _node_text(func_node, source_bytes)
        for callee, offset in _iter_callable_reference_matches(function_text):
            abs_offset = func_node.start_byte + offset
            if any(start <= abs_offset < end for start, end in lambda_ranges):
                continue
            call_line, call_column = _line_col_from_byte(source_bytes, abs_offset)
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_package=package_name,
                    caller_class=class_name,
                    imports=imports,
                    call_line=call_line,
                    call_column=call_column,
                    call_type="callable_reference",
                    callee_name=callee,
                    callee_id=None,
                )
            )

        for lambda_node in lambda_nodes:
            snippet, l_start, l_end = _node_snippet(lambda_node, source_bytes)
            lambda_name = f"{func_name}$lambda@{l_start}:{lambda_node.start_point[1] + 1}"
            lambda_id = _symbol_id(package_name, class_name, lambda_name, 0, rel_path)
            lambda_qualified = _qualified_name(package_name, class_name, lambda_name)
            functions.append(
                FunctionDef(
                    symbol_id=lambda_id,
                    qualified_name=lambda_qualified,
                    name=lambda_name,
                    kind="lambda",
                    class_name=class_name,
                    package_name=package_name,
                    file_path=rel_path,
                    start_line=l_start,
                    end_line=l_end,
                    arity=0,
                    code=snippet,
                    comment="",
                    summary="",
                    note=_build_note(snippet, "", ""),
                )
            )
            relation_edges.append(
                RelationEdge(
                    source_id=symbol_id,
                    source_label="Function",
                    target_id=lambda_id,
                    target_label="Function",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
            for lambda_call in _iter_calls(lambda_node):
                callee = _extract_call_name(lambda_call, source_bytes)
                if not callee:
                    continue
                call_line = lambda_call.start_point[0] + 1
                call_column = lambda_call.start_point[1] + 1
                calls.append(
                    CallEdge(
                        caller_id=lambda_id,
                        caller_file=rel_path,
                        caller_package=package_name,
                        caller_class=class_name,
                        imports=imports,
                        call_line=call_line,
                        call_column=call_column,
                        call_type="call_expression",
                        callee_name=callee,
                        callee_id=None,
                    )
                )
            lambda_text = _node_text(lambda_node, source_bytes)
            for callee, offset in _iter_callable_reference_matches(lambda_text):
                abs_offset = lambda_node.start_byte + offset
                call_line, call_column = _line_col_from_byte(source_bytes, abs_offset)
                calls.append(
                    CallEdge(
                        caller_id=lambda_id,
                        caller_file=rel_path,
                        caller_package=package_name,
                        caller_class=class_name,
                        imports=imports,
                        call_line=call_line,
                        call_column=call_column,
                        call_type="callable_reference",
                        callee_name=callee,
                        callee_id=None,
                    )
                )

    return (
        functions,
        calls,
        classes,
        type_edges,
        function_types,
        relation_edges,
        file_def,
        package_def,
        compose_routes,
        {"events": android_events},
    )


def _resolve_calls(functions: List[FunctionDef], calls: List[CallEdge]) -> None:
    by_name: Dict[str, List[FunctionDef]] = {}
    by_qualified: Dict[str, FunctionDef] = {}
    by_class_and_name: Dict[Tuple[str, str], List[FunctionDef]] = {}
    by_package_and_name: Dict[Tuple[str, str], List[FunctionDef]] = {}

    for func in functions:
        by_name.setdefault(func.name, []).append(func)
        by_qualified[func.qualified_name] = func
        if func.class_name:
            by_class_and_name.setdefault((func.class_name, func.name), []).append(func)
        if func.package_name:
            by_package_and_name.setdefault((func.package_name, func.name), []).append(func)

    def pick_candidate(candidates: List[FunctionDef], call: CallEdge) -> Optional[FunctionDef]:
        if call.caller_class:
            for func in candidates:
                if func.class_name == call.caller_class and func.package_name == call.caller_package:
                    return func
        if call.caller_package:
            for func in candidates:
                if func.package_name == call.caller_package:
                    return func
        if call.imports:
            for func in candidates:
                if func.package_name and any(
                    imp.startswith(func.package_name) for imp in call.imports
                ):
                    return func
        return candidates[0] if candidates else None

    for call in calls:
        callee_name = call.callee_name
        candidate: Optional[FunctionDef] = None

        if "." in callee_name:
            if callee_name in by_qualified:
                candidate = by_qualified[callee_name]
            else:
                parts = callee_name.split(".")
                method_name = parts[-1]
                qualifier = parts[-2] if len(parts) >= 2 else None
                if qualifier:
                    candidates = by_class_and_name.get((qualifier, method_name), [])
                    candidate = pick_candidate(candidates, call)
                if candidate is None:
                    for qual, func in by_qualified.items():
                        if qual.endswith(callee_name):
                            candidate = func
                            break

        if candidate is None:
            candidates = by_name.get(callee_name, [])
            candidate = pick_candidate(candidates, call)

        if candidate:
            call.callee_id = candidate.symbol_id


# Neo4jWriter class has been removed and replaced with GraphDriverFactory + LanguageCodeWriter
# See tools/graph/ for the new abstraction layer
# Migration guide: See kotlin_analyzer.py for reference implementation


class QdrantWriter:
    def __init__(
        self,
        url: str,
        collection: str,
        vector_size: int,
        timeout: float = 300.0,
        retries: int = 3,
        retry_sleep: float = 2.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.collection = collection
        self.vector_size = vector_size
        self.timeout = timeout
        self.retries = retries
        self.retry_sleep = retry_sleep

    def ensure_collection(self) -> None:
        for attempt in range(self.retries + 1):
            try:
                response = requests.get(
                    f"{self.url}/collections/{self.collection}",
                    timeout=self.timeout,
                )
                if response.status_code == 200:
                    existing_size = self._extract_vector_size(response)
                    if existing_size and existing_size != self.vector_size:
                        raise ValueError(
                            "Qdrant collection vector size mismatch: "
                            f"{self.collection} has size {existing_size}, "
                            f"but embedder produces size {self.vector_size}. "
                            "Use a matching embedder or recreate the collection."
                        )
                    return
                payload = {
                    "vectors": {"size": self.vector_size, "distance": "Cosine"},
                }
                response = requests.put(
                    f"{self.url}/collections/{self.collection}",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return
            except requests.RequestException:
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_sleep)

    @staticmethod
    def _extract_vector_size(response: requests.Response) -> Optional[int]:
        try:
            data = response.json()
        except ValueError:
            return None
        result = data.get("result", {})
        config = result.get("config", {})
        params = config.get("params", {})
        vectors = params.get("vectors", {})
        if isinstance(vectors, dict):
            size = vectors.get("size")
            if isinstance(size, int):
                return size
        return None

    def upsert(self, points: List[Dict]) -> None:
        if not points:
            return
        payload = {"points": points}
        for attempt in range(self.retries + 1):
            try:
                response = requests.put(
                    f"{self.url}/collections/{self.collection}/points?wait=true",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return
            except requests.RequestException:
                if attempt >= self.retries:
                    raise
                time.sleep(self.retry_sleep)


def _should_trust_remote_code(model_name: str) -> bool:
    jina_path = os.environ.get("JINA_MODEL_PATH")
    if jina_path and os.path.normpath(jina_path) == os.path.normpath(model_name):
        return True
    return "jina" in model_name.lower()


class CodeEmbedder:
    def __init__(self, model_name: str, device: str, max_embed_chars: int, chunk_embed: bool) -> None:
        trust_remote_code = _should_trust_remote_code(model_name)
        extra_tokenizer_kwargs = {"fix_mistral_regex": True} if trust_remote_code else {}
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
            **extra_tokenizer_kwargs,
        )
        self.model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=trust_remote_code,
        )
        self.device = torch.device(device)
        self.model.to(self.device)
        self.model.eval()
        self.max_embed_chars = max_embed_chars if max_embed_chars > 0 else None
        self.chunk_embed = chunk_embed
        self.vector_size = self._infer_vector_size()

    def embed(self, texts: List[str], batch_size: int = 8, verbose: bool = False) -> List[List[float]]:
        if not texts:
            return []
        if self.chunk_embed:
            chunk_texts: List[str] = []
            chunk_map: List[int] = []
            for idx, text in enumerate(texts):
                for chunk in self._split_chunks(text):
                    chunk_texts.append(chunk)
                    chunk_map.append(idx)
            chunk_vectors = self._embed_texts(chunk_texts, batch_size=batch_size, verbose=verbose)
            return self._mean_pool_chunks(chunk_vectors, chunk_map, len(texts))
        truncated = [self._truncate_text(text) for text in texts]
        return self._embed_texts(truncated, batch_size=batch_size, verbose=verbose)

    def _embed_texts(self, texts: List[str], batch_size: int, verbose: bool) -> List[List[float]]:
        vectors: List[List[float]] = []
        total = len(texts)
        with torch.no_grad():
            for idx in range(0, len(texts), batch_size):
                batch = texts[idx : idx + batch_size]
                if verbose:
                    total_batches = max(1, (total + batch_size - 1) // batch_size)
                    print(f"[embed] batch {idx // batch_size + 1} / {total_batches}")
                if hasattr(self.model, "encode"):
                    try:
                        encoded = self.model.encode(batch, device=str(self.device))
                    except TypeError:
                        encoded = self.model.encode(batch)
                    if isinstance(encoded, torch.Tensor):
                        vectors.extend(encoded.detach().cpu().tolist())
                    else:
                        vectors.extend(encoded.tolist() if hasattr(encoded, "tolist") else [list(vec) for vec in encoded])
                    continue
                encoded = self.tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                )
                encoded = {key: value.to(self.device) for key, value in encoded.items()}
                outputs = self.model(**encoded)
                embeddings = self._mean_pool(outputs.last_hidden_state, encoded["attention_mask"])
                vectors.extend(embeddings.cpu().tolist())
        return vectors

    @staticmethod
    def _mean_pool(last_hidden: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        mask = mask.unsqueeze(-1).type_as(last_hidden)
        summed = (last_hidden * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1)
        return summed / counts

    def _truncate_text(self, text: str) -> str:
        if self.max_embed_chars is None:
            return text
        if len(text) <= self.max_embed_chars:
            return text
        return text[: self.max_embed_chars]

    def _split_chunks(self, text: str) -> List[str]:
        if self.max_embed_chars is None:
            return [text]
        if len(text) <= self.max_embed_chars:
            return [text]
        return [text[i : i + self.max_embed_chars] for i in range(0, len(text), self.max_embed_chars)]

    @staticmethod
    def _mean_pool_chunks(
        vectors: List[List[float]],
        chunk_map: List[int],
        total_texts: int,
    ) -> List[List[float]]:
        sums: List[Optional[List[float]]] = [None] * total_texts
        counts = [0] * total_texts
        for vector, idx in zip(vectors, chunk_map):
            if sums[idx] is None:
                sums[idx] = list(vector)
            else:
                current = sums[idx]
                if current is not None:
                    for j, value in enumerate(vector):
                        current[j] += value
            counts[idx] += 1
        results: List[List[float]] = []
        for idx in range(total_texts):
            if sums[idx] is None:
                results.append([])
                continue
            denom = max(counts[idx], 1)
            results.append([value / denom for value in sums[idx]])
        return results

    def _infer_vector_size(self) -> int:
        if hasattr(self.model, "get_sentence_embedding_dimension"):
            try:
                size = self.model.get_sentence_embedding_dimension()
                if isinstance(size, int) and size > 0:
                    return size
            except Exception:
                pass
        config = getattr(self.model, "config", None)
        if config:
            for attr in ("sentence_embedding_dimension", "projection_dim", "hidden_size", "dim"):
                size = getattr(config, attr, None)
                if isinstance(size, int) and size > 0:
                    return size
        sample = self.embed(["_"], batch_size=1, verbose=False)
        if sample and sample[0]:
            return len(sample[0])
        raise ValueError("Unable to infer embedding vector size.")


def _stable_point_id(symbol_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, symbol_id))


def _event_id(event: Dict[str, Any]) -> str:
    event_id = str(event.get("id") or "").strip()
    if event_id:
        return event_id
    name = str(event.get("name") or "").strip()
    if not name:
        raise ValueError("Event mapping entry missing 'id' or 'name'")
    namespace = str(event.get("namespace") or "").strip()
    version = str(event.get("version") or "").strip()
    if namespace and version:
        return f"event::{namespace}::{name}::{version}"
    if namespace:
        return f"event::{namespace}::{name}"
    if version:
        return f"event::{name}::{version}"
    return f"event::{name}"


def _call_site_id(
    caller_id: str,
    callee_id: str,
    file_path: str,
    line: int,
    column: int,
    call_type: str,
) -> str:
    key = f"{caller_id}:{callee_id}:{file_path}:{line}:{column}:{call_type}"
    return _stable_point_id(key)


_ANDROID_SKIP_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".android",
    ".cxx",
    "build",
    "out",
    "dist",
    "node_modules",
}


def _walk_android_tree(root: str) -> Iterable[Tuple[str, List[str], List[str]]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _ANDROID_SKIP_DIRS]
        yield dirpath, dirnames, filenames


def _scan_android_kotlin_files(root: str) -> List[str]:
    kotlin_files: List[str] = []
    for dirpath, _, filenames in _walk_android_tree(root):
        for name in filenames:
            if name.endswith((".kt", ".kts")):
                kotlin_files.append(os.path.join(dirpath, name))
    return sorted(kotlin_files)


def _scan_android_manifest_files(root: str) -> List[str]:
    manifests: List[str] = []
    for dirpath, _, filenames in _walk_android_tree(root):
        for name in filenames:
            if name == "AndroidManifest.xml":
                manifests.append(os.path.join(dirpath, name))
    return sorted(manifests)


def _scan_android_gradle_files(root: str) -> List[str]:
    gradle_files: List[str] = []
    for dirpath, _, filenames in _walk_android_tree(root):
        for name in filenames:
            if name in {"build.gradle", "build.gradle.kts"}:
                gradle_files.append(os.path.join(dirpath, name))
    return sorted(gradle_files)


def _scan_android_resource_xml_files(root: str) -> List[str]:
    xml_files: List[str] = []
    for dirpath, _, filenames in _walk_android_tree(root):
        if f"{os.sep}res{os.sep}" not in f"{dirpath}{os.sep}":
            continue
        for name in filenames:
            if name.endswith(".xml"):
                xml_files.append(os.path.join(dirpath, name))
    return sorted(xml_files)


_ANDROID_NS = "http://schemas.android.com/apk/res/android"


def _android_attr(element: ET.Element, name: str) -> Optional[str]:
    return element.get(f"{{{_ANDROID_NS}}}{name}")


def _parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    return None


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _resolve_android_class_name(name: Optional[str], package_name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    if name.startswith("."):
        return f"{package_name}{name}" if package_name else name.lstrip(".")
    if "." in name:
        return name
    return f"{package_name}.{name}" if package_name else name


def _manifest_symbol_id(rel_path: str) -> str:
    return f"manifest::{rel_path}"


def _component_symbol_id(component_type: str, class_name: Optional[str], rel_path: str, line: int) -> str:
    base = class_name or "unknown"
    return f"component::{component_type}:{base}@{rel_path}:{line}"


def _resource_symbol_id(res_type: str, name: str) -> str:
    return f"res::{res_type}:{name}"


def _module_symbol_id(module_path: str) -> str:
    return f"module::{module_path}"


def _dependency_symbol_id(coordinate: str) -> str:
    return f"dep::{coordinate}"


def _annotation_symbol_id(name: str) -> str:
    return f"annotation::{name}"


def _nav_route_symbol_id(route: str) -> str:
    return f"navroute::{route}"


def _intent_action_symbol_id(action: str) -> str:
    return f"intent_action::{action}"


def _handler_message_symbol_id(token: str) -> str:
    return f"handler_msg::{token}"


def _parse_android_manifest(path: str, root: str) -> Tuple[AndroidManifestDef, List[AndroidComponentDef]]:
    rel_path = os.path.relpath(path, root)
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        content = handle.read()
    end_line = content.count("\n") + 1
    package_name = None
    components: List[AndroidComponentDef] = []

    try:
        tree = ET.parse(path)
        root_elem = tree.getroot()
        package_name = root_elem.get("package")

        for elem in root_elem.iter():
            tag = _strip_ns(elem.tag)
            if tag not in {"activity", "activity-alias", "service", "receiver", "provider"}:
                continue
            name = _android_attr(elem, "name")
            class_name = _resolve_android_class_name(name, package_name)
            exported = _parse_bool(_android_attr(elem, "exported"))
            enabled = _parse_bool(_android_attr(elem, "enabled"))
            direct_boot = _parse_bool(_android_attr(elem, "directBootAware"))
            process = _android_attr(elem, "process")
            permission = _android_attr(elem, "permission")
            target_activity = _android_attr(elem, "targetActivity")
            start_line = getattr(elem, "sourceline", 0) or 0
            code = ET.tostring(elem, encoding="unicode")
            intent_actions: List[str] = []
            intent_categories: List[str] = []
            intent_data: List[str] = []
            for child in list(elem):
                if _strip_ns(child.tag) != "intent-filter":
                    continue
                for sub in list(child):
                    sub_tag = _strip_ns(sub.tag)
                    if sub_tag == "action":
                        action_name = _android_attr(sub, "name")
                        if action_name:
                            intent_actions.append(action_name)
                    elif sub_tag == "category":
                        category_name = _android_attr(sub, "name")
                        if category_name:
                            intent_categories.append(category_name)
                    elif sub_tag == "data":
                        data_parts: List[str] = []
                        for key in (
                            "scheme",
                            "host",
                            "port",
                            "path",
                            "pathPrefix",
                            "pathPattern",
                            "mimeType",
                        ):
                            value = _android_attr(sub, key)
                            if value:
                                data_parts.append(f"{key}={value}")
                        if data_parts:
                            intent_data.append(",".join(data_parts))
            component_id = _component_symbol_id(tag, class_name, rel_path, start_line)
            note = _build_note(code, "", "")
            components.append(
                AndroidComponentDef(
                    symbol_id=component_id,
                    name=name or "",
                    component_type=tag,
                    class_name=class_name,
                    exported=exported,
                    process=process,
                    permission=permission,
                    enabled=enabled,
                    direct_boot_aware=direct_boot,
                    target_activity=target_activity,
                    intent_actions=intent_actions,
                    intent_categories=intent_categories,
                    intent_data=intent_data,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=start_line,
                    code=code,
                    summary="",
                    note=note,
                )
            )
    except ET.ParseError:
        pass

    manifest_note = _build_note(content, "", "")
    manifest_def = AndroidManifestDef(
        symbol_id=_manifest_symbol_id(rel_path),
        package_name=package_name,
        file_path=rel_path,
        start_line=1,
        end_line=end_line,
        code=content,
        summary="",
        note=manifest_note,
    )
    return manifest_def, components


def _extract_resource_ids_from_xml(path: str) -> List[str]:
    ids: List[str] = []
    try:
        for _, elem in ET.iterparse(path, events=("start",)):
            for attr_value in elem.attrib.values():
                if "@id/" in attr_value or "@+id/" in attr_value:
                    for token in re.findall(r"@\\+?id/([A-Za-z0-9_]+)", attr_value):
                        ids.append(token)
    except ET.ParseError:
        return ids
    return ids


def _collect_android_resources(root: str) -> Tuple[List[AndroidResourceDef], Dict[Tuple[str, str], str]]:
    resources: List[AndroidResourceDef] = []
    resource_index: Dict[Tuple[str, str], str] = {}
    for path in _scan_android_resource_xml_files(root):
        rel_path = os.path.relpath(path, root)
        dir_name = os.path.basename(os.path.dirname(path))
        if "-" in dir_name:
            res_type, qualifier = dir_name.split("-", 1)
        else:
            res_type, qualifier = dir_name, ""
        if res_type not in {"layout", "navigation", "menu"}:
            continue
        base_name = os.path.splitext(os.path.basename(path))[0]
        res_id = _resource_symbol_id(res_type, base_name)
        if (res_type, base_name) not in resource_index:
            resource_index[(res_type, base_name)] = res_id
            resources.append(
                AndroidResourceDef(
                    symbol_id=res_id,
                    name=base_name,
                    res_type=res_type,
                    file_path=rel_path,
                    qualifier=qualifier,
                    summary="",
                    note="",
                )
            )
        for view_id in _extract_resource_ids_from_xml(path):
            id_key = ("id", view_id)
            if id_key in resource_index:
                continue
            view_res_id = _resource_symbol_id("id", view_id)
            resource_index[id_key] = view_res_id
            resources.append(
                AndroidResourceDef(
                    symbol_id=view_res_id,
                    name=view_id,
                    res_type="id",
                    file_path=rel_path,
                    qualifier=qualifier,
                    summary="",
                    note="",
                )
            )
    return resources, resource_index


def _parse_gradle_file(path: str, root: str) -> Tuple[GradleModuleDef, List[GradleDependencyDef], List[Tuple[str, str, str]]]:
    rel_path = os.path.relpath(path, root)
    module_dir = os.path.relpath(os.path.dirname(path), root)
    module_path = ":" if module_dir in {".", ""} else ":" + module_dir.replace(os.sep, ":")
    module_name = module_dir if module_dir not in {".", ""} else "root"
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        content = handle.read()

    module_type = "unknown"
    if "com.android.application" in content:
        module_type = "app"
    elif "com.android.library" in content:
        module_type = "library"

    namespace_match = re.search(r"^\\s*namespace\\s*=?\\s*[\"']([^\"']+)[\"']", content, re.M)
    namespace = namespace_match.group(1) if namespace_match else None
    app_id_match = re.search(r"^\\s*applicationId\\s*=?\\s*[\"']([^\"']+)[\"']", content, re.M)
    application_id = app_id_match.group(1) if app_id_match else None

    module_def = GradleModuleDef(
        symbol_id=_module_symbol_id(module_path),
        name=module_name,
        module_path=module_path,
        module_type=module_type,
        namespace=namespace,
        application_id=application_id,
        file_path=rel_path,
        summary="",
        note="",
    )

    dependencies: List[GradleDependencyDef] = []
    dep_edges: List[Tuple[str, str, str]] = []
    for match in re.finditer(r"^\\s*(\\w+)\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)", content, re.M):
        config = match.group(1)
        coordinate = match.group(2).strip()
        if ":" not in coordinate or coordinate.startswith("project("):
            continue
        parts = coordinate.split(":")
        group = parts[0] if len(parts) >= 1 else None
        artifact = parts[1] if len(parts) >= 2 else None
        version = parts[2] if len(parts) >= 3 else None
        dep_id = _dependency_symbol_id(coordinate)
        dependencies.append(
            GradleDependencyDef(
                symbol_id=dep_id,
                coordinate=coordinate,
                group=group,
                artifact=artifact,
                version=version,
                summary="",
                note="",
            )
        )
        dep_edges.append((module_def.symbol_id, dep_id, config))
    return module_def, dependencies, dep_edges


def _extract_resource_refs(code: str) -> List[Tuple[str, str]]:
    refs: List[Tuple[str, str]] = []
    pattern = re.compile(
        r"(?:\\b[A-Za-z0-9_\\.]+\\.)?R\\.(layout|id|string|drawable|navigation|menu|color|anim|mipmap|raw|font|xml)\\.([A-Za-z0-9_]+)"
    )
    for match in pattern.finditer(code):
        refs.append((match.group(1), match.group(2)))
    return refs


_ANDROID_ANNOTATIONS = {
    "HiltAndroidApp",
    "AndroidEntryPoint",
    "InstallIn",
    "Module",
    "Provides",
    "Binds",
    "Inject",
    "AssistedInject",
    "AssistedFactory",
    "Singleton",
    "Qualifier",
    "Entity",
    "Dao",
    "Database",
    "Query",
    "Insert",
    "Update",
    "Delete",
    "Transaction",
    "TypeConverter",
    "Embedded",
    "Relation",
    "Parcelize",
    "Composable",
}


def _extract_android_annotations(text: str) -> List[str]:
    if not text:
        return []
    found = re.findall(r"@\s*([A-Za-z_][A-Za-z0-9_]*)", text)
    return [name for name in found if name in _ANDROID_ANNOTATIONS]


def _parse_compose_routes(code: str) -> Tuple[List[str], List[str], List[Tuple[str, List[str]]]]:
    if not code:
        return [], [], []
    routes: List[str] = []
    start_routes: List[str] = []
    route_targets: List[Tuple[str, List[str]]] = []
    reserved = {"if", "for", "while", "when", "return", "else", "try", "catch", "finally"}

    def extract_route(args_text: str) -> Optional[str]:
        match = re.search(r"route\s*=\s*['\"]([^'\"]+)['\"]", args_text)
        if match:
            return match.group(1)
        match = re.search(r"['\"]([^'\"]+)['\"]", args_text)
        if match:
            return match.group(1)
        return None

    def callable_names(block_text: str) -> List[str]:
        names: List[str] = []
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(", block_text):
            name = match.group(1)
            short_name = name.split(".")[-1]
            if short_name in reserved or short_name == "composable":
                continue
            names.append(name)
        for name, _ in _iter_callable_reference_matches(block_text):
            short_name = name.split(".")[-1]
            if short_name in reserved or short_name == "composable":
                continue
            names.append(name)
        return names

    for match in re.finditer(r"\bcomposable\s*\((.*?)\)\s*\{", code, re.S):
        args_text = match.group(1)
        route = extract_route(args_text)
        if not route:
            continue
        routes.append(route)
        block_start = match.end() - 1
        depth = 0
        block_end = None
        for idx in range(block_start, len(code)):
            if code[idx] == "{":
                depth += 1
            elif code[idx] == "}":
                depth -= 1
                if depth == 0:
                    block_end = idx
                    break
        block_text = code[block_start:block_end] if block_end is not None else ""
        targets = callable_names(block_text)
        if not targets:
            content_match = re.search(
                r"content\s*=\s*\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\(",
                args_text,
            )
            if content_match:
                targets = [content_match.group(1)]
        if not targets:
            content_match = re.search(
                r"content\s*=\s*::\s*([A-Za-z_][A-Za-z0-9_.]*)",
                args_text,
            )
            if content_match:
                targets = [content_match.group(1)]
        if targets:
            route_targets.append((route, targets))

    for match in re.finditer(
        r"\bNavHost\s*\(.*?startDestination\s*=\s*['\"]([^'\"]+)['\"]",
        code,
        re.S,
    ):
        start_routes.append(match.group(1))
    return routes, start_routes, route_targets


def _extract_string_literals(text: str) -> List[str]:
    literals: List[str] = []
    for match in re.finditer(r'"""(.*?)"""', text, re.S):
        literals.append(match.group(1))
    for match in re.finditer(r'"([^"\\\\]*(?:\\\\.[^"\\\\]*)*)"', text):
        literals.append(match.group(1))
    for match in re.finditer(r"'([^'\\\\]*(?:\\\\.[^'\\\\]*)*)'", text):
        literals.append(match.group(1))
    return literals


def _extract_class_refs(text: str) -> List[str]:
    return re.findall(r"([A-Za-z_][A-Za-z0-9_\\.]*)::class\\.java", text)


def _extract_handler_tokens(text: str) -> List[str]:
    tokens: List[str] = []
    tokens.extend(re.findall(r"\\b[A-Z][A-Z0-9_]{2,}\\b", text))
    tokens.extend(re.findall(r"\\b\\d+\\b", text))
    return tokens


def _extract_register_receiver_target(text: str) -> Optional[str]:
    match = re.search(r"registerReceiver\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,", text)
    if match:
        return match.group(1)
    match = re.search(r"registerReceiver\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)", text)
    if match:
        return match.group(1)
    return None


def _extract_intentfilter_actions(text: str) -> List[str]:
    actions = re.findall(r"addAction\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)", text)
    return actions


def _extract_intentfilter_var_actions(text: str) -> List[str]:
    actions: List[str] = []
    for match in re.finditer(r"\\b(?:val|var)\\s+(\\w+)\\s*=\\s*IntentFilter\\b(\\s*\\(([^\\)]*)\\))?", text):
        var_name = match.group(1)
        ctor_args = match.group(3) or ""
        for action in _extract_string_literals(ctor_args):
            actions.append(action)
        pattern = re.compile(
            rf"\\b{re.escape(var_name)}\\.addAction\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)"
        )
        for action_match in pattern.finditer(text):
            actions.append(action_match.group(1))
    return actions


def _extract_inline_intentfilter_actions(text: str) -> List[str]:
    return _extract_intentfilter_actions(text)


def _extract_component_name_target(text: str) -> Optional[str]:
    match = re.search(r"ComponentName\\s*\\([^\\)]*[\"']([^\"']+)[\"']\\s*\\)", text)
    if match:
        return match.group(1)
    match = re.search(r"setClassName\\s*\\([^,]*,\\s*[\"']([^\"']+)[\"']\\s*\\)", text)
    if match:
        return match.group(1)
    match = re.search(r"setComponent\\s*\\(\\s*ComponentName\\s*\\([^\\)]*[\"']([^\"']+)[\"']\\s*\\)\\s*\\)", text)
    if match:
        return match.group(1)
    return None


def _collect_android_events(function_node, source_bytes: bytes, function_id: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    function_text = _node_text(function_node, source_bytes)
    filter_actions_by_var = _extract_intentfilter_var_actions(function_text)
    filter_actions_inline = _extract_inline_intentfilter_actions(function_text)
    intent_vars: Dict[str, Dict[str, List[str]]] = {}
    for match in re.finditer(r"\\b(?:val|var)\\s+(\\w+)\\s*=\\s*Intent\\s*\\(([^\\)]*)\\)", function_text):
        var_name = match.group(1)
        args_text = match.group(2) or ""
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        intent_vars[var_name]["actions"].extend(_extract_string_literals(args_text))
        intent_vars[var_name]["targets"].extend(_extract_class_refs(args_text))
        component_target = _extract_component_name_target(args_text)
        if component_target:
            intent_vars[var_name]["targets"].append(component_target)
    for match in re.finditer(r"\\b(\\w+)\\.setAction\\s*\\(\\s*[\"']([^\"']+)[\"']\\s*\\)", function_text):
        var_name, action = match.group(1), match.group(2)
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        if action not in intent_vars[var_name]["actions"]:
            intent_vars[var_name]["actions"].append(action)
    for match in re.finditer(r"\\b(\\w+)\\.action\\s*=\\s*[\"']([^\"']+)[\"']\\s*", function_text):
        var_name, action = match.group(1), match.group(2)
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        if action not in intent_vars[var_name]["actions"]:
            intent_vars[var_name]["actions"].append(action)
    for match in re.finditer(r"\\b(\\w+)\\.setClassName\\s*\\([^,]*,\\s*[\"']([^\"']+)[\"']\\s*\\)", function_text):
        var_name, target = match.group(1), match.group(2)
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        if target not in intent_vars[var_name]["targets"]:
            intent_vars[var_name]["targets"].append(target)
    for match in re.finditer(r"\\b(\\w+)\\.setComponent\\s*\\(\\s*ComponentName\\s*\\([^\\)]*[\"']([^\"']+)[\"']\\s*\\)\\s*\\)", function_text):
        var_name, target = match.group(1), match.group(2)
        intent_vars.setdefault(var_name, {"actions": [], "targets": []})
        if target not in intent_vars[var_name]["targets"]:
            intent_vars[var_name]["targets"].append(target)
    for call_node in _iter_calls(function_node):
        callee = _extract_call_name(call_node, source_bytes)
        if not callee:
            continue
        short_name = callee.split(".")[-1]
        args_node = _call_argument_node(call_node)
        args_text = _node_text(args_node, source_bytes) if args_node is not None else ""
        implied_actions: List[str] = []
        implied_targets: List[str] = []
        for var_name, meta in intent_vars.items():
            if re.search(rf"\\b{re.escape(var_name)}\\b", args_text):
                implied_actions.extend(meta.get("actions", []))
                implied_targets.extend(meta.get("targets", []))
        if short_name in {
            "startActivity",
            "startActivityForResult",
            "startService",
            "startForegroundService",
        }:
            events.append(
                {
                    "event_type": "start_component",
                    "function_id": function_id,
                    "actions": _extract_string_literals(args_text) + implied_actions,
                    "targets": _extract_class_refs(args_text) + implied_targets,
                    "receiver": None,
                }
            )
        elif short_name in {"sendBroadcast", "sendOrderedBroadcast", "sendStickyBroadcast"}:
            events.append(
                {
                    "event_type": "send_broadcast",
                    "function_id": function_id,
                    "actions": _extract_string_literals(args_text) + implied_actions,
                    "targets": _extract_class_refs(args_text) + implied_targets,
                    "receiver": None,
                }
            )
        elif short_name == "registerReceiver":
            receiver_target = _extract_register_receiver_target(args_text)
            filter_actions = _extract_intentfilter_actions(args_text)
            all_actions = _extract_string_literals(args_text)
            if filter_actions:
                all_actions.extend([a for a in filter_actions if a not in all_actions])
            for action in filter_actions_by_var:
                if action not in all_actions:
                    all_actions.append(action)
            for action in filter_actions_inline:
                if action not in all_actions:
                    all_actions.append(action)
            events.append(
                {
                    "event_type": "register_receiver",
                    "function_id": function_id,
                    "actions": all_actions,
                    "targets": _extract_class_refs(args_text),
                    "receiver": receiver_target,
                }
            )
        elif short_name in {
            "sendMessage",
            "sendEmptyMessage",
            "sendEmptyMessageDelayed",
            "sendMessageDelayed",
            "post",
            "postDelayed",
        }:
            events.append(
                {
                    "event_type": "handler_message",
                    "function_id": function_id,
                    "tokens": _extract_handler_tokens(args_text),
                }
            )
        if short_name in {
            "startActivity",
            "startActivityForResult",
            "startService",
            "startForegroundService",
            "sendBroadcast",
            "sendOrderedBroadcast",
            "sendStickyBroadcast",
        }:
            component_target = _extract_component_name_target(args_text)
            if component_target:
                events.append(
                    {
                        "event_type": "start_component",
                        "function_id": function_id,
                        "actions": [],
                        "targets": [component_target],
                        "receiver": None,
                    }
                )
    return events


def _call_argument_node(call_node):
    for child in call_node.children:
        if child.type in {"value_arguments", "argument_list"}:
            return child
    return call_node.child_by_field_name("arguments") or call_node.child_by_field_name("value_arguments")


def _extract_route_from_args(arg_text: str) -> Optional[str]:
    if not arg_text:
        return None
    match = re.search(r"route\\s*=\\s*[\"']([^\"']+)[\"']", arg_text)
    if match:
        return match.group(1)
    match = re.search(r"[\"']([^\"']+)[\"']", arg_text)
    if match:
        return match.group(1)
    return None


def _find_first_lambda_literal(node) -> Optional[Any]:
    for child in node.children:
        if child.type in {"lambda_literal", "lambda_expression"}:
            return child
        result = _find_first_lambda_literal(child)
        if result is not None:
            return result
    return None


def _iter_call_expressions_in_node(node) -> Iterable[Any]:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            yield current
        for child in reversed(current.children):
            stack.append(child)


def _extract_compose_routes_from_tree(tree, source_bytes: bytes) -> Dict[str, Any]:
    routes: List[str] = []
    start_routes: List[str] = []
    route_targets: List[Dict[str, Any]] = []
    reserved = {"if", "for", "while", "when", "return", "else", "try", "catch", "finally"}

    def callable_names_from_lambda(lambda_node) -> List[str]:
        names: List[str] = []
        for call_node in _iter_call_expressions_in_node(lambda_node):
            callee = _extract_call_name(call_node, source_bytes)
            if not callee:
                continue
            short_name = callee.split(".")[-1]
            if short_name in reserved or short_name == "composable":
                continue
            names.append(callee)
        lambda_text = _node_text(lambda_node, source_bytes)
        for callee, _ in _iter_callable_reference_matches(lambda_text):
            short_name = callee.split(".")[-1]
            if short_name in reserved or short_name == "composable":
                continue
            names.append(callee)
        return names

    for call_node in _find_nodes_by_type(tree.root_node, "call_expression"):
        callee = _extract_call_name(call_node, source_bytes) or ""
        short_name = callee.split(".")[-1]
        if short_name == "composable":
            arg_node = _call_argument_node(call_node)
            arg_text = _node_text(arg_node, source_bytes) if arg_node is not None else ""
            route = _extract_route_from_args(arg_text)
            if not route:
                continue
            routes.append(route)
            lambda_node = _find_first_lambda_literal(call_node)
            targets: List[str] = []
            if lambda_node is not None:
                targets = callable_names_from_lambda(lambda_node)
            if not targets:
                content_match = re.search(
                    r"content\\s*=\\s*\\{\\s*([A-Za-z_][A-Za-z0-9_\\.]*)\\s*\\(",
                    arg_text,
                )
                if content_match:
                    targets = [content_match.group(1)]
            if not targets:
                content_match = re.search(
                    r"content\\s*=\\s*::\\s*([A-Za-z_][A-Za-z0-9_\\.]*)",
                    arg_text,
                )
                if content_match:
                    targets = [content_match.group(1)]
            route_targets.append({"route": route, "targets": targets})
            continue
        if short_name == "NavHost":
            arg_node = _call_argument_node(call_node)
            arg_text = _node_text(arg_node, source_bytes) if arg_node is not None else ""
            match = re.search(
                r"startDestination\\s*=\\s*[\"']([^\"']+)[\"']",
                arg_text,
            )
            if match:
                start_routes.append(match.group(1))

    return {"routes": routes, "start_routes": start_routes, "route_targets": route_targets}


def _infer_component_type(target_name: str) -> Optional[str]:
    short_name = target_name.split(".")[-1]
    if short_name.endswith("Activity"):
        return "activity"
    if short_name.endswith("Fragment"):
        return "fragment"
    if short_name in {"Service", "IntentService", "JobIntentService"} or short_name.endswith("Service"):
        return "service"
    if short_name.endswith("BroadcastReceiver"):
        return "receiver"
    if short_name.endswith("ContentProvider"):
        return "provider"
    if short_name.endswith("Application"):
        return "application"
    if short_name.endswith("ViewModel"):
        return "view_model"
    return None


def _load_or_parse_payload(
    file_path: str,
    root: str,
    parse_cache_root: str,
    parse_cache: bool,
) -> Dict[str, Any]:
    def ensure_text_fields(item: Dict[str, Any]) -> None:
        if "comment" not in item:
            item["comment"] = ""
        if "summary" not in item:
            item["summary"] = item.get("comment") or ""
        if "note" not in item:
            item["note"] = _build_note(
                item.get("code") or "",
                item.get("comment") or "",
                item.get("summary") or "",
            )

    def normalize_cached_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        file_def = payload.get("file_def")
        if isinstance(file_def, dict):
            ensure_text_fields(file_def)
        package_def = payload.get("package_def")
        if isinstance(package_def, dict):
            ensure_text_fields(package_def)
        classes = payload.get("classes")
        if isinstance(classes, list):
            for item in classes:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        functions = payload.get("functions")
        if isinstance(functions, list):
            for item in functions:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        if "compose_routes" not in payload:
            payload["compose_routes"] = {"routes": [], "start_routes": [], "route_targets": []}
        if "android_events" not in payload:
            payload["android_events"] = {"events": []}
        return payload

    rel_path = os.path.relpath(file_path, root)
    cached_payload = None
    signature = None
    if parse_cache:
        signature = file_signature(file_path)
        cached_payload = load_parse_cache(parse_cache_root, rel_path, signature)
    if cached_payload:
        return normalize_cached_payload(cached_payload)
    (
        file_functions,
        file_calls,
        file_classes,
        file_type_edges,
        file_function_types,
        file_relations,
        file_def,
        package_def,
        compose_routes,
        android_events,
    ) = parse_kotlin_file(file_path, root)
    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "classes": [asdict(item) for item in file_classes],
        "type_edges": [asdict(item) for item in file_type_edges],
        "function_types": [asdict(item) for item in file_function_types],
        "relations": [asdict(item) for item in file_relations],
        "file_def": asdict(file_def),
        "package_def": asdict(package_def) if package_def else None,
        "compose_routes": compose_routes,
        "android_events": android_events,
    }
    if parse_cache and signature is not None:
        write_parse_cache(parse_cache_root, rel_path, signature, payload)
    return payload


def _load_event_map(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Event map must be a JSON object")
    if "events" not in data:
        data["events"] = []
    if not isinstance(data["events"], list):
        raise ValueError("Event map 'events' must be a list")
    return data


async def build_call_graph(
    root: str,
    code_writer: Optional['LanguageCodeWriter'],
    qdrant_writer: Optional[QdrantWriter],
    embedder: Optional[CodeEmbedder],
    batch_size: int,
    qdrant_batch_size: int,
    cache_dir: Optional[str],
    keep_cache: bool,
    parse_cache: bool,
    neo4j_batch_size: int,
    neo4j_state_path: Optional[str],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    event_map_path: Optional[str],
    verbose: bool,
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "android_kotlin_analyzer")
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    kotlin_files = _scan_android_kotlin_files(root)
    if verbose:
        print(f"[scan] Found {len(kotlin_files)} Kotlin files under {root}")
    total_files = len(kotlin_files)

    def iter_payloads(log_parse: bool) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(kotlin_files, start=1):
            if log_parse and verbose and (index == 1 or index % 50 == 0 or index == total_files):
                print(f"[parse] {index}/{total_files}: {file_path}")
            yield _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)

    function_index_by_name: Dict[str, List[Dict[str, Any]]] = {}
    function_index_by_qualified: Dict[str, Dict[str, Any]] = {}
    event_nodes: List[Dict[str, Any]] = []
    event_relations: List[Dict[str, Any]] = []
    function_index_by_class_and_name: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    class_index_by_qualified: Dict[str, str] = {}
    class_index_by_name: Dict[str, List[str]] = {}
    class_info_by_id: Dict[str, Dict[str, Any]] = {}
    component_candidates: Dict[str, str] = {}
    composable_functions_by_name: Dict[str, List[str]] = {}
    composable_functions_by_qualified: Dict[str, str] = {}
    composable_functions_by_class_and_name: Dict[Tuple[str, str], List[str]] = {}
    composable_functions_by_file_name: Dict[str, Dict[str, List[str]]] = {}
    composable_functions_by_file_qualified: Dict[str, Dict[str, str]] = {}
    composable_qualified_names: List[str] = []
    file_package_by_path: Dict[str, Optional[str]] = {}
    expected_points = 0

    for payload in iter_payloads(log_parse=True):
        file_def = payload.get("file_def") or {}
        file_path = file_def.get("file_path")
        if file_path and file_path not in file_package_by_path:
            file_package_by_path[file_path] = file_def.get("package_name")
        for class_def in payload["classes"]:
            class_index_by_qualified[class_def["qualified_name"]] = class_def["symbol_id"]
            class_index_by_name.setdefault(class_def["name"].split(".")[-1], []).append(
                class_def["symbol_id"]
            )
            class_info_by_id[class_def["symbol_id"]] = class_def
        for edge in payload["type_edges"]:
            if edge.get("rel_type") != "EXTENDS":
                continue
            component_type = _infer_component_type(edge["target_name"])
            if component_type:
                component_candidates[edge["source_id"]] = component_type
        for func in payload["functions"]:
            expected_points += 1
            entry = {
                "symbol_id": func["symbol_id"],
                "class_name": func["class_name"],
                "package_name": func["package_name"],
            }
            function_index_by_name.setdefault(func["name"], []).append(entry)
            function_index_by_qualified[func["qualified_name"]] = entry
            if func["class_name"]:
                function_index_by_class_and_name.setdefault(
                    (func["class_name"], func["name"]),
                    [],
                ).append(entry)
            annotations = _extract_android_annotations(func.get("code") or "")
            if "Composable" in annotations:
                composable_functions_by_name.setdefault(func["name"], []).append(func["symbol_id"])
                composable_functions_by_qualified[func["qualified_name"]] = func["symbol_id"]
                composable_qualified_names.append(func["qualified_name"])
                if func.get("class_name"):
                    composable_functions_by_class_and_name.setdefault(
                        (func["class_name"], func["name"]),
                        [],
                    ).append(func["symbol_id"])
                if file_path:
                    composable_functions_by_file_name.setdefault(file_path, {}).setdefault(
                        func["name"], []
                    ).append(func["symbol_id"])
                    composable_functions_by_file_qualified.setdefault(file_path, {})[
                        func["qualified_name"]
                    ] = func["symbol_id"]

    if event_map_path:
        event_map = _load_event_map(event_map_path)
    else:
        event_map = {"events": []}

    for event in event_map.get("events", []):
        event_id = _event_id(event)
        event_nodes.append(
            {
                "id": event_id,
                "name": event.get("name") or "",
                "namespace": event.get("namespace") or "",
                "version": event.get("version") or "",
                "payload_schema": event.get("payload_schema") or "",
                "payload_type": event.get("payload_type") or "",
                "payload_version": event.get("payload_version") or "",
                "payload_example": event.get("payload_example") or "",
                "source": event.get("source") or "",
            }
        )

        for emitter in event.get("emits", []) or []:
            if emitter.get("project_id") and emitter.get("project_id") != project_id:
                continue
            func_id = emitter.get("function_id")
            if not func_id:
                qualified = emitter.get("function_qualified")
                if qualified:
                    entry = function_index_by_qualified.get(qualified)
                    if entry:
                        func_id = entry.get("symbol_id")
            if not func_id:
                name = emitter.get("function_name")
                if name:
                    candidates = function_index_by_name.get(name)
                    if candidates:
                        func_id = candidates[0].get("symbol_id")
            if func_id:
                event_relations.append(
                    {
                        "rel_type": "EMITS_EVENT",
                        "source_id": func_id,
                        "target_id": event_id,
                        "props": {
                            "file_path": emitter.get("file_path") or "",
                            "line": int(emitter.get("line") or 0),
                            "column": int(emitter.get("column") or 0),
                            "note": emitter.get("note") or "",
                        },
                    }
                )

        for handler in event.get("handles", []) or []:
            if handler.get("project_id") and handler.get("project_id") != project_id:
                continue
            func_id = handler.get("function_id")
            if not func_id:
                qualified = handler.get("function_qualified")
                if qualified:
                    entry = function_index_by_qualified.get(qualified)
                    if entry:
                        func_id = entry.get("symbol_id")
            if not func_id:
                name = handler.get("function_name")
                if name:
                    candidates = function_index_by_name.get(name)
                    if candidates:
                        func_id = candidates[0].get("symbol_id")
            if func_id:
                event_relations.append(
                    {
                        "rel_type": "HANDLES_EVENT",
                        "source_id": func_id,
                        "target_id": event_id,
                        "props": {
                            "file_path": handler.get("file_path") or "",
                            "line": int(handler.get("line") or 0),
                            "column": int(handler.get("column") or 0),
                            "note": handler.get("note") or "",
                        },
                    }
                )

    external_classes: Dict[str, Dict[str, Any]] = {}

    for payload in iter_payloads(log_parse=False):
        for edge in payload["type_edges"]:
            target_id = None
            target_name = edge["target_name"]
            if "." in target_name:
                target_id = class_index_by_qualified.get(target_name)
            if target_id is None and edge.get("source_package"):
                qualified = f"{edge['source_package']}.{target_name}"
                target_id = class_index_by_qualified.get(qualified)
            if target_id is None:
                candidates = class_index_by_name.get(target_name, [])
                if candidates:
                    target_id = candidates[0]
            if target_id is None:
                if target_name not in external_classes:
                    external_classes[target_name] = {
                        "symbol_id": target_name,
                        "qualified_name": target_name,
                        "name": target_name,
                        "kind": "external",
                        "package_name": None,
                        "file_path": "",
                        "start_line": 0,
                        "end_line": 0,
                        "code": "",
                        "comment": "",
                        "summary": "",
                        "note": "",
                    }

    manifest_defs: List[AndroidManifestDef] = []
    component_defs: List[AndroidComponentDef] = []
    resource_defs: List[AndroidResourceDef] = []
    resource_index: Dict[Tuple[str, str], str] = {}
    gradle_modules: List[GradleModuleDef] = []
    gradle_dependencies: Dict[str, GradleDependencyDef] = {}
    gradle_dep_edges: List[Tuple[str, str, str]] = []

    if code_writer:
        for manifest_path in _scan_android_manifest_files(root):
            manifest_def, components = _parse_android_manifest(manifest_path, root)
            manifest_defs.append(manifest_def)
            for component in components:
                if component.class_name and component.class_name not in class_index_by_qualified:
                    if component.class_name not in external_classes:
                        external_classes[component.class_name] = {
                            "symbol_id": component.class_name,
                            "qualified_name": component.class_name,
                            "name": component.class_name.split(".")[-1],
                            "kind": "external",
                            "package_name": None,
                            "file_path": "",
                            "start_line": 0,
                            "end_line": 0,
                            "code": "",
                            "comment": "",
                            "summary": "",
                            "note": "",
                        }
                component_defs.append(component)

        manifest_component_classes = {comp.class_name for comp in component_defs if comp.class_name}
        for class_id, component_type in component_candidates.items():
            class_def = class_info_by_id.get(class_id)
            if not class_def:
                continue
            qualified = class_def.get("qualified_name")
            if not qualified or qualified in manifest_component_classes:
                continue
            component_id = _component_symbol_id(
                component_type,
                qualified,
                class_def.get("file_path") or "",
                class_def.get("start_line") or 0,
            )
            component_defs.append(
                AndroidComponentDef(
                    symbol_id=component_id,
                    name=class_def.get("name", "").split(".")[-1],
                    component_type=component_type,
                    class_name=qualified,
                    exported=None,
                    process=None,
                    permission=None,
                    enabled=None,
                    direct_boot_aware=None,
                    target_activity=None,
                    intent_actions=[],
                    intent_categories=[],
                    intent_data=[],
                    file_path=class_def.get("file_path") or "",
                    start_line=class_def.get("start_line") or 0,
                    end_line=class_def.get("end_line") or 0,
                    code=class_def.get("code") or "",
                    summary="",
                    note="",
                )
            )

        resource_defs, resource_index = _collect_android_resources(root)

        for gradle_path in _scan_android_gradle_files(root):
            module_def, deps, dep_edges = _parse_gradle_file(gradle_path, root)
            gradle_modules.append(module_def)
            for dep in deps:
                gradle_dependencies.setdefault(dep.symbol_id, dep)
            gradle_dep_edges.extend(dep_edges)

    if code_writer:
        if verbose:
            print("[graph] Writing nodes and relations (streaming)...")
        allowed_rel_types = {
            "CONTAINS",
            "DECLARES",
            "EXTENDS",
            "IMPLEMENTS",
            "TAKES_FUNCTION",
            "USES_RESOURCE",
            "DEPENDS_ON",
            "DECLARES_COMPONENT",
            "ANNOTATED_WITH",
            "DECLARES_ROUTE",
            "STARTS_WITH_ROUTE",
            "ROUTE_CALLS",
            "STARTS_COMPONENT",
            "STARTS_INTENT",
            "SENDS_BROADCAST",
            "REGISTERS_RECEIVER",
            "DECLARES_INTENT_ACTION",
            "SENDS_HANDLER_MESSAGE",
            "ACTION_TARGETS_COMPONENT",
            "EMITS_EVENT",
            "HANDLES_EVENT",
        }

        def pick_candidate(candidates: List[Dict[str, Any]], call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            if call.get("caller_class"):
                for func in candidates:
                    if (
                        func.get("class_name") == call.get("caller_class")
                        and func.get("package_name") == call.get("caller_package")
                    ):
                        return func
            if call.get("caller_package"):
                for func in candidates:
                    if func.get("package_name") == call.get("caller_package"):
                        return func
            if call.get("imports"):
                for func in candidates:
                    if func.get("package_name") and any(
                        imp.startswith(func["package_name"]) for imp in call["imports"]
                    ):
                        return func
            return candidates[0] if candidates else None

        def resolve_callee_id(call: Dict[str, Any]) -> Optional[str]:
            callee_name = call["callee_name"]
            candidate = None

            if "." in callee_name:
                if callee_name in function_index_by_qualified:
                    candidate = function_index_by_qualified[callee_name]
                else:
                    parts = callee_name.split(".")
                    method_name = parts[-1]
                    qualifier = parts[-2] if len(parts) >= 2 else None
                    if qualifier:
                        candidates = function_index_by_class_and_name.get((qualifier, method_name), [])
                        candidate = pick_candidate(candidates, call)
                    if candidate is None:
                        for qual, func in function_index_by_qualified.items():
                            if qual.endswith(callee_name):
                                candidate = func
                                break
            if candidate is None:
                candidates = function_index_by_name.get(callee_name, [])
                candidate = pick_candidate(candidates, call)
            if candidate:
                return candidate["symbol_id"]
            return None

        def resolve_function_name(name: str) -> Optional[str]:
            if name in function_index_by_qualified:
                return function_index_by_qualified[name]["symbol_id"]
            candidates = function_index_by_name.get(name, [])
            if candidates:
                return candidates[0]["symbol_id"]
            return None

        def resolve_class_id(name: str, file_path: Optional[str]) -> Optional[str]:
            if name in class_index_by_qualified:
                return class_index_by_qualified[name]
            if "." not in name and file_path:
                package_name = file_package_by_path.get(file_path)
                if package_name:
                    qualified = f"{package_name}.{name}"
                    if qualified in class_index_by_qualified:
                        return class_index_by_qualified[qualified]
            candidates = class_index_by_name.get(name.split(".")[-1], [])
            if candidates:
                return candidates[0]
            for qualified in class_index_by_qualified:
                if qualified.endswith(name):
                    return class_index_by_qualified[qualified]
            return None

        def resolve_composable_name(name: str, file_path: Optional[str]) -> Optional[str]:
            short_name = name.split(".")[-1]
            if file_path:
                per_file_qualified = composable_functions_by_file_qualified.get(file_path, {})
                if name in per_file_qualified:
                    return per_file_qualified[name]
                per_file_name = composable_functions_by_file_name.get(file_path, {})
                candidates = per_file_name.get(short_name, [])
                if candidates:
                    return candidates[0]
                package_name = file_package_by_path.get(file_path)
                if package_name and "." not in name:
                    qualified = f"{package_name}.{name}"
                    if qualified in composable_functions_by_qualified:
                        return composable_functions_by_qualified[qualified]
            if "." in name:
                parts = name.split(".")
                if len(parts) >= 2:
                    qualifier = parts[-2]
                    candidates = composable_functions_by_class_and_name.get(
                        (qualifier, short_name),
                        [],
                    )
                    if candidates:
                        return candidates[0]
            if name in composable_functions_by_qualified:
                return composable_functions_by_qualified[name]
            candidates = composable_functions_by_name.get(short_name, [])
            if candidates:
                return candidates[0]
            if "." in name:
                for qualified in composable_qualified_names:
                    if qualified.endswith(name):
                        return composable_functions_by_qualified.get(qualified)
            return None

        async def _write_android() -> None:
            node_queries = {
                "projects": """
                UNWIND $rows AS row
                MERGE (p {id: row.id})
                SET p:Project,
                    p.name = row.name,
                    p.language = row.language,
                    p.repo = row.repo,
                    p.root = row.root,
                    p.build_system = row.build_system
                """,
                "packages": """
                UNWIND $rows AS row
                MERGE (p {id: row.id})
                SET p:Package,
                    p.name = row.name,
                    p.start_line = row.start_line,
                    p.end_line = row.end_line,
                    p.code = row.code,
                    p.comment = row.comment,
                    p.summary = row.summary,
                    p.note = row.note,
                    p.project_id = row.project_id,
                    p.project_name = row.project_name,
                    p.language = row.language,
                    p.repo = row.repo,
                    p.build_system = row.build_system
                """,
                "namespaces": """
                UNWIND $rows AS row
                MERGE (n {id: row.id})
                SET n:Namespace,
                    n.name = row.name,
                    n.qualified_name = row.qualified_name,
                    n.file_path = row.file_path,
                    n.start_line = row.start_line,
                    n.end_line = row.end_line,
                    n.code = row.code,
                    n.comment = row.comment,
                    n.summary = row.summary,
                    n.note = row.note,
                    n.project_id = row.project_id,
                    n.project_name = row.project_name,
                    n.language = row.language,
                    n.repo = row.repo,
                    n.build_system = row.build_system
                """,
                "files": """
                UNWIND $rows AS row
                MERGE (f {id: row.id})
                SET f:File,
                    f.path = row.path,
                    f.package_name = row.package_name,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.code = row.code,
                    f.comment = row.comment,
                    f.summary = row.summary,
                    f.note = row.note,
                    f.project_id = row.project_id,
                    f.project_name = row.project_name,
                    f.language = row.language,
                    f.repo = row.repo,
                    f.build_system = row.build_system
                """,
                "classes": """
                UNWIND $rows AS row
                MERGE (c {id: row.id})
                SET c:Class,
                    c.name = row.name,
                    c.qualified_name = row.qualified_name,
                    c.kind = row.kind,
                    c.package_name = row.package_name,
                    c.file_path = row.file_path,
                    c.start_line = row.start_line,
                    c.end_line = row.end_line,
                    c.code = row.code,
                    c.comment = row.comment,
                    c.summary = row.summary,
                    c.note = row.note,
                    c.project_id = row.project_id,
                    c.project_name = row.project_name,
                    c.language = row.language,
                    c.repo = row.repo,
                    c.build_system = row.build_system
                """,
                "function_types": """
                UNWIND $rows AS row
                MERGE (t {id: row.id})
                SET t:FunctionType,
                    t.type_signature = row.type_signature,
                    t.file_path = row.file_path,
                    t.start_line = row.start_line,
                    t.end_line = row.end_line,
                    t.code = row.code,
                    t.project_id = row.project_id,
                    t.project_name = row.project_name,
                    t.language = row.language,
                    t.repo = row.repo,
                    t.build_system = row.build_system
                """,
                "functions": """
                UNWIND $rows AS row
                MERGE (f {id: row.id})
                SET f:Function,
                    f.name = row.name,
                    f.qualified_name = row.qualified_name,
                    f.kind = row.kind,
                    f.class_name = row.class_name,
                    f.package_name = row.package_name,
                    f.file_path = row.file_path,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.arity = row.arity,
                    f.code = row.code,
                    f.comment = row.comment,
                    f.summary = row.summary,
                    f.note = row.note,
                    f.project_id = row.project_id,
                    f.project_name = row.project_name,
                    f.language = row.language,
                    f.repo = row.repo,
                    f.build_system = row.build_system
                """,
                "android_manifests": """
                UNWIND $rows AS row
                MERGE (m {id: row.id})
                SET m:AndroidManifest,
                    m.package_name = row.package_name,
                    m.file_path = row.file_path,
                    m.start_line = row.start_line,
                    m.end_line = row.end_line,
                    m.code = row.code,
                    m.summary = row.summary,
                    m.note = row.note,
                    m.project_id = row.project_id,
                    m.project_name = row.project_name,
                    m.language = row.language,
                    m.repo = row.repo,
                    m.build_system = row.build_system
                """,
                "android_components": """
                UNWIND $rows AS row
                MERGE (c {id: row.id})
                SET c:AndroidComponent,
                    c.name = row.name,
                    c.component_type = row.component_type,
                    c.class_name = row.class_name,
                    c.exported = row.exported,
                    c.process = row.process,
                    c.permission = row.permission,
                    c.enabled = row.enabled,
                    c.direct_boot_aware = row.direct_boot_aware,
                    c.target_activity = row.target_activity,
                    c.intent_actions = row.intent_actions,
                    c.intent_categories = row.intent_categories,
                    c.intent_data = row.intent_data,
                    c.file_path = row.file_path,
                    c.start_line = row.start_line,
                    c.end_line = row.end_line,
                    c.code = row.code,
                    c.summary = row.summary,
                    c.note = row.note,
                    c.project_id = row.project_id,
                    c.project_name = row.project_name,
                    c.language = row.language,
                    c.repo = row.repo,
                    c.build_system = row.build_system
                """,
                "android_resources": """
                UNWIND $rows AS row
                MERGE (r {id: row.id})
                SET r:AndroidResource,
                    r.name = row.name,
                    r.res_type = row.res_type,
                    r.file_path = row.file_path,
                    r.qualifier = row.qualifier,
                    r.summary = row.summary,
                    r.note = row.note,
                    r.project_id = row.project_id,
                    r.project_name = row.project_name,
                    r.language = row.language,
                    r.repo = row.repo,
                    r.build_system = row.build_system
                """,
                "gradle_modules": """
                UNWIND $rows AS row
                MERGE (m {id: row.id})
                SET m:GradleModule,
                    m.name = row.name,
                    m.module_path = row.module_path,
                    m.module_type = row.module_type,
                    m.namespace = row.namespace,
                    m.application_id = row.application_id,
                    m.file_path = row.file_path,
                    m.summary = row.summary,
                    m.note = row.note,
                    m.project_id = row.project_id,
                    m.project_name = row.project_name,
                    m.language = row.language,
                    m.repo = row.repo,
                    m.build_system = row.build_system
                """,
                "gradle_dependencies": """
                UNWIND $rows AS row
                MERGE (d {id: row.id})
                SET d:GradleDependency,
                    d.coordinate = row.coordinate,
                    d.group = row.group,
                    d.artifact = row.artifact,
                    d.version = row.version,
                    d.summary = row.summary,
                    d.note = row.note,
                    d.project_id = row.project_id,
                    d.project_name = row.project_name,
                    d.language = row.language,
                    d.repo = row.repo,
                    d.build_system = row.build_system
                """,
                "android_annotations": """
                UNWIND $rows AS row
                MERGE (a {id: row.id})
                SET a:AndroidAnnotation,
                    a.name = row.name,
                    a.summary = row.summary,
                    a.note = row.note,
                    a.project_id = row.project_id,
                    a.project_name = row.project_name,
                    a.language = row.language,
                    a.repo = row.repo,
                    a.build_system = row.build_system
                """,
                "android_nav_routes": """
                UNWIND $rows AS row
                MERGE (r {id: row.id})
                SET r:AndroidNavRoute,
                    r.route = row.route,
                    r.file_path = row.file_path,
                    r.summary = row.summary,
                    r.note = row.note,
                    r.project_id = row.project_id,
                    r.project_name = row.project_name,
                    r.language = row.language,
                    r.repo = row.repo,
                    r.build_system = row.build_system
                """,
                "android_intent_actions": """
                UNWIND $rows AS row
                MERGE (a {id: row.id})
                SET a:AndroidIntentAction,
                    a.action = row.action,
                    a.summary = row.summary,
                    a.note = row.note,
                    a.project_id = row.project_id,
                    a.project_name = row.project_name,
                    a.language = row.language,
                    a.repo = row.repo,
                    a.build_system = row.build_system
                """,
                "android_handler_messages": """
                UNWIND $rows AS row
                MERGE (m {id: row.id})
                SET m:AndroidHandlerMessage,
                    m.token = row.token,
                    m.summary = row.summary,
                    m.note = row.note,
                    m.project_id = row.project_id,
                    m.project_name = row.project_name,
                    m.language = row.language,
                    m.repo = row.repo,
                    m.build_system = row.build_system
                """,
                "events": """
                UNWIND $rows AS row
                MERGE (e {id: row.id})
                SET e:Event,
                    e.name = row.name,
                    e.namespace = row.namespace,
                    e.version = row.version,
                    e.payload_schema = row.payload_schema,
                    e.payload_type = row.payload_type,
                    e.payload_version = row.payload_version,
                    e.payload_example = row.payload_example,
                    e.source = row.source
                """,
            }

            node_rows: Dict[str, List[Dict[str, Any]]] = {label: [] for label in node_queries}

            seen_packages: set[str] = set()
            seen_namespaces: set[str] = set()
            seen_resources: set[str] = set()
            resource_refs_by_file: Dict[str, List[str]] = {}
            annotation_defs: Dict[str, AndroidAnnotationDef] = {}
            annotation_relations: List[Tuple[str, str, str]] = []
            nav_route_defs: Dict[str, AndroidNavRouteDef] = {}
            nav_routes_by_file: Dict[str, List[str]] = {}
            nav_start_routes_by_file: Dict[str, List[str]] = {}
            nav_route_targets_by_file: Dict[str, List[Tuple[str, List[str]]]] = {}
            intent_action_defs: Dict[str, AndroidIntentActionDef] = {}
            handler_message_defs: Dict[str, AndroidHandlerMessageDef] = {}
            android_event_relations: List[Dict[str, Any]] = []
            intent_action_to_components: Dict[str, List[str]] = {}
            project_added = False

            def add_node_row(label: str, row: Dict[str, object]) -> None:
                node_rows[label].append(row)

            for event_def in event_nodes:
                add_node_row("events", event_def)

            for manifest_def in manifest_defs:
                add_node_row(
                    "android_manifests",
                    {
                        "id": manifest_def.symbol_id,
                        "package_name": manifest_def.package_name,
                        "file_path": manifest_def.file_path,
                        "start_line": manifest_def.start_line,
                        "end_line": manifest_def.end_line,
                        "code": manifest_def.code,
                        "summary": manifest_def.summary,
                        "note": manifest_def.note,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )
            for component_def in component_defs:
                add_node_row(
                    "android_components",
                    {
                        "id": component_def.symbol_id,
                        "name": component_def.name,
                        "component_type": component_def.component_type,
                        "class_name": component_def.class_name,
                        "exported": component_def.exported,
                        "process": component_def.process,
                        "permission": component_def.permission,
                        "enabled": component_def.enabled,
                        "direct_boot_aware": component_def.direct_boot_aware,
                        "target_activity": component_def.target_activity,
                        "intent_actions": component_def.intent_actions,
                        "intent_categories": component_def.intent_categories,
                        "intent_data": component_def.intent_data,
                        "file_path": component_def.file_path,
                        "start_line": component_def.start_line,
                        "end_line": component_def.end_line,
                        "code": component_def.code,
                        "summary": component_def.summary,
                        "note": component_def.note,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )
                for action in component_def.intent_actions:
                    if component_def.component_type == "receiver":
                        intent_action_to_components.setdefault(action, []).append(
                            component_def.symbol_id
                        )
                    action_id = _intent_action_symbol_id(action)
                    if action_id not in intent_action_defs:
                        intent_action_defs[action_id] = AndroidIntentActionDef(
                            symbol_id=action_id,
                            action=action,
                            summary="",
                            note="",
                        )
                        add_node_row(
                            "android_intent_actions",
                            {
                                "id": action_id,
                                "action": action,
                                "summary": "",
                                "note": "",
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        )
                    android_event_relations.append(
                        {
                            "source_label": "AndroidComponent",
                            "source_id": component_def.symbol_id,
                            "target_label": "AndroidIntentAction",
                            "target_id": action_id,
                            "rel_type": "DECLARES_INTENT_ACTION",
                            "props": {},
                        }
                    )
            for resource_def in resource_defs:
                if resource_def.symbol_id in seen_resources:
                    continue
                seen_resources.add(resource_def.symbol_id)
                add_node_row(
                    "android_resources",
                    {
                        "id": resource_def.symbol_id,
                        "name": resource_def.name,
                        "res_type": resource_def.res_type,
                        "file_path": resource_def.file_path,
                        "qualifier": resource_def.qualifier,
                        "summary": resource_def.summary,
                        "note": resource_def.note,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )
            for module_def in gradle_modules:
                add_node_row(
                    "gradle_modules",
                    {
                        "id": module_def.symbol_id,
                        "name": module_def.name,
                        "module_path": module_def.module_path,
                        "module_type": module_def.module_type,
                        "namespace": module_def.namespace,
                        "application_id": module_def.application_id,
                        "file_path": module_def.file_path,
                        "summary": module_def.summary,
                        "note": module_def.note,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )
            for dependency_def in gradle_dependencies.values():
                add_node_row(
                    "gradle_dependencies",
                    {
                        "id": dependency_def.symbol_id,
                        "coordinate": dependency_def.coordinate,
                        "group": dependency_def.group,
                        "artifact": dependency_def.artifact,
                        "version": dependency_def.version,
                        "summary": dependency_def.summary,
                        "note": dependency_def.note,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )

            for payload in iter_payloads(log_parse=False):
                if not project_added:
                    add_node_row(
                        "projects",
                        {
                            "id": project_id,
                            "name": project_name,
                            "language": language,
                            "repo": repo,
                            "root": root,
                            "build_system": build_system,
                        },
                    )
                    project_added = True
                file_def = payload["file_def"]
                add_node_row(
                    "files",
                    {
                        "id": file_def["file_path"],
                        "path": file_def["file_path"],
                        "package_name": file_def["package_name"],
                        "start_line": file_def["start_line"],
                        "end_line": file_def["end_line"],
                        "code": file_def["code"],
                        "comment": file_def["comment"],
                        "summary": file_def["summary"],
                        "note": file_def["note"],
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )
                file_id = file_def["file_path"]
                android_events = payload.get("android_events", {}).get("events", [])
                for event in android_events:
                    event_type = event.get("event_type")
                    function_id = event.get("function_id")
                    if not function_id or not event_type:
                        continue
                    for action in event.get("actions", []) or []:
                        action_id = _intent_action_symbol_id(action)
                        if action_id not in intent_action_defs:
                            intent_action_defs[action_id] = AndroidIntentActionDef(
                                symbol_id=action_id,
                                action=action,
                                summary="",
                                note="",
                            )
                            add_node_row(
                                "android_intent_actions",
                                {
                                    "id": action_id,
                                    "action": action,
                                    "summary": "",
                                    "note": "",
                                    "project_id": project_id,
                                    "project_name": project_name,
                                    "language": language,
                                    "repo": repo,
                                    "build_system": build_system,
                                },
                            )
                        rel_type = "STARTS_INTENT"
                        if event_type == "send_broadcast":
                            rel_type = "SENDS_BROADCAST"
                        elif event_type == "register_receiver":
                            rel_type = "REGISTERS_RECEIVER"
                        android_event_relations.append(
                            {
                                "source_label": "Function",
                                "source_id": function_id,
                                "target_label": "AndroidIntentAction",
                                "target_id": action_id,
                                "rel_type": rel_type,
                                "props": {"event_type": event_type},
                            }
                        )
                    for token in event.get("tokens", []) or []:
                        message_id = _handler_message_symbol_id(token)
                        if message_id not in handler_message_defs:
                            handler_message_defs[message_id] = AndroidHandlerMessageDef(
                                symbol_id=message_id,
                                token=token,
                                summary="",
                                note="",
                            )
                            add_node_row(
                                "android_handler_messages",
                                {
                                    "id": message_id,
                                    "token": token,
                                    "summary": "",
                                    "note": "",
                                    "project_id": project_id,
                                    "project_name": project_name,
                                    "language": language,
                                    "repo": repo,
                                    "build_system": build_system,
                                },
                            )
                        android_event_relations.append(
                            {
                                "source_label": "Function",
                                "source_id": function_id,
                                "target_label": "AndroidHandlerMessage",
                                "target_id": message_id,
                                "rel_type": "SENDS_HANDLER_MESSAGE",
                                "props": {"event_type": event_type},
                            }
                        )
                    for target in event.get("targets", []) or []:
                        android_event_relations.append(
                            {
                                "source_label": "Function",
                                "source_id": function_id,
                                "target_label": "Class",
                                "target_id": target,
                                "rel_type": "STARTS_COMPONENT",
                                "props": {"event_type": event_type, "file_path": file_id},
                            }
                        )
                    receiver_target = event.get("receiver")
                    if receiver_target:
                        android_event_relations.append(
                            {
                                "source_label": "Function",
                                "source_id": function_id,
                                "target_label": "Class",
                                "target_id": receiver_target,
                                "rel_type": "REGISTERS_RECEIVER",
                                "props": {"event_type": event_type, "file_path": file_id},
                            }
                        )
                compose_payload = payload.get("compose_routes") or {}
                nav_routes = compose_payload.get("routes") or []
                nav_start_routes = compose_payload.get("start_routes") or []
                nav_route_targets_payload = compose_payload.get("route_targets") or []
                if not nav_routes and not nav_start_routes and not nav_route_targets_payload:
                    nav_routes, nav_start_routes, nav_route_targets = _parse_compose_routes(file_def["code"])
                    nav_route_targets_payload = [
                        {"route": route, "targets": targets}
                        for route, targets in nav_route_targets
                    ]
                nav_route_targets = [
                    (item.get("route") or "", item.get("targets") or [])
                    for item in nav_route_targets_payload
                    if item.get("route")
                ]
                if nav_routes:
                    nav_routes_by_file.setdefault(file_id, [])
                for route in nav_routes:
                    route_id = _nav_route_symbol_id(route)
                    nav_routes_by_file[file_id].append(route_id)
                    if route_id not in nav_route_defs:
                        nav_route_defs[route_id] = AndroidNavRouteDef(
                            symbol_id=route_id,
                            route=route,
                            file_path=file_id,
                            summary="",
                            note="",
                        )
                        add_node_row(
                            "android_nav_routes",
                            {
                                "id": route_id,
                                "route": route,
                                "file_path": file_id,
                                "summary": "",
                                "note": "",
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        )
                if nav_start_routes:
                    nav_start_routes_by_file.setdefault(file_id, [])
                for route in nav_start_routes:
                    route_id = _nav_route_symbol_id(route)
                    nav_start_routes_by_file[file_id].append(route_id)
                    if route_id not in nav_route_defs:
                        nav_route_defs[route_id] = AndroidNavRouteDef(
                            symbol_id=route_id,
                            route=route,
                            file_path=file_id,
                            summary="",
                            note="",
                        )
                if nav_route_targets:
                    nav_route_targets_by_file.setdefault(file_id, [])
                for route, targets in nav_route_targets:
                    route_id = _nav_route_symbol_id(route)
                    nav_route_targets_by_file[file_id].append((route_id, targets))
                    if route_id not in nav_route_defs:
                        nav_route_defs[route_id] = AndroidNavRouteDef(
                            symbol_id=route_id,
                            route=route,
                            file_path=file_id,
                            summary="",
                            note="",
                        )
                        add_node_row(
                            "android_nav_routes",
                            {
                                "id": route_id,
                                "route": route,
                                "file_path": file_id,
                                "summary": "",
                                "note": "",
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        )
                resource_refs = _extract_resource_refs(file_def["code"])
                if resource_refs:
                    resource_refs_by_file.setdefault(file_id, [])
                for res_type, name in resource_refs:
                    resource_id = resource_index.get((res_type, name))
                    if resource_id is None:
                        resource_id = _resource_symbol_id(res_type, name)
                        resource_index[(res_type, name)] = resource_id
                        if resource_id not in seen_resources:
                            seen_resources.add(resource_id)
                            add_node_row(
                                "android_resources",
                                {
                                    "id": resource_id,
                                    "name": name,
                                    "res_type": res_type,
                                    "file_path": "",
                                    "qualifier": "",
                                    "summary": "",
                                    "note": "",
                                    "project_id": project_id,
                                    "project_name": project_name,
                                    "language": language,
                                    "repo": repo,
                                    "build_system": build_system,
                                },
                            )
                    if resource_refs:
                        resource_refs_by_file[file_id].append(resource_id)
                package_def = payload.get("package_def")
                if package_def:
                    package_name = package_def["name"]
                    if package_name not in seen_packages:
                        seen_packages.add(package_name)
                        add_node_row(
                            "packages",
                            {
                                "id": package_def["name"],
                                "name": package_def["name"],
                                "start_line": package_def["start_line"],
                                "end_line": package_def["end_line"],
                                "code": package_def["code"],
                                "comment": package_def["comment"],
                                "summary": package_def["summary"],
                                "note": package_def["note"],
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        )
                    namespace_id = f"namespace::{package_name}"
                    if namespace_id not in seen_namespaces:
                        seen_namespaces.add(namespace_id)
                        namespace_summary = package_def.get("comment") or ""
                        namespace_note = _build_note(
                            package_def.get("code") or "",
                            package_def.get("comment") or "",
                            namespace_summary,
                        )
                        add_node_row(
                            "namespaces",
                            {
                                "id": namespace_id,
                                "name": package_name,
                                "qualified_name": package_name,
                                "file_path": file_def["file_path"],
                                "start_line": package_def["start_line"],
                                "end_line": package_def["end_line"],
                                "code": package_def["code"],
                                "comment": package_def["comment"],
                                "summary": namespace_summary,
                                "note": namespace_note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        )
                for class_def in payload["classes"]:
                    add_node_row(
                        "classes",
                        {
                            "id": class_def["symbol_id"],
                            "name": class_def["name"],
                            "qualified_name": class_def["qualified_name"],
                            "kind": class_def["kind"],
                            "package_name": class_def["package_name"],
                            "file_path": class_def["file_path"],
                            "start_line": class_def["start_line"],
                            "end_line": class_def["end_line"],
                            "code": class_def["code"],
                            "comment": class_def["comment"],
                            "summary": class_def["summary"],
                            "note": class_def["note"],
                            "project_id": project_id,
                            "project_name": project_name,
                            "language": language,
                            "repo": repo,
                            "build_system": build_system,
                        },
                    )
                    annotations = _extract_android_annotations(class_def.get("code") or "")
                    for annotation in annotations:
                        annotation_id = _annotation_symbol_id(annotation)
                        if annotation_id not in annotation_defs:
                            annotation_defs[annotation_id] = AndroidAnnotationDef(
                                symbol_id=annotation_id,
                                name=annotation,
                                summary="",
                                note="",
                            )
                            add_node_row(
                                "android_annotations",
                                {
                                    "id": annotation_id,
                                    "name": annotation,
                                    "summary": "",
                                    "note": "",
                                    "project_id": project_id,
                                    "project_name": project_name,
                                    "language": language,
                                    "repo": repo,
                                    "build_system": build_system,
                                },
                            )
                        annotation_relations.append(("Class", class_def["symbol_id"], annotation_id))
                for func_type in payload["function_types"]:
                    add_node_row(
                        "function_types",
                        {
                            "id": func_type["symbol_id"],
                            "type_signature": func_type["type_signature"],
                            "file_path": func_type["file_path"],
                            "start_line": func_type["start_line"],
                            "end_line": func_type["end_line"],
                            "code": func_type["code"],
                            "project_id": project_id,
                            "project_name": project_name,
                            "language": language,
                            "repo": repo,
                            "build_system": build_system,
                        },
                    )
                for func in payload["functions"]:
                    add_node_row(
                        "functions",
                        {
                            "id": func["symbol_id"],
                            "name": func["name"],
                            "qualified_name": func["qualified_name"],
                            "kind": func["kind"],
                            "class_name": func["class_name"],
                            "package_name": func["package_name"],
                            "file_path": func["file_path"],
                            "start_line": func["start_line"],
                            "end_line": func["end_line"],
                            "arity": func["arity"],
                            "code": func["code"],
                            "comment": func["comment"],
                            "summary": func["summary"],
                            "note": func["note"],
                            "project_id": project_id,
                            "project_name": project_name,
                            "language": language,
                            "repo": repo,
                            "build_system": build_system,
                        },
                    )
                    annotations = _extract_android_annotations(func.get("code") or "")
                    for annotation in annotations:
                        annotation_id = _annotation_symbol_id(annotation)
                        if annotation_id not in annotation_defs:
                            annotation_defs[annotation_id] = AndroidAnnotationDef(
                                symbol_id=annotation_id,
                                name=annotation,
                                summary="",
                                note="",
                            )
                            add_node_row(
                                "android_annotations",
                                {
                                    "id": annotation_id,
                                    "name": annotation,
                                    "summary": "",
                                    "note": "",
                                    "project_id": project_id,
                                    "project_name": project_name,
                                    "language": language,
                                    "repo": repo,
                                    "build_system": build_system,
                                },
                            )
                        annotation_relations.append(("Function", func["symbol_id"], annotation_id))

            for class_def in external_classes.values():
                add_node_row(
                    "classes",
                    {
                        "id": class_def["symbol_id"],
                        "name": class_def["name"],
                        "qualified_name": class_def["qualified_name"],
                        "kind": class_def["kind"],
                        "package_name": class_def["package_name"],
                        "file_path": class_def["file_path"],
                        "start_line": class_def["start_line"],
                        "end_line": class_def["end_line"],
                        "code": class_def["code"],
                        "comment": class_def["comment"],
                        "summary": class_def["summary"],
                        "note": class_def["note"],
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                )

            # node flush now handled in the write phase below

            all_relations: List[Dict[str, Any]] = []

            def add_relation_row(
                source_label: str,
                target_label: str,
                rel_type: str,
                row: Dict[str, object],
            ) -> None:
                if rel_type not in allowed_rel_types:
                    raise ValueError(f"Unsupported relation type: {rel_type}")
                all_relations.append(
                    {
                        "source_id": row["source_id"],
                        "target_id": row["target_id"],
                        "rel_type": rel_type,
                        "properties": row.get("props") or {},
                    }
                )

            all_calls: List[Dict[str, Any]] = []
            seen_project_resources: set[str] = set()

            for event_rel in event_relations:
                add_relation_row(
                    "Function",
                    "Event",
                    event_rel["rel_type"],
                    {
                        "source_id": event_rel["source_id"],
                        "target_id": event_rel["target_id"],
                        "props": event_rel["props"],
                    },
                )

            for manifest_def in manifest_defs:
                add_relation_row(
                    "Project",
                    "AndroidManifest",
                    "CONTAINS",
                    {"source_id": project_id, "target_id": manifest_def.symbol_id, "props": {}},
                )
            for resource_def in resource_defs:
                add_relation_row(
                    "Project",
                    "AndroidResource",
                    "CONTAINS",
                    {"source_id": project_id, "target_id": resource_def.symbol_id, "props": {}},
                )
            for module_def in gradle_modules:
                add_relation_row(
                    "Project",
                    "GradleModule",
                    "CONTAINS",
                    {"source_id": project_id, "target_id": module_def.symbol_id, "props": {}},
                )
            for dependency_def in gradle_dependencies.values():
                add_relation_row(
                    "Project",
                    "GradleDependency",
                    "CONTAINS",
                    {"source_id": project_id, "target_id": dependency_def.symbol_id, "props": {}},
                )
            for action, component_ids in intent_action_to_components.items():
                action_id = _intent_action_symbol_id(action)
                for component_id in component_ids:
                    add_relation_row(
                        "AndroidIntentAction",
                        "AndroidComponent",
                        "ACTION_TARGETS_COMPONENT",
                        {"source_id": action_id, "target_id": component_id, "props": {}},
                    )
            for event in android_event_relations:
                rel_type = event.get("rel_type") or ""
                if not rel_type:
                    continue
                source_label = event.get("source_label") or "Function"
                target_label = event.get("target_label") or "AndroidIntentAction"
                target_id = event.get("target_id")
                if rel_type in {"STARTS_COMPONENT", "REGISTERS_RECEIVER"} and target_label == "Class":
                    props = event.get("props") or {}
                    file_path = props.get("file_path")
                    resolved_id = resolve_class_id(str(target_id), file_path)
                    if resolved_id:
                        target_id = resolved_id
                    else:
                        continue
                add_relation_row(
                    source_label,
                    target_label,
                    rel_type,
                    {
                        "source_id": event.get("source_id"),
                        "target_id": target_id,
                        "props": event.get("props") or {},
                    },
                )
            for source_label, source_id, annotation_id in annotation_relations:
                add_relation_row(
                    source_label,
                    "AndroidAnnotation",
                    "ANNOTATED_WITH",
                    {"source_id": source_id, "target_id": annotation_id, "props": {}},
                )
            for component_def in component_defs:
                add_relation_row(
                    "Project",
                    "AndroidComponent",
                    "CONTAINS",
                    {"source_id": project_id, "target_id": component_def.symbol_id, "props": {}},
                )
                if component_def.file_path.endswith("AndroidManifest.xml"):
                    manifest_id = _manifest_symbol_id(component_def.file_path)
                    add_relation_row(
                        "AndroidManifest",
                        "AndroidComponent",
                        "CONTAINS",
                        {"source_id": manifest_id, "target_id": component_def.symbol_id, "props": {}},
                    )
                if component_def.class_name:
                    class_id = class_index_by_qualified.get(component_def.class_name)
                    if class_id is None:
                        class_id = external_classes.get(component_def.class_name, {}).get("symbol_id")
                    if class_id is None:
                        candidates = class_index_by_name.get(component_def.class_name.split(".")[-1], [])
                        class_id = candidates[0] if candidates else None
                    if class_id:
                        add_relation_row(
                            "AndroidComponent",
                            "Class",
                            "DECLARES_COMPONENT",
                            {"source_id": component_def.symbol_id, "target_id": class_id, "props": {}},
                        )
            for module_id, dep_id, config in gradle_dep_edges:
                add_relation_row(
                    "GradleModule",
                    "GradleDependency",
                    "DEPENDS_ON",
                    {"source_id": module_id, "target_id": dep_id, "props": {"configuration": config}},
                )

            for payload in iter_payloads(log_parse=False):
                file_def = payload["file_def"]
                file_id = file_def["file_path"]
                add_relation_row(
                    "Project",
                    "File",
                    "CONTAINS",
                    {"source_id": project_id, "target_id": file_id, "props": {}},
                )
                for resource_id in resource_refs_by_file.get(file_id, []):
                    if resource_id not in seen_project_resources:
                        seen_project_resources.add(resource_id)
                        add_relation_row(
                            "Project",
                            "AndroidResource",
                            "CONTAINS",
                            {"source_id": project_id, "target_id": resource_id, "props": {}},
                        )
                    add_relation_row(
                        "File",
                        "AndroidResource",
                        "USES_RESOURCE",
                        {"source_id": file_id, "target_id": resource_id, "props": {}},
                    )
                for route_id in nav_routes_by_file.get(file_id, []):
                    add_relation_row(
                        "File",
                        "AndroidNavRoute",
                        "DECLARES_ROUTE",
                        {"source_id": file_id, "target_id": route_id, "props": {}},
                    )
                for route_id in nav_start_routes_by_file.get(file_id, []):
                    add_relation_row(
                        "File",
                        "AndroidNavRoute",
                        "STARTS_WITH_ROUTE",
                        {"source_id": file_id, "target_id": route_id, "props": {}},
                    )
                for route_id, target_names in nav_route_targets_by_file.get(file_id, []):
                    target_id = None
                    for target_name in target_names:
                        target_id = resolve_composable_name(target_name, file_id)
                        if target_id:
                            break
                        target_id = resolve_function_name(target_name)
                        if target_id:
                            break
                    if target_id:
                        add_relation_row(
                            "AndroidNavRoute",
                            "Function",
                            "ROUTE_CALLS",
                            {"source_id": route_id, "target_id": target_id, "props": {}},
                        )
                package_name = file_def.get("package_name")
                if package_name:
                    namespace_id = f"namespace::{package_name}"
                    add_relation_row(
                        "Package",
                        "File",
                        "CONTAINS",
                        {"source_id": package_name, "target_id": file_id, "props": {}},
                    )
                    add_relation_row(
                        "Namespace",
                        "File",
                        "CONTAINS",
                        {"source_id": namespace_id, "target_id": file_id, "props": {}},
                    )
                    add_relation_row(
                        "Package",
                        "Namespace",
                        "CONTAINS",
                        {"source_id": package_name, "target_id": namespace_id, "props": {}},
                    )
                for class_def in payload["classes"]:
                    if class_def.get("file_path"):
                        add_relation_row(
                            "File",
                            "Class",
                            "CONTAINS",
                            {"source_id": file_id, "target_id": class_def["symbol_id"], "props": {}},
                        )
                for func in payload["functions"]:
                    add_relation_row(
                        "File",
                        "Function",
                        "CONTAINS",
                        {"source_id": file_id, "target_id": func["symbol_id"], "props": {}},
                    )
                    if func.get("class_name"):
                        class_id = _class_id(func.get("package_name"), func["class_name"])
                        add_relation_row(
                            "Class",
                            "Function",
                            "DECLARES",
                            {"source_id": class_id, "target_id": func["symbol_id"], "props": {}},
                        )
                for edge in payload["type_edges"]:
                    target_name = edge["target_name"]
                    target_id = None
                    if "." in target_name:
                        target_id = class_index_by_qualified.get(target_name)
                    if target_id is None and edge.get("source_package"):
                        qualified = f"{edge['source_package']}.{target_name}"
                        target_id = class_index_by_qualified.get(qualified)
                    if target_id is None:
                        candidates = class_index_by_name.get(target_name, [])
                        if candidates:
                            target_id = candidates[0]
                    if target_id is None:
                        target_id = target_name
                    add_relation_row(
                        "Class",
                        "Class",
                        edge["rel_type"],
                        {"source_id": edge["source_id"], "target_id": target_id, "props": {}},
                    )
                for rel in payload["relations"]:
                    add_relation_row(
                        rel["source_label"],
                        rel["target_label"],
                        rel["rel_type"],
                        {
                            "source_id": rel["source_id"],
                            "target_id": rel["target_id"],
                            "props": rel["properties"],
                        },
                    )

                for call in payload["calls"]:
                    callee_id = call.get("callee_id") or resolve_callee_id(call)
                    if not callee_id:
                        continue
                    call_file = call.get("caller_file") or file_id
                    call_line = int(call.get("call_line") or 0)
                    call_column = int(call.get("call_column") or 0)
                    call_type = call.get("call_type") or "call_expression"
                    site_id = _call_site_id(
                        call["caller_id"],
                        callee_id,
                        call_file,
                        call_line,
                        call_column,
                        call_type,
                    )
                    all_calls.append(
                        {
                            "caller_id": call["caller_id"],
                            "callee_id": callee_id,
                            "site_id": site_id,
                            "props": {
                                "file_path": call_file,
                                "line": call_line,
                                "column": call_column,
                                "call_type": call_type,
                                "callee_name": call.get("callee_name") or "",
                                "caller_class": call.get("caller_class") or "",
                                "caller_package": call.get("caller_package") or "",
                            },
                        }
                    )

            # --- Write all collected nodes via language_writer ---
            for label, rows in node_rows.items():
                if rows:
                    await code_writer.write_nodes_batch(label, node_queries[label], rows)

            # --- Write all collected relations ---
            await code_writer.write_relations_typed(all_relations)

            # --- Write all collected calls ---
            await code_writer.write_calls_with_site(all_calls)

        await _write_android()
        if neo4j_state_path and os.path.exists(neo4j_state_path):
            os.remove(neo4j_state_path)
        if verbose:
            print("[graph] Write complete")

    if qdrant_writer and embedder:
        if verbose:
            print("[qdrant] Ensuring collection...")
        qdrant_writer.ensure_collection()
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", qdrant_writer.collection)
        points_path = os.path.join(qdrant_cache_root, f"{safe_name}_points.jsonl")
        state_path = os.path.join(qdrant_cache_root, f"{safe_name}_state.json")

        def read_qdrant_state() -> Dict[str, int]:
            if not os.path.exists(state_path):
                return {}
            with open(state_path, "r", encoding="utf-8") as handle:
                return json.load(handle)

        def write_qdrant_state(state: Dict[str, int]) -> None:
            temp_path = f"{state_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
            os.replace(temp_path, state_path)

        state = read_qdrant_state()
        cached_points = state.get("total_points")
        if not os.path.exists(points_path) or cached_points != expected_points:
            if verbose:
                print(f"[cache] Building Qdrant cache at {points_path}")
            with open(points_path, "w", encoding="utf-8") as handle:
                batch_funcs: List[Dict[str, Any]] = []
                batch_index = 0
                total_batches = max(1, (expected_points + batch_size - 1) // batch_size)
                for payload in iter_payloads(log_parse=False):
                    for func in payload["functions"]:
                        batch_funcs.append(func)
                        if len(batch_funcs) < batch_size:
                            continue
                        batch_index += 1
                        if verbose:
                            print(f"[embed] batch {batch_index} / {total_batches}")
                        texts = [item["note"] or item["code"] for item in batch_funcs]
                        vectors = embedder.embed(texts, batch_size=batch_size, verbose=False)
                        for func_item, vector in zip(batch_funcs, vectors):
                            point = {
                                "id": _stable_point_id(func_item["symbol_id"]),
                                "vector": vector,
                                "payload": {
                                    "symbol_id": func_item["symbol_id"],
                                    "qualified_name": func_item["qualified_name"],
                                    "name": func_item["name"],
                                    "kind": func_item["kind"],
                                    "class_name": func_item["class_name"],
                                    "package_name": func_item["package_name"],
                                    "file_path": func_item["file_path"],
                                    "start_line": func_item["start_line"],
                                    "end_line": func_item["end_line"],
                                    "arity": func_item["arity"],
                                    "code": func_item["code"],
                                    "comment": func_item["comment"],
                                    "summary": func_item["summary"],
                                    "note": func_item["note"],
                                    "project_id": project_id,
                                    "project_name": project_name,
                                    "language": language,
                                    "repo": repo,
                                    "build_system": build_system,
                                },
                            }
                            handle.write(json.dumps(point, ensure_ascii=True) + "\n")
                        batch_funcs.clear()
                if batch_funcs:
                    batch_index += 1
                    if verbose:
                        print(f"[embed] batch {batch_index} / {total_batches}")
                    texts = [item["note"] or item["code"] for item in batch_funcs]
                    vectors = embedder.embed(texts, batch_size=batch_size, verbose=False)
                    for func_item, vector in zip(batch_funcs, vectors):
                        point = {
                            "id": _stable_point_id(func_item["symbol_id"]),
                            "vector": vector,
                            "payload": {
                                "symbol_id": func_item["symbol_id"],
                                "qualified_name": func_item["qualified_name"],
                                "name": func_item["name"],
                                "kind": func_item["kind"],
                                "class_name": func_item["class_name"],
                                "package_name": func_item["package_name"],
                                "file_path": func_item["file_path"],
                                "start_line": func_item["start_line"],
                                "end_line": func_item["end_line"],
                                "arity": func_item["arity"],
                                "code": func_item["code"],
                                "comment": func_item["comment"],
                                "summary": func_item["summary"],
                                "note": func_item["note"],
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        }
                        handle.write(json.dumps(point, ensure_ascii=True) + "\n")
            state = {"total_points": expected_points, "upserted": 0}
            write_qdrant_state(state)
        else:
            if verbose:
                print(f"[cache] Using existing Qdrant cache at {points_path}")

        total_points = state.get("total_points", expected_points)
        upserted = state.get("upserted", 0)
        if verbose:
            print(f"[qdrant] Resuming at {upserted}/{total_points}")
        remaining = max(total_points - upserted, 0)
        total_batches = max(1, (remaining + qdrant_batch_size - 1) // qdrant_batch_size)
        batch_index = 0
        line_index = 0
        batch: List[Dict] = []
        with open(points_path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line_index < upserted:
                    line_index += 1
                    continue
                batch.append(json.loads(line))
                line_index += 1
                if len(batch) >= qdrant_batch_size:
                    batch_index += 1
                    if verbose:
                        print(f"[qdrant] Upsert batch {batch_index}/{total_batches}")
                    qdrant_writer.upsert(batch)
                    write_qdrant_state({"total_points": total_points, "upserted": line_index})
                    batch = []
            if batch:
                batch_index += 1
                if verbose:
                    print(f"[qdrant] Upsert batch {batch_index}/{total_batches}")
                qdrant_writer.upsert(batch)
                write_qdrant_state({"total_points": total_points, "upserted": line_index})
        if verbose:
            print("[qdrant] Upsert complete")
        if not keep_cache:
            try:
                os.remove(points_path)
                os.remove(state_path)
            except OSError:
                pass
    if verbose:
        elapsed = time.time() - start_time
        print(f"[done] Total time: {elapsed:.2f}s")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Android Kotlin call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing Kotlin sources")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-pass", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.environ.get("QDRANT_COLLECTION_CODE", "android_kotlin_functions"),
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBEDDING_DEVICE", "auto"))
    parser.add_argument("--batch-size", type=int, default=4) # for embedding - 4 function 1 turn embedding
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=128) # for qdrant upsert - 128 vectors 1 time upsert
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--keep-cache", action="store_true")
    parser.add_argument("--disable-parse-cache", action="store_true")
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--event-map", help="JSON mapping file for cross-project events/IDL")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _resolve_embed_device(requested: str) -> str:
    if requested and requested.lower() not in {"auto", ""}:
        return requested
    if torch.cuda.is_available():
        return "cuda"
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend and mps_backend.is_available():
        return "mps"
    return "cpu"


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    code_writer = None
    driver = None
    if args.neo4j_uri and args.neo4j_user and args.NEO4J_PASS:
        driver = await GraphDriverFactory.create_driver(
            provider=GraphProvider.NEO4J,
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.NEO4J_PASS,
        )
        code_writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )

    qdrant_writer = None
    embedder = None
    if args.qdrant_url:
        args.device = _resolve_embed_device(args.device)
        if args.verbose:
            print(f"[embed] device: {args.device}")
        embedder = CodeEmbedder(args.embed_model, args.device, args.max_embed_chars, args.chunk_embed)
        qdrant_writer = QdrantWriter(
            args.qdrant_url,
            args.qdrant_collection,
            vector_size=embedder.vector_size,
            timeout=args.qdrant_timeout,
            retries=args.qdrant_retries,
            retry_sleep=args.qdrant_retry_sleep,
        )

    parse_cache = not args.disable_parse_cache
    neo4j_state_path = None
    if not args.disable_neo4j_resume:
        cache_root = safe_cache_root(args.cache_dir, "android_kotlin_analyzer")
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "android-kotlin"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""
    if code_writer:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            pass  # CLOC stats now handled directly in build_call_graph
        elif args.verbose:
            print("[cloc] Skipped (cloc not available or failed)")

    try:
        if args.dry_run:
            kotlin_files = _scan_android_kotlin_files(args.root)
            print(f"Dry run: {len(kotlin_files)} Kotlin files found")
            return 0
        await build_call_graph(
            args.root,
            code_writer=code_writer,
            qdrant_writer=qdrant_writer,
            embedder=embedder,
            batch_size=args.batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            cache_dir=args.cache_dir,
            keep_cache=args.keep_cache,
            parse_cache=parse_cache,
            neo4j_batch_size=args.neo4j_batch_size,
            neo4j_state_path=neo4j_state_path,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            event_map_path=args.event_map,
            verbose=args.verbose,
        )
    finally:
        if driver:
            await driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

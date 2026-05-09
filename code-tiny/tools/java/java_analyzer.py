from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
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
    load_state,
    safe_cache_root,
    write_parse_cache,
    write_state,
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
    callee_name: str
    callee_id: Optional[str]


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _extract_identifiers(text: str) -> List[str]:
    return [part for part in re.split(r"[^A-Za-z0-9_]+", text) if part]


def _extract_leading_comment(node, source_bytes: bytes) -> str:
    comment_parts: List[str] = []
    prev = node.prev_sibling
    while prev is not None and prev.type in {"line_comment", "block_comment"}:
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
        if child.type in {"line_comment", "block_comment"}:
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


def _line_from_byte(source_bytes: bytes, byte_index: int) -> int:
    return source_bytes[:byte_index].count(b"\n") + 1


def _node_snippet(node, source_bytes: bytes) -> Tuple[str, int, int]:
    start_byte = node.start_byte
    prev = node.prev_sibling
    while prev is not None and prev.type in {"line_comment", "block_comment"}:
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
    if node.type in {"identifier", "type_identifier"}:
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


def _collect_package_info(tree, source_bytes: bytes) -> Tuple[Optional[str], int, int, str, str]:
    for node in _find_nodes_by_type(tree.root_node, "package_declaration"):
        text = _node_text(node, source_bytes)
        identifiers = [token for token in _extract_identifiers(text) if token != "package"]
        if identifiers:
            snippet, start_line, end_line = _node_snippet(node, source_bytes)
            comment = _extract_leading_comment(node, source_bytes)
            return ".".join(identifiers), start_line, end_line, snippet, comment
    return None, 0, 0, "", ""


def _collect_imports(tree, source_bytes: bytes) -> List[str]:
    imports: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "import_declaration"):
        text = _node_text(node, source_bytes)
        identifiers = [token for token in _extract_identifiers(text) if token not in {"import", "static"}]
        if identifiers:
            imports.append(".".join(identifiers))
    return imports


def _count_parameters(method_node) -> int:
    param_list = method_node.child_by_field_name("parameters")
    if param_list is None:
        return 0
    return sum(1 for child in param_list.children if child.type == "formal_parameter")


def _iter_method_parameters(method_node) -> Iterable:
    param_list = method_node.child_by_field_name("parameters")
    if param_list is None:
        return
    for child in param_list.children:
        if child.type == "formal_parameter":
            yield child


def _extract_method_name(method_node, source_bytes: bytes) -> Optional[str]:
    name_node = method_node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes)
    return _first_identifier(method_node, source_bytes)


def _extract_class_name(class_node, source_bytes: bytes) -> Optional[str]:
    name_node = class_node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes)
    return _first_identifier(class_node, source_bytes)


def _normalize_callee(text: str) -> str:
    callee = text.split("(", 1)[0].strip()
    callee = callee.replace("::", ".")
    callee = re.sub(r"<.*?>", "", callee)
    callee = callee.strip(" .")
    return callee


def _extract_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    name_node = call_node.child_by_field_name("name")
    if name_node is not None:
        name_text = _node_text(name_node, source_bytes)
        object_node = call_node.child_by_field_name("object")
        if object_node is not None:
            obj_text = _node_text(object_node, source_bytes)
            return _normalize_callee(f"{obj_text}.{name_text}")
        return _normalize_callee(name_text)
    text = _node_text(call_node, source_bytes)
    return _normalize_callee(text)


def _is_probably_qualified(type_text: str) -> bool:
    if "." not in type_text:
        return False
    first = type_text.split(".", 1)[0]
    return bool(first) and first[0].islower()


def _constructor_name_from_class(class_name: Optional[str]) -> Optional[str]:
    if not class_name:
        return None
    return class_name.split(".")[-1]


def _constructor_callee_name(type_text: Optional[str]) -> Optional[str]:
    if not type_text:
        return None
    cleaned = re.sub(r"<.*?>", "", type_text).strip()
    if not cleaned:
        return None
    simple = cleaned.split(".")[-1]
    if _is_probably_qualified(cleaned):
        return f"{cleaned}.{simple}"
    return simple


def _extract_constructor_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    type_node = call_node.child_by_field_name("type")
    type_text = None
    if type_node is not None:
        type_text = _node_text(type_node, source_bytes)
    if not type_text:
        text = _node_text(call_node, source_bytes)
        if "new" in text:
            type_text = text.split("new", 1)[1].split("(", 1)[0]
        else:
            type_text = text
    return _constructor_callee_name(type_text)


def _extract_method_reference_name(ref_node, source_bytes: bytes) -> Optional[str]:
    text = _node_text(ref_node, source_bytes)
    if "::" not in text:
        return _normalize_callee(text)
    qualifier, member = text.split("::", 1)
    qualifier = qualifier.strip()
    member = member.strip()
    if member == "new":
        return _constructor_callee_name(qualifier)
    return _normalize_callee(f"{qualifier}.{member}")


def _extract_explicit_constructor_name(
    node,
    source_bytes: bytes,
    class_name: Optional[str],
    class_super_map: Dict[str, List[str]],
) -> Optional[str]:
    text = _node_text(node, source_bytes).strip()
    if text.startswith("this"):
        return _constructor_name_from_class(class_name)
    if text.startswith("super"):
        if not class_name:
            return None
        super_types = class_super_map.get(class_name, [])
        return _constructor_callee_name(super_types[0]) if super_types else None
    return None


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
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "record_declaration": "record",
    }
    return mapping.get(node_type)


def _extract_super_types(class_node, source_bytes: bytes) -> List[str]:
    text = _node_text(class_node, source_bytes)
    if "extends" not in text and "implements" not in text:
        return []
    segment = text
    segment = segment.split("{", 1)[0]
    segment = re.sub(r"<.*?>", "", segment)
    results: List[str] = []
    for keyword in ["extends", "implements"]:
        if keyword in segment:
            after = segment.split(keyword, 1)[1]
            parts = [item.strip() for item in after.split(",") if item.strip()]
            for part in parts:
                name = _extract_type_name(part)
                if name:
                    results.append(name)
    return results


def _extract_type_name(text: str) -> Optional[str]:
    match = re.search(r"[A-Za-z_][A-Za-z0-9_\.]*", text)
    if match:
        return match.group(0)
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
        if node.type == "object_creation_expression":
            class_body = None
            for child in node.children:
                if child.type == "class_body":
                    class_body = child
                    break
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


def _iter_methods(tree, source_bytes: bytes) -> Iterable[Tuple]:
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
        if node.type == "object_creation_expression":
            class_body = None
            for child in node.children:
                if child.type == "class_body":
                    class_body = child
                    break
            if class_body is not None:
                anonymous_name = f"Anonymous@{node.start_point[0] + 1}:{node.start_point[1] + 1}"
                next_stack = list(class_stack)
                next_stack.append(anonymous_name)
                for child in class_body.children:
                    stack.append((child, next_stack))
                continue
        if node.type in {"method_declaration", "constructor_declaration"}:
            active_class = ".".join(class_stack) if class_stack else None
            yield node, active_class
        for child in node.children:
            stack.append((child, class_stack))


def _iter_calls(method_node) -> Iterable:
    for node in _find_nodes_by_type(method_node, "method_invocation"):
        yield node


def _iter_constructor_calls(method_node) -> Iterable:
    for node in _find_nodes_by_type(method_node, "object_creation_expression"):
        yield node


def _iter_explicit_constructor_invocations(method_node) -> Iterable:
    for node in _find_nodes_by_type(method_node, "explicit_constructor_invocation"):
        yield node


def _iter_method_references(method_node) -> Iterable:
    for node in _find_nodes_by_type(method_node, "method_reference"):
        yield node


def _iter_lambdas(method_node) -> Iterable:
    for node in _find_nodes_by_type(method_node, "lambda_expression"):
        yield node


def _get_java_parser() -> Parser:
    if ts_get_parser is not None:
        try:
            return ts_get_parser("java")
        except TypeError:
            pass
        except Exception:
            pass
    try:
        from tree_sitter_java import language as java_language
    except Exception as exc:
        raise RuntimeError(
            "Java parser unavailable. Install 'tree-sitter-java' or pin tree-sitter==0.20.x."
        ) from exc
    language = java_language()
    if not isinstance(language, Language):
        try:
            language = Language(language)
        except Exception as exc:
            raise RuntimeError("Invalid Java language capsule for tree_sitter") from exc
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


def _is_functional_interface_type(type_text: str) -> bool:
    candidates = {
        "Runnable",
        "Callable",
        "Supplier",
        "Consumer",
        "BiConsumer",
        "Function",
        "BiFunction",
        "Predicate",
        "BiPredicate",
        "UnaryOperator",
        "BinaryOperator",
        "Comparator",
    }
    for name in candidates:
        if name in type_text:
            return True
    return False


def parse_java_file(
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
]:
    parser = _get_java_parser()
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
    class_super_map: Dict[str, List[str]] = {}

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
        class_super_map[class_path] = super_types
        if super_types:
            if kind in {"class", "enum", "record", "anonymous"}:
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

    for method_node, class_name in _iter_methods(tree, source_bytes):
        method_name = _extract_method_name(method_node, source_bytes)
        if not method_name:
            continue
        arity = _count_parameters(method_node)
        kind = "constructor" if method_node.type == "constructor_declaration" else "method"
        symbol_id = _symbol_id(package_name, class_name, method_name, arity, rel_path)
        qualified = _qualified_name(package_name, class_name, method_name)
        snippet, start_line, end_line = _node_snippet(method_node, source_bytes)
        comment = _extract_leading_comment(method_node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        functions.append(
            FunctionDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=method_name,
                kind=kind,
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

        for param_node in _iter_method_parameters(method_node):
            param_name, type_text, type_info = _extract_parameter_info(param_node, source_bytes)
            if not type_text:
                continue
            if _is_functional_interface_type(type_text):
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

        for call_node in _iter_calls(method_node):
            callee = _extract_call_name(call_node, source_bytes)
            if not callee:
                continue
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_package=package_name,
                    caller_class=class_name,
                    imports=imports,
                    callee_name=callee,
                    callee_id=None,
                )
            )

        for ctor_node in _iter_constructor_calls(method_node):
            callee = _extract_constructor_call_name(ctor_node, source_bytes)
            if not callee:
                continue
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_package=package_name,
                    caller_class=class_name,
                    imports=imports,
                    callee_name=callee,
                    callee_id=None,
                )
            )

        for ctor_inv in _iter_explicit_constructor_invocations(method_node):
            callee = _extract_explicit_constructor_name(
                ctor_inv, source_bytes, class_name, class_super_map
            )
            if not callee:
                continue
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_package=package_name,
                    caller_class=class_name,
                    imports=imports,
                    callee_name=callee,
                    callee_id=None,
                )
            )

        for ref_node in _iter_method_references(method_node):
            callee = _extract_method_reference_name(ref_node, source_bytes)
            if not callee:
                continue
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_package=package_name,
                    caller_class=class_name,
                    imports=imports,
                    callee_name=callee,
                    callee_id=None,
                )
            )

        for lambda_node in _iter_lambdas(method_node):
            snippet, l_start, l_end = _node_snippet(lambda_node, source_bytes)
            lambda_name = f"{method_name}$lambda@{l_start}:{lambda_node.start_point[1] + 1}"
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
                calls.append(
                    CallEdge(
                        caller_id=lambda_id,
                        caller_file=rel_path,
                        caller_package=package_name,
                        caller_class=class_name,
                        imports=imports,
                        callee_name=callee,
                        callee_id=None,
                    )
                )

            for lambda_ctor in _iter_constructor_calls(lambda_node):
                callee = _extract_constructor_call_name(lambda_ctor, source_bytes)
                if not callee:
                    continue
                calls.append(
                    CallEdge(
                        caller_id=lambda_id,
                        caller_file=rel_path,
                        caller_package=package_name,
                        caller_class=class_name,
                        imports=imports,
                        callee_name=callee,
                        callee_id=None,
                    )
                )

            for lambda_ctor_inv in _iter_explicit_constructor_invocations(lambda_node):
                callee = _extract_explicit_constructor_name(
                    lambda_ctor_inv, source_bytes, class_name, class_super_map
                )
                if not callee:
                    continue
                calls.append(
                    CallEdge(
                        caller_id=lambda_id,
                        caller_file=rel_path,
                        caller_package=package_name,
                        caller_class=class_name,
                        imports=imports,
                        callee_name=callee,
                        callee_id=None,
                    )
                )

            for lambda_ref in _iter_method_references(lambda_node):
                callee = _extract_method_reference_name(lambda_ref, source_bytes)
                if not callee:
                    continue
                calls.append(
                    CallEdge(
                        caller_id=lambda_id,
                        caller_file=rel_path,
                        caller_package=package_name,
                        caller_class=class_name,
                        imports=imports,
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


def _stable_point_id(symbol_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, symbol_id))


def _scan_java_files(root: str) -> List[str]:
    java_files: List[str] = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(".java"):
                java_files.append(os.path.join(dirpath, name))
    return sorted(java_files)


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
    ) = parse_java_file(file_path, root)
    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "classes": [asdict(item) for item in file_classes],
        "type_edges": [asdict(item) for item in file_type_edges],
        "function_types": [asdict(item) for item in file_function_types],
        "relations": [asdict(item) for item in file_relations],
        "file_def": asdict(file_def),
        "package_def": asdict(package_def) if package_def else None,
    }
    if parse_cache and signature is not None:
        write_parse_cache(parse_cache_root, rel_path, signature, payload)
    return payload


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
    verbose: bool,
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "java_analyzer")
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    java_files = _scan_java_files(root)
    if verbose:
        print(f"[scan] Found {len(java_files)} Java files under {root}")
    total_files = len(java_files)

    def iter_payloads(log_parse: bool) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(java_files, start=1):
            if log_parse and verbose and (index == 1 or index % 50 == 0 or index == total_files):
                print(f"[parse] {index}/{total_files}: {file_path}")
            yield _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)

    function_index_by_name: Dict[str, List[Dict[str, Any]]] = {}
    function_index_by_qualified: Dict[str, Dict[str, Any]] = {}
    function_index_by_class_and_name: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    class_index_by_qualified: Dict[str, str] = {}
    class_index_by_name: Dict[str, List[str]] = {}
    expected_points = 0

    for payload in iter_payloads(log_parse=True):
        for class_def in payload["classes"]:
            class_index_by_qualified[class_def["qualified_name"]] = class_def["symbol_id"]
            class_index_by_name.setdefault(class_def["name"].split(".")[-1], []).append(
                class_def["symbol_id"]
            )
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

    if code_writer:
        if verbose:
            print("[graph] Writing nodes and relations (streaming)...")
        allowed_rel_types = {"CONTAINS", "DECLARES", "EXTENDS", "IMPLEMENTS", "TAKES_FUNCTION"}

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

        all_projects = [
            {
                "id": project_id,
                "name": project_name,
                "language": language,
                "repo": repo,
                "root": root,
                "build_system": build_system,
            }
        ]
        all_packages: List[Dict[str, Any]] = []
        all_namespaces: List[Dict[str, Any]] = []
        all_files: List[Dict[str, Any]] = []
        all_classes: List[Dict[str, Any]] = []
        all_function_types: List[Dict[str, Any]] = []
        all_functions: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []

        seen_packages: set = set()
        seen_namespaces: set = set()

        for payload in iter_payloads(log_parse=False):
            file_def = payload["file_def"]
            all_files.append(
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
                }
            )
            file_id = file_def["file_path"]
            all_relations.append(
                {"source_id": project_id, "target_id": file_id, "rel_type": "CONTAINS", "properties": {}}
            )
            package_def = payload.get("package_def")
            if package_def:
                pkg_name = package_def["name"]
                if pkg_name not in seen_packages:
                    seen_packages.add(pkg_name)
                    all_packages.append(
                        {
                            "id": pkg_name,
                            "name": pkg_name,
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
                        }
                    )
                namespace_id = f"namespace::{pkg_name}"
                if namespace_id not in seen_namespaces:
                    seen_namespaces.add(namespace_id)
                    namespace_summary = package_def.get("comment") or ""
                    namespace_note = _build_note(
                        package_def.get("code") or "",
                        package_def.get("comment") or "",
                        namespace_summary,
                    )
                    all_namespaces.append(
                        {
                            "id": namespace_id,
                            "name": pkg_name,
                            "qualified_name": pkg_name,
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
                        }
                    )
                all_relations.append(
                    {"source_id": pkg_name, "target_id": file_id, "rel_type": "CONTAINS", "properties": {}}
                )
                all_relations.append(
                    {"source_id": namespace_id, "target_id": file_id, "rel_type": "CONTAINS", "properties": {}}
                )
                all_relations.append(
                    {"source_id": pkg_name, "target_id": namespace_id, "rel_type": "CONTAINS", "properties": {}}
                )
            for class_def in payload["classes"]:
                all_classes.append(
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
                    }
                )
                if class_def.get("file_path"):
                    all_relations.append(
                        {"source_id": file_id, "target_id": class_def["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                    )
            for func_type in payload["function_types"]:
                all_function_types.append(
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
                    }
                )
            for func in payload["functions"]:
                all_functions.append(
                    {
                        "id": func["symbol_id"],
                        "name": func["name"],
                        "qualified_name": func["qualified_name"],
                        "kind": func["kind"],
                        "class_name": func["class_name"],
                        "package_name": func["package_name"],
                        "scope_name": None,
                        "file_path": func["file_path"],
                        "start_line": func["start_line"],
                        "end_line": func["end_line"],
                        "arity": func["arity"],
                        "code": func["code"],
                        "comment": func["comment"],
                        "summary": func["summary"],
                        "note": func["note"],
                        "exported": False,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": func["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                )
                if func.get("class_name"):
                    class_id = _class_id(func.get("package_name"), func["class_name"])
                    all_relations.append(
                        {"source_id": class_id, "target_id": func["symbol_id"], "rel_type": "DECLARES", "properties": {}}
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
                if edge["rel_type"] in allowed_rel_types:
                    all_relations.append(
                        {
                            "source_id": edge["source_id"],
                            "target_id": target_id,
                            "rel_type": edge["rel_type"],
                            "properties": {},
                        }
                    )
            for rel in payload["relations"]:
                if rel["rel_type"] in allowed_rel_types:
                    all_relations.append(
                        {
                            "source_id": rel["source_id"],
                            "target_id": rel["target_id"],
                            "rel_type": rel["rel_type"],
                            "properties": rel["properties"],
                        }
                    )
            for call in payload["calls"]:
                callee_id = call.get("callee_id") or resolve_callee_id(call)
                if callee_id:
                    all_calls.append({"caller_id": call["caller_id"], "callee_id": callee_id})

        for class_def in external_classes.values():
            all_classes.append(
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
                }
            )

        await code_writer.write_all(            projects=all_projects,
            packages=all_packages or None,
            namespaces=all_namespaces or None,
            files=all_files or None,
            classes=all_classes or None,
            function_types=all_function_types or None,
            functions=all_functions or None,
            relations=all_relations or None,
            calls=all_calls or None,
            use_full_writers=True,
            files_variant="with_package",
        )
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
    parser = argparse.ArgumentParser(description="Java call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing Java sources")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "java_functions"))
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("EMBED_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
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
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    code_writer = None
    driver = None
    if args.neo4j_uri and args.neo4j_user and args.neo4j_password:
        driver = await GraphDriverFactory.create_driver(
            provider=GraphProvider.NEO4J,
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_password,
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
        cache_root = safe_cache_root(args.cache_dir, "java_analyzer")
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "java"
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
            java_files = _scan_java_files(args.root)
            print(f"Dry run: {len(java_files)} Java files found")
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
            verbose=args.verbose,
        )
    finally:
        if driver:
            await driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

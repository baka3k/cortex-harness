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
    scope_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    arity: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class FileDef:
    file_path: str
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
    code: str


@dataclass
class TemplateDef:
    symbol_id: str
    name: str
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
class CallEdge:
    caller_id: str
    caller_file: str
    caller_scope: Optional[str]
    call_line: int
    call_column: int
    call_type: str
    call_arity: int
    callee_name: str
    callee_id: Optional[str]


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _extract_leading_comment(node, source_bytes: bytes) -> str:
    comment_parts: List[str] = []
    prev = node.prev_sibling
    while prev is not None and prev.type == "comment":
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
        if child.type == "comment":
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
    while prev is not None and prev.type == "comment":
        start_byte = prev.start_byte
        prev = prev.prev_sibling
    snippet = source_bytes[start_byte : node.end_byte].decode("utf-8", errors="ignore")
    start_line = _line_from_byte(source_bytes, start_byte)
    end_line = node.end_point[0] + 1
    return snippet, start_line, end_line


def _find_nodes_by_type(node, node_type: str) -> Iterable:
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from _find_nodes_by_type(child, node_type)


def _first_identifier(node, source_bytes: bytes) -> Optional[str]:
    if node is None:
        return None
    if node.type in {"identifier", "type_identifier", "field_identifier"}:
        return _node_text(node, source_bytes)
    for child in node.children:
        result = _first_identifier(child, source_bytes)
        if result:
            return result
    return None


def _extract_type_name(text: str) -> Optional[str]:
    match = re.search(r"[A-Za-z_][A-Za-z0-9_:]*", text)
    if match:
        return match.group(0)
    return None


def _normalize_type_signature(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _symbol_id(scope: Optional[str], name: str, arity: int, rel_path: str) -> str:
    qualified = f"{scope}::{name}" if scope else name
    return f"{qualified}/{arity}@{rel_path}"


def _qualified_name(scope: Optional[str], name: str) -> str:
    return f"{scope}::{name}" if scope else name


def _type_id(scope: Optional[str], name: str) -> str:
    return _qualified_name(scope, name)


def _namespace_id(name: str) -> str:
    return f"namespace::{name}"


def _extract_scope_stack(stack: List[str]) -> Optional[str]:
    return "::".join(stack) if stack else None


def _strip_template_args(text: str) -> str:
    return re.sub(r"<[^<>]*>", "", text)


def _normalize_call_name(text: str) -> str:
    cleaned = _strip_template_args(text)
    cleaned = cleaned.replace("this->", "")
    cleaned = cleaned.replace("->", ".")
    cleaned = cleaned.replace("::", "::")
    cleaned = cleaned.replace("&", "").replace("*", "")
    cleaned = cleaned.strip()
    if "." in cleaned:
        cleaned = cleaned.split(".")[-1]
    return cleaned.strip()


def _extract_base_type(type_text: str) -> Optional[str]:
    cleaned = _strip_template_args(type_text)
    cleaned = re.sub(r"\b(const|volatile|mutable|static|extern|register|inline|struct|class|enum|typename)\b", "", cleaned)
    cleaned = cleaned.replace("&", " ").replace("*", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return _extract_type_name(cleaned)


def _pointer_kind(type_text: str) -> Optional[str]:
    if "&&" in type_text:
        return "rvalue_ref"
    if "&" in type_text:
        return "lvalue_ref"
    if "*" in type_text:
        return "pointer"
    return None


def _anonymous_name(prefix: str, node) -> str:
    return f"Anonymous{prefix}@{node.start_point[0] + 1}:{node.start_point[1] + 1}"


def _get_cpp_parser() -> Parser:
    if ts_get_parser is not None:
        try:
            return ts_get_parser("cpp")
        except Exception:
            pass
    try:
        from tree_sitter_cpp import language as cpp_language
    except Exception as exc:
        raise RuntimeError("C++ parser unavailable. Install 'tree-sitter-cpp'.") from exc
    language = cpp_language()
    if not isinstance(language, Language):
        language = Language(language)
    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    return parser


def _get_c_parser() -> Parser:
    if ts_get_parser is not None:
        try:
            return ts_get_parser("c")
        except Exception:
            pass
    try:
        from tree_sitter_c import language as c_language
    except Exception as exc:
        raise RuntimeError("C parser unavailable. Install 'tree-sitter-c'.") from exc
    language = c_language()
    if not isinstance(language, Language):
        language = Language(language)
    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    return parser


def _parse_file(path: str, is_cpp: bool) -> Tuple[Any, bytes]:
    parser = _get_cpp_parser() if is_cpp else _get_c_parser()
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    tree = parser.parse(source_bytes)
    return tree, source_bytes


def _extract_base_types(node, source_bytes: bytes) -> List[str]:
    text = _node_text(node, source_bytes)
    if ":" not in text:
        return []
    after = text.split(":", 1)[1]
    after = after.split("{", 1)[0]
    after = re.sub(r"<.*?>", "", after)
    parts = [part.strip() for part in after.split(",") if part.strip()]
    results: List[str] = []
    for part in parts:
        name = _extract_type_name(part)
        if name:
            results.append(name)
    return results


def _iter_calls(func_node) -> Iterable:
    for node in _find_nodes_by_type(func_node, "call_expression"):
        yield node


def _extract_call_info(call_node, source_bytes: bytes) -> Tuple[Optional[str], str]:
    function_node = call_node.child_by_field_name("function")
    if function_node is not None:
        raw = _node_text(function_node, source_bytes).strip()
        call_type = "call_expression"
        if "->" in raw or "." in raw:
            call_type = "member_call"
        elif "::" in raw:
            call_type = "qualified_call"
        return _normalize_call_name(raw), call_type
    raw = _node_text(call_node, source_bytes).split("(", 1)[0].strip()
    return _normalize_call_name(raw), "call_expression"


def _extract_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    name, _ = _extract_call_info(call_node, source_bytes)
    return name


def _identifier_from_node(node, source_bytes: bytes) -> Optional[str]:
    if node is None:
        return None
    if node.type in {"identifier", "field_identifier", "scoped_identifier", "namespace_identifier"}:
        return _node_text(node, source_bytes).strip()
    text = _node_text(node, source_bytes).strip()
    match = re.search(r"[A-Za-z_][A-Za-z0-9_:]*", text)
    return match.group(0) if match else None


def _call_arity(call_node) -> int:
    args = call_node.child_by_field_name("arguments")
    if args is None:
        for child in call_node.children:
            if child.type == "argument_list":
                args = child
                break
    if args is None:
        return 0
    return sum(1 for child in args.children if child.is_named)


def _collect_fp_aliases(func_node, source_bytes: bytes) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for assign in _find_nodes_by_type(func_node, "assignment_expression"):
        left = assign.child_by_field_name("left")
        right = assign.child_by_field_name("right")
        left_name = _identifier_from_node(left, source_bytes)
        right_name = _identifier_from_node(right, source_bytes)
        if left_name and right_name:
            aliases[left_name] = right_name
    return aliases


def _extract_function_name(declarator, source_bytes: bytes) -> Optional[str]:
    if declarator is None:
        return None
    for child in declarator.children:
        if child.type == "operator_name":
            return _node_text(child, source_bytes).strip()
    name = _first_identifier(declarator, source_bytes)
    if name:
        text = _node_text(declarator, source_bytes)
        if f"~{name}" in text:
            return f"~{name}"
        return name
    text = _node_text(declarator, source_bytes)
    op_match = re.search(r"operator\s*([^\s(]+)", text)
    if op_match:
        return f"operator{op_match.group(1)}"
    return None


def _extract_param_name_from_text(text: str) -> Optional[str]:
    # Function pointer: int (*cb)(int)
    match = re.search(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", text)
    if match:
        return match.group(1)
    cleaned = re.sub(r"[&*(),<>]", " ", text)
    parts = [part for part in re.split(r"\s+", cleaned.strip()) if part]
    if not parts:
        return None
    return parts[-1]


def _extract_using_namespace(text: str) -> Optional[str]:
    match = re.search(r"\busing\s+namespace\s+([A-Za-z_][A-Za-z0-9_:]*)", text)
    if match:
        return match.group(1)
    return None


def _extract_using_qualified(text: str) -> Optional[str]:
    match = re.search(r"\busing\s+([A-Za-z_][A-Za-z0-9_:]*)", text)
    if match:
        qualified = match.group(1)
        if qualified != "namespace":
            return qualified
    return None


def _extract_includes(text: str) -> List[str]:
    includes: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("#include"):
            continue
        match = re.search(r"#include\s+[<\"]([^>\"]+)[>\"]", line)
        if match:
            includes.append(match.group(1))
    return includes


def _extract_macros(text: str) -> Dict[str, str]:
    macros: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("#define"):
            continue
        match = re.match(r"#define\s+([A-Za-z_][A-Za-z0-9_]*)(\s*\([^)]*\))?\s+(.*)", line)
        if match:
            name = match.group(1)
            expansion = match.group(3).strip()
            if name and expansion:
                macros[name] = expansion
    return macros


def _register_type_usage(
    owner_id: str,
    owner_label: str,
    type_text: str,
    rel_path: str,
    types: List[TypeDef],
    relations: List[RelationEdge],
    type_registry: Dict[str, TypeDef],
) -> None:
    base = _extract_base_type(type_text)
    if not base:
        return
    type_id = _type_id(None, base)
    if type_id not in type_registry:
        types.append(
            TypeDef(
                symbol_id=type_id,
                qualified_name=base,
                name=base.split("::")[-1],
                kind="external",
                file_path=rel_path,
                start_line=0,
                end_line=0,
                code=base,
            )
        )
        type_registry[type_id] = types[-1]
    kind = _pointer_kind(type_text)
    rel_type = "POINTER_TO" if kind else "USES_TYPE"
    relations.append(
        RelationEdge(
            source_id=owner_id,
            source_label=owner_label,
            target_id=type_id,
            target_label="Type",
            rel_type=rel_type,
            properties={"kind": kind} if kind else {},
        )
    )


def _find_function_pointer_types(node, source_bytes: bytes) -> List[str]:
    types: List[str] = []
    for declarator in _find_nodes_by_type(node, "function_declarator"):
        text = _node_text(declarator, source_bytes)
        if "(*" in text:
            types.append(_normalize_type_signature(text))
    return types


def _walk_tree(
    node,
    source_bytes: bytes,
    rel_path: str,
    namespace_stack: List[str],
    type_stack: List[str],
    using_namespaces: List[str],
    using_imports: Dict[str, str],
    namespaces: List[NamespaceDef],
    types: List[TypeDef],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    func_types: Dict[str, FunctionTypeDef],
    fields: List[FieldDef],
    aliases: List[AliasDef],
    templates: List[TemplateDef],
    type_registry: Dict[str, TypeDef],
    namespace_registry: Dict[str, NamespaceDef],
) -> None:
    if node.type in {"using_directive", "using_declaration"}:
        text = _node_text(node, source_bytes)
        ns_name = _extract_using_namespace(text)
        if ns_name:
            using_namespaces.append(ns_name)
            return
        qualified = _extract_using_qualified(text)
        if qualified and "::" in qualified:
            short = qualified.split("::")[-1]
            using_imports[short] = qualified
        return
    if node.type == "template_declaration":
        name = _anonymous_name("Template", node)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        template_id = f"template::{rel_path}:{start_line}:{end_line}"
        templates.append(
            TemplateDef(
                symbol_id=template_id,
                name=name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=snippet,
            )
        )
        for child in node.children:
            if child.type in {
                "class_specifier",
                "struct_specifier",
                "union_specifier",
                "enum_specifier",
                "function_definition",
                "function_declaration",
            }:
                _walk_tree(
                    child,
                    source_bytes,
                    rel_path,
                    namespace_stack,
                    type_stack,
                    list(using_namespaces),
                    dict(using_imports),
                    namespaces,
                    types,
                    functions,
                    relations,
                    calls,
                    func_types,
                    fields,
                    aliases,
                    templates,
                    type_registry,
                    namespace_registry,
                )
                target_name = _extract_function_name(child.child_by_field_name("declarator"), source_bytes)
                target_id = None
                target_label = None
                if child.type in {"function_definition", "function_declaration"} and target_name:
                    scope = _extract_scope_stack(namespace_stack + type_stack)
                    arity = 0
                    declarator = child.child_by_field_name("declarator")
                    if declarator is not None:
                        params = None
                        for grand in declarator.children:
                            if grand.type == "parameter_list":
                                params = grand
                                break
                        if params is not None:
                            arity = sum(1 for grand in params.children if grand.type == "parameter_declaration")
                    target_id = _symbol_id(scope, target_name, arity, rel_path)
                    target_label = "Function"
                if child.type in {"class_specifier", "struct_specifier", "union_specifier", "enum_specifier"}:
                    tname = _first_identifier(child, source_bytes) or _anonymous_name("Type", child)
                    qualified = "::".join(namespace_stack + type_stack + [tname]) if (namespace_stack or type_stack) else tname
                    target_id = _type_id(None, qualified)
                    target_label = "Type"
                if target_id and target_label:
                    relations.append(
                        RelationEdge(
                            source_id=template_id,
                            source_label="Template",
                            target_id=target_id,
                            target_label=target_label,
                            rel_type="TEMPLATES",
                            properties={},
                        )
                    )
                return
        return

    if node.type == "namespace_definition":
        name = _first_identifier(node, source_bytes)
        if not name:
            name = _anonymous_name("Namespace", node)
        qualified = "::".join(namespace_stack + [name])
        ns_id = _namespace_id(qualified)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        comment = _extract_leading_comment(node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        namespaces.append(
            NamespaceDef(
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
            )
        )
        namespace_registry[ns_id] = namespaces[-1]
        if namespace_stack:
            parent = _namespace_id("::".join(namespace_stack))
            relations.append(
                RelationEdge(
                    source_id=parent,
                    source_label="Namespace",
                    target_id=ns_id,
                    target_label="Namespace",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
                namespace_stack + [name],
                type_stack,
                list(using_namespaces),
                dict(using_imports),
                namespaces,
                types,
                functions,
                relations,
                calls,
                func_types,
                fields,
                aliases,
                templates,
                type_registry,
                namespace_registry,
            )
        return

    if node.type in {"class_specifier", "struct_specifier", "union_specifier", "enum_specifier"}:
        kind_map = {
            "class_specifier": "class",
            "struct_specifier": "struct",
            "union_specifier": "union",
            "enum_specifier": "enum",
        }
        name = _first_identifier(node, source_bytes)
        kind = kind_map[node.type]
        if not name:
            name = _anonymous_name(kind.capitalize(), node)
            kind = f"anonymous_{kind}"
        qualified = "::".join(namespace_stack + type_stack + [name]) if (namespace_stack or type_stack) else name
        type_id = _type_id(None, qualified)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        comment = _extract_leading_comment(node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        types.append(
            TypeDef(
                symbol_id=type_id,
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
            )
        )
        type_registry[type_id] = types[-1]
        if namespace_stack:
            ns_id = _namespace_id("::".join(namespace_stack))
            relations.append(
                RelationEdge(
                    source_id=ns_id,
                    source_label="Namespace",
                    target_id=type_id,
                    target_label="Type",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
        if type_stack:
            parent_type = _type_id(None, "::".join(namespace_stack + type_stack))
            relations.append(
                RelationEdge(
                    source_id=parent_type,
                    source_label="Type",
                    target_id=type_id,
                    target_label="Type",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
        if kind in {"class", "struct", "anonymous_class", "anonymous_struct"}:
            for base in _extract_base_types(node, source_bytes):
                base_id = _type_id(None, base)
                if base_id not in type_registry:
                    types.append(
                        TypeDef(
                            symbol_id=base_id,
                            qualified_name=base,
                            name=base.split("::")[-1],
                            kind="external",
                            file_path=rel_path,
                            start_line=0,
                            end_line=0,
                            code=base,
                        )
                    )
                    type_registry[base_id] = types[-1]
                relations.append(
                    RelationEdge(
                        source_id=type_id,
                        source_label="Type",
                        target_id=base_id,
                        target_label="Type",
                        rel_type="EXTENDS",
                        properties={},
                    )
                )
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
                namespace_stack,
                type_stack + [name],
                list(using_namespaces),
                dict(using_imports),
                namespaces,
                types,
                functions,
                relations,
                calls,
                func_types,
                fields,
                aliases,
                templates,
                type_registry,
                namespace_registry,
            )
        return

    if node.type == "function_definition":
        scope_stack = namespace_stack + type_stack
        scope = _extract_scope_stack(scope_stack)
        declarator = node.child_by_field_name("declarator")
        name = _extract_function_name(declarator, source_bytes)
        if name:
            arity = 0
            if declarator is not None:
                params = None
                for child in declarator.children:
                    if child.type == "parameter_list":
                        params = child
                        break
                if params is not None:
                    arity = sum(1 for child in params.children if child.type == "parameter_declaration")
            symbol_id = _symbol_id(scope, name, arity, rel_path)
            qualified = _qualified_name(scope, name)
            snippet, start_line, end_line = _node_snippet(node, source_bytes)
            comment = _extract_leading_comment(node, source_bytes)
            summary = comment
            note = _build_note(snippet, comment, summary)
            functions.append(
                FunctionDef(
                    symbol_id=symbol_id,
                    qualified_name=qualified,
                    name=name,
                    kind="destructor" if name.startswith("~") else ("constructor" if type_stack and name == type_stack[-1] else "function"),
                    scope_name=scope,
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
            if namespace_stack:
                ns_id = _namespace_id("::".join(namespace_stack))
                relations.append(
                    RelationEdge(
                        source_id=ns_id,
                        source_label="Namespace",
                        target_id=symbol_id,
                        target_label="Function",
                        rel_type="CONTAINS",
                        properties={},
                    )
                )
            if type_stack:
                type_id = _type_id(None, "::".join(namespace_stack + type_stack))
                relations.append(
                    RelationEdge(
                        source_id=type_id,
                        source_label="Type",
                        target_id=symbol_id,
                        target_label="Function",
                        rel_type="DECLARES",
                        properties={},
                    )
                )
            param_fp_types: Dict[str, str] = {}
            fp_aliases: Dict[str, str] = {}
            if declarator is not None:
                for child in declarator.children:
                    if child.type == "parameter_list":
                        for param in child.children:
                            if param.type == "parameter_declaration":
                                _register_type_usage(
                                    symbol_id,
                                    "Function",
                                    _node_text(param, source_bytes),
                                    rel_path,
                                    types,
                                    relations,
                                    type_registry,
                                )
                                param_text = _node_text(param, source_bytes)
                                if "(*" in param_text or "std::function" in param_text or "function<" in param_text:
                                    param_name = _extract_param_name_from_text(param_text)
                                    fp_sig = _normalize_type_signature(param_text)
                                    fp_id = f"functype::{fp_sig}"
                                    if fp_id not in func_types:
                                        func_types[fp_id] = FunctionTypeDef(
                                            symbol_id=fp_id,
                                            type_signature=fp_sig,
                                            file_path=rel_path,
                                            start_line=start_line,
                                            end_line=end_line,
                                            code=fp_sig,
                                        )
                                    if param_name:
                                        param_fp_types[param_name] = fp_id
                                    relations.append(
                                        RelationEdge(
                                            source_id=symbol_id,
                                            source_label="Function",
                                            target_id=fp_id,
                                            target_label="FunctionType",
                                            rel_type="TAKES_FUNCTION",
                                            properties={"parameter_name": param_name or ""},
                                        )
                                    )
            fp_aliases = _collect_fp_aliases(node, source_bytes)
            for call_node in _iter_calls(node):
                callee, call_type = _extract_call_info(call_node, source_bytes)
                if not callee:
                    continue
                call_line = call_node.start_point[0] + 1
                call_column = call_node.start_point[1] + 1
                call_arity = _call_arity(call_node)
                calls.append(
                    CallEdge(
                        caller_id=symbol_id,
                        caller_file=rel_path,
                        caller_scope=scope,
                        call_line=call_line,
                        call_column=call_column,
                        call_type=call_type,
                        call_arity=call_arity,
                        callee_name=callee,
                        callee_id=None,
                    )
                )
                fp_target = fp_aliases.get(callee)
                if fp_target:
                    calls.append(
                        CallEdge(
                            caller_id=symbol_id,
                            caller_file=rel_path,
                            caller_scope=scope,
                            call_line=call_line,
                            call_column=call_column,
                            call_type="fp_alias",
                            call_arity=call_arity,
                            callee_name=fp_target,
                            callee_id=None,
                        )
                    )
                fp_id = param_fp_types.get(callee)
                if fp_id:
                    relations.append(
                        RelationEdge(
                            source_id=symbol_id,
                            source_label="Function",
                            target_id=fp_id,
                            target_label="FunctionType",
                            rel_type="CALLS_FUNCTION_POINTER",
                            properties={
                                "parameter_name": callee,
                                "line": str(call_line),
                                "column": str(call_column),
                            },
                        )
                    )
            for fp_sig in _find_function_pointer_types(node, source_bytes):
                fp_id = f"functype::{fp_sig}"
                if fp_id not in func_types:
                    func_types[fp_id] = FunctionTypeDef(
                        symbol_id=fp_id,
                        type_signature=fp_sig,
                        file_path=rel_path,
                        start_line=start_line,
                        end_line=end_line,
                        code=fp_sig,
                    )
                relations.append(
                    RelationEdge(
                        source_id=symbol_id,
                        source_label="Function",
                        target_id=fp_id,
                        target_label="FunctionType",
                        rel_type="TAKES_FUNCTION",
                        properties={},
                    )
                )
        return

    if node.type == "function_declaration":
        scope_stack = namespace_stack + type_stack
        scope = _extract_scope_stack(scope_stack)
        declarator = node.child_by_field_name("declarator")
        name = _extract_function_name(declarator, source_bytes)
        if name:
            arity = 0
            if declarator is not None:
                params = None
                for child in declarator.children:
                    if child.type == "parameter_list":
                        params = child
                        break
                if params is not None:
                    arity = sum(1 for child in params.children if child.type == "parameter_declaration")
            symbol_id = _symbol_id(scope, name, arity, rel_path)
            qualified = _qualified_name(scope, name)
            snippet, start_line, end_line = _node_snippet(node, source_bytes)
            comment = _extract_leading_comment(node, source_bytes)
            summary = comment
            note = _build_note(snippet, comment, summary)
            functions.append(
                FunctionDef(
                    symbol_id=symbol_id,
                    qualified_name=qualified,
                    name=name,
                    kind="declaration",
                    scope_name=scope,
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
            if namespace_stack:
                ns_id = _namespace_id("::".join(namespace_stack))
                relations.append(
                    RelationEdge(
                        source_id=ns_id,
                        source_label="Namespace",
                        target_id=symbol_id,
                        target_label="Function",
                        rel_type="CONTAINS",
                        properties={},
                    )
                )
            if type_stack:
                type_id = _type_id(None, "::".join(namespace_stack + type_stack))
                relations.append(
                    RelationEdge(
                        source_id=type_id,
                        source_label="Type",
                        target_id=symbol_id,
                        target_label="Function",
                        rel_type="DECLARES",
                        properties={},
                    )
                )
            if declarator is not None:
                for child in declarator.children:
                    if child.type == "parameter_list":
                        for param in child.children:
                            if param.type == "parameter_declaration":
                                _register_type_usage(
                                    symbol_id,
                                    "Function",
                                    _node_text(param, source_bytes),
                                    rel_path,
                                    types,
                                    relations,
                                    type_registry,
                                )
        return

    if node.type == "field_declaration":
        scope_stack = namespace_stack + type_stack
        scope = _extract_scope_stack(scope_stack)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        field_name = _first_identifier(node, source_bytes)
        if field_name:
            field_id = f"{scope}::{field_name}@{rel_path}" if scope else f"{field_name}@{rel_path}"
            fields.append(
                FieldDef(
                    symbol_id=field_id,
                    qualified_name=_qualified_name(scope, field_name),
                    name=field_name,
                    scope_name=scope,
                    type_signature=_normalize_type_signature(_node_text(node, source_bytes)),
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    code=snippet,
                )
            )
            if type_stack:
                type_id = _type_id(None, "::".join(namespace_stack + type_stack))
                relations.append(
                    RelationEdge(
                        source_id=type_id,
                        source_label="Type",
                        target_id=field_id,
                        target_label="Field",
                        rel_type="DECLARES",
                        properties={},
                    )
                )
            _register_type_usage(
                field_id,
                "Field",
                _node_text(node, source_bytes),
                rel_path,
                types,
                relations,
                type_registry,
            )
        return

    if node.type in {"type_definition", "alias_declaration", "type_alias_declaration"}:
        kind = "typedef" if node.type == "type_definition" else "using"
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        alias_name = _first_identifier(node, source_bytes) or _anonymous_name("Alias", node)
        scope = _extract_scope_stack(namespace_stack + type_stack)
        alias_id = f"alias::{_qualified_name(scope, alias_name)}@{rel_path}"
        target_name = _extract_base_type(_node_text(node, source_bytes))
        aliases.append(
            AliasDef(
                symbol_id=alias_id,
                qualified_name=_qualified_name(scope, alias_name),
                name=alias_name,
                kind=kind,
                target_name=target_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=snippet,
            )
        )
        if target_name:
            target_id = _type_id(None, target_name)
            if target_id not in type_registry:
                types.append(
                    TypeDef(
                        symbol_id=target_id,
                        qualified_name=target_name,
                        name=target_name.split("::")[-1],
                        kind="external",
                        file_path=rel_path,
                        start_line=0,
                        end_line=0,
                        code=target_name,
                    )
                )
                type_registry[target_id] = types[-1]
            relations.append(
                RelationEdge(
                    source_id=alias_id,
                    source_label="Alias",
                    target_id=target_id,
                    target_label="Type",
                    rel_type="ALIASES",
                    properties={"kind": kind},
                )
            )
        return

    if node.type == "namespace_alias_definition":
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        alias_name = _first_identifier(node, source_bytes) or _anonymous_name("NamespaceAlias", node)
        scope = _extract_scope_stack(namespace_stack + type_stack)
        alias_id = f"alias::{_qualified_name(scope, alias_name)}@{rel_path}"
        target_name = None
        text = _node_text(node, source_bytes)
        parts = text.split("=", 1)
        if len(parts) == 2:
            target_name = _extract_type_name(parts[1])
        aliases.append(
            AliasDef(
                symbol_id=alias_id,
                qualified_name=_qualified_name(scope, alias_name),
                name=alias_name,
                kind="namespace_alias",
                target_name=target_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=snippet,
            )
        )
        if target_name:
            ns_id = _namespace_id(target_name)
            if ns_id not in namespace_registry:
                namespaces.append(
                    NamespaceDef(
                        symbol_id=ns_id,
                        qualified_name=target_name,
                        name=target_name.split("::")[-1],
                        file_path=rel_path,
                        start_line=0,
                        end_line=0,
                        code=target_name,
                    )
                )
                namespace_registry[ns_id] = namespaces[-1]
            relations.append(
                RelationEdge(
                    source_id=alias_id,
                    source_label="Alias",
                    target_id=ns_id,
                    target_label="Namespace",
                    rel_type="ALIASES",
                    properties={"kind": "namespace_alias"},
                )
            )
        return

    for child in node.children:
        _walk_tree(
            child,
            source_bytes,
            rel_path,
            namespace_stack,
            type_stack,
            list(using_namespaces),
            dict(using_imports),
            namespaces,
            types,
            functions,
            relations,
            calls,
            func_types,
            fields,
            aliases,
            templates,
            type_registry,
            namespace_registry,
        )


def parse_c_family_file(
    path: str,
    root: str,
    is_cpp: bool,
) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[TypeDef],
    List[NamespaceDef],
    List[RelationEdge],
    List[FunctionTypeDef],
    List[FieldDef],
    List[AliasDef],
    List[TemplateDef],
    FileDef,
    List[str],
    Dict[str, str],
    List[str],
    Dict[str, str],
]:
    tree, source_bytes = _parse_file(path, is_cpp)
    rel_path = os.path.relpath(path, root)
    file_code = source_bytes.decode("utf-8", errors="ignore")
    file_lines = file_code.count("\n") + 1
    file_comment = _extract_file_comment(tree, source_bytes)
    file_summary = file_comment
    file_note = _build_note(file_code, file_comment, file_summary)
    file_def = FileDef(
        file_path=rel_path,
        start_line=1,
        end_line=file_lines,
        code=file_code,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
    )
    file_includes = _extract_includes(file_code)
    file_macros = _extract_macros(file_code)

    functions: List[FunctionDef] = []
    calls: List[CallEdge] = []
    types: List[TypeDef] = []
    namespaces: List[NamespaceDef] = []
    relations: List[RelationEdge] = []
    func_types: Dict[str, FunctionTypeDef] = {}
    fields: List[FieldDef] = []
    aliases: List[AliasDef] = []
    templates: List[TemplateDef] = []
    type_registry: Dict[str, TypeDef] = {}
    namespace_registry: Dict[str, NamespaceDef] = {}
    using_namespaces: List[str] = []
    using_imports: Dict[str, str] = {}

    _walk_tree(
        tree.root_node,
        source_bytes,
        rel_path,
        [],
        [],
        using_namespaces,
        using_imports,
        namespaces,
        types,
        functions,
        relations,
        calls,
        func_types,
        fields,
        aliases,
        templates,
        type_registry,
        namespace_registry,
    )

    return (
        functions,
        calls,
        types,
        namespaces,
        relations,
        list(func_types.values()),
        fields,
        aliases,
        templates,
        file_def,
        using_namespaces,
        using_imports,
        file_includes,
        file_macros,
    )


def _resolve_calls(functions: List[FunctionDef], calls: List[CallEdge]) -> None:
    by_name: Dict[str, List[FunctionDef]] = {}
    by_name_arity: Dict[Tuple[str, int], List[FunctionDef]] = {}
    by_scope_name: Dict[Tuple[Optional[str], str], List[FunctionDef]] = {}
    by_scope_name_arity: Dict[Tuple[Optional[str], str, int], List[FunctionDef]] = {}
    by_qualified: Dict[str, FunctionDef] = {}
    by_qualified_arity: Dict[Tuple[str, int], FunctionDef] = {}

    for func in functions:
        by_name.setdefault(func.name, []).append(func)
        by_qualified[func.qualified_name] = func
        by_name_arity.setdefault((func.name, func.arity), []).append(func)
        by_scope_name.setdefault((func.scope_name, func.name), []).append(func)
        by_scope_name_arity.setdefault((func.scope_name, func.name, func.arity), []).append(func)
        by_qualified_arity[(func.qualified_name, func.arity)] = func

    for call in calls:
        callee_name = call.callee_name
        candidate = None
        call_arity = call.call_arity

        def scope_chain(scope: Optional[str]) -> List[Optional[str]]:
            if not scope:
                return [None]
            parts = scope.split("::")
            chain = ["::".join(parts[:idx]) for idx in range(len(parts), 0, -1)]
            chain.append(None)
            return chain

        if "::" in callee_name:
            if (callee_name, call_arity) in by_qualified_arity:
                candidate = by_qualified_arity[(callee_name, call_arity)]
            elif callee_name in by_qualified:
                candidate = by_qualified[callee_name]

        if candidate is None:
            short_name = callee_name.split("::")[-1]
            for scope in scope_chain(call.caller_scope):
                if call_arity >= 0:
                    scoped = by_scope_name_arity.get((scope, short_name, call_arity))
                    if scoped:
                        candidate = scoped[0]
                        break
                scoped = by_scope_name.get((scope, short_name))
                if scoped:
                    candidate = scoped[0]
                    break

        if candidate is None and call_arity >= 0:
            candidates = by_name_arity.get((callee_name.split("::")[-1], call_arity), [])
            if candidates:
                candidate = candidates[0]

        if candidate is None:
            candidates = by_name.get(callee_name.split("::")[-1], [])
            if candidates:
                candidate = candidates[0]
        if candidate:
            call.callee_id = candidate.symbol_id


class _LegacyNeo4jWriter:  # dead code – kept as tombstone only
    def __init__(self, uri: str, user: str, password: str, database: Optional[str]) -> None:
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self) -> None:
        self.driver.close()

    def write(
        self,
        files: List[FileDef],
        namespaces: List[NamespaceDef],
        types: List[TypeDef],
        function_types: List[FunctionTypeDef],
        functions: List[FunctionDef],
        fields: List[FieldDef],
        aliases: List[AliasDef],
        templates: List[TemplateDef],
        relations: List[RelationEdge],
        calls: List[CallEdge],
        verbose: bool = False,
        batch_size: int = 1000,
        state_path: Optional[str] = None,
    ) -> None:
        with self.driver.session(database=self.database) as session:
            state = load_state(state_path) if state_path else {}

            def run_write(query: str, rows: List[Dict[str, object]]) -> None:
                def tx_write(tx):
                    tx.run(query, rows=rows)

                if hasattr(session, "execute_write"):
                    session.execute_write(tx_write)
                    return
                if hasattr(session, "write_transaction"):
                    session.write_transaction(tx_write)
                    return
                session._run_transaction(  # type: ignore[attr-defined]
                    tx_write, access_mode="WRITE"
                )

            def log_progress(label: str, index: int, total: int) -> None:
                if verbose and (index == 1 or index % 1000 == 0 or index == total):
                    print(f"[neo4j] {label} {index}/{total}")

            def write_batches(label: str, rows: List[Dict[str, object]], query: str) -> None:
                start_index = state.get(label, 0) if state_path else 0
                total = len(rows)
                for offset in range(start_index, total, batch_size):
                    batch = rows[offset : offset + batch_size]
                    run_write(query, batch)
                    next_index = offset + len(batch)
                    if state_path:
                        state[label] = next_index
                        write_state(state_path, state)
                    log_progress(label, next_index, total)

            file_rows = [
                {
                    "id": f.file_path,
                    "path": f.file_path,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "code": f.code,
                    "comment": f.comment,
                    "summary": f.summary,
                    "note": f.note,
                }
                for f in files
            ]
            write_batches(
                "files",
                file_rows,
                """
                UNWIND $rows AS row
                MERGE (f:File {id: row.id})
                SET f.path = row.path,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.code = row.code,
                    f.comment = row.comment,
                    f.summary = row.summary,
                    f.note = row.note
                """,
            )

            namespace_rows = [
                {
                    "id": n.symbol_id,
                    "name": n.name,
                    "qualified_name": n.qualified_name,
                    "file_path": n.file_path,
                    "start_line": n.start_line,
                    "end_line": n.end_line,
                    "code": n.code,
                    "comment": n.comment,
                    "summary": n.summary,
                    "note": n.note,
                }
                for n in namespaces
            ]
            write_batches(
                "namespaces",
                namespace_rows,
                """
                UNWIND $rows AS row
                MERGE (n:Namespace {id: row.id})
                SET n.name = row.name,
                    n.qualified_name = row.qualified_name,
                    n.file_path = row.file_path,
                    n.start_line = row.start_line,
                    n.end_line = row.end_line,
                    n.code = row.code,
                    n.comment = row.comment,
                    n.summary = row.summary,
                    n.note = row.note
                """,
            )

            type_rows = [
                {
                    "id": t.symbol_id,
                    "name": t.name,
                    "qualified_name": t.qualified_name,
                    "kind": t.kind,
                    "file_path": t.file_path,
                    "start_line": t.start_line,
                    "end_line": t.end_line,
                    "code": t.code,
                    "comment": t.comment,
                    "summary": t.summary,
                    "note": t.note,
                }
                for t in types
            ]
            write_batches(
                "types",
                type_rows,
                """
                UNWIND $rows AS row
                MERGE (t:Type {id: row.id})
                SET t.name = row.name,
                    t.qualified_name = row.qualified_name,
                    t.kind = row.kind,
                    t.file_path = row.file_path,
                    t.start_line = row.start_line,
                    t.end_line = row.end_line,
                    t.code = row.code,
                    t.comment = row.comment,
                    t.summary = row.summary,
                    t.note = row.note
                FOREACH (_ IN CASE WHEN row.kind IN ['class','struct','anonymous_class','anonymous_struct'] THEN [1] ELSE [] END |
                    SET t:Class
                )
                """,
            )

            function_type_rows = [
                {
                    "id": t.symbol_id,
                    "type_signature": t.type_signature,
                    "file_path": t.file_path,
                    "start_line": t.start_line,
                    "end_line": t.end_line,
                    "code": t.code,
                }
                for t in function_types
            ]
            write_batches(
                "function_types",
                function_type_rows,
                """
                UNWIND $rows AS row
                MERGE (t:FunctionType {id: row.id})
                SET t.type_signature = row.type_signature,
                    t.file_path = row.file_path,
                    t.start_line = row.start_line,
                    t.end_line = row.end_line,
                    t.code = row.code
                """,
            )

            function_rows = [
                {
                    "id": f.symbol_id,
                    "name": f.name,
                    "qualified_name": f.qualified_name,
                    "kind": f.kind,
                    "scope_name": f.scope_name,
                    "file_path": f.file_path,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "arity": f.arity,
                    "code": f.code,
                    "comment": f.comment,
                    "summary": f.summary,
                    "note": f.note,
                }
                for f in functions
            ]
            write_batches(
                "functions",
                function_rows,
                """
                UNWIND $rows AS row
                MERGE (f:Function {id: row.id})
                SET f.name = row.name,
                    f.qualified_name = row.qualified_name,
                    f.kind = row.kind,
                    f.scope_name = row.scope_name,
                    f.file_path = row.file_path,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.arity = row.arity,
                    f.code = row.code,
                    f.comment = row.comment,
                    f.summary = row.summary,
                    f.note = row.note
                """,
            )

            field_rows = [
                {
                    "id": f.symbol_id,
                    "name": f.name,
                    "qualified_name": f.qualified_name,
                    "scope_name": f.scope_name,
                    "type_signature": f.type_signature,
                    "file_path": f.file_path,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "code": f.code,
                }
                for f in fields
            ]
            write_batches(
                "fields",
                field_rows,
                """
                UNWIND $rows AS row
                MERGE (f:Field {id: row.id})
                SET f.name = row.name,
                    f.qualified_name = row.qualified_name,
                    f.scope_name = row.scope_name,
                    f.type_signature = row.type_signature,
                    f.file_path = row.file_path,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.code = row.code
                """,
            )

            alias_rows = [
                {
                    "id": a.symbol_id,
                    "name": a.name,
                    "qualified_name": a.qualified_name,
                    "kind": a.kind,
                    "target_name": a.target_name,
                    "file_path": a.file_path,
                    "start_line": a.start_line,
                    "end_line": a.end_line,
                    "code": a.code,
                }
                for a in aliases
            ]
            write_batches(
                "aliases",
                alias_rows,
                """
                UNWIND $rows AS row
                MERGE (a:Alias {id: row.id})
                SET a.name = row.name,
                    a.qualified_name = row.qualified_name,
                    a.kind = row.kind,
                    a.target_name = row.target_name,
                    a.file_path = row.file_path,
                    a.start_line = row.start_line,
                    a.end_line = row.end_line,
                    a.code = row.code
                """,
            )

            template_rows = [
                {
                    "id": t.symbol_id,
                    "name": t.name,
                    "file_path": t.file_path,
                    "start_line": t.start_line,
                    "end_line": t.end_line,
                    "code": t.code,
                }
                for t in templates
            ]
            write_batches(
                "templates",
                template_rows,
                """
                UNWIND $rows AS row
                MERGE (t:Template {id: row.id})
                SET t.name = row.name,
                    t.file_path = row.file_path,
                    t.start_line = row.start_line,
                    t.end_line = row.end_line,
                    t.code = row.code
                """,
            )

            relation_groups: Dict[Tuple[str, str, str], List[RelationEdge]] = {}
            for rel in relations:
                relation_groups.setdefault(
                    (rel.source_label, rel.target_label, rel.rel_type), []
                ).append(rel)
            for (source_label, target_label, rel_type), group in relation_groups.items():
                rel_rows = [
                    {
                        "source_id": rel.source_id,
                        "target_id": rel.target_id,
                        "props": rel.properties,
                    }
                    for rel in group
                ]
                label_key = f"relations:{source_label}:{target_label}:{rel_type}"
                query = (
                    f"UNWIND $rows AS row "
                    f"MATCH (a:{source_label} {{id: row.source_id}}), "
                    f"(b:{target_label} {{id: row.target_id}}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) "
                    "SET r += row.props"
                )
                write_batches(label_key, rel_rows, query)

            call_rows = [
                {
                    "caller_id": call.caller_id,
                    "callee_id": call.callee_id,
                    "site_id": _call_site_id(
                        call.caller_id,
                        call.callee_id,
                        call.caller_file,
                        call.call_line,
                        call.call_column,
                        call.call_type,
                    ),
                    "props": {
                        "file_path": call.caller_file,
                        "line": call.call_line,
                        "column": call.call_column,
                        "call_type": call.call_type,
                        "call_arity": call.call_arity,
                        "callee_name": call.callee_name,
                        "caller_scope": call.caller_scope or "",
                    },
                }
                for call in calls
                if call.callee_id
            ]
            write_batches(
                "calls",
                call_rows,
                """
                UNWIND $rows AS row
                MATCH (caller:Function {id: row.caller_id}), (callee:Function {id: row.callee_id})
                MERGE (caller)-[r:CALLS {site_id: row.site_id}]->(callee)
                SET r += row.props
                """,
            )

    @staticmethod
    def _merge_file(tx, file_def: FileDef) -> None:
        tx.run(
            """
            MERGE (f:File {id: $id})
            SET f.path = $path,
                f.start_line = $start_line,
                f.end_line = $end_line,
                f.code = $code,
                f.comment = $comment,
                f.summary = $summary,
                f.note = $note
            """,
            id=file_def.file_path,
            path=file_def.file_path,
            start_line=file_def.start_line,
            end_line=file_def.end_line,
            code=file_def.code,
            comment=file_def.comment,
            summary=file_def.summary,
            note=file_def.note,
        )

    @staticmethod
    def _merge_namespace(tx, ns_def: NamespaceDef) -> None:
        tx.run(
            """
            MERGE (n:Namespace {id: $id})
            SET n.name = $name,
                n.qualified_name = $qualified_name,
                n.file_path = $file_path,
                n.start_line = $start_line,
                n.end_line = $end_line,
                n.code = $code,
                n.comment = $comment,
                n.summary = $summary,
                n.note = $note
            """,
            id=ns_def.symbol_id,
            name=ns_def.name,
            qualified_name=ns_def.qualified_name,
            file_path=ns_def.file_path,
            start_line=ns_def.start_line,
            end_line=ns_def.end_line,
            code=ns_def.code,
            comment=ns_def.comment,
            summary=ns_def.summary,
            note=ns_def.note,
        )

    @staticmethod
    def _merge_type(tx, type_def: TypeDef) -> None:
        tx.run(
            """
            MERGE (t:Type {id: $id})
            SET t.name = $name,
                t.qualified_name = $qualified_name,
                t.kind = $kind,
                t.file_path = $file_path,
                t.start_line = $start_line,
                t.end_line = $end_line,
                t.code = $code,
                t.comment = $comment,
                t.summary = $summary,
                t.note = $note
            FOREACH (_ IN CASE WHEN $kind IN ['class','struct','anonymous_class','anonymous_struct'] THEN [1] ELSE [] END |
                SET t:Class
            )
            """,
            id=type_def.symbol_id,
            name=type_def.name,
            qualified_name=type_def.qualified_name,
            kind=type_def.kind,
            file_path=type_def.file_path,
            start_line=type_def.start_line,
            end_line=type_def.end_line,
            code=type_def.code,
            comment=type_def.comment,
            summary=type_def.summary,
            note=type_def.note,
        )

    @staticmethod
    def _merge_function_type(tx, func_type: FunctionTypeDef) -> None:
        tx.run(
            """
            MERGE (t:FunctionType {id: $id})
            SET t.type_signature = $type_signature,
                t.file_path = $file_path,
                t.start_line = $start_line,
                t.end_line = $end_line,
                t.code = $code
            """,
            id=func_type.symbol_id,
            type_signature=func_type.type_signature,
            file_path=func_type.file_path,
            start_line=func_type.start_line,
            end_line=func_type.end_line,
            code=func_type.code,
        )

    @staticmethod
    def _merge_relation(tx, relation: RelationEdge) -> None:
        if relation.rel_type not in {
            "CONTAINS",
            "DECLARES",
            "EXTENDS",
            "TAKES_FUNCTION",
            "USES_TYPE",
            "POINTER_TO",
            "ALIASES",
            "TEMPLATES",
            "EMITS_EVENT",
            "HANDLES_EVENT",
            "CALLS_FUNCTION_POINTER",
            "POSSIBLE_CALLS",
        }:
            raise ValueError(f"Unsupported relation type: {relation.rel_type}")
        query = (
            f"MATCH (a:{relation.source_label} {{id: $source_id}}),"
            f" (b:{relation.target_label} {{id: $target_id}}) "
            f"MERGE (a)-[r:{relation.rel_type}]->(b) "
            "SET r += $props"
        )
        tx.run(
            query,
            source_id=relation.source_id,
            target_id=relation.target_id,
            props=relation.properties,
        )

    @staticmethod
    def _merge_function(tx, func: FunctionDef) -> None:
        tx.run(
            """
            MERGE (f:Function {id: $id})
            SET f.name = $name,
                f.qualified_name = $qualified_name,
                f.kind = $kind,
                f.scope_name = $scope_name,
                f.file_path = $file_path,
                f.start_line = $start_line,
                f.end_line = $end_line,
                f.arity = $arity,
                f.code = $code,
                f.comment = $comment,
                f.summary = $summary,
                f.note = $note
            """,
            id=func.symbol_id,
            name=func.name,
            qualified_name=func.qualified_name,
            kind=func.kind,
            scope_name=func.scope_name,
            file_path=func.file_path,
            start_line=func.start_line,
            end_line=func.end_line,
            arity=func.arity,
            code=func.code,
            comment=func.comment,
            summary=func.summary,
            note=func.note,
        )

    @staticmethod
    def _merge_field(tx, field: FieldDef) -> None:
        tx.run(
            """
            MERGE (f:Field {id: $id})
            SET f.name = $name,
                f.qualified_name = $qualified_name,
                f.scope_name = $scope_name,
                f.type_signature = $type_signature,
                f.file_path = $file_path,
                f.start_line = $start_line,
                f.end_line = $end_line,
                f.code = $code
            """,
            id=field.symbol_id,
            name=field.name,
            qualified_name=field.qualified_name,
            scope_name=field.scope_name,
            type_signature=field.type_signature,
            file_path=field.file_path,
            start_line=field.start_line,
            end_line=field.end_line,
            code=field.code,
        )

    @staticmethod
    def _merge_alias(tx, alias: AliasDef) -> None:
        tx.run(
            """
            MERGE (a:Alias {id: $id})
            SET a.name = $name,
                a.qualified_name = $qualified_name,
                a.kind = $kind,
                a.target_name = $target_name,
                a.file_path = $file_path,
                a.start_line = $start_line,
                a.end_line = $end_line,
                a.code = $code
            """,
            id=alias.symbol_id,
            name=alias.name,
            qualified_name=alias.qualified_name,
            kind=alias.kind,
            target_name=alias.target_name,
            file_path=alias.file_path,
            start_line=alias.start_line,
            end_line=alias.end_line,
            code=alias.code,
        )

    @staticmethod
    def _merge_template(tx, template: TemplateDef) -> None:
        tx.run(
            """
            MERGE (t:Template {id: $id})
            SET t.name = $name,
                t.file_path = $file_path,
                t.start_line = $start_line,
                t.end_line = $end_line,
                t.code = $code
            """,
            id=template.symbol_id,
            name=template.name,
            file_path=template.file_path,
            start_line=template.start_line,
            end_line=template.end_line,
            code=template.code,
        )

    @staticmethod
    def _merge_call(tx, call: CallEdge) -> None:
        tx.run(
            """
            MATCH (caller:Function {id: $caller_id}), (callee:Function {id: $callee_id})
            MERGE (caller)-[r:CALLS {site_id: $site_id}]->(callee)
            SET r += $props
            """,
            caller_id=call.caller_id,
            callee_id=call.callee_id,
            site_id=_call_site_id(
                call.caller_id,
                call.callee_id,
                call.caller_file,
                call.call_line,
                call.call_column,
                call.call_type,
            ),
            props={
                "file_path": call.caller_file,
                "line": call.call_line,
                "column": call.call_column,
                "call_type": call.call_type,
                "callee_name": call.callee_name,
                "caller_scope": call.caller_scope or "",
            },
        )

    def write_cloc_stats(
        self,
        project_id: str,
        project_name: str,
        root: str,
        repo: str,
        language: str,
        stats: Dict[str, Any],
    ) -> None:
        payload = {
            "id": project_id,
            "project_id": project_id,
            "project_name": project_name,
            "root": root,
            "repo": repo,
            "language": language,
            "total_files": stats.get("total_files"),
            "total_blank": stats.get("total_blank"),
            "total_comment": stats.get("total_comment"),
            "total_code": stats.get("total_code"),
            "cloc_version": stats.get("cloc_version"),
            "elapsed_seconds": stats.get("elapsed_seconds"),
            "languages_json": json.dumps(stats.get("languages", {}), ensure_ascii=True),
            "generated_at": stats.get("generated_at"),
        }
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                MERGE (s:CodebaseStats {id: $id})
                SET s.project_id = $project_id,
                    s.project_name = $project_name,
                    s.root = $root,
                    s.repo = $repo,
                    s.language = $language,
                    s.total_files = $total_files,
                    s.total_blank = $total_blank,
                    s.total_comment = $total_comment,
                    s.total_code = $total_code,
                    s.cloc_version = $cloc_version,
                    s.elapsed_seconds = $elapsed_seconds,
                    s.languages_json = $languages_json,
                    s.generated_at = $generated_at
                """,
                **payload,
            )


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


def _scan_c_family_files(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith((".c", ".h", ".hpp", ".cpp", ".cc", ".cxx", ".hh", ".hxx")):
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def _is_cpp_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in {".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"}


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
        namespaces = payload.get("namespaces")
        if isinstance(namespaces, list):
            for item in namespaces:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        types = payload.get("types")
        if isinstance(types, list):
            for item in types:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        functions = payload.get("functions")
        if isinstance(functions, list):
            for item in functions:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        fields = payload.get("fields")
        if isinstance(fields, list):
            for item in fields:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        aliases = payload.get("aliases")
        if isinstance(aliases, list):
            for item in aliases:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        using_namespaces = payload.get("using_namespaces")
        if using_namespaces is None:
            payload["using_namespaces"] = []
        using_imports = payload.get("using_imports")
        if using_imports is None:
            payload["using_imports"] = {}
        includes = payload.get("includes")
        if includes is None:
            payload["includes"] = []
        macros = payload.get("macros")
        if macros is None:
            payload["macros"] = {}
        templates = payload.get("templates")
        if isinstance(templates, list):
            for item in templates:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        calls = payload.get("calls")
        if isinstance(calls, list):
            for item in calls:
                if not isinstance(item, dict):
                    continue
                item.setdefault("caller_file", file_def.get("file_path") if isinstance(file_def, dict) else "")
                item.setdefault("caller_scope", None)
                item.setdefault("call_line", 0)
                item.setdefault("call_column", 0)
                item.setdefault("call_type", "call_expression")
                item.setdefault("call_arity", 0)
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
        file_types,
        file_namespaces,
        file_relations,
        file_func_types,
        file_fields,
        file_aliases,
        file_templates,
        file_def,
        file_using_namespaces,
        file_using_imports,
        file_includes,
        file_macros,
    ) = parse_c_family_file(file_path, root, _is_cpp_file(file_path))
    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "types": [asdict(item) for item in file_types],
        "namespaces": [asdict(item) for item in file_namespaces],
        "relations": [asdict(item) for item in file_relations],
        "function_types": [asdict(item) for item in file_func_types],
        "fields": [asdict(item) for item in file_fields],
        "aliases": [asdict(item) for item in file_aliases],
        "templates": [asdict(item) for item in file_templates],
        "file_def": asdict(file_def),
        "using_namespaces": file_using_namespaces,
        "using_imports": file_using_imports,
        "includes": file_includes,
        "macros": file_macros,
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
    call_stats_path: Optional[str],
    possible_calls_path: Optional[str],
    unresolved_calls_path: Optional[str],
    verbose: bool,
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "cplus_analyzer")
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    all_file_paths = _scan_c_family_files(root)
    if verbose:
        print(f"[scan] Found {len(all_file_paths)} C/C++ files under {root}")
    total_files = len(all_file_paths)

    def iter_payloads(log_parse: bool) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(all_file_paths, start=1):
            if log_parse and verbose and (index == 1 or index % 50 == 0 or index == total_files):
                print(f"[parse] {index}/{total_files}: {file_path}")
            yield _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)

    function_index_by_name: Dict[str, List[Dict[str, Any]]] = {}
    function_index_by_name_arity: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    function_index_by_scope_name: Dict[Tuple[Optional[str], str], List[Dict[str, Any]]] = {}
    function_index_by_scope_name_arity: Dict[Tuple[Optional[str], str, int], List[Dict[str, Any]]] = {}
    function_index_by_qualified: Dict[str, Dict[str, Any]] = {}
    function_index_by_qualified_arity: Dict[Tuple[str, int], Dict[str, Any]] = {}
    function_index_by_file_name: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    function_index_by_file_name_arity: Dict[Tuple[str, str, int], List[Dict[str, Any]]] = {}
    using_namespaces_by_file: Dict[str, List[str]] = {}
    using_imports_by_file: Dict[str, Dict[str, str]] = {}
    includes_by_file: Dict[str, List[str]] = {}
    macros_by_file: Dict[str, Dict[str, str]] = {}
    alias_targets_by_name: Dict[str, str] = {}
    event_nodes: List[Dict[str, Any]] = []
    event_relations: List[Dict[str, Any]] = []
    possible_call_relations: List[Dict[str, Any]] = []
    class_methods: Dict[str, List[Dict[str, Any]]] = {}
    base_relations: List[Tuple[str, str]] = []
    expected_points = 0
    all_files_set = set(all_file_paths)
    file_lookup_by_basename: Dict[str, List[str]] = {}
    for path in all_file_paths:
        file_lookup_by_basename.setdefault(os.path.basename(path), []).append(path)

    for payload in iter_payloads(log_parse=True):
        file_def = payload.get("file_def") or {}
        file_path = file_def.get("file_path")
        if file_path:
            using_namespaces_by_file[file_path] = list(payload.get("using_namespaces") or [])
            using_imports_by_file[file_path] = dict(payload.get("using_imports") or {})
            includes_by_file[file_path] = list(payload.get("includes") or [])
            macros_by_file[file_path] = dict(payload.get("macros") or {})
        for alias in payload.get("aliases", []):
            name = alias.get("name")
            target = alias.get("target_name")
            if name and target:
                alias_targets_by_name[name] = target
        for func in payload["functions"]:
            expected_points += 1
            entry = {
                "symbol_id": func["symbol_id"],
                "qualified_name": func["qualified_name"],
                "name": func["name"],
                "scope_name": func.get("scope_name"),
                "arity": func.get("arity", 0),
                "file_path": func.get("file_path"),
            }
            function_index_by_name.setdefault(func["name"], []).append(entry)
            function_index_by_name_arity.setdefault((func["name"], func.get("arity", 0)), []).append(entry)
            function_index_by_scope_name.setdefault((func.get("scope_name"), func["name"]), []).append(entry)
            function_index_by_scope_name_arity.setdefault(
                (func.get("scope_name"), func["name"], func.get("arity", 0)),
                [],
            ).append(entry)
            function_index_by_qualified[func["qualified_name"]] = entry
            function_index_by_qualified_arity[(func["qualified_name"], func.get("arity", 0))] = entry
            if file_path:
                function_index_by_file_name.setdefault((file_path, func["name"]), []).append(entry)
                function_index_by_file_name_arity.setdefault(
                    (file_path, func["name"], func.get("arity", 0)),
                    [],
                ).append(entry)
            if func.get("scope_name"):
                class_methods.setdefault(func.get("scope_name"), []).append(entry)

        for rel in payload.get("relations", []):
            if (
                rel.get("rel_type") == "EXTENDS"
                and rel.get("source_label") == "Type"
                and rel.get("target_label") == "Type"
            ):
                base_relations.append((rel.get("source_id"), rel.get("target_id")))

    def resolve_include_path(source_file: str, include_name: str) -> Optional[str]:
        if not include_name:
            return None
        if "/" in include_name or "\\" in include_name:
            candidate = os.path.normpath(os.path.join(os.path.dirname(source_file), include_name))
            if candidate in all_files_set:
                return candidate
            candidate = os.path.normpath(os.path.join(root, include_name))
            if candidate in all_files_set:
                return candidate
        candidates = file_lookup_by_basename.get(os.path.basename(include_name), [])
        return candidates[0] if candidates else None

    resolved_includes_by_file: Dict[str, List[str]] = {}
    for file_path, includes in includes_by_file.items():
        resolved: List[str] = []
        for inc in includes:
            resolved_path = resolve_include_path(os.path.join(root, file_path), inc)
            if resolved_path:
                rel_inc = os.path.relpath(resolved_path, root)
                resolved.append(rel_inc)
        resolved_includes_by_file[file_path] = resolved

    def collect_transitive(
        start_file: str,
        visited: Optional[set[str]] = None,
    ) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
        if visited is None:
            visited = set()
        if start_file in visited:
            return [], {}, {}
        visited.add(start_file)
        namespaces = list(using_namespaces_by_file.get(start_file, []))
        imports = dict(using_imports_by_file.get(start_file, {}))
        macros = dict(macros_by_file.get(start_file, {}))
        for inc in resolved_includes_by_file.get(start_file, []):
            ns, im, ma = collect_transitive(inc, visited)
            for item in ns:
                if item not in namespaces:
                    namespaces.append(item)
            for key, value in im.items():
                imports.setdefault(key, value)
            for key, value in ma.items():
                macros.setdefault(key, value)
        return namespaces, imports, macros

    for file_path in list(using_namespaces_by_file.keys()):
        ns, im, ma = collect_transitive(file_path)
        using_namespaces_by_file[file_path] = ns
        using_imports_by_file[file_path] = im
        macros_by_file[file_path] = ma

    base_to_derived: Dict[str, List[str]] = {}
    for derived, base in base_relations:
        if not derived or not base:
            continue
        base_to_derived.setdefault(base, []).append(derived)

    for base, derived_list in base_to_derived.items():
        base_methods = class_methods.get(base, [])
        if not base_methods:
            continue
        for derived in derived_list:
            derived_methods = class_methods.get(derived, [])
            if not derived_methods:
                continue
            derived_index: Dict[Tuple[str, int], Dict[str, Any]] = {
                (item["name"], item.get("arity", 0)): item for item in derived_methods
            }
            for base_method in base_methods:
                key = (base_method["name"], base_method.get("arity", 0))
                derived_method = derived_index.get(key)
                if not derived_method:
                    continue
                possible_call_relations.append(
                    {
                        "rel_type": "POSSIBLE_CALLS",
                        "source_id": base_method["symbol_id"],
                        "target_id": derived_method["symbol_id"],
                        "props": {"base_type": base, "derived_type": derived},
                    }
                )

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
                        func_id = candidates[0]
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
                        func_id = candidates[0]
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

    if code_writer:
        if verbose:
            print("[neo4j] Collecting nodes and relations for batch write...")
        all_files: List[Dict[str, Any]] = []
        all_namespaces: List[Dict[str, Any]] = []
        all_types: List[Dict[str, Any]] = []
        all_function_types: List[Dict[str, Any]] = []
        all_functions: List[Dict[str, Any]] = []
        all_fields: List[Dict[str, Any]] = []
        all_aliases: List[Dict[str, Any]] = []
        all_templates: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []

        allowed_rel_types = {
            "CONTAINS",
            "DECLARES",
            "EXTENDS",
            "TAKES_FUNCTION",
            "USES_TYPE",
            "POINTER_TO",
            "ALIASES",
            "TEMPLATES",
            "EMITS_EVENT",
            "HANDLES_EVENT",
            "CALLS_FUNCTION_POINTER",
            "POSSIBLE_CALLS",
        }

        def resolve_callee_id(
            callee_name: str,
            caller_scope: Optional[str],
            call_arity: int,
            caller_file: Optional[str],
        ) -> Optional[str]:
            def expand_macros(name: str, file_path: Optional[str]) -> List[str]:
                if not file_path:
                    return [name]
                macros = macros_by_file.get(file_path, {})
                expansion = macros.get(name)
                if not expansion:
                    return [name]
                expanded = _normalize_call_name(expansion)
                return [name, expanded] if expanded and expanded != name else [name]

            def scope_chain(scope: Optional[str]) -> List[Optional[str]]:
                if not scope:
                    return [None]
                parts = scope.split("::")
                chain = ["::".join(parts[:idx]) for idx in range(len(parts), 0, -1)]
                chain.append(None)
                return chain

            def expand_aliases(name: str) -> List[str]:
                if "::" not in name:
                    return [name]
                prefix, rest = name.split("::", 1)
                target = alias_targets_by_name.get(prefix)
                if target:
                    return [name, f"{target}::{rest}"]
                return [name]

            candidates: List[Dict[str, Any]] = []
            expanded = []
            for variant in expand_aliases(callee_name):
                for macro_variant in expand_macros(variant, caller_file):
                    expanded.append(macro_variant)
            for variant in expanded:
                if "::" in variant:
                    entry = function_index_by_qualified_arity.get((variant, call_arity))
                    if entry:
                        return entry["symbol_id"]
                    entry = function_index_by_qualified.get(variant)
                    if entry:
                        return entry["symbol_id"]

            short_name = callee_name.split("::")[-1]
            if caller_file:
                scoped = function_index_by_file_name_arity.get((caller_file, short_name, call_arity))
                if scoped:
                    return scoped[0]["symbol_id"]
                scoped = function_index_by_file_name.get((caller_file, short_name))
                if scoped:
                    return scoped[0]["symbol_id"]
                for ns in using_namespaces_by_file.get(caller_file, []):
                    qualified = f"{ns}::{short_name}"
                    entry = function_index_by_qualified_arity.get((qualified, call_arity))
                    if entry:
                        return entry["symbol_id"]
                    entry = function_index_by_qualified.get(qualified)
                    if entry:
                        return entry["symbol_id"]
                imported = using_imports_by_file.get(caller_file, {}).get(short_name)
                if imported:
                    entry = function_index_by_qualified_arity.get((imported, call_arity))
                    if entry:
                        return entry["symbol_id"]
                    entry = function_index_by_qualified.get(imported)
                    if entry:
                        return entry["symbol_id"]
            for scope in scope_chain(caller_scope):
                scoped = function_index_by_scope_name_arity.get((scope, short_name, call_arity))
                if scoped:
                    return scoped[0]["symbol_id"]
                scoped = function_index_by_scope_name.get((scope, short_name))
                if scoped:
                    return scoped[0]["symbol_id"]

            candidates = function_index_by_name_arity.get((short_name, call_arity), [])
            if candidates:
                return candidates[0]["symbol_id"]

            candidates = function_index_by_name.get(short_name, [])
            if candidates:
                return candidates[0]["symbol_id"]
            return None

        call_stats_total = 0
        call_stats_resolved = 0
        call_stats_by_file: Dict[str, Tuple[int, int]] = {}
        call_stats_macro_resolved = 0
        unresolved_handle = None
        if unresolved_calls_path:
            os.makedirs(os.path.dirname(os.path.abspath(unresolved_calls_path)), exist_ok=True)
            unresolved_handle = open(unresolved_calls_path, "w", encoding="utf-8")

        for event_def in event_nodes:
            # Events will be added through relations
            pass

        for payload in iter_payloads(log_parse=False):
            file_def = payload["file_def"]
            all_files.append({
                "id": file_def["file_path"],
                "path": file_def["file_path"],
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
            })
            for ns in payload["namespaces"]:
                all_namespaces.append({
                    "id": ns["symbol_id"],
                    "name": ns["name"],
                    "qualified_name": ns["qualified_name"],
                    "file_path": ns["file_path"],
                    "start_line": ns["start_line"],
                    "end_line": ns["end_line"],
                    "code": ns["code"],
                    "comment": ns["comment"],
                    "summary": ns["summary"],
                    "note": ns["note"],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })
            for type_def in payload["types"]:
                all_types.append({
                    "id": type_def["symbol_id"],
                    "name": type_def["name"],
                    "qualified_name": type_def["qualified_name"],
                    "kind": type_def["kind"],
                    "file_path": type_def["file_path"],
                    "start_line": type_def["start_line"],
                    "end_line": type_def["end_line"],
                    "code": type_def["code"],
                    "comment": type_def["comment"],
                    "summary": type_def["summary"],
                    "note": type_def["note"],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })
            for func_type in payload["function_types"]:
                all_function_types.append({
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
                })
            for func in payload["functions"]:
                all_functions.append({
                    "id": func["symbol_id"],
                    "name": func["name"],
                    "qualified_name": func["qualified_name"],
                    "kind": func["kind"],
                    "scope_name": func["scope_name"],
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
                })
            for field in payload["fields"]:
                all_fields.append({
                    "id": field["symbol_id"],
                    "name": field["name"],
                    "qualified_name": field["qualified_name"],
                    "scope_name": field["scope_name"],
                    "type_signature": field["type_signature"],
                    "file_path": field["file_path"],
                    "start_line": field["start_line"],
                    "end_line": field["end_line"],
                    "code": field["code"],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })
            for alias in payload["aliases"]:
                all_aliases.append({
                    "id": alias["symbol_id"],
                    "name": alias["name"],
                    "qualified_name": alias["qualified_name"],
                    "kind": alias["kind"],
                    "target_name": alias["target_name"],
                    "file_path": alias["file_path"],
                    "start_line": alias["start_line"],
                    "end_line": alias["end_line"],
                    "code": alias["code"],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })
            for template in payload["templates"]:
                all_templates.append({
                    "id": template["symbol_id"],
                    "name": template["name"],
                    "file_path": template["file_path"],
                    "start_line": template["start_line"],
                    "end_line": template["end_line"],
                    "code": template["code"],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })

            # Add relations for project containment
            file_id = file_def["file_path"]
            all_relations.append({
                "source_label": "Project",
                "target_label": "File",
                "rel_type": "CONTAINS",
                "source_id": project_id,
                "target_id": file_id,
                "properties": {},
            })
            for ns in payload["namespaces"]:
                all_relations.append({
                    "source_label": "File",
                    "target_label": "Namespace",
                    "rel_type": "CONTAINS",
                    "source_id": file_id,
                    "target_id": ns["symbol_id"],
                    "properties": {},
                })
            for type_def in payload["types"]:
                all_relations.append({
                    "source_label": "File",
                    "target_label": "Type",
                    "rel_type": "CONTAINS",
                    "source_id": file_id,
                    "target_id": type_def["symbol_id"],
                    "properties": {},
                })
            for func in payload["functions"]:
                all_relations.append({
                    "source_label": "File",
                    "target_label": "Function",
                    "rel_type": "CONTAINS",
                    "source_id": file_id,
                    "target_id": func["symbol_id"],
                    "properties": {},
                })
            for field in payload["fields"]:
                all_relations.append({
                    "source_label": "File",
                    "target_label": "Field",
                    "rel_type": "CONTAINS",
                    "source_id": file_id,
                    "target_id": field["symbol_id"],
                    "properties": {},
                })
            for alias in payload["aliases"]:
                all_relations.append({
                    "source_label": "File",
                    "target_label": "Alias",
                    "rel_type": "CONTAINS",
                    "source_id": file_id,
                    "target_id": alias["symbol_id"],
                    "properties": {},
                })
            for template in payload["templates"]:
                all_relations.append({
                    "source_label": "File",
                    "target_label": "Template",
                    "rel_type": "CONTAINS",
                    "source_id": file_id,
                    "target_id": template["symbol_id"],
                    "properties": {},
                })

            for rel in payload["relations"]:
                if rel["rel_type"] not in allowed_rel_types:
                    continue
                all_relations.append({
                    "source_label": rel["source_label"],
                    "target_label": rel["target_label"],
                    "rel_type": rel["rel_type"],
                    "source_id": rel["source_id"],
                    "target_id": rel["target_id"],
                    "properties": rel["properties"],
                })

            for call in payload["calls"]:
                call_stats_total += 1
                macro_hit = False
                call_file = call.get("caller_file") or file_id
                if call.get("callee_name") in macros_by_file.get(call_file, {}):
                    macro_hit = True
                callee_id = call.get("callee_id") or resolve_callee_id(
                    call["callee_name"],
                    call.get("caller_scope"),
                    int(call.get("call_arity") or 0),
                    call_file,
                )
                if not callee_id:
                    total, resolved = call_stats_by_file.get(call_file, (0, 0))
                    call_stats_by_file[call_file] = (total + 1, resolved)
                    if unresolved_handle is not None:
                        macro_expansion = macros_by_file.get(call_file, {}).get(call.get("callee_name") or "")
                        unresolved_handle.write(
                            json.dumps(
                                {
                                    "caller_id": call.get("caller_id"),
                                    "caller_scope": call.get("caller_scope"),
                                    "file_path": call_file,
                                    "line": int(call.get("call_line") or 0),
                                    "column": int(call.get("call_column") or 0),
                                    "call_type": call.get("call_type") or "call_expression",
                                    "call_arity": int(call.get("call_arity") or 0),
                                    "callee_name": call.get("callee_name") or "",
                                    "macro_expansion": macro_expansion or "",
                                },
                                ensure_ascii=True,
                            )
                            + "\n"
                        )
                    continue
                call_stats_resolved += 1
                if macro_hit:
                    call_stats_macro_resolved += 1
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
                total, resolved = call_stats_by_file.get(call_file, (0, 0))
                call_stats_by_file[call_file] = (total + 1, resolved + 1)
                all_calls.append({
                    "caller_id": call["caller_id"],
                    "callee_id": callee_id,
                    "site_id": site_id,
                    "props": {
                        "file_path": call_file,
                        "line": call_line,
                        "column": call_column,
                        "call_type": call_type,
                        "call_arity": int(call.get("call_arity") or 0),
                        "callee_name": call.get("callee_name") or "",
                        "caller_scope": call.get("caller_scope") or "",
                    },
                })

        # Add event relations
        for event_rel in event_relations:
            all_relations.append({
                "source_label": "Function",
                "target_label": "Event",
                "rel_type": event_rel["rel_type"],
                "source_id": event_rel["source_id"],
                "target_id": event_rel["target_id"],
                "properties": event_rel["props"],
            })
        for rel in possible_call_relations:
            all_relations.append({
                "source_label": "Function",
                "target_label": "Function",
                "rel_type": rel["rel_type"],
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "properties": rel["props"],
            })

        if unresolved_handle is not None:
            unresolved_handle.close()

        if verbose:
            print(f"[neo4j] Writing {len(all_files)} files, {len(all_namespaces)} namespaces, "
                  f"{len(all_types)} types, {len(all_function_types)} function_types, "
                  f"{len(all_functions)} functions, {len(all_fields)} fields, "
                  f"{len(all_aliases)} aliases, {len(all_templates)} templates, "
                  f"{len(all_relations)} relations, {len(all_calls)} calls")
        
        await code_writer.write_all(
            files=all_files,
            namespaces=all_namespaces,
            types=all_types,
            function_types=all_function_types,
            functions=all_functions,
            fields=all_fields,
            aliases=all_aliases,
            templates=all_templates,
            relations=all_relations,
            calls_with_site=all_calls,
        )

        if verbose:
            unresolved = call_stats_total - call_stats_resolved
            ratio = (call_stats_resolved / call_stats_total) if call_stats_total else 0.0
            print(
                "[calls] resolved %d / %d (%.1f%%), unresolved %d"
                % (call_stats_resolved, call_stats_total, ratio * 100, unresolved)
            )
            if call_stats_macro_resolved:
                print("[calls] macro-resolved %d" % call_stats_macro_resolved)
            if call_stats_by_file:
                worst = sorted(
                    call_stats_by_file.items(),
                    key=lambda item: (item[1][0] - item[1][1], item[1][0]),
                    reverse=True,
                )[:10]
                print("[calls] top unresolved files:")
                for file_path, (total, resolved) in worst:
                    print("  - %s: %d unresolved / %d total" % (file_path, total - resolved, total))
        if possible_calls_path:
            os.makedirs(os.path.dirname(os.path.abspath(possible_calls_path)), exist_ok=True)
            with open(possible_calls_path, "w", encoding="utf-8") as handle:
                json.dump(possible_call_relations, handle, ensure_ascii=True, indent=2)
        if call_stats_path:
            stats_payload = {
                "total_calls": call_stats_total,
                "resolved_calls": call_stats_resolved,
                "unresolved_calls": call_stats_total - call_stats_resolved,
                "resolved_ratio": (call_stats_resolved / call_stats_total) if call_stats_total else 0.0,
                "macro_resolved_calls": call_stats_macro_resolved,
                "possible_calls_written": len(possible_call_relations),
                "by_file": [
                    {
                        "file_path": file_path,
                        "total": total,
                        "resolved": resolved,
                        "unresolved": total - resolved,
                    }
                    for file_path, (total, resolved) in call_stats_by_file.items()
                ],
            }
            os.makedirs(os.path.dirname(os.path.abspath(call_stats_path)), exist_ok=True)
            with open(call_stats_path, "w", encoding="utf-8") as handle:
                json.dump(stats_payload, handle, ensure_ascii=True, indent=2)
        if verbose:
            print("[neo4j] Write complete")

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
                                    "scope_name": func_item["scope_name"],
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
                                "scope_name": func_item["scope_name"],
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
    parser = argparse.ArgumentParser(description="C/C++ call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing C/C++ sources")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-pass", default=os.environ.get("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "cplus_functions"))
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("EMBED_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=512)
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
    parser.add_argument("--event-map", help="JSON mapping file for cross-project events/IDL")
    parser.add_argument("--call-stats-path", help="Write call resolution stats JSON")
    parser.add_argument("--possible-calls-path", help="Write POSSIBLE_CALLS edges JSON")
    parser.add_argument("--unresolved-calls-path", help="Write unresolved calls as JSONL")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    driver = None
    code_writer = None
    if args.neo4j_uri and args.neo4j_user and args.neo4j_password:
        driver = await GraphDriverFactory.create_driver(
            GraphProvider.NEO4J,
            {"uri": args.neo4j_uri, "user": args.neo4j_user, "password": args.neo4j_password, "database": args.neo4j_db}
        )
        code_writer = LanguageCodeWriter(driver, database=args.neo4j_db, batch_size=args.neo4j_batch_size, verbose=args.verbose)

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
        cache_root = safe_cache_root(args.cache_dir, "cplus_analyzer")
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "cplus"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""
    if driver:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            payload = {
                "id": project_id,
                "project_id": project_id,
                "project_name": project_name,
                "root": args.root,
                "repo": repo,
                "language": language,
                "total_files": cloc_stats.get("total_files"),
                "total_blank": cloc_stats.get("total_blank"),
                "total_comment": cloc_stats.get("total_comment"),
                "total_code": cloc_stats.get("total_code"),
                "cloc_version": cloc_stats.get("cloc_version"),
                "elapsed_seconds": cloc_stats.get("elapsed_seconds"),
                "languages_json": json.dumps(cloc_stats.get("languages", {}), ensure_ascii=True),
                "generated_at": cloc_stats.get("generated_at"),
            }
            query = """
                MERGE (s:CodebaseStats {id: $id})
                SET s.project_id = $project_id,
                    s.project_name = $project_name,
                    s.root = $root,
                    s.repo = $repo,
                    s.language = $language,
                    s.total_files = $total_files,
                    s.total_blank = $total_blank,
                    s.total_comment = $total_comment,
                    s.total_code = $total_code,
                    s.cloc_version = $cloc_version,
                    s.elapsed_seconds = $elapsed_seconds,
                    s.languages_json = $languages_json,
                    s.generated_at = $generated_at
            """
            await driver.execute_query(query, payload, database=args.neo4j_db)
            if args.verbose:
                print("[cloc] Stats stored in Neo4j")
        elif args.verbose:
            print("[cloc] Skipped (cloc not available or failed)")

    try:
        if args.dry_run:
            files = _scan_c_family_files(args.root)
            print(f"Dry run: {len(files)} C/C++ files found")
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
            call_stats_path=args.call_stats_path,
            possible_calls_path=args.possible_calls_path,
            unresolved_calls_path=args.unresolved_calls_path,
            verbose=args.verbose,
        )
    finally:
        if driver:
            await driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

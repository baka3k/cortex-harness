from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests
import torch
from transformers import AutoModel, AutoTokenizer

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
    exported: bool = False


@dataclass
class FileDef:
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)


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
class ClassDef:
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
    exported: bool = False


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
    caller_scope: Optional[str]
    callee_name: str
    callee_id: Optional[str]
    callee_arity: Optional[int]


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
    if node.type in {"identifier", "attribute"}:
        return _node_text(node, source_bytes)
    for child in node.children:
        result = _first_identifier(child, source_bytes)
        if result:
            return result
    return None


def _extract_name_field(node, source_bytes: bytes) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes)
    return _first_identifier(node, source_bytes)


def _normalize_call_name(text: str) -> str:
    cleaned = re.sub(r"<[^<>]*>", "", text)
    cleaned = cleaned.replace("?.", ".")
    cleaned = cleaned.replace("::", ".")
    cleaned = cleaned.strip()
    if "." in cleaned:
        cleaned = cleaned.split(".")[-1]
    return cleaned.strip()


def _extract_scope_stack(stack: List[str]) -> Optional[str]:
    return "::".join(stack) if stack else None


def _symbol_id(scope: Optional[str], name: str, arity: int, rel_path: str) -> str:
    qualified = f"{scope}::{name}" if scope else name
    return f"{qualified}/{arity}@{rel_path}"


def _qualified_name(scope: Optional[str], name: str) -> str:
    return f"{scope}::{name}" if scope else name


def _class_id(qualified: str) -> str:
    return qualified


def _namespace_id(name: str) -> str:
    return f"namespace::{name}"


def _anonymous_name(prefix: str, node) -> str:
    return f"Anonymous{prefix}@{node.start_point[0] + 1}:{node.start_point[1] + 1}"



def _count_parameters(node) -> int:
    params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter_list")
    if params is None:
        return 0
    return sum(1 for child in params.children if child.is_named and child.type != "comment")


def _count_arguments(node) -> int:
    args = node.child_by_field_name("arguments") or node.child_by_field_name("argument_list")
    if args is None:
        return 0
    return sum(1 for child in args.children if child.is_named and child.type != "comment")


def _iter_calls(func_node) -> Iterable:
    for node in _find_nodes_by_type(func_node, "call"):
        yield node


def _extract_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    expr = call_node.child_by_field_name("function")
    if expr is not None:
        if expr.type == "attribute":
            attr = expr.child_by_field_name("attribute")
            if attr is not None:
                return _normalize_call_name(_node_text(attr, source_bytes).strip())
        return _normalize_call_name(_node_text(expr, source_bytes).strip())
    text = _node_text(call_node, source_bytes).strip()
    text = text.split("(", 1)[0].strip()
    return _normalize_call_name(text)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _collect_imports(tree, source_bytes: bytes) -> List[str]:
    imports: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "import_statement"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            imports.append(text)
    for node in _find_nodes_by_type(tree.root_node, "import_from_statement"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            imports.append(text)
    return imports


_NAMESPACE_NODE_TYPES: Set[str] = set()

_CLASS_NODE_TYPES = {
    "class_definition": "class",
}

_FUNCTION_NODE_KINDS = {
    "function_definition": "function",
}


def _record_function(
    node,
    source_bytes: bytes,
    rel_path: str,
    namespace_stack: List[str],
    class_stack: List[str],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    name_override: Optional[str] = None,
    kind_override: Optional[str] = None,
    calls_root=None,
    parameters_node=None,
    exported: bool = False,
) -> None:
    name = name_override or _extract_name_field(node, source_bytes)
    kind = kind_override or _FUNCTION_NODE_KINDS.get(node.type, "function")
    if not name:
        name = _anonymous_name("Function", node)
    snippet, start_line, end_line = _node_snippet(node, source_bytes)
    comment = _extract_leading_comment(node, source_bytes)
    summary = comment
    note = _build_note(snippet, comment, summary)
    scope_stack = namespace_stack + class_stack
    scope_name = _extract_scope_stack(scope_stack)
    arity = _count_parameters(parameters_node or node)
    func_id = _symbol_id(scope_name, name, arity, rel_path)
    functions.append(
        FunctionDef(
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
        )
    )
    if class_stack:
        relations.append(
            RelationEdge(
                source_id=_class_id("::".join(namespace_stack + class_stack)),
                source_label="Type",
                target_id=func_id,
                target_label="Function",
                rel_type="CONTAINS",
                properties={},
            )
        )
    elif namespace_stack:
        relations.append(
            RelationEdge(
                source_id=_namespace_id("::".join(namespace_stack)),
                source_label="Namespace",
                target_id=func_id,
                target_label="Function",
                rel_type="CONTAINS",
                properties={},
            )
        )
    call_root = calls_root or node
    for call_node in _iter_calls(call_root):
        callee = _extract_call_name(call_node, source_bytes)
        if not callee:
            continue
        calls.append(
            CallEdge(
                caller_id=func_id,
                caller_scope=scope_name,
                callee_name=callee,
                callee_id=None,
                callee_arity=_count_arguments(call_node),
            )
        )


def _walk_tree(
    node,
    source_bytes: bytes,
    rel_path: str,
    namespace_stack: List[str],
    class_stack: List[str],
    namespaces: List[NamespaceDef],
    classes: List[ClassDef],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    namespace_registry: Dict[str, NamespaceDef],
    class_registry: Dict[str, ClassDef],
) -> None:
    if node.type == "decorated_definition":
        target = None
        for child in node.children:
            if child.type in _CLASS_NODE_TYPES or child.type in _FUNCTION_NODE_KINDS:
                target = child
                break
        if target is not None:
            _walk_tree(
                target,
                source_bytes,
                rel_path,
                namespace_stack,
                class_stack,
                namespaces,
                classes,
                functions,
                relations,
                calls,
                namespace_registry,
                class_registry,
            )
            return

    if node.type in _CLASS_NODE_TYPES:
        kind = _CLASS_NODE_TYPES[node.type]
        name = _extract_name_field(node, source_bytes)
        if not name:
            name = _anonymous_name(kind.capitalize(), node)
            kind = f"anonymous_{kind}"
        qualified = "::".join(namespace_stack + class_stack + [name]) if (namespace_stack or class_stack) else name
        class_id = _class_id(qualified)
        snippet, start_line, end_line = _node_snippet(node, source_bytes)
        comment = _extract_leading_comment(node, source_bytes)
        summary = comment
        note = _build_note(snippet, comment, summary)
        classes.append(
            ClassDef(
                symbol_id=class_id,
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
        class_registry[class_id] = classes[-1]
        if namespace_stack:
            ns_id = _namespace_id("::".join(namespace_stack))
            relations.append(
                RelationEdge(
                    source_id=ns_id,
                    source_label="Namespace",
                    target_id=class_id,
                    target_label="Type",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
        if class_stack:
            parent_type = _class_id("::".join(namespace_stack + class_stack))
            relations.append(
                RelationEdge(
                    source_id=parent_type,
                    source_label="Type",
                    target_id=class_id,
                    target_label="Type",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
                namespace_stack,
                class_stack + [name],
                namespaces,
                classes,
                functions,
                relations,
                calls,
                namespace_registry,
                class_registry,
            )
        return

    if node.type in _FUNCTION_NODE_KINDS:
        _record_function(
            node,
            source_bytes,
            rel_path,
            namespace_stack,
            class_stack,
            functions,
            relations,
            calls,
        )
        return

    for child in node.children:
        _walk_tree(
            child,
            source_bytes,
            rel_path,
            namespace_stack,
            class_stack,
            namespaces,
            classes,
            functions,
            relations,
            calls,
            namespace_registry,
            class_registry,
        )


_PLSQL_CREATE_RE = re.compile(
    r"\\bcreate\\s+(?:or\\s+replace\\s+)?(?P<kind>procedure|proc|function)\\s+(?P<name>[A-Za-z_][\\w$#\\.]+)",
    re.IGNORECASE,
)
_PLSQL_PACKAGE_BODY_RE = re.compile(
    r"\\bcreate\\s+(?:or\\s+replace\\s+)?package\\s+body\\s+(?P<name>[A-Za-z_][\\w$#]*)",
    re.IGNORECASE,
)
_PLSQL_PACKAGE_RE = re.compile(
    r"\\bcreate\\s+(?:or\\s+replace\\s+)?package\\s+(?P<name>[A-Za-z_][\\w$#]*)",
    re.IGNORECASE,
)
_PLSQL_PROC_RE = re.compile(r"\\bprocedure\\s+(?P<name>[A-Za-z_][\\w$#]*)", re.IGNORECASE)
_PLSQL_FUNC_RE = re.compile(r"\\bfunction\\s+(?P<name>[A-Za-z_][\\w$#]*)", re.IGNORECASE)
_PLSQL_CALL_RE = re.compile(r"\\bcall\\s+(?P<name>[A-Za-z_][\\w$#\\.]+)", re.IGNORECASE)
_PLSQL_EXEC_RE = re.compile(r"\\bexec(?:ute)?\\s+(?P<name>[A-Za-z_][\\w$#\\.]+)", re.IGNORECASE)
_PLSQL_GENERIC_CALL_RE = re.compile(r"\\b(?P<name>[A-Za-z_][\\w$#\\.]+)\\s*\\(", re.IGNORECASE)
_PLSQL_BODY_START_RE = re.compile(r"\\b(as|is|begin)\\b", re.IGNORECASE)

_PLSQL_CALL_KEYWORDS: Set[str] = {
    "and",
    "begin",
    "between",
    "bulk",
    "case",
    "close",
    "collect",
    "commit",
    "create",
    "cursor",
    "declare",
    "delete",
    "drop",
    "else",
    "elsif",
    "end",
    "execute",
    "exit",
    "exception",
    "fetch",
    "for",
    "forall",
    "from",
    "function",
    "group",
    "having",
    "if",
    "in",
    "insert",
    "into",
    "is",
    "join",
    "loop",
    "merge",
    "not",
    "null",
    "open",
    "or",
    "order",
    "package",
    "procedure",
    "raise",
    "return",
    "rollback",
    "select",
    "then",
    "type",
    "update",
    "values",
    "when",
    "where",
    "while",
}


def _mask_plsql_comments(text: str) -> str:
    def repl(match: re.Match) -> str:
        return " " * (match.end() - match.start())

    masked = re.sub(r"/\\*.*?\\*/", repl, text, flags=re.DOTALL)
    masked = re.sub(r"--[^\\n]*", repl, masked)
    masked = re.sub(r"//[^\\n]*", repl, masked)
    return masked


def _line_from_index(text: str, index: int) -> int:
    return text.count("\\n", 0, index) + 1


def _snippet_from_span(text: str, start_idx: int, end_idx: int) -> Tuple[str, int, int]:
    snippet = text[start_idx:end_idx]
    start_line = _line_from_index(text, start_idx)
    end_line = _line_from_index(text, max(end_idx - 1, start_idx))
    return snippet, start_line, end_line


def _extract_file_comment_from_lines(lines: List[str]) -> str:
    comment_lines: List[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if in_block:
            comment_lines.append(line)
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("--") or stripped.startswith("//"):
            comment_lines.append(line)
            continue
        if stripped.startswith("/*"):
            comment_lines.append(line)
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped == "":
            continue
        break
    return "\n".join([line.strip() for line in comment_lines if line.strip()])


def _extract_leading_comment_from_lines(lines: List[str], start_line: int) -> str:
    comment_lines: List[str] = []
    in_block = False
    idx = start_line - 2
    while idx >= 0:
        line = lines[idx]
        stripped = line.strip()
        if in_block:
            comment_lines.append(line)
            if "/*" in stripped:
                in_block = False
            idx -= 1
            continue
        if stripped.startswith("--") or stripped.startswith("//"):
            comment_lines.append(line)
            idx -= 1
            continue
        if stripped.endswith("*/") or stripped.startswith("/*"):
            comment_lines.append(line)
            if "/*" not in stripped:
                in_block = True
            idx -= 1
            continue
        if stripped == "":
            idx -= 1
            continue
        break
    return "\n".join([line.strip() for line in reversed(comment_lines) if line.strip()])


def _find_definition_end(masked_text: str, start_idx: int) -> int:
    end_match = re.search(
        r"\\bend\\b\\s*(?:[A-Za-z_][\\w$#\\.]*)?\\s*;",
        masked_text[start_idx:],
        flags=re.IGNORECASE,
    )
    if end_match:
        return start_idx + end_match.end()
    semi = masked_text.find(";", start_idx)
    if semi != -1:
        return semi + 1
    return len(masked_text)


def _find_routine_end(masked_text: str, start_idx: int, name: str) -> int:
    pattern = rf"\\bend\\b\\s+{re.escape(name)}\\s*;"
    match = re.search(pattern, masked_text[start_idx:], flags=re.IGNORECASE)
    if match:
        return start_idx + match.end()
    return _find_definition_end(masked_text, start_idx)


def _find_body_start(masked_text: str, start_idx: int, end_idx: int) -> Optional[int]:
    match = _PLSQL_BODY_START_RE.search(masked_text, start_idx, end_idx)
    if match:
        return match.end()
    return None


def _extract_paren_segment(text: str, open_index: int) -> Optional[str]:
    depth = 0
    in_string: Optional[str] = None
    for idx in range(open_index, len(text)):
        ch = text[idx]
        if in_string:
            if ch == in_string:
                in_string = None
            elif ch == "\\":
                continue
            continue
        if ch in ("'", "\""):
            in_string = ch
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : idx]
            continue
    return None


def _count_params_segment(segment: str) -> int:
    if not segment or not segment.strip():
        return 0
    depth = 0
    in_string: Optional[str] = None
    count = 0
    has_token = False
    for ch in segment:
        if in_string:
            if ch == in_string:
                in_string = None
            elif ch == "\\":
                continue
            has_token = True
            continue
        if ch in ("'", "\""):
            in_string = ch
            has_token = True
            continue
        if ch == "(":
            depth += 1
            has_token = True
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
            has_token = True
            continue
        if ch == "," and depth == 0:
            if has_token:
                count += 1
            has_token = False
            continue
        if not ch.isspace():
            has_token = True
    if has_token:
        count += 1
    return count


def _split_scope(qualified_name: str) -> Tuple[Optional[str], str]:
    if "." in qualified_name:
        scope, name = qualified_name.rsplit(".", 1)
        return scope.strip(), name.strip()
    return None, qualified_name.strip()


def _extract_calls_from_body(body_masked: str) -> List[str]:
    calls: List[str] = []
    seen: Set[str] = set()
    for match in _PLSQL_CALL_RE.finditer(body_masked):
        name = _normalize_call_name(match.group("name"))
        if name and name not in seen:
            seen.add(name)
            calls.append(name)
    for match in _PLSQL_EXEC_RE.finditer(body_masked):
        name = _normalize_call_name(match.group("name"))
        if name and name not in seen:
            seen.add(name)
            calls.append(name)
    for match in _PLSQL_GENERIC_CALL_RE.finditer(body_masked):
        raw = match.group("name")
        if raw.lower() in _PLSQL_CALL_KEYWORDS:
            continue
        name = _normalize_call_name(raw)
        if name and name not in seen:
            seen.add(name)
            calls.append(name)
    return calls


def _find_package_ranges(masked_text: str) -> List[Tuple[int, int, str]]:
    ranges: List[Tuple[int, int, str]] = []
    seen_names: Set[str] = set()
    for match in _PLSQL_PACKAGE_BODY_RE.finditer(masked_text):
        name = match.group("name")
        start_idx = match.start()
        end_idx = _find_definition_end(masked_text, match.end())
        ranges.append((start_idx, end_idx, name))
        seen_names.add(name.lower())
    for match in _PLSQL_PACKAGE_RE.finditer(masked_text):
        name = match.group("name")
        if name.lower() in seen_names:
            continue
        start_idx = match.start()
        end_idx = _find_definition_end(masked_text, match.end())
        ranges.append((start_idx, end_idx, name))
    return ranges


def parse_plsql_file(path: str, root: str) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[ClassDef],
    List[NamespaceDef],
    List[RelationEdge],
    FileDef,
]:
    rel_path = os.path.relpath(path, root)
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        source = handle.read()
    masked = _mask_plsql_comments(source)
    lines = source.splitlines()
    start_line = 1
    end_line = source.count("\\n") + 1
    file_comment = _extract_file_comment_from_lines(lines)
    file_summary = file_comment
    file_note = _build_note(source, file_comment, file_summary)
    file_def = FileDef(
        file_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        code=source,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
        imports=[],
        exports=[],
    )

    namespaces: List[NamespaceDef] = []
    classes: List[ClassDef] = []
    functions: List[FunctionDef] = []
    relations: List[RelationEdge] = []
    calls: List[CallEdge] = []
    namespace_registry: Dict[str, NamespaceDef] = {}

    for start_idx, end_idx, pkg_name in _find_package_ranges(masked):
        snippet, ns_start_line, ns_end_line = _snippet_from_span(source, start_idx, end_idx)
        comment = _extract_leading_comment_from_lines(lines, ns_start_line)
        summary = comment
        note = _build_note(snippet, comment, summary)
        namespace_id = _namespace_id(pkg_name)
        namespace = namespace_registry.get(namespace_id)
        if namespace is None:
            namespace = NamespaceDef(
                symbol_id=namespace_id,
                qualified_name=pkg_name,
                name=pkg_name,
                file_path=rel_path,
                start_line=ns_start_line,
                end_line=ns_end_line,
                code=snippet,
                comment=comment,
                summary=summary,
                note=note,
            )
            namespaces.append(namespace)
            namespace_registry[namespace_id] = namespace

        segment_masked = masked[start_idx:end_idx]
        for matcher in (_PLSQL_PROC_RE, _PLSQL_FUNC_RE):
            for match in matcher.finditer(segment_masked):
                local_start = match.start()
                abs_start = start_idx + local_start
                name = match.group("name")
                if segment_masked[max(0, local_start - 4) : local_start].lower().endswith("end"):
                    continue
                routine_end = _find_routine_end(masked, abs_start, name)
                body_start = _find_body_start(masked, abs_start, routine_end)
                if body_start is None:
                    continue
                snippet, def_start_line, def_end_line = _snippet_from_span(source, abs_start, routine_end)
                comment = _extract_leading_comment_from_lines(lines, def_start_line)
                summary = comment
                note = _build_note(snippet, comment, summary)
                param_open = source.find("(", abs_start, body_start)
                param_segment = _extract_paren_segment(source, param_open) if param_open != -1 else ""
                arity = _count_params_segment(param_segment or "")
                func_id = _symbol_id(pkg_name, name, arity, rel_path)
                kind = "procedure" if matcher is _PLSQL_PROC_RE else "function"
                functions.append(
                    FunctionDef(
                        symbol_id=func_id,
                        qualified_name=_qualified_name(pkg_name, name),
                        name=name,
                        kind=kind,
                        scope_name=pkg_name,
                        file_path=rel_path,
                        start_line=def_start_line,
                        end_line=def_end_line,
                        arity=arity,
                        code=snippet,
                        comment=comment,
                        summary=summary,
                        note=note,
                        exported=False,
                    )
                )
                relations.append(
                    RelationEdge(
                        source_id=namespace_id,
                        source_label="Namespace",
                        target_id=func_id,
                        target_label="Function",
                        rel_type="CONTAINS",
                        properties={},
                    )
                )
                body_text = source[body_start:routine_end]
                body_masked = masked[body_start:routine_end]
                for callee in _extract_calls_from_body(body_masked):
                    calls.append(
                        CallEdge(
                            caller_id=func_id,
                            caller_scope=pkg_name,
                            callee_name=callee,
                            callee_id=None,
                            callee_arity=None,
                        )
                    )

    for match in _PLSQL_CREATE_RE.finditer(masked):
        kind = match.group("kind").lower()
        if kind == "proc":
            kind = "procedure"
        full_name = match.group("name")
        start_idx = match.start()
        end_idx = _find_definition_end(masked, match.end())
        snippet, def_start_line, def_end_line = _snippet_from_span(source, start_idx, end_idx)
        comment = _extract_leading_comment_from_lines(lines, def_start_line)
        summary = comment
        note = _build_note(snippet, comment, summary)
        scope_name, name = _split_scope(full_name)
        body_start = _find_body_start(masked, match.end(), end_idx)
        if body_start is None:
            continue
        param_open = source.find("(", match.end(), body_start)
        param_segment = _extract_paren_segment(source, param_open) if param_open != -1 else ""
        arity = _count_params_segment(param_segment or "")
        func_id = _symbol_id(scope_name, name, arity, rel_path)
        functions.append(
            FunctionDef(
                symbol_id=func_id,
                qualified_name=_qualified_name(scope_name, name),
                name=name,
                kind=kind,
                scope_name=scope_name,
                file_path=rel_path,
                start_line=def_start_line,
                end_line=def_end_line,
                arity=arity,
                code=snippet,
                comment=comment,
                summary=summary,
                note=note,
                exported=False,
            )
        )
        body_masked = masked[body_start:end_idx]
        for callee in _extract_calls_from_body(body_masked):
            calls.append(
                CallEdge(
                    caller_id=func_id,
                    caller_scope=scope_name,
                    callee_name=callee,
                    callee_id=None,
                    callee_arity=None,
                )
            )

    return functions, calls, classes, namespaces, relations, file_def


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


def _scan_plsql_files(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if name.endswith(
                (".sql", ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb")
            ):
                files.append(os.path.join(dirpath, name))
    return sorted(files)


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

    def ensure_file_fields(item: Dict[str, Any]) -> None:
        ensure_text_fields(item)
        if "imports" not in item or item["imports"] is None:
            item["imports"] = []
        if "exports" not in item or item["exports"] is None:
            item["exports"] = []

    def ensure_exported_field(item: Dict[str, Any]) -> None:
        if "exported" not in item:
            item["exported"] = False

    def normalize_cached_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        file_def = payload.get("file_def")
        if isinstance(file_def, dict):
            ensure_file_fields(file_def)
        namespaces = payload.get("namespaces")
        if isinstance(namespaces, list):
            for item in namespaces:
                if isinstance(item, dict):
                    ensure_text_fields(item)
        classes = payload.get("classes")
        if isinstance(classes, list):
            for item in classes:
                if isinstance(item, dict):
                    ensure_text_fields(item)
                    ensure_exported_field(item)
        functions = payload.get("functions")
        if isinstance(functions, list):
            for item in functions:
                if isinstance(item, dict):
                    ensure_text_fields(item)
                    ensure_exported_field(item)
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
        file_namespaces,
        file_relations,
        file_def,
    ) = parse_plsql_file(file_path, root)
    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "classes": [asdict(item) for item in file_classes],
        "namespaces": [asdict(item) for item in file_namespaces],
        "relations": [asdict(item) for item in file_relations],
        "file_def": asdict(file_def),
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
    cache_root = safe_cache_root(cache_dir, "plsql_analyzer")
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    all_files = _scan_plsql_files(root)
    if verbose:
        print(f"[scan] Found {len(all_files)} PL/SQL files under {root}")
    total_files = len(all_files)

    def iter_payloads(log_parse: bool) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(all_files, start=1):
            if log_parse and verbose and (index == 1 or index % 50 == 0 or index == total_files):
                print(f"[parse] {index}/{total_files}: {file_path}")
            yield _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)

    function_index_by_name: Dict[str, List[Dict[str, Any]]] = {}
    function_index_by_name_arity: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    expected_points = 0
    for payload in iter_payloads(log_parse=True):
        for func in payload["functions"]:
            expected_points += 1
            entry = {
                "symbol_id": func["symbol_id"],
                "scope_name": func["scope_name"],
                "arity": func["arity"],
            }
            function_index_by_name.setdefault(func["name"], []).append(entry)
            if func["arity"] is not None:
                function_index_by_name_arity.setdefault((func["name"], func["arity"]), []).append(entry)

    if code_writer:
        if verbose:
            print("[graph] Writing nodes and relations (streaming)...")

        def resolve_callee_id(call: Dict[str, Any]) -> Optional[str]:
            candidates = None
            if call.get("callee_arity") is not None:
                candidates = function_index_by_name_arity.get((call["callee_name"], call["callee_arity"]))
            if not candidates:
                candidates = function_index_by_name.get(call["callee_name"])
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]["symbol_id"]
            caller_scope = call.get("caller_scope")
            if caller_scope:
                scoped = [cand for cand in candidates if cand.get("scope_name") == caller_scope]
                if len(scoped) == 1:
                    return scoped[0]["symbol_id"]
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
        all_namespaces: List[Dict[str, Any]] = []
        all_files: List[Dict[str, Any]] = []
        all_types: List[Dict[str, Any]] = []
        all_functions: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []

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
        all_namespaces: List[Dict[str, Any]] = []
        all_files: List[Dict[str, Any]] = []
        all_types: List[Dict[str, Any]] = []
        all_functions: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []

        for payload in iter_payloads(log_parse=False):
            file_def = payload["file_def"]
            file_id = file_def["file_path"]
            all_files.append(
                {
                    "id": file_id,
                    "path": file_id,
                    "start_line": file_def["start_line"],
                    "end_line": file_def["end_line"],
                    "code": file_def["code"],
                    "comment": file_def["comment"],
                    "summary": file_def["summary"],
                    "note": file_def["note"],
                    "imports": file_def.get("imports") or [],
                    "exports": file_def.get("exports") or [],
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                }
            )
            all_relations.append(
                {"source_id": project_id, "target_id": file_id, "rel_type": "CONTAINS", "properties": {}}
            )
            for ns in payload["namespaces"]:
                all_namespaces.append(
                    {
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
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": ns["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                )
            for class_def in payload["classes"]:
                all_types.append(
                    {
                        "id": class_def["symbol_id"],
                        "name": class_def["name"],
                        "qualified_name": class_def["qualified_name"],
                        "kind": class_def["kind"],
                        "file_path": class_def["file_path"],
                        "start_line": class_def["start_line"],
                        "end_line": class_def["end_line"],
                        "code": class_def["code"],
                        "comment": class_def["comment"],
                        "summary": class_def["summary"],
                        "note": class_def["note"],
                        "exported": class_def.get("exported", False),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": class_def["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
                )
            for func in payload["functions"]:
                all_functions.append(
                    {
                        "id": func["symbol_id"],
                        "name": func["name"],
                        "qualified_name": func["qualified_name"],
                        "kind": func["kind"],
                        "scope_name": func["scope_name"],
                        "class_name": None,
                        "package_name": None,
                        "file_path": func["file_path"],
                        "start_line": func["start_line"],
                        "end_line": func["end_line"],
                        "arity": func["arity"],
                        "code": func["code"],
                        "comment": func["comment"],
                        "summary": func["summary"],
                        "note": func["note"],
                        "exported": func.get("exported", False),
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
            for rel in payload["relations"]:
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

        await code_writer.write_all(            projects=all_projects,
            namespaces=all_namespaces or None,
            files=all_files or None,
            types=all_types or None,
            functions=all_functions or None,
            relations=all_relations or None,
            calls=all_calls or None,
            use_full_writers=True,
            files_variant="with_imports",
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
                                    "scope_name": func_item["scope_name"],
                                    "file_path": func_item["file_path"],
                                    "start_line": func_item["start_line"],
                                    "end_line": func_item["end_line"],
                                    "arity": func_item["arity"],
                                    "code": func_item["code"],
                                    "comment": func_item["comment"],
                                    "summary": func_item["summary"],
                                    "note": func_item["note"],
                                    "exported": func_item.get("exported", False),
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
                                "exported": func_item.get("exported", False),
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
    parser = argparse.ArgumentParser(description="Python call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing Python sources")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-pass", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.environ.get("QDRANT_COLLECTION_CODE", "plsql_functions"),
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
    parser.add_argument("--batch-size", type=int, default=4)  # for embedding - 4 function 1 turn embedding
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=128)  # for qdrant upsert - 128 vectors 1 time upsert
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
        cache_root = safe_cache_root(args.cache_dir, "plsql_analyzer")
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "plsql"
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
            files = _scan_plsql_files(args.root)
            print(f"Dry run: {len(files)} PL/SQL files found")
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

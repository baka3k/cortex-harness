from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
import torch
from transformers import AutoModel, AutoTokenizer
from tree_sitter import Language, Parser

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config
from tools.common.analyzer_cache import (
    file_signature,
    load_parse_cache,
    load_state,
    safe_cache_root,
    write_parse_cache,
    write_state,
)
from tools.common.cloc_stats import collect_cloc_stats, normalize_cloc_payload, write_cloc_stats_to_neo4j
from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files, cleanup_qdrant_with_writer
from tools.common.message_scan import default_message_collection_name, run_message_scan_pipeline
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.writer.language_writer import LanguageCodeWriter

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:
    ts_get_parser = None

try:
    from tree_sitter_language_pack import get_parser as tslp_get_parser
except Exception:
    tslp_get_parser = None


_PARSE_CACHE_VERSION = "delphi-v2026-03-09-1"


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
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, Any]


@dataclass
class CallEdge:
    caller_id: str
    caller_file: str
    caller_scope: Optional[str]
    call_line: int
    callee_raw: str
    callee_name: str
    call_arity: int
    callee_id: Optional[str]


def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")


def _line_from_byte(source_bytes: bytes, byte_index: int) -> int:
    return source_bytes[:byte_index].count(b"\n") + 1


def _find_nodes_by_type(node, node_type: str) -> Iterable:
    if node.type == node_type:
        yield node
    for child in node.children:
        yield from _find_nodes_by_type(child, node_type)


def _tree_error_stats(tree) -> Tuple[bool, int]:
    if tree is None:
        return False, 0
    has_error = bool(getattr(tree.root_node, "has_error", False))
    error_nodes = sum(1 for _ in _find_nodes_by_type(tree.root_node, "ERROR"))
    return has_error, error_nodes


def _extract_file_comment_from_text(text: str) -> str:
    lines = text.splitlines()
    comments: List[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if comments:
                break
            continue
        if in_block:
            comments.append(stripped)
            if "}" in stripped or "*)" in stripped:
                in_block = False
            continue
        if stripped.startswith("//"):
            comments.append(stripped)
            continue
        if stripped.startswith("{") or stripped.startswith("(*"):
            comments.append(stripped)
            if not ("}" in stripped or "*)" in stripped):
                in_block = True
            continue
        break
    return "\n".join(comments)


def _build_note(code: str, comment: str, summary: str) -> str:
    parts: List[str] = []
    if summary:
        parts.append(f"Summary:\n{summary}")
    if comment:
        parts.append(f"Comment:\n{comment}")
    if code:
        parts.append(f"Code:\n{code}")
    return "\n\n".join(parts)


def _normalize_call_name(text: str) -> str:
    cleaned = text.strip()
    cleaned = cleaned.replace("self.", "")
    cleaned = cleaned.replace("inherited ", "")
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


def _namespace_id(name: str) -> str:
    return f"namespace::{name}"


def _type_id(qualified: str) -> str:
    return qualified


def _normalize_type_name(text: str) -> Optional[str]:
    cleaned = re.sub(r"<[^<>]*>", "", text)
    cleaned = re.sub(
        r"(?i)\b(const|var|out|array\s+of|class\s+of|packed|reference\s+to|specialize|generic)\b",
        " ",
        cleaned,
    )
    cleaned = cleaned.replace("^", " ")
    match = re.search(r"[A-Za-z_][A-Za-z0-9_\.]*", cleaned)
    if not match:
        return None
    name = match.group(0)
    if "." in name:
        name = name.split(".")[-1]
    return name


def _strip_comments_and_strings(text: str) -> str:
    pattern = re.compile(
        r"'([^']|'')*'|\{[^}]*\}|\(\*.*?\*\)|//.*?$",
        re.MULTILINE | re.DOTALL,
    )

    def repl(match: re.Match[str]) -> str:
        return " " * (match.end() - match.start())

    return pattern.sub(repl, text)


def _find_matching_end_block(text: str, begin_idx: int) -> Optional[int]:
    masked = _strip_comments_and_strings(text)
    token_re = re.compile(r"\b(begin|end)\b", re.IGNORECASE)
    depth = 0
    started = False
    for token in token_re.finditer(masked, begin_idx):
        value = token.group(1).lower()
        if value == "begin":
            depth += 1
            started = True
        else:
            if not started:
                continue
            depth -= 1
            if depth == 0:
                end_pos = token.end()
                while end_pos < len(text) and text[end_pos].isspace():
                    end_pos += 1
                if end_pos < len(text) and text[end_pos] == ";":
                    end_pos += 1
                return end_pos
    return None


def _find_matching_paren(text: str, open_idx: int) -> Optional[int]:
    depth = 0
    in_string = False
    i = open_idx
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _split_identifier_list(text: str) -> List[str]:
    values = [item.strip() for item in text.split(",")]
    return [item for item in values if item]


def _count_signature_arity(params_text: str) -> int:
    if not params_text:
        return 0
    inside = params_text.strip()
    if inside.startswith("(") and inside.endswith(")"):
        inside = inside[1:-1]
    inside = inside.strip()
    if not inside:
        return 0

    segments = [segment.strip() for segment in inside.split(";") if segment.strip()]
    count = 0
    for segment in segments:
        part = segment.split("=", 1)[0].strip()
        if ":" in part:
            names = part.split(":", 1)[0].strip()
            if names:
                count += len(_split_identifier_list(names))
                continue
        count += 1
    return count


def _extract_call_arity(body_text: str, open_paren_idx: int) -> int:
    close_idx = _find_matching_paren(body_text, open_paren_idx)
    if close_idx is None:
        return 0
    inside = body_text[open_paren_idx + 1 : close_idx].strip()
    if not inside:
        return 0
    depth = 0
    count = 1
    in_string = False
    i = 0
    while i < len(inside):
        ch = inside[i]
        if in_string:
            if ch == "'":
                if i + 1 < len(inside) and inside[i + 1] == "'":
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            i += 1
            continue
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth = max(depth - 1, 0)
        elif ch == "," and depth == 0:
            count += 1
        i += 1
    return count


def _extract_unit_name(text: str, rel_path: str) -> str:
    for pattern in (
        r"(?im)^\s*unit\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
        r"(?im)^\s*program\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
        r"(?im)^\s*library\s+([A-Za-z_][A-Za-z0-9_]*)\s*;",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return os.path.splitext(os.path.basename(rel_path))[0]


def _line_in_ranges(line_no: int, ranges: Optional[List[Tuple[int, int]]]) -> bool:
    if not ranges:
        return True
    for start, end in ranges:
        if start <= line_no <= end:
            return True
    return False


def _extract_uses_units(text: str, allowed_line_ranges: Optional[List[Tuple[int, int]]] = None) -> List[str]:
    results: List[str] = []
    seen: set[str] = set()
    masked = _strip_comments_and_strings(text)
    for match in re.finditer(r"(?is)\buses\b\s*([^;]+);", masked):
        uses_line = text.count("\n", 0, match.start()) + 1
        if not _line_in_ranges(uses_line, allowed_line_ranges):
            continue
        chunk = text[match.start(1) : match.end(1)]
        parts = [item.strip() for item in chunk.split(",")]
        for part in parts:
            if not part:
                continue
            unit_match = re.match(r"^([A-Za-z_][A-Za-z0-9_\.]*)", part)
            if not unit_match:
                continue
            unit_name = unit_match.group(1)
            if unit_name.lower() in seen:
                continue
            seen.add(unit_name.lower())
            results.append(unit_name)
    return results


def _merge_line_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not ranges:
        return []
    cleaned = sorted((max(1, int(start)), max(1, int(end))) for start, end in ranges if start and end)
    merged: List[Tuple[int, int]] = []
    for start, end in cleaned:
        if start > end:
            start, end = end, start
        if not merged:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _find_type_declaration_end(text: str, decl_end_idx: int) -> Optional[int]:
    trailing = re.match(r"\s*;", text[decl_end_idx:])
    if trailing:
        return decl_end_idx + trailing.end()

    masked = _strip_comments_and_strings(text)
    token_re = re.compile(r"(?is)=\s*(?:packed\s+)?(class|record|interface)\b|\bend\s*;")
    depth = 1
    for token in token_re.finditer(masked, decl_end_idx):
        if token.group(1):
            depth += 1
            continue
        depth -= 1
        if depth == 0:
            return token.end()
    return None


def _register_type_usage(
    source_id: str,
    source_label: str,
    type_text: str,
    rel_path: str,
    types: List[TypeDef],
    relations: List[RelationEdge],
    type_registry: Dict[str, TypeDef],
) -> None:
    primitive_types = {
        "integer",
        "int64",
        "word",
        "longword",
        "cardinal",
        "byte",
        "shortint",
        "smallint",
        "single",
        "double",
        "extended",
        "real",
        "currency",
        "boolean",
        "string",
        "ansistring",
        "widestring",
        "unicodestring",
        "char",
        "widechar",
        "pchar",
        "pointer",
        "variant",
        "olevariant",
        "tobject",
        "nil",
        "void",
    }

    parts: List[str] = []
    if ":" in type_text:
        for match in re.finditer(r":\s*([^;\)\n=]+)", type_text):
            parts.append(match.group(1).strip())
    if not parts:
        parts = [type_text]

    seen_local: set[str] = set()
    for part in parts:
        type_name = _normalize_type_name(part)
        if not type_name:
            continue
        if type_name.lower() in primitive_types:
            continue
        if type_name in seen_local:
            continue
        seen_local.add(type_name)

        type_id = _type_id(type_name)
        if type_id not in type_registry:
            placeholder = TypeDef(
                symbol_id=type_id,
                qualified_name=type_name,
                name=type_name,
                kind="external",
                file_path=rel_path,
                start_line=0,
                end_line=0,
                code=type_name,
                comment="",
                summary="",
                note="",
            )
            type_registry[type_id] = placeholder
            types.append(placeholder)

        relations.append(
            RelationEdge(
                source_id=source_id,
                source_label=source_label,
                target_id=type_id,
                target_label="Type",
                rel_type="USES_TYPE",
                properties={},
            )
        )

        if "^" in part:
            relations.append(
                RelationEdge(
                    source_id=source_id,
                    source_label=source_label,
                    target_id=type_id,
                    target_label="Type",
                    rel_type="POINTER_TO",
                    properties={"kind": "pointer"},
                )
            )


def _extract_type_declarations(
    text: str,
    namespace_name: str,
    rel_path: str,
    types: List[TypeDef],
    functions: List[FunctionDef],
    fields: List[FieldDef],
    relations: List[RelationEdge],
    type_registry: Dict[str, TypeDef],
    function_registry: set[str],
    type_block_ranges: List[Tuple[int, int]],
    allowed_line_ranges: Optional[List[Tuple[int, int]]] = None,
) -> None:
    type_decl_re = re.compile(
        r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(class|record|interface)\b(?:\s*\(([^)]*)\))?",
        re.MULTILINE,
    )

    for match in type_decl_re.finditer(text):
        type_name = match.group(1)
        type_kind = match.group(2).lower()
        base_types = match.group(3) or ""
        start_idx = match.start()
        end_idx = _find_type_declaration_end(text, match.end())
        if end_idx is None:
            end_idx = min(len(text), start_idx + 800)

        snippet = text[start_idx:end_idx]
        start_line = text[:start_idx].count("\n") + 1
        end_line = text[:end_idx].count("\n") + 1
        if not _line_in_ranges(start_line, allowed_line_ranges):
            continue

        qualified = f"{namespace_name}::{type_name}" if namespace_name else type_name
        type_id = _type_id(qualified)
        if type_id in type_registry:
            continue

        type_def = TypeDef(
            symbol_id=type_id,
            qualified_name=qualified,
            name=type_name,
            kind=type_kind,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            code=snippet,
            comment="",
            summary="",
            note="",
        )
        type_registry[type_id] = type_def
        types.append(type_def)
        type_block_ranges.append((start_line, end_line))

        for base_item in [item.strip() for item in base_types.split(",") if item.strip()]:
            base_name = _normalize_type_name(base_item)
            if not base_name:
                continue
            base_id = _type_id(base_name)
            if base_id not in type_registry:
                placeholder = TypeDef(
                    symbol_id=base_id,
                    qualified_name=base_name,
                    name=base_name,
                    kind="external",
                    file_path=rel_path,
                    start_line=0,
                    end_line=0,
                    code=base_name,
                    comment="",
                    summary="",
                    note="",
                )
                type_registry[base_id] = placeholder
                types.append(placeholder)
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

        method_re = re.compile(
            r"(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+"
            r"([A-Za-z_][A-Za-z0-9_]*)\s*(\([^;\n]*\))?\s*(?::\s*([^;\n]+))?\s*;",
            re.MULTILINE,
        )
        body_offset = match.end()
        body_text = text[body_offset:end_idx]
        for method_match in method_re.finditer(body_text):
            method_name = (method_match.group(3) or "").strip()
            if not method_name:
                continue
            params_text = (method_match.group(4) or "").strip()
            return_type_text = (method_match.group(5) or "").strip()
            method_kind = (method_match.group(2) or "").lower()
            arity = _count_signature_arity(params_text)
            method_scope = qualified
            method_symbol_id = _symbol_id(method_scope, method_name, arity, rel_path)
            if method_symbol_id in function_registry:
                continue
            function_registry.add(method_symbol_id)

            method_start_idx = body_offset + method_match.start()
            method_end_idx = body_offset + method_match.end()
            method_start_line = text[:method_start_idx].count("\n") + 1
            method_end_line = text[:method_end_idx].count("\n") + 1
            method_snippet = text[method_start_idx:method_end_idx]

            functions.append(
                FunctionDef(
                    symbol_id=method_symbol_id,
                    qualified_name=_qualified_name(method_scope, method_name),
                    name=method_name,
                    kind=f"{method_kind}_declaration"
                    if method_kind in {"constructor", "destructor"}
                    else "declaration",
                    scope_name=method_scope,
                    file_path=rel_path,
                    start_line=method_start_line,
                    end_line=method_end_line,
                    arity=arity,
                    code=method_snippet,
                    comment="",
                    summary="",
                    note="",
                )
            )

            relations.append(
                RelationEdge(
                    source_id=type_id,
                    source_label="Type",
                    target_id=method_symbol_id,
                    target_label="Function",
                    rel_type="DECLARES",
                    properties={"declared_in_type": True},
                )
            )
            if namespace_name:
                relations.append(
                    RelationEdge(
                        source_id=_namespace_id(namespace_name),
                        source_label="Namespace",
                        target_id=method_symbol_id,
                        target_label="Function",
                        rel_type="CONTAINS",
                        properties={},
                    )
                )

            _register_type_usage(
                method_symbol_id,
                "Function",
                params_text,
                rel_path,
                types,
                relations,
                type_registry,
            )
            if return_type_text:
                _register_type_usage(
                    method_symbol_id,
                    "Function",
                    return_type_text,
                    rel_path,
                    types,
                    relations,
                    type_registry,
                )

        # Basic class field extraction (declarative fields)
        field_re = re.compile(
            r"(?im)^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)\s*:\s*([^;=\n]+)\s*;"
        )
        for field_match in field_re.finditer(body_text):
            prefix = field_match.group(1).strip().lower()
            if prefix in {
                "public",
                "private",
                "protected",
                "published",
                "strict private",
                "strict protected",
                "class",
                "property",
                "procedure",
                "function",
                "constructor",
                "destructor",
            }:
                continue
            type_sig = field_match.group(2).strip()
            line = text[: match.end() + field_match.start()].count("\n") + 1
            for field_name in _split_identifier_list(field_match.group(1)):
                field_id = f"{qualified}::{field_name}@{rel_path}"
                fields.append(
                    FieldDef(
                        symbol_id=field_id,
                        qualified_name=f"{qualified}::{field_name}",
                        name=field_name,
                        scope_name=qualified,
                        type_signature=type_sig,
                        file_path=rel_path,
                        start_line=line,
                        end_line=line,
                        code=field_match.group(0),
                    )
                )
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
                    type_sig,
                    rel_path,
                    types,
                    relations,
                    type_registry,
                )


def _parse_function_signatures(
    text: str,
    namespace_name: str,
    rel_path: str,
    types: List[TypeDef],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    type_registry: Dict[str, TypeDef],
    function_registry: set[str],
    declaration_skip_line_ranges: Optional[List[Tuple[int, int]]] = None,
    allowed_line_ranges: Optional[List[Tuple[int, int]]] = None,
) -> None:
    signature_re = re.compile(
        r"(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+"
        r"([A-Za-z_][A-Za-z0-9_\.]*)\s*(\([^;\n]*\))?\s*(?::\s*([^;\n]+))?\s*;",
        re.MULTILINE,
    )

    matches = list(signature_re.finditer(text))
    for idx, match in enumerate(matches):
        is_class = bool(match.group(1))
        kind = (match.group(2) or "").lower()
        raw_name = (match.group(3) or "").strip()
        params_text = (match.group(4) or "").strip()
        return_type_text = (match.group(5) or "").strip()
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)

        local_scope = ""
        func_name = raw_name
        if "." in raw_name:
            local_scope, func_name = raw_name.rsplit(".", 1)

        scope_parts: List[str] = []
        if namespace_name:
            scope_parts.append(namespace_name)
        if local_scope:
            scope_parts.extend([part for part in local_scope.split(".") if part])
        scope_name = _extract_scope_stack(scope_parts)

        signature_start = match.start()
        start_line = text[:signature_start].count("\n") + 1
        if not _line_in_ranges(start_line, allowed_line_ranges):
            continue

        if "." not in raw_name and declaration_skip_line_ranges and _line_in_ranges(start_line, declaration_skip_line_ranges):
            continue

        arity = _count_signature_arity(params_text)
        begin_match = re.search(r"(?is)\bbegin\b", text[match.end() : next_start])
        body_end = None
        if begin_match:
            begin_idx = match.end() + begin_match.start()
            body_end = _find_matching_end_block(text, begin_idx)

        if body_end is not None:
            end_idx = body_end
            function_kind = "function"
        else:
            end_idx = match.end()
            function_kind = "declaration"

        end_line = text[:end_idx].count("\n") + 1
        snippet = text[signature_start:end_idx]

        symbol_id = _symbol_id(scope_name, func_name, arity, rel_path)
        qualified = _qualified_name(scope_name, func_name)
        existing_idx = next((i for i, item in enumerate(functions) if item.symbol_id == symbol_id), None)
        function_payload = FunctionDef(
            symbol_id=symbol_id,
            qualified_name=qualified,
            name=func_name,
            kind=f"{kind}_{function_kind}" if kind in {"constructor", "destructor"} else function_kind,
            scope_name=scope_name,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            arity=arity,
            code=snippet,
            comment="",
            summary="",
            note="",
        )

        if existing_idx is not None:
            if body_end is None:
                continue
            functions[existing_idx] = function_payload
        else:
            function_registry.add(symbol_id)
            functions.append(function_payload)

            if scope_name:
                type_id = _type_id(scope_name)
                if type_id in type_registry:
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
            if namespace_name:
                relations.append(
                    RelationEdge(
                        source_id=_namespace_id(namespace_name),
                        source_label="Namespace",
                        target_id=symbol_id,
                        target_label="Function",
                        rel_type="CONTAINS",
                        properties={"static": bool(is_class)},
                    )
                )

            _register_type_usage(
                symbol_id,
                "Function",
                params_text,
                rel_path,
                types,
                relations,
                type_registry,
            )
            if return_type_text:
                _register_type_usage(
                    symbol_id,
                    "Function",
                    return_type_text,
                    rel_path,
                    types,
                    relations,
                    type_registry,
                )

        if body_end is None:
            continue
        body_text = text[match.end() : body_end]
        call_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_\.]*)\s*\(", re.IGNORECASE)
        for call_match in call_re.finditer(body_text):
            raw_call = (call_match.group(1) or "").strip()
            if not raw_call:
                continue
            lowered = raw_call.lower()
            if lowered in {
                "if",
                "for",
                "while",
                "case",
                "inherited",
                "with",
                "array",
                "setlength",
                "length",
                "high",
                "low",
                "ord",
                "chr",
            }:
                continue
            open_idx = call_match.end() - 1
            arity_guess = _extract_call_arity(body_text, open_idx)
            call_line = text[: match.end() + call_match.start()].count("\n") + 1
            calls.append(
                CallEdge(
                    caller_id=symbol_id,
                    caller_file=rel_path,
                    caller_scope=scope_name,
                    call_line=call_line,
                    callee_raw=raw_call,
                    callee_name=_normalize_call_name(raw_call),
                    call_arity=arity_guess,
                    callee_id=None,
                )
            )


def _get_delphi_parser() -> Optional[Parser]:
    parser: Optional[Parser] = None
    if tslp_get_parser is not None:
        for name in ("pascal", "delphi", "object_pascal", "objectpascal"):
            try:
                parser = tslp_get_parser(name)
                if parser is not None:
                    return parser
            except Exception:
                continue

    if ts_get_parser is not None:
        for name in ("pascal", "delphi", "objectpascal"):
            try:
                parser = ts_get_parser(name)
                if parser is not None:
                    return parser
            except Exception:
                continue

    for module_name, symbol_name in (
        ("tree_sitter_pascal", "language"),
        ("tree_sitter_delphi", "language"),
    ):
        try:
            module = __import__(module_name, fromlist=[symbol_name])
            language_factory = getattr(module, symbol_name)
            language = language_factory()
            if not isinstance(language, Language):
                language = Language(language)
            parser = Parser()
            if hasattr(parser, "set_language"):
                parser.set_language(language)
            else:
                parser.language = language
            return parser
        except Exception:
            continue

    return None


def _extract_section_line_ranges_from_tree(tree) -> Dict[str, List[Tuple[int, int]]]:
    ranges: Dict[str, List[Tuple[int, int]]] = {"interface": [], "implementation": []}
    if tree is None:
        return ranges

    interface_types = {
        "interface_section",
        "interface_part",
        "unit_interface",
    }
    implementation_types = {
        "implementation_section",
        "implementation_part",
        "unit_implementation",
    }

    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        node_type = str(getattr(node, "type", "") or "").lower()
        start_line = int(getattr(node, "start_point", (0, 0))[0]) + 1
        end_line = int(getattr(node, "end_point", (0, 0))[0]) + 1
        if node_type in interface_types or ("interface" in node_type and ("section" in node_type or "part" in node_type)):
            ranges["interface"].append((start_line, end_line))
        elif node_type in implementation_types or (
            "implementation" in node_type and ("section" in node_type or "part" in node_type)
        ):
            ranges["implementation"].append((start_line, end_line))
        for child in reversed(node.children):
            stack.append(child)

    ranges["interface"] = _merge_line_ranges(ranges["interface"])
    ranges["implementation"] = _merge_line_ranges(ranges["implementation"])
    return ranges


def _parse_file(path: str) -> Tuple[Optional[Any], bytes, bool]:
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    parser = _get_delphi_parser()
    if parser is None:
        return None, source_bytes, False
    tree = parser.parse(source_bytes)
    return tree, source_bytes, True


def parse_delphi_file(path: str, root: str) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[TypeDef],
    List[NamespaceDef],
    List[FieldDef],
    List[RelationEdge],
    FileDef,
    List[str],
    Dict[str, Any],
]:
    rel_path = os.path.relpath(path, root)
    tree, source_bytes, parser_available = _parse_file(path)
    text = source_bytes.decode("utf-8", errors="ignore")

    has_error, error_nodes = _tree_error_stats(tree)
    parser_language = "delphi_tree_sitter" if parser_available else "regex_fallback"
    section_ranges = _extract_section_line_ranges_from_tree(tree) if parser_available else {"interface": [], "implementation": []}
    parser_guided_ranges = bool(section_ranges.get("interface") or section_ranges.get("implementation"))
    all_section_ranges = _merge_line_ranges(
        [*(section_ranges.get("interface") or []), *(section_ranges.get("implementation") or [])]
    )

    file_comment = _extract_file_comment_from_text(text)
    file_summary = file_comment
    file_note = _build_note(text, file_comment, file_summary)
    file_def = FileDef(
        file_path=rel_path,
        start_line=1,
        end_line=text.count("\n") + 1,
        code=text,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
    )

    namespace_name = _extract_unit_name(text, rel_path)
    namespace_id = _namespace_id(namespace_name)
    namespace_def = NamespaceDef(
        symbol_id=namespace_id,
        qualified_name=namespace_name,
        name=namespace_name,
        file_path=rel_path,
        start_line=1,
        end_line=max(1, text.count("\n") + 1),
        code=f"unit {namespace_name};",
        comment="",
        summary="",
        note="",
    )

    uses_units = _extract_uses_units(text, allowed_line_ranges=all_section_ranges or None)

    types: List[TypeDef] = []
    functions: List[FunctionDef] = []
    fields: List[FieldDef] = []
    relations: List[RelationEdge] = []
    calls: List[CallEdge] = []
    function_registry: set[str] = set()
    type_block_ranges: List[Tuple[int, int]] = []

    type_registry: Dict[str, TypeDef] = {}

    _extract_type_declarations(
        text,
        namespace_name,
        rel_path,
        types,
        functions,
        fields,
        relations,
        type_registry,
        function_registry,
        type_block_ranges,
        allowed_line_ranges=(section_ranges.get("interface") or all_section_ranges or None),
    )
    _parse_function_signatures(
        text,
        namespace_name,
        rel_path,
        types,
        functions,
        relations,
        calls,
        type_registry,
        function_registry,
        declaration_skip_line_ranges=_merge_line_ranges(type_block_ranges),
        allowed_line_ranges=all_section_ranges or None,
    )

    for type_def in types:
        if type_def.kind == "external":
            continue
        relations.append(
            RelationEdge(
                source_id=namespace_id,
                source_label="Namespace",
                target_id=type_def.symbol_id,
                target_label="Type",
                rel_type="CONTAINS",
                properties={},
            )
        )

    for field in fields:
        relations.append(
            RelationEdge(
                source_id=namespace_id,
                source_label="Namespace",
                target_id=field.symbol_id,
                target_label="Field",
                rel_type="CONTAINS",
                properties={},
            )
        )

    parse_meta = {
        "parser_language": parser_language,
        "parser_available": parser_available,
        "has_error": has_error,
        "error_nodes": error_nodes,
        "range_guided_by_tree": parser_guided_ranges,
        "interface_ranges": section_ranges.get("interface") or [],
        "implementation_ranges": section_ranges.get("implementation") or [],
    }

    return (
        functions,
        calls,
        types,
        [namespace_def],
        fields,
        relations,
        file_def,
        uses_units,
        parse_meta,
    )


def _resolve_calls(functions: List[FunctionDef], calls: List[CallEdge], uses_closure_by_file: Optional[Dict[str, set[str]]] = None) -> None:
    by_name: Dict[str, List[FunctionDef]] = {}
    by_name_arity: Dict[Tuple[str, int], List[FunctionDef]] = {}
    by_scope_name: Dict[Tuple[Optional[str], str], List[FunctionDef]] = {}
    by_scope_name_arity: Dict[Tuple[Optional[str], str, int], List[FunctionDef]] = {}
    by_file_name: Dict[Tuple[str, str], List[FunctionDef]] = {}
    by_file_name_arity: Dict[Tuple[str, str, int], List[FunctionDef]] = {}
    by_qualified: Dict[str, FunctionDef] = {}
    by_qualified_arity: Dict[Tuple[str, int], FunctionDef] = {}

    for func in functions:
        by_name.setdefault(func.name, []).append(func)
        by_name_arity.setdefault((func.name, func.arity), []).append(func)
        by_scope_name.setdefault((func.scope_name, func.name), []).append(func)
        by_scope_name_arity.setdefault((func.scope_name, func.name, func.arity), []).append(func)
        by_file_name.setdefault((func.file_path, func.name), []).append(func)
        by_file_name_arity.setdefault((func.file_path, func.name, func.arity), []).append(func)
        by_qualified[func.qualified_name] = func
        by_qualified_arity[(func.qualified_name, func.arity)] = func

    def scope_chain(scope: Optional[str]) -> List[Optional[str]]:
        if not scope:
            return [None]
        parts = scope.split("::")
        chain = ["::".join(parts[:idx]) for idx in range(len(parts), 0, -1)]
        chain.append(None)
        return chain

    for call in calls:
        candidates: Dict[str, Tuple[int, str]] = {}

        def add(items: Iterable[FunctionDef], base_score: int) -> None:
            for item in items:
                score = base_score
                if item.file_path == call.caller_file:
                    score += 15
                closure = uses_closure_by_file.get(call.caller_file, set()) if uses_closure_by_file else set()
                if item.file_path in closure:
                    score += 7
                if call.caller_scope and item.scope_name == call.caller_scope:
                    score += 10
                tie = item.qualified_name
                existing = candidates.get(item.symbol_id)
                if existing is None or (score, tie) > existing:
                    candidates[item.symbol_id] = (score, tie)

        raw = call.callee_raw
        if "::" in raw:
            direct = by_qualified_arity.get((raw, call.call_arity))
            if direct is not None:
                add([direct], 130)
            direct = by_qualified.get(raw)
            if direct is not None:
                add([direct], 120)

        short_name = call.callee_name
        add(by_file_name_arity.get((call.caller_file, short_name, call.call_arity), []), 115)
        add(by_file_name.get((call.caller_file, short_name), []), 105)

        for depth, scope in enumerate(scope_chain(call.caller_scope)):
            add(by_scope_name_arity.get((scope, short_name, call.call_arity), []), 95 - min(depth, 20))
            add(by_scope_name.get((scope, short_name), []), 85 - min(depth, 20))

        add(by_name_arity.get((short_name, call.call_arity), []), 70)
        add(by_name.get(short_name, []), 55)

        if candidates:
            call.callee_id = max(candidates.items(), key=lambda item: (item[1][0], item[1][1]))[0]


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


def _resolve_embedding_model_source(model_name: str) -> str:
    local_model_path = os.environ.get("CODE_EMBEDDING_MODEL_PATH")
    if not local_model_path:
        return model_name
    resolved_path = os.path.abspath(os.path.expanduser(local_model_path))
    if not os.path.exists(resolved_path):
        raise FileNotFoundError(
            "CODE_EMBEDDING_MODEL_PATH does not exist: %s" % local_model_path
        )
    return resolved_path


class CodeEmbedder:
    def __init__(self, model_name: str, device: str, max_embed_chars: int, chunk_embed: bool) -> None:
        model_source = _resolve_embedding_model_source(model_name)
        trust_remote_code = _should_trust_remote_code(model_name) or _should_trust_remote_code(model_source)
        extra_tokenizer_kwargs = {"fix_mistral_regex": True} if trust_remote_code else {}
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            trust_remote_code=trust_remote_code,
            **extra_tokenizer_kwargs,
        )
        self.model = AutoModel.from_pretrained(
            model_source,
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


def _unknown_function_id(callee_name: str) -> str:
    normalized = (callee_name or "").strip() or "unknown"
    return f"unknown::{_stable_point_id(normalized.lower())}"


def _collect_unit_and_uses_index(
    all_scanned_files: List[str],
    root: str,
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    unit_name_by_file: Dict[str, str] = {}
    uses_by_file: Dict[str, List[str]] = {}
    for abs_path in all_scanned_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        try:
            with open(abs_path, "rb") as handle:
                raw = handle.read()
            text = raw.decode("utf-8", errors="ignore")
        except OSError:
            unit_name_by_file[rel_path] = os.path.splitext(os.path.basename(rel_path))[0]
            uses_by_file[rel_path] = []
            continue
        unit_name_by_file[rel_path] = _extract_unit_name(text, rel_path)
        uses_by_file[rel_path] = _extract_uses_units(text)
    return unit_name_by_file, uses_by_file


def _resolve_uses_by_file(
    *,
    uses_by_file: Dict[str, List[str]],
    unit_name_by_file: Dict[str, str],
    all_scanned_files: List[str],
    root: str,
) -> Dict[str, List[str]]:
    file_lookup_by_basename: Dict[str, str] = {}
    file_lookup_by_unit: Dict[str, str] = {}
    for abs_path in all_scanned_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        stem = os.path.splitext(os.path.basename(rel_path))[0].lower()
        file_lookup_by_basename.setdefault(stem, rel_path)

    for rel_path, unit_name in unit_name_by_file.items():
        raw = (unit_name or "").strip().lower()
        if raw:
            file_lookup_by_unit.setdefault(raw, rel_path)

    resolved_uses_by_file: Dict[str, List[str]] = {}
    for file_path, units in uses_by_file.items():
        resolved: List[str] = []
        seen: set[str] = set()
        for unit_name in units:
            raw = unit_name.strip().lower()
            if not raw:
                continue
            candidates = [raw]
            if "." in raw:
                candidates.append(raw.split(".")[-1])
            target = None
            for cand in candidates:
                target = file_lookup_by_unit.get(cand) or file_lookup_by_basename.get(cand)
                if target:
                    break
            if not target or target == file_path or target in seen:
                continue
            seen.add(target)
            resolved.append(target)
        resolved_uses_by_file[file_path] = resolved
    return resolved_uses_by_file


def _expand_impacted_files_by_uses(
    changed_existing: set[str],
    resolved_uses_by_file: Dict[str, List[str]],
) -> set[str]:
    reverse_map: Dict[str, set[str]] = {}
    for source_file, deps in resolved_uses_by_file.items():
        for dep in deps:
            reverse_map.setdefault(dep, set()).add(source_file)

    impacted: set[str] = set()
    queue: List[str] = list(changed_existing)
    seen: set[str] = set(changed_existing)
    while queue:
        current = queue.pop(0)
        for dependent in sorted(reverse_map.get(current, set())):
            if dependent in seen:
                continue
            seen.add(dependent)
            impacted.add(dependent)
            queue.append(dependent)
    return impacted


def _should_ignore_directory(dir_name: str, dir_path: str) -> bool:
    """
    Check if a directory should be ignored during Delphi project scanning.

    Ignores:
    - Delphi build outputs: __history__, __recovery/, *.dcu, *.dcp
    - Compiled files: *.exe, *.dll
    - Project cache: *.dproj.local, *.groupproj.local
    - IDE: .idea/, .vscode/, *.~*
    """
    ignore_patterns = {
        # Version control
        ".git", ".svn", ".hg",

        # IDE
        ".idea", ".vscode",

        # Delphi/Pascal specific
        "__history__", "__recovery__",
        "lib", "dcu", "dcp",

        # Build outputs
        "bin", "obj", "build", "out", "output",

        # Backup
        "backup", "backups", "tmp", "temp", ".tmp",

        # OS specific
        ".DS_Store", "Thumbs.db",

        # Node (mixed projects)
        "node_modules", "dist",

        # Cache
        ".cache", "__pycache__",

        # Delphi compiled
        "*.dcu", "*.dcp", "*.dpu", "*.exe", "*.dll", "*.bpl",
    }

    if dir_name in ignore_patterns:
        return True

    if dir_name.endswith((".~", ".swp", ".swo")):
        return True

    return False


def _scan_delphi_files(root: str) -> List[str]:
    """
    Scan for Delphi files, ignoring unnecessary directories.
    """
    files: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Filter out ignored directories in-place
        dirnames[:] = [
            d for d in dirnames
            if not _should_ignore_directory(d, os.path.join(dirpath, d))
        ]

        for name in filenames:
            lower = name.lower()

            # Skip compiled and IDE backup files
            if lower.endswith((".dcu", ".dcp", ".dpu", ".exe", ".dll", ".bpl", ".~")):
                continue
            if name.endswith((".swp", ".swo", ".local")):
                continue
            if name in (".DS_Store", "Thumbs.db"):
                continue

            if lower.endswith((".pas", ".dpr", ".inc")):
                files.append(os.path.join(dirpath, name))

    return sorted(files)


def _load_or_parse_payload(
    file_path: str,
    root: str,
    parse_cache_root: str,
    parse_cache: bool,
) -> Dict[str, Any]:
    def normalize_cached_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        parse_meta = payload.get("parse_meta")
        if not isinstance(parse_meta, dict):
            payload["parse_meta"] = {
                "parser_language": "unknown",
                "parser_available": False,
                "has_error": False,
                "error_nodes": 0,
                "range_guided_by_tree": False,
                "interface_ranges": [],
                "implementation_ranges": [],
            }
        else:
            parser_language = parse_meta.get("parser_language") or "unknown"
            parse_meta.setdefault("parser_language", parser_language)
            parse_meta.setdefault("parser_available", parser_language != "regex_fallback")
            parse_meta.setdefault("has_error", False)
            parse_meta.setdefault("error_nodes", 0)
            parse_meta.setdefault("range_guided_by_tree", False)
            parse_meta.setdefault("interface_ranges", [])
            parse_meta.setdefault("implementation_ranges", [])
        return payload

    rel_path = os.path.relpath(file_path, root)
    cached_payload = None
    signature = None

    if parse_cache:
        file_sig = file_signature(file_path)
        if file_sig is not None:
            signature = f"{file_sig}|schema:{_PARSE_CACHE_VERSION}"
        cached_payload = load_parse_cache(parse_cache_root, rel_path, signature)

    if cached_payload:
        return normalize_cached_payload(cached_payload)

    (
        file_functions,
        file_calls,
        file_types,
        file_namespaces,
        file_fields,
        file_relations,
        file_def,
        file_uses,
        parse_meta,
    ) = parse_delphi_file(file_path, root)

    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "types": [asdict(item) for item in file_types],
        "namespaces": [asdict(item) for item in file_namespaces],
        "fields": [asdict(item) for item in file_fields],
        "relations": [asdict(item) for item in file_relations],
        "file_def": asdict(file_def),
        "uses_units": file_uses,
        "parse_meta": parse_meta,
    }

    if parse_cache and signature is not None:
        write_parse_cache(parse_cache_root, rel_path, signature, payload)

    return normalize_cached_payload(payload)


async def build_call_graph(
    root: str,
    code_writer: Optional["LanguageCodeWriter"],
    qdrant_writer: Optional[QdrantWriter],
    embedder: Optional[CodeEmbedder],
    batch_size: int,
    qdrant_batch_size: int,
    cache_dir: Optional[str],
    keep_cache: bool,
    parse_cache: bool,
    neo4j_batch_size: int,
    neo4j_calls_batch_size: int,
    neo4j_state_path: Optional[str],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    call_stats_path: Optional[str],
    unresolved_calls_path: Optional[str],
    parse_errors_path: Optional[str],
    parse_run_id: str,
    commit_sha: str,
    verbose: bool,
    incremental: bool = False,
    changed_files: Optional[Iterable[str]] = None,
    deleted_files: Optional[Iterable[str]] = None,
    commit_sha_before: str = "",
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "delphi_analyzer", project_root=root)
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)

    all_scanned_files = _scan_delphi_files(root)
    all_rel_paths = [os.path.relpath(path, root).replace("\\", "/") for path in all_scanned_files]
    rel_to_abs = {os.path.relpath(path, root).replace("\\", "/"): path for path in all_scanned_files}
    changed_set = {item.replace("\\", "/") for item in (changed_files or []) if item}
    deleted_set = {item.replace("\\", "/") for item in (deleted_files or []) if item}
    selected_rel_paths: set[str]
    unit_name_by_file_all, uses_by_file_all = _collect_unit_and_uses_index(all_scanned_files, root)
    resolved_uses_by_file_all = _resolve_uses_by_file(
        uses_by_file=uses_by_file_all,
        unit_name_by_file=unit_name_by_file_all,
        all_scanned_files=all_scanned_files,
        root=root,
    )
    if incremental:
        changed_existing = {path for path in changed_set if path in rel_to_abs}
        impacted_by_uses = _expand_impacted_files_by_uses(changed_existing, resolved_uses_by_file_all)
        selected_rel_paths = changed_existing | impacted_by_uses
        all_files = [rel_to_abs[path] for path in all_rel_paths if path in selected_rel_paths]
    else:
        selected_rel_paths = set(all_rel_paths)
        all_files = all_scanned_files
    if verbose:
        if incremental:
            print(
                "[scan] incremental before=%s after=%s changed=%d deleted=%d selected=%d/%d impacted_by_uses=%d"
                % (
                    commit_sha_before or "unknown",
                    commit_sha or "unknown",
                    len(changed_set),
                    len(deleted_set),
                    len(all_files),
                    len(all_scanned_files),
                    max(len(selected_rel_paths) - len({path for path in changed_set if path in rel_to_abs}), 0),
                )
            )
        print(f"[scan] Found {len(all_files)} Delphi files under {root}")
    total_files = len(all_files)

    cleanup_targets = sorted(changed_set | deleted_set)
    if incremental and cleanup_targets:
        if code_writer:
            await cleanup_neo4j_for_files(
                driver=code_writer.driver,
                database=code_writer.database,
                project_id=project_id,
                file_paths=cleanup_targets,
                verbose=verbose,
            )
        if qdrant_writer:
            cleanup_qdrant_with_writer(
                writer=qdrant_writer,
                project_id=project_id,
                file_paths=cleanup_targets,
                verbose=verbose,
            )

    def iter_payloads(log_parse: bool = False) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(all_files, start=1):
            if log_parse and verbose and (index == 1 or index % 50 == 0 or index == total_files):
                print(f"[parse] {index}/{total_files}: {file_path}")
            yield _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)

    parse_error_file_count = 0
    parse_error_node_total = 0
    parse_error_examples: List[str] = []
    parse_error_details: List[Dict[str, Any]] = []
    parser_available_file_count = 0
    parser_fallback_file_count = 0
    parser_language_counts: Dict[str, int] = {}

    function_defs: List[FunctionDef] = []
    calls_all: List[CallEdge] = []
    uses_by_file: Dict[str, List[str]] = {}
    all_payloads: List[Dict[str, Any]] = []
    expected_points = 0

    for payload in iter_payloads(log_parse=True):
        all_payloads.append(payload)
        parse_meta = payload.get("parse_meta") or {}
        parser_available = bool(parse_meta.get("parser_available"))
        if parser_available:
            parser_available_file_count += 1
        else:
            parser_fallback_file_count += 1
        parser_language = str(parse_meta.get("parser_language") or "unknown")
        parser_language_counts[parser_language] = parser_language_counts.get(parser_language, 0) + 1
        has_error = bool(parse_meta.get("has_error"))
        error_nodes = int(parse_meta.get("error_nodes") or 0)
        if has_error or error_nodes > 0:
            parse_error_file_count += 1
            parse_error_node_total += error_nodes
            file_path = payload.get("file_def", {}).get("file_path") or ""
            if len(parse_error_examples) < 10:
                parse_error_examples.append(file_path)
            parse_error_details.append(
                {
                    "file_path": file_path,
                    "parser_language": parser_language,
                    "parser_available": parser_available,
                    "has_error": has_error,
                    "error_nodes": error_nodes,
                }
            )

        uses_by_file[payload["file_def"]["file_path"]] = list(payload.get("uses_units") or [])

        for item in payload.get("functions", []):
            function_defs.append(FunctionDef(**item))
            expected_points += 1
        for item in payload.get("calls", []):
            calls_all.append(CallEdge(**item))

    if verbose:
        if parser_fallback_file_count:
            print(
                "[parse] tree-sitter unavailable for %d/%d files; used regex fallback"
                % (parser_fallback_file_count, total_files)
            )
        if parse_error_file_count:
            print(
                "[parse] tree-sitter reported errors in %d/%d files (%d ERROR nodes)"
                % (parse_error_file_count, parser_available_file_count, parse_error_node_total)
            )
            for path in parse_error_examples:
                print(f"  [parse][sample-error] {path}")
        else:
            if parser_available_file_count:
                print(
                    "[parse] tree-sitter parse status: no error nodes detected in %d/%d parsed files"
                    % (parser_available_file_count, total_files)
                )
            else:
                print("[parse] tree-sitter parse status: parser unavailable; regex fallback only")

    if parse_errors_path:
        os.makedirs(os.path.dirname(os.path.abspath(parse_errors_path)) or ".", exist_ok=True)
        report = {
            "parse_run_id": parse_run_id,
            "commit_sha": commit_sha,
            "root": root,
            "total_files": total_files,
            "parser_available_file_count": parser_available_file_count,
            "parser_fallback_file_count": parser_fallback_file_count,
            "parser_languages": parser_language_counts,
            "error_file_count": parse_error_file_count,
            "error_node_total": parse_error_node_total,
            "files": parse_error_details,
        }
        with open(parse_errors_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=True, indent=2)
        if verbose:
            print(f"[parse] wrote parse error report: {parse_errors_path}")

    resolved_uses_by_file: Dict[str, List[str]] = {
        file_path: list(resolved_uses_by_file_all.get(file_path, []))
        for file_path in uses_by_file.keys()
    }

    closure_cache: Dict[str, set[str]] = {}

    def uses_closure(file_path: str, stack: Optional[set[str]] = None) -> set[str]:
        if file_path in closure_cache:
            return closure_cache[file_path]
        if stack is None:
            stack = set()
        if file_path in stack:
            return set()
        stack.add(file_path)
        result: set[str] = set()
        for dep in resolved_uses_by_file_all.get(file_path, []):
            result.add(dep)
            result.update(uses_closure(dep, stack))
        stack.remove(file_path)
        closure_cache[file_path] = result
        return result

    uses_closure_by_file: Dict[str, set[str]] = {fp: uses_closure(fp) for fp in uses_by_file.keys()}

    _resolve_calls(function_defs, calls_all, uses_closure_by_file)

    call_stats_total = len(calls_all)
    call_stats_resolved = 0
    call_stats_by_file: Dict[str, Tuple[int, int]] = {}
    unresolved_rows: List[Dict[str, Any]] = []

    calls_by_key = {
        (
            c.caller_id,
            c.caller_file,
            c.call_line,
            c.callee_raw,
            c.callee_name,
            c.call_arity,
        ): c
        for c in calls_all
    }

    if code_writer:
        if verbose:
            print("[neo4j] Collecting nodes and relations for batch write...")

        all_projects: List[Dict[str, Any]] = [
            {
                "id": project_id,
                "project_id": project_id,
                "project_name": project_name,
                "root": root,
                "repo": repo,
                "language": language,
            }
        ]
        all_files_nodes: List[Dict[str, Any]] = []
        all_namespaces: List[Dict[str, Any]] = []
        all_types: List[Dict[str, Any]] = []
        all_functions: List[Dict[str, Any]] = []
        all_fields: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        all_calls: List[Dict[str, Any]] = []

        allowed_rel_types = {
            "CONTAINS",
            "DECLARES",
            "EXTENDS",
            "USES_TYPE",
            "POINTER_TO",
            "DEPENDS_ON",
        }

        for payload in all_payloads:
            file_def = payload["file_def"]
            file_id = file_def["file_path"]

            all_files_nodes.append(
                {
                    "id": file_id,
                    "path": file_id,
                    "file_path": file_id,
                    "start_line": file_def["start_line"],
                    "end_line": file_def["end_line"],
                    "code": file_def["code"],
                    "comment": file_def.get("comment", ""),
                    "summary": file_def.get("summary", ""),
                    "note": file_def.get("note", ""),
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                }
            )

            all_relations.append(
                {
                    "source_id": project_id,
                    "target_id": file_id,
                    "rel_type": "CONTAINS",
                    "properties": {},
                }
            )

            for dep_file in resolved_uses_by_file.get(file_id, []):
                all_relations.append(
                    {
                        "source_id": file_id,
                        "target_id": dep_file,
                        "rel_type": "DEPENDS_ON",
                        "properties": {"kind": "uses"},
                    }
                )

            for ns in payload.get("namespaces", []):
                all_namespaces.append(
                    {
                        "id": ns["symbol_id"],
                        "name": ns["name"],
                        "qualified_name": ns["qualified_name"],
                        "file_path": ns["file_path"],
                        "start_line": ns["start_line"],
                        "end_line": ns["end_line"],
                        "code": ns.get("code", ""),
                        "comment": ns.get("comment", ""),
                        "summary": ns.get("summary", ""),
                        "note": ns.get("note", ""),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {
                        "source_id": file_id,
                        "target_id": ns["symbol_id"],
                        "rel_type": "CONTAINS",
                        "properties": {},
                    }
                )

            for type_def in payload.get("types", []):
                all_types.append(
                    {
                        "id": type_def["symbol_id"],
                        "name": type_def["name"],
                        "qualified_name": type_def["qualified_name"],
                        "kind": type_def["kind"],
                        "file_path": type_def["file_path"],
                        "start_line": type_def["start_line"],
                        "end_line": type_def["end_line"],
                        "code": type_def.get("code", ""),
                        "comment": type_def.get("comment", ""),
                        "summary": type_def.get("summary", ""),
                        "note": type_def.get("note", ""),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {
                        "source_id": file_id,
                        "target_id": type_def["symbol_id"],
                        "rel_type": "CONTAINS",
                        "properties": {},
                    }
                )

            for func in payload.get("functions", []):
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
                        "code": func.get("code", ""),
                        "comment": func.get("comment", ""),
                        "summary": func.get("summary", ""),
                        "note": func.get("note", ""),
                        "exported": False,
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {
                        "source_id": file_id,
                        "target_id": func["symbol_id"],
                        "rel_type": "CONTAINS",
                        "properties": {},
                    }
                )

            for field in payload.get("fields", []):
                all_fields.append(
                    {
                        "id": field["symbol_id"],
                        "name": field["name"],
                        "qualified_name": field["qualified_name"],
                        "scope_name": field["scope_name"],
                        "type_signature": field["type_signature"],
                        "file_path": field["file_path"],
                        "start_line": field["start_line"],
                        "end_line": field["end_line"],
                        "code": field["code"],
                    }
                )
                all_relations.append(
                    {
                        "source_id": file_id,
                        "target_id": field["symbol_id"],
                        "rel_type": "CONTAINS",
                        "properties": {},
                    }
                )

            for rel in payload.get("relations", []):
                if rel.get("rel_type") not in allowed_rel_types:
                    continue
                all_relations.append(
                    {
                        "source_id": rel["source_id"],
                        "target_id": rel["target_id"],
                        "rel_type": rel["rel_type"],
                        "properties": rel.get("properties") or {},
                    }
                )

            for call in payload.get("calls", []):
                key = (
                    call["caller_id"],
                    call["caller_file"],
                    call["call_line"],
                    call["callee_raw"],
                    call["callee_name"],
                    call["call_arity"],
                )
                resolved = calls_by_key.get(key)
                callee_id = resolved.callee_id if resolved is not None else None
                total, resolved_cnt = call_stats_by_file.get(call["caller_file"], (0, 0))
                if callee_id:
                    call_stats_resolved += 1
                    call_stats_by_file[call["caller_file"]] = (total + 1, resolved_cnt + 1)
                    all_calls.append({"caller_id": call["caller_id"], "callee_id": callee_id})
                else:
                    call_stats_by_file[call["caller_file"]] = (total + 1, resolved_cnt)
                    unresolved_rows.append(
                        {
                            "caller_id": call["caller_id"],
                            "caller_scope": call.get("caller_scope") or "",
                            "file_path": call["caller_file"],
                            "line": int(call.get("call_line") or 0),
                            "callee_name": call.get("callee_raw") or call.get("callee_name") or "",
                            "call_arity": int(call.get("call_arity") or 0),
                            "parse_run_id": parse_run_id,
                            "commit_sha": commit_sha,
                        }
                    )

        state = load_state(neo4j_state_path) if neo4j_state_path else None

        def state_writer(updated_state: Dict[str, int]) -> None:
            if neo4j_state_path:
                write_state(neo4j_state_path, updated_state)

        await code_writer.write_all(
            projects=all_projects,
            namespaces=all_namespaces or None,
            files=all_files_nodes or None,
            types=all_types or None,
            functions=all_functions or None,
            fields=all_fields or None,
            relations=all_relations or None,
            state=state,
            state_writer=state_writer,
            use_full_writers=True,
            files_variant="default",
        )

        if all_calls:
            original_batch_size = code_writer.batch_size
            code_writer.batch_size = max(1, neo4j_calls_batch_size)
            try:
                await code_writer.write_calls_with_site(
                    [
                        {
                            "caller_id": row["caller_id"],
                            "callee_id": row["callee_id"],
                            "site_id": f"{parse_run_id}:{_stable_point_id(row['caller_id'] + '->' + row['callee_id'])}",
                            "props": {
                                "parse_run_id": parse_run_id,
                                "commit_sha": commit_sha,
                            },
                        }
                        for row in all_calls
                    ],
                    state=state,
                    state_writer=state_writer,
                )
            finally:
                code_writer.batch_size = original_batch_size

        if verbose:
            unresolved = call_stats_total - call_stats_resolved
            ratio = (call_stats_resolved / call_stats_total) if call_stats_total else 0.0
            print(
                "[calls] resolved %d / %d (%.1f%%), unresolved %d"
                % (call_stats_resolved, call_stats_total, ratio * 100, unresolved)
            )

    else:
        # Dry parse mode without graph writer
        for call in calls_all:
            total, resolved_cnt = call_stats_by_file.get(call.caller_file, (0, 0))
            if call.callee_id:
                call_stats_resolved += 1
                call_stats_by_file[call.caller_file] = (total + 1, resolved_cnt + 1)
            else:
                call_stats_by_file[call.caller_file] = (total + 1, resolved_cnt)
                unresolved_rows.append(
                    {
                        "caller_id": call.caller_id,
                        "caller_scope": call.caller_scope or "",
                        "file_path": call.caller_file,
                        "line": int(call.call_line or 0),
                        "callee_name": call.callee_raw or call.callee_name,
                        "call_arity": int(call.call_arity or 0),
                        "parse_run_id": parse_run_id,
                        "commit_sha": commit_sha,
                    }
                )

    if unresolved_calls_path:
        os.makedirs(os.path.dirname(os.path.abspath(unresolved_calls_path)) or ".", exist_ok=True)
        with open(unresolved_calls_path, "w", encoding="utf-8") as handle:
            for row in unresolved_rows:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
        if verbose:
            print(f"[calls] wrote unresolved calls: {unresolved_calls_path}")

    if call_stats_path:
        payload = {
            "parse_run_id": parse_run_id,
            "commit_sha": commit_sha,
            "total_calls": call_stats_total,
            "resolved_calls": call_stats_resolved,
            "unresolved_calls": call_stats_total - call_stats_resolved,
            "resolved_ratio": (call_stats_resolved / call_stats_total) if call_stats_total else 0.0,
            "by_file": [
                {
                    "file_path": fp,
                    "total": total,
                    "resolved": resolved,
                    "unresolved": total - resolved,
                }
                for fp, (total, resolved) in sorted(call_stats_by_file.items())
            ],
        }
        os.makedirs(os.path.dirname(os.path.abspath(call_stats_path)) or ".", exist_ok=True)
        with open(call_stats_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
        if verbose:
            print(f"[calls] wrote call stats: {call_stats_path}")

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
                for payload in all_payloads:
                    for func in payload.get("functions", []):
                        batch_funcs.append(func)
                        if len(batch_funcs) < batch_size:
                            continue
                        batch_index += 1
                        if verbose:
                            print(f"[embed] batch {batch_index} / {total_batches}")
                        texts = [item.get("note") or item.get("code") or "" for item in batch_funcs]
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
                                    "code": func_item.get("code", ""),
                                    "comment": func_item.get("comment", ""),
                                    "summary": func_item.get("summary", ""),
                                    "note": func_item.get("note", ""),
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
                    texts = [item.get("note") or item.get("code") or "" for item in batch_funcs]
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
                                "code": func_item.get("code", ""),
                                "comment": func_item.get("comment", ""),
                                "summary": func_item.get("summary", ""),
                                "note": func_item.get("note", ""),
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

    _sr_fn = sum(len(p.get("functions") or []) for p in all_payloads)
    _sr_cls = sum(len(p.get("classes") or []) for p in all_payloads)
    print(f"[SCAN_RESULT] parser={language} files={len(all_payloads)} functions={_sr_fn} classes={_sr_cls}", flush=True)
    if verbose:
        elapsed = time.time() - start_time
        print(f"[done] Total time: {elapsed:.2f}s")


def _detect_git_commit_sha(root: str) -> str:
    try:
        sha = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return sha or "unknown"
    except Exception:
        return "unknown"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delphi call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing Delphi sources")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "delphi_functions"))
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-calls-batch-size", type=int, default=100)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.set_defaults(enable_message_scan=True)
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true", help="Enable message scan and sync (default)")
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false", help="Disable message scan and sync")
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--keep-cache", action="store_true")
    parser.add_argument("--disable-parse-cache", action="store_true")
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Ignore local caches for this run (parse cache, Neo4j/Qdrant resume state).",
    )
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--call-stats-path", help="Write call resolution stats JSON")
    parser.add_argument("--unresolved-calls-path", help="Write unresolved calls as JSONL")
    parser.add_argument("--parse-errors-path", help="Write tree-sitter parse error summary JSON")
    parser.add_argument("--parse-run-id", default=os.environ.get("PARSE_RUN_ID"))
    parser.add_argument("--commit-sha", default=os.environ.get("GIT_COMMIT_SHA"))
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true", help="Enable incremental ingestion mode")
    parser.add_argument(
        "--changed-files-manifest",
        help="JSON/TXT manifest of changed+impacted file paths (relative to --root)",
    )
    parser.add_argument(
        "--deleted-files-manifest",
        help="JSON/TXT manifest of deleted file paths (relative to --root)",
    )
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
            {"uri": args.neo4j_uri, "user": args.neo4j_user, "password": args.neo4j_password, "database": args.neo4j_db},
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
    effective_cache_dir = args.cache_dir
    if args.ignore_cache:
        run_cache_root = safe_cache_root(effective_cache_dir, "delphi_analyzer", project_root=args.root)
        effective_cache_dir = os.path.join(
            run_cache_root,
            "ignore_runs",
            f"run_{int(time.time() * 1000)}",
        )
        os.makedirs(effective_cache_dir, exist_ok=True)
        parse_cache = False
        args.disable_neo4j_resume = True
        args.keep_cache = False
        if args.verbose:
            print(
                "[cache] ignore-cache enabled; using isolated cache dir: %s"
                % effective_cache_dir
            )
    changed_manifest_files: List[str] = []
    deleted_manifest_files: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_manifest_files = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        if args.deleted_files_manifest:
            deleted_manifest_files = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
        if args.verbose:
            print(
                "[diff] incremental manifests changed=%d deleted=%d"
                % (len(changed_manifest_files), len(deleted_manifest_files))
            )
    neo4j_state_path = None
    if not args.disable_neo4j_resume and not args.incremental:
        cache_root = safe_cache_root(effective_cache_dir, "delphi_analyzer", project_root=args.root)
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    elif args.incremental and args.verbose:
        print("[state] incremental mode disables neo4j resume state")

    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "delphi"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""
    parse_run_id = args.parse_run_id or f"parse-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    commit_sha = args.commit_sha_after or args.commit_sha or _detect_git_commit_sha(args.root)
    commit_sha_before = args.commit_sha_before or ""
    message_qdrant_collection = (
        args.message_qdrant_collection
        or default_message_collection_name(args.qdrant_collection)
    )

    if driver:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            await write_cloc_stats_to_neo4j(
                driver=driver,
                database=args.neo4j_db,
                project_id=project_id,
                project_name=project_name,
                root=args.root,
                repo=repo,
                language=language,
                stats=cloc_stats,
            )
            if args.verbose:
                print("[cloc] Stats stored in Neo4j")
        elif args.verbose:
            print("[cloc] Skipped (cloc not available or failed)")

    try:
        if args.dry_run:
            files = _scan_delphi_files(args.root)
            if args.incremental and changed_manifest_files:
                manifest_set = set(changed_manifest_files)
                files = [
                    file_path
                    for file_path in files
                    if os.path.relpath(file_path, args.root).replace("\\", "/") in manifest_set
                ]
                print(
                    "Dry run (incremental): %d Delphi files selected (manifest=%d)"
                    % (len(files), len(changed_manifest_files))
                )
            else:
                print(f"Dry run: {len(files)} Delphi files found")
            return 0

        await build_call_graph(
            root=args.root,
            code_writer=code_writer,
            qdrant_writer=qdrant_writer,
            embedder=embedder,
            batch_size=args.batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            cache_dir=effective_cache_dir,
            keep_cache=args.keep_cache,
            parse_cache=parse_cache,
            neo4j_batch_size=args.neo4j_batch_size,
            neo4j_calls_batch_size=args.neo4j_calls_batch_size,
            neo4j_state_path=neo4j_state_path,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            call_stats_path=args.call_stats_path,
            unresolved_calls_path=args.unresolved_calls_path,
            parse_errors_path=args.parse_errors_path,
            parse_run_id=parse_run_id,
            commit_sha=commit_sha,
            verbose=args.verbose,
            incremental=args.incremental,
            changed_files=changed_manifest_files,
            deleted_files=deleted_manifest_files,
            commit_sha_before=commit_sha_before,
        )
        if args.enable_message_scan:
            message_summary = await run_message_scan_pipeline(
                root=args.root,
                parser="delphi",
                project_id=project_id,
                project_name=project_name,
                language=language,
                repo=repo,
                build_system=build_system,
                incremental=args.incremental,
                changed_files=changed_manifest_files,
                deleted_files=deleted_manifest_files,
                driver=driver,
                neo4j_database=args.neo4j_db,
                qdrant_url=args.qdrant_url,
                qdrant_collection=message_qdrant_collection if args.qdrant_url else None,
                qdrant_vector_size=embedder.vector_size if embedder else 1024,
                embed_texts=embedder.embed if embedder else None,
                output_dir=args.message_output_dir,
                cache_dir=effective_cache_dir,
                commit_sha_before=commit_sha_before,
                commit_sha_after=commit_sha,
                qdrant_batch_size=args.qdrant_batch_size,
                qdrant_timeout=args.qdrant_timeout,
                qdrant_retries=args.qdrant_retries,
                qdrant_retry_sleep=args.qdrant_retry_sleep,
                verbose=args.verbose,
            )
            print(
                "[message] parser=%s count=%s neo4j=%s qdrant=%s collection=%s artifact=%s"
                % (
                    message_summary.get("parser"),
                    message_summary.get("message_count"),
                    message_summary.get("neo4j_upserted"),
                    message_summary.get("qdrant_upserted"),
                    message_summary.get("qdrant_collection"),
                    message_summary.get("artifact_path"),
                )
            )
    finally:
        if driver:
            close_result = driver.close()
            if hasattr(close_result, "__await__"):
                await close_result

    return 0


if __name__ == "__main__":
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--root", default=".")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    _config_path = _pre_args.config or os.path.join(
        _pre_args.root, ".cortext-harness", "config", "dev.json"
    )
    load_harness_config(_config_path)
    raise SystemExit(asyncio.run(main()))

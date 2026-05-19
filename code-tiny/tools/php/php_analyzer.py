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
    jsx_tags: List[str] = field(default_factory=list)
    jsx_components: List[str] = field(default_factory=list)


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
    if node.type in {"identifier", "name", "qualified_name", "namespace_name"}:
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
    cleaned = cleaned.replace("this->", "")
    cleaned = cleaned.replace("self::", "")
    cleaned = cleaned.replace("static::", "")
    cleaned = cleaned.replace("?->", "->")
    cleaned = cleaned.strip()
    cleaned = cleaned.lstrip("$")
    if "->" in cleaned:
        cleaned = cleaned.split("->")[-1]
    if "::" in cleaned:
        cleaned = cleaned.split("::")[-1]
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


def _type_id(qualified: str) -> str:
    return qualified


def _namespace_id(name: str) -> str:
    return f"namespace::{name}"


def _anonymous_name(prefix: str, node) -> str:
    return f"Anonymous{prefix}@{node.start_point[0] + 1}:{node.start_point[1] + 1}"


def _get_php_parser() -> Parser:
    if ts_get_parser is not None:
        for name in ("php", "php_only"):
            try:
                return ts_get_parser(name)
            except Exception:
                continue
    try:
        import tree_sitter_php as ts_php
    except Exception as exc:
        raise RuntimeError(
            "PHP parser unavailable. Install 'tree-sitter-php' or 'tree-sitter-languages'."
        ) from exc
    language_obj = None
    for attr in ("language_php", "language", "LANGUAGE"):
        if hasattr(ts_php, attr):
            language_obj = getattr(ts_php, attr)
            break
    if language_obj is None:
        raise RuntimeError(
            "PHP parser unavailable. Install 'tree-sitter-php' or 'tree-sitter-languages'."
        )
    language = language_obj() if callable(language_obj) else language_obj
    if not isinstance(language, Language):
        language = Language(language)
    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    return parser


def _parse_file(path: str) -> Tuple[Any, bytes]:
    parser = _get_php_parser()
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    tree = parser.parse(source_bytes)
    return tree, source_bytes


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
    for node in _find_nodes_by_type(func_node, "function_call_expression"):
        yield node
    for node in _find_nodes_by_type(func_node, "method_call_expression"):
        yield node
    for node in _find_nodes_by_type(func_node, "scoped_call_expression"):
        yield node
    for node in _find_nodes_by_type(func_node, "call_expression"):
        yield node
    for node in _find_nodes_by_type(func_node, "object_creation_expression"):
        yield node


def _extract_call_name(call_node, source_bytes: bytes) -> Optional[str]:
    for field in ("function", "name", "callable"):
        expr = call_node.child_by_field_name(field)
        if expr is not None:
            return _normalize_call_name(_node_text(expr, source_bytes).strip())
    if call_node.type == "object_creation_expression":
        name = _first_identifier(call_node, source_bytes)
        if name:
            return _normalize_call_name(name)
    text = _node_text(call_node, source_bytes).strip()
    text = text.split("(", 1)[0].strip()
    return _normalize_call_name(text)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _collect_imports(tree, source_bytes: bytes) -> List[str]:
    imports: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "namespace_use_declaration"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            imports.append(text)
    for node in _find_nodes_by_type(tree.root_node, "namespace_use_clause"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            imports.append(text)
    for node_type in (
        "include_expression",
        "include_once_expression",
        "require_expression",
        "require_once_expression",
    ):
        for node in _find_nodes_by_type(tree.root_node, node_type):
            text = _normalize_ws(_node_text(node, source_bytes))
            if text:
                imports.append(text)
    return imports


def _collect_exports(tree, source_bytes: bytes) -> List[str]:
    return []


def _collect_jsx_tags(tree, source_bytes: bytes) -> Tuple[List[str], List[str]]:
    return [], []


_NAMESPACE_NODE_TYPES: Set[str] = {"namespace_definition", "namespace_declaration"}

_TYPE_NODE_KINDS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
}

_FUNCTION_NODE_KINDS = {
    "function_definition": "function",
    "method_declaration": "method",
}

_ANON_FUNCTION_NODE_TYPES = {
    "anonymous_function",
    "anonymous_function_creation_expression",
    "arrow_function",
}


def _record_function(
    node,
    source_bytes: bytes,
    rel_path: str,
    namespace_stack: List[str],
    type_stack: List[str],
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
    if kind == "method" and name == "__construct":
        kind = "constructor"
    snippet, start_line, end_line = _node_snippet(node, source_bytes)
    comment = _extract_leading_comment(node, source_bytes)
    summary = comment
    note = _build_note(snippet, comment, summary)
    scope_stack = namespace_stack + type_stack
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
    if type_stack:
        relations.append(
            RelationEdge(
                source_id=_type_id("::".join(namespace_stack + type_stack)),
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
    type_stack: List[str],
    namespaces: List[NamespaceDef],
    types: List[TypeDef],
    functions: List[FunctionDef],
    relations: List[RelationEdge],
    calls: List[CallEdge],
    namespace_registry: Dict[str, NamespaceDef],
    type_registry: Dict[str, TypeDef],
    exported_context: bool,
    exported_names: Set[str],
    skip_function_ranges: Set[Tuple[int, int]],
) -> None:
    if node.type in _ANON_FUNCTION_NODE_TYPES and (node.start_byte, node.end_byte) in skip_function_ranges:
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
                namespace_stack,
                type_stack,
                namespaces,
                types,
                functions,
                relations,
                calls,
                namespace_registry,
                type_registry,
                exported_context,
                exported_names,
                skip_function_ranges,
            )
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
                namespaces,
                types,
                functions,
                relations,
                calls,
                namespace_registry,
                type_registry,
                False,
                exported_names,
                skip_function_ranges,
            )
        return

    if node.type in _TYPE_NODE_KINDS:
        kind = _TYPE_NODE_KINDS[node.type]
        name = _extract_name_field(node, source_bytes)
        if not name:
            name = _anonymous_name(kind.capitalize(), node)
            kind = f"anonymous_{kind}"
        qualified = "::".join(namespace_stack + type_stack + [name]) if (namespace_stack or type_stack) else name
        type_id = _type_id(qualified)
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
                exported=exported_context,
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
            parent_type = _type_id("::".join(namespace_stack + type_stack))
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
        for child in node.children:
            _walk_tree(
                child,
                source_bytes,
                rel_path,
                namespace_stack,
                type_stack + [name],
                namespaces,
                types,
                functions,
                relations,
                calls,
                namespace_registry,
                type_registry,
                False,
                exported_names,
                skip_function_ranges,
            )
        return

    if node.type in _FUNCTION_NODE_KINDS:
        _record_function(
            node,
            source_bytes,
            rel_path,
            namespace_stack,
            type_stack,
            functions,
            relations,
            calls,
            exported=exported_context,
        )
        return

    if node.type in _ANON_FUNCTION_NODE_TYPES:
        kind_override = "arrow_function" if node.type == "arrow_function" else "anonymous_function"
        _record_function(
            node,
            source_bytes,
            rel_path,
            namespace_stack,
            type_stack,
            functions,
            relations,
            calls,
            kind_override=kind_override,
            calls_root=node,
            parameters_node=node,
            exported=exported_context,
        )
        return

    if node.type in {"assignment_expression", "simple_assignment_expression"}:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if right is not None and right.type in _ANON_FUNCTION_NODE_TYPES:
            name = _node_text(left, source_bytes).strip() if left is not None else None
            if name:
                name = name.lstrip("$")
            _record_function(
                right,
                source_bytes,
                rel_path,
                namespace_stack,
                type_stack,
                functions,
                relations,
                calls,
                name_override=name,
                kind_override="function_variable",
                calls_root=right,
                parameters_node=right,
                exported=exported_context,
            )
            skip_function_ranges.add((right.start_byte, right.end_byte))

    for child in node.children:
        _walk_tree(
            child,
            source_bytes,
            rel_path,
            namespace_stack,
            type_stack,
            namespaces,
            types,
            functions,
            relations,
            calls,
            namespace_registry,
            type_registry,
            exported_context,
            exported_names,
            skip_function_ranges,
        )


def parse_php_file(path: str, root: str) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[TypeDef],
    List[NamespaceDef],
    List[RelationEdge],
    FileDef,
]:
    rel_path = os.path.relpath(path, root)
    tree, source_bytes = _parse_file(path)
    snippet = source_bytes.decode("utf-8", errors="ignore")
    start_line = 1
    end_line = snippet.count("\n") + 1
    file_comment = _extract_file_comment(tree, source_bytes)
    file_summary = file_comment
    file_note = _build_note(snippet, file_comment, file_summary)
    imports = _collect_imports(tree, source_bytes)
    exports = _collect_exports(tree, source_bytes)
    jsx_tags, jsx_components = _collect_jsx_tags(tree, source_bytes)
    file_def = FileDef(
        file_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        code=snippet,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
        imports=imports,
        exports=exports,
        jsx_tags=jsx_tags,
        jsx_components=jsx_components,
    )
    namespaces: List[NamespaceDef] = []
    types: List[TypeDef] = []
    functions: List[FunctionDef] = []
    relations: List[RelationEdge] = []
    calls: List[CallEdge] = []
    namespace_registry: Dict[str, NamespaceDef] = {}
    type_registry: Dict[str, TypeDef] = {}
    exported_names: Set[str] = set()
    skip_function_ranges: Set[Tuple[int, int]] = set()
    _walk_tree(
        tree.root_node,
        source_bytes,
        rel_path,
        [],
        [],
        namespaces,
        types,
        functions,
        relations,
        calls,
        namespace_registry,
        type_registry,
        False,
        exported_names,
        skip_function_ranges,
    )
    if exported_names:
        for func in functions:
            if func.exported:
                continue
            if func.scope_name is None and func.name in exported_names:
                func.exported = True
        for type_def in types:
            if type_def.exported:
                continue
            if "::" not in type_def.qualified_name and type_def.name in exported_names:
                type_def.exported = True
    return functions, calls, types, namespaces, relations, file_def


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


def _should_ignore_directory(dir_name: str, dir_path: str) -> bool:
    """
    Check if a directory should be ignored during PHP project scanning.

    Ignores:
    - Composer: vendor/, composer.lock
    - Frameworks: var/, cache/, storage/cache/
    - Build: var/cache/, bootstrap/cache/
    - Testing: vendor/bin/, phpunit/, tests/output/
    """
    ignore_patterns = {
        # Composer
        "vendor",

        # Build outputs & cache
        "var", "cache", "tmp", "temp",

        # Framework caches (Laravel, Symfony, etc.)
        "storage/cache", "bootstrap/cache",

        # Testing
        "tests/output", ".phpunit.result.cache", "phpstan-cache", "psalm-cache",

        # IDE
        ".idea", ".vscode",

        # Version control
        ".git", ".svn", ".hg",

        # Node (mixed projects)
        "node_modules", "dist", "build",

        # Logs
        "storage/logs", "logs",

        # Environment
        ".env", ".env.local", ".env.*.local",

        # OS specific
        ".DS_Store", "Thumbs.db",
    }

    if dir_name in ignore_patterns:
        return True

    if dir_name.endswith((".swp", ".swo")):
        return True

    return False


def _scan_php_files(root: str) -> List[str]:
    """
    Scan for PHP files, ignoring unnecessary directories.
    """
    files: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Filter out ignored directories in-place
        dirnames[:] = [
            d for d in dirnames
            if not _should_ignore_directory(d, os.path.join(dirpath, d))
        ]

        for name in filenames:
            if name.endswith((".swp", ".swo", ".cache")):
                continue
            if name in (".DS_Store", "Thumbs.db", ".env", ".env.local"):
                continue

            if name.endswith((".php", ".phtml", ".inc", ".php4", ".php5")):
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
        if "jsx_tags" not in item or item["jsx_tags"] is None:
            item["jsx_tags"] = []
        if "jsx_components" not in item or item["jsx_components"] is None:
            item["jsx_components"] = []

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
        types = payload.get("types")
        if isinstance(types, list):
            for item in types:
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
        file_types,
        file_namespaces,
        file_relations,
        file_def,
    ) = parse_php_file(file_path, root)
    payload = {
        "functions": [asdict(item) for item in file_functions],
        "calls": [asdict(item) for item in file_calls],
        "types": [asdict(item) for item in file_types],
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
    incremental: bool = False,
    changed_files: Optional[Iterable[str]] = None,
    deleted_files: Optional[Iterable[str]] = None,
    commit_sha: str = "",
    commit_sha_before: str = "",
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "php_analyzer", project_root=root)
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    all_scanned_files = _scan_php_files(root)
    changed_set = {item.replace("\\", "/") for item in (changed_files or []) if item}
    deleted_set = {item.replace("\\", "/") for item in (deleted_files or []) if item}
    if incremental:
        selected_files = [
            file_path
            for file_path in all_scanned_files
            if os.path.relpath(file_path, root).replace("\\", "/") in changed_set
        ]
    else:
        selected_files = all_scanned_files
    if verbose:
        if incremental:
            print(
                "[scan] incremental before=%s after=%s changed=%d deleted=%d selected=%d/%d"
                % (
                    commit_sha_before or "unknown",
                    commit_sha or "unknown",
                    len(changed_set),
                    len(deleted_set),
                    len(selected_files),
                    len(all_scanned_files),
                )
            )
        print(f"[scan] Found {len(selected_files)} PHP files under {root}")
    total_files = len(selected_files)

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

    def iter_payloads(log_parse: bool) -> Iterable[Dict[str, Any]]:
        for index, file_path in enumerate(selected_files, start=1):
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

        session_args = {"database": code_writer.database} if code_writer.database else {}
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
                    "jsx_tags": file_def.get("jsx_tags") or [],
                    "jsx_components": file_def.get("jsx_components") or [],
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
            for type_def in payload["types"]:
                all_types.append(
                    {
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
                        "exported": type_def.get("exported", False),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    }
                )
                all_relations.append(
                    {"source_id": file_id, "target_id": type_def["symbol_id"], "rel_type": "CONTAINS", "properties": {}}
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
            files_variant="with_jsx",
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
    _sr_fn = len(all_functions) if 'all_functions' in vars() else 0  # noqa: F821
    _sr_cls = len(all_types) if 'all_types' in vars() else 0  # noqa: F821
    print(f"[SCAN_RESULT] parser={language} files={total_files} functions={_sr_fn} classes={_sr_cls}", flush=True)
    if verbose:
        elapsed = time.time() - start_time
        print(f"[done] Total time: {elapsed:.2f}s")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PHP call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing PHP sources")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.environ.get("QDRANT_COLLECTION", "php_functions"),
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", 4000)))
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", 4)))  # for embedding - 4 function 1 turn embedding
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--neo4j-state", default=os.environ.get("NEO4J_STATE_PATH"))
    parser.add_argument("--disable-neo4j-resume", action="store_true")
    parser.add_argument("--qdrant-batch-size", type=int, default=128)  # for qdrant upsert - 128 vectors 1 time upsert
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
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--build_system", dest="build_system")
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
    effective_cache_dir = args.cache_dir
    if args.ignore_cache:
        run_cache_root = safe_cache_root(effective_cache_dir, "php_analyzer", project_root=args.root)
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
        cache_root = safe_cache_root(effective_cache_dir, "php_analyzer", project_root=args.root)
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    elif args.incremental and args.verbose:
        print("[state] incremental mode disables neo4j resume state")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "php"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""
    commit_sha = args.commit_sha_after or ""
    commit_sha_before = args.commit_sha_before or ""
    message_qdrant_collection = (
        args.message_qdrant_collection
        or default_message_collection_name(args.qdrant_collection)
    )
    if code_writer:
        cloc_raw = collect_cloc_stats(args.root)
        if cloc_raw:
            cloc_stats = normalize_cloc_payload(cloc_raw)
            await write_cloc_stats_to_neo4j(
                driver=code_writer.driver,
                database=code_writer.database,
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
            files = _scan_php_files(args.root)
            if args.incremental and changed_manifest_files:
                manifest_set = set(changed_manifest_files)
                files = [
                    file_path
                    for file_path in files
                    if os.path.relpath(file_path, args.root).replace("\\", "/") in manifest_set
                ]
                print(
                    "Dry run (incremental): %d PHP files selected (manifest=%d)"
                    % (len(files), len(changed_manifest_files))
                )
            else:
                print(f"Dry run: {len(files)} PHP files found")
            return 0
        await build_call_graph(
            args.root,
            code_writer=code_writer,
            qdrant_writer=qdrant_writer,
            embedder=embedder,
            batch_size=args.batch_size,
            qdrant_batch_size=args.qdrant_batch_size,
            cache_dir=effective_cache_dir,
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
            incremental=args.incremental,
            changed_files=changed_manifest_files,
            deleted_files=deleted_manifest_files,
            commit_sha=commit_sha,
            commit_sha_before=commit_sha_before,
        )
        if args.enable_message_scan:
            message_summary = await run_message_scan_pipeline(
                root=args.root,
                parser="php",
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

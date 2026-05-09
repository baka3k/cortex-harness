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

from tools.common.analyzer_cache import (
    file_signature,
    load_parse_cache,
    load_state,
    safe_cache_root,
    write_parse_cache,
    write_state,
)
from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files, cleanup_qdrant_with_writer
from tools.common.message_scan import default_message_collection_name, run_message_scan_pipeline
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.writer.language_writer import LanguageCodeWriter

try:
    from tools.common.semantic_inference import SemanticInferenceEngine as _SemanticInferenceEngine
    _semantic_engine: Optional[Any] = _SemanticInferenceEngine()
except Exception:  # pragma: no cover
    _semantic_engine = None

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
    # Semantic fields (populated by SemanticInferenceEngine.enrich_corpus)
    intent: str = ""
    inferred_doc: bool = False
    doc_confidence: float = 0.0
    signals: Dict[str, float] = field(default_factory=dict)
    side_effect: bool = False
    # Entrypoint detection
    is_entrypoint: bool = False
    entrypoint_kind: str = ""


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
    # Structural fields
    base_classes: List[str] = field(default_factory=list)
    self_fields: Dict[str, str] = field(default_factory=dict)


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
    callee_receiver: Optional[str] = None  # receiver chain before method (e.g. "self.repo")


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


def _extract_docstring(node, source_bytes: bytes) -> str:
    """Extract Python docstring from a function or class body node."""
    body = node.child_by_field_name("body")
    if body is None:
        return ""
    for child in body.children:
        if child.type == "expression_statement":
            for sub in child.children:
                if sub.type in ("string", "concatenated_string"):
                    raw = _node_text(sub, source_bytes).strip()
                    for q in ('"""', "'''", '"', "'"):
                        if raw.startswith(q) and raw.endswith(q) and len(raw) > 2 * len(q):
                            return raw[len(q) : -len(q)].strip()
                    return raw
        elif child.is_named:
            break
    return ""


def _extract_function_signature(node, source_bytes: bytes) -> str:
    """Extract just the function signature line (def … :) without the body."""
    full = _node_text(node, source_bytes)
    depth = 0
    for i, ch in enumerate(full):
        if ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1
        elif ch == ":" and depth == 0:
            return full[: i + 1].strip()
    return full.split("\n", 1)[0].strip()


def _build_structured_note(
    signature: str,
    docstring: str,
    comment: str,
    code: str,
    max_body_chars: int = 800,
) -> str:
    """Build embedding note with smart chunking: signature > docstring > truncated body."""
    parts: List[str] = []
    if signature:
        parts.append(f"Signature:\n{signature}")
    if docstring:
        parts.append(f"Docstring:\n{docstring}")
    elif comment:
        parts.append(f"Comment:\n{comment}")
    body = code if len(code) <= max_body_chars else code[:max_body_chars] + "\n# ... (truncated)"
    if body:
        parts.append(f"Code:\n{body}")
    return "\n\n".join(parts)


def _extract_base_classes(class_node, source_bytes: bytes) -> List[str]:
    """Return simple base class names from a class_definition node."""
    bases: List[str] = []
    superclasses = class_node.child_by_field_name("superclasses")
    if superclasses is None:
        return bases
    for child in superclasses.children:
        if child.type in ("identifier", "attribute"):
            raw = _node_text(child, source_bytes).strip()
            if raw and raw not in (",", "(", ")"):
                bases.append(raw.split(".")[-1])
    return bases


def _scan_self_assignment(node, source_bytes: bytes, result: Dict[str, str]) -> None:
    """Recursively extract self.field = ClassName(...) pattern into *result*."""
    if node.type == "expression_statement":
        for child in node.children:
            _scan_self_assignment(child, source_bytes, result)
        return
    if node.type == "assignment":
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left and left.type == "attribute":
            obj = left.child_by_field_name("object")
            attr = left.child_by_field_name("attribute")
            if obj and _node_text(obj, source_bytes) == "self" and attr:
                field_name = _node_text(attr, source_bytes)
                if right and right.type == "call":
                    func = right.child_by_field_name("function")
                    if func:
                        result[field_name] = _node_text(func, source_bytes).split(".")[-1]
    elif node.type == "annotated_assignment":
        # self.field: ClassName = ...
        target = node.child_by_field_name("lhs") or node.child_by_field_name("left")
        ann = node.child_by_field_name("type")
        if target and ann and target.type == "attribute":
            obj = target.child_by_field_name("object")
            attr_node = target.child_by_field_name("attribute")
            if obj and _node_text(obj, source_bytes) == "self" and attr_node:
                field_name = _node_text(attr_node, source_bytes)
                ann_text = _node_text(ann, source_bytes).split("[")[0].split(".")[-1].strip()
                result[field_name] = ann_text


def _extract_self_fields(class_node, source_bytes: bytes) -> Dict[str, str]:
    """Scan __init__ body for self.field = ClassName(...) patterns."""
    self_fields: Dict[str, str] = {}
    body = class_node.child_by_field_name("body")
    if body is None:
        return self_fields
    for child in body.children:
        if child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            if name_node and _node_text(name_node, source_bytes) == "__init__":
                init_body = child.child_by_field_name("body")
                if init_body:
                    for stmt in init_body.children:
                        _scan_self_assignment(stmt, source_bytes, self_fields)
                break
    return self_fields


# Decorator patterns that mark API / task / CLI entrypoints
_ENTRYPOINT_KIND_MAP: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"@(?:app|router|blueprint|api_router)\.(get|post|put|delete|patch|head|options|route|websocket)", re.I), "http_handler"),
    (re.compile(r"@api_view\b",                 re.I), "http_handler"),
    (re.compile(r"@(?:celery\.task|shared_task|app\.task)\b", re.I), "celery_task"),
    (re.compile(r"@(?:on_event|event\.listen(?:er)?|signal\.connect)\b", re.I), "event_handler"),
    (re.compile(r"@(?:click|cli|app)\.command\b", re.I), "cli_command"),
    (re.compile(r"@pytest\.(?:fixture|mark)\b", re.I), "test"),
]


def _detect_entrypoint_kind(decorated_node, source_bytes: bytes) -> Tuple[bool, str]:
    """Detect entrypoint kind from a decorated_definition node."""
    code = _node_text(decorated_node, source_bytes)
    for pattern, kind in _ENTRYPOINT_KIND_MAP:
        if pattern.search(code):
            return True, kind
    return False, ""


@dataclass
class FlowDef:
    flow_id: str
    name: str
    kind: str
    entrypoint_id: str
    step_ids: List[str]


def _build_execution_flows(
    functions: List[Dict[str, Any]],
    resolved_calls: List[Tuple[str, str]],
    max_depth: int = 5,
) -> List[FlowDef]:
    """Build execution flows from entrypoint functions via BFS through the call graph."""
    call_graph: Dict[str, List[str]] = {}
    for caller_id, callee_id in resolved_calls:
        call_graph.setdefault(caller_id, []).append(callee_id)

    flows: List[FlowDef] = []
    for func in functions:
        if not func.get("is_entrypoint"):
            continue
        func_id = func["symbol_id"]
        ep_kind = func.get("entrypoint_kind", "unknown")
        visited: Set[str] = set()
        queue = [(func_id, 0)]
        steps: List[str] = []
        while queue:
            node_id, depth = queue.pop(0)
            if node_id in visited or depth > max_depth:
                continue
            visited.add(node_id)
            steps.append(node_id)
            for callee in call_graph.get(node_id, []):
                if callee not in visited:
                    queue.append((callee, depth + 1))
        if len(steps) > 1:
            flows.append(FlowDef(
                flow_id=f"flow::{func_id}",
                name=func.get("name", "unknown_flow"),
                kind=ep_kind,
                entrypoint_id=func_id,
                step_ids=steps,
            ))
    return flows


def _resolve_import_to_path(import_str: str, module_to_rel_path: Dict[str, str]) -> Optional[str]:
    """Try to resolve an import string to a scanned file rel_path."""
    m = re.match(r"from\s+([\w.]+)\s+import", import_str)
    if m:
        return module_to_rel_path.get(m.group(1))
    m = re.match(r"import\s+([\w.]+)", import_str)
    if m:
        return module_to_rel_path.get(m.group(1))
    return None


def _build_module_index(scanned_files: List[str], root: str) -> Dict[str, str]:
    """Map Python dotted module names to their relative file paths."""
    index: Dict[str, str] = {}
    for file_path in scanned_files:
        rel = os.path.relpath(file_path, root).replace(os.sep, "/")
        module = rel.replace("/", ".")
        if module.endswith(".py"):
            module = module[:-3]
        if module.endswith(".__init__"):
            module = module[:-9]
        index[module] = rel
    return index


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


def _get_python_parser() -> Parser:
    if ts_get_parser is not None:
        try:
            return ts_get_parser("python")
        except Exception:
            pass
    try:
        import tree_sitter_python as ts_python
    except Exception as exc:
        raise RuntimeError(
            "Python parser unavailable. Install 'tree-sitter-python' or 'tree-sitter-languages'."
        ) from exc
    language_obj = None
    for attr in ("language", "LANGUAGE"):
        if hasattr(ts_python, attr):
            language_obj = getattr(ts_python, attr)
            break
    if language_obj is None:
        raise RuntimeError(
            "Python parser unavailable. Install 'tree-sitter-python' or 'tree-sitter-languages'."
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
    parser = _get_python_parser()
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
    for node in _find_nodes_by_type(func_node, "call"):
        yield node


def _extract_call_name(call_node, source_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    """Returns (callee_name, receiver_chain). receiver_chain is the object before the method."""
    expr = call_node.child_by_field_name("function")
    if expr is not None:
        if expr.type == "attribute":
            attr = expr.child_by_field_name("attribute")
            obj = expr.child_by_field_name("object")
            if attr is not None:
                receiver = _node_text(obj, source_bytes).strip() if obj is not None else None
                return _normalize_call_name(_node_text(attr, source_bytes).strip()), receiver
        return _normalize_call_name(_node_text(expr, source_bytes).strip()), None
    text = _node_text(call_node, source_bytes).strip()
    text = text.split("(", 1)[0].strip()
    return _normalize_call_name(text), None


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
    is_entrypoint: bool = False,
    entrypoint_kind: str = "",
) -> None:
    name = name_override or _extract_name_field(node, source_bytes)
    kind = kind_override or _FUNCTION_NODE_KINDS.get(node.type, "function")
    if not name:
        name = _anonymous_name("Function", node)
    snippet, start_line, end_line = _node_snippet(node, source_bytes)
    comment = _extract_leading_comment(node, source_bytes)
    docstring = _extract_docstring(node, source_bytes)
    signature = _extract_function_signature(node, source_bytes)
    effective_comment = docstring or comment
    summary = effective_comment
    note = _build_structured_note(signature, docstring, comment, snippet)
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
            comment=effective_comment,
            summary=summary,
            note=note,
            exported=exported,
            is_entrypoint=is_entrypoint,
            entrypoint_kind=entrypoint_kind,
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
        callee, receiver = _extract_call_name(call_node, source_bytes)
        if not callee:
            continue
        calls.append(
            CallEdge(
                caller_id=func_id,
                caller_scope=scope_name,
                callee_name=callee,
                callee_id=None,
                callee_arity=_count_arguments(call_node),
                callee_receiver=receiver,
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
            if target.type in _FUNCTION_NODE_KINDS:
                is_ep, ep_kind = _detect_entrypoint_kind(node, source_bytes)
                _record_function(
                    target,
                    source_bytes,
                    rel_path,
                    namespace_stack,
                    class_stack,
                    functions,
                    relations,
                    calls,
                    is_entrypoint=is_ep,
                    entrypoint_kind=ep_kind,
                )
            else:
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
        docstring = _extract_docstring(node, source_bytes)
        effective_comment = docstring or comment
        summary = effective_comment
        note = _build_structured_note(f"class {name}", docstring, comment, snippet)
        base_classes = _extract_base_classes(node, source_bytes)
        self_fields = _extract_self_fields(node, source_bytes)
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
                comment=effective_comment,
                summary=summary,
                note=note,
                base_classes=base_classes,
                self_fields=self_fields,
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


def parse_python_file(path: str, root: str) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[ClassDef],
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
    file_def = FileDef(
        file_path=rel_path,
        start_line=start_line,
        end_line=end_line,
        code=snippet,
        comment=file_comment,
        summary=file_summary,
        note=file_note,
        imports=imports,
        exports=[],
    )
    namespaces: List[NamespaceDef] = []
    classes: List[ClassDef] = []
    functions: List[FunctionDef] = []
    relations: List[RelationEdge] = []
    calls: List[CallEdge] = []
    namespace_registry: Dict[str, NamespaceDef] = {}
    class_registry: Dict[str, ClassDef] = {}
    _walk_tree(
        tree.root_node,
        source_bytes,
        rel_path,
        [],
        [],
        namespaces,
        classes,
        functions,
        relations,
        calls,
        namespace_registry,
        class_registry,
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
    Check if a directory should be ignored during scanning.

    Ignores:
    - Virtual environments: venv/, .venv/, env/, virtualenv/, env.bak/, venv.bak/
    - Python cache: __pycache__/, .pytest_cache/, .mypy_cache/, .cache/
    - Build artifacts: dist/, build/, *.egg-info/
    - Testing: .tox/, .coverage/, htmlcov/
    - IDE: .idea/, .vscode/, *.swp, *.swo
    - Version control: .git/, .svn/, .hg/
    - Temporary: temp/, tmp/, .tmp/
    - OS: .DS_Store, Thumbs.db
    - Node modules: node_modules/
    """
    ignore_patterns = {
        # Virtual environments
        "venv", ".venv", "env", "virtualenv",
        "env.bak", "venv.bak", ".env.bak", ".venv.bak",

        # Python cache
        "__pycache__", ".pytest_cache", ".mypy_cache", ".cache",

        # Build artifacts
        "dist", "build", ".eggs",

        # Testing
        ".tox", ".coverage", "htmlcov", "pytest_cache",

        # IDE
        ".idea", ".vscode",

        # Version control
        ".git", ".svn", ".hg",

        # Temporary
        "temp", "tmp", ".tmp", "tmpdir",

        # Node (mixed projects)
        "node_modules",

        # OS specific
        ".DS_Store", "Thumbs.db",
    }

    # Check directory name
    if dir_name in ignore_patterns:
        return True

    # Check for .egg-info directories (pattern match)
    if dir_name.endswith(".egg-info"):
        return True

    # Check for IDE swap files
    if dir_name.endswith((".swp", ".swo")):
        return True

    return False


def _scan_python_files(root: str) -> List[str]:
    """
    Scan for Python files, ignoring unnecessary directories.

    Returns sorted list of .py and .pyi file paths.
    """
    files: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        # Filter out ignored directories in-place
        # This modifies dirnames so os.walk won't recurse into them
        dirnames[:] = [
            d for d in dirnames
            if not _should_ignore_directory(d, os.path.join(dirpath, d))
        ]

        # Collect Python files from current directory
        for name in filenames:
            # Skip IDE swap files and OS files
            if name.endswith((".swp", ".swo", ".pyc", ".pyo")):
                continue
            if name in (".DS_Store", "Thumbs.db"):
                continue
            # Skip .env files
            if name.startswith(".env"):
                continue

            if name.endswith((".py", ".pyi")):
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
    ) = parse_python_file(file_path, root)
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
    incremental: bool = False,
    changed_files: Optional[Iterable[str]] = None,
    deleted_files: Optional[Iterable[str]] = None,
    commit_sha: str = "",
    commit_sha_before: str = "",
    enable_llm_summary: bool = False,
    enable_flows: bool = False,
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "python_analyzer", project_root=root)
    parse_cache_root = os.path.join(cache_root, "parse")
    qdrant_cache_root = os.path.join(cache_root, "qdrant")
    os.makedirs(parse_cache_root, exist_ok=True)
    os.makedirs(qdrant_cache_root, exist_ok=True)
    all_scanned_files = _scan_python_files(root)
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
        print(f"[scan] Found {len(selected_files)} Python files under {root}")
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

    # ── Load all selected payloads into memory ──────────────────────────────
    # Must be in-memory list (not generator) so semantic enrichment mutations
    # are visible to the graph and Qdrant write passes.
    cached_payloads: List[Dict[str, Any]] = []
    for index, file_path in enumerate(selected_files, start=1):
        if verbose and (index == 1 or index % 50 == 0 or index == total_files):
            print(f"[parse] {index}/{total_files}: {file_path}")
        cached_payloads.append(_load_or_parse_payload(file_path, root, parse_cache_root, parse_cache))

    # ── Build function index (for call resolution) ───────────────────────────
    function_index_by_name: Dict[str, List[Dict[str, Any]]] = {}
    function_index_by_name_arity: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    # {class_simple_name: {field_name: type_name}} for self-field type resolution
    class_self_fields_index: Dict[str, Dict[str, str]] = {}
    expected_points = 0
    for payload in cached_payloads:
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
        for cls in payload["classes"]:
            sf = cls.get("self_fields")
            if sf:
                class_self_fields_index[cls["name"]] = sf
            # Count class + file nodes for multi-node Qdrant
            expected_points += 1  # class embedding
        expected_points += 1  # file embedding

    # ── Semantic enrichment (mutates cached_payloads in-place) ───────────────
    if _semantic_engine is not None:
        all_enrichment_functions: List[Dict[str, Any]] = [
            f for p in cached_payloads for f in p["functions"]
        ]
        all_enrichment_calls: List[Dict[str, Any]] = [
            c for p in cached_payloads for c in p["calls"]
        ]
        if verbose:
            print(f"[semantic] Enriching {len(all_enrichment_functions)} functions...")
        _semantic_engine.enrich_corpus(all_enrichment_functions, all_enrichment_calls)
        if verbose:
            enriched = sum(1 for f in all_enrichment_functions if f.get("inferred_doc"))
            avg_conf = sum(f.get("doc_confidence", 0.0) for f in all_enrichment_functions) / max(len(all_enrichment_functions), 1)
            print(f"[semantic] Enriched {enriched}/{len(all_enrichment_functions)} functions; avg confidence={avg_conf:.2f}")

    # ── LLM summary (opt-in, applied to low-confidence functions only) ───────
    if enable_llm_summary:
        try:
            from tools.common.llm_summary import generate_summaries as _gen_summaries
            target_funcs = [
                f for p in cached_payloads for f in p["functions"]
                if f.get("doc_confidence", 1.0) < 0.4
            ]
            if target_funcs:
                if verbose:
                    print(f"[llm] Generating summaries for {len(target_funcs)} low-confidence functions...")
                _gen_summaries(target_funcs, verbose=verbose)
        except ImportError:
            if verbose:
                print("[llm] llm_summary module not available; skipping")

    # ── Build module-path index for IMPORTS edge resolution ──────────────────
    module_to_rel_path = _build_module_index(all_scanned_files, root)

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
            # Self-field type resolution: self.repo.save() → UserRepository.save
            receiver = call.get("callee_receiver", "") or ""
            if receiver.startswith("self."):
                field_name = receiver[5:].split(".")[0]
                caller_scope = call.get("caller_scope", "") or ""
                class_name = caller_scope.split("::")[-1] if caller_scope else ""
                if class_name and class_name in class_self_fields_index:
                    resolved_type = class_self_fields_index[class_name].get(field_name)
                    if resolved_type:
                        type_scoped = [c for c in candidates if (c.get("scope_name") or "").endswith(resolved_type)]
                        if len(type_scoped) == 1:
                            return type_scoped[0]["symbol_id"]
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

        for payload in cached_payloads:
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
            # IMPORTS edges: file → imported file
            for imp_str in file_def.get("imports") or []:
                target_path = _resolve_import_to_path(imp_str, module_to_rel_path)
                if target_path and target_path != file_id:
                    all_relations.append(
                        {"source_id": file_id, "target_id": target_path, "rel_type": "IMPORTS", "properties": {}}
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
                        "base_classes": class_def.get("base_classes") or [],
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
                # INHERITS_FROM edges
                for base in class_def.get("base_classes") or []:
                    all_relations.append(
                        {"source_id": class_def["symbol_id"], "target_id": base, "rel_type": "INHERITS_FROM", "properties": {"base_name": base}}
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
                        # Semantic fields
                        "intent": func.get("intent", ""),
                        "inferred_doc": func.get("inferred_doc", False),
                        "doc_confidence": func.get("doc_confidence", 0.0),
                        "side_effect": func.get("side_effect", False),
                        # Entrypoint fields
                        "is_entrypoint": func.get("is_entrypoint", False),
                        "entrypoint_kind": func.get("entrypoint_kind", ""),
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

        # ── OVERRIDES edges ───────────────────────────────────────────────────
        # method_by_class: {class_symbol_id: {method_name: symbol_id}}
        method_by_class: Dict[str, Dict[str, str]] = {}
        for func_node in all_functions:
            scope = func_node.get("scope_name", "") or ""
            if scope:
                method_by_class.setdefault(scope, {})[func_node["name"]] = func_node["id"]
        for payload in cached_payloads:
            for cls in payload["classes"]:
                cls_id = cls["symbol_id"]
                for base in cls.get("base_classes") or []:
                    base_methods = method_by_class.get(base, {})
                    my_methods = method_by_class.get(cls_id, {})
                    for method_name, method_id in my_methods.items():
                        if method_name in base_methods:
                            all_relations.append({
                                "source_id": method_id,
                                "target_id": base_methods[method_name],
                                "rel_type": "OVERRIDES",
                                "properties": {},
                            })

        # ── Flow extraction ───────────────────────────────────────────────────
        all_workflows: List[Dict[str, Any]] = []
        all_workflow_steps: List[Dict[str, Any]] = []
        if enable_flows:
            resolved_call_pairs = [(c["caller_id"], c["callee_id"]) for c in all_calls]
            flows = _build_execution_flows(all_functions, resolved_call_pairs)
            if verbose:
                print(f"[flows] Extracted {len(flows)} execution flows")
            if flows:
                try:
                    from tools.common.workflow_classifier import WorkflowNameClassifier
                    function_lookup = {f["id"]: f for f in all_functions}
                    classifier = WorkflowNameClassifier(
                        project_id=project_id,
                        language=language,
                    )
                    matches = await classifier.classify_batch(flows, function_lookup)
                    for m in matches:
                        all_workflows.append(m.to_dict())
                        for order, sid in enumerate(m.step_ids):
                            all_workflow_steps.append({
                                "workflow_id": m.workflow_id,
                                "function_id": sid,
                                "step_order": order,
                            })
                    if verbose:
                        print(f"[flows] Classified {len(all_workflows)} workflows")
                except Exception as _exc:
                    if verbose:
                        print(f"[flows] Workflow classification failed: {_exc}")
                    # Fallback: store raw flow nodes without LLM naming
                    for flow in flows:
                        all_workflows.append({
                            "workflow_id": flow.flow_id,
                            "workflow_name": flow.name,
                            "domain": "unknown",
                            "description": "",
                            "confidence": 0.0,
                            "entrypoint_id": flow.entrypoint_id,
                            "language": language,
                            "project": project_id,
                            "kind": flow.kind,
                        })
                        for order, step_id in enumerate(flow.step_ids):
                            all_workflow_steps.append({
                                "workflow_id": flow.flow_id,
                                "function_id": step_id,
                                "step_order": order,
                            })

        await code_writer.write_all(
            projects=all_projects,
            namespaces=all_namespaces or None,
            files=all_files or None,
            types=all_types or None,
            functions=all_functions or None,
            relations=all_relations or None,
            calls=all_calls or None,
            workflows=all_workflows or None,
            workflow_steps=all_workflow_steps or None,
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

            def _make_func_point(func_item: Dict[str, Any], vector: List[float]) -> Dict[str, Any]:
                return {
                    "id": _stable_point_id(func_item["symbol_id"]),
                    "vector": vector,
                    "payload": {
                        "node_type": "function",
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
                        "intent": func_item.get("intent", ""),
                        "inferred_doc": func_item.get("inferred_doc", False),
                        "doc_confidence": func_item.get("doc_confidence", 0.0),
                        "side_effect": func_item.get("side_effect", False),
                        "is_entrypoint": func_item.get("is_entrypoint", False),
                        "entrypoint_kind": func_item.get("entrypoint_kind", ""),
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                }

            def _make_class_point(cls_item: Dict[str, Any], vector: List[float]) -> Dict[str, Any]:
                return {
                    "id": _stable_point_id(cls_item["symbol_id"]),
                    "vector": vector,
                    "payload": {
                        "node_type": "class",
                        "symbol_id": cls_item["symbol_id"],
                        "qualified_name": cls_item["qualified_name"],
                        "name": cls_item["name"],
                        "kind": cls_item["kind"],
                        "file_path": cls_item["file_path"],
                        "start_line": cls_item["start_line"],
                        "end_line": cls_item["end_line"],
                        "comment": cls_item["comment"],
                        "summary": cls_item["summary"],
                        "note": cls_item["note"],
                        "base_classes": cls_item.get("base_classes") or [],
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                }

            def _make_file_point(file_item: Dict[str, Any], vector: List[float]) -> Dict[str, Any]:
                return {
                    "id": _stable_point_id(f"file::{file_item['file_path']}"),
                    "vector": vector,
                    "payload": {
                        "node_type": "file",
                        "symbol_id": f"file::{file_item['file_path']}",
                        "file_path": file_item["file_path"],
                        "comment": file_item["comment"],
                        "summary": file_item["summary"],
                        "note": file_item["note"],
                        "project_id": project_id,
                        "project_name": project_name,
                        "language": language,
                        "repo": repo,
                        "build_system": build_system,
                    },
                }

            # Collect all embeddable items: (text, point_factory)
            embed_items: List[Tuple[str, Any]] = []
            for payload in cached_payloads:
                for func in payload["functions"]:
                    text = func.get("note") or func.get("code") or ""
                    embed_items.append((text, lambda v, f=func: _make_func_point(f, v)))
                for cls in payload["classes"]:
                    text = cls.get("note") or cls.get("code") or ""
                    embed_items.append((text, lambda v, c=cls: _make_class_point(c, v)))
                fd = payload["file_def"]
                imports_preview = "; ".join((fd.get("imports") or [])[:15])
                file_text = f"{fd.get('summary', '')}\n{imports_preview}".strip() or fd.get("code", "")[:400]
                embed_items.append((file_text, lambda v, f=fd: _make_file_point(f, v)))

            total_embed = len(embed_items)
            total_batches = max(1, (total_embed + batch_size - 1) // batch_size)
            batch_index = 0
            with open(points_path, "w", encoding="utf-8") as handle:
                current_batch: List[Tuple[str, Any]] = []
                for text, make_fn in embed_items:
                    current_batch.append((text, make_fn))
                    if len(current_batch) >= batch_size:
                        batch_index += 1
                        texts = [t for t, _ in current_batch]
                        vectors = embedder.embed(texts, batch_size=batch_size, verbose=False)
                        if verbose:
                            print(f"[embed] batch {batch_index} / {total_batches}")
                        for (_, make_fn_item), vector in zip(current_batch, vectors):
                            handle.write(json.dumps(make_fn_item(vector), ensure_ascii=True) + "\n")
                        current_batch.clear()
                if current_batch:
                    batch_index += 1
                    texts = [t for t, _ in current_batch]
                    vectors = embedder.embed(texts, batch_size=batch_size, verbose=False)
                    if verbose:
                        print(f"[embed] batch {batch_index} / {total_batches}")
                    for (_, make_fn_item), vector in zip(current_batch, vectors):
                        handle.write(json.dumps(make_fn_item(vector), ensure_ascii=True) + "\n")

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
    _sr_fn = sum(len(p.get("functions") or []) for p in cached_payloads)
    _sr_cls = sum(len(p.get("classes") or []) for p in cached_payloads)
    print(f"[SCAN_RESULT] parser={language} files={len(cached_payloads)} functions={_sr_fn} classes={_sr_cls}", flush=True)
    if verbose:
        elapsed = time.time() - start_time
        print(f"[done] Total time: {elapsed:.2f}s")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Python call graph analyzer")
    parser.add_argument("--root", required=True, help="Root folder containing Python sources")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.environ.get("QDRANT_COLLECTION", "python_functions"),
    )
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--batch-size", type=int, default=4)  # for embedding - 4 function 1 turn embedding
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
    # Semantic upgrade flags
    parser.add_argument(
        "--enable-llm-summary",
        action="store_true",
        default=os.environ.get("ENABLE_LLM_SUMMARY", "").lower() in ("1", "true", "yes"),
        help="Generate LLM summaries for low-confidence functions (requires OPENAI_API_KEY or LITELLM_ENDPOINT)",
    )
    parser.add_argument(
        "--enable-flows",
        action="store_true",
        default=os.environ.get("ENABLE_FLOWS", "").lower() in ("1", "true", "yes"),
        help="Extract execution flows from entrypoint functions and write to Neo4j",
    )
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
        run_cache_root = safe_cache_root(effective_cache_dir, "python_analyzer", project_root=args.root)
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
        cache_root = safe_cache_root(effective_cache_dir, "python_analyzer", project_root=args.root)
        neo4j_state_path = args.neo4j_state or os.path.join(cache_root, "neo4j_state.json")
    elif args.incremental and args.verbose:
        print("[state] incremental mode disables neo4j resume state")
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "python"
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""
    commit_sha = args.commit_sha_after or ""
    commit_sha_before = args.commit_sha_before or ""
    message_qdrant_collection = (
        args.message_qdrant_collection
        or default_message_collection_name(args.qdrant_collection)
    )
    if code_writer and args.verbose:
        print("[cloc] Disabled for python_analyzer (temporary)")

    try:
        if args.dry_run:
            files = _scan_python_files(args.root)
            if args.incremental and changed_manifest_files:
                manifest_set = set(changed_manifest_files)
                files = [
                    file_path
                    for file_path in files
                    if os.path.relpath(file_path, args.root).replace("\\", "/") in manifest_set
                ]
                print(
                    "Dry run (incremental): %d Python files selected (manifest=%d)"
                    % (len(files), len(changed_manifest_files))
                )
            else:
                print(f"Dry run: {len(files)} Python files found")
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
            enable_llm_summary=args.enable_llm_summary,
            enable_flows=args.enable_flows,
        )
        if args.enable_message_scan:
            message_summary = await run_message_scan_pipeline(
                root=args.root,
                parser="python",
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
    raise SystemExit(asyncio.run(main()))

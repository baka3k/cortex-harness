"""ParserAgent — tree-sitter parsing and low-level AST node utilities.

Responsibilities:
- Obtain a cached tree-sitter parser for TypeScript or TSX.
- Parse a file into (tree, source_bytes).
- Walk tree nodes to extract text, line numbers, comments, identifiers, etc.
- Collect JSX tags/components from a parse tree.

All functions in this module are pure (no side-effects, no I/O except
``_parse_file`` which reads from disk).
"""
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Tuple

from tree_sitter import Language, Parser

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:
    ts_get_parser = None  # type: ignore[assignment]


# ─── JSX node types ───────────────────────────────────────────────────────────
_JSX_NODE_TYPES = {
    "jsx_element", "jsx_fragment", "jsx_text",
    "jsx_opening_element", "jsx_self_closing_element",
}


# ─── Parser factory ───────────────────────────────────────────────────────────

def _get_ts_parser(language_name: str) -> Parser:
    """Return a tree-sitter parser for *language_name* ("typescript" or "tsx")."""
    if ts_get_parser is not None:
        try:
            return ts_get_parser(language_name)
        except Exception:
            pass
    try:
        from tree_sitter_typescript import language_tsx, language_typescript
    except Exception as exc:
        raise RuntimeError(
            "TypeScript parser unavailable. Install 'tree-sitter-typescript' or "
            "'tree-sitter-languages'."
        ) from exc
    language = language_tsx() if language_name == "tsx" else language_typescript()
    if not isinstance(language, Language):
        language = Language(language)
    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    return parser


def _parse_file(path: str) -> Tuple[Any, bytes]:
    """Parse *path* with the appropriate grammar; return (tree, source_bytes)."""
    language_name = "tsx" if path.endswith(".tsx") else "typescript"
    parser = _get_ts_parser(language_name)
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    tree = parser.parse(source_bytes)
    return tree, source_bytes


# ─── Error stats ──────────────────────────────────────────────────────────────

def _is_benign_jsx_entity_error(node: Any, source_bytes: bytes) -> bool:
    """Return True for ERROR nodes that are bare '&' in JSX text content.

    Tree-sitter's TSX grammar interprets '&' as the start of an HTML entity
    and raises an ERROR when no terminating ';' follows (e.g. 'Help & Support').
    React/React-Native handles this at runtime, so these pseudo-errors should
    not count against the file's parse-error score.
    """
    text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
    if not text.startswith("&"):
        return False
    parent = node.parent
    while parent is not None:
        if parent.type in _JSX_NODE_TYPES:
            return True
        parent = parent.parent
    return False


def _tree_error_stats(tree: Any, source_bytes: bytes = b"") -> Tuple[bool, int]:
    if tree is None:
        return False, 0
    real_errors = [
        n for n in _find_nodes_by_type(tree.root_node, "ERROR")
        if not _is_benign_jsx_entity_error(n, source_bytes)
    ]
    return bool(real_errors), len(real_errors)


# ─── Node text extraction ─────────────────────────────────────────────────────

def _node_text(node: Any, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte: node.end_byte].decode("utf-8", errors="ignore")


def _line_from_byte(source_bytes: bytes, byte_index: int) -> int:
    return source_bytes[:byte_index].count(b"\n") + 1


def _node_snippet(node: Any, source_bytes: bytes) -> Tuple[str, int, int]:
    start_byte = node.start_byte
    prev = node.prev_sibling
    while prev is not None and prev.type == "comment":
        start_byte = prev.start_byte
        prev = prev.prev_sibling
    snippet = source_bytes[start_byte: node.end_byte].decode("utf-8", errors="ignore")
    start_line = _line_from_byte(source_bytes, start_byte)
    end_line = node.end_point[0] + 1
    return snippet, start_line, end_line


# ─── Node traversal helpers ───────────────────────────────────────────────────

def _find_nodes_by_type(node: Any, node_type: str) -> Iterable[Any]:
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
            break


def _first_identifier(node: Any, source_bytes: bytes) -> Optional[str]:
    if node is None:
        return None
    if node.type in {"identifier", "property_identifier", "type_identifier", "namespace_identifier"}:
        return _node_text(node, source_bytes)
    for child in node.children:
        result = _first_identifier(child, source_bytes)
        if result:
            return result
    return None


def _extract_name_field(node: Any, source_bytes: bytes) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source_bytes)
    return _first_identifier(node, source_bytes)


def _extract_leading_comment(node: Any, source_bytes: bytes) -> str:
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


def _extract_file_comment(tree: Any, source_bytes: bytes) -> str:
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


# ─── Text normalization ───────────────────────────────────────────────────────

def _build_note(code: str, comment: str, summary: str) -> str:
    parts: List[str] = []
    if summary:
        parts.append(f"Summary:\n{summary}")
    if comment:
        parts.append(f"Comment:\n{comment}")
    if code:
        parts.append(f"Code:\n{code}")
    return "\n\n".join(parts)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_call_name(text: str) -> str:
    cleaned = re.sub(r"<[^<>]*>", "", text)
    cleaned = cleaned.replace("this.", "")
    cleaned = cleaned.replace("super.", "")
    cleaned = cleaned.replace("?.", ".")
    cleaned = cleaned.replace("::", ".")
    cleaned = cleaned.strip()
    bracket_match = re.search(r"\[\s*(['\"])(?P<name>[^'\"]+)\1\s*\]\s*$", cleaned)
    if bracket_match:
        return bracket_match.group("name")
    if "." in cleaned:
        cleaned = cleaned.split(".")[-1]
    return cleaned.strip()


def _extract_scope_stack(stack: List[str]) -> Optional[str]:
    return "::".join(stack) if stack else None


# ─── Function node introspection ──────────────────────────────────────────────

def _count_parameters(node: Any) -> int:
    params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter_list")
    if params is None:
        return 0
    return sum(1 for child in params.children if child.is_named and child.type != "comment")


def _extract_return_type(node: Any, source_bytes: bytes) -> str:
    ret_node = node.child_by_field_name("return_type")
    if ret_node is None:
        return ""
    text = _node_text(ret_node, source_bytes).strip()
    return text.lstrip(":").strip()


def _extract_param_types(node: Any, source_bytes: bytes) -> List[str]:
    params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter_list")
    if params is None:
        return []
    result: List[str] = []
    for child in params.children:
        if not child.is_named or child.type == "comment":
            continue
        type_node = child.child_by_field_name("type")
        if type_node is not None:
            result.append(_node_text(type_node, source_bytes).strip().lstrip(":").strip())
        else:
            result.append("")
    return result


def _count_arguments(node: Any) -> int:
    args = node.child_by_field_name("arguments") or node.child_by_field_name("argument_list")
    if args is None:
        return 0
    return sum(1 for child in args.children if child.is_named and child.type != "comment")


def _iter_calls(func_node: Any) -> Iterable[Any]:
    for node in _find_nodes_by_type(func_node, "call_expression"):
        yield node
    for node in _find_nodes_by_type(func_node, "new_expression"):
        yield node


def _extract_call_name(call_node: Any, source_bytes: bytes) -> Optional[str]:
    field = None
    if call_node.type == "call_expression":
        field = "function"
    elif call_node.type == "new_expression":
        field = "constructor"
    if field:
        expr = call_node.child_by_field_name(field)
        if expr is not None:
            return _normalize_call_name(_node_text(expr, source_bytes).strip())
    text = _node_text(call_node, source_bytes).strip()
    if text.startswith("new "):
        text = text[4:]
    text = text.split("(", 1)[0].strip()
    return _normalize_call_name(text)


# ─── JSX helpers ─────────────────────────────────────────────────────────────

def _jsx_name(node: Any, source_bytes: bytes) -> Optional[str]:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        for child in node.children:
            if child.type in {"jsx_identifier", "jsx_member_expression", "jsx_namespaced_name"}:
                name_node = child
                break
    if name_node is None:
        return None
    return _node_text(name_node, source_bytes)


def _collect_jsx_tags(tree: Any, source_bytes: bytes) -> Tuple[List[str], List[str]]:
    """Return (lowercase_tags, PascalCase_components) lists from a parse tree."""
    tags: dict = {}
    components: dict = {}
    for node in _find_nodes_by_type(tree.root_node, "jsx_opening_element"):
        name = _jsx_name(node, source_bytes)
        if not name:
            continue
        if name[0].islower():
            tags[name] = None
        else:
            components[name] = None
    for node in _find_nodes_by_type(tree.root_node, "jsx_self_closing_element"):
        name = _jsx_name(node, source_bytes)
        if not name:
            continue
        if name[0].islower():
            tags[name] = None
        else:
            components[name] = None
    return sorted(tags.keys()), sorted(components.keys())


# ─── Import / export extraction ───────────────────────────────────────────────

def _collect_imports(tree: Any, source_bytes: bytes) -> List[str]:
    imports: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "import_statement"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            imports.append(text)
    for node in _find_nodes_by_type(tree.root_node, "import_require_clause"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            imports.append(text)
    return imports


def _collect_exports(tree: Any, source_bytes: bytes) -> List[str]:
    exports: List[str] = []
    for node in _find_nodes_by_type(tree.root_node, "export_statement"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            exports.append(text)
    for node in _find_nodes_by_type(tree.root_node, "export_default_declaration"):
        text = _normalize_ws(_node_text(node, source_bytes))
        if text:
            exports.append(text)
    return exports


# ─── ParserAgent class facade ─────────────────────────────────────────────────

class ParserAgent:
    """Thin object-oriented facade over the module-level parsing functions.

    Instantiate once per pipeline run and call ``parse_file`` for each file.
    The module-level functions remain the canonical implementation and are
    directly importable for use outside the class hierarchy.
    """

    def get_parser(self, language_name: str) -> Parser:
        return _get_ts_parser(language_name)

    def parse_file(self, path: str) -> Tuple[Any, bytes]:
        return _parse_file(path)

    def tree_error_stats(self, tree: Any, source_bytes: bytes = b"") -> Tuple[bool, int]:
        return _tree_error_stats(tree, source_bytes)

    def collect_jsx_tags(self, tree: Any, source_bytes: bytes) -> Tuple[List[str], List[str]]:
        return _collect_jsx_tags(tree, source_bytes)

    def collect_imports(self, tree: Any, source_bytes: bytes) -> List[str]:
        return _collect_imports(tree, source_bytes)

    def collect_exports(self, tree: Any, source_bytes: bytes) -> List[str]:
        return _collect_exports(tree, source_bytes)

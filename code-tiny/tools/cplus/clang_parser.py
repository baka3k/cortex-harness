"""
tools/cplus/clang_parser.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Optional libclang-based fallback parser for C/C++ files.

Activated automatically from ``cplus_analyzer._load_or_parse_payload`` when
tree-sitter reports excessive error nodes (threshold managed by the caller).

Install the dependency with:
    pip install libclang

libclang requires LLVM to be present on the system.  On macOS it is
provided by ``brew install llvm``.  On Linux it ships with the distro
``clang`` package (e.g. ``apt install libclang-dev``).

If the package is not installed the module degrades silently: every
public function returns a safe default so that callers can use
``clang_parser.is_available()`` as a guard without try/except.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional libclang import
# ---------------------------------------------------------------------------
try:
    import clang.cindex as _ci  # pip install libclang

    _CLANG_AVAILABLE = True

    _FUNC_KINDS = frozenset(
        {
            _ci.CursorKind.FUNCTION_DECL,
            _ci.CursorKind.CXX_METHOD,
            _ci.CursorKind.CONSTRUCTOR,
            _ci.CursorKind.DESTRUCTOR,
            _ci.CursorKind.FUNCTION_TEMPLATE,
        }
    )
    _TYPE_KINDS: Dict[Any, str] = {
        _ci.CursorKind.CLASS_DECL: "class",
        _ci.CursorKind.STRUCT_DECL: "struct",
        _ci.CursorKind.UNION_DECL: "union",
        _ci.CursorKind.ENUM_DECL: "enum",
        _ci.CursorKind.CLASS_TEMPLATE: "class_template",
    }
    # TypeKind values that represent function-pointer / function-prototype
    # types.  Used to detect typedef/using declarations that alias a function
    # type (e.g. ``typedef void (*Callback)(int)``).
    _FUNC_TYPE_KINDS: frozenset = frozenset(
        {
            _ci.TypeKind.FUNCTIONPROTO,
            _ci.TypeKind.FUNCTIONNOPROTO,
        }
    )
    # CursorKind values for template parameter declarations.
    _TEMPLATE_PARAM_KINDS: frozenset = frozenset(
        {
            _ci.CursorKind.TEMPLATE_TYPE_PARAMETER,
            _ci.CursorKind.TEMPLATE_NON_TYPE_PARAMETER,
            _ci.CursorKind.TEMPLATE_TEMPLATE_PARAMETER,
        }
    )

except ImportError:
    _ci = None  # type: ignore[assignment]
    _CLANG_AVAILABLE = False
    _FUNC_KINDS: frozenset = frozenset()  # type: ignore[no-redef]
    _TYPE_KINDS = {}  # type: ignore[no-redef]
    _FUNC_TYPE_KINDS: frozenset = frozenset()  # type: ignore[no-redef]
    _TEMPLATE_PARAM_KINDS: frozenset = frozenset()  # type: ignore[no-redef]



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True when libclang is importable and usable."""
    return _CLANG_AVAILABLE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_compile_args(abs_path: str, compile_commands_path: str) -> List[str]:
    """Look up per-file compile flags from *compile_commands.json*.

    Returns an empty list when the file is not found in the database or the
    database cannot be read.
    """
    try:
        with open(compile_commands_path, "r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except Exception:
        return []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        file_field = entry.get("file", "")
        if not file_field:
            continue
        entry_dir = entry.get("directory") or ""
        if not os.path.isabs(file_field) and entry_dir:
            file_field = os.path.join(entry_dir, file_field)
        try:
            if os.path.abspath(file_field) != abs_path:
                continue
        except ValueError:
            continue

        args = entry.get("arguments")
        if isinstance(args, list):
            tokens = [str(a) for a in args]
        else:
            cmd = entry.get("command", "")
            try:
                tokens = shlex.split(cmd)
            except ValueError:
                tokens = cmd.split()

        # Strip: compiler (index 0), -o <output>, -c, and the source file.
        result: List[str] = []
        skip_next = False
        for tok in tokens[1:]:
            if skip_next:
                skip_next = False
                continue
            if tok in ("-o", "--output"):
                skip_next = True
                continue
            if tok == "-c":
                continue
            if not tok.startswith("-"):
                try:
                    if os.path.abspath(tok) == abs_path:
                        continue
                except ValueError:
                    pass
            result.append(tok)
        return result

    return []


def _build_scope(cursor: Any) -> str:
    """Return the ``::``-separated scope path of *cursor*'s semantic parents."""
    if _ci is None:
        return ""
    parts: List[str] = []
    parent = cursor.semantic_parent
    while parent is not None:
        if parent.kind == _ci.CursorKind.TRANSLATION_UNIT:
            break
        sp = parent.spelling
        if sp:
            parts.append(sp)
        parent = parent.semantic_parent
    return "::".join(reversed(parts))


def _extract_file_comment(text: str) -> str:
    """Return the leading block of C/C++ comment lines from *text*."""
    comment_lines: List[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("//", "/*", "*")):
            comment_lines.append(stripped)
        elif stripped:
            break
    return "\n".join(comment_lines)


def _extract_leading_comment(text: str, start_line: int) -> str:
    """Return the comment block immediately above *start_line* (1-indexed)."""
    if start_line <= 1:
        return ""
    lines = text.split("\n")
    parts: List[str] = []
    idx = start_line - 2  # 0-based, one line above
    while idx >= 0:
        stripped = lines[idx].strip()
        if stripped.startswith(("//", "/*", "*")):
            parts.append(stripped)
            idx -= 1
        else:
            break
    return "\n".join(reversed(parts))


def _find_enclosing_func(
    offset: int, func_extents: List[Tuple[int, int, str]]
) -> Optional[str]:
    """Return the *symbol_id* of the innermost function that contains *offset*.

    Uses bisect on the pre-sorted (by start offset) *func_extents* list for
    O(log n) candidate lookup instead of linear scan.
    """
    if not func_extents:
        return None

    # Build the index array for start offsets (lazy, on first call).
    # Cache keyed by ``id(func_extents)`` so we don't rebuild across calls
    # on the same list.
    cache: List[Tuple[int, int, str]] = getattr(  # type: ignore[assignment]
        _find_enclosing_func, "_cache", None
    )
    if cache is None or getattr(_find_enclosing_func, "_cache_id", None) != id(
        func_extents
    ):
        # Sort by start offset (pre-order DFS already guarantees this, but
        # ensure it explicitly).
        sorted_extents = sorted(func_extents, key=lambda x: x[0])
        _find_enclosing_func._cache = sorted_extents  # type: ignore[attr-defined]
        _find_enclosing_func._cache_id = id(func_extents)  # type: ignore[attr-defined]
        _find_enclosing_func._starts = [s[0] for s in sorted_extents]  # type: ignore[attr-defined]
        cache = sorted_extents

    starts: List[int] = getattr(_find_enclosing_func, "_starts")  # type: ignore[attr-defined]
    idx = bisect_right(starts, offset) - 1

    best_span: Optional[int] = None
    best_id: Optional[str] = None
    while idx >= 0:
        start, end, symbol_id = cache[idx]
        # A function to the left starts earlier but may end later
        # (outer scope), so we cannot break early.  Check every candidate.
        if start <= offset <= end:
            span = end - start
            if best_span is None or span < best_span:
                best_span = span
                best_id = symbol_id
        idx -= 1
    return best_id


import hashlib


def _function_type_id(type_signature: str, rel_path: str) -> str:
    """Build a stable symbol_id for a function-type typedef/using alias."""
    normalized = " ".join(type_signature.split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return f"functype::{digest}@{rel_path}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_and_extract(
    path: str,
    root: str,
    compile_commands_path: str,
) -> Optional[Dict[str, Any]]:
    """Parse a C/C++ file with libclang and return a *payload* dict.

    The returned dict has exactly the same schema as the ``payload`` built by
    ``_load_or_parse_payload`` in ``cplus_analyzer.py`` so it can be returned
    transparently (and cached) in place of the tree-sitter result.

    Returns ``None`` when:
    - libclang is not installed/importable.
    - libclang fails to create a TranslationUnit.
    - Any unexpected exception occurs during extraction.
    """
    if _ci is None:
        return None

    abs_path = os.path.abspath(path)
    rel_path = os.path.relpath(path, root)

    compile_args = _get_compile_args(abs_path, compile_commands_path)

    try:
        index = _ci.Index.create()
        tu = index.parse(
            path,
            args=compile_args,
            options=(
                _ci.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
                | _ci.TranslationUnit.PARSE_INCOMPLETE
            ),
        )
    except Exception as exc:
        logger.warning("libclang failed to create TU for %s: %s", path, exc)
        return None

    if tu is None:
        return None

    try:
        with open(path, "rb") as fh:
            source_bytes = fh.read()
    except OSError:
        source_bytes = b""

    source_text = source_bytes.decode("utf-8", errors="ignore")
    file_lines = source_text.count("\n") + 1
    file_comment = _extract_file_comment(source_text)

    functions: List[Dict[str, Any]] = []
    function_types: List[Dict[str, Any]] = []
    calls: List[Dict[str, Any]] = []
    types: List[Dict[str, Any]] = []
    namespaces: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    fields: List[Dict[str, Any]] = []
    aliases: List[Dict[str, Any]] = []
    templates: List[Dict[str, Any]] = []
    using_namespaces: List[str] = []
    using_imports: Dict[str, str] = {}
    includes: List[str] = []
    macros: Dict[str, str] = {}

    seen_func_ids: set = set()
    seen_type_ids: set = set()
    seen_ns_ids: set = set()

    # (start_offset, end_offset, symbol_id) — used to find the enclosing
    # function of a CALL_EXPR during the single-pass traversal.
    func_extents: List[Tuple[int, int, str]] = []

    # Iterative pre-order DFS (same pattern as tree-sitter TreeCursor fix).
    # Children are pushed in reversed order so left-to-right processing is
    # preserved.  This also means parent cursors are always processed before
    # their children, which guarantees func_extents is populated before any
    # CALL_EXPR inside the function body is encountered.
    stack: List[Any] = [tu.cursor]
    while stack:
        cursor = stack.pop()
        kind = cursor.kind

        # -- INCLUSION_DIRECTIVE ------------------------------------------
        # Collect the include path but do NOT recurse into the included
        # file's cursor tree.
        if kind == _ci.CursorKind.INCLUSION_DIRECTIVE:
            inc_name = cursor.displayname or cursor.spelling
            if inc_name:
                includes.append(inc_name)
            continue  # do not push children

        # -- Filter out cursors from other files --------------------------
        # If the cursor has a resolved location that belongs to a different
        # file, skip it AND its entire subtree.  This avoids iterating over
        # the full standard-library AST included via headers.
        loc = cursor.location
        if loc and loc.file:
            try:
                if os.path.abspath(loc.file.name) != abs_path:
                    continue
            except Exception:
                pass  # unknown location — keep going

        # Push children now (before processing) so pre-order is maintained.
        stack.extend(reversed(list(cursor.get_children())))

        # -- MACRO_DEFINITION ---------------------------------------------
        if kind == _ci.CursorKind.MACRO_DEFINITION and cursor.spelling:
            tokens = list(cursor.get_tokens())
            value = " ".join(t.spelling for t in tokens[1:]) if len(tokens) > 1 else ""
            macros[cursor.spelling] = value
            continue

        # -- USING_DIRECTIVE ----------------------------------------------
        if kind == _ci.CursorKind.USING_DIRECTIVE:
            ref = cursor.referenced
            if ref and ref.spelling:
                using_namespaces.append(ref.spelling)
            continue

        # -- USING_DECLARATION --------------------------------------------
        if kind == _ci.CursorKind.USING_DECLARATION and cursor.spelling:
            ref = cursor.referenced
            if ref and ref.spelling:
                using_imports[cursor.spelling] = ref.spelling
            continue

        # -- Shared extent for remaining kinds ----------------------------
        ext = cursor.extent
        start_line: int = ext.start.line
        end_line: int = ext.end.line
        start_byte: int = ext.start.offset
        end_byte: int = ext.end.offset
        code = source_bytes[start_byte:end_byte].decode("utf-8", errors="ignore")

        # -- FUNCTION / METHOD DEFINITIONS --------------------------------
        if kind in _FUNC_KINDS and cursor.is_definition() and cursor.spelling:
            scope = _build_scope(cursor)
            name = cursor.spelling
            qualified = f"{scope}::{name}" if scope else name
            arity = sum(1 for _ in cursor.get_arguments())
            _kind_map = {
                _ci.CursorKind.FUNCTION_DECL: "function",
                _ci.CursorKind.CXX_METHOD: "method",
                _ci.CursorKind.CONSTRUCTOR: "constructor",
                _ci.CursorKind.DESTRUCTOR: "destructor",
                _ci.CursorKind.FUNCTION_TEMPLATE: "function_template",
            }
            fkind = _kind_map.get(kind, "function")
            symbol_id = f"{qualified}/{arity}@{rel_path}"
            if symbol_id not in seen_func_ids:
                seen_func_ids.add(symbol_id)
                comment = _extract_leading_comment(source_text, start_line)
                functions.append(
                    {
                        "symbol_id": symbol_id,
                        "qualified_name": qualified,
                        "name": name,
                        "kind": fkind,
                        "scope_name": scope or None,
                        "file_path": rel_path,
                        "start_byte": start_byte,
                        "end_byte": end_byte,
                        "start_line": start_line,
                        "end_line": end_line,
                        "arity": arity,
                        "code": code,
                        "comment": comment,
                        "summary": comment,
                        "note": "",
                    }
                )
                func_extents.append((start_byte, end_byte, symbol_id))

            # Emit template parameter entries when this is a template
            # function/class.  The children contain
            # TEMPLATE_TYPE_PARAMETER etc.
            if kind in (
                _ci.CursorKind.FUNCTION_TEMPLATE,
                _ci.CursorKind.CLASS_TEMPLATE,
            ):
                for child in cursor.get_children():
                    if child.kind in _TEMPLATE_PARAM_KINDS and child.spelling:
                        child_ext = child.extent
                        child_code = (
                            source_bytes[child_ext.start.offset : child_ext.end.offset]
                            .decode("utf-8", errors="ignore")
                        )
                        templates.append(
                            {
                                "symbol_id": f"tparam::{child.spelling}@{rel_path}",
                                "name": child.spelling,
                                "file_path": rel_path,
                                "start_line": child_ext.start.line,
                                "end_line": child_ext.end.line,
                                "code": child_code,
                            }
                        )

        # -- CALL EXPRESSIONS ---------------------------------------------
        elif kind == _ci.CursorKind.CALL_EXPR:
            ref = cursor.referenced
            callee_name = (ref.spelling if ref and ref.spelling else None) or cursor.spelling or ""
            if callee_name:
                caller_id = _find_enclosing_func(start_byte, func_extents)
                if caller_id:
                    parent_cursor = cursor.semantic_parent
                    caller_scope = _build_scope(parent_cursor) if parent_cursor else None
                    calls.append(
                        {
                            "caller_id": caller_id,
                            "caller_file": rel_path,
                            "caller_scope": caller_scope,
                            "call_line": loc.line if loc else start_line,
                            "call_column": loc.column if loc else 0,
                            "call_start_byte": start_byte,
                            "call_branch_kind": "none",
                            "call_loop_depth": 0,
                            "call_control_frames_json": "[]",
                            "call_type": "call_expression",
                            "call_arity": sum(1 for _ in cursor.get_arguments()),
                            "callee_name": callee_name,
                            "callee_id": None,
                        }
                    )

        # -- CLASS / STRUCT / UNION / ENUM --------------------------------
        elif kind in _TYPE_KINDS and cursor.spelling:
            tkind = _TYPE_KINDS[kind]
            scope = _build_scope(cursor)
            name = cursor.spelling
            qualified = f"{scope}::{name}" if scope else name
            if qualified not in seen_type_ids:
                seen_type_ids.add(qualified)
                comment = _extract_leading_comment(source_text, start_line)
                types.append(
                    {
                        "symbol_id": qualified,
                        "qualified_name": qualified,
                        "name": name,
                        "kind": tkind,
                        "file_path": rel_path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "code": code,
                        "comment": comment,
                        "summary": comment,
                        "note": "",
                    }
                )
                # Emit CONTAINS relation for nested type definitions.
                parent_cursor = cursor.semantic_parent
                if (
                    parent_cursor is not None
                    and parent_cursor.kind in _TYPE_KINDS
                    and parent_cursor.spelling
                ):
                    p_scope = _build_scope(parent_cursor)
                    p_name = parent_cursor.spelling
                    p_qualified = f"{p_scope}::{p_name}" if p_scope else p_name
                    relations.append(
                        {
                            "source_id": p_qualified,
                            "source_label": "TypeDef",
                            "target_id": qualified,
                            "target_label": "TypeDef",
                            "rel_type": "CONTAINS",
                            "properties": {},
                        }
                    )

        # -- NAMESPACE ----------------------------------------------------
        elif kind == _ci.CursorKind.NAMESPACE and cursor.spelling:
            scope = _build_scope(cursor)
            name = cursor.spelling
            qualified = f"{scope}::{name}" if scope else name
            ns_id = f"namespace::{qualified}"
            if ns_id not in seen_ns_ids:
                seen_ns_ids.add(ns_id)
                comment = _extract_leading_comment(source_text, start_line)
                namespaces.append(
                    {
                        "symbol_id": ns_id,
                        "qualified_name": qualified,
                        "name": name,
                        "file_path": rel_path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "code": code,
                        "comment": comment,
                        "summary": comment,
                        "note": "",
                    }
                )

        # -- FIELD_DECL ---------------------------------------------------
        elif kind == _ci.CursorKind.FIELD_DECL and cursor.spelling:
            scope = _build_scope(cursor)
            name = cursor.spelling
            qualified = f"{scope}::{name}" if scope else name
            type_sig = ""
            try:
                type_sig = cursor.type.spelling if cursor.type else ""
            except Exception:
                pass
            fields.append(
                {
                    "symbol_id": f"field::{qualified}@{rel_path}",
                    "qualified_name": qualified,
                    "name": name,
                    "scope_name": scope or None,
                    "type_signature": type_sig,
                    "file_path": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "code": code,
                }
            )

        # -- ENUM_CONSTANT_DECL -------------------------------------------
        elif kind == _ci.CursorKind.ENUM_CONSTANT_DECL and cursor.spelling:
            # Emit enum constants as fields scoped under the parent enum.
            scope = _build_scope(cursor)
            name = cursor.spelling
            qualified = f"{scope}::{name}" if scope else name
            # Try to capture the constant value from the cursor's tokens.
            const_value = ""
            try:
                tokens = list(cursor.get_tokens())
                # Skip the enumerator name token, grab the value if present.
                if len(tokens) > 1:
                    const_value = " ".join(
                        t.spelling for t in tokens[1:]
                    ).strip()
            except Exception:
                pass
            fields.append(
                {
                    "symbol_id": f"field::{qualified}@{rel_path}",
                    "qualified_name": qualified,
                    "name": name,
                    "scope_name": scope or None,
                    "type_signature": const_value,
                    "file_path": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "code": code,
                }
            )

        # -- TYPEDEF / TYPE_ALIAS -----------------------------------------
        elif (
            kind in (_ci.CursorKind.TYPEDEF_DECL, _ci.CursorKind.TYPE_ALIAS_DECL)
            and cursor.spelling
        ):
            scope = _build_scope(cursor)
            name = cursor.spelling
            qualified = f"{scope}::{name}" if scope else name
            akind = "typedef" if kind == _ci.CursorKind.TYPEDEF_DECL else "using"
            target: Optional[str] = None
            try:
                target = cursor.underlying_typedef_type.spelling
            except Exception:
                pass
            aliases.append(
                {
                    "symbol_id": f"alias::{qualified}@{rel_path}",
                    "qualified_name": qualified,
                    "name": name,
                    "kind": akind,
                    "target_name": target,
                    "file_path": rel_path,
                    "start_line": start_line,
                    "end_line": end_line,
                    "code": code,
                }
            )
            # Also emit a function_type entry when the alias targets a
            # function-pointer / function-prototype type
            # (e.g. ``typedef void (*Callback)(int)``).
            if _ci is not None and target:
                try:
                    ut_kind = cursor.underlying_typedef_type.kind
                except Exception:
                    ut_kind = None
                if ut_kind in _FUNC_TYPE_KINDS:
                    ft_id = _function_type_id(target, rel_path)
                    function_types.append(
                        {
                            "symbol_id": ft_id,
                            "type_signature": target,
                            "file_path": rel_path,
                            "start_line": start_line,
                            "end_line": end_line,
                            "code": code,
                        }
                    )

    # -- Parse meta -------------------------------------------------------
    error_count = 0
    has_errors = False
    for diag in tu.diagnostics:
        if diag.severity >= _ci.Diagnostic.Error:
            error_count += 1
            has_errors = True

    parse_meta: Dict[str, Any] = {
        "parser_language": "clang",
        "parser_language_initial": "clang",
        "has_error": has_errors,
        "error_nodes": error_count,
        "error_nodes_initial": error_count,
        "header_retry_attempted": False,
        "header_retry_selected": False,
        "header_retry_error_nodes": None,
        "header_retry_has_error": None,
    }

    return {
        "functions": functions,
        "calls": sorted(calls, key=lambda c: (c["caller_id"], c["call_start_byte"])),
        "types": types,
        "namespaces": namespaces,
        "relations": relations,
        "function_types": function_types,
        "fields": fields,
        "aliases": aliases,
        "templates": templates,
        "file_def": {
            "file_path": rel_path,
            "start_line": 1,
            "end_line": file_lines,
            "code": source_text,
            "comment": file_comment,
            "summary": file_comment,
            "note": "",
        },
        "using_namespaces": using_namespaces,
        "using_imports": using_imports,
        "includes": includes,
        "macros": macros,
        "parse_meta": parse_meta,
    }

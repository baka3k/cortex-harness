from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from tree_sitter import Language, Parser


_PARSE_CACHE_VERSION = "vb-family-v2026-04-03-2"


@dataclass
class FunctionDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    class_name: Optional[str]
    namespace_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    arity: int
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
    namespace_name: Optional[str]
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
class FileDef:
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""
    imports: List[str] = None
    exports: List[str] = None


@dataclass
class PropertyDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    class_name: Optional[str]
    namespace_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    parameters: str
    return_type: str
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class EventDef:
    symbol_id: str
    qualified_name: str
    name: str
    class_name: Optional[str]
    namespace_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    parameters: str
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class InterfaceDef:
    symbol_id: str
    qualified_name: str
    name: str
    namespace_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    base_interfaces: List[str]
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class EnumDef:
    symbol_id: str
    qualified_name: str
    name: str
    namespace_name: Optional[str]
    class_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    members: List[Tuple[str, str]]
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class ConstantDef:
    symbol_id: str
    qualified_name: str
    name: str
    value: str
    type_name: str
    class_name: Optional[str]
    namespace_name: Optional[str]
    file_path: str
    line_number: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class VariableDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str
    is_global: bool
    is_shared: bool
    class_name: Optional[str]
    namespace_name: Optional[str]
    file_path: str
    line_number: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class CallEdge:
    caller_id: str
    caller_scope: Optional[str]
    callee_name: str
    callee_id: Optional[str]
    callee_arity: Optional[int]
    call_line: int = 0


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, str]


def _safe_rel(path: str) -> str:
    return (path or "").replace("\\", "/")


def _line_slice(lines: Sequence[str], start_line: int, end_line: int) -> str:
    if not lines:
        return ""
    start = max(1, start_line)
    end = max(start, end_line)
    return "\n".join(lines[start - 1 : end])


def _extract_file_comment(lines: Sequence[str]) -> str:
    parts: List[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            if parts:
                break
            continue
        if stripped.startswith("'"):
            parts.append(stripped)
            continue
        break
    return "\n".join(parts)


def _build_note(code: str, comment: str, summary: str) -> str:
    parts: List[str] = []
    if summary:
        parts.append(f"Summary:\n{summary}")
    if comment:
        parts.append(f"Comment:\n{comment}")
    if code:
        parts.append(f"Code:\n{code}")
    return "\n\n".join(parts)


def _normalize_callee(text: str) -> str:
    cleaned = re.sub(r"\s+", "", text or "")
    cleaned = cleaned.replace("?.", ".")
    return cleaned.strip(".")


def _split_params(text: str) -> List[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    chunks: List[str] = []
    current: List[str] = []
    depth = 0
    in_string = False
    quote = ""
    for ch in raw:
        if in_string:
            current.append(ch)
            if ch == quote:
                in_string = False
            continue
        if ch in {'"', "'"}:
            in_string = True
            quote = ch
            current.append(ch)
            continue
        if ch in "([":
            depth += 1
            current.append(ch)
            continue
        if ch in ")]":
            depth = max(depth - 1, 0)
            current.append(ch)
            continue
        if ch == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                chunks.append(token)
            current = []
            continue
        current.append(ch)
    token = "".join(current).strip()
    if token:
        chunks.append(token)
    return chunks


def _guess_arity(param_text: str) -> int:
    return len(_split_params(param_text))


def _tree_error_stats(tree: Any) -> Tuple[bool, int]:
    if tree is None:
        return False, 0
    has_error = bool(getattr(tree.root_node, "has_error", False))
    stack = [tree.root_node]
    error_count = 0
    while stack:
        node = stack.pop()
        if getattr(node, "type", "") == "ERROR":
            error_count += 1
        stack.extend(list(getattr(node, "children", [])))
    return has_error, error_count


def _build_ts_parser(language_obj: Any) -> Parser:
    language = language_obj
    if not isinstance(language, Language):
        try:
            language = Language(language)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Invalid tree-sitter language capsule") from exc

    parser = Parser()
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    elif hasattr(parser, "language"):
        parser.language = language
    else:
        try:
            parser = Parser(language)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Unsupported tree-sitter Parser API") from exc
    return parser


def get_vbnet_parser() -> Parser:
    try:
        import tree_sitter_vb_dotnet as vbnet

        return _build_ts_parser(vbnet.language())
    except Exception as exc:
        raise RuntimeError("VB.NET parser unavailable. Install tree-sitter-vb-dotnet.") from exc


def get_vb6_parser() -> Parser:
    try:
        import tree_sitter_vb6 as vb6

        return _build_ts_parser(vb6.language())
    except Exception as exc:
        raise RuntimeError("VB6 parser unavailable. Install tree-sitter-vb6.") from exc


def get_vba_parser() -> Parser:
    try:
        import tree_sitter_vba as vba

        return _build_ts_parser(vba.language())
    except Exception as exc:
        raise RuntimeError("VBA parser unavailable. Install tree-sitter-vba.") from exc


def get_vbscript_parser() -> Parser:
    try:
        import tree_sitter_vbscript as vbscript

        return _build_ts_parser(vbscript.language())
    except Exception as exc:
        raise RuntimeError("VBScript parser unavailable. Install tree-sitter-vbscript.") from exc


_NAMESPACE_START_RE = re.compile(r"^\s*(?:Public\s+|Private\s+|Friend\s+)?Namespace\s+([A-Za-z_][A-Za-z0-9_.]*)\b", re.IGNORECASE)
_NAMESPACE_END_RE = re.compile(r"^\s*End\s+Namespace\b", re.IGNORECASE)
_TYPE_START_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Partial\s+|Static\s+|Shadows\s+|Default\s+|NotInheritable\s+|MustInherit\s+|Global\s+)*"
    r"(Class|Module|Structure|Interface|Enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
_TYPE_END_RE = re.compile(r"^\s*End\s+(Class|Module|Structure|Interface|Enum)\b", re.IGNORECASE)
_FUNC_START_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Static\s+|Shared\s+|Overloads\s+|Overrides\s+|Overridable\s+|NotOverridable\s+|MustOverride\s+|Partial\s+|Default\s+|Async\s+|Iterator\s+|Shadows\s+|Global\s+)*"
    r"(Sub|Function|Property\s+Get|Property\s+Set|Property\s+Let)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*?)\))?",
    re.IGNORECASE,
)
_FUNC_END_RE = re.compile(r"^\s*End\s+(Sub|Function|Property)\b", re.IGNORECASE)
_IMPORTS_RE = re.compile(r"^\s*Imports\s+([A-Za-z_][A-Za-z0-9_.]*)\b", re.IGNORECASE)
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_.]*)\s*\(")
_CALL_KEYWORDS = {"if", "while", "for", "select", "return", "cint", "cstr", "cdbl", "ctype", "directcast", "trycast"}

_PROPERTY_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|ReadOnly\s+|WriteOnly\s+|Overrides\s+|Overridable\s+|MustOverride\s+|Default\s+|Shared\s+)*"
    r"Property\s+(?P<kind>Get|Set|Let)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?P<params>.*?)\))?"
    r"(?:\s+As\s+(?P<type>[A-Za-z0-9_.<>]+))?",
    re.IGNORECASE | re.MULTILINE,
)

_EVENT_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Shared\s+|Overrides\s+|Overridable\s+|Custom\s+)*"
    r"Event\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?P<params>.*?)\))?",
    re.IGNORECASE | re.MULTILINE,
)

# Variable/field declaration patterns
_VAR_DECL_RE = re.compile(
    r"^\s*(?P<scope>Public|Private|Friend|Protected|Global|Shared|Static|Dim|Const)\s+"
    r"(?P<with_events>WithEvents\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\((?:\d+|\s*(?:To)?\s*\d+(?:\s*,\s*\d+)*)\))?\s*"
    r"(?:As\s+(?P<type>[A-Za-z0-9_.()]+))?"
    r"(?:\s*=\s*(?P<init>[^'\n]+))?",
    re.IGNORECASE | re.MULTILINE,
)

# Array declaration pattern (e.g., "Dim arr(10) As Integer")
_ARRAY_DECL_RE = re.compile(
    r"^\s*(?P<scope>Public|Private|Friend|Protected|Global|Shared|Static|Dim)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<size>[^)]+)\)\s*"
    r"(?:As\s+(?P<type>[A-Za-z0-9_.()]+))?",
    re.IGNORECASE | re.MULTILINE,
)

_INTERFACE_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Partial\s+)*"
    r"Interface\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\s+(Inherits\s+(?P<bases>[A-Za-z0-9_,\s]+)))?",
    re.IGNORECASE | re.MULTILINE,
)

_ENUM_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+)*"
    r"Enum\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE | re.MULTILINE,
)

_CONST_RE = re.compile(
    r"^\s*(?:Public\s+|Private\s+|Friend\s+|Protected\s+|Global\s+)*"
    r"Const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?:As\s+(?P<type>[A-Za-z0-9_]+))?\s*=\s*(?P<value>[^'\n]+)",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_inline_comment(line: str) -> str:
    text = line
    out: List[str] = []
    in_str = False
    for ch in text:
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if ch == "'" and not in_str:
            break
        out.append(ch)
    return "".join(out)


def parse_vb_file(
    path: str,
    root: str,
    parser_factory: Callable[[], Parser],
    dialect: str,
    *,
    vbnet_parser_engine: str = "auto",
    vbnet_semantic: str = "auto",
    fallback_reason: str = "",
) -> Tuple[
    List[FunctionDef],
    List[CallEdge],
    List[ClassDef],
    List[NamespaceDef],
    List[RelationEdge],
    List[PropertyDef],
    List[EventDef],
    List[InterfaceDef],
    List[EnumDef],
    List[ConstantDef],
    List[VariableDef],
    FileDef,
    Dict[str, Any],
]:
    with open(path, "rb") as handle:
        source_bytes = handle.read()
    has_error, error_nodes = False, 0
    try:
        parser = parser_factory()
        tree = parser.parse(source_bytes)
        has_error, error_nodes = _tree_error_stats(tree)
    except Exception:
        pass  # tree-sitter parser unavailable; continue with regex-only parsing

    source = source_bytes.decode("utf-8", errors="ignore")
    lines = source.splitlines()
    rel_path = _safe_rel(os.path.relpath(path, root))

    namespaces: List[NamespaceDef] = []
    classes: List[ClassDef] = []
    functions: List[FunctionDef] = []
    calls: List[CallEdge] = []
    relations: List[RelationEdge] = []
    properties: List[PropertyDef] = []
    events: List[EventDef] = []
    interfaces: List[InterfaceDef] = []
    enums: List[EnumDef] = []
    constants: List[ConstantDef] = []
    variables: List[VariableDef] = []

    imports: List[str] = []
    for raw in lines:
        m = _IMPORTS_RE.match(raw)
        if m:
            imports.append(m.group(1))

    ns_stack: List[Tuple[str, int]] = []
    type_stack: List[Tuple[str, str, int]] = []
    func_stack: List[Dict[str, Any]] = []

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped:
            continue

        if _FUNC_END_RE.match(stripped) and func_stack:
            open_func = func_stack.pop()
            start_line = int(open_func["start_line"])
            end_line = idx
            code = _line_slice(lines, start_line, end_line)
            name = str(open_func["name"])
            kind = str(open_func["kind"])
            class_name = open_func.get("class_name")
            namespace_name = open_func.get("namespace_name")
            arity = int(open_func.get("arity", 0))

            parts = [part for part in [namespace_name, class_name, name] if part]
            qualified = ".".join(parts)
            symbol_id = f"{qualified}/{arity}@{rel_path}" if qualified else f"{name}/{arity}@{rel_path}"
            summary = ""
            comment = ""
            note = _build_note(code, comment, summary)
            functions.append(
                FunctionDef(
                    symbol_id=symbol_id,
                    qualified_name=qualified,
                    name=name,
                    kind=kind,
                    class_name=class_name,
                    namespace_name=namespace_name,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    arity=arity,
                    code=code,
                    comment=comment,
                    summary=summary,
                    note=note,
                )
            )

            # Extract simple call sites from function body.
            body_lines = lines[start_line - 1 : end_line]
            for offset, body_raw in enumerate(body_lines, start=0):
                no_comment = _strip_inline_comment(body_raw)
                if not no_comment.strip():
                    continue
                for match in _CALL_RE.finditer(no_comment):
                    candidate = _normalize_callee(match.group(1))
                    if not candidate:
                        continue
                    simple = candidate.split(".")[-1].lower()
                    if simple in _CALL_KEYWORDS:
                        continue
                    calls.append(
                        CallEdge(
                            caller_id=symbol_id,
                            caller_scope=qualified.rsplit(".", 1)[0] if "." in qualified else namespace_name,
                            callee_name=candidate,
                            callee_id=None,
                            callee_arity=None,
                            call_line=start_line + offset,
                        )
                    )
            continue

        if _TYPE_END_RE.match(stripped) and type_stack:
            type_name, type_kind, start_line = type_stack.pop()
            end_line = idx
            namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
            code = _line_slice(lines, start_line, end_line)
            qualified = ".".join([part for part in [namespace_name, type_name] if part])
            class_id = qualified or type_name
            summary = ""
            comment = ""
            note = _build_note(code, comment, summary)
            classes.append(
                ClassDef(
                    symbol_id=class_id,
                    qualified_name=qualified,
                    name=type_name,
                    kind=type_kind.lower(),
                    namespace_name=namespace_name,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    code=code,
                    comment=comment,
                    summary=summary,
                    note=note,
                )
            )
            continue

        if _NAMESPACE_END_RE.match(stripped) and ns_stack:
            ns_name, start_line = ns_stack.pop()
            end_line = idx
            code = _line_slice(lines, start_line, end_line)
            parent = ".".join(item[0] for item in ns_stack)
            qualified = ".".join([part for part in [parent, ns_name] if part])
            ns_id = f"namespace::{qualified}@{rel_path}"
            summary = ""
            comment = ""
            note = _build_note(code, comment, summary)
            namespaces.append(
                NamespaceDef(
                    symbol_id=ns_id,
                    qualified_name=qualified,
                    name=ns_name,
                    file_path=rel_path,
                    start_line=start_line,
                    end_line=end_line,
                    code=code,
                    comment=comment,
                    summary=summary,
                    note=note,
                )
            )
            continue

        m_ns = _NAMESPACE_START_RE.match(stripped)
        if m_ns:
            ns_stack.append((m_ns.group(1), idx))
            continue

        m_type = _TYPE_START_RE.match(stripped)
        if m_type:
            type_kind = m_type.group(1)
            type_name = m_type.group(2)
            type_stack.append((type_name, type_kind, idx))
            continue

        m_func = _FUNC_START_RE.match(stripped)
        if m_func:
            kind = m_func.group(1).lower()
            name = m_func.group(2)
            params = m_func.group(3) or ""
            namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
            class_name = ".".join(item[0] for item in type_stack) if type_stack else None
            func_stack.append(
                {
                    "kind": kind,
                    "name": name,
                    "arity": _guess_arity(params),
                    "start_line": idx,
                    "namespace_name": namespace_name,
                    "class_name": class_name,
                }
            )
            continue

    # close unbalanced blocks
    end_line = len(lines) if lines else 1
    while func_stack:
        open_func = func_stack.pop()
        start_line = int(open_func["start_line"])
        code = _line_slice(lines, start_line, end_line)
        name = str(open_func["name"])
        kind = str(open_func["kind"])
        class_name = open_func.get("class_name")
        namespace_name = open_func.get("namespace_name")
        arity = int(open_func.get("arity", 0))
        parts = [part for part in [namespace_name, class_name, name] if part]
        qualified = ".".join(parts)
        symbol_id = f"{qualified}/{arity}@{rel_path}" if qualified else f"{name}/{arity}@{rel_path}"
        note = _build_note(code, "", "")
        functions.append(
            FunctionDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                kind=kind,
                class_name=class_name,
                namespace_name=namespace_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                arity=arity,
                code=code,
                comment="",
                summary="",
                note=note,
            )
        )

    while type_stack:
        type_name, type_kind, start_line = type_stack.pop()
        namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
        code = _line_slice(lines, start_line, end_line)
        qualified = ".".join([part for part in [namespace_name, type_name] if part])
        class_id = qualified or type_name
        note = _build_note(code, "", "")
        classes.append(
            ClassDef(
                symbol_id=class_id,
                qualified_name=qualified,
                name=type_name,
                kind=type_kind.lower(),
                namespace_name=namespace_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=code,
                comment="",
                summary="",
                note=note,
            )
        )

    while ns_stack:
        ns_name, start_line = ns_stack.pop()
        parent = ".".join(item[0] for item in ns_stack)
        qualified = ".".join([part for part in [parent, ns_name] if part])
        code = _line_slice(lines, start_line, end_line)
        note = _build_note(code, "", "")
        namespaces.append(
            NamespaceDef(
                symbol_id=f"namespace::{qualified}@{rel_path}",
                qualified_name=qualified,
                name=ns_name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=code,
                comment="",
                summary="",
                note=note,
            )
        )

    # Extract properties
    for match in _PROPERTY_RE.finditer(source):
        kind = match.group("kind").lower()
        name = match.group("name")
        params = match.group("params") or ""
        return_type = match.group("type") or ""

        # Find line number
        match_start = match.start()
        line_num = 1
        for i, line in enumerate(lines):
            if source.find(line, match_start - len(line)) == match_start - len(line):
                line_num = i + 1
                break

        # Find end line (End Property)
        end_line_num = line_num
        for i in range(line_num, len(lines)):
            if re.search(r"^\s*End\s+Property\b", lines[i], re.IGNORECASE):
                end_line_num = i + 1
                break

        class_name = ".".join(item[0] for item in type_stack) if type_stack else None
        namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
        parts = [part for part in [namespace_name, class_name, name] if part]
        qualified = ".".join(parts)
        symbol_id = f"{qualified}@{rel_path}"
        code = _line_slice(lines, line_num, end_line_num) if end_line_num > line_num else match.group(0)

        properties.append(
            PropertyDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                kind=kind,
                class_name=class_name,
                namespace_name=namespace_name,
                file_path=rel_path,
                start_line=line_num,
                end_line=end_line_num,
                parameters=params,
                return_type=return_type,
                code=code,
                comment="",
                summary="",
                note=_build_note(code, "", ""),
            )
        )

    # Extract events
    for match in _EVENT_RE.finditer(source):
        name = match.group("name")
        params = match.group("params") or ""

        # Find line number
        match_start = match.start()
        line_num = 1
        for i, line in enumerate(lines):
            if source.find(line, match_start - len(line)) == match_start - len(line):
                line_num = i + 1
                break

        class_name = ".".join(item[0] for item in type_stack) if type_stack else None
        namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
        parts = [part for part in [namespace_name, class_name, name] if part]
        qualified = ".".join(parts)
        symbol_id = f"{qualified}@{rel_path}"
        code = match.group(0)

        events.append(
            EventDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                class_name=class_name,
                namespace_name=namespace_name,
                file_path=rel_path,
                start_line=line_num,
                end_line=line_num,
                parameters=params,
                code=code,
                comment="",
                summary="",
                note=_build_note(code, "", ""),
            )
        )

    # Extract interfaces (from TYPE_START_RE, but separate them)
    for match in _INTERFACE_RE.finditer(source):
        name = match.group("name")
        bases = match.group("bases") or ""

        # Find line number
        match_start = match.start()
        line_num = 1
        for i, line in enumerate(lines):
            if source.find(line, match_start - len(line)) == match_start - len(line):
                line_num = i + 1
                break

        # Find end line
        end_line_num = line_num
        for i in range(line_num, len(lines)):
            if re.search(r"^\s*End\s+Interface\b", lines[i], re.IGNORECASE):
                end_line_num = i + 1
                break

        namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
        qualified = ".".join([part for part in [namespace_name, name] if part])
        symbol_id = f"{qualified}@{rel_path}"
        base_list = [b.strip() for b in bases.split(",") if b.strip()] if bases else []
        code = _line_slice(lines, line_num, end_line_num) if end_line_num > line_num else match.group(0)

        interfaces.append(
            InterfaceDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                namespace_name=namespace_name,
                file_path=rel_path,
                start_line=line_num,
                end_line=end_line_num,
                base_interfaces=base_list,
                code=code,
                comment="",
                summary="",
                note=_build_note(code, "", ""),
            )
        )

    # Extract enums
    for match in _ENUM_RE.finditer(source):
        name = match.group("name")

        # Find line number
        match_start = match.start()
        line_num = 1
        for i, line in enumerate(lines):
            if source.find(line, match_start - len(line)) == match_start - len(line):
                line_num = i + 1
                break

        # Find end line and extract members
        end_line_num = line_num
        members = []
        for i in range(line_num, len(lines)):
            if re.search(r"^\s*End\s+Enum\b", lines[i], re.IGNORECASE):
                end_line_num = i + 1
                break
            # Extract enum members
            member_match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*([^'\n]+))?", lines[i])
            if member_match and i > line_num:
                member_name = member_match.group(1)
                member_value = member_match.group(2) or ""
                members.append((member_name, member_value))

        class_name = ".".join(item[0] for item in type_stack) if type_stack else None
        namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
        parts = [part for part in [namespace_name, class_name, name] if part]
        qualified = ".".join(parts)
        symbol_id = f"{qualified}@{rel_path}"
        code = _line_slice(lines, line_num, end_line_num) if end_line_num > line_num else match.group(0)

        enums.append(
            EnumDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                namespace_name=namespace_name,
                class_name=class_name,
                file_path=rel_path,
                start_line=line_num,
                end_line=end_line_num,
                members=members,
                code=code,
                comment="",
                summary="",
                note=_build_note(code, "", ""),
            )
        )

    # Extract constants
    for match in _CONST_RE.finditer(source):
        name = match.group("name")
        value = match.group("value").strip()
        type_name = match.group("type") or ""

        # Find line number
        match_start = match.start()
        line_num = 1
        for i, line in enumerate(lines):
            if source.find(line, match_start - len(line)) == match_start - len(line):
                line_num = i + 1
                break

        class_name = ".".join(item[0] for item in type_stack) if type_stack else None
        namespace_name = ".".join(item[0] for item in ns_stack) if ns_stack else None
        parts = [part for part in [namespace_name, class_name, name] if part]
        qualified = ".".join(parts)
        symbol_id = f"{qualified}@{rel_path}"
        code = match.group(0)

        constants.append(
            ConstantDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                value=value,
                type_name=type_name,
                class_name=class_name,
                namespace_name=namespace_name,
                file_path=rel_path,
                line_number=line_num,
                code=code,
                comment="",
                summary="",
                note=_build_note(code, "", ""),
            )
        )

    # Extract variables (module/class level declarations)
    for match in _VAR_DECL_RE.finditer(source):
        scope = match.group("scope").lower()
        name = match.group("name")
        type_name = match.group("type") or "Variant"
        init_value = match.group("init") or ""

        # Determine if global/shared
        is_global = scope in {"public", "global", "friend"}
        is_shared = scope == "shared"

        # Find line number
        match_start = match.start()
        line_num = 1
        for i, line in enumerate(lines):
            if source.find(line, match_start - len(line)) == match_start - len(line):
                line_num = i + 1
                break

        # Note: type_stack and ns_stack are empty at this point (after main loop)
        # This is a known limitation - following same pattern as constants/properties
        class_name = None
        namespace_name = None
        parts = [part for part in [namespace_name, class_name, name] if part]
        qualified = ".".join(parts) if parts else name
        symbol_id = f"{qualified}@{rel_path}"
        code = match.group(0)

        variables.append(
            VariableDef(
                symbol_id=symbol_id,
                qualified_name=qualified,
                name=name,
                type_name=type_name,
                is_global=is_global,
                is_shared=is_shared,
                class_name=class_name,
                namespace_name=namespace_name,
                file_path=rel_path,
                line_number=line_num,
                code=code,
                comment="",
                summary="",
                note=_build_note(code, "", ""),
            )
        )

    file_comment = _extract_file_comment(lines)
    file_summary = file_comment
    file_def = FileDef(
        file_path=rel_path,
        start_line=1,
        end_line=end_line,
        code=source,
        comment=file_comment,
        summary=file_summary,
        note=_build_note(source, file_comment, file_summary),
        imports=imports,
        exports=[],
    )

    parse_meta = {
        "parser_language": f"{dialect}_tree_sitter",
        "parse_cache_version": _PARSE_CACHE_VERSION,
        "has_error": has_error,
        "error_nodes": error_nodes,
        "line_count": end_line,
        "parser_engine": "regex",
        "semantic_mode": vbnet_semantic if dialect == "vbnet" else "off",
        "semantic_enabled": False,
        "fallback_reason": fallback_reason,
        "worker_elapsed_ms": 0,
        "workspace_kind": "none",
        "solution_or_project_path": "",
        "semantic_errors": [],
        "resolution_source": "syntax",
        "requested_engine": vbnet_parser_engine if dialect == "vbnet" else "regex",
    }

    return functions, calls, classes, namespaces, relations, properties, events, interfaces, enums, constants, variables, file_def, parse_meta


def resolve_calls(functions: Sequence[FunctionDef], calls: Sequence[CallEdge]) -> None:
    by_qualified: Dict[str, str] = {}
    by_simple: Dict[str, List[str]] = {}
    for func in functions:
        q_key = (func.qualified_name or "").strip().lower()
        if q_key:
            by_qualified[q_key] = func.symbol_id
        simple = (func.name or "").strip().lower()
        if not simple:
            continue
        by_simple.setdefault(simple, []).append(func.symbol_id)

    for call in calls:
        raw = (call.callee_name or "").strip()
        if not raw:
            continue
        q_key = raw.lower()
        target = by_qualified.get(q_key)
        if target is None:
            simple = raw.split(".")[-1].lower()
            candidates = by_simple.get(simple) or []
            if candidates:
                target = sorted(candidates)[0]
        if target:
            call.callee_id = target


def asdict_function(func: FunctionDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": func.symbol_id,
        "name": func.name,
        "qualified_name": func.qualified_name,
        "kind": func.kind,
        "scope_name": ".".join([part for part in [func.namespace_name, func.class_name] if part]) or "",
        "class_name": func.class_name,
        "package_name": func.namespace_name,
        "file_path": func.file_path,
        "start_line": func.start_line,
        "end_line": func.end_line,
        "arity": func.arity,
        "code": func.code,
        "comment": func.comment,
        "summary": func.summary,
        "note": func.note,
        "exported": False,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_class(cls: ClassDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": cls.symbol_id,
        "name": cls.name,
        "qualified_name": cls.qualified_name,
        "kind": cls.kind,
        "file_path": cls.file_path,
        "start_line": cls.start_line,
        "end_line": cls.end_line,
        "code": cls.code,
        "comment": cls.comment,
        "summary": cls.summary,
        "note": cls.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_namespace(ns: NamespaceDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": ns.symbol_id,
        "name": ns.name,
        "qualified_name": ns.qualified_name,
        "file_path": ns.file_path,
        "start_line": ns.start_line,
        "end_line": ns.end_line,
        "code": ns.code,
        "comment": ns.comment,
        "summary": ns.summary,
        "note": ns.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_file(file_def: FileDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": file_def.file_path,
        "path": file_def.file_path,
        "start_line": file_def.start_line,
        "end_line": file_def.end_line,
        "code": file_def.code,
        "comment": file_def.comment,
        "summary": file_def.summary,
        "note": file_def.note,
        "imports": list(file_def.imports or []),
        "exports": list(file_def.exports or []),
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_property(prop: PropertyDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": prop.symbol_id,
        "name": prop.name,
        "qualified_name": prop.qualified_name,
        "kind": f"property_{prop.kind}",
        "scope_name": ".".join([part for part in [prop.namespace_name, prop.class_name] if part]) or "",
        "class_name": prop.class_name,
        "package_name": prop.namespace_name,
        "file_path": prop.file_path,
        "start_line": prop.start_line,
        "end_line": prop.end_line,
        "parameters": prop.parameters,
        "return_type": prop.return_type,
        "code": prop.code,
        "comment": prop.comment,
        "summary": prop.summary,
        "note": prop.note,
        "exported": False,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_event(event: EventDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": event.symbol_id,
        "name": event.name,
        "qualified_name": event.qualified_name,
        "kind": "event",
        "scope_name": ".".join([part for part in [event.namespace_name, event.class_name] if part]) or "",
        "class_name": event.class_name,
        "package_name": event.namespace_name,
        "file_path": event.file_path,
        "start_line": event.start_line,
        "end_line": event.end_line,
        "parameters": event.parameters,
        "code": event.code,
        "comment": event.comment,
        "summary": event.summary,
        "note": event.note,
        "exported": False,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_interface(iface: InterfaceDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    import json
    return {
        "id": iface.symbol_id,
        "name": iface.name,
        "qualified_name": iface.qualified_name,
        "kind": "interface",
        "file_path": iface.file_path,
        "start_line": iface.start_line,
        "end_line": iface.end_line,
        "base_interfaces": json.dumps(iface.base_interfaces) if iface.base_interfaces else "[]",
        "code": iface.code,
        "comment": iface.comment,
        "summary": iface.summary,
        "note": iface.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_enum(enum: EnumDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    import json
    return {
        "id": enum.symbol_id,
        "name": enum.name,
        "qualified_name": enum.qualified_name,
        "kind": "enum",
        "scope_name": ".".join([part for part in [enum.namespace_name, enum.class_name] if part]) or "",
        "class_name": enum.class_name,
        "package_name": enum.namespace_name,
        "file_path": enum.file_path,
        "start_line": enum.start_line,
        "end_line": enum.end_line,
        "members": json.dumps(enum.members) if enum.members else "[]",
        "code": enum.code,
        "comment": enum.comment,
        "summary": enum.summary,
        "note": enum.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_constant(const: ConstantDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": const.symbol_id,
        "name": const.name,
        "qualified_name": const.qualified_name,
        "kind": "constant",
        "scope_name": ".".join([part for part in [const.namespace_name, const.class_name] if part]) or "",
        "class_name": const.class_name,
        "package_name": const.namespace_name,
        "file_path": const.file_path,
        "line_number": const.line_number,
        "value": const.value,
        "type_name": const.type_name,
        "code": const.code,
        "comment": const.comment,
        "summary": const.summary,
        "note": const.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


def asdict_variable(var: VariableDef, project_id: str, project_name: str, language: str, repo: str, build_system: str) -> Dict[str, Any]:
    return {
        "id": var.symbol_id,
        "name": var.name,
        "qualified_name": var.qualified_name,
        "kind": "variable",
        "scope_name": ".".join([part for part in [var.namespace_name, var.class_name] if part]) or "",
        "class_name": var.class_name,
        "package_name": var.namespace_name,
        "file_path": var.file_path,
        "line_number": var.line_number,
        "type_name": var.type_name,
        "is_global": var.is_global,
        "is_shared": var.is_shared,
        "code": var.code,
        "comment": var.comment,
        "summary": var.summary,
        "note": var.note,
        "project_id": project_id,
        "project_name": project_name,
        "language": language,
        "repo": repo,
        "build_system": build_system,
    }


PARSE_CACHE_VERSION = _PARSE_CACHE_VERSION

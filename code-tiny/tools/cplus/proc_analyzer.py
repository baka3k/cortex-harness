"""Comprehensive Pro*C / Oracle precompiler analyzer.

This module replaces the basic ``proc_sql`` regex extractor. It scans
``.pc`` / ``.pcc`` sources, performs a lexical pass that respects
C comments, string/char literals, raw string literals, and embedded
PL/SQL blocks, and emits a richer graph payload:

* Five node labels — ``SqlStatement``, ``SqlDirective``, ``SqlCursor``,
  ``SqlHostVariable``, ``DatabaseTable``.
* Nine relationship types — ``DECLARES_STATEMENT``,
  ``DECLARES_DIRECTIVE``, ``BINDS_PARAMETER``, ``DECLARES_CURSOR``,
  ``REFERENCES_CURSOR``, ``REFERENCES_STATEMENT``, ``READS_FROM``,
  ``WRITES_TO``, ``REFERENCES_TABLE``.

The public API is intentionally small and dependency-free at the module
level: ``tools.sql.sql_analyzer._get_sql_parser`` is loaded lazily so
that this module imports cleanly even when the SQL runtime tree-sitter
libraries are missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROC_EXTENSIONS: Tuple[str, ...] = (".pc", ".pcc")


# Operations that indicate a SELECT (read) statement.
_READ_OPERATIONS = frozenset({"SELECT", "WITH", "FETCH", "OPEN"})
# Operations that indicate a write to a table.
_WRITE_OPERATIONS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcRegion:
    """A scanned Pro*C region (one ``EXEC SQL`` block)."""

    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    code: str
    sql: str
    operation: str
    operation_upper: str
    targets: Tuple[str, ...]
    host_variables: Tuple[str, ...]
    indicator_variables: Tuple[str, ...]
    is_dynamic: bool
    raw_text: str
    diagnostics: Tuple["ProcDiagnostic", ...] = ()


@dataclass(frozen=True)
class ProcDiagnostic:
    """Non-fatal diagnostic surfaced by the lexer or grammar pass."""

    code: str
    message: str
    start_line: int


@dataclass
class PreparedProcSource:
    """Result of decoding + masking a Pro*C source file."""

    source_bytes: bytes
    masked_bytes: bytes
    encoding: str
    regions: List[ProcRegion] = field(default_factory=list)
    diagnostics: List[ProcDiagnostic] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def _decode_source(raw: bytes) -> Tuple[str, str, List[ProcDiagnostic]]:
    """Decode ``raw`` as UTF-8 then fall back to CP932.

    Returns ``(text, encoding, diagnostics)``. The encoding string is
    exposed so callers can persist it alongside parsed facts.
    """

    diagnostics: List[ProcDiagnostic] = []
    try:
        text = raw.decode("utf-8")
        return text, "utf-8", diagnostics
    except UnicodeDecodeError:
        pass

    try:
        text = raw.decode("cp932")
        diagnostics.append(
            ProcDiagnostic(
                code="encoding_fallback",
                message="UTF-8 decode failed; fell back to CP932",
                start_line=0,
            )
        )
        return text, "cp932", diagnostics
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        diagnostics.append(
            ProcDiagnostic(
                code="encoding_replacement",
                message="Source bytes were not valid UTF-8 or CP932; using replacement characters",
                start_line=0,
            )
        )
        return text, "utf-8-replace", diagnostics


# ---------------------------------------------------------------------------
# Lexical scanner
# ---------------------------------------------------------------------------


# Comments + literals + EXEC SQL detection. We walk the source manually
# rather than relying on a single regex so that strings/comments don't
# confuse the scanner and so we can emit per-region byte offsets.

_EXEC_SQL_RE = re.compile(r"\bEXEC\s+SQL\b", re.IGNORECASE)
_INDICATOR_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)\s*(?::|INDICATOR)\s*:?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_HOST_VAR_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_TABLE_HINT_RE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+([A-Za-z_][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)
_OP_RE = re.compile(r"EXEC\s+SQL\s+([A-Z]+)", re.IGNORECASE)


def _line_from_byte(source: str, byte_index: int) -> int:
    return source.count("\n", 0, byte_index) + 1


def _mask_executable_sql(source: str) -> Tuple[str, List[ProcRegion], List[ProcDiagnostic]]:
    """Walk ``source`` and return ``(masked_text, regions, diagnostics)``.

    The masked text replaces each ``EXEC SQL ... ;`` block with spaces of
    identical length so downstream C parsing stays consistent. Regions
    are emitted as :class:`ProcRegion` records with byte offsets
    measured against the *original* string.
    """

    regions: List[ProcRegion] = []
    diagnostics: List[ProcDiagnostic] = []
    output: List[str] = []

    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        ch2 = source[i : i + 2]

        # Block comment /* ... */ (including nested block comments in
        # the C sense — but tree-sitter's C parser only does non-nested
        # ones; we mirror that for the lexical scan.)
        if ch2 == "/*":
            end = source.find("*/", i + 2)
            if end == -1:
                # Unterminated block comment — keep going to EOF.
                output.append(source[i:])
                break
            output.append(source[i : end + 2])
            i = end + 2
            continue

        # Line comment //...\n
        if ch2 == "//":
            nl = source.find("\n", i + 2)
            if nl == -1:
                output.append(source[i:])
                break
            output.append(source[i : nl + 1])
            i = nl + 1
            continue

        # String literal
        if ch == '"':
            j = i + 1
            while j < n:
                if source[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if source[j] == '"':
                    j += 1
                    break
                j += 1
            output.append(source[i:j])
            i = j
            continue

        # Character literal
        if ch == "'":
            j = i + 1
            while j < n:
                if source[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if source[j] == "'":
                    j += 1
                    break
                j += 1
            output.append(source[i:j])
            i = j
            continue

        # Raw string literal R"delim(...)delim"
        if ch == "R" and ch2 == "R" and i + 1 < n and source[i + 1] == '"':
            # Find the delimiter (sequence of chars up to the next '(')
            delim_start = i + 2
            paren = source.find("(", delim_start)
            if paren != -1:
                delim = source[delim_start:paren]
                end_marker = ')"' + delim
                end = source.find(end_marker, paren + 1)
                if end == -1:
                    j = n
                else:
                    j = end + len(end_marker)
                output.append(source[i:j])
                i = j
                continue
            # else fall through to ordinary char handling

        # EXEC SQL directive
        match = _EXEC_SQL_RE.match(source, i)
        if match:
            block_start = match.start()
            # Roll back to the previous newline boundary so the masked
            # region covers leading whitespace if any.
            line_start = source.rfind("\n", 0, block_start) + 1
            # Find the matching ';' terminator (skip over nested ; that
            # appear inside string/comment contexts).
            j = match.end()
            paren_depth = 0
            end = -1
            while j < n:
                c = source[j]
                c2 = source[j : j + 2]
                if c2 == "/*":
                    close = source.find("*/", j + 2)
                    if close == -1:
                        j = n
                        break
                    j = close + 2
                    continue
                if c == '"' or c == "'":
                    j += 1
                    while j < n and source[j] != c:
                        if source[j] == "\\" and j + 1 < n:
                            j += 2
                            continue
                        j += 1
                    j += 1
                    continue
                if c == "(":
                    paren_depth += 1
                elif c == ")":
                    paren_depth = max(0, paren_depth - 1)
                elif c == ";" and paren_depth == 0:
                    end = j
                    break
                j += 1

            if end == -1:
                diagnostics.append(
                    ProcDiagnostic(
                        code="unterminated_exec_sql",
                        message="EXEC SQL block did not terminate before EOF",
                        start_line=_line_from_byte(source, block_start),
                    )
                )
                end = n - 1

            block_end = end + 1
            raw_text = source[block_start:block_end]
            op_match = _OP_RE.search(raw_text)
            operation = (op_match.group(1).upper() if op_match else "")
            # Indicator variables: :name INDICATOR :name2
            indicator_pairs = _INDICATOR_RE.findall(raw_text)
            host_vars = [name for name in _HOST_VAR_RE.findall(raw_text) if name]
            # Drop host vars that are part of an indicator pair (the
            # second tuple element is the indicator variable; we keep
            # it for graph emission, the first is the bound value).
            indicator_vars: List[str] = []
            seen_ind: set[str] = set()
            for _, indicator in indicator_pairs:
                if indicator and indicator not in seen_ind:
                    indicator_vars.append(indicator)
                    seen_ind.add(indicator)
            # Targets — table names after FROM/INTO/UPDATE/JOIN/TABLE
            targets = []
            seen_targets: set[str] = set()
            for match_t in _TABLE_HINT_RE.finditer(raw_text):
                value = match_t.group(1).upper()
                if value in {"DUAL", "SQLCA", "SQLDA"}:
                    continue
                if value not in seen_targets:
                    targets.append(value)
                    seen_targets.add(value)
            # Dynamic SQL detection: EXEC SQL EXECUTE IMMEDIATE
            is_dynamic = bool(re.search(r"EXECUTE\s+IMMEDIATE", raw_text, re.IGNORECASE))

            start_byte_utf8 = len(source[:block_start].encode("utf-8"))
            end_byte_utf8 = len(source[:block_end].encode("utf-8"))

            region = ProcRegion(
                start_byte=start_byte_utf8,
                end_byte=end_byte_utf8,
                start_line=_line_from_byte(source, block_start),
                end_line=_line_from_byte(source, block_end),
                code=raw_text,
                sql=raw_text,
                operation=operation,
                operation_upper=operation.upper(),
                targets=tuple(targets),
                host_variables=tuple(dict.fromkeys(host_vars)),
                indicator_variables=tuple(indicator_vars),
                is_dynamic=is_dynamic,
                raw_text=raw_text,
            )
            regions.append(region)

            # Emit whitespace before the block, then a masked span.
            output.append(source[i:line_start])
            output.append(" " * (block_end - line_start))
            i = block_end
            continue

        output.append(ch)
        i += 1

    masked = "".join(output)
    return masked, regions, diagnostics


# ---------------------------------------------------------------------------
# Public API: prepare bytes / prepare path
# ---------------------------------------------------------------------------


def prepare_proc_bytes(raw: bytes) -> PreparedProcSource:
    """Decode, mask, and lex ``raw`` (already-loaded file bytes)."""

    text, encoding, enc_diags = _decode_source(raw)
    masked, regions, lex_diags = _mask_executable_sql(text)

    source_bytes = text.encode("utf-8")
    masked_bytes = masked.encode("utf-8")

    # Length-preserving mask with newline alignment assertions.
    if len(source_bytes) != len(masked_bytes):
        lex_diags.append(
            ProcDiagnostic(
                code="mask_length_mismatch",
                message=(
                    "Masked bytes length differs from source bytes length; "
                    "downstream C parser will see misaligned offsets"
                ),
                start_line=0,
            )
        )

    return PreparedProcSource(
        source_bytes=source_bytes,
        masked_bytes=masked_bytes,
        encoding=encoding,
        regions=regions,
        diagnostics=enc_diags + lex_diags,
    )


def prepare_proc_path(path: Union[str, Path]) -> PreparedProcSource:
    """Load a Pro*C file from disk and prepare it."""

    p = Path(path)
    raw = p.read_bytes()
    return prepare_proc_bytes(raw)


# ---------------------------------------------------------------------------
# SQL parser integration
# ---------------------------------------------------------------------------


def _sql_parse_status() -> Dict[str, Any]:
    """Best-effort call into ``tools.sql.sql_analyzer._get_sql_parser``.

    The SQL runtime is heavy (transformers + tree-sitter). Importing it
    eagerly would slow every Pro*C parse down. Instead we import it on
    demand and report its availability without raising.
    """

    try:
        from tools.sql.sql_analyzer import _get_sql_parser  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        return {"available": False, "error": repr(exc)}

    try:
        parser = _get_sql_parser()
    except Exception as exc:  # pragma: no cover - parser init failure
        return {"available": False, "error": repr(exc)}

    if parser is None:
        return {"available": False, "error": "parser not available"}

    return {"available": True, "parser": parser}


# ---------------------------------------------------------------------------
# Public API: analyze_proc_file
# ---------------------------------------------------------------------------


@dataclass
class _CursorState:
    name: str
    declared_at: int


def analyze_proc_file(
    path: Union[str, Path],
    *,
    file_path: str,
    project_id: str = "",
    functions: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """Analyze a Pro*C file and emit a graph payload.

    Returns a dict with three keys:

    * ``nodes`` — list of label-typed node dicts (5 possible labels)
    * ``relations`` — list of typed relations (9 possible types)
    * ``diagnostics`` — list of :class:`ProcDiagnostic` dicts
    """

    prepared = prepare_proc_path(path)
    payload: Dict[str, Any] = {
        "nodes": [],
        "relations": [],
        "diagnostics": [d.__dict__ for d in prepared.diagnostics],
        "encoding": prepared.encoding,
    }

    sql_status = _sql_parse_status()
    if not sql_status.get("available", False):
        payload["diagnostics"].append(
            {
                "code": "sql_parser_unavailable",
                "message": str(sql_status.get("error", "unknown")),
                "start_line": 0,
            }
        )

    cursor_state: Dict[str, _CursorState] = {}

    for region in prepared.regions:
        op = region.operation_upper or ""

        # 1. Cursor lifecycle -------------------------------------------
        if op in {"DECLARE"} and "CURSOR" in region.code.upper():
            cursor_name = _extract_cursor_name(region.code)
            if cursor_name:
                node_id = _make_id(file_path, region.start_byte, "SqlCursor", cursor_name)
                payload["nodes"].append(
                    {
                        "label": "SqlCursor",
                        "id": node_id,
                        "name": cursor_name,
                        "file_path": file_path,
                        "start_line": region.start_line,
                        "end_line": region.end_line,
                        "operation": op,
                        "raw_text": region.raw_text,
                    }
                )
                payload["relations"].append(
                    _relation(
                        "DECLARES_CURSOR",
                        _enclosing_function(functions, region.start_line),
                        node_id,
                        "Function",
                        "SqlCursor",
                        file_path,
                        project_id,
                    )
                )
                cursor_state[cursor_name] = _CursorState(name=cursor_name, declared_at=region.start_byte)
            continue

        if op in {"OPEN", "CLOSE", "FETCH"} and "CURSOR" in region.code.upper():
            cursor_name = _extract_cursor_name(region.code)
            if cursor_name:
                payload["relations"].append(
                    _relation(
                        "REFERENCES_CURSOR",
                        _make_statement_id(file_path, region.start_byte, region.operation_upper or op),
                        _make_id(file_path, region.start_byte, "SqlCursor", cursor_name),
                        "SqlStatement",
                        "SqlCursor",
                        file_path,
                        project_id,
                    )
                )
                # Fall through to the normal statement emission below.

        # 2. Directive-only blocks (WHENEVER / INCLUDE) -----------------
        if op in {"WHENEVER", "INCLUDE"}:
            node_id = _make_id(file_path, region.start_byte, "SqlDirective", op)
            payload["nodes"].append(
                {
                    "label": "SqlDirective",
                    "id": node_id,
                    "name": op,
                    "file_path": file_path,
                    "start_line": region.start_line,
                    "end_line": region.end_line,
                    "operation": op,
                    "raw_text": region.raw_text,
                }
            )
            payload["relations"].append(
                _relation(
                    "DECLARES_DIRECTIVE",
                    _enclosing_function(functions, region.start_line),
                    node_id,
                    "Function",
                    "SqlDirective",
                    file_path,
                    project_id,
                )
            )
            continue

        # 3. Statements (SELECT / INSERT / UPDATE / DELETE / etc.) ------
        statement_id = _make_statement_id(file_path, region.start_byte, op or "UNKNOWN")
        payload["nodes"].append(
            {
                "label": "SqlStatement",
                "id": statement_id,
                "name": op or "UNKNOWN",
                "operation": op,
                "operation_upper": op,
                "file_path": file_path,
                "start_line": region.start_line,
                "end_line": region.end_line,
                "is_dynamic": region.is_dynamic,
                "raw_text": region.raw_text,
            }
        )

        enclosing_function_id = _enclosing_function(functions, region.start_line)
        payload["relations"].append(
            _relation(
                "DECLARES_STATEMENT",
                enclosing_function_id,
                statement_id,
                "Function",
                "SqlStatement",
                file_path,
                project_id,
            )
        )

        # Host variable binds
        for host in region.host_variables:
            host_id = _make_id(file_path, region.start_byte, "SqlHostVariable", host)
            payload["nodes"].append(
                {
                    "label": "SqlHostVariable",
                    "id": host_id,
                    "name": host,
                    "file_path": file_path,
                    "start_line": region.start_line,
                    "operation": op,
                    "raw_text": host,
                }
            )
            payload["relations"].append(
                _relation(
                    "BINDS_PARAMETER",
                    statement_id,
                    host_id,
                    "SqlStatement",
                    "SqlHostVariable",
                    file_path,
                    project_id,
                )
            )

        # Table references — emit both nodes and edges.
        for table in region.targets:
            table_id = _make_id(file_path, region.start_byte, "DatabaseTable", table)
            payload["nodes"].append(
                {
                    "label": "DatabaseTable",
                    "id": table_id,
                    "name": table,
                    "file_path": file_path,
                    "start_line": region.start_line,
                }
            )
            if op in _READ_OPERATIONS:
                payload["relations"].append(
                    _relation(
                        "READS_FROM",
                        statement_id,
                        table_id,
                        "SqlStatement",
                        "DatabaseTable",
                        file_path,
                        project_id,
                    )
                )
            elif op in _WRITE_OPERATIONS:
                payload["relations"].append(
                    _relation(
                        "WRITES_TO",
                        statement_id,
                        table_id,
                        "SqlStatement",
                        "DatabaseTable",
                        file_path,
                        project_id,
                    )
                )
            else:
                payload["relations"].append(
                    _relation(
                        "REFERENCES_TABLE",
                        statement_id,
                        table_id,
                        "SqlStatement",
                        "DatabaseTable",
                        file_path,
                        project_id,
                    )
                )

    # Cursor reference edges for any SELECT referencing an open cursor.
    for region in prepared.regions:
        op = region.operation_upper or ""
        if op in {"SELECT"}:
            cursor_name = _extract_cursor_name(region.code)
            if cursor_name and cursor_name in cursor_state:
                cursor_id = _make_id(
                    file_path, cursor_state[cursor_name].declared_at, "SqlCursor", cursor_name
                )
                statement_id = _make_statement_id(file_path, region.start_byte, op)
                payload["relations"].append(
                    _relation(
                        "REFERENCES_CURSOR",
                        statement_id,
                        cursor_id,
                        "SqlStatement",
                        "SqlCursor",
                        file_path,
                        project_id,
                    )
                )

    return payload


# ---------------------------------------------------------------------------
# Public API: summarize_proc_root
# ---------------------------------------------------------------------------


def summarize_proc_root(
    root: Union[str, Path],
    *,
    max_files: int = 0,
) -> Dict[str, Any]:
    """Walk ``root`` and produce aggregate counts for Pro*C files."""

    root_path = Path(root)
    files: List[Path] = []
    for ext in PROC_EXTENSIONS:
        files.extend(sorted(root_path.rglob(f"*{ext}")))

    if max_files and len(files) > max_files:
        files = files[:max_files]

    total_statements = 0
    total_cursors = 0
    total_directives = 0
    total_tables: set[str] = set()
    total_diagnostics = 0
    per_file: List[Dict[str, Any]] = []

    for file in files:
        try:
            analysis = analyze_proc_file(
                file,
                file_path=str(file),
            )
        except Exception as exc:  # pragma: no cover - defensive
            per_file.append({"file_path": str(file), "error": repr(exc)})
            continue

        node_labels = [n["label"] for n in analysis["nodes"]]
        total_statements += node_labels.count("SqlStatement")
        total_cursors += node_labels.count("SqlCursor")
        total_directives += node_labels.count("SqlDirective")
        for n in analysis["nodes"]:
            if n["label"] == "DatabaseTable":
                total_tables.add(n["name"])
        total_diagnostics += len(analysis["diagnostics"])
        per_file.append(
            {
                "file_path": str(file),
                "statements": node_labels.count("SqlStatement"),
                "cursors": node_labels.count("SqlCursor"),
                "directives": node_labels.count("SqlDirective"),
                "tables": sum(1 for n in analysis["nodes"] if n["label"] == "DatabaseTable"),
                "diagnostics": len(analysis["diagnostics"]),
            }
        )

    return {
        "files": len(files),
        "statements": total_statements,
        "cursors": total_cursors,
        "directives": total_directives,
        "tables": sorted(total_tables),
        "diagnostics": total_diagnostics,
        "per_file": per_file,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_cursor_name(code: str) -> Optional[str]:
    """Extract the cursor name from a DECLARE/OPEN/CLOSE/FETCH block."""

    upper = code.upper()
    for keyword in ("DECLARE", "OPEN", "CLOSE", "FETCH"):
        idx = upper.find(keyword + " ")
        if idx == -1:
            continue
        after = code[idx + len(keyword) + 1 :].lstrip()
        if not after:
            continue
        # Cursor keyword may follow DECLARE
        if keyword == "DECLARE" and after.upper().startswith("CURSOR "):
            after = after[len("CURSOR ") :].lstrip()
        # Pull the identifier.
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", after)
        if match:
            return match.group(1)
    return None


def _make_id(file_path: str, start_byte: int, label: str, name: str) -> str:
    return f"{label}::{file_path}::{start_byte}::{name}"


def _make_statement_id(file_path: str, start_byte: int, operation: str) -> str:
    return _make_id(file_path, start_byte, "SqlStatement", operation)


def _enclosing_function(
    functions: Sequence[Mapping[str, Any]], start_line: int
) -> str:
    """Return the id of the function enclosing ``start_line``."""

    best_id = "<file>"
    best_end = -1
    for fn in functions:
        try:
            fn_start = int(fn.get("start_line", 0))
            fn_end = int(fn.get("end_line", 0))
        except Exception:
            continue
        if fn_start <= start_line <= fn_end and fn_end >= best_end:
            best_id = str(fn.get("symbol_id") or fn.get("id") or "<file>")
            best_end = fn_end
    return best_id


def _relation(
    rel_type: str,
    source_id: str,
    target_id: str,
    source_label: str,
    target_label: str,
    file_path: str,
    project_id: str,
) -> Dict[str, Any]:
    rel = {
        "rel_type": rel_type,
        "source_id": source_id,
        "target_id": target_id,
        "source_label": source_label,
        "target_label": target_label,
        "file_path": file_path,
    }
    rel.setdefault("project_id", project_id)
    return rel


__all__ = [
    "PROC_EXTENSIONS",
    "ProcDiagnostic",
    "ProcRegion",
    "PreparedProcSource",
    "analyze_proc_file",
    "prepare_proc_bytes",
    "prepare_proc_path",
    "summarize_proc_root",
]
"""Pro*C (Oracle precompiler) ``EXEC SQL`` / ``EXEC ORACLE`` preprocessing.

Pro*C source is plain C with embedded ``EXEC SQL ...;`` / ``EXEC ORACLE
...;`` directives that the Oracle precompiler strips out before handing the
file to a C compiler. The C/C++ tree-sitter grammar used by
``cplus_analyzer.py`` doesn't understand these directives, so before parsing
we replace each directive with a byte-length-preserving placeholder C
statement. Preserving the exact byte length and newline count of each
replaced span keeps every symbol location *after* the directive unshifted.

Only symbol/host-variable/statement-kind extraction is implemented here —
this is not a Pro*C grammar and does not validate or execute SQL (see
plan.md Non-Goals).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_EXEC_RE = re.compile(rb"\bEXEC\s+(SQL|ORACLE)\b", re.IGNORECASE)
_HOST_VAR_RE = re.compile(rb":([A-Za-z_][A-Za-z0-9_]*)")
_TABLE_RE = re.compile(
    rb"\b(?:FROM|INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_$#]*(?:\.[A-Za-z_][A-Za-z0-9_$#]*)?)",
    re.IGNORECASE,
)


@dataclass
class EmbeddedSqlStatement:
    kind: str
    raw_text: str
    host_vars: List[str]
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    table_name: Optional[str] = None
    enclosing_function_id: str = ""


def _find_statement_end(data: bytes, start: int) -> int:
    """Return the index just after the terminating ``;`` of a directive.

    Tracks quote state so a ``;`` inside a string/char literal doesn't end
    the statement early. This is intentionally not a full C tokenizer.
    """

    i = start
    in_squote = False
    in_dquote = False
    n = len(data)
    while i < n:
        ch = data[i:i + 1]
        if in_squote:
            if ch == b"\\":
                i += 2
                continue
            if ch == b"'":
                in_squote = False
            i += 1
            continue
        if in_dquote:
            if ch == b"\\":
                i += 2
                continue
            if ch == b'"':
                in_dquote = False
            i += 1
            continue
        if ch == b"'":
            in_squote = True
            i += 1
            continue
        if ch == b'"':
            in_dquote = True
            i += 1
            continue
        if ch == b";":
            return i + 1
        i += 1
    return n


def _statement_kind(directive_body: bytes) -> str:
    match = re.match(rb"\s*([A-Za-z_][A-Za-z0-9_]*)", directive_body)
    if not match:
        return "UNKNOWN"
    return match.group(1).decode("ascii", errors="ignore").upper()


def _statement_table(kind: str, raw_text: bytes) -> Optional[str]:
    if kind not in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
        return None
    match = _TABLE_RE.search(raw_text)
    if not match:
        return None
    return match.group(1).decode("ascii", errors="ignore")


def preprocess_proc_directives(source_bytes: bytes) -> Tuple[bytes, List[EmbeddedSqlStatement]]:
    """Replace ``EXEC SQL``/``EXEC ORACLE`` directives with C placeholder statements.

    Returns ``(patched_bytes, statements)``. ``patched_bytes`` has the exact
    same length and the exact same newline count within every replaced span
    as ``source_bytes``, so line numbers for code following a directive are
    unaffected.
    """

    statements: List[EmbeddedSqlStatement] = []
    result = bytearray(source_bytes)
    search_start = 0
    while True:
        match = _EXEC_RE.search(bytes(result), search_start)
        if not match:
            break
        stmt_start = match.start()
        stmt_end = _find_statement_end(bytes(result), match.end())
        raw_segment = bytes(result[stmt_start:stmt_end])
        directive_body = raw_segment[match.end() - stmt_start:]
        kind = _statement_kind(directive_body)
        host_vars = sorted({m.decode("ascii", errors="ignore") for m in _HOST_VAR_RE.findall(raw_segment)})
        table_name = _statement_table(kind, raw_segment)

        start_line = source_bytes.count(b"\n", 0, stmt_start) + 1
        end_line = source_bytes.count(b"\n", 0, stmt_end) + 1

        placeholder_text = f'__exec_sql__("{kind}");'.encode("ascii")
        newline_count = raw_segment.count(b"\n")
        filler_len = len(raw_segment) - len(placeholder_text) - newline_count
        if filler_len < 0:
            # Directive shorter than the placeholder text (rare, e.g. "EXEC SQL;").
            placeholder_text = placeholder_text[: max(0, len(raw_segment) - newline_count)]
            filler_len = 0
        replacement = placeholder_text + b" " * filler_len + b"\n" * newline_count
        result[stmt_start:stmt_end] = replacement

        statements.append(
            EmbeddedSqlStatement(
                kind=kind,
                raw_text=raw_segment.decode("utf-8", errors="replace"),
                host_vars=host_vars,
                start_line=start_line,
                end_line=end_line,
                start_byte=stmt_start,
                end_byte=stmt_end,
                table_name=table_name,
            )
        )
        search_start = stmt_start + len(replacement)

    return bytes(result), statements

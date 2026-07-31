"""Line/regex-based structural extraction for POSIX shell (`.sh`) scripts.

Deliberately does not use a full shell grammar: the batch scripts targeted by
this analyzer are simple, sequential `KEY=value` + command-invocation style
scripts (JP1/AJS batch chains), not deeply nested control-flow programs.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import List, Optional, Tuple

from tools.common.text_encoding import decode_source_bytes
from tools.shell.models import (
    RelationEdge,
    ShellCallEdge,
    ShellConfigRead,
    ShellFunctionDef,
    ShellScriptFile,
    ShellVariable,
)


_FUNCTION_DEF_RE = re.compile(
    r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{?\s*$"
)
_FUNCTION_END_RE = re.compile(r"^\s*\}\s*$")
_VARIABLE_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$"
)
_CONFIG_READ_RE = re.compile(
    r"grep\s+['\"]?(?P<key>[A-Za-z0-9_.\-]+)['\"]?\s+(?P<path>[\"'][^\"']+[\"']|\S+)"
    r".*\|\s*awk\b"
)
_SHELL_INVOKE_RE = re.compile(
    r"(?:^|\s)(?:sh\s+|\.\s+|source\s+)?(?P<target>(?:\$\{?[A-Za-z0-9_]+\}?/)?[A-Za-z0-9_./${}]+\.sh)\b"
)
_BINARY_INVOKE_RE = re.compile(
    r"(?:^|\s)(?:\$\{?[A-Za-z0-9_]+\}?/)?(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s*(?:\$|\"|'|\s|$)"
)
_COMMENT_RE = re.compile(r"^\s*#(.*)$")
_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case",
    "esac", "in", "function", "return", "exit", "echo", "test", "export",
    "local", "readonly", "shift", "break", "continue", "set", "unset",
}


def _stable_id(kind: str, symbol_id: str) -> str:
    return f"{kind}::{uuid.uuid5(uuid.NAMESPACE_URL, symbol_id)}"


def _extract_leading_comment(lines: List[str], func_start_idx: int) -> str:
    comments: List[str] = []
    idx = func_start_idx - 1
    while idx >= 0:
        match = _COMMENT_RE.match(lines[idx])
        if not match:
            break
        comments.insert(0, match.group(1).strip())
        idx -= 1
    return "\n".join(comments)


def _find_function_bodies(lines: List[str]) -> List[Tuple[str, int, int]]:
    """Return (name, start_line, end_line) 1-based inclusive spans."""
    functions: List[Tuple[str, int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        match = _FUNCTION_DEF_RE.match(lines[i])
        if match:
            name = match.group(1)
            start_line = i + 1
            depth = 0
            has_open_brace = "{" in lines[i]
            j = i
            if not has_open_brace:
                # brace on its own line
                j += 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and lines[j].strip() == "{":
                    depth = 1
                    j += 1
                else:
                    i += 1
                    continue
            else:
                depth = 1
                j += 1
            while j < n and depth > 0:
                depth += lines[j].count("{")
                depth -= lines[j].count("}")
                j += 1
            end_line = j
            functions.append((name, start_line, end_line))
            i = j
        else:
            i += 1
    return functions


def _enclosing_function(functions: List[Tuple[str, int, int]], line: int) -> str:
    best: Optional[Tuple[str, int, int]] = None
    for name, start, end in functions:
        if start <= line <= end:
            if best is None or (end - start) < (best[2] - best[1]):
                best = (name, start, end)
    return best[0] if best else ""


def parse_shell_file(path: str, root: str) -> ShellScriptFile:
    with open(path, "rb") as handle:
        raw = handle.read()
    code, encoding, lossy = decode_source_bytes(raw)
    rel_path = os.path.relpath(path, root)
    lines = code.split("\n")
    line_count = len(lines)

    file_comment_lines: List[str] = []
    for line in lines:
        match = _COMMENT_RE.match(line)
        if not match:
            break
        if line.strip().startswith("#!"):
            continue
        file_comment_lines.append(match.group(1).strip())
    file_comment = "\n".join(file_comment_lines)

    function_spans = _find_function_bodies(lines)
    functions: List[ShellFunctionDef] = []
    for name, start_line, end_line in function_spans:
        func_code = "\n".join(lines[start_line - 1 : end_line])
        qualified_name = f"{rel_path}::{name}"
        symbol_id = _stable_id("function", f"{rel_path}:{name}:{start_line}")
        functions.append(
            ShellFunctionDef(
                symbol_id=symbol_id,
                qualified_name=qualified_name,
                name=name,
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                code=func_code,
                comment=_extract_leading_comment(lines, start_line - 1),
            )
        )

    variables: List[ShellVariable] = []
    config_reads: List[ShellConfigRead] = []
    call_edges: List[ShellCallEdge] = []

    for idx, line in enumerate(lines):
        line_no = idx + 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        config_match = _CONFIG_READ_RE.search(line)
        if config_match:
            config_reads.append(
                ShellConfigRead(
                    config_key=config_match.group("key"),
                    ini_path_expr=config_match.group("path").strip("\"'"),
                    line=line_no,
                    enclosing_function=_enclosing_function(function_spans, line_no),
                )
            )
            continue

        var_match = _VARIABLE_ASSIGN_RE.match(line)
        if var_match and var_match.group(1) not in _KEYWORDS:
            variables.append(
                ShellVariable(
                    name=var_match.group(1),
                    raw_expr=var_match.group(2).strip(),
                    line=line_no,
                )
            )
            continue

        shell_match = _SHELL_INVOKE_RE.search(line)
        if shell_match:
            call_edges.append(
                ShellCallEdge(
                    callee_ref=shell_match.group("target"),
                    line=line_no,
                    enclosing_function=_enclosing_function(function_spans, line_no),
                )
            )
            continue

    return ShellScriptFile(
        file_path=rel_path,
        code=code,
        comment=file_comment,
        start_line=1,
        end_line=line_count,
        source_encoding=encoding,
        source_encoding_lossy=lossy,
        functions=functions,
        variables=variables,
        config_reads=config_reads,
        call_edges=call_edges,
    )


def build_relations(script: ShellScriptFile) -> List[RelationEdge]:
    """Translate config-read/call-edge facts into generic graph relations."""
    relations: List[RelationEdge] = []
    func_by_name = {f.name: f for f in script.functions}

    def _source(enclosing_function: str) -> Tuple[str, str]:
        if enclosing_function and enclosing_function in func_by_name:
            return func_by_name[enclosing_function].symbol_id, "Function"
        return _stable_id("file", script.file_path), "File"

    for read in script.config_reads:
        source_id, source_label = _source(read.enclosing_function)
        properties = {
            "config_key": read.config_key,
            "ini_path_expr": read.ini_path_expr,
            "line": str(read.line),
        }
        if "${" in read.ini_path_expr:
            # Templated path (e.g. "${DIR}/${KEY}.ini"): can't resolve to a
            # single ConfigEntry without runtime values, so scope the edge to
            # the static directory prefix instead of guessing a file.
            static_prefix = read.ini_path_expr.split("${", 1)[0].rstrip("/")
            target_id = _stable_id("config_dir", static_prefix or read.ini_path_expr)
            target_label = "ConfigDirectory"
            properties["note"] = "templated path, resolved to directory scope"
            properties["dir_path"] = static_prefix
        else:
            target_id = _stable_id(
                "config_entry", f"{read.ini_path_expr}:{read.config_key}"
            )
            target_label = "ConfigEntry"
        relations.append(
            RelationEdge(
                source_id=source_id,
                source_label=source_label,
                target_id=target_id,
                target_label=target_label,
                rel_type="READS_CONFIG",
                properties=properties,
            )
        )

    for edge in script.call_edges:
        source_id, source_label = _source(edge.enclosing_function)
        target_id = _stable_id("shell_call", edge.callee_ref)
        relations.append(
            RelationEdge(
                source_id=source_id,
                source_label=source_label,
                target_id=target_id,
                target_label="File",
                rel_type="CALLS",
                properties={
                    "callee_ref": edge.callee_ref,
                    "line": str(edge.line),
                },
            )
        )

    return relations

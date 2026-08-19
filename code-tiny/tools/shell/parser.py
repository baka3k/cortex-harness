from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from .models import (
    ShellDiagnostic,
    ShellFile,
    ShellFunction,
    ShellInvocation,
    ShellRelation,
)


_FUNCTION_RE = re.compile(
    r"^\s*(?:function\s+([A-Za-z_][A-Za-z0-9_]*)\s*|([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\))\s*\{"
)
_ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|([^\s#;]+))")
_SOURCE_RE = re.compile(r"^\s*(?:\.|source|sh|bash)\s+([^\s;&|]+\.sh)\b")
_DIRECT_RE = re.compile(r"^\s*(\.?\.?/[^\s;&|]+\.sh|\$\{[A-Za-z_][A-Za-z0-9_]*\}\.sh)\b")
_GREP_RE = re.compile(
    r"\bgrep\s+(?:-[A-Za-z]+\s+)*(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))\s+(?:\"([^\"]+)\"|'([^']+)'|([^\s|;&]+))"
)
_VARIABLE_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_SHELL_BUILTINS = {
    ".", ":", "alias", "bg", "break", "cd", "command", "continue",
    "echo", "eval", "exec", "exit", "export", "false", "fc", "fg",
    "getopts", "hash", "jobs", "kill", "local", "printf", "pwd", "read",
    "readonly", "return", "set", "shift", "source", "test", "times", "trap",
    "true", "type", "typeset", "ulimit", "umask", "unalias", "unset", "wait",
}
_CONTROL_WORDS = {
    "case", "do", "done", "elif", "else", "esac", "fi", "for", "function",
    "in", "select", "then", "until", "while", "{", "}",
}
_LEADING_CONDITIONALS = {"if", "elif", "until", "while"}
_COMMAND_WRAPPERS = {"env", "nohup"}
_SHELL_INTERPRETERS = {"bash", "dash", "ksh", "sh", "zsh"}
_ASSIGNMENT_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _substitute(raw: str, variables: dict[str, str]) -> tuple[str, bool]:
    unresolved = False

    def replace(match: re.Match[str]) -> str:
        nonlocal unresolved
        name = match.group(1) or match.group(2)
        if name not in variables:
            unresolved = True
            return match.group(0)
        return variables[name]

    return _VARIABLE_RE.sub(replace, raw), not unresolved


def _resolve_path(raw: str, file_path: str, project_root: str, variables: dict[str, str]) -> tuple[str, bool]:
    substituted, complete = _substitute(raw.strip('"\''), variables)
    root_real = os.path.realpath(project_root)
    candidates = [
        Path(project_root, Path(file_path).parent, substituted),
        Path(project_root, substituted),
    ]
    for candidate in candidates:
        normalized = os.path.realpath(candidate)
        try:
            if os.path.commonpath((root_real, normalized)) != root_real:
                continue
        except ValueError:
            continue
        if os.path.isfile(normalized):
            return os.path.relpath(normalized, root_real).replace("\\", "/"), complete
    return substituted.replace("\\", "/"), False


def _command_segments(line: str) -> tuple[list[list[str]], str | None]:
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        tokens = list(lexer)
    except ValueError as exc:
        return [], str(exc)
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and all(character in ";&|" for character in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments, None


def _command_token(segment: list[str]) -> str | None:
    tokens = list(segment)
    while tokens and _ASSIGNMENT_TOKEN_RE.match(tokens[0]):
        tokens.pop(0)
    if not tokens:
        return None
    if tokens[0] in _LEADING_CONDITIONALS:
        tokens.pop(0)
    elif tokens[0] in _CONTROL_WORDS:
        return None
    while tokens and tokens[0] in _COMMAND_WRAPPERS | {"command", "exec"}:
        tokens.pop(0)
        while tokens and tokens[0].startswith("-"):
            tokens.pop(0)
        while tokens and _ASSIGNMENT_TOKEN_RE.match(tokens[0]):
            tokens.pop(0)
    return tokens[0] if tokens else None


def _command_name(raw_command: str, variables: dict[str, str]) -> tuple[str, bool]:
    substituted, complete = _substitute(raw_command.strip('"\''), variables)
    without_variables = _VARIABLE_RE.sub("", substituted).rstrip("/")
    name = os.path.basename(without_variables).strip()
    dynamic = not complete or not name
    return name, dynamic


def _functions(lines: list[str], file_path: str) -> list[ShellFunction]:
    functions: list[ShellFunction] = []
    active: ShellFunction | None = None
    brace_depth = 0
    for line_number, line in enumerate(lines, 1):
        match = _FUNCTION_RE.match(line)
        if match and active is None:
            name = match.group(1) or match.group(2)
            active = ShellFunction(
                symbol_id=f"shell-function::{file_path}:{name}:{line_number}",
                name=name,
                file_path=file_path,
                start_line=line_number,
                end_line=line_number,
                code=line,
            )
            functions.append(active)
            brace_depth = line.count("{") - line.count("}")
            continue
        if active is not None:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                functions[-1] = ShellFunction(
                    **{**active.__dict__, "end_line": line_number}
                )
                active = None
    return functions


def parse_shell_text(source_text: str, *, file_path: str, project_root: str) -> ShellFile:
    lines = source_text.splitlines()
    variables: dict[str, str] = {}
    functions = _functions(lines, file_path)
    functions_by_name = {function.name: function for function in functions}
    invocations: list[ShellInvocation] = []
    relations: list[ShellRelation] = []
    diagnostics: list[ShellDiagnostic] = []

    for line_number, line in enumerate(lines, 1):
        assignment = _ASSIGNMENT_RE.match(line)
        if assignment:
            variables[assignment.group(1)] = next(
                value for value in assignment.groups()[1:] if value is not None
            )

        active_function = next(
            (
                function
                for function in functions
                if function.start_line < line_number < function.end_line
            ),
            None,
        )

        source_id = active_function.symbol_id if active_function else file_path
        source_label = "ShellFunction" if active_function else "ShellScript"
        invocation = _SOURCE_RE.match(line) or _DIRECT_RE.match(line)
        if invocation:
            raw_target = invocation.group(1)
            target_id, resolved = _resolve_path(raw_target, file_path, project_root, variables)
            relations.append(
                ShellRelation(source_id, source_label, target_id, "ShellScript", "CALLS", line_number, raw_target, resolved)
            )
            if not resolved:
                diagnostics.append(
                    ShellDiagnostic("shell-call-unresolved", f"Unable to resolve {raw_target}", file_path, line_number)
                )

        grep_match = _GREP_RE.search(line)
        if grep_match:
            raw_path = next(value for value in grep_match.groups()[3:] if value is not None)
            substituted, complete = _substitute(raw_path, variables)
            if substituted.lower().endswith(".ini") or ".ini" in substituted.lower():
                target_id, resolved = _resolve_path(raw_path, file_path, project_root, variables)
                relations.append(
                    ShellRelation(source_id, source_label, target_id, "File", "REFERENCES", line_number, raw_path, resolved and complete)
                )
                if not resolved or not complete:
                    diagnostics.append(
                        ShellDiagnostic("shell-ini-unresolved", f"Unable to resolve {raw_path}", file_path, line_number)
                    )

        if assignment or _FUNCTION_RE.match(line):
            continue
        segments, token_error = _command_segments(line)
        if token_error:
            diagnostics.append(
                ShellDiagnostic(
                    "shell-tokenize-unresolved",
                    token_error,
                    file_path,
                    line_number,
                )
            )
            continue
        for ordinal, segment in enumerate(segments, 1):
            raw_command = _command_token(segment)
            if not raw_command:
                continue
            name, dynamic = _command_name(raw_command, variables)
            if name in _SHELL_BUILTINS or name in _CONTROL_WORDS:
                continue
            if invocation and (name in _SHELL_INTERPRETERS or name.endswith(".sh")):
                continue
            internal = functions_by_name.get(name) if name else None
            if internal is not None:
                relations.append(
                    ShellRelation(
                        source_id,
                        source_label,
                        internal.symbol_id,
                        "ShellFunction",
                        "CALLS",
                        line_number,
                        raw_command,
                        True,
                    )
                )
                continue
            invocations.append(
                ShellInvocation(
                    symbol_id=f"shell-invocation::{file_path}:{line_number}:{ordinal}",
                    source_id=source_id,
                    source_label=source_label,
                    file_path=file_path,
                    line=line_number,
                    ordinal=ordinal,
                    raw_command=raw_command,
                    command_name=name,
                    dynamic=dynamic,
                )
            )

    return ShellFile(
        file_path=file_path,
        line_count=max(1, len(lines)),
        encoding="",
        functions=tuple(functions),
        invocations=tuple(invocations),
        relations=tuple(relations),
        diagnostics=tuple(diagnostics),
    )

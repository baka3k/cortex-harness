from __future__ import annotations

import os
import re
from pathlib import Path

from .models import ShellDiagnostic, ShellFile, ShellFunction, ShellRelation


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


def parse_shell_text(source_text: str, *, file_path: str, project_root: str) -> ShellFile:
    lines = source_text.splitlines()
    variables: dict[str, str] = {}
    functions: list[ShellFunction] = []
    relations: list[ShellRelation] = []
    diagnostics: list[ShellDiagnostic] = []
    active_function: ShellFunction | None = None
    brace_depth = 0

    for line_number, line in enumerate(lines, 1):
        assignment = _ASSIGNMENT_RE.match(line)
        if assignment:
            variables[assignment.group(1)] = next(
                value for value in assignment.groups()[1:] if value is not None
            )

        function_match = _FUNCTION_RE.match(line)
        if function_match:
            name = function_match.group(1) or function_match.group(2)
            active_function = ShellFunction(
                symbol_id=f"shell-function::{file_path}:{name}:{line_number}",
                name=name,
                file_path=file_path,
                start_line=line_number,
                end_line=line_number,
                code=line,
            )
            functions.append(active_function)
            brace_depth = line.count("{") - line.count("}")
        elif active_function:
            brace_depth += line.count("{") - line.count("}")
            if brace_depth <= 0:
                functions[-1] = ShellFunction(
                    **{**active_function.__dict__, "end_line": line_number}
                )
                active_function = None

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

    return ShellFile(
        file_path=file_path,
        line_count=max(1, len(lines)),
        encoding="",
        functions=tuple(functions),
        relations=tuple(relations),
        diagnostics=tuple(diagnostics),
    )
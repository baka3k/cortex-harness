from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from tools.servlet_jsp.models import Diagnostic, ResourceBudgets, SourceSpan
from tools.servlet_jsp.path_resolver import read_bounded_file, resolve_project_path


_REFERENCE_RE = re.compile(r"\$\{([^{}]+)\}")
_TARGET_RE = re.compile(
    r"(?:^|/)(?:WEB-INF/.*|[^/?#]+\.(?:jsp|jspx|jspf|html?|css|js|mjs|map|png|jpe?g|gif|svg|ico|woff2?|ttf|xml))(?:[?#].*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PropertyEntry:
    key: str
    value: str
    raw_value: str
    source: SourceSpan


@dataclass(frozen=True)
class PropertyReference:
    source_key: str
    target_key: str
    default_value: str
    raw: str
    source: SourceSpan


@dataclass(frozen=True)
class PropertyTarget:
    source_key: str
    raw_value: str
    resolved_path: str
    classification: str
    resolution_status: str
    source: SourceSpan


@dataclass(frozen=True)
class PropertiesParseResult:
    file_path: str
    entries: Tuple[PropertyEntry, ...] = ()
    values: Dict[str, str] = field(default_factory=dict)
    resolved_values: Dict[str, str] = field(default_factory=dict)
    references: Tuple[PropertyReference, ...] = ()
    targets: Tuple[PropertyTarget, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    truncated: bool = False
    complete: bool = True

def parse_properties_file(
    root: str,
    file_path: str,
    *,
    budgets: Optional[ResourceBudgets] = None,
) -> PropertiesParseResult:
    """Parse a Java properties file after a root-confined, bounded read."""

    effective = budgets or ResourceBudgets()
    resolution = resolve_project_path(root, file_path, require_exists=True)
    if resolution.status != "resolved":
        return PropertiesParseResult(
            file_path=file_path,
            diagnostics=(
                Diagnostic(
                    "servlet_jsp.properties.path_rejected",
                    resolution.message or f"Unable to read properties file: {resolution.status}",
                    "error",
                    file_path,
                ),
            ),
            complete=False,
        )
    try:
        payload, truncated = read_bounded_file(resolution.absolute_path, effective.max_properties_bytes)
    except OSError as exc:
        return PropertiesParseResult(
            file_path=resolution.relative_path,
            diagnostics=(
                Diagnostic(
                    "servlet_jsp.properties.read_error",
                    str(exc),
                    "error",
                    resolution.relative_path,
                ),
            ),
            complete=False,
        )

    diagnostics: List[Diagnostic] = []
    if truncated:
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.properties.byte_budget",
                f"Properties byte budget {effective.max_properties_bytes} reached",
                "warning",
                resolution.relative_path,
            )
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("latin-1")
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.properties.legacy_encoding",
                "Properties file is not UTF-8; decoded as ISO-8859-1",
                "info",
                resolution.relative_path,
            )
        )

    entries: List[PropertyEntry] = []
    values: Dict[str, str] = {}
    references: List[PropertyReference] = []
    for start_line, end_line, logical in _logical_lines(text, diagnostics, resolution.relative_path):
        stripped = logical.lstrip(" \t\f")
        if not stripped or stripped.startswith(("#", "!")):
            continue
        raw_key, raw_value = _split_property(stripped)
        key = _unescape(raw_key, diagnostics, resolution.relative_path, start_line, "key")
        value = _unescape(raw_value, diagnostics, resolution.relative_path, start_line, "value")
        source = SourceSpan(resolution.relative_path, start_line, end_line)
        if not key:
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.properties.empty_key",
                    "Properties entry has an empty key",
                    "warning",
                    resolution.relative_path,
                    start_line,
                    end_line,
                )
            )
            continue
        if key in values:
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.properties.duplicate_key",
                    f"Duplicate property key {key!r}; the last occurrence is effective",
                    "warning",
                    resolution.relative_path,
                    start_line,
                    end_line,
                )
            )
        entry = PropertyEntry(key=key, value=value, raw_value=raw_value, source=source)
        entries.append(entry)
        values[key] = value
        for match in _REFERENCE_RE.finditer(value):
            reference_key, default = _split_reference(match.group(1))
            references.append(
                PropertyReference(
                    source_key=key,
                    target_key=reference_key,
                    default_value=default,
                    raw=match.group(0),
                    source=source,
                )
            )

    resolved_values, closure_diagnostics = _resolve_reference_closure(
        values,
        entries,
        resolution.relative_path,
        effective.max_include_depth,
    )
    diagnostics.extend(closure_diagnostics)
    targets = _inventory_targets(root, resolution.relative_path, entries, resolved_values, diagnostics)
    return PropertiesParseResult(
        file_path=resolution.relative_path,
        entries=tuple(entries),
        values=dict(values),
        resolved_values=resolved_values,
        references=tuple(references),
        targets=tuple(targets),
        diagnostics=tuple(diagnostics),
        truncated=truncated,
        complete=not truncated and not any(item.severity == "error" for item in diagnostics),
    )


def _logical_lines(
    text: str, diagnostics: List[Diagnostic], file_path: str
) -> List[Tuple[int, int, str]]:
    logical: List[Tuple[int, int, str]] = []
    pending = ""
    start_line = 1
    continued = False
    lines = text.splitlines()
    for line_number, physical in enumerate(lines, start=1):
        if not continued:
            pending = ""
            start_line = line_number
        else:
            physical = physical.lstrip(" \t\f")
        slash_count = len(physical) - len(physical.rstrip("\\"))
        if slash_count % 2 == 1:
            pending += physical[:-1]
            continued = True
            continue
        pending += physical
        logical.append((start_line, line_number, pending))
        pending = ""
        continued = False
    if continued:
        logical.append((start_line, max(start_line, len(lines)), pending))
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.properties.unclosed_continuation",
                "Properties file ends during a continued logical line",
                "warning",
                file_path,
                start_line,
                max(start_line, len(lines)),
            )
        )
    return logical


def _split_property(line: str) -> Tuple[str, str]:
    escaped = False
    key_end = len(line)
    separator = -1
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"=", ":"} or char.isspace():
            key_end = index
            separator = index
            break
    if separator < 0:
        return line, ""
    cursor = separator
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    if cursor < len(line) and line[cursor] in {"=", ":"}:
        cursor += 1
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    return line[:key_end], line[cursor:]


def _unescape(
    value: str,
    diagnostics: List[Diagnostic],
    file_path: str,
    line: int,
    role: str,
) -> str:
    output: List[str] = []
    index = 0
    escapes = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
    while index < len(value):
        if value[index] != "\\":
            output.append(value[index])
            index += 1
            continue
        index += 1
        if index >= len(value):
            output.append("\\")
            break
        marker = value[index]
        if marker == "u":
            digits = value[index + 1 : index + 5]
            if len(digits) == 4 and all(char in "0123456789abcdefABCDEF" for char in digits):
                output.append(chr(int(digits, 16)))
                index += 5
                continue
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.properties.invalid_unicode_escape",
                    f"Invalid Unicode escape in property {role}",
                    "warning",
                    file_path,
                    line,
                    line,
                )
            )
            output.append("\\u")
            index += 1
            continue
        output.append(escapes.get(marker, marker))
        index += 1
    return "".join(output)


def _split_reference(value: str) -> Tuple[str, str]:
    key, separator, default = value.partition(":")
    return key.strip(), default if separator else ""


def _resolve_reference_closure(
    values: Dict[str, str],
    entries: List[PropertyEntry],
    file_path: str,
    max_depth: int,
) -> Tuple[Dict[str, str], List[Diagnostic]]:
    diagnostics: List[Diagnostic] = []
    sources = {item.key: item.source for item in entries}
    cache: Dict[str, str] = {}
    reported_cycles: set[Tuple[str, ...]] = set()

    def resolve(key: str, stack: Tuple[str, ...]) -> str:
        if key in cache:
            return cache[key]
        if key in stack:
            cycle = stack[stack.index(key) :] + (key,)
            canonical = tuple(sorted(set(cycle)))
            if canonical not in reported_cycles:
                reported_cycles.add(canonical)
                source = sources.get(key, SourceSpan(file_path))
                diagnostics.append(
                    Diagnostic(
                        "servlet_jsp.properties.reference_cycle",
                        "Property reference cycle: " + " -> ".join(cycle),
                        "warning",
                        file_path,
                        source.start_line,
                        source.end_line,
                    )
                )
            return values.get(key, "")
        if len(stack) >= max_depth:
            source = sources.get(key, SourceSpan(file_path))
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.properties.reference_depth",
                    f"Property reference depth budget {max_depth} reached",
                    "warning",
                    file_path,
                    source.start_line,
                    source.end_line,
                )
            )
            return values.get(key, "")
        raw = values.get(key, "")

        def replace(match: re.Match[str]) -> str:
            target, default = _split_reference(match.group(1))
            if target in values:
                return resolve(target, stack + (key,))
            return default if default else match.group(0)

        resolved = _REFERENCE_RE.sub(replace, raw)
        cache[key] = resolved
        return resolved

    for key in sorted(values):
        resolve(key, ())
    return cache, diagnostics


def _inventory_targets(
    root: str,
    file_path: str,
    entries: List[PropertyEntry],
    resolved_values: Dict[str, str],
    diagnostics: List[Diagnostic],
) -> List[PropertyTarget]:
    targets: List[PropertyTarget] = []
    for entry in entries:
        value = resolved_values.get(entry.key, entry.value).strip()
        if not value or not (_TARGET_RE.search(value) or value.startswith(("http://", "https://"))):
            continue
        if _REFERENCE_RE.search(value):
            targets.append(PropertyTarget(entry.key, value, "", "dynamic", "dynamic", entry.source))
            continue
        classification = "context_relative" if value.startswith("/") else "relative"
        resolution = resolve_project_path(
            root,
            value,
            base_file=file_path,
            web_root_relative=value.startswith("/"),
            require_exists=False,
        )
        if resolution.status == "external":
            classification = "external"
        elif resolution.status in {"rejected", "invalid"}:
            classification = "rejected"
            diagnostics.append(
                Diagnostic(
                    "servlet_jsp.properties.target_rejected",
                    resolution.message,
                    "warning",
                    file_path,
                    entry.source.start_line,
                    entry.source.end_line,
                )
            )
        targets.append(
            PropertyTarget(
                source_key=entry.key,
                raw_value=value,
                resolved_path=resolution.relative_path,
                classification=classification,
                resolution_status=resolution.status,
                source=entry.source,
            )
        )
    return targets

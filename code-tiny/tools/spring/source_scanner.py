from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Sequence, Tuple

from tools.spring.annotation_catalog import short_annotation_name
from tools.spring.detector import read_limited
from tools.spring.models import SourceSpan
from tools.spring.value_resolver import parse_annotation_args


_ANNOTATION_START_RE = re.compile(r"@(?:[A-Za-z_]\w*:)?[A-Za-z_][\w.]*")
_CLASS_RE = re.compile(
    r"\b(?:(?:public|private|protected|abstract|final|open|data|sealed|enum|value)\s+)*"
    r"(class|interface|record|object|enum\s+class)\s+([A-Za-z_]\w*)"
    r"(?P<tail>[^{;\n]*)"
)
_KOTLIN_FUN_RE = re.compile(r"\b(?:suspend\s+)?fun\s+(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?::\s*([A-Za-z_][\w.<>,? ]*))?")
_JAVA_METHOD_RE = re.compile(
    r"\b(?:(?:public|private|protected|static|final|abstract|default|synchronized|native|open|override)\s+)*"
    r"(?P<ret>[A-Za-z_][\w.<>,?\[\] ]+)\s+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)"
)
_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][\w.]*)(?:\s*;)?", re.MULTILINE)


@dataclass(frozen=True)
class SourceAnnotation:
    name: str
    raw_args: str
    raw: str
    line: int
    args: dict = field(default_factory=dict)

    @property
    def short_name(self) -> str:
        return short_annotation_name(self.name)


@dataclass(frozen=True)
class SourceMethod:
    name: str
    return_type: str
    params: str
    annotations: Tuple[SourceAnnotation, ...]
    source: SourceSpan
    code: str
    class_name: str = ""
    package_name: str = ""
    language: str = ""
    file_path: str = ""

    @property
    def qualified_name(self) -> str:
        parts = [part for part in (self.package_name, self.class_name, self.name) if part]
        return ".".join(parts)

    @property
    def arity(self) -> int:
        return _count_parameters(self.params)

    @property
    def symbol_id(self) -> str:
        return f"{self.qualified_name}/{self.arity}@{self.file_path}"


@dataclass(frozen=True)
class SourceClass:
    name: str
    declaration_kind: str
    annotations: Tuple[SourceAnnotation, ...]
    source: SourceSpan
    header: str
    code: str
    methods: Tuple[SourceMethod, ...]
    language: str
    file_path: str
    package_name: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.package_name}.{self.name}" if self.package_name else self.name

    @property
    def symbol_id(self) -> str:
        return self.qualified_name


@dataclass(frozen=True)
class SourceUnit:
    language: str
    file_path: str
    package_name: str
    classes: Tuple[SourceClass, ...]
    top_level_methods: Tuple[SourceMethod, ...]
    text: str


def scan_source_units(root: str, rel_paths: Iterable[str]) -> List[SourceUnit]:
    units: List[SourceUnit] = []
    for rel_path in sorted(set(rel_paths)):
        if rel_path.endswith(".java"):
            language = "java"
        elif rel_path.endswith((".kt", ".kts")):
            language = "kotlin"
        else:
            continue
        text = read_limited(os.path.join(root, rel_path), limit=2 * 1024 * 1024)
        units.append(scan_source_text(text=text, file_path=rel_path, language=language))
    return units


def scan_source_text(*, text: str, file_path: str, language: str) -> SourceUnit:
    lines = text.splitlines()
    package = _extract_package(text)
    class_ranges = _find_class_ranges(lines)
    classes: List[SourceClass] = []
    for start_idx, end_idx, class_match in class_ranges:
        start_line = start_idx + 1
        end_line = end_idx + 1
        annotations = _collect_preceding_annotations(lines, start_idx)
        class_name = class_match.group(2)
        class_code = "\n".join(lines[start_idx : end_idx + 1])
        methods = tuple(_find_methods(lines, start_idx, end_idx, file_path, language, class_name, package))
        classes.append(
            SourceClass(
                name=class_name,
                declaration_kind=class_match.group(1).replace(" ", "_"),
                annotations=tuple(annotations),
                source=SourceSpan(file_path, start_line, end_line),
                header=lines[start_idx].strip(),
                code=class_code,
                methods=methods,
                language=language,
                file_path=file_path,
                package_name=package,
            )
        )

    top_methods = tuple(_find_methods(lines, 0, len(lines) - 1, file_path, language, "", package, class_ranges=class_ranges))
    return SourceUnit(
        language=language,
        file_path=file_path,
        package_name=package,
        classes=tuple(classes),
        top_level_methods=top_methods,
        text=text,
    )


def _extract_package(text: str) -> str:
    match = _PACKAGE_RE.search(text)
    return match.group(1) if match else ""


def _find_class_ranges(lines: Sequence[str]) -> List[Tuple[int, int, re.Match[str]]]:
    ranges: List[Tuple[int, int, re.Match[str]]] = []
    for idx, line in enumerate(lines):
        match = _CLASS_RE.search(line)
        if not match:
            continue
        end_idx = _find_block_end(lines, idx)
        ranges.append((idx, end_idx, match))
    return ranges


def _find_methods(
    lines: Sequence[str],
    start_idx: int,
    end_idx: int,
    file_path: str,
    language: str,
    class_name: str,
    package_name: str,
    class_ranges: Sequence[Tuple[int, int, re.Match[str]]] = (),
) -> List[SourceMethod]:
    methods: List[SourceMethod] = []
    skip_ranges = [(s, e) for s, e, _ in class_ranges]
    idx = start_idx
    while idx <= end_idx:
        if any(s <= idx <= e for s, e in skip_ranges):
            idx += 1
            continue
        signature, signature_end = _signature_window(lines, idx)
        match = _KOTLIN_FUN_RE.search(signature) if language == "kotlin" else None
        return_type = ""
        params = ""
        name = ""
        if match:
            name = match.group(1)
            params = match.group(2)
            return_type = (match.group(3) or "").strip()
        else:
            java_match = _JAVA_METHOD_RE.search(signature)
            if java_match:
                name = java_match.group("name")
                params = java_match.group("params")
                return_type = java_match.group("ret").strip()
        if not name:
            idx += 1
            continue
        if name in {"if", "for", "while", "switch", "catch", "return", "new"}:
            idx += 1
            continue
        body_end = max(_find_block_end(lines, idx), signature_end)
        annotations = tuple(_collect_preceding_annotations(lines, idx))
        methods.append(
            SourceMethod(
                name=name,
                return_type=return_type,
                params=params,
                annotations=annotations,
                source=SourceSpan(file_path, idx + 1, body_end + 1),
                code="\n".join(lines[idx : body_end + 1]),
                class_name=class_name,
                package_name=package_name,
                language=language,
                file_path=file_path,
            )
        )
        idx = max(idx + 1, body_end + 1)
    return methods


def _signature_window(lines: Sequence[str], start_idx: int) -> Tuple[str, int]:
    chunks: List[str] = []
    depth = 0
    saw_paren = False
    for idx in range(start_idx, min(len(lines), start_idx + 25)):
        line = _strip_line_comment(lines[idx]).strip()
        if not line:
            if not chunks:
                return "", start_idx
            break
        chunks.append(line)
        depth += line.count("(")
        if "(" in line:
            saw_paren = True
        depth -= line.count(")")
        if saw_paren and depth <= 0:
            return " ".join(chunks), idx
        if not saw_paren and any(token in line for token in ("{", ";", "=")):
            break
    return " ".join(chunks), start_idx


def _collect_preceding_annotations(lines: Sequence[str], declaration_idx: int) -> List[SourceAnnotation]:
    annotations: List[SourceAnnotation] = []
    idx = declaration_idx - 1
    buffer: List[str] = []
    start_line = declaration_idx
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped:
            idx -= 1
            continue
        if not stripped.startswith("@"):
            break
        buffer.insert(0, stripped)
        start_line = idx + 1
        idx -= 1
    for offset, raw in enumerate(buffer):
        annotations.extend(_parse_annotation_line(raw, start_line + offset))
    inline_prefix = lines[declaration_idx].split("{", 1)[0]
    if inline_prefix.strip().startswith("@"):
        annotations.extend(_parse_annotation_line(inline_prefix, declaration_idx + 1))
    return annotations


def _parse_annotation_line(raw: str, line: int) -> List[SourceAnnotation]:
    result: List[SourceAnnotation] = []
    for match in _ANNOTATION_START_RE.finditer(raw):
        name = match.group(0)[1:]
        tail = raw[match.end() :].lstrip()
        args = ""
        if tail.startswith("("):
            args = _balanced_prefix(tail)
        raw_text = f"@{name}{args}"
        result.append(
            SourceAnnotation(
                name=name,
                raw_args=args,
                raw=raw_text,
                line=line,
                args=parse_annotation_args(args),
            )
        )
    return result


def _balanced_prefix(text: str) -> str:
    depth = 0
    quote = ""
    escaped = False
    out: List[str] = []
    for char in text:
        out.append(char)
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth <= 0:
                break
    return "".join(out)


def _find_block_end(lines: Sequence[str], start_idx: int) -> int:
    depth = 0
    saw_open = False
    for idx in range(start_idx, len(lines)):
        line = _strip_line_comment(lines[idx])
        depth += line.count("{")
        if "{" in line:
            saw_open = True
        depth -= line.count("}")
        if saw_open and depth <= 0:
            return idx
    return start_idx


def _strip_line_comment(line: str) -> str:
    return line.split("//", 1)[0]


def _count_parameters(params: str) -> int:
    text = (params or "").strip()
    if not text:
        return 0
    return len([item for item in _split_top_level_commas(text) if item.strip()])


def _split_top_level_commas(text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    quote = ""
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "({[<":
            depth += 1
        elif char in ")}]>":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return parts

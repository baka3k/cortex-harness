from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_STRING_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'')
_NAMED_ARG_RE = re.compile(r"([A-Za-z_][\w.-]*)\s*=\s*([^,]+(?:\{[^}]*\})?)")
_PLACEHOLDER_RE = re.compile(r"\$\{([^}:]+)(?::([^}]+))?\}")


def strip_quotes(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def extract_string_literals(text: str) -> List[str]:
    values: List[str] = []
    for match in _STRING_RE.finditer(text or ""):
        values.append(match.group(1) if match.group(1) is not None else match.group(2))
    return values


def parse_annotation_args(raw_args: str) -> Dict[str, Any]:
    text = (raw_args or "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    result: Dict[str, Any] = {}
    if not text:
        return result
    for key, value in _NAMED_ARG_RE.findall(text):
        result[key] = parse_value(value)
    if not result:
        result["value"] = parse_value(text)
    return result


def parse_value(value: str) -> Any:
    text = (value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        return [parse_value(item) for item in _split_top_level(text[1:-1])]
    strings = extract_string_literals(text)
    if len(strings) == 1 and text in {f'"{strings[0]}"', f"'{strings[0]}'"}:
        return strings[0]
    if strings and text.startswith("{"):
        return strings
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return strip_quotes(text)


def first_arg(args: Dict[str, Any], *names: str, default: str = "") -> Any:
    for name in names:
        if name in args:
            return args[name]
    return default


def list_arg(args: Dict[str, Any], *names: str) -> List[str]:
    value: Any = first_arg(args, *names, default=[])
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def resolve_placeholders(value: str, config_index: Dict[str, List[Any]]) -> Tuple[str, str]:
    raw = str(value or "")
    status = "resolved"

    def repl(match: re.Match[str]) -> str:
        nonlocal status
        key = match.group(1)
        default = match.group(2)
        candidates = config_index.get(key) or []
        if candidates:
            return str(candidates[-1])
        if default is not None:
            status = "defaulted"
            return default
        status = "unresolved"
        return match.group(0)

    return _PLACEHOLDER_RE.sub(repl, raw), status


def normalize_path(path: str) -> str:
    text = strip_quotes(str(path or "").strip())
    if not text:
        return "/"
    if not text.startswith("/"):
        text = "/" + text
    text = re.sub(r"/+", "/", text)
    return text.rstrip("/") or "/"


def combine_paths(prefixes: Sequence[str], suffixes: Sequence[str]) -> List[str]:
    base = list(prefixes) or [""]
    tail = list(suffixes) or [""]
    out: List[str] = []
    for left in base:
        for right in tail:
            out.append(normalize_path("/".join([left.strip("/"), right.strip("/")]).strip("/")))
    return sorted(set(out))


def _split_top_level(text: str) -> List[str]:
    items: List[str] = []
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
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items

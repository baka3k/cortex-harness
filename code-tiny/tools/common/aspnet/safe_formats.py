from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from .identity import resolve_inside_root


SENSITIVE_KEY_RE = re.compile(
    r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential|authorization|"
    r"connectionstring|private[_-]?key|machinekey|cookie|session(?:id)?)",
    re.IGNORECASE,
)
_CONNECTION_SECRET_RE = re.compile(
    r"(?i)(password|pwd|user\s*id|uid|access\s*token)\s*=\s*([^;]+)"
)


def redact_value(key: str, value: Any, max_length: int = 4096) -> Any:
    if SENSITIVE_KEY_RE.search(str(key or "")):
        return "[REDACTED]"
    if isinstance(value, str):
        value = _CONNECTION_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
        return value if len(value) <= max_length else value[:max_length] + "...[TRUNCATED]"
    if isinstance(value, Mapping):
        name_fields = {"name", "key", "config_key", "raw_name"}
        value_fields = {"value", "config_value", "raw_value", "resolved_value"}
        sensitive_record = any(
            str(item_key).lower() in name_fields
            and isinstance(item_value, str)
            and SENSITIVE_KEY_RE.search(item_value)
            for item_key, item_value in value.items()
        )
        result: Dict[str, Any] = {}
        for nested_key, nested_value in sorted(value.items(), key=lambda item: str(item[0])):
            normalized = str(nested_key)
            if sensitive_record and normalized.lower() in name_fields | value_fields:
                result[normalized] = "[REDACTED]"
            else:
                result[normalized] = redact_value(normalized, nested_value, max_length)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(key, item, max_length) for item in value]
    return value


def graph_property_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        rows = list(value)
        if all(item is None or isinstance(item, (str, int, float, bool)) for item in rows):
            return rows
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def read_bounded_text(root: str, path: str, max_bytes: int) -> tuple[str, str, bool]:
    absolute, relative = resolve_inside_root(root, path, require_exists=True)
    with open(absolute, "rb") as handle:
        payload = handle.read(max(1, int(max_bytes)) + 1)
    truncated = len(payload) > max_bytes
    payload = payload[:max_bytes]
    return payload.decode("utf-8", errors="replace"), relative, truncated


def parse_json_file(root: str, path: str, max_bytes: int = 1024 * 1024) -> tuple[Any, str, bool, Tuple[str, ...]]:
    text, relative, truncated = read_bounded_text(root, path, max_bytes)
    duplicates: list[str] = []

    def object_pairs(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_pairs), relative, truncated, tuple(sorted(set(duplicates)))


def parse_xml_file(root: str, path: str, max_bytes: int = 1024 * 1024) -> tuple[ET.Element, str, bool]:
    text, relative, truncated = read_bounded_text(root, path, max_bytes)
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError(f"DTD/entity declarations are prohibited: {relative}")
    return ET.fromstring(text), relative, truncated


def local_name(tag: str) -> str:
    return str(tag).split("}", 1)[-1].split(":", 1)[-1]


def flatten_json(value: Any, prefix: str = "") -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = f"{prefix}:{key}" if prefix else str(key)
            output.update(flatten_json(value[key], child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{prefix}:{index}" if prefix else str(index)
            output.update(flatten_json(item, child))
    else:
        output[prefix] = redact_value(prefix, value)
    return output

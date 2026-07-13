from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Tuple

from tools.spring.detector import is_application_config_name, read_limited
from tools.spring.models import ConfigValue, Diagnostic, SourceSpan

try:
    import yaml  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised when PyYAML is absent
    yaml = None


_CONFIG_NAME_RE = re.compile(r"^application(?:-([A-Za-z0-9_.-]+))?\.(properties|ya?ml|json)$", re.IGNORECASE)


def profile_from_config_name(path: str) -> str:
    match = _CONFIG_NAME_RE.match(os.path.basename(path))
    return match.group(1) if match and match.group(1) else ""


def discover_config_files(root: str, rel_paths: Iterable[str]) -> List[str]:
    files: List[str] = []
    for rel_path in rel_paths:
        if is_application_config_name(os.path.basename(rel_path)):
            files.append(rel_path)
    return sorted(set(files))


def parse_config_file(root: str, rel_path: str) -> Tuple[List[ConfigValue], List[Diagnostic]]:
    lower = rel_path.lower()
    abs_path = os.path.join(root, rel_path)
    profile = profile_from_config_name(rel_path)
    if lower.endswith(".properties"):
        return _parse_properties(abs_path, rel_path, profile)
    if lower.endswith((".yml", ".yaml")):
        return _parse_yaml(abs_path, rel_path, profile)
    if lower.endswith(".json"):
        return _parse_json(abs_path, rel_path, profile)
    return [], []


def _parse_properties(abs_path: str, rel_path: str, profile: str) -> Tuple[List[ConfigValue], List[Diagnostic]]:
    text = read_limited(abs_path, limit=2 * 1024 * 1024)
    values: List[ConfigValue] = []
    diagnostics: List[Diagnostic] = []
    logical_lines: List[Tuple[int, str]] = []
    pending = ""
    start_line = 1
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not pending:
            start_line = lineno
        if line.endswith("\\") and not line.endswith("\\\\"):
            pending += line[:-1]
            continue
        pending += line
        logical_lines.append((start_line, pending))
        pending = ""
    if pending:
        logical_lines.append((start_line, pending))

    for lineno, line in logical_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        key, sep, value = _split_property(stripped)
        if not sep:
            diagnostics.append(Diagnostic("spring.config.properties.malformed", "Missing key/value separator", "warning", rel_path, lineno, lineno))
            continue
        values.append(
            ConfigValue(
                key=_unescape_properties(key.strip()),
                value=_unescape_properties(value.strip()),
                source=SourceSpan(rel_path, lineno, lineno),
                profile=profile,
                raw_value=value.strip(),
            )
        )
    return values, diagnostics


def _split_property(line: str) -> Tuple[str, str, str]:
    escaped = False
    for idx, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in ("=", ":"):
            return line[:idx], char, line[idx + 1 :]
        if char.isspace():
            return line[:idx], char, line[idx + 1 :]
    return line, "", ""


def _unescape_properties(value: str) -> str:
    return (
        value.replace("\\:", ":")
        .replace("\\=", "=")
        .replace("\\ ", " ")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def _parse_yaml(abs_path: str, rel_path: str, profile: str) -> Tuple[List[ConfigValue], List[Diagnostic]]:
    if yaml is None:
        return [], [
            Diagnostic(
                "spring.config.yaml.no_pyyaml",
                "PyYAML is required to parse Spring YAML configuration",
                "error",
                rel_path,
                1,
                1,
            )
        ]
    text = read_limited(abs_path, limit=2 * 1024 * 1024)
    values: List[ConfigValue] = []
    diagnostics: List[Diagnostic] = []
    try:
        documents = list(yaml.safe_load_all(text))
    except Exception as exc:  # noqa: BLE001
        return [], [Diagnostic("spring.config.yaml.parse_error", str(exc), "error", rel_path, 1, 1)]
    for doc_index, document in enumerate(documents):
        if document is None:
            continue
        for key, value in _flatten_mapping(document):
            values.append(
                ConfigValue(
                    key=key,
                    value=value,
                    source=SourceSpan(rel_path, 1, 1),
                    profile=profile,
                    raw_value=json.dumps(value, ensure_ascii=True, sort_keys=True) if not isinstance(value, str) else value,
                    resolution_status="resolved",
                )
            )
        if not isinstance(document, dict):
            diagnostics.append(Diagnostic("spring.config.yaml.non_mapping_document", f"YAML document {doc_index + 1} is not a mapping", "warning", rel_path, 1, 1))
    return values, diagnostics


def _parse_json(abs_path: str, rel_path: str, profile: str) -> Tuple[List[ConfigValue], List[Diagnostic]]:
    text = read_limited(abs_path, limit=2 * 1024 * 1024)
    try:
        payload = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return [], [Diagnostic("spring.config.json.parse_error", str(exc), "error", rel_path, 1, 1)]
    return [
        ConfigValue(
            key=key,
            value=value,
            source=SourceSpan(rel_path, 1, 1),
            profile=profile,
            raw_value=json.dumps(value, ensure_ascii=True, sort_keys=True) if not isinstance(value, str) else value,
        )
        for key, value in _flatten_mapping(payload)
    ], []


def _flatten_mapping(value: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(value, dict):
        for key in sorted(value.keys(), key=str):
            next_key = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten_mapping(value[key], next_key)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            next_key = f"{prefix}[{idx}]"
            yield from _flatten_mapping(item, next_key)
    else:
        yield prefix, value

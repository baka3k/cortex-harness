from __future__ import annotations

import hashlib
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tools.spring.annotation_catalog import short_annotation_name
from tools.spring.models import SourceSpan, SpringFact, SpringRelationship
from tools.spring.source_scanner import SourceAnnotation, SourceClass, SourceMethod


def stable_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def fact(
    *,
    kind: str,
    stable_id: str,
    name: str,
    source: SourceSpan,
    project_id: str,
    project_name: str,
    language: str,
    confidence: float = 1.0,
    resolution_status: str = "resolved",
    raw_value: str = "",
    resolved_value: str = "",
    source_symbol_id: str = "",
    **properties,
) -> SpringFact:
    return SpringFact(
        kind=kind,
        stable_id=stable_id,
        name=name,
        source=source,
        project_id=project_id,
        project_name=project_name,
        language=f"spring-{language}" if language in {"java", "kotlin"} else "spring",
        confidence=confidence,
        resolution_status=resolution_status,
        raw_value=raw_value,
        resolved_value=resolved_value,
        source_symbol_id=source_symbol_id,
        properties=properties,
    )


def rel(
    rel_type: str,
    from_id: str,
    to_id: str,
    project_id: str,
    source: SourceSpan,
    reason: str,
    confidence: float = 1.0,
    resolution_status: str = "resolved",
    **properties,
) -> SpringRelationship:
    return SpringRelationship(
        type=rel_type,
        from_id=from_id,
        to_id=to_id,
        project_id=project_id,
        source=source,
        reason=reason,
        confidence=confidence,
        resolution_status=resolution_status,
        properties=properties,
    )


def annotation_map(annotations: Sequence[SourceAnnotation]) -> Dict[str, SourceAnnotation]:
    return {ann.short_name: ann for ann in annotations}


def has_annotation(annotations: Sequence[SourceAnnotation], names: Iterable[str]) -> bool:
    wanted = set(names)
    return any(ann.short_name in wanted for ann in annotations)


def first_annotation(annotations: Sequence[SourceAnnotation], names: Iterable[str]) -> Optional[SourceAnnotation]:
    wanted = set(names)
    for ann in annotations:
        if ann.short_name in wanted:
            return ann
    return None


def bean_name(default_name: str, ann: Optional[SourceAnnotation] = None) -> str:
    explicit = ""
    if ann:
        value = ann.args.get("name") or ann.args.get("value")
        if isinstance(value, list):
            explicit = str(value[0]) if value else ""
        elif value:
            explicit = str(value)
    if explicit:
        return explicit
    return default_name[:1].lower() + default_name[1:] if default_name else ""


def method_owner_id(method: SourceMethod) -> str:
    return method.symbol_id


def class_owner_id(cls: SourceClass) -> str:
    return cls.symbol_id


def parse_extends_types(header: str) -> List[str]:
    text = header or ""
    out: List[str] = []
    for marker in ("extends", "implements", ":"):
        if marker not in text:
            continue
        tail = text.split(marker, 1)[1]
        tail = tail.split("{", 1)[0]
        out.extend([item.strip() for item in _split_top_level_commas(tail) if item.strip()])
    return out


def generic_args(type_text: str) -> List[str]:
    match = re.search(r"<([^<>]+)>", type_text or "")
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _split_top_level_commas(text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                parts.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        parts.append(item)
    return parts


def all_methods(cls: SourceClass) -> Tuple[SourceMethod, ...]:
    return cls.methods

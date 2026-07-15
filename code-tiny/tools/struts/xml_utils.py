from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, Optional

from tools.struts.models import Diagnostic, SourceSpan


def parse_xml(root: str, file_path: str, code_prefix: str) -> tuple[Optional[ET.Element], SourceSpan, tuple[Diagnostic, ...]]:
    project_root = Path(root).resolve()
    candidate = Path(file_path)
    absolute = candidate.resolve() if candidate.is_absolute() else (project_root / candidate).resolve()
    try:
        relative = absolute.relative_to(project_root).as_posix()
    except ValueError:
        return None, SourceSpan(str(file_path)), (
            Diagnostic(f"{code_prefix}.path.outside_root", "XML path is outside the project root", "error", str(file_path)),
        )
    try:
        document = ET.parse(absolute)
    except (OSError, ET.ParseError) as exc:
        return None, SourceSpan(relative), (
            Diagnostic(f"{code_prefix}.xml.parse_error", str(exc), "error", relative),
        )
    return document.getroot(), SourceSpan(relative), ()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def children(element: ET.Element, name: str) -> Iterable[ET.Element]:
    return (child for child in element if local_name(child.tag) == name)


def first_child(element: ET.Element, name: str) -> Optional[ET.Element]:
    return next(children(element, name), None)


def child_text(element: ET.Element, name: str, default: str = "") -> str:
    child = first_child(element, name)
    return text(child) if child is not None else default


def text(element: Optional[ET.Element]) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def params(element: ET.Element) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for item in children(element, "param"):
        name = (item.get("name") or "").strip()
        if name:
            values[name] = text(item)
    return values


def normalized_path(path: str) -> str:
    return os.path.normpath(path).replace(os.sep, "/")

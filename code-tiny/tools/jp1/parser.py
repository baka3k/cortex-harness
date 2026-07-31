from __future__ import annotations

import os
import re
from pathlib import Path

from .models import Jp1Diagnostic, Jp1File, Jp1Relation, Jp1Unit


_UNIT_RE = re.compile(r"^\s*unit=([^,;]+)")
_PROPERTY_RE = re.compile(r"^\s*(ty|cm|te)\s*=\s*(.*?);?\s*$", re.IGNORECASE)
_AR_RE = re.compile(r"\bar=\(\s*f=([^,]+),\s*t=([^,)]+)", re.IGNORECASE)


def _clean(value: str) -> str:
    return value.strip().rstrip(";").strip().strip('"')


def _resolve_exec_target(raw: str, project_root: str) -> tuple[str, bool]:
    if re.search(r"@[A-Za-z_][A-Za-z0-9_]*@|\$", raw):
        return raw, False
    root_real = os.path.realpath(project_root)
    candidate = os.path.realpath(Path(project_root, raw.lstrip("/")))
    try:
        within_root = os.path.commonpath((root_real, candidate)) == root_real
    except ValueError:
        within_root = False
    if within_root and os.path.isfile(candidate):
        return os.path.relpath(candidate, root_real).replace("\\", "/"), True
    return raw.replace("\\", "/"), False


def parse_jp1_text(source_text: str, *, file_path: str, project_root: str) -> Jp1File:
    units: list[Jp1Unit] = []
    relations: list[Jp1Relation] = []
    diagnostics: list[Jp1Diagnostic] = []
    stack: list[Jp1Unit] = []
    pending: Jp1Unit | None = None
    arcs: list[tuple[str | None, str, str, int]] = []

    for line_number, line in enumerate(source_text.splitlines(), 1):
        unit_match = _UNIT_RE.match(line)
        if unit_match:
            parent_id = stack[-1].unit_id if stack else None
            name = _clean(unit_match.group(1))
            unit_id = f"jp1-unit::{file_path}:{parent_id or 'root'}:{name}:{line_number}"
            pending = Jp1Unit(unit_id, name, file_path, parent_id, line_number, line_number)
            units.append(pending)
            if parent_id:
                relations.append(Jp1Relation(parent_id, "Jp1Unit", unit_id, "Jp1Unit", "INCLUDES", line_number))
            continue
        if "{" in line and pending:
            stack.append(pending)
            pending = None
            continue
        if "}" in line:
            if stack:
                stack[-1].end_line = line_number
                stack.pop()
            continue
        if not stack:
            continue
        property_match = _PROPERTY_RE.match(line)
        if property_match:
            key, value = property_match.group(1).lower(), _clean(property_match.group(2))
            if key == "ty":
                stack[-1].unit_type = value
            elif key == "cm":
                stack[-1].comment = value
            else:
                stack[-1].exec_target = value
        arc_match = _AR_RE.search(line)
        if arc_match:
            arcs.append((stack[-1].unit_id, _clean(arc_match.group(1)), _clean(arc_match.group(2)), line_number))

    child_index = {(unit.parent_id, unit.name): unit.unit_id for unit in units}
    for parent_id, source_name, target_name, line_number in arcs:
        source_id = child_index.get((parent_id, source_name))
        target_id = child_index.get((parent_id, target_name))
        if source_id and target_id:
            relations.append(Jp1Relation(source_id, "Jp1Unit", target_id, "Jp1Unit", "NEXT", line_number))
        else:
            diagnostics.append(Jp1Diagnostic("jp1-arc-unresolved", f"Unable to resolve arc {source_name} -> {target_name}", file_path, line_number))

    for unit in units:
        if not unit.exec_target:
            continue
        target_id, resolved = _resolve_exec_target(unit.exec_target, project_root)
        relations.append(Jp1Relation(unit.unit_id, "Jp1Unit", target_id, "ShellScript", "CALLS", unit.start_line, unit.exec_target, resolved))
        if not resolved:
            diagnostics.append(Jp1Diagnostic("jp1-exec-target-unresolved", f"Unable to resolve {unit.exec_target}", file_path, unit.start_line))

    return Jp1File(file_path, "", tuple(units), tuple(relations), tuple(diagnostics))
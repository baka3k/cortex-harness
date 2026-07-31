"""Line/regex-based structural extraction for JP1/AJS job-net unit-definition
export files (the `unit=<id>,...,;\\n{ ... }` DSL).

Deliberately not a full grammar: the block structure is simple brace-nested
`key=value;` attribute lists with optionally-nested `unit=...{ ... }` children,
so a stack-based line scanner is sufficient.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import Dict, List, Optional

from tools.common.text_encoding import decode_source_bytes
from tools.jp1.models import Jp1DefinitionFile, Jp1SequenceEdge, Jp1Unit, RelationEdge


_UNIT_SNIFF_RE = re.compile(r"^\s*unit=[A-Za-z0-9_\-]+,")
_UNIT_DECL_RE = re.compile(r"^\s*unit=(?P<id>[A-Za-z0-9_\-]+)\s*,.*;\s*$")
_BLOCK_OPEN_RE = re.compile(r"^\s*\{\s*$")
_BLOCK_CLOSE_RE = re.compile(r"^\s*\}\s*$")
_ATTR_RE = re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z0-9_]*)=(?P<value>.*?);\s*$")
_AR_RE = re.compile(
    r"ar=\(f=(?P<from>[A-Za-z0-9_\-]+)\s*,\s*t=(?P<to>[A-Za-z0-9_\-]+)(?:,[^)]*)?\)"
)
_ENV_PLACEHOLDER_RE = re.compile(r"@[A-Za-z0-9_]+@/?")
_COMMENT_RE = re.compile(r"^\s*#(.*)$")


def looks_like_jp1_unit_definition(text: str) -> bool:
    """Content-sniff heuristic: first non-blank line matches `unit=<id>,`."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return bool(_UNIT_SNIFF_RE.match(stripped))
    return False


def _stable_id(kind: str, symbol_id: str) -> str:
    return f"{kind}::{uuid.uuid5(uuid.NAMESPACE_URL, symbol_id)}"


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_units(lines: List[str], file_path: str) -> List[Jp1Unit]:
    """Parse nested `unit=...{ ... }` blocks into a flat list of `Jp1Unit`."""
    units: List[Jp1Unit] = []
    stack: List[Dict] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        decl_match = _UNIT_DECL_RE.match(line)
        if decl_match:
            unit_id = decl_match.group("id")
            start_line = i + 1
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and _BLOCK_OPEN_RE.match(lines[j]):
                parent_id = stack[-1]["unit_id"] if stack else None
                stack.append(
                    {
                        "unit_id": unit_id,
                        "start_line": start_line,
                        "attrs": {},
                        "seq": [],
                        "parent_id": parent_id,
                    }
                )
                i = j + 1
                continue
            i += 1
            continue

        if _BLOCK_CLOSE_RE.match(line):
            if stack:
                top = stack.pop()
                end_line = i + 1
                attrs = top["attrs"]
                unit = Jp1Unit(
                    unit_id=top["unit_id"],
                    unit_type=attrs.get("ty", ""),
                    comment=_strip_quotes(attrs.get("cm", "")),
                    parent_id=top["parent_id"],
                    file_path=file_path,
                    start_line=top["start_line"],
                    end_line=end_line,
                    exec_command=_strip_quotes(attrs.get("te", "")),
                    sequence_edges=top["seq"],
                    attributes=attrs,
                )
                units.append(unit)
            i += 1
            continue

        if stack:
            ar_match = _AR_RE.search(line)
            if ar_match:
                stack[-1]["seq"].append(
                    Jp1SequenceEdge(
                        from_unit=ar_match.group("from"),
                        to_unit=ar_match.group("to"),
                        line=i + 1,
                    )
                )
            attr_match = _ATTR_RE.match(line)
            if attr_match:
                stack[-1]["attrs"][attr_match.group("key")] = attr_match.group("value").strip()
        i += 1

    return units


def parse_jp1_file(path: str, root: str) -> Jp1DefinitionFile:
    with open(path, "rb") as handle:
        raw = handle.read()
    code, encoding, lossy = decode_source_bytes(raw)
    rel_path = os.path.relpath(path, root)
    lines = code.split("\n")
    line_count = len(lines)

    file_comment_lines: List[str] = []
    for line in lines:
        match = _COMMENT_RE.match(line)
        if not match:
            break
        file_comment_lines.append(match.group(1).strip())
    file_comment = "\n".join(file_comment_lines)

    units = parse_units(lines, rel_path)

    return Jp1DefinitionFile(
        file_path=rel_path,
        code=code,
        comment=file_comment,
        start_line=1,
        end_line=line_count,
        source_encoding=encoding,
        source_encoding_lossy=lossy,
        units=units,
    )


def build_relations(definition: Jp1DefinitionFile) -> List[RelationEdge]:
    """Translate unit hierarchy/sequence/exec facts into generic graph relations."""
    relations: List[RelationEdge] = []
    unit_by_id = {unit.unit_id: unit for unit in definition.units}

    def _unit_node_id(unit_id: str) -> str:
        return _stable_id("jp1_unit", f"{definition.file_path}:{unit_id}")

    for unit in definition.units:
        if unit.parent_id and unit.parent_id in unit_by_id:
            relations.append(
                RelationEdge(
                    source_id=_unit_node_id(unit.parent_id),
                    source_label="Jp1Unit",
                    target_id=_unit_node_id(unit.unit_id),
                    target_label="Jp1Unit",
                    rel_type="CONTAINS",
                    properties={},
                )
            )
        for edge in unit.sequence_edges:
            relations.append(
                RelationEdge(
                    source_id=_unit_node_id(edge.from_unit),
                    source_label="Jp1Unit",
                    target_id=_unit_node_id(edge.to_unit),
                    target_label="Jp1Unit",
                    rel_type="PRECEDES",
                    properties={"line": str(edge.line)},
                )
            )
        if unit.unit_type == "j" and unit.exec_command:
            script_ref = _ENV_PLACEHOLDER_RE.sub("", unit.exec_command)
            relations.append(
                RelationEdge(
                    source_id=_unit_node_id(unit.unit_id),
                    source_label="Jp1Unit",
                    target_id=_stable_id("file", script_ref),
                    target_label="File",
                    rel_type="EXECUTES",
                    properties={"exec_command": unit.exec_command, "script_ref": script_ref},
                )
            )

    return relations

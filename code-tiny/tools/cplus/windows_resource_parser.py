from __future__ import annotations

import csv
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


WINDOWS_RESOURCE_EXTENSIONS = (".rc", ".rc2")

_BLOCK_RESOURCE_RE = re.compile(
    r"^\s*(?:(?P<name>[A-Za-z_][\w]*|\d+)\s+)?"
    r"(?P<kind>DIALOGEX?|MENUEX?|ACCELERATORS|VERSIONINFO|TOOLBAR|DLGINIT|"
    r"RCDATA|HTML|MESSAGETABLE|AFX_DIALOG_LAYOUT|DESIGNINFO|TEXTINCLUDE|STRINGTABLE)\b",
    re.IGNORECASE,
)
_ASSET_RESOURCE_RE = re.compile(
    r'^\s*(?P<name>[A-Za-z_][\w]*|\d+)\s+'
    r'(?P<kind>ICON|BITMAP|CURSOR|FONT|HTML|RCDATA|MESSAGETABLE)\s+'
    r'(?P<value>"(?:""|[^"])+"|\S+)\s*$',
    re.IGNORECASE,
)
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s*(.*)$", re.MULTILINE)
_LANGUAGE_RE = re.compile(r"^\s*LANGUAGE\s+(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_CAPTION_RE = re.compile(r'^\s*CAPTION\s+"((?:""|[^"])*)"', re.MULTILINE | re.IGNORECASE)
_VALUE_RE = re.compile(r'^\s*VALUE\s+"((?:""|[^"])*)"\s*,\s*(.+?)\s*$')
_STRING_ENTRY_RE = re.compile(r'^\s*([A-Za-z_]\w*|\d+)\s+"((?:""|[^"])*)"\s*$')

_DIALOG_CONTROL_KEYWORDS = {
    "AUTO3STATE",
    "AUTOCHECKBOX",
    "AUTORADIOBUTTON",
    "CHECKBOX",
    "COMBOBOX",
    "CONTROL",
    "CTEXT",
    "DEFPUSHBUTTON",
    "EDITTEXT",
    "GROUPBOX",
    "ICON",
    "LISTBOX",
    "LTEXT",
    "PUSHBOX",
    "PUSHBUTTON",
    "RADIOBUTTON",
    "RTEXT",
    "SCROLLBAR",
    "STATE3",
}
_CONTROL_ID_FIRST = {"COMBOBOX", "EDITTEXT", "LISTBOX", "SCROLLBAR"}


def is_windows_resource_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in WINDOWS_RESOURCE_EXTENSIONS


def read_windows_resource_text(path: str) -> Tuple[str, str]:
    """Read a Windows resource script while preserving its declared text."""
    with open(path, "rb") as handle:
        data = handle.read()

    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16-be"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"

    if data and data.count(b"\x00") >= max(2, len(data) // 8):
        even_nuls = data[0::2].count(0)
        odd_nuls = data[1::2].count(0)
        encoding = "utf-16-le" if odd_nuls >= even_nuls else "utf-16-be"
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass

    for encoding in ("utf-8", "cp932", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def _clean_quoted(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value.replace('""', '"')


def _split_rc_arguments(value: str) -> List[str]:
    try:
        return [item.strip() for item in next(csv.reader([value], skipinitialspace=True))]
    except (csv.Error, StopIteration):
        return [item.strip() for item in value.split(",")]


def _leading_comment(lines: Sequence[str]) -> str:
    comments: List[str] = []
    in_block = False
    for raw_line in lines:
        stripped = raw_line.strip().lstrip("\ufeff")
        if not stripped and not comments:
            continue
        if in_block:
            cleaned = stripped.removeprefix("*").removesuffix("*/").strip()
            if cleaned:
                comments.append(cleaned)
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("//"):
            cleaned = stripped[2:].strip()
            if cleaned:
                comments.append(cleaned)
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped[2:]
            cleaned = stripped[2:].removesuffix("*/").strip()
            if cleaned:
                comments.append(cleaned)
            continue
        break
    return "\n".join(comments)


def _preceding_comment(lines: Sequence[str], index: int) -> str:
    comments: List[str] = []
    cursor = index - 1
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped:
            if comments:
                break
            cursor -= 1
            continue
        if not stripped.startswith("//"):
            break
        cleaned = stripped[2:].strip()
        if cleaned and set(cleaned) != {"/"}:
            comments.append(cleaned)
        cursor -= 1
    return "\n".join(reversed(comments))


def _resource_symbol_id(rel_path: str, name: str, kind: str, line: int) -> str:
    normalized = rel_path.replace("\\", "/")
    return f"resource::{normalized}::{name}::{kind.lower()}@{line}"


def _field_payload(
    resource_id: str,
    resource_name: str,
    field_name: str,
    type_signature: str,
    rel_path: str,
    line_number: int,
    code: str,
) -> Dict[str, Any]:
    qualified_name = f"{resource_name}::{field_name}"
    return {
        "symbol_id": f"{resource_id}::field::{field_name}@{line_number}",
        "qualified_name": qualified_name,
        "name": field_name,
        "scope_name": resource_name,
        "type_signature": type_signature,
        "file_path": rel_path,
        "start_line": line_number,
        "end_line": line_number,
        "code": code,
    }


def _dialog_fields(
    block_lines: Sequence[str],
    start_line: int,
    resource_id: str,
    resource_name: str,
    rel_path: str,
) -> List[Dict[str, Any]]:
    fields: List[Dict[str, Any]] = []
    for offset, raw_line in enumerate(block_lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        keyword, _, arguments = stripped.partition(" ")
        keyword = keyword.upper()
        if keyword not in _DIALOG_CONTROL_KEYWORDS or not arguments:
            continue
        items = _split_rc_arguments(arguments)
        if not items:
            continue
        if keyword in _CONTROL_ID_FIRST:
            control_id = items[0]
            label = ""
        elif len(items) >= 2:
            control_id = items[1]
            label = _clean_quoted(items[0])
        else:
            continue
        control_id = control_id.strip()
        if not control_id:
            continue
        signature = f"windows_resource_control:{keyword.lower()}"
        if label:
            signature = f'{signature} label="{label}"'
        fields.append(
            _field_payload(
                resource_id,
                resource_name,
                control_id,
                signature,
                rel_path,
                start_line + offset,
                raw_line.strip(),
            )
        )
    return fields


def _value_fields(
    block_lines: Sequence[str],
    start_line: int,
    resource_id: str,
    resource_name: str,
    rel_path: str,
) -> List[Dict[str, Any]]:
    fields: List[Dict[str, Any]] = []
    for offset, raw_line in enumerate(block_lines):
        match = _VALUE_RE.match(raw_line)
        if not match:
            continue
        key = _clean_quoted(f'"{match.group(1)}"')
        value = match.group(2).strip()
        fields.append(
            _field_payload(
                resource_id,
                resource_name,
                key,
                f"windows_resource_value:{value}",
                rel_path,
                start_line + offset,
                raw_line.strip(),
            )
        )
    return fields


def _string_table_fields(
    block_lines: Sequence[str],
    start_line: int,
    resource_id: str,
    resource_name: str,
    rel_path: str,
) -> List[Dict[str, Any]]:
    fields: List[Dict[str, Any]] = []
    for offset, raw_line in enumerate(block_lines):
        match = _STRING_ENTRY_RE.match(raw_line)
        if not match:
            continue
        key = match.group(1)
        value = _clean_quoted(f'"{match.group(2)}"')
        fields.append(
            _field_payload(
                resource_id,
                resource_name,
                key,
                f'windows_resource_string value="{value}"',
                rel_path,
                start_line + offset,
                raw_line.strip(),
            )
        )
    return fields


def _find_block_end(lines: Sequence[str], begin_index: int) -> Optional[int]:
    depth = 0
    for index in range(begin_index, len(lines)):
        token = lines[index].strip().upper()
        if token == "BEGIN":
            depth += 1
        elif token == "END":
            depth -= 1
            if depth == 0:
                return index
    return None


def _resource_summary(kind: str, name: str, code: str, fields: Sequence[Dict[str, Any]]) -> str:
    kind_lower = kind.lower()
    caption_match = _CAPTION_RE.search(code)
    caption = _clean_quoted(f'"{caption_match.group(1)}"') if caption_match else ""
    if kind_lower in {"dialog", "dialogex"}:
        suffix = f' caption "{caption}"' if caption else ""
        return f"Windows dialog resource {name}{suffix} with {len(fields)} controls."
    if kind_lower == "versioninfo":
        keys = ", ".join(field["name"] for field in fields[:8])
        suffix = f": {keys}" if keys else ""
        return f"Windows version resource {name} with {len(fields)} metadata values{suffix}."
    if kind_lower == "stringtable":
        keys = ", ".join(field["name"] for field in fields[:8])
        suffix = f": {keys}" if keys else ""
        return f"Windows string table with {len(fields)} entries{suffix}."
    if kind_lower == "textinclude":
        return f"Visual C++ TEXTINCLUDE resource {name} containing generated include metadata."
    if kind_lower == "designinfo":
        return f"Visual C++ dialog design metadata resource {name}."
    if kind_lower == "afx_dialog_layout":
        return f"MFC dialog layout resource {name}."
    return f"Windows {kind_lower} resource {name}."


def parse_windows_resource_file(path: str, root: str) -> Dict[str, Any]:
    text, encoding = read_windows_resource_text(path)
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    rel_path = os.path.relpath(path, root).replace("\\", "/")
    file_comment = _leading_comment(lines)
    types: List[Dict[str, Any]] = []
    fields: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    resource_counts: Dict[str, int] = {}

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        asset_match = _ASSET_RESOURCE_RE.match(raw_line)
        if asset_match:
            name = asset_match.group("name")
            kind = asset_match.group("kind").upper()
            value = _clean_quoted(asset_match.group("value"))
            symbol_id = _resource_symbol_id(rel_path, name, kind, index + 1)
            summary = f"Windows {kind.lower()} resource {name} references {value}."
            types.append(
                {
                    "symbol_id": symbol_id,
                    "qualified_name": f"{rel_path}::{name}",
                    "name": name,
                    "kind": f"windows_resource_{kind.lower()}",
                    "file_path": rel_path,
                    "start_line": index + 1,
                    "end_line": index + 1,
                    "code": raw_line.strip(),
                    "comment": _preceding_comment(lines, index),
                    "summary": summary,
                    "note": summary,
                }
            )
            resource_counts[kind.lower()] = resource_counts.get(kind.lower(), 0) + 1
            index += 1
            continue

        header_match = _BLOCK_RESOURCE_RE.match(raw_line)
        if not header_match:
            index += 1
            continue
        kind = header_match.group("kind").upper()
        name = header_match.group("name") or f"{kind}@{index + 1}"
        begin_index: Optional[int] = None
        for candidate in range(index, min(len(lines), index + 20)):
            token = lines[candidate].strip().upper()
            if token == "BEGIN":
                begin_index = candidate
                break
            if candidate > index and _BLOCK_RESOURCE_RE.match(lines[candidate]):
                break
        if begin_index is None:
            index += 1
            continue
        end_index = _find_block_end(lines, begin_index)
        if end_index is None:
            end_index = len(lines) - 1

        block_lines = lines[index : end_index + 1]
        block_code = "\n".join(block_lines)
        symbol_id = _resource_symbol_id(rel_path, name, kind, index + 1)
        if kind in {"DIALOG", "DIALOGEX"}:
            resource_fields = _dialog_fields(
                block_lines, index + 1, symbol_id, name, rel_path
            )
        elif kind == "VERSIONINFO":
            resource_fields = _value_fields(
                block_lines, index + 1, symbol_id, name, rel_path
            )
        elif kind == "STRINGTABLE":
            resource_fields = _string_table_fields(
                block_lines, index + 1, symbol_id, name, rel_path
            )
        else:
            resource_fields = []
        summary = _resource_summary(kind, name, block_code, resource_fields)
        types.append(
            {
                "symbol_id": symbol_id,
                "qualified_name": f"{rel_path}::{name}",
                "name": name,
                "kind": f"windows_resource_{kind.lower()}",
                "file_path": rel_path,
                "start_line": index + 1,
                "end_line": end_index + 1,
                "code": block_code,
                "comment": _preceding_comment(lines, index),
                "summary": summary,
                "note": summary,
            }
        )
        for field in resource_fields:
            fields.append(field)
            relations.append(
                {
                    "source_id": symbol_id,
                    "source_label": "Type",
                    "target_id": field["symbol_id"],
                    "target_label": "Field",
                    "rel_type": "CONTAINS",
                    "properties": {"resource_kind": kind.lower()},
                }
            )
        resource_counts[kind.lower()] = resource_counts.get(kind.lower(), 0) + 1
        index = end_index + 1

    manual_resource_file = os.path.splitext(path)[1].lower() == ".rc2"
    if not types:
        name = os.path.basename(rel_path)
        symbol_id = _resource_symbol_id(rel_path, name, "fragment", 1)
        summary = (
            f"Manually maintained Windows resource fragment {name}."
            if manual_resource_file
            else f"Windows resource script fragment {name}."
        )
        if file_comment:
            summary = f"{summary} {file_comment.replace(chr(10), ' ')}"
        types.append(
            {
                "symbol_id": symbol_id,
                "qualified_name": f"{rel_path}::{name}",
                "name": name,
                "kind": "windows_resource_fragment",
                "file_path": rel_path,
                "start_line": 1,
                "end_line": max(1, len(lines)),
                "code": text,
                "comment": file_comment,
                "summary": summary,
                "note": summary,
            }
        )
        resource_counts["fragment"] = 1

    count_summary = ", ".join(
        f"{kind}={count}" for kind, count in sorted(resource_counts.items())
    )
    language_match = _LANGUAGE_RE.search(text)
    language = language_match.group(1).strip() if language_match else ""
    file_summary = f"Windows resource script with {len(types)} semantic resources ({count_summary})."
    if manual_resource_file:
        file_summary = f"Manual {file_summary[0].lower()}{file_summary[1:]}"
    if language:
        file_summary = f"{file_summary} LANGUAGE {language}."

    includes = list(dict.fromkeys(match.group(1) for match in _INCLUDE_RE.finditer(text)))
    macros = {
        match.group(1): match.group(2).strip()
        for match in _DEFINE_RE.finditer(text)
    }
    return {
        "functions": [],
        "calls": [],
        "types": types,
        "namespaces": [],
        "relations": relations,
        "function_types": [],
        "fields": fields,
        "aliases": [],
        "templates": [],
        "file_def": {
            "file_path": rel_path,
            "start_line": 1,
            "end_line": max(1, len(lines)),
            "code": text,
            "comment": file_comment,
            "summary": file_summary,
            "note": file_summary,
        },
        "using_namespaces": [],
        "using_imports": {},
        "includes": includes,
        "macros": macros,
        "parse_meta": {
            "parser_language": "windows-resource",
            "parser_language_initial": "windows-resource",
            "encoding": encoding,
            "manual_resource_file": manual_resource_file,
            "resource_counts": resource_counts,
            "header_retry_attempted": False,
            "header_retry_selected": False,
            "has_error": False,
            "error_nodes": 0,
            "error_nodes_initial": 0,
            "header_retry_error_nodes": None,
            "header_retry_has_error": None,
        },
    }

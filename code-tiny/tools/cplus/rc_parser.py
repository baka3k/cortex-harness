"""Tolerant parser for Microsoft Windows ``.rc`` and ``.rc2`` files.

Resource scripts are declarative UI/metadata files, not C/C++ translation
units.  This module intentionally uses a small stateful parser instead of the
C/C++ tree-sitter grammar so UTF-16 files, nested BEGIN/END blocks, and Visual
Studio resource constructs remain queryable.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_BLOCK_RESOURCE_TYPES = {
    "ACCELERATORS",
    "AFX_DIALOG_LAYOUT",
    "DESIGNINFO",
    "DIALOG",
    "DIALOGEX",
    "MENU",
    "MENUEX",
    "STRINGTABLE",
    "TEXTINCLUDE",
    "TOOLBAR",
    "VERSIONINFO",
}
_CONTROL_WITH_TEXT = {
    "AUTO3STATE",
    "AUTOCHECKBOX",
    "AUTORADIOBUTTON",
    "CHECKBOX",
    "CTEXT",
    "DEFPUSHBUTTON",
    "GROUPBOX",
    "LTEXT",
    "PUSHBOX",
    "PUSHBUTTON",
    "RADIOBUTTON",
    "RTEXT",
    "STATE3",
}
_CONTROL_WITHOUT_TEXT = {
    "COMBOBOX",
    "EDITTEXT",
    "LISTBOX",
    "SCROLLBAR",
}


def decode_rc_bytes(data: bytes) -> Tuple[str, str, bool]:
    """Decode a resource file and return ``(text, encoding, lossy)``.

    Visual Studio commonly writes UTF-16LE resource scripts.  Older projects
    may use UTF-8 or a Windows ANSI code page, especially CP932 for Japanese.
    """

    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le"), "utf-16-le", False
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be"), "utf-16-be", False
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig", False

    sample = data[:512]
    if sample:
        odd_nuls = sample[1::2].count(0)
        even_nuls = sample[0::2].count(0)
        threshold = max(2, len(sample) // 8)
        if odd_nuls >= threshold:
            return data.decode("utf-16-le"), "utf-16-le", False
        if even_nuls >= threshold:
            return data.decode("utf-16-be"), "utf-16-be", False

    for encoding in ("utf-8", "cp932"):
        try:
            return data.decode(encoding), encoding, False
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace"), "cp1252", True


def read_rc_text(path: str) -> Tuple[str, str, bool]:
    with open(path, "rb") as handle:
        return decode_rc_bytes(handle.read())


def _strip_line_comment(line: str) -> str:
    quoted = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            if quoted and index + 1 < len(line) and line[index + 1] == '"':
                index += 2
                continue
            quoted = not quoted
        elif not quoted and char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            return line[:index]
        index += 1
    return line


def _mask_strings(line: str) -> str:
    chars = list(_strip_line_comment(line))
    quoted = False
    index = 0
    while index < len(chars):
        if chars[index] == '"':
            if quoted and index + 1 < len(chars) and chars[index + 1] == '"':
                chars[index] = chars[index + 1] = " "
                index += 2
                continue
            quoted = not quoted
            chars[index] = " "
        elif quoted:
            chars[index] = " "
        index += 1
    return "".join(chars)


def _split_fields(text: str) -> List[str]:
    fields: List[str] = []
    current: List[str] = []
    quoted = False
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            current.append(char)
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                current.append(text[index + 1])
                index += 2
                continue
            quoted = not quoted
        elif not quoted and char in "([":
            depth += 1
            current.append(char)
        elif not quoted and char in ")]":
            depth = max(0, depth - 1)
            current.append(char)
        elif not quoted and depth == 0 and char == ",":
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    if current or text.endswith(","):
        fields.append("".join(current).strip())
    return fields


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.replace('""', '"').replace("\\0", "")


def _int_or_none(value: str) -> Optional[int]:
    try:
        return int(value.strip(), 0)
    except (TypeError, ValueError):
        return None


def _resource_id(rel_path: str, kind: str, symbol: str, line: int) -> str:
    stable_symbol = re.sub(r"[^A-Za-z0-9_.:-]+", "_", symbol or f"line_{line}")
    return f"resource::{rel_path}::{kind}::{stable_symbol}"


def _control_id(rel_path: str, dialog_symbol: str, symbol: str, line: int) -> str:
    stable_symbol = re.sub(r"[^A-Za-z0-9_.:-]+", "_", symbol or "anonymous")
    return f"ui::{rel_path}::{dialog_symbol}::{stable_symbol}::{line}"


def _condition_text(stack: Sequence[str]) -> str:
    return " && ".join(item for item in stack if item)


def _update_conditions(code: str, stack: List[str]) -> None:
    if match := re.match(r"^\s*#\s*if\s+(.+)$", code):
        stack.append(match.group(1).strip())
    elif match := re.match(r"^\s*#\s*ifdef\s+(.+)$", code):
        stack.append(f"defined({match.group(1).strip()})")
    elif match := re.match(r"^\s*#\s*ifndef\s+(.+)$", code):
        stack.append(f"!defined({match.group(1).strip()})")
    elif re.match(r"^\s*#\s*else\b", code):
        if stack:
            stack[-1] = f"!({stack[-1]})"
    elif match := re.match(r"^\s*#\s*elif\s+(.+)$", code):
        if stack:
            stack[-1] = match.group(1).strip()
    elif re.match(r"^\s*#\s*endif\b", code):
        if stack:
            stack.pop()


def _resource_header(code: str, line: int) -> Optional[Tuple[str, str, str]]:
    stripped = code.strip()
    if re.match(r"^STRINGTABLE\b", stripped, re.IGNORECASE):
        return f"STRINGTABLE@{line}", "STRINGTABLE", stripped[len("STRINGTABLE") :].strip()
    if re.match(r"^GUIDELINES\s+DESIGNINFO\b", stripped, re.IGNORECASE):
        return f"DESIGNINFO@{line}", "DESIGNINFO", stripped
    match = re.match(
        r"^([^\s]+)\s+(DIALOGEX|DIALOG|VERSIONINFO|TEXTINCLUDE|AFX_DIALOG_LAYOUT|"
        r"MENUEX|MENU|ACCELERATORS|TOOLBAR|ICON|BITMAP|CURSOR|RCDATA|HTML|"
        r"MANIFEST|MESSAGETABLE|AVI|FONT)\b(.*)$",
        stripped,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1), match.group(2).upper(), match.group(3).strip()


def _block_end(lines: Sequence[str], header_index: int) -> int:
    depth = 0
    started = False
    for index in range(header_index, len(lines)):
        masked = _mask_strings(lines[index])
        for token in re.findall(r"\b(?:BEGIN|END)\b", masked, re.IGNORECASE):
            if token.upper() == "BEGIN":
                depth += 1
                started = True
            elif started:
                depth -= 1
                if depth == 0:
                    return index
        if index > header_index + 20000:
            break
    return header_index


def _find_begin(lines: Sequence[str], start: int, end: int) -> Optional[int]:
    for index in range(start, end + 1):
        if re.search(r"\bBEGIN\b", _mask_strings(lines[index]), re.IGNORECASE):
            return index
    return None


def _quoted_value(code: str) -> str:
    match = re.search(r'"(?:[^"]|"")*"', code)
    return _unquote(match.group(0)) if match else ""


def _parse_control(
    code: str,
    rel_path: str,
    dialog_symbol: str,
    dialog_id: str,
    line: int,
    condition: str,
) -> Optional[Dict[str, Any]]:
    match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s+(.*)$", code)
    if not match:
        return None
    keyword = match.group(1).upper()
    fields = _split_fields(match.group(2))
    text = ""
    resource_ref = ""
    style = ""
    symbol = ""
    coords: List[str] = []

    if keyword == "CONTROL" and len(fields) >= 8:
        text, symbol = _unquote(fields[0]), fields[1]
        style = " | ".join(part for part in (fields[2], fields[3]) if part)
        coords = fields[4:8]
    elif keyword == "ICON" and len(fields) >= 6:
        resource_ref, symbol = fields[0], fields[1]
        coords = fields[2:6]
        style = ", ".join(fields[6:])
    elif keyword in _CONTROL_WITH_TEXT and len(fields) >= 6:
        text, symbol = _unquote(fields[0]), fields[1]
        coords = fields[2:6]
        style = ", ".join(fields[6:])
    elif keyword in _CONTROL_WITHOUT_TEXT and len(fields) >= 5:
        symbol = fields[0]
        coords = fields[1:5]
        style = ", ".join(fields[5:])
    else:
        return None

    x, y, width, height = (_int_or_none(value) for value in coords)
    symbol = symbol.strip()
    control_id = _control_id(rel_path, dialog_symbol, symbol, line)
    note_parts = [f"{keyword} control", symbol]
    if text:
        note_parts.append(text)
    if resource_ref:
        note_parts.append(f"resource {resource_ref}")
    return {
        "symbol_id": control_id,
        "qualified_name": f"{dialog_symbol}::{symbol}@{line}",
        "name": symbol or f"{keyword}@{line}",
        "kind": "ui_control",
        "control_type": keyword.lower(),
        "resource_symbol": symbol,
        "resource_ref": resource_ref,
        "dialog_id": dialog_id,
        "dialog_symbol": dialog_symbol,
        "text": text,
        "style": style,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "file_path": rel_path,
        "start_line": line,
        "end_line": line,
        "code": code.strip(),
        "comment": "",
        "summary": text,
        "note": " ".join(part for part in note_parts if part),
        "condition": condition,
    }


def _parse_dialog(
    lines: Sequence[str],
    start: int,
    end: int,
    resource: Dict[str, Any],
    condition: str,
) -> List[Dict[str, Any]]:
    begin = _find_begin(lines, start, end)
    if begin is None:
        return []
    metadata: Dict[str, Any] = {}
    header_tail = resource.pop("_header_tail", "")
    header_fields = _split_fields(header_tail)
    if len(header_fields) >= 4:
        metadata["bounds"] = [_int_or_none(value) for value in header_fields[-4:]]
    for index in range(start + 1, begin):
        code = _strip_line_comment(lines[index]).strip()
        if match := re.match(r"^(CAPTION|STYLE|EXSTYLE|FONT)\s+(.+)$", code, re.IGNORECASE):
            key, value = match.group(1).lower(), match.group(2).strip()
            metadata[key] = _quoted_value(value) if key == "caption" else value
    resource["caption"] = str(metadata.get("caption") or "")
    resource["style"] = str(metadata.get("style") or "")

    controls: List[Dict[str, Any]] = []
    depth = 0
    for index in range(begin, end + 1):
        masked = _mask_strings(lines[index])
        upper_tokens = [token.upper() for token in re.findall(r"\b(?:BEGIN|END)\b", masked, re.IGNORECASE)]
        if index > begin and depth == 1:
            control = _parse_control(
                _strip_line_comment(lines[index]).strip(),
                resource["file_path"],
                resource["resource_symbol"],
                resource["symbol_id"],
                index + 1,
                condition,
            )
            if control:
                controls.append(control)
        for token in upper_tokens:
            depth += 1 if token == "BEGIN" else -1

    metadata["control_count"] = len(controls)
    resource["metadata_json"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    control_summary = "; ".join(
        " ".join(part for part in (item["control_type"], item["resource_symbol"], item["text"]) if part)
        for item in controls
        if item["resource_symbol"] != "IDC_STATIC" or item["text"]
    )
    resource["note"] = " | ".join(
        part
        for part in (
            f"Dialog {resource['resource_symbol']}",
            resource["caption"],
            control_summary,
        )
        if part
    )
    resource["summary"] = resource["caption"]
    return controls


def _parse_value_metadata(lines: Sequence[str], start: int, end: int) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for index in range(start, end + 1):
        code = _strip_line_comment(lines[index]).strip()
        match = re.match(r"^VALUE\s+(.+)$", code, re.IGNORECASE)
        if not match:
            continue
        fields = _split_fields(match.group(1))
        if len(fields) >= 2:
            values[_unquote(fields[0])] = _unquote(", ".join(fields[1:]))
    return values


def _parse_string_table(lines: Sequence[str], start: int, end: int) -> List[Tuple[str, str, int]]:
    values: List[Tuple[str, str, int]] = []
    for index in range(start, end + 1):
        code = _strip_line_comment(lines[index]).strip()
        match = re.match(r"^([^\s]+)\s+(\".*\")\s*$", code)
        if match:
            values.append((match.group(1), _unquote(match.group(2)), index + 1))
    return values


def _resource_note(resource: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    parts = [resource["kind"], resource["resource_symbol"]]
    if resource.get("caption"):
        parts.append(resource["caption"])
    if resource.get("asset_path"):
        parts.append(resource["asset_path"])
    for key, value in metadata.items():
        if value:
            parts.append(f"{key}: {value}")
    return " | ".join(str(part) for part in parts if part)


def parse_rc_file(path: str, root: str) -> Dict[str, Any]:
    text, encoding, lossy = read_rc_text(path)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    rel_path = os.path.relpath(path, root).replace("\\", "/")
    includes: List[str] = []
    macros: Dict[str, str] = {}
    resources: List[Dict[str, Any]] = []
    elements: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    conditions: List[str] = []
    language = ""
    diagnostics: List[str] = []
    index = 0

    while index < len(lines):
        raw = lines[index]
        code = _strip_line_comment(raw).strip()
        if re.match(r"^#\s*(?:if|ifdef|ifndef|else|elif|endif)\b", code):
            _update_conditions(code, conditions)
            index += 1
            continue
        if match := re.match(r'^#\s*include\s+[<"]([^>"]+)[>"]', code):
            includes.append(match.group(1))
        if match := re.match(r"^#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$", code):
            macros[match.group(1)] = match.group(2).strip()
        if match := re.match(r"^LANGUAGE\s+(.+)$", code, re.IGNORECASE):
            language = match.group(1).strip()

        header = _resource_header(code, index + 1)
        if not header:
            index += 1
            continue
        symbol, resource_type, header_tail = header
        kind = resource_type.lower()
        end = _block_end(lines, index) if resource_type in _BLOCK_RESOURCE_TYPES else index
        if resource_type in _BLOCK_RESOURCE_TYPES and end == index:
            diagnostics.append(f"unterminated {resource_type} at line {index + 1}")
        snippet = "\n".join(lines[index : end + 1])
        resource = {
            "symbol_id": _resource_id(rel_path, kind, symbol, index + 1),
            "qualified_name": f"{rel_path}::{symbol}",
            "name": symbol,
            "kind": kind,
            "scope_name": None,
            "resource_symbol": symbol,
            "numeric_id": None,
            "language": language,
            "caption": "",
            "style": "",
            "asset_path": "",
            "condition": _condition_text(conditions),
            "encoding": encoding,
            "metadata_json": "{}",
            "file_path": rel_path,
            "start_byte": 0,
            "end_byte": 0,
            "start_line": index + 1,
            "end_line": end + 1,
            "arity": 0,
            "code": snippet,
            "comment": "",
            "summary": "",
            "note": "",
            "_header_tail": header_tail,
        }
        metadata: Dict[str, Any] = {}
        child_resources: List[Dict[str, Any]] = []
        if resource_type in {"DIALOG", "DIALOGEX"}:
            controls = _parse_dialog(lines, index, end, resource, resource["condition"])
            elements.extend(controls)
            for control in controls:
                relations.append(
                    {
                        "source_label": "Resource",
                        "target_label": "UIControl",
                        "rel_type": "CONTAINS",
                        "source_id": resource["symbol_id"],
                        "target_id": control["symbol_id"],
                        "properties": {},
                    }
                )
        elif resource_type == "STRINGTABLE":
            string_entries = _parse_string_table(lines, index, end)
            metadata = {symbol: value for symbol, value, _ in string_entries}
            resource["metadata_json"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            resource["note"] = _resource_note(resource, metadata)
            resource["summary"] = "; ".join(metadata.values())
            for entry_symbol, entry_text, entry_line in string_entries:
                child = {
                    "symbol_id": _resource_id(rel_path, "string", entry_symbol, entry_line),
                    "qualified_name": f"{rel_path}::{entry_symbol}",
                    "name": entry_symbol,
                    "kind": "string",
                    "scope_name": None,
                    "resource_symbol": entry_symbol,
                    "numeric_id": None,
                    "language": language,
                    "caption": entry_text,
                    "style": "",
                    "asset_path": "",
                    "condition": resource["condition"],
                    "encoding": encoding,
                    "metadata_json": "{}",
                    "file_path": rel_path,
                    "start_byte": 0,
                    "end_byte": 0,
                    "start_line": entry_line,
                    "end_line": entry_line,
                    "arity": 0,
                    "code": f'{entry_symbol} "{entry_text}"',
                    "comment": "",
                    "summary": entry_text,
                    "note": f"String resource {entry_symbol} | {entry_text}",
                }
                child_resources.append(child)
                relations.append(
                    {
                        "source_label": "Resource",
                        "target_label": "Resource",
                        "rel_type": "CONTAINS",
                        "source_id": resource["symbol_id"],
                        "target_id": child["symbol_id"],
                        "properties": {},
                    }
                )
        elif resource_type == "VERSIONINFO":
            metadata = _parse_value_metadata(lines, index, end)
            resource["metadata_json"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            resource["note"] = _resource_note(resource, metadata)
            resource["summary"] = str(metadata.get("FileDescription") or metadata.get("ProductName") or "")
        else:
            resource["asset_path"] = _quoted_value(header_tail)
            resource["metadata_json"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
            resource["note"] = _resource_note(resource, metadata)
        resource.pop("_header_tail", None)
        resources.append(resource)
        resources.extend(child_resources)
        index = end + 1

    file_comment = ""
    for line in lines[:20]:
        stripped = line.strip()
        if stripped.startswith("//"):
            file_comment = stripped[2:].strip()
            if file_comment:
                break
    file_note = (
        f"Windows resource script with {len(resources)} resources and {len(elements)} UI controls"
        if resources or elements
        else "Windows resource script with no structured resources"
    )
    return {
        "functions": [],
        "calls": [],
        "types": [],
        "namespaces": [],
        "relations": relations,
        "function_types": [],
        "fields": [],
        "aliases": [],
        "templates": [],
        "resources": resources,
        "resource_elements": elements,
        "file_def": {
            "file_path": rel_path,
            "start_line": 1,
            "end_line": max(1, len(lines)),
            "code": text,
            "comment": file_comment,
            "summary": file_comment,
            "note": file_note,
        },
        "using_namespaces": [],
        "using_imports": {},
        "includes": list(dict.fromkeys(includes)),
        "macros": macros,
        "parse_meta": {
            "parser_language": "windows_rc",
            "parser_language_initial": "windows_rc",
            "encoding": encoding,
            "lossy_decode": lossy,
            "header_retry_attempted": False,
            "header_retry_selected": False,
            "has_error": bool(diagnostics),
            "error_nodes": len(diagnostics),
            "error_nodes_initial": len(diagnostics),
            "header_retry_error_nodes": None,
            "header_retry_has_error": None,
            "diagnostics": diagnostics,
        },
    }


def extract_resource_tokens(text: str, known_symbols: Iterable[str]) -> List[str]:
    """Return resource identifiers explicitly present in a C/C++ snippet."""

    known = set(known_symbols)
    if not known:
        return []
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text)
    return list(dict.fromkeys(token for token in tokens if token in known and token != "IDC_STATIC"))


def extract_message_map_handlers(
    text: str,
    known_symbols: Iterable[str],
) -> List[Dict[str, Any]]:
    """Extract explicit MFC ``ON_*`` resource-to-handler declarations."""

    known = set(known_symbols)
    results: List[Dict[str, Any]] = []
    for match in re.finditer(r"\b(ON_[A-Z0-9_]+)\s*\(([^)]*)\)", text, re.MULTILINE):
        macro = match.group(1)
        fields = _split_fields(match.group(2))
        resource_symbol = next((field.strip() for field in fields if field.strip() in known), "")
        if not resource_symbol or resource_symbol == "IDC_STATIC":
            continue
        handler = fields[-1].strip().lstrip("&") if fields else ""
        handler = re.sub(r"\s+", "", handler)
        if not re.match(r"^(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*$", handler):
            continue
        results.append(
            {
                "macro": macro,
                "resource_symbol": resource_symbol,
                "handler": handler,
                "line": text.count("\n", 0, match.start()) + 1,
            }
        )
    return results

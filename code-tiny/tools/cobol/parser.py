"""Tree-sitter-assisted, source-evidence-preserving COBOL extraction."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
from typing import Iterable

from .models import (
    Diagnostic,
    ParsedCopy,
    ParsedDataItem,
    ParsedFile,
    ParsedFileBinding,
    ParsedParagraph,
    ParsedStatement,
    SourceEvidence,
)


COBOL_EXTENSIONS = frozenset({".cbl", ".cob", ".cpy", ".copy"})
COPYBOOK_EXTENSIONS = (".cpy", ".copy", ".cbl", ".cob")
_NAME = r"[A-Z0-9][A-Z0-9-]*"
_DIVISION_RE = re.compile(rf"^\s*({_NAME})\s+DIVISION\s*\.", re.IGNORECASE)
_SECTION_RE = re.compile(rf"^\s*({_NAME}(?:-{_NAME})*)\s+SECTION\s*\.", re.IGNORECASE)
_PARAGRAPH_RE = re.compile(rf"^\s*({_NAME})\s*\.\s*(?:\*>.*)?$", re.IGNORECASE)
_DATA_RE = re.compile(rf"^\s*(\d{{1,2}})\s+({_NAME})(.*?)(?:\.\s*)?$", re.IGNORECASE)
_PROGRAM_RE = re.compile(rf"\bPROGRAM-ID\s*\.\s*({_NAME})", re.IGNORECASE)
_COPY_RE = re.compile(rf"\bCOPY\s+['\"]?({_NAME})['\"]?(?:\s+(?:OF|IN)\s+{_NAME})?(.*?)(?:\.\s*)?$", re.IGNORECASE)
_SELECT_RE = re.compile(rf"\bSELECT\s+(?:OPTIONAL\s+)?({_NAME}).*?\bASSIGN(?:\s+TO)?\s+(.+?)(?:\.\s*)?$", re.IGNORECASE)
_FD_RE = re.compile(rf"^\s*(?:FD|SD)\s+({_NAME})", re.IGNORECASE)
_NON_PARAGRAPH_LABELS = frozenset({
    "ELSE", "END-IF", "END-EVALUATE", "END-PERFORM", "END-EXEC",
    "EXIT", "GOBACK", "CONTINUE", "NEXT", "RETURN", "STOP",
})


def iter_cobol_files(root: Path, extensions: Iterable[str] = COBOL_EXTENSIONS) -> list[Path]:
    allowed = {value.lower() if value.startswith(".") else f".{value.lower()}" for value in extensions}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in allowed and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def _decode_source(data: bytes) -> tuple[str, str, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    try:
        return data.decode("utf-8-sig"), "utf-8-sig", diagnostics
    except UnicodeDecodeError:
        pass
    western = data.decode("cp1252")
    try:
        ebcdic = data.decode("cp037")
        keywords = ("DIVISION", "PROGRAM-ID", "PROCEDURE", "WORKING-STORAGE", "COPY", " PIC ")
        western_score = sum(keyword in western.upper() for keyword in keywords)
        ebcdic_score = sum(keyword in ebcdic.upper() for keyword in keywords)
        if ebcdic_score > western_score and ebcdic_score > 0:
            diagnostics.append(Diagnostic("COBOL_ENCODING_EBCDIC", "decoded source as EBCDIC cp037", "info"))
            return ebcdic, "cp037", diagnostics
    except UnicodeDecodeError:
        pass
    diagnostics.append(Diagnostic("COBOL_ENCODING_WESTERN", "decoded non-UTF-8 source as Windows-1252", "info"))
    return western, "cp1252", diagnostics


def detect_source_format(lines: list[str]) -> str:
    directive = "\n".join(lines[:20]).upper()
    if re.search(r">>SOURCE\s+FORMAT\s+(?:IS\s+)?FREE", directive):
        return "free"
    if re.search(r">>SOURCE\s+FORMAT\s+(?:IS\s+)?FIXED", directive):
        return "fixed"
    candidates = [line for line in lines if line.strip()][:40]
    fixed = sum(len(line) >= 7 and line[:6].strip().isdigit() or (len(line) >= 7 and line[:7].isspace()) for line in candidates)
    return "fixed" if candidates and fixed >= max(2, len(candidates) // 2) else "free"


def detect_dialect(text: str) -> str:
    upper = text.upper()
    if "MICRO FOCUS" in upper or "$SET" in upper:
        return "micro-focus"
    if "GNUCOBOL" in upper or ">>SOURCE" in upper:
        return "gnucobol"
    if re.search(r"^\s*(?:\d{6}\s+)?(?:CBL|PROCESS)\b", upper, re.MULTILINE) or "EXEC CICS" in upper:
        return "ibm-enterprise"
    return "ansi"


def _source_codec(encoding: str) -> str:
    return "utf-8" if encoding == "utf-8-sig" else encoding


def _line_metrics(text: str, encoding: str, data: bytes) -> tuple[list[int], list[int]]:
    lines = text.splitlines()
    chunks = text.splitlines(keepends=True)
    codec = _source_codec(encoding)
    current = 3 if encoding == "utf-8-sig" and data.startswith(b"\xef\xbb\xbf") else 0
    offsets: list[int] = []
    lengths: list[int] = []
    for index, line in enumerate(lines):
        offsets.append(current)
        lengths.append(len(line.encode(codec, errors="replace")))
        chunk = chunks[index] if index < len(chunks) else line
        current += len(chunk.encode(codec, errors="replace"))
    return offsets, lengths


def _code_text(line: str, source_format: str) -> str:
    if source_format != "fixed" or len(line) < 7:
        return line
    indicator = line[6]
    if indicator in {"*", "/"}:
        return ""
    return line[7:]


def _evidence(
    path: str,
    lines: list[str],
    offsets: list[int],
    byte_lengths: list[int],
    start: int,
    end: int | None = None,
) -> SourceEvidence:
    end_index = start if end is None else end
    end_text = lines[end_index] if end_index < len(lines) else ""
    return SourceEvidence(
        file=path,
        start_line=start + 1,
        start_column=1,
        end_line=end_index + 1,
        end_column=len(end_text) + 1,
        start_byte=offsets[start] if start < len(offsets) else 0,
        end_byte=(offsets[end_index] + byte_lengths[end_index]) if end_index < len(offsets) else 0,
    )


def _tree_diagnostics(
    parser,
    text: str,
    path: str,
    *,
    encoding: str,
    offsets: list[int],
    source_length: int,
) -> tuple[list[Diagnostic], int]:
    tree = parser.parse(text.encode("utf-8"))
    lines = text.splitlines()
    codec = _source_codec(encoding)

    def original_byte(row: int, utf8_column: int) -> int:
        if row >= len(lines) or row >= len(offsets):
            return source_length
        prefix_bytes = lines[row].encode("utf-8")[:utf8_column]
        prefix = prefix_bytes.decode("utf-8", errors="ignore")
        return offsets[row] + len(prefix.encode(codec, errors="replace"))

    diagnostics: list[Diagnostic] = []
    error_count = 0
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            error_count += 1
            code = "COBOL_SYNTAX_MISSING" if node.is_missing else "COBOL_SYNTAX_ERROR"
            diagnostics.append(
                Diagnostic(
                    code,
                    f"Tree-sitter reported {node.type} while parsing source",
                    evidence=SourceEvidence(
                        path,
                        node.start_point.row + 1,
                        node.start_point.column + 1,
                        node.end_point.row + 1,
                        node.end_point.column + 1,
                        original_byte(node.start_point.row, node.start_point.column),
                        original_byte(node.end_point.row, node.end_point.column),
                    ),
                    details={"node_type": node.type},
                )
            )
        stack.extend(reversed(node.named_children))
    return diagnostics, error_count


def _statement(text: str, evidence: SourceEvidence, degraded: bool) -> ParsedStatement | None:
    upper = " ".join(text.upper().split())
    confidence = 0.65 if degraded else 1.0
    if not upper or upper.startswith(("*", "*>")):
        return None
    if upper.startswith("EXEC SQL"):
        op = re.search(r"EXEC\s+SQL\s+([A-Z]+)", upper)
        tables = re.findall(r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+([A-Z0-9_.-]+)", upper)
        hosts = re.findall(r":([A-Z0-9-]+)", upper)
        return ParsedStatement("sql", text.strip(), evidence, {"operation": op.group(1) if op else "", "targets": tables, "host_variables": hosts}, confidence)
    if upper.startswith("EXEC CICS"):
        op = re.search(r"EXEC\s+CICS\s+([A-Z]+)", upper)
        resources = re.findall(r"\b(?:PROGRAM|FILE|DATASET|QUEUE|TRANSID)\s*\(?\s*['\"]?([A-Z0-9_.-]+)", upper)
        return ParsedStatement("cics", text.strip(), evidence, {"operation": op.group(1) if op else "", "resources": resources}, confidence)
    call = re.search(rf"\bCALL\s+((?:['\"][^'\"]+['\"])|(?:{_NAME}))", upper)
    if call:
        raw = call.group(1)
        literal = raw[:1] in {"'", '"'}
        return ParsedStatement("call", text.strip(), evidence, {"target": raw.strip("'\""), "literal": literal}, confidence if literal else min(confidence, 0.6))
    perform = re.search(rf"\bPERFORM\s+({_NAME})(?:\s+(?:THRU|THROUGH)\s+({_NAME}))?", upper)
    if perform and perform.group(1) not in {"UNTIL", "VARYING", "WITH", "TEST"}:
        props = {"target": perform.group(1), "through": perform.group(2) or ""}
        if " UNTIL " in f" {upper} ":
            props["loop"] = "until"
        if " VARYING " in f" {upper} ":
            props["loop"] = "varying"
        return ParsedStatement("perform", text.strip(), evidence, props, confidence)
    goto = re.search(r"\bGO\s+TO\s+(.+)", upper)
    if goto:
        body = goto.group(1).rstrip(".")
        target_text, _, selector = body.partition(" DEPENDING ON ")
        targets = re.findall(_NAME, target_text)
        return ParsedStatement("goto", text.strip(), evidence, {"targets": targets, "selector": selector.strip(), "dynamic": bool(selector)}, min(confidence, 0.7) if selector else confidence)
    alter = re.search(rf"\bALTER\s+({_NAME})\s+TO\s+PROCEED\s+TO\s+({_NAME})", upper)
    if alter:
        return ParsedStatement("alter", text.strip(), evidence, {"source": alter.group(1), "target": alter.group(2)}, min(confidence, 0.5))
    open_statement = re.match(rf"\s*OPEN\s+(INPUT|OUTPUT|I-O|EXTEND)\s+({_NAME})", upper)
    if open_statement:
        return ParsedStatement(
            "io",
            text.strip(),
            evidence,
            {"operation": "OPEN", "mode": open_statement.group(1), "target": open_statement.group(2)},
            confidence,
        )
    io = re.match(rf"\s*(CLOSE|READ|WRITE|REWRITE|DELETE|START)\s+({_NAME})", upper)
    if io:
        return ParsedStatement("io", text.strip(), evidence, {"operation": io.group(1), "target": io.group(2)}, confidence)
    if re.match(r"\s*(?:EXIT(?:\s+PROGRAM)?|STOP\s+RUN|GOBACK)\b", upper):
        terminal = "STOP RUN" in upper or "GOBACK" in upper or "EXIT PROGRAM" in upper
        return ParsedStatement("exit", text.strip(), evidence, {"terminal": terminal}, confidence)
    if re.match(r"\s*(?:IF|ELSE|EVALUATE|WHEN)\b", upper):
        return ParsedStatement("conditional", text.strip(), evidence, {}, confidence)
    words = re.findall(_NAME, upper)
    if words:
        return ParsedStatement("statement", text.strip(), evidence, {"words": words}, confidence)
    return None


def parse_file(path: Path, root: Path, parser) -> ParsedFile:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = f"@copybook/{path.parent.name}/{path.name}"
    data = path.read_bytes()
    text, encoding, diagnostics = _decode_source(data)
    lines = text.splitlines()
    offsets, byte_lengths = _line_metrics(text, encoding, data)
    tree_diagnostics, error_count = _tree_diagnostics(
        parser,
        text,
        relative,
        encoding=encoding,
        offsets=offsets,
        source_length=len(data),
    )
    diagnostics.extend(tree_diagnostics)
    if text.count("(") != text.count(")"):
        broken_line = next((line for line, value in enumerate(lines) if value.count("(") != value.count(")")), 0)
        diagnostics.append(
            Diagnostic(
                "COBOL_SYNTAX_UNBALANCED_DELIMITER",
                "source contains unbalanced parentheses",
                evidence=_evidence(relative, lines, offsets, byte_lengths, broken_line),
            )
        )
        error_count += 1
    source_format = detect_source_format(lines)
    dialect = detect_dialect(text)
    program_match = _PROGRAM_RE.search(text)
    program_name = program_match.group(1).upper() if program_match else ""
    is_copybook = path.suffix.lower() in {".cpy", ".copy"} or not program_name

    divisions: list[str] = []
    sections: list[str] = []
    data_items: list[ParsedDataItem] = []
    copies: list[ParsedCopy] = []
    selects: dict[str, ParsedFileBinding] = {}
    described: set[str] = set()
    paragraph_rows: list[dict[str, object]] = []
    current_division = ""
    current_storage = ""
    current_section = ""
    current_paragraph: dict[str, object] | None = None
    degraded_lines: set[int] = set()
    for diagnostic in tree_diagnostics:
        if diagnostic.evidence:
            degraded_lines.update(range(diagnostic.evidence.start_line - 1, diagnostic.evidence.end_line))

    index = 0
    while index < len(lines):
        raw = lines[index]
        code = _code_text(raw, source_format)
        stripped = code.strip()
        upper = stripped.upper()
        if not stripped or upper.startswith("*>"):
            index += 1
            continue
        division = _DIVISION_RE.match(code)
        if division:
            current_division = division.group(1).upper()
            divisions.append(current_division)
            current_storage = ""
            current_section = ""
            index += 1
            continue
        section = _SECTION_RE.match(code)
        if section:
            name = section.group(1).upper()
            sections.append(name)
            if current_division == "PROCEDURE":
                current_section = name
            elif current_division == "DATA":
                current_storage = name
            index += 1
            continue
        copy = _COPY_RE.search(code)
        if copy:
            tail = copy.group(2).strip()
            replacing = tail if tail.upper().startswith("REPLACING") else ""
            copies.append(ParsedCopy(copy.group(1).upper(), _evidence(relative, lines, offsets, byte_lengths, index), replacing))
        select = _SELECT_RE.search(code)
        if select:
            name = select.group(1).upper()
            selects[name] = ParsedFileBinding(name, _evidence(relative, lines, offsets, byte_lengths, index), select.group(2).strip().strip("'\""), False)
        fd = _FD_RE.match(code)
        if fd:
            described.add(fd.group(1).upper())
        if current_division == "DATA" or is_copybook:
            item = _DATA_RE.match(code)
            if item:
                level = int(item.group(1))
                name = item.group(2).upper()
                tail = item.group(3)
                pic = re.search(r"\bPIC(?:TURE)?\s+([^\s.]+(?:\([^)]*\))?)", tail, re.IGNORECASE)
                usage = re.search(r"\bUSAGE(?:\s+IS)?\s+([A-Z0-9-]+)", tail, re.IGNORECASE)
                value = re.search(r"\bVALUE(?:\s+IS)?\s+(.+?)(?=\s+(?:REDEFINES|OCCURS|USAGE|PIC)\b|\.$|$)", tail, re.IGNORECASE)
                redefines = re.search(rf"\bREDEFINES\s+({_NAME})", tail, re.IGNORECASE)
                occurs = re.search(r"\bOCCURS\s+(.+?)(?=\s+(?:PIC|USAGE|VALUE|REDEFINES)\b|\.$|$)", tail, re.IGNORECASE)
                data_items.append(
                    ParsedDataItem(
                        name,
                        level,
                        current_storage or ("COPYBOOK" if is_copybook else "DATA"),
                        _evidence(relative, lines, offsets, byte_lengths, index),
                        pic.group(1).upper() if pic else "",
                        usage.group(1).upper() if usage else "",
                        value.group(1).strip() if value else "",
                        redefines.group(1).upper() if redefines else "",
                        occurs.group(1).strip() if occurs else "",
                    )
                )
        if current_division == "PROCEDURE":
            paragraph = _PARAGRAPH_RE.match(code)
            if paragraph and paragraph.group(1).upper() not in _NON_PARAGRAPH_LABELS:
                current_paragraph = {
                    "name": paragraph.group(1).upper(),
                    "section": current_section,
                    "ordinal": len(paragraph_rows),
                    "evidence": _evidence(relative, lines, offsets, byte_lengths, index),
                    "statements": [],
                }
                paragraph_rows.append(current_paragraph)
                index += 1
                continue
            if current_paragraph is not None:
                end_index = index
                statement_text = code
                if re.search(r"\bEXEC\s+(?:SQL|CICS)\b", upper) and "END-EXEC" not in upper:
                    block = [code]
                    while end_index + 1 < len(lines):
                        end_index += 1
                        block.append(_code_text(lines[end_index], source_format))
                        if "END-EXEC" in lines[end_index].upper():
                            break
                    statement_text = "\n".join(block)
                fact = _statement(
                    statement_text,
                    _evidence(relative, lines, offsets, byte_lengths, index, end_index),
                    any(line in degraded_lines for line in range(index, end_index + 1)),
                )
                if fact:
                    current_paragraph["statements"].append(fact)
                index = end_index + 1
                continue
        index += 1

    paragraphs = tuple(
        ParsedParagraph(
            str(row["name"]),
            str(row["section"]),
            int(row["ordinal"]),
            row["evidence"],
            tuple(row["statements"]),
        )
        for row in paragraph_rows
    )
    bindings = tuple(
        replace(binding, has_description=name in described)
        for name, binding in sorted(selects.items())
    )
    for name in sorted(described - set(selects)):
        evidence = next((item.evidence for item in data_items if item.storage == "FILE"), SourceEvidence(relative, 1))
        bindings += (ParsedFileBinding(name, evidence, "", True),)
    for copy in copies:
        if copy.replacing:
            diagnostics.append(
                Diagnostic(
                    "COBOL_COPY_REPLACING_PARTIAL",
                    f"COPY {copy.name} REPLACING is retained but substitution is not applied",
                    evidence=copy.evidence,
                    details={"replacement": copy.replacing},
                )
            )
    return ParsedFile(
        relative,
        program_name,
        source_format,
        dialect,
        encoding,
        is_copybook,
        tuple(dict.fromkeys(divisions)),
        tuple(dict.fromkeys(sections)),
        paragraphs,
        tuple(data_items),
        tuple(copies),
        bindings,
        tuple(diagnostics),
        error_count,
    )


def parse_paths(paths: Iterable[Path], root: Path, parser) -> tuple[ParsedFile, ...]:
    return tuple(parse_file(path, root, parser) for path in sorted(paths))

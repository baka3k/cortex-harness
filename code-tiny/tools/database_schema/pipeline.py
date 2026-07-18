from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import (
    DatabaseAnalysisResult,
    DatabaseObjectFact,
    DatabaseRelationshipFact,
    normalize_identifier,
    stable_id,
)


_SQL_EXTENSIONS = frozenset({".sql", ".ddl", ".dml", ".psql"})
_PLSQL_EXTENSIONS = frozenset({".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc"})
_SKIP_DIRS = frozenset({".git", ".cache", ".venv", "venv", "node_modules", "vendor", "dist", "build", "bin", "obj"})
_IDENTIFIER = r'(?:[A-Za-z_$#][\w$#]*|"[^"]+"|`[^`]+`|\[[^\]]+\])(?:\s*\.\s*(?:[A-Za-z_$#][\w$#]*|"[^"]+"|`[^`]+`|\[[^\]]+\]))?'
_OBJECT_RE = re.compile(
    rf"\bcreate\s+(?:or\s+replace\s+)?(?P<kind>table|view|procedure|proc|function)\s+(?P<name>{_IDENTIFIER})",
    re.IGNORECASE,
)
_READ_RE = re.compile(rf"\b(?:from|join)\s+(?P<name>{_IDENTIFIER})", re.IGNORECASE)
_WRITE_RE = re.compile(rf"\b(?:insert\s+into|update|delete\s+from|merge\s+into)\s+(?P<name>{_IDENTIFIER})", re.IGNORECASE)
_REFERENCE_RE = re.compile(rf"\breferences\s+(?P<name>{_IDENTIFIER})", re.IGNORECASE)


def _mask_non_code(text: str) -> str:
    chars = list(text)
    patterns = (
        re.compile(r"--[^\n]*"),
        re.compile(r"/\*.*?\*/", re.DOTALL),
        re.compile(r"'(?:''|[^'])*'", re.DOTALL),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            for index in range(match.start(), match.end()):
                if chars[index] != "\n":
                    chars[index] = " "
    return "".join(chars)


def _files(root: Path, dialects: Sequence[str]) -> Iterable[Tuple[Path, str]]:
    enabled = {str(value).strip().lower() for value in dialects}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        extension = path.suffix.lower()
        if extension in _SQL_EXTENSIONS and "sql" in enabled:
            yield path, "sql"
        elif extension in _PLSQL_EXTENSIONS and "plsql" in enabled:
            yield path, "plsql"


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _object_label(kind: str) -> str:
    lowered = kind.lower()
    if lowered == "table":
        return "Table"
    if lowered == "view":
        return "View"
    return "Procedure"


def analyze_project(
    root: Path | str,
    project_id: str,
    dialects: Sequence[str],
    selected_paths: Optional[Sequence[str]] = None,
) -> DatabaseAnalysisResult:
    root_path = Path(root).resolve()
    selected = {str(path).replace("\\", "/") for path in selected_paths or ()}
    objects: Dict[str, DatabaseObjectFact] = {}
    relationships: Dict[str, DatabaseRelationshipFact] = {}

    def ensure_table(schema: str, name: str, dialect: str, rel: str, line: int, declared: bool = False) -> DatabaseObjectFact:
        object_id = stable_id(project_id, "Table", schema, name)
        current = objects.get(object_id)
        if current is None or (declared and not current.declared):
            objects[object_id] = DatabaseObjectFact(
                object_id, project_id, "Table", name, schema, dialect, rel, line, declared, "table",
            )
        return objects[object_id]

    for path, dialect in _files(root_path, dialects):
        rel = path.relative_to(root_path).as_posix()
        if selected and rel not in selected:
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        code = _mask_non_code(raw)
        matches = list(_OBJECT_RE.finditer(code))
        for index, match in enumerate(matches):
            kind = match.group("kind").lower()
            schema, name = normalize_identifier(match.group("name"))
            if not name:
                continue
            label = _object_label(kind)
            start_line = _line(raw, match.start())
            source_id = stable_id(project_id, label, schema, name)
            source = DatabaseObjectFact(
                source_id, project_id, label, name, schema, dialect, rel, start_line, True, kind,
            )
            objects[source_id] = source
            if label == "Table":
                ensure_table(schema, name, dialect, rel, start_line, True)
            end = matches[index + 1].start() if index + 1 < len(matches) else len(code)
            body = code[match.end():end]
            references: Dict[Tuple[str, str], Set[str]] = {}
            for read in _READ_RE.finditer(body):
                target_schema, target_name = normalize_identifier(read.group("name"))
                if target_name:
                    references.setdefault((target_schema, target_name), set()).add("READS_FROM")
            for write in _WRITE_RE.finditer(body):
                target_schema, target_name = normalize_identifier(write.group("name"))
                if target_name:
                    references.setdefault((target_schema, target_name), set()).add("WRITES_TO")
            for reference in _REFERENCE_RE.finditer(body):
                target_schema, target_name = normalize_identifier(reference.group("name"))
                if target_name:
                    references.setdefault((target_schema, target_name), set()).add("REFERENCES_TABLE")
            for (target_schema, target_name), semantic_types in references.items():
                target = ensure_table(target_schema, target_name, dialect, rel, start_line)
                rel_types = set(semantic_types) | {"REFERENCES_TABLE"}
                for rel_type in rel_types:
                    relationship_id = stable_id(source_id, rel_type, target.object_id)
                    relationships[relationship_id] = DatabaseRelationshipFact(
                        relationship_id, project_id, rel_type, source_id, label, name,
                        target.object_id, "Table", target_name, dialect, rel, start_line,
                    )
    return DatabaseAnalysisResult(
        project_id=project_id,
        objects=tuple(sorted(objects.values(), key=lambda item: (item.label, item.schema_name, item.name))),
        relationships=tuple(sorted(relationships.values(), key=lambda item: (item.source_id, item.rel_type, item.target_id))),
    )


__all__ = ["DatabaseAnalysisResult", "analyze_project"]

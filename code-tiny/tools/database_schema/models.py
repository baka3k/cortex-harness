from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Tuple


def normalize_identifier(value: str) -> Tuple[str, str]:
    cleaned = value.strip().rstrip(";,)")
    parts = [part.strip('`"[]').lower() for part in cleaned.split(".") if part]
    if not parts:
        return "", ""
    return (".".join(parts[:-1]), parts[-1])


def stable_id(*parts: object) -> str:
    normalized = "\x1f".join(str(part).strip().lower() for part in parts)
    return "db::" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class DatabaseObjectFact:
    object_id: str
    project_id: str
    label: str
    name: str
    schema_name: str
    dialect: str
    file_path: str
    start_line: int
    declared: bool = True
    object_kind: str = ""

    def node_row(self) -> Dict[str, object]:
        return {
            "id": self.object_id,
            "symbol_id": self.object_id,
            "project_id": self.project_id,
            "name": self.name,
            "qualified_name": f"{self.schema_name}.{self.name}" if self.schema_name else self.name,
            "schema_name": self.schema_name,
            "dialect": self.dialect,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "declared": self.declared,
            "object_kind": self.object_kind or self.label.lower(),
            "label": self.label,
        }


@dataclass(frozen=True)
class DatabaseRelationshipFact:
    relationship_id: str
    project_id: str
    rel_type: str
    source_id: str
    source_label: str
    source_name: str
    target_id: str
    target_label: str
    target_name: str
    dialect: str
    file_path: str
    start_line: int

    def row(self) -> Dict[str, object]:
        return {
            "id": self.relationship_id,
            "project_id": self.project_id,
            "type": self.rel_type,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "target_id": self.target_id,
            "target_label": self.target_label,
            "dialect": self.dialect,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "confidence": 0.9,
        }


@dataclass(frozen=True)
class DatabaseAnalysisResult:
    project_id: str
    objects: Tuple[DatabaseObjectFact, ...]
    relationships: Tuple[DatabaseRelationshipFact, ...]

    def graph_rows(self) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
        return (
            [item.node_row() for item in self.objects],
            [item.row() for item in self.relationships],
        )

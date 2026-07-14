"""Typed records shared by the Python Dart parser and graph pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class SourceEvidence:
    file: str
    offset: int = 0
    length: int = 0
    start_line: int = 1
    start_column: int = 1
    end_line: int = 1
    end_column: int = 1

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceEvidence":
        return cls(
            file=str(value["file"]),
            offset=int(value.get("offset", 0)),
            length=int(value.get("length", 0)),
            start_line=int(value.get("start_line", 1)),
            start_column=int(value.get("start_column", 1)),
            end_line=int(value.get("end_line", value.get("start_line", 1))),
            end_column=int(value.get("end_column", value.get("start_column", 1))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "offset": self.offset,
            "length": self.length,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }


@dataclass(frozen=True)
class HeaderRecord:
    schema_version: str
    analyzer_version: str
    sdk_version: str
    root: str
    project_id: str
    mode: str = "dart"
    record_type: str = field(default="header", init=False)


@dataclass(frozen=True)
class NodeRecord:
    identity: str
    kind: str
    properties: Mapping[str, Any]
    evidence: SourceEvidence
    record_type: str = field(default="node", init=False)


@dataclass(frozen=True)
class EdgeRecord:
    source: str
    target: str
    relationship: str
    evidence: SourceEvidence
    properties: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    record_type: str = field(default="edge", init=False)


@dataclass(frozen=True)
class DiagnosticRecord:
    severity: str
    code: str
    message: str
    recoverable: bool
    evidence: Optional[SourceEvidence] = None
    record_type: str = field(default="diagnostic", init=False)


@dataclass(frozen=True)
class SummaryRecord:
    processed_files: int
    skipped_files: int
    error_count: int
    elapsed_ms: int
    record_type: str = field(default="summary", init=False)


ProtocolRecord = HeaderRecord | NodeRecord | EdgeRecord | DiagnosticRecord | SummaryRecord


@dataclass(frozen=True)
class AnalysisFacts:
    header: HeaderRecord
    nodes: Tuple[NodeRecord, ...]
    edges: Tuple[EdgeRecord, ...]
    diagnostics: Tuple[DiagnosticRecord, ...]
    summary: SummaryRecord

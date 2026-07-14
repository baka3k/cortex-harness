"""Versioned, deterministic fact and parser models for COBOL analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Mapping, Tuple


SCHEMA_VERSION = "1"
ANALYZER_VERSION = "1.0.0"


def stable_id(project_id: str, kind: str, *parts: object) -> str:
    """Return a project-scoped checkout-independent identity."""
    value = "\x1f".join((project_id, kind, *(str(part) for part in parts)))
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"cobol:{kind.lower()}:{digest}"


@dataclass(frozen=True, order=True)
class SourceEvidence:
    file: str
    start_line: int
    start_column: int = 1
    end_line: int = 1
    end_column: int = 1
    start_byte: int = 0
    end_byte: int = 0


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    recoverable: bool = True
    evidence: SourceEvidence | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticNode:
    id: str
    label: str
    name: str
    file_path: str
    evidence: SourceEvidence
    properties: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True)
class SemanticEdge:
    id: str
    source_id: str
    target_id: str
    relationship: str
    evidence: SourceEvidence
    properties: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    dynamic: bool = False


@dataclass(frozen=True)
class AnalysisSummary:
    processed_files: int
    node_count: int
    edge_count: int
    diagnostic_count: int
    syntax_error_count: int
    analyzer_version: str = ANALYZER_VERSION
    schema_version: str = SCHEMA_VERSION
    runtime: Mapping[str, Any] = field(default_factory=dict)
    invalidated_files: int = 0


@dataclass(frozen=True)
class AnalysisResult:
    project_id: str
    root: str
    nodes: Tuple[SemanticNode, ...]
    edges: Tuple[SemanticEdge, ...]
    diagnostics: Tuple[Diagnostic, ...]
    summary: AnalysisSummary

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=True) + "\n"


@dataclass(frozen=True)
class ParsedStatement:
    kind: str
    text: str
    evidence: SourceEvidence
    properties: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True)
class ParsedParagraph:
    name: str
    section: str
    ordinal: int
    evidence: SourceEvidence
    statements: Tuple[ParsedStatement, ...]


@dataclass(frozen=True)
class ParsedDataItem:
    name: str
    level: int
    storage: str
    evidence: SourceEvidence
    picture: str = ""
    usage: str = ""
    value: str = ""
    redefines: str = ""
    occurs: str = ""


@dataclass(frozen=True)
class ParsedCopy:
    name: str
    evidence: SourceEvidence
    replacing: str = ""


@dataclass(frozen=True)
class ParsedFileBinding:
    name: str
    evidence: SourceEvidence
    assignment: str = ""
    has_description: bool = False


@dataclass(frozen=True)
class ParsedFile:
    path: str
    program_name: str
    source_format: str
    dialect: str
    encoding: str
    is_copybook: bool
    divisions: Tuple[str, ...]
    sections: Tuple[str, ...]
    paragraphs: Tuple[ParsedParagraph, ...]
    data_items: Tuple[ParsedDataItem, ...]
    copies: Tuple[ParsedCopy, ...]
    file_bindings: Tuple[ParsedFileBinding, ...]
    diagnostics: Tuple[Diagnostic, ...]
    tree_error_count: int = 0


def sorted_result(
    *,
    project_id: str,
    root: str,
    nodes: list[SemanticNode],
    edges: list[SemanticEdge],
    diagnostics: list[Diagnostic],
    runtime: Mapping[str, Any],
    processed_files: int,
    syntax_error_count: int,
    invalidated_files: int = 0,
) -> AnalysisResult:
    ordered_nodes = tuple(sorted(nodes, key=lambda item: (item.label, item.file_path, item.id)))
    ordered_edges = tuple(sorted(edges, key=lambda item: (item.relationship, item.source_id, item.target_id, item.id)))
    ordered_diagnostics = tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                item.evidence.file if item.evidence else "",
                item.evidence.start_line if item.evidence else 0,
                item.code,
                item.message,
            ),
        )
    )
    summary = AnalysisSummary(
        processed_files=processed_files,
        node_count=len(ordered_nodes),
        edge_count=len(ordered_edges),
        diagnostic_count=len(ordered_diagnostics),
        syntax_error_count=syntax_error_count,
        runtime=dict(runtime),
        invalidated_files=invalidated_files,
    )
    return AnalysisResult(project_id, root, ordered_nodes, ordered_edges, ordered_diagnostics, summary)

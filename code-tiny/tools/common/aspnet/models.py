from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Tuple

from .identity import normalize_relative_path
from .safe_formats import graph_property_value, redact_value


ASPNET_PROTOCOL_VERSION = "aspnet-roslyn-v1"
ASPNET_MODEL_VERSION = "aspnet-semantic-v1"

ASPNET_NODE_LABELS = frozenset({
    "HttpEndpoint", "Route", "Middleware", "Controller", "Action", "RazorPage",
    "PageHandler", "WebFormPage", "HttpHandler", "HttpModule", "Filter", "Result",
    "View", "Layout", "PartialView", "Service", "Repository", "Model", "ViewModel",
    "ValidationRule", "ConfigurationKey", "SessionState", "ApplicationEvent",
    "AuthenticationScheme", "AuthorizationPolicy",
})

ASPNET_RELATIONSHIP_TYPES = frozenset({
    "MAPPED_TO", "HANDLED_BY", "PASSES_THROUGH", "INVOKES", "INJECTS",
    "VALIDATES_WITH", "RENDERS", "REDIRECTS_TO", "FORWARDS_TO", "LOADS_FROM",
    "DEPENDS_ON", "READS_CONFIG", "WRITES_SESSION", "POSTS_BACK_TO", "INITIALIZES",
    "RETURNS_RESULT", "SEMANTIC_OF",
})


@dataclass(frozen=True, order=True)
class SourceSpan:
    file_path: str
    start_line: int = 1
    end_line: int = 1
    start_column: int = 1
    end_column: int = 1

    def normalized(self) -> "SourceSpan":
        return SourceSpan(
            normalize_relative_path(self.file_path),
            max(1, int(self.start_line)),
            max(max(1, int(self.start_line)), int(self.end_line)),
            max(1, int(self.start_column)),
            max(1, int(self.end_column)),
        )


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParserCapability:
    name: str
    available: bool
    mandatory: bool
    mode: str
    status: str = "ok"
    message: str = ""


@dataclass(frozen=True)
class AnalysisModule:
    module_id: str
    module_path: str
    framework: str
    evidence: Tuple[str, ...] = ()
    confidence: float = 0.0
    artifacts: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticFact:
    kind: str
    stable_id: str
    name: str
    framework: str
    project_id: str
    project_name: str
    module_id: str
    source: SourceSpan
    confidence: float = 1.0
    resolution_status: str = "resolved"
    extraction_method: str = "source"
    source_symbol_id: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_graph_node(self, generation_id: str = "") -> Dict[str, Any]:
        if self.kind not in ASPNET_NODE_LABELS:
            raise ValueError(f"unsupported ASP.NET node label: {self.kind}")
        source = self.source.normalized()
        storage_id = f"{self.stable_id}::generation::{generation_id}" if generation_id else self.stable_id
        safe_properties = redact_value("properties", self.properties)
        row: Dict[str, Any] = {
            "id": storage_id,
            "semantic_id": self.stable_id,
            "symbol_id": self.stable_id,
            "generation_id": generation_id,
            "name": redact_value("name", self.name),
            "kind": self.kind,
            "framework": self.framework,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "module_id": self.module_id,
            "file_path": source.file_path,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "start_column": source.start_column,
            "end_column": source.end_column,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "extraction_method": self.extraction_method,
            "source_symbol_id": self.source_symbol_id,
            "parser_version": ASPNET_MODEL_VERSION,
        }
        for key, value in sorted(safe_properties.items()):
            if key not in row:
                row[key] = graph_property_value(value)
        return row


@dataclass(frozen=True)
class SemanticRelationship:
    stable_id: str
    relationship_type: str
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    framework: str
    project_id: str
    module_id: str
    source: SourceSpan
    confidence: float = 1.0
    resolution_status: str = "resolved"
    reason: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    from_generated: bool = True
    to_generated: bool = True

    def to_graph_row(self, generation_id: str = "") -> Dict[str, Any]:
        if self.relationship_type not in ASPNET_RELATIONSHIP_TYPES:
            raise ValueError(f"unsupported ASP.NET relationship type: {self.relationship_type}")
        storage = lambda value, generated: (
            f"{value}::generation::{generation_id}" if generation_id and generated else value
        )
        source = self.source.normalized()
        return {
            "id": storage(self.stable_id, True),
            "semantic_id": self.stable_id,
            "type": self.relationship_type,
            "from_id": storage(self.from_id, self.from_generated),
            "to_id": storage(self.to_id, self.to_generated),
            "from_label": self.from_label,
            "to_label": self.to_label,
            "framework": self.framework,
            "project_id": self.project_id,
            "module_id": self.module_id,
            "generation_id": generation_id,
            "source_file": source.file_path,
            "start_line": source.start_line,
            "end_line": source.end_line,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "reason": self.reason,
            "properties": {
                key: graph_property_value(value)
                for key, value in sorted(redact_value("properties", self.properties).items())
            },
        }


@dataclass(frozen=True)
class DependencyIndex:
    files: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    symbols: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    modules: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalysisResult:
    project_id: str
    project_name: str
    framework: str
    modules: Tuple[AnalysisModule, ...] = ()
    facts: Tuple[SemanticFact, ...] = ()
    relationships: Tuple[SemanticRelationship, ...] = ()
    capabilities: Tuple[ParserCapability, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    dependency_index: DependencyIndex = field(default_factory=DependencyIndex)
    coverage_status: str = "empty"
    module_coverage: Dict[str, str] = field(default_factory=dict)
    parser_version: str = ASPNET_MODEL_VERSION
    root: str = "."

    def to_dict(self) -> Dict[str, Any]:
        payload = redact_value("result", asdict(self))
        payload["root"] = "."
        return payload

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=True, sort_keys=True, indent=indent,
            separators=(",", ":") if indent is None else None,
        ) + "\n"


def dedupe_facts(values: Tuple[SemanticFact, ...] | list[SemanticFact]) -> Tuple[SemanticFact, ...]:
    by_id = {item.stable_id: item for item in values}
    return tuple(sorted(by_id.values(), key=lambda item: (item.kind, item.stable_id)))


def dedupe_relationships(
    values: Tuple[SemanticRelationship, ...] | list[SemanticRelationship],
) -> Tuple[SemanticRelationship, ...]:
    by_id = {item.stable_id: item for item in values}
    return tuple(sorted(by_id.values(), key=lambda item: (item.relationship_type, item.stable_id)))

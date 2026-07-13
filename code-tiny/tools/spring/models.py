from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


SPRING_PARSER_VERSION = "spring-v2026-07-13-1"


@dataclass(frozen=True)
class SourceSpan:
    file_path: str
    start_line: int = 1
    end_line: int = 1


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    file_path: str = ""
    start_line: int = 1
    end_line: int = 1


@dataclass(frozen=True)
class SpringModule:
    root: str
    rel_path: str
    languages: Tuple[str, ...]
    build_files: Tuple[str, ...]
    config_files: Tuple[str, ...]
    evidence: Tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class ConfigValue:
    key: str
    value: Any
    source: SourceSpan
    profile: str = ""
    raw_value: str = ""
    resolution_status: str = "resolved"


@dataclass(frozen=True)
class LanguageSourceFact:
    language: str
    file_path: str
    source_symbol_id: str
    package_name: str = ""
    declarations: Tuple[str, ...] = ()
    annotations: Tuple[str, ...] = ()
    parser_status: str = "not_parsed"


@dataclass(frozen=True)
class SpringFact:
    kind: str
    stable_id: str
    name: str
    source: SourceSpan
    project_id: str
    project_name: str
    language: str = "spring"
    confidence: float = 1.0
    extraction_method: str = "spring_foundation"
    resolution_status: str = "resolved"
    raw_value: str = ""
    resolved_value: str = ""
    source_symbol_id: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_graph_node(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": self.stable_id,
            "symbol_id": self.stable_id,
            "name": self.name,
            "kind": self.kind,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "language": self.language,
            "framework": "spring",
            "file_path": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "resolution_status": self.resolution_status,
            "raw_value": self.raw_value,
            "resolved_value": self.resolved_value,
            "source_symbol_id": self.source_symbol_id,
            "parser_version": SPRING_PARSER_VERSION,
        }
        row.update(self.properties)
        return row


@dataclass(frozen=True)
class SpringRelationship:
    type: str
    from_id: str
    to_id: str
    project_id: str
    confidence: float = 1.0
    resolution_status: str = "resolved"
    reason: str = ""
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_graph_row(self) -> Dict[str, Any]:
        props = {
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "reason": self.reason,
            "source_file": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
        }
        props.update(self.properties)
        return {
            "type": self.type,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "project_id": self.project_id,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "source_file": self.source.file_path,
            "properties": props,
        }


@dataclass(frozen=True)
class SpringAnalysisResult:
    project_id: str
    project_name: str
    root: str
    modules: Tuple[SpringModule, ...]
    config_values: Tuple[ConfigValue, ...]
    language_facts: Tuple[LanguageSourceFact, ...]
    semantic_facts: Tuple[SpringFact, ...]
    relationships: Tuple[SpringRelationship, ...]
    diagnostics: Tuple[Diagnostic, ...]
    parser_version: str = SPRING_PARSER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

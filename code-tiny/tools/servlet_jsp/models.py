from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Tuple


SERVLET_JSP_PARSER_VERSION = "servlet-jsp-v2026-07-13-1"
FRAMEWORK = "servlet_jsp"

SENSITIVE_KEY_RE = re.compile(
    r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential|authorization|cookie|session(?:id)?)",
    re.IGNORECASE,
)


def stable_digest(*parts: object, length: int = 20) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8", errors="surrogatepass")).hexdigest()[:length]


def stable_semantic_id(kind: str, project_id: str, module_id: str, *parts: object) -> str:
    safe_kind = re.sub(r"[^a-z0-9_]+", "_", kind.lower()).strip("_") or "fact"
    return f"servlet_jsp::{safe_kind}::{stable_digest(project_id, module_id, *parts)}"


def generation_storage_id(semantic_id: str, generation_id: str) -> str:
    return f"{semantic_id}::generation::{generation_id}" if generation_id else semantic_id


def redact_value(key: str, value: Any, max_length: int = 4096) -> Any:
    if SENSITIVE_KEY_RE.search(key or ""):
        return "[REDACTED]"
    if isinstance(value, str):
        if len(value) <= max_length:
            return value
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{value[:max_length]}...[sha256:{digest}]"
    if isinstance(value, Mapping):
        name_fields = {"name", "param_name", "key", "config_key", "raw_name"}
        value_fields = {"value", "param_value", "config_value", "raw_value", "resolved_value"}
        sensitive_record = any(
            str(k).lower() in name_fields and isinstance(v, str) and SENSITIVE_KEY_RE.search(v)
            for k, v in value.items()
        )
        result: Dict[str, Any] = {}
        for nested_key, nested_value in sorted(value.items(), key=lambda item: str(item[0])):
            normalized_key = str(nested_key)
            if sensitive_record and normalized_key.lower() in name_fields | value_fields:
                result[normalized_key] = "[REDACTED]"
            else:
                result[normalized_key] = redact_value(normalized_key, nested_value, max_length)
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(key, item, max_length) for item in value]
    return value


def graph_property_value(value: Any) -> Any:
    """Convert a redacted fact value to a Neo4j/FalkorDB property value."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        rows = list(value)
        if all(item is None or isinstance(item, (str, int, float, bool)) for item in rows):
            return rows
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, order=True)
class SourceSpan:
    file_path: str
    start_line: int = 1
    end_line: int = 1
    start_column: int = 1
    end_column: int = 1


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    file_path: str = ""
    start_line: int = 1
    end_line: int = 1
    hint: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParserCapability:
    language: str
    available: bool
    mandatory: bool
    parser: str
    package: str = ""
    package_version: str = ""
    abi_version: str = ""
    status: str = "ok"
    message: str = ""


@dataclass(frozen=True)
class ResourceBudgets:
    max_source_bytes: int = 4 * 1024 * 1024
    max_properties_bytes: int = 1024 * 1024
    max_el_bytes: int = 64 * 1024
    max_el_tokens: int = 2048
    max_el_nesting: int = 64
    max_jsp_regions: int = 50_000
    max_diagnostics_per_file: int = 1_000
    max_include_depth: int = 32
    max_include_edges_per_module: int = 100_000
    max_constant_steps_per_file: int = 10_000
    max_endpoint_filter_relationships: int = 250_000
    max_artifacts_per_project: int = 250_000
    max_total_source_bytes: int = 2 * 1024 * 1024 * 1024
    max_dependency_entries: int = 2_000_000
    max_facts_per_project: int = 2_000_000
    max_relationships_per_project: int = 4_000_000
    max_diagnostics_per_project: int = 100_000
    max_wall_time_seconds: float = 1_800.0
    max_peak_rss_bytes: int = 8 * 1024 * 1024 * 1024

    def fingerprint(self) -> str:
        values = asdict(self)
        # Operational abort guards decide whether a run succeeds; unlike
        # accepted-output budgets, they must not alter successful artifact or
        # generation identities.
        values.pop("max_wall_time_seconds", None)
        values.pop("max_peak_rss_bytes", None)
        return stable_digest(json.dumps(values, sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True)
class ServletJspArtifact:
    kind: str
    file_path: str
    module_id: str
    module_path: str
    evidence: Tuple[str, ...]
    confidence: float
    source: SourceSpan


@dataclass(frozen=True)
class ServletJspModule:
    module_id: str
    root: str
    rel_path: str
    java_files: Tuple[str, ...] = ()
    descriptor_files: Tuple[str, ...] = ()
    jsp_files: Tuple[str, ...] = ()
    properties_files: Tuple[str, ...] = ()
    build_files: Tuple[str, ...] = ()
    static_files: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()
    confidence: float = 0.0


@dataclass(frozen=True)
class ServletJspFact:
    kind: str
    stable_id: str
    name: str
    source: SourceSpan
    project_id: str
    project_name: str
    module_id: str
    language: str = "servlet_jsp"
    confidence: float = 1.0
    extraction_method: str = "servlet_jsp"
    resolution_status: str = "resolved"
    raw_value: str = ""
    resolved_value: str = ""
    source_symbol_id: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_graph_node(self, generation_id: str = "") -> Dict[str, Any]:
        semantic_id = self.stable_id
        safe_payload = redact_value(
            "fact",
            {
                "name": self.name,
                "raw_value": self.raw_value,
                "resolved_value": self.resolved_value,
                "properties": self.properties,
            },
        )
        row: Dict[str, Any] = {
            "id": generation_storage_id(semantic_id, generation_id),
            "semantic_id": semantic_id,
            "symbol_id": semantic_id,
            "generation_id": generation_id,
            "name": safe_payload["name"],
            "kind": self.kind,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "module_id": self.module_id,
            "language": self.language,
            "framework": FRAMEWORK,
            "file_path": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "start_column": self.source.start_column,
            "end_column": self.source.end_column,
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "resolution_status": self.resolution_status,
            "raw_value": safe_payload["raw_value"],
            "resolved_value": safe_payload["resolved_value"],
            "source_symbol_id": self.source_symbol_id,
            "parser_version": SERVLET_JSP_PARSER_VERSION,
        }
        for key, value in sorted(safe_payload["properties"].items()):
            if key not in row:
                row[key] = graph_property_value(value)
        return row


@dataclass(frozen=True)
class ServletJspRelationship:
    stable_id: str
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    type: str
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
        row: Dict[str, Any] = {
            "id": generation_storage_id(self.stable_id, generation_id),
            "semantic_id": self.stable_id,
            "from_id": generation_storage_id(self.from_id, generation_id) if self.from_generated else self.from_id,
            "to_id": generation_storage_id(self.to_id, generation_id) if self.to_generated else self.to_id,
            "from_label": self.from_label,
            "to_label": self.to_label,
            "type": self.type,
            "project_id": self.project_id,
            "module_id": self.module_id,
            "generation_id": generation_id,
            "framework": FRAMEWORK,
            "source_file": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "reason": self.reason,
            "properties": {
                key: graph_property_value(value)
                for key, value in redact_value("properties", self.properties).items()
            },
        }
        return row


@dataclass(frozen=True)
class ServletJspDependencyIndex:
    files: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    components: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    mappings: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    views: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    state_slots: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ServletJspAnalysisResult:
    project_id: str
    project_name: str
    root: str
    modules: Tuple[ServletJspModule, ...] = ()
    artifacts: Tuple[ServletJspArtifact, ...] = ()
    parser_capabilities: Tuple[ParserCapability, ...] = ()
    semantic_facts: Tuple[ServletJspFact, ...] = ()
    relationships: Tuple[ServletJspRelationship, ...] = ()
    dependency_index: ServletJspDependencyIndex = field(default_factory=ServletJspDependencyIndex)
    diagnostics: Tuple[Diagnostic, ...] = ()
    coverage_status: str = "empty"
    missing_anchor_count: int = 0
    ambiguity_count: int = 0
    truncation_count: int = 0
    parser_version: str = SERVLET_JSP_PARSER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        payload = redact_value("result", asdict(self))
        # Serialized facts are checkout-independent. The validated snapshot
        # envelope carries a project-root digest separately when needed.
        payload["root"] = "."
        for module in payload.get("modules", []):
            module["root"] = "."
        return payload

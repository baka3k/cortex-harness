from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Mapping, Tuple


STRUTS_PARSER_VERSION = "2.0.0-mvp1"
FRAMEWORK = "struts2"
_SENSITIVE_KEY_RE = re.compile(r"(?:password|passwd|secret|token|credential|api[_-]?key)", re.IGNORECASE)


def stable_id(kind: str, *parts: object) -> str:
    """Build a checkout-independent semantic identifier."""

    payload = "\x1f".join(str(part).strip() for part in parts)
    digest = hashlib.sha256(f"{kind}\x1e{payload}".encode("utf-8")).hexdigest()[:24]
    return f"struts::{kind.lower()}::{digest}"


def _graph_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[REDACTED]" if _SENSITIVE_KEY_RE.search(str(key)) else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_payload(item) for item in value]
    return value


def _safe_properties(properties: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: "[REDACTED]" if _SENSITIVE_KEY_RE.search(key) else _graph_value(_redact_payload(value))
        for key, value in sorted(properties.items())
    }


@dataclass(frozen=True)
class SourceSpan:
    file_path: str
    start_line: int = 0
    end_line: int = 0
    start_column: int = 0
    end_column: int = 0


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    file_path: str = ""


@dataclass(frozen=True)
class InterceptorRef:
    name: str
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InterceptorConfig:
    name: str
    class_name: str
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InterceptorStackConfig:
    name: str
    refs: Tuple[InterceptorRef, ...] = ()


@dataclass(frozen=True)
class ResultTypeConfig:
    name: str
    class_name: str
    default: bool = False
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultConfig:
    name: str
    type_name: str = ""
    location: str = ""
    params: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExceptionMappingConfig:
    exception: str
    result: str


@dataclass(frozen=True)
class ActionConfig:
    name: str
    class_name: str
    method: str = "execute"
    interceptor_refs: Tuple[InterceptorRef, ...] = ()
    results: Tuple[ResultConfig, ...] = ()
    exception_mappings: Tuple[ExceptionMappingConfig, ...] = ()
    params: Mapping[str, str] = field(default_factory=dict)
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))


@dataclass(frozen=True)
class PackageConfig:
    name: str
    namespace: str = ""
    extends: Tuple[str, ...] = ()
    default_interceptor_ref: str = ""
    default_result_type: str = ""
    interceptors: Tuple[InterceptorConfig, ...] = ()
    interceptor_stacks: Tuple[InterceptorStackConfig, ...] = ()
    result_types: Tuple[ResultTypeConfig, ...] = ()
    global_results: Tuple[ResultConfig, ...] = ()
    exception_mappings: Tuple[ExceptionMappingConfig, ...] = ()
    actions: Tuple[ActionConfig, ...] = ()
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))


@dataclass(frozen=True)
class StrutsXmlData:
    packages: Tuple[PackageConfig, ...] = ()
    constants: Mapping[str, str] = field(default_factory=dict)
    includes: Tuple[str, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class WebFilterConfig:
    name: str
    class_name: str
    url_patterns: Tuple[str, ...] = ()
    init_params: Mapping[str, str] = field(default_factory=dict)
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))


@dataclass(frozen=True)
class WebXmlData:
    filters: Tuple[WebFilterConfig, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class ValidationRule:
    target: str
    method: str
    validator_type: str
    field_name: str = ""
    message: str = ""
    message_key: str = ""
    params: Mapping[str, str] = field(default_factory=dict)
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))


@dataclass(frozen=True)
class ValidationData:
    rules: Tuple[ValidationRule, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class StrutsFact:
    kind: str
    stable_id: str
    name: str
    source: SourceSpan
    project_id: str
    project_name: str
    module_id: str
    confidence: float = 1.0
    extraction_method: str = "struts_xml"
    resolution_status: str = "resolved"
    properties: Mapping[str, Any] = field(default_factory=dict)

    def to_graph_node(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": self.stable_id,
            "semantic_id": self.stable_id,
            "symbol_id": self.stable_id,
            "name": self.name,
            "kind": self.kind,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "module_id": self.module_id,
            "language": "java",
            "framework": FRAMEWORK,
            "file_path": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "resolution_status": self.resolution_status,
            "parser_version": STRUTS_PARSER_VERSION,
        }
        for key, value in _safe_properties(self.properties).items():
            if key not in row:
                row[key] = value
        return row


@dataclass(frozen=True)
class StrutsRelationship:
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
    properties: Mapping[str, Any] = field(default_factory=dict)

    def to_graph_row(self) -> Dict[str, Any]:
        return {
            "id": self.stable_id,
            "semantic_id": self.stable_id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "from_label": self.from_label,
            "to_label": self.to_label,
            "type": self.type,
            "project_id": self.project_id,
            "module_id": self.module_id,
            "framework": FRAMEWORK,
            "source_file": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "reason": self.reason,
            "properties": _safe_properties(self.properties),
        }


@dataclass(frozen=True)
class StrutsAnalysisResult:
    project_id: str
    project_name: str
    root: str
    semantic_facts: Tuple[StrutsFact, ...] = ()
    relationships: Tuple[StrutsRelationship, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    coverage_status: str = "empty"
    parser_version: str = STRUTS_PARSER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        payload = _redact_payload(asdict(self))
        payload["root"] = "."
        return payload

"""Enhanced data classes and payload conversion for the C# primary analyzer.

The Roslyn worker emits a JSON evidence shape; this module converts that shape
into the analyzer's existing payload format and adds additive data classes for
the full member inventory (properties, fields, events, delegates, parameters).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


from .roslyn_adapter import (
    CSHARP_ROSLYN_CACHE_VERSION,
    CSHARP_ROSLYN_MODEL_VERSION,
    CSHARP_ROSLYN_PROTOCOL_VERSION,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CSHARP_NODE_LABELS = frozenset({
    "Namespace", "Type", "Function", "File",
    # New (Phase 03) member-inventory labels.
    "Property", "Field", "Event", "Delegate", "Parameter", "GenericParameter",
    # Project-metadata labels.
    "Project", "PackageReference",
    # Framework-agnostic semantic items (Phase 04).
    "EfEntityMapping", "GrpcService", "SignalRHub", "BackgroundService",
    "AuthPolicy", "LoggingTelemetry", "NuGetDependency",
    # Generic fallbacks already exposed by the analyzer.
    "Service", "Repository", "Model", "Route", "HttpEndpoint",
})


CSHARP_RELATIONSHIP_TYPES = frozenset({
    "CONTAINS", "CALLS", "INHERITS", "IMPLEMENTS",
    # Member containment
    "HAS_PROPERTY", "HAS_FIELD", "HAS_EVENT", "HAS_DELEGATE",
    "HAS_PARAMETER", "HAS_GENERIC_PARAMETER", "HAS_ATTRIBUTE",
    # Type relationships (resolved)
    "EXTENDS_CLASS", "IMPLEMENTS_INTERFACE",
    # Project dependencies
    "DEPENDS_ON_PACKAGE", "REFERENCES_PROJECT",
    # Framework-agnostic relationships
    "MAPS_ENTITY", "EXPOSES_GRPC", "EXPOSES_HUB", "RUNS_BACKGROUND",
    "ENFORCES_POLICY", "EMITS_LOG", "TRACES_ACTIVITY",
    # Semantic bridge to overlay nodes.
    "SEMANTIC_OF",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PropertyDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str = ""
    accessibility: str = ""
    has_getter: bool = False
    has_setter: bool = False
    has_init: bool = False
    is_static: bool = False
    is_virtual: bool = False
    is_override: bool = False
    is_abstract: bool = False
    is_auto_property: bool = False
    is_indexer: bool = False
    attributes: Tuple[str, ...] = ()
    xml_doc: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    code: str = ""
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass(frozen=True)
class FieldDef:
    symbol_id: str
    qualified_name: str
    name: str
    type_name: str = ""
    accessibility: str = ""
    is_static: bool = False
    is_const: bool = False
    is_readonly: bool = False
    is_volatile: bool = False
    constant_value: Optional[str] = None
    attributes: Tuple[str, ...] = ()
    xml_doc: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    code: str = ""
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass(frozen=True)
class EventDef:
    symbol_id: str
    qualified_name: str
    name: str
    delegate_type: str = ""
    accessibility: str = ""
    is_static: bool = False
    is_abstract: bool = False
    attributes: Tuple[str, ...] = ()
    xml_doc: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    code: str = ""
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass(frozen=True)
class ParameterDef:
    name: str
    type_name: str = ""
    is_optional: bool = False
    default_value: Optional[str] = None
    is_params: bool = False
    is_ref: bool = False
    is_out: bool = False
    is_in: bool = False


@dataclass(frozen=True)
class DelegateDef:
    symbol_id: str
    qualified_name: str
    name: str
    return_type: str = ""
    type_parameters: Tuple[str, ...] = ()
    parameters: Tuple[ParameterDef, ...] = ()
    accessibility: str = ""
    attributes: Tuple[str, ...] = ()
    xml_doc: str = ""
    file_path: str = ""
    start_line: int = 0
    end_line: int = 0
    code: str = ""
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass(frozen=True)
class SemanticFrameworkItem:
    """Framework-agnostic semantic fact extracted by `framework_items/*` extractors."""
    kind: str
    name: str
    qualified_name: str
    file_path: str
    start_line: int = 0
    end_line: int = 0
    code: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    extraction_method: str = "roslyn"
    source_symbol_id: str = ""


@dataclass(frozen=True)
class RoslynConversionStats:
    types: int = 0
    members: int = 0
    fields: int = 0
    events: int = 0
    delegates: int = 0
    properties: int = 0
    calls: int = 0
    resolved_calls: int = 0


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _strip_xml_doc(xml: str) -> str:
    """Lightly reduce `///` doc strings for embedding into analyzer fields."""
    if not xml:
        return ""
    cleaned = re.sub(r"</?[^>]+>", " ", xml)
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_note(code: str, comment: str, summary: str) -> str:
    parts: List[str] = []
    if summary:
        parts.append(f"Summary:\n{summary}")
    if comment:
        parts.append(f"Comment:\n{comment}")
    if code:
        parts.append(f"Code:\n{code}")
    return "\n\n".join(parts)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _tuple_of(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        return (value,) if value else ()
    return ()


def _parameter_from_evidence(payload: Dict[str, Any]) -> ParameterDef:
    return ParameterDef(
        name=_to_str(payload.get("name")),
        type_name=_to_str(payload.get("type_name")),
        is_optional=bool(payload.get("is_optional", False)),
        default_value=payload.get("default_value") if payload.get("default_value") else None,
        is_params=bool(payload.get("is_params", False)),
        is_ref=bool(payload.get("is_ref", False)),
        is_out=bool(payload.get("is_out", False)),
        is_in=bool(payload.get("is_in", False)),
    )


def _symbol_id_for_member(
    file_rel: str, qualified_name: str, kind: str, arity: int = 0
) -> str:
    if not qualified_name:
        return f"{kind.lower()}@anon@{file_rel}"
    return f"{qualified_name}/{arity}@{file_rel}"


def roslyn_evidence_to_payload(
    evidence: Dict[str, Any],
    *,
    fallback_kind: str = "type",
) -> Dict[str, Any]:
    """Convert one Roslyn `DocumentEvidence` to the analyzer's payload dict.

    The result contains:
    - types, properties, fields, events, delegates, parameters (in addition to the
      legacy namespaces / types / functions / calls / relations sets),
    - parse_meta with `parser_language="csharp_roslyn_*"` and provenance,
    - file_def with the namespace string and usings folded into note text.
    """
    file_path = evidence.get("file_path", "")
    namespace_name = evidence.get("namespace", "") or ""
    members = list(evidence.get("members", []))
    types = list(evidence.get("types", []))
    fields = list(evidence.get("fields", []))
    events = list(evidence.get("events", []))
    delegates = list(evidence.get("delegates", []))
    parameters = list(evidence.get("parameters", []))
    usings = list(evidence.get("usings", []))
    attributes = list(evidence.get("attributes", []))
    calls = list(evidence.get("calls", []))

    # Convert methods/constructors/properties/indexers into the legacy `FunctionDef`
    # shape so downstream graph writers can keep working without changes.
    legacy_functions: List[Dict[str, Any]] = []
    for member in members:
        kind = _to_str(member.get("kind", "method"))
        qualified_name = _to_str(member.get("qualified_name")) or _to_str(member.get("name"))
        parameters_in_member = [_parameter_from_evidence(p) for p in member.get("parameters", [])]
        arity = len(parameters_in_member)
        symbol_id = _to_str(member.get("canonical_symbol_id")) or _symbol_id_for_member(
            file_path, qualified_name, kind, arity
        )
        legacy_functions.append({
            "symbol_id": symbol_id,
            "qualified_name": qualified_name,
            "name": _to_str(member.get("name")),
            "kind": _type_kind_to_function_kind(kind),
            "scope_name": _scope_of(qualified_name),
            "file_path": file_path,
            "start_line": _to_int(member.get("start_line"), 0),
            "end_line": _to_int(member.get("end_line"), 0),
            "arity": arity,
            "code": "",
            "comment": "",
            "summary": _strip_xml_doc(_to_str(member.get("xml_doc"))),
            "note": _build_note("", "", _strip_xml_doc(_to_str(member.get("xml_doc")))),
            "class_name": _scope_of(qualified_name),
            "package_name": namespace_name,
            "member_kind": kind,
            "is_async": bool(member.get("is_async", False)),
            "is_static": bool(member.get("is_static", False)),
            "is_virtual": bool(member.get("is_virtual", False)),
            "is_override": bool(member.get("is_override", False)),
            "is_abstract": bool(member.get("is_abstract", False)),
            "is_extension_method": bool(member.get("is_extension_method", False)),
            "accessibility": _to_str(member.get("accessibility")),
            "attributes": list(member.get("attributes", []) or []),
            "return_type": _to_str(member.get("return_type")),
            "type_parameters": list(member.get("type_parameters", []) or []),
            "xml_doc": _to_str(member.get("xml_doc")),
            "parameters": [asdict(p) for p in parameters_in_member],
        })

    legacy_types: List[Dict[str, Any]] = []
    for type_evidence in types:
        qualified_name = _to_str(type_evidence.get("qualified_name")) or _to_str(
            type_evidence.get("name"))
        kind = _to_str(type_evidence.get("kind", fallback_kind))
        legacy_types.append({
            "symbol_id": _to_str(type_evidence.get("canonical_symbol_id")) or qualified_name,
            "qualified_name": qualified_name,
            "name": _to_str(type_evidence.get("name")),
            "kind": kind,
            "namespace_name": namespace_name,
            "file_path": file_path,
            "start_line": _to_int(type_evidence.get("start_line"), 0),
            "end_line": _to_int(type_evidence.get("end_line"), 0),
            "code": "",
            "comment": "",
            "summary": _strip_xml_doc(_to_str(type_evidence.get("xml_doc"))),
            "note": _build_note("", "", _strip_xml_doc(_to_str(type_evidence.get("xml_doc")))),
            "base_types": list(type_evidence.get("base_types", []) or []),
            "implemented_interfaces": list(type_evidence.get("implemented_interfaces", []) or []),
            "type_parameters": list(type_evidence.get("type_parameters", []) or []),
            "type_constraints": {k: list(v) for k, v in
                                  (type_evidence.get("type_constraints") or {}).items()},
            "is_abstract": bool(type_evidence.get("is_abstract", False)),
            "is_sealed": bool(type_evidence.get("is_sealed", False)),
            "is_static": bool(type_evidence.get("is_static", False)),
            "is_partial": bool(type_evidence.get("is_partial", False)),
            "is_record": bool(type_evidence.get("is_record", False)),
            "accessibility": _to_str(type_evidence.get("accessibility")),
            "attributes": list(type_evidence.get("attributes", []) or []),
            "xml_doc": _to_str(type_evidence.get("xml_doc")),
        })

    legacy_fields: List[Dict[str, Any]] = []
    for field_evidence in fields:
        qualified_name = _to_str(field_evidence.get("qualified_name")) or _to_str(
            field_evidence.get("name"))
        legacy_fields.append({
            "symbol_id": _to_str(field_evidence.get("canonical_symbol_id"))
                or _symbol_id_for_member(file_path, qualified_name, "field"),
            "qualified_name": qualified_name,
            "name": _to_str(field_evidence.get("name")),
            "kind": "field",
            "namespace_name": namespace_name,
            "class_name": _scope_of(qualified_name),
            "file_path": file_path,
            "start_line": _to_int(field_evidence.get("start_line"), 0),
            "end_line": _to_int(field_evidence.get("end_line"), 0),
            "type_name": _to_str(field_evidence.get("type_name")),
            "accessibility": _to_str(field_evidence.get("accessibility")),
            "is_static": bool(field_evidence.get("is_static", False)),
            "is_const": bool(field_evidence.get("is_const", False)),
            "is_readonly": bool(field_evidence.get("is_readonly", False)),
            "is_volatile": bool(field_evidence.get("is_volatile", False)),
            "constant_value": field_evidence.get("constant_value") or None,
            "attributes": list(field_evidence.get("attributes", []) or []),
            "xml_doc": _to_str(field_evidence.get("xml_doc")),
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        })

    legacy_events: List[Dict[str, Any]] = []
    for event_evidence in events:
        qualified_name = _to_str(event_evidence.get("qualified_name")) or _to_str(
            event_evidence.get("name"))
        legacy_events.append({
            "symbol_id": _to_str(event_evidence.get("canonical_symbol_id"))
                or _symbol_id_for_member(file_path, qualified_name, "event"),
            "qualified_name": qualified_name,
            "name": _to_str(event_evidence.get("name")),
            "kind": "event",
            "namespace_name": namespace_name,
            "class_name": _scope_of(qualified_name),
            "file_path": file_path,
            "start_line": _to_int(event_evidence.get("start_line"), 0),
            "end_line": _to_int(event_evidence.get("end_line"), 0),
            "delegate_type": _to_str(event_evidence.get("delegate_type")),
            "accessibility": _to_str(event_evidence.get("accessibility")),
            "is_static": bool(event_evidence.get("is_static", False)),
            "is_abstract": bool(event_evidence.get("is_abstract", False)),
            "attributes": list(event_evidence.get("attributes", []) or []),
            "xml_doc": _to_str(event_evidence.get("xml_doc")),
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        })

    legacy_delegates: List[Dict[str, Any]] = []
    for delegate_evidence in delegates:
        qualified_name = _to_str(delegate_evidence.get("qualified_name")) or _to_str(
            delegate_evidence.get("name"))
        legacy_delegates.append({
            "symbol_id": _to_str(delegate_evidence.get("canonical_symbol_id"))
                or _symbol_id_for_member(file_path, qualified_name, "delegate"),
            "qualified_name": qualified_name,
            "name": _to_str(delegate_evidence.get("name")),
            "kind": "delegate",
            "namespace_name": namespace_name,
            "class_name": _scope_of(qualified_name),
            "file_path": file_path,
            "start_line": _to_int(delegate_evidence.get("start_line"), 0),
            "end_line": _to_int(delegate_evidence.get("end_line"), 0),
            "return_type": _to_str(delegate_evidence.get("return_type")),
            "type_parameters": list(delegate_evidence.get("type_parameters", []) or []),
            "parameters": [asdict(_parameter_from_evidence(p)) for p in
                           delegate_evidence.get("parameters", [])],
            "accessibility": _to_str(delegate_evidence.get("accessibility")),
            "attributes": list(delegate_evidence.get("attributes", []) or []),
            "xml_doc": _to_str(delegate_evidence.get("xml_doc")),
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        })

    legacy_parameters: List[Dict[str, Any]] = []
    for param_evidence in parameters:
        param = _parameter_from_evidence(param_evidence)
        legacy_parameters.append({**asdict(param), "file_path": file_path})

    legacy_relations: List[Dict[str, Any]] = []

    # Type-level relationship edges (CONTAINS, INHERITS, IMPLEMENTS).
    for type_evidence in types:
        type_symbol_id = _to_str(type_evidence.get("canonical_symbol_id")) or _to_str(
            type_evidence.get("qualified_name")) or _to_str(type_evidence.get("name"))
        for base_type in type_evidence.get("base_types", []) or []:
            if not base_type:
                continue
            legacy_relations.append({
                "source_id": type_symbol_id,
                "source_label": "Type",
                "target_id": base_type,
                "target_label": "Type",
                "rel_type": "EXTENDS_CLASS",
                "properties": {"resolved": bool(type_evidence.get("base_types"))},
            })
        for iface in type_evidence.get("implemented_interfaces", []) or []:
            if not iface:
                continue
            legacy_relations.append({
                "source_id": type_symbol_id,
                "source_label": "Type",
                "target_id": iface,
                "target_label": "Type",
                "rel_type": "IMPLEMENTS_INTERFACE",
                "properties": {"resolved": bool(type_evidence.get("implemented_interfaces"))},
            })

    legacy_calls: List[Dict[str, Any]] = []
    for call in calls:
        legacy_calls.append({
            "caller_id": _to_str(call.get("caller_id")),
            "caller_scope": _to_str(call.get("caller_id")).rsplit("::", 1)[0]
                if call.get("caller_id") else "",
            "callee_name": _to_str(call.get("expression")).split("(")[0].split(".")[-1],
            "callee_id": _to_str(call.get("resolved_callee_id")) or None,
            "callee_arity": call.get("callee_arity"),
            "call_line": _to_int(call.get("start_line"), 0),
            "resolved": bool(call.get("resolved", False)),
            "is_async": bool(call.get("is_async", False)),
            "is_virtual_dispatch": bool(call.get("is_virtual_dispatch", False)),
            "arguments": list(call.get("arguments", []) or []),
        })

    namespace_payload = []
    if namespace_name:
        namespace_payload.append({
            "symbol_id": f"namespace::{namespace_name}",
            "qualified_name": namespace_name,
            "name": namespace_name,
            "file_path": file_path,
            "start_line": 1,
            "end_line": 1,
            "code": "",
            "comment": "",
            "summary": "",
            "note": "",
        })

    file_def = {
        "file_path": file_path,
        "start_line": 1,
        "end_line": 1,
        "code": "",
        "comment": "",
        "summary": "",
        "note": "",
        "imports": [u.get("name", "") for u in usings if u.get("name")],
        "attributes": [a.get("name", "") for a in attributes if a.get("name")],
    }

    return {
        "namespaces": namespace_payload,
        "types": legacy_types,
        "functions": legacy_functions,
        "calls": legacy_calls,
        "fields": legacy_fields,
        "events": legacy_events,
        "delegates": legacy_delegates,
        "parameters": legacy_parameters,
        "properties": [
            {
                "name": m.get("name"),
                "qualified_name": m.get("qualified_name"),
                "type_name": m.get("return_type"),
                "accessibility": m.get("accessibility"),
                "is_static": m.get("is_static"),
                "is_virtual": m.get("is_virtual"),
                "is_override": m.get("is_override"),
                "is_abstract": m.get("is_abstract"),
                "kind": m.get("kind"),
                "file_path": file_path,
                "start_line": m.get("start_line"),
                "end_line": m.get("end_line"),
            }
            for m in members if m.get("kind") in {"property", "indexer"}
        ],
        "usings": usings,
        "attributes": attributes,
        "relations": legacy_relations,
        "file_def": file_def,
        "parse_meta": {
            "parser_language": "csharp_roslyn",
            "parser_available": True,
            "has_error": False,
            "error_nodes": 0,
            "semantic_enabled": True,
            "coverage_status": "full",
            "roslyn_workspace_kind": "safe_compilation",
        },
        "parse_cache_version": CSHARP_ROSLYN_CACHE_VERSION,
        "model_version": CSHARP_ROSLYN_MODEL_VERSION,
    }


def project_metadata_to_payload(project: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not project:
        return {"packages": [], "project_references": []}
    return {
        "project_path": _to_str(project.get("project_path")),
        "target_framework": _to_str(project.get("target_framework")),
        "sdk": _to_str(project.get("sdk")),
        "output_type": _to_str(project.get("output_type")),
        "packages": [
            {
                "name": p.get("name", ""),
                "version": p.get("version", ""),
                "is_development": bool(p.get("is_development", False)),
            }
            for p in (project.get("nuget_packages") or [])
        ],
        "project_references": list(project.get("project_references") or []),
    }


def _scope_of(qualified_name: str) -> str:
    if not qualified_name:
        return ""
    if "::" in qualified_name:
        return qualified_name.rsplit("::", 1)[0]
    if "." in qualified_name:
        return qualified_name.rsplit(".", 1)[0]
    return ""


def _type_kind_to_function_kind(kind: str) -> str:
    if kind == "constructor":
        return "constructor"
    if kind == "property":
        return "property"
    if kind == "indexer":
        return "indexer"
    return "method"


def stats_from_conversion(payload: Dict[str, Any]) -> RoslynConversionStats:
    return RoslynConversionStats(
        types=len(payload.get("types", [])),
        members=len(payload.get("functions", [])),
        fields=len(payload.get("fields", [])),
        events=len(payload.get("events", [])),
        delegates=len(payload.get("delegates", [])),
        properties=len(payload.get("properties", [])),
        calls=len(payload.get("calls", [])),
        resolved_calls=sum(1 for c in payload.get("calls", []) if c.get("resolved")),
    )

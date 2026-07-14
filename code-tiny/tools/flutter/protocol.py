"""Versioned fact protocol validation for Dart and Flutter analysis."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .models import (
    AnalysisFacts,
    DiagnosticRecord,
    EdgeRecord,
    HeaderRecord,
    NodeRecord,
    ProtocolRecord,
    SourceEvidence,
    SummaryRecord,
)


PROTOCOL_MAJOR = 1
PROTOCOL_VERSION = "1.0"
RECORD_TYPES = frozenset({"header", "node", "edge", "diagnostic", "summary"})


class ProtocolError(ValueError):
    """Raised when a fact stream violates the supported protocol."""


def _required(record: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in record]
    if missing:
        raise ProtocolError(
            f"{record.get('type', 'record')} is missing required field(s): {', '.join(missing)}"
        )


def _evidence(record: Mapping[str, Any], *, required: bool = True) -> SourceEvidence | None:
    value = record.get("evidence")
    if value is None:
        if required:
            raise ProtocolError(f"{record.get('type', 'record')} is missing required field: evidence")
        return None
    if not isinstance(value, Mapping):
        raise ProtocolError("evidence must be an object")
    _required(value, "file")
    evidence = SourceEvidence.from_mapping(value)
    if evidence.offset < 0 or evidence.length < 0:
        raise ProtocolError("evidence offset and length must be non-negative")
    if evidence.start_line < 1 or evidence.start_column < 1:
        raise ProtocolError("evidence line and column values are 1-based")
    return evidence


def parse_record(record: Mapping[str, Any]) -> ProtocolRecord:
    record_type = record.get("type")
    if record_type not in RECORD_TYPES:
        raise ProtocolError(f"unsupported record type: {record_type!r}")
    if record_type == "header":
        _required(record, "schema_version", "analyzer_version", "sdk_version", "root", "project_id")
        version = str(record["schema_version"])
        try:
            major = int(version.split(".", 1)[0])
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"invalid schema_version: {version!r}") from exc
        if major != PROTOCOL_MAJOR:
            raise ProtocolError(
                f"unsupported protocol major {major}; adapter supports {PROTOCOL_MAJOR}.x"
            )
        mode = str(record.get("mode", "dart"))
        if mode not in {"dart", "flutter", "all"}:
            raise ProtocolError(f"unsupported analysis mode: {mode!r}")
        return HeaderRecord(
            schema_version=version,
            analyzer_version=str(record["analyzer_version"]),
            sdk_version=str(record["sdk_version"]),
            root=str(record["root"]),
            project_id=str(record["project_id"]),
            mode=mode,
        )
    if record_type == "node":
        _required(record, "identity", "kind", "properties")
        properties = record["properties"]
        if not isinstance(properties, Mapping):
            raise ProtocolError("node properties must be an object")
        return NodeRecord(
            identity=str(record["identity"]),
            kind=str(record["kind"]),
            properties=dict(properties),
            evidence=_evidence(record),  # type: ignore[arg-type]
        )
    if record_type == "edge":
        _required(record, "source", "target", "relationship")
        properties = record.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ProtocolError("edge properties must be an object")
        confidence = float(record.get("confidence", 1.0))
        if not 0.0 <= confidence <= 1.0:
            raise ProtocolError("edge confidence must be between 0 and 1")
        return EdgeRecord(
            source=str(record["source"]),
            target=str(record["target"]),
            relationship=str(record["relationship"]),
            properties=dict(properties),
            confidence=confidence,
            evidence=_evidence(record),  # type: ignore[arg-type]
        )
    if record_type == "diagnostic":
        _required(record, "severity", "code", "message", "recoverable")
        severity = str(record["severity"])
        if severity not in {"info", "warning", "error"}:
            raise ProtocolError(f"invalid diagnostic severity: {severity!r}")
        return DiagnosticRecord(
            severity=severity,
            code=str(record["code"]),
            message=str(record["message"]),
            recoverable=bool(record["recoverable"]),
            evidence=_evidence(record, required=False),
        )
    _required(record, "processed_files", "skipped_files", "error_count", "elapsed_ms")
    values = [int(record[key]) for key in ("processed_files", "skipped_files", "error_count", "elapsed_ms")]
    if any(value < 0 for value in values):
        raise ProtocolError("summary counters must be non-negative")
    return SummaryRecord(*values)


def parse_jsonl(lines: Iterable[str]) -> AnalysisFacts:
    parsed: List[ProtocolRecord] = []
    for line_number, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON on fact stream line {line_number}: {exc.msg}") from exc
        if not isinstance(value, Mapping):
            raise ProtocolError(f"fact stream line {line_number} must be a JSON object")
        try:
            parsed.append(parse_record(value))
        except ProtocolError as exc:
            raise ProtocolError(f"fact stream line {line_number}: {exc}") from exc
    if not parsed:
        raise ProtocolError("parser produced an empty protocol stream")
    if not isinstance(parsed[0], HeaderRecord):
        raise ProtocolError("the first protocol record must be a header")
    if not isinstance(parsed[-1], SummaryRecord):
        raise ProtocolError("the final protocol record must be a summary")
    if sum(isinstance(item, HeaderRecord) for item in parsed) != 1:
        raise ProtocolError("the protocol stream must contain exactly one header")
    if sum(isinstance(item, SummaryRecord) for item in parsed) != 1:
        raise ProtocolError("the protocol stream must contain exactly one summary")

    nodes = tuple(item for item in parsed if isinstance(item, NodeRecord))
    identities = [node.identity for node in nodes]
    if len(identities) != len(set(identities)):
        raise ProtocolError("the protocol stream contains duplicate node identities")
    identity_set = set(identities)
    edges = tuple(item for item in parsed if isinstance(item, EdgeRecord))
    dangling = [
        f"{edge.source}->{edge.target}"
        for edge in edges
        if edge.source not in identity_set or edge.target not in identity_set
    ]
    if dangling:
        raise ProtocolError(f"edge endpoints are missing from the staged fact set: {dangling[0]}")
    return AnalysisFacts(
        header=parsed[0],
        nodes=nodes,
        edges=edges,
        diagnostics=tuple(item for item in parsed if isinstance(item, DiagnosticRecord)),
        summary=parsed[-1],
    )


def record_to_dict(record: ProtocolRecord) -> Dict[str, Any]:
    value = asdict(record)
    value["type"] = value.pop("record_type")
    if value.get("evidence") is None:
        value.pop("evidence", None)
    return value


def serialize_records(records: Sequence[ProtocolRecord]) -> str:
    return "".join(
        json.dumps(record_to_dict(record), sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    )

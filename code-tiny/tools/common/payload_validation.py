"""Pre-mutation analyzer payload validation and bounded quarantine records."""

from __future__ import annotations

import itertools
import json
import posixpath
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from tools.common.reliability import fingerprint_rows


PAYLOAD_SCHEMA_VERSION = "1.0"
MAX_EVIDENCE_CHARS = 512


class QuarantineReason(str, Enum):
    MALFORMED_DECLARATOR = "malformed_declarator_capture"
    PREPROCESSOR_LEAKAGE = "preprocessor_leakage"
    COMMENT_LEAKAGE = "comment_leakage"
    DAMAGED_SCOPE = "damaged_scope"
    CONFLICTING_DUPLICATE = "conflicting_duplicate"
    INVALID_SPAN = "invalid_span"
    MISSING_OWNER = "missing_owner"
    QUARANTINED_FILE_QUALITY = "quarantined_file_quality"
    UNRESOLVED_REFERENCE = "unresolved_reference"
    INVALID_PATH = "invalid_path"
    INVALID_RECORD = "invalid_record"


@dataclass(frozen=True)
class QuarantineRecord:
    record_type: str
    identity: str
    reason: QuarantineReason
    source_path: str
    evidence: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)
    dependent_effects: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "identity": self.identity[:MAX_EVIDENCE_CHARS],
            "reason": self.reason.value,
            "source_path": self.source_path[:MAX_EVIDENCE_CHARS],
            "evidence": self.evidence[:MAX_EVIDENCE_CHARS],
            "provenance": {
                str(key)[:128]: str(value)[:MAX_EVIDENCE_CHARS]
                for key, value in sorted(self.provenance.items(), key=lambda item: str(item[0]))
            },
            "dependent_effects": max(0, int(self.dependent_effects)),
        }


@dataclass(frozen=True)
class PayloadAccounting:
    discovered: int
    accepted: int
    quarantined: int
    rejected: int = 0

    def __post_init__(self) -> None:
        if min(self.discovered, self.accepted, self.quarantined, self.rejected) < 0:
            raise ValueError("payload accounting counts must be non-negative")
        if self.discovered != self.accepted + self.quarantined + self.rejected:
            raise ValueError("payload accounting is not balanced")

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "accepted": self.accepted,
            "quarantined": self.quarantined,
            "rejected": self.rejected,
        }


@dataclass(frozen=True)
class ValidatedPayloadEnvelope:
    run_id: str
    project_id: str
    analyzer: str
    source_fingerprint: str
    policy_fingerprint: str
    nodes: tuple[Mapping[str, Any], ...]
    relations: tuple[Mapping[str, Any], ...]
    vectors: tuple[Mapping[str, Any], ...]
    quarantine: tuple[QuarantineRecord, ...]
    accounting: PayloadAccounting
    schema_version: str = PAYLOAD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.run_id, self.project_id, self.analyzer)):
            raise ValueError("validated envelopes require run, project, and analyzer identifiers")

    @property
    def fingerprint(self) -> str:
        return fingerprint_rows(
            [*self.nodes, *self.relations, *self.vectors]
            + [record.to_dict() for record in self.quarantine]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "analyzer": self.analyzer,
            "source_fingerprint": self.source_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "nodes": [dict(item) for item in self.nodes],
            "relations": [dict(item) for item in self.relations],
            "vectors": [dict(item) for item in self.vectors],
            "quarantine": [item.to_dict() for item in self.quarantine],
            "accounting": self.accounting.to_dict(),
            "fingerprint": self.fingerprint,
        }


class IdentityRegistry:
    """Label-qualified accepted identities; unlabeled lookups are impossible."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}

    @staticmethod
    def key(label: str, project_id: str, source_path: str, identity: str) -> tuple[str, str, str, str]:
        return (label, project_id, source_path, identity)

    def register(
        self,
        *,
        label: str,
        project_id: str,
        source_path: str,
        identity: str,
        record: Mapping[str, Any],
    ) -> str:
        key = self.key(label, project_id, source_path, identity)
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = record
            return "accepted"
        return "duplicate" if _canonical(existing) == _canonical(record) else "conflict"

    def contains(
        self, *, label: str, project_id: str, identity: str, source_path: str | None = None
    ) -> bool:
        if source_path is not None:
            return self.key(label, project_id, source_path, identity) in self._records
        return any(
            key_label == label and key_project == project_id and key_identity == identity
            for key_label, key_project, _, key_identity in self._records
        )


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def identity_merge_fingerprint(label: str, record: Mapping[str, Any]) -> str:
    """Return the semantic identity shape used to reconcile declarations."""

    shared_fields = ("name", "qualified_name", "kind")
    label_fields: Mapping[str, tuple[str, ...]] = {
        "Function": ("scope_name", "arity"),
        "FunctionType": ("type_signature",),
        "Field": ("scope_name", "type_signature"),
        "Alias": ("target_name",),
        "Resource": ("resource_symbol",),
        "ResourceElement": ("resource_symbol", "dialog_symbol", "control_type"),
    }
    semantic = {
        field: record.get(field)
        for field in (*shared_fields, *label_fields.get(label, ()))
        if field in record
    }
    return _canonical(semantic)


def _has_forbidden_control(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)


def _identity_reason(value: Any, *, name: bool = False) -> QuarantineReason | None:
    if not isinstance(value, str) or not value.strip():
        return QuarantineReason.MALFORMED_DECLARATOR
    if _has_forbidden_control(value):
        return QuarantineReason.MALFORMED_DECLARATOR
    stripped = value.strip()
    if name and (stripped.startswith("#") or "#define" in stripped or "#if" in stripped):
        return QuarantineReason.PREPROCESSOR_LEAKAGE
    if name and any(marker in stripped for marker in ("/*", "*/", "//")):
        return QuarantineReason.COMMENT_LEAKAGE
    return None


def normalize_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or _has_forbidden_control(value):
        raise ValueError("path is missing or contains forbidden controls")
    candidate = value.replace("\\", "/")
    if candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
        raise ValueError("source paths must be repository-relative")
    normalized = posixpath.normpath(candidate)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise ValueError("source path escapes the repository root")
    return normalized


def _span_is_valid(record: Mapping[str, Any]) -> bool:
    for start_key, end_key in (("start_byte", "end_byte"), ("start_line", "end_line")):
        if start_key not in record and end_key not in record:
            continue
        try:
            start = int(record.get(start_key) or 0)
            end = int(record.get(end_key) or 0)
        except (TypeError, ValueError):
            return False
        if start < 0 or end < start:
            return False
    return True


def _record_identity(record: Mapping[str, Any]) -> str:
    return str(record.get("id") or record.get("symbol_id") or "")


def _record_path(record: Mapping[str, Any], default: str = "") -> str:
    return str(record.get("file_path") or record.get("source_path") or default)


def _quarantine(
    record_type: str,
    record: Mapping[str, Any],
    reason: QuarantineReason,
    *,
    default_path: str = "",
    dependent_effects: int = 0,
) -> QuarantineRecord:
    return QuarantineRecord(
        record_type=record_type,
        identity=_record_identity(record),
        reason=reason,
        source_path=_record_path(record, default_path),
        evidence=str(record.get("name") or record.get("qualified_name") or _record_identity(record)),
        provenance=dict(record.get("quality_provenance") or {}),
        dependent_effects=dependent_effects,
    )


_CPLUS_COLLECTION_LABELS: Mapping[str, str] = {
    "namespaces": "Namespace",
    "types": "Type",
    "function_types": "FunctionType",
    "functions": "Function",
    "fields": "Field",
    "aliases": "Alias",
    "templates": "Template",
    "resources": "Resource",
    "resource_elements": "ResourceElement",
    "proc_nodes": "ProcStatement",
}

_CPLUS_REQUIRED_FIELDS: Mapping[str, tuple[str, ...]] = {
    "namespaces": (
        "symbol_id", "name", "qualified_name", "file_path", "start_line",
        "end_line", "code", "comment", "summary", "note",
    ),
    "types": (
        "symbol_id", "name", "qualified_name", "kind", "file_path", "start_line",
        "end_line", "code", "comment", "summary", "note",
    ),
    "function_types": (
        "symbol_id", "type_signature", "file_path", "start_line", "end_line", "code",
    ),
    "functions": (
        "symbol_id", "name", "qualified_name", "kind", "scope_name",
        "file_path", "start_line", "end_line", "arity", "code", "comment",
        "summary", "note",
    ),
    "fields": (
        "symbol_id", "name", "qualified_name", "scope_name", "type_signature",
        "file_path", "start_line", "end_line", "code",
    ),
    "aliases": (
        "symbol_id", "name", "qualified_name", "kind", "target_name", "file_path",
        "start_line", "end_line", "code",
    ),
    "templates": ("symbol_id", "name", "file_path", "start_line", "end_line", "code"),
    "resources": (
        "symbol_id", "name", "qualified_name", "kind", "resource_symbol", "file_path",
        "start_line", "end_line", "code", "comment", "summary", "note",
    ),
    "resource_elements": (
        "symbol_id", "name", "qualified_name", "kind", "file_path", "start_line",
        "end_line", "code", "comment", "summary", "note",
    ),
    "proc_nodes": (
        "symbol_id", "name", "qualified_name", "kind", "file_path", "start_line",
        "end_line", "code", "comment", "summary", "note",
    ),
}


def _invalid_required_fields(collection: str, record: Mapping[str, Any]) -> tuple[str, ...]:
    invalid: list[str] = []
    integer_fields = {"start_byte", "end_byte", "start_line", "end_line", "arity"}
    optional_text_fields = {"scope_name"}
    for field_name in _CPLUS_REQUIRED_FIELDS.get(collection, ()):
        if field_name not in record:
            invalid.append(field_name)
            continue
        value = record[field_name]
        if field_name in integer_fields:
            if isinstance(value, bool) or not isinstance(value, int):
                invalid.append(field_name)
        elif field_name in optional_text_fields:
            if value is not None and not isinstance(value, str):
                invalid.append(field_name)
        elif not isinstance(value, str):
            invalid.append(field_name)
    return tuple(invalid)


def validate_cplus_payload(
    payload: Mapping[str, Any], *, project_id: str,
    known_identities: set[tuple[str, str]] | None = None,
    blocked_identities: set[tuple[str, str]] | None = None,
) -> tuple[dict[str, Any], tuple[QuarantineRecord, ...]]:
    """Filter one C/C++/Pro*C payload before any graph or vector side effect."""

    validated: dict[str, Any] = dict(payload)
    rejected_count = 0
    validated["_validation_rejected_count"] = rejected_count
    quarantine: list[QuarantineRecord] = []
    file_def = dict(payload.get("file_def") or {})
    source_path = _record_path(file_def)
    try:
        source_path = normalize_relative_path(source_path)
        file_def["file_path"] = source_path
    except ValueError:
        quarantine.append(_quarantine("File", file_def, QuarantineReason.INVALID_PATH))
        validated["file_def"] = file_def
        for collection in _CPLUS_COLLECTION_LABELS:
            for record in payload.get(collection, []) or []:
                safe_record = (
                    record
                    if isinstance(record, Mapping)
                    else {"name": type(record).__name__, "file_path": source_path}
                )
                quarantine.append(
                    _quarantine(
                        _CPLUS_COLLECTION_LABELS[collection],
                        safe_record,
                        (
                            QuarantineReason.MISSING_OWNER
                            if isinstance(record, Mapping)
                            else QuarantineReason.INVALID_RECORD
                        ),
                        default_path=source_path,
                    )
                )
            validated[collection] = []
        validated["relations"] = []
        validated["calls"] = []
        validated["_quarantine_entire_payload"] = True
        return validated, tuple(quarantine)

    validated["file_def"] = file_def
    file_def.setdefault("start_line", 1)
    file_def.setdefault("end_line", file_def["start_line"])
    for field_name in ("code", "comment", "summary", "note"):
        file_def.setdefault(field_name, "")
    evidence_policy = payload.get("evidence_policy") or {}
    file_quarantined = (
        str((file_def.get("parse_quality") or {}).get("tier") or "") == "quarantined"
        or evidence_policy.get("strong_relations_allowed") is False
    )

    accepted_ids: set[tuple[str, str]] = {
        ("Project", project_id),
        ("File", source_path),
    }
    invalid_keys: set[tuple[str, str]] = set()

    for collection, default_label in _CPLUS_COLLECTION_LABELS.items():
        for item in payload.get(collection, []) or []:
            if not isinstance(item, Mapping):
                quarantine.append(
                    _quarantine(
                        default_label,
                        {"name": type(item).__name__, "file_path": source_path},
                        QuarantineReason.INVALID_RECORD,
                        default_path=source_path,
                    )
                )
        rows = [dict(item) for item in (payload.get(collection, []) or []) if isinstance(item, Mapping)]
        if file_quarantined:
            for row in rows:
                label = str(row.get("label") or default_label)
                invalid_keys.add((label, _record_identity(row)))
                quarantine.append(
                    _quarantine(
                        label,
                        row,
                        QuarantineReason.QUARANTINED_FILE_QUALITY,
                        default_path=source_path,
                    )
                )
            validated[collection] = []
            continue

        by_identity: dict[str, list[dict[str, Any]]] = {}
        prevalidated: list[dict[str, Any]] = []
        for row in rows:
            label = str(row.get("label") or default_label)
            identity = _record_identity(row)
            reason = _identity_reason(identity)
            if reason is None and "name" in row:
                reason = _identity_reason(row.get("name"), name=True)
            if reason is None and _invalid_required_fields(collection, row):
                reason = QuarantineReason.INVALID_RECORD
            if reason is None and not _span_is_valid(row):
                reason = QuarantineReason.INVALID_SPAN
            try:
                owner = normalize_relative_path(_record_path(row, source_path))
            except ValueError:
                owner = ""
                reason = reason or QuarantineReason.MISSING_OWNER
            if owner and owner != source_path:
                reason = reason or QuarantineReason.MISSING_OWNER
            if reason is not None:
                invalid_keys.add((label, identity))
                quarantine.append(_quarantine(label, row, reason, default_path=source_path))
                continue
            row["file_path"] = owner
            by_identity.setdefault(identity, []).append(row)
            prevalidated.append(row)

        conflicts = {
            identity
            for identity, candidates in by_identity.items()
            if len(
                {
                    identity_merge_fingerprint(
                        str(candidate.get("label") or default_label), candidate
                    )
                    for candidate in candidates
                }
            ) > 1
        }
        accepted_rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in prevalidated:
            label = str(row.get("label") or default_label)
            identity = _record_identity(row)
            if blocked_identities is not None and (label, identity) in blocked_identities:
                invalid_keys.add((label, identity))
                quarantine.append(
                    _quarantine(
                        label,
                        row,
                        QuarantineReason.CONFLICTING_DUPLICATE,
                        default_path=source_path,
                    )
                )
                continue
            if identity in conflicts:
                invalid_keys.add((label, identity))
                quarantine.append(
                    _quarantine(
                        label,
                        row,
                        QuarantineReason.CONFLICTING_DUPLICATE,
                        default_path=source_path,
                    )
                )
                continue
            if identity in seen:
                rejected_count += 1
                continue
            seen.add(identity)
            accepted_rows.append(row)
            accepted_ids.add((label, identity))
        validated[collection] = accepted_rows

    filtered_relations: list[dict[str, Any]] = []
    for relation in payload.get("relations", []) or []:
        if not isinstance(relation, Mapping):
            quarantine.append(
                _quarantine(
                    "Relation",
                    {"name": type(relation).__name__, "file_path": source_path},
                    QuarantineReason.INVALID_RECORD,
                    default_path=source_path,
                )
            )
            continue
        row = dict(relation)
        if any(
            field not in row
            for field in ("source_label", "target_label", "source_id", "target_id", "rel_type")
        ):
            quarantine.append(
                _quarantine("Relation", row, QuarantineReason.INVALID_RECORD, default_path=source_path)
            )
            continue
        source_key = (str(row.get("source_label") or ""), str(row.get("source_id") or ""))
        target_key = (str(row.get("target_label") or ""), str(row.get("target_id") or ""))
        registry = known_identities if known_identities is not None else accepted_ids
        unresolved = (
            source_key in invalid_keys
            or target_key in invalid_keys
            or (
                known_identities is not None
                and (source_key not in registry or target_key not in registry)
            )
        )
        if file_quarantined or unresolved:
            quarantine.append(
                _quarantine(
                    "Relation",
                    {"id": f"{source_key[1]}->{target_key[1]}", "file_path": source_path},
                    (
                        QuarantineReason.QUARANTINED_FILE_QUALITY
                        if file_quarantined
                        else QuarantineReason.UNRESOLVED_REFERENCE
                    ),
                    default_path=source_path,
                )
            )
            continue
        filtered_relations.append(row)
    validated["relations"] = filtered_relations

    filtered_calls: list[dict[str, Any]] = []
    for call in payload.get("calls", []) or []:
        if not isinstance(call, Mapping):
            quarantine.append(
                _quarantine(
                    "Call",
                    {"name": type(call).__name__, "file_path": source_path},
                    QuarantineReason.INVALID_RECORD,
                    default_path=source_path,
                )
            )
            continue
        row = dict(call)
        caller_id = str(row.get("caller_id") or "")
        if (
            file_quarantined
            or ("Function", caller_id) in invalid_keys
            or ("Function", caller_id) not in accepted_ids
        ):
            quarantine.append(
                _quarantine(
                    "Call",
                    {"id": caller_id, "name": row.get("callee_name") or "", "file_path": source_path},
                    (
                        QuarantineReason.QUARANTINED_FILE_QUALITY
                        if file_quarantined
                        else QuarantineReason.UNRESOLVED_REFERENCE
                    ),
                    default_path=source_path,
                )
            )
            continue
        filtered_calls.append(row)
    validated["calls"] = filtered_calls
    validated["_validation_rejected_count"] = rejected_count
    return validated, tuple(quarantine)


def accounting_for_payload(
    payload: Mapping[str, Any], quarantine: Sequence[QuarantineRecord]
) -> PayloadAccounting:
    accepted = sum(
        len(payload.get(collection, []) or []) for collection in _CPLUS_COLLECTION_LABELS
    ) + len(payload.get("relations", []) or []) + len(payload.get("calls", []) or [])
    quarantined = len(quarantine)
    rejected = int(payload.get("_validation_rejected_count") or 0)
    return PayloadAccounting(
        discovered=accepted + quarantined + rejected,
        accepted=accepted,
        quarantined=quarantined,
        rejected=rejected,
    )


def quarantine_dicts(records: Iterable[QuarantineRecord], *, limit: int) -> list[dict[str, Any]]:
    return [
        record.to_dict()
        for record in itertools.islice(records, max(0, limit))
    ]

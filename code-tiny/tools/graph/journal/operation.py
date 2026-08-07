"""Serializable graph-write operation contract used by the shared writer."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping

from .models import OperationPhase


_NODE_LABELS = frozenset(
    {
        "aliases",
        "classes",
        "fields",
        "files",
        "function_types",
        "functions",
        "namespaces",
        "packages",
        "projects",
        "templates",
        "types",
        "variables",
    }
)

_NODE_CONTRACTS = {
    "aliases": ("Alias", "id"),
    "classes": ("Class", "id"),
    "fields": ("Field", "id"),
    "files": ("File", "id"),
    "function_types": ("FunctionType", "id"),
    "functions": ("Function", "id"),
    "namespaces": ("Namespace", "id"),
    "packages": ("Package", "id"),
    "projects": ("Project", "project_id"),
    "templates": ("Template", "id"),
    "types": ("Type", "id"),
    "variables": ("Variable", "id"),
    "properties": ("Property", "id"),
    "events": ("Event", "id"),
    "interfaces": ("Interface", "id"),
    "enums": ("Enum", "id"),
    "constants": ("Constant", "id"),
    "navigators": ("Navigator", "id"),
    "param_lists": ("ParamList", "id"),
    "workflows": ("Workflow", "id"),
    "workflow_steps": ("WorkflowStep", "id"),
}


def phase_for_label(label: str) -> OperationPhase:
    normalized = label.strip().casefold()
    if normalized == "calls" or normalized.startswith("calls:"):
        return OperationPhase.CALLS
    if normalized in _NODE_LABELS:
        return OperationPhase.NODES
    if (
        normalized.endswith("_edges")
        or normalized.startswith("relations:")
        or normalized in {"relations", "relationships"}
    ):
        return OperationPhase.RELATIONSHIPS
    return OperationPhase.CUSTOM


@dataclass(frozen=True)
class GraphWriteOperation:
    """Stable, versioned description of one replay-safe graph mutation."""

    label: str
    phase: OperationPhase
    version: int = 1
    idempotent: bool = True
    reconciliation: str = "unsupported"
    node_label: str | None = None
    identity_property: str | None = None
    row_identity_property: str = "id"
    query_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("operation label must not be empty")
        if self.version < 1:
            raise ValueError("operation version must be positive")
        if not self.idempotent:
            raise ValueError("journaled graph operations must be replay-safe")

    @classmethod
    def for_label(cls, label: str) -> "GraphWriteOperation":
        normalized = label.strip().casefold()
        if normalized in _NODE_CONTRACTS:
            node_label, identity_property = _NODE_CONTRACTS[normalized]
            return cls(
                label=label,
                phase=OperationPhase.NODES,
                reconciliation="node_identity",
                node_label=node_label,
                identity_property=identity_property,
            )
        if normalized.startswith("relations:"):
            return cls(
                label=label,
                phase=OperationPhase.RELATIONSHIPS,
                reconciliation="typed_relationship",
            )
        if normalized == "repo_file_edges":
            return cls(
                label=label,
                phase=OperationPhase.RELATIONSHIPS,
                reconciliation="repository_file",
            )
        if normalized == "calls":
            return cls(
                label=label,
                phase=OperationPhase.CALLS,
                reconciliation="call_edge",
            )
        if normalized == "calls:site":
            return cls(
                label=label,
                phase=OperationPhase.CALLS,
                reconciliation="call_site",
            )
        return cls(label=label, phase=phase_for_label(label))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GraphWriteOperation":
        """Restore a validated operation descriptor without stored executable text."""

        operation = cls(
            label=str(value["label"]),
            phase=OperationPhase(str(value["phase"])),
            version=int(value.get("version", 1)),
            idempotent=bool(value.get("idempotent", True)),
            reconciliation=str(value.get("reconciliation", "unsupported")),
            node_label=(
                str(value["node_label"]) if value.get("node_label") else None
            ),
            identity_property=(
                str(value["identity_property"])
                if value.get("identity_property")
                else None
            ),
            row_identity_property=str(value.get("row_identity_property", "id")),
            query_fingerprint=(
                str(value["query_fingerprint"])
                if value.get("query_fingerprint")
                else None
            ),
        )
        if value.get("operation_key") not in {None, operation.operation_key}:
            raise ValueError("persisted operation key does not match its descriptor")
        return operation

    @property
    def operation_key(self) -> str:
        suffix = f"/{self.query_fingerprint}" if self.query_fingerprint else ""
        return f"graph-write/v{self.version}/{self.phase.value}/{self.label}{suffix}"

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "label": self.label,
            "phase": self.phase.value,
            "version": self.version,
            "idempotent": self.idempotent,
            "operation_key": self.operation_key,
            "reconciliation": self.reconciliation,
            "node_label": self.node_label,
            "identity_property": self.identity_property,
            "row_identity_property": self.row_identity_property,
            "query_fingerprint": self.query_fingerprint,
        }


_NODE_MERGE_PATTERN = re.compile(
    r"\bMERGE\s*\(\s*[A-Za-z_]\w*\s*:(?P<label>[A-Za-z_]\w*)"
    r"(?::[A-Za-z_]\w*)*\s*\{\s*(?P<identity>[A-Za-z_]\w*)\s*:"
    r"\s*row\.(?P<row_identity>[A-Za-z_]\w*)\s*\}",
    re.IGNORECASE,
)


def operation_for_custom_query(label: str, cypher: str) -> GraphWriteOperation:
    """Derive only the narrow allowlisted node-identity shape from source Cypher."""

    matches = list(_NODE_MERGE_PATTERN.finditer(cypher))
    query_fingerprint = hashlib.sha256(
        " ".join(cypher.split()).encode("utf-8")
    ).hexdigest()[:16]
    if len(matches) == 1:
        match = matches[0]
        return GraphWriteOperation(
            label=label,
            phase=OperationPhase.NODES,
            reconciliation="node_identity",
            node_label=match.group("label"),
            identity_property=match.group("identity"),
            row_identity_property=match.group("row_identity"),
            query_fingerprint=query_fingerprint,
        )
    base = GraphWriteOperation.for_label(label)
    return GraphWriteOperation(
        label=base.label,
        phase=base.phase,
        version=base.version,
        idempotent=base.idempotent,
        reconciliation=base.reconciliation,
        node_label=base.node_label,
        identity_property=base.identity_property,
        row_identity_property=base.row_identity_property,
        query_fingerprint=query_fingerprint,
    )

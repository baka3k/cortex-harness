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
    # Semantic call-evidence staging plane (cplus Phase 06).  Rows merge one
    # staging node per row; the optional third/fourth elements name the row's
    # identity and properties fields when they differ from ``id``/whole-row.
    "call_evidence:sites": ("CallSite", "site_id", "site_id", "props"),
    "call_evidence:configurations": (
        "BuildConfiguration",
        "config_fingerprint",
        "config_fingerprint",
        "props",
    ),
    "call_evidence:coverage": ("SemanticCoverage", "fingerprint", "fingerprint", "props"),
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
    row_properties_property: str | None = None
    mutation_kind: str = "merge"
    query_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("operation label must not be empty")
        if self.version < 1:
            raise ValueError("operation version must be positive")
        if not self.idempotent:
            raise ValueError("journaled graph operations must be replay-safe")
        if self.mutation_kind not in {"merge", "match"}:
            raise ValueError("unsupported persisted mutation kind")

    @classmethod
    def for_label(cls, label: str) -> "GraphWriteOperation":
        normalized = label.strip().casefold()
        if normalized in _NODE_CONTRACTS:
            contract = _NODE_CONTRACTS[normalized]
            node_label, identity_property = contract[0], contract[1]
            row_identity = contract[2] if len(contract) > 2 else "id"
            row_properties = contract[3] if len(contract) > 3 else None
            return cls(
                label=label,
                phase=OperationPhase.NODES,
                reconciliation="node_identity",
                node_label=node_label,
                identity_property=identity_property,
                row_identity_property=row_identity,
                row_properties_property=row_properties,
            )
        if normalized.startswith("relations:"):
            return cls(
                label=label,
                phase=OperationPhase.RELATIONSHIPS,
                reconciliation="typed_relationship",
            )
        if normalized == "call_evidence:edges" or normalized.startswith(
            "call_evidence:edges:"
        ):
            # Staging-plane evidence edges (HAS_CALLSITE, RESOLVES_TO,
            # OBSERVED_AS, IN_CONFIGURATION, EXECUTES_SQL,
            # RESOLVES_HOST_DECLARATION).  Rows are self-describing: they
            # carry endpoint label/property/value triples, the relationship
            # type, and an optional edge identity property, so replay and
            # reconciliation never need stored Cypher.  The label may carry
            # the compiled group suffix for per-shape journal accounting.
            return cls(
                label=label,
                phase=OperationPhase.RELATIONSHIPS,
                reconciliation="evidence_edge",
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
        if normalized == "possible_calls:site":
            # Same row shape as call_site but a weak-evidence edge; journal
            # replay must never materialize it as a strict CALLS relation.
            return cls(
                label=label,
                phase=OperationPhase.CALLS,
                reconciliation="possible_call_site",
            )
        return cls(label=label, phase=phase_for_label(label))

    @classmethod
    def for_node_contract(
        cls,
        label: str,
        *,
        node_label: str,
        identity_property: str = "id",
        row_identity_property: str = "id",
        row_properties_property: str | None = None,
    ) -> "GraphWriteOperation":
        """Declare an explicit replay-safe node upsert for specialized writers."""

        return cls(
            label=label,
            phase=OperationPhase.NODES,
            reconciliation="node_identity",
            node_label=node_label,
            identity_property=identity_property,
            row_identity_property=row_identity_property,
            row_properties_property=row_properties_property,
        )

    @classmethod
    def for_incremental_cleanup(
        cls,
        label: str,
        *,
        reconciliation: str,
        node_label: str | None = None,
    ) -> "GraphWriteOperation":
        """Declare an allowlisted, idempotent cleanup that runs in node phase."""

        if reconciliation not in {"file_cleanup", "orphan_unknown_cleanup"}:
            raise ValueError("unsupported incremental cleanup contract")
        return cls(
            label=label,
            phase=OperationPhase.NODES,
            version=2,
            reconciliation=reconciliation,
            node_label=node_label,
            mutation_kind="match",
        )

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
            row_properties_property=(
                str(value["row_properties_property"])
                if value.get("row_properties_property")
                else None
            ),
            mutation_kind=str(value.get("mutation_kind", "merge")),
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
            "row_properties_property": self.row_properties_property,
            "mutation_kind": self.mutation_kind,
            "query_fingerprint": self.query_fingerprint,
        }


_SAFE_NODE_QUERY_PATTERN = re.compile(
    r"^\s*UNWIND\s+\$rows\s+AS\s+row\s+"
    r"(?P<verb>MERGE|MATCH)\s*\(\s*(?P<variable>[A-Za-z_]\w*)\s*:"
    r"(?P<label>[A-Za-z_]\w*)\s*\{\s*(?P<identity>[A-Za-z_]\w*)\s*:"
    r"\s*row\.(?P<row_identity>[A-Za-z_]\w*)\s*\}\s*\)\s+"
    r"SET\s+(?P=variable)\s*\+=\s*row"
    r"(?:\.(?P<properties>[A-Za-z_]\w*))?\s*"
    r"(?:RETURN\s+count\((?P=variable)\)\s+AS\s+count\s*)?$",
    re.IGNORECASE,
)


def operation_for_custom_query(label: str, cypher: str) -> GraphWriteOperation:
    """Derive only the narrow allowlisted node-identity shape from source Cypher."""

    match = _SAFE_NODE_QUERY_PATTERN.fullmatch(cypher)
    query_fingerprint = hashlib.sha256(
        " ".join(cypher.split()).encode("utf-8")
    ).hexdigest()[:16]
    if match is not None:
        return GraphWriteOperation(
            label=label,
            phase=OperationPhase.NODES,
            reconciliation="node_identity",
            node_label=match.group("label"),
            identity_property=match.group("identity"),
            row_identity_property=match.group("row_identity"),
            row_properties_property=match.group("properties"),
            mutation_kind=match.group("verb").casefold(),
            query_fingerprint=query_fingerprint,
        )
    return GraphWriteOperation(
        label=label,
        phase=phase_for_label(label),
        query_fingerprint=query_fingerprint,
    )

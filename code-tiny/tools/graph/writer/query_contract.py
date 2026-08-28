"""Safe Cypher construction for label-qualified relationship mutations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from tools.common.project_scope import project_id_lookup_key
from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA, validate_cypher_identifier


@dataclass(frozen=True, order=True)
class RelationshipGroup:
    source_label: str
    target_label: str
    relationship_type: str

    def __post_init__(self) -> None:
        validate_cypher_identifier(self.source_label, kind="source label")
        validate_cypher_identifier(self.target_label, kind="target label")
        validate_cypher_identifier(self.relationship_type, kind="relationship type")
        for role, label in (
            ("source", self.source_label),
            ("target", self.target_label),
        ):
            if not CODE_GRAPH_SCHEMA.has_identity_index(label, "id"):
                raise ValueError(
                    f"{role} label {label!r} has no required id index in "
                    f"schema {CODE_GRAPH_SCHEMA.name}@{CODE_GRAPH_SCHEMA.fingerprint}"
                )

    @property
    def state_key(self) -> str:
        return (
            f"relations:{self.source_label}:{self.relationship_type}:"
            f"{self.target_label}"
        )


def group_typed_relations(
    relations: Iterable[Mapping[str, Any]],
    *,
    default_project_id: Any = None,
) -> Dict[RelationshipGroup, List[Dict[str, Any]]]:
    """Group fully scoped rows by endpoint labels and relationship type."""

    groups: Dict[RelationshipGroup, List[Dict[str, Any]]] = defaultdict(list)
    for position, relation in enumerate(relations):
        source_label = relation.get("source_label")
        target_label = relation.get("target_label")
        relationship_type = relation.get("rel_type")
        if not source_label or not target_label or not relationship_type:
            raise ValueError(
                "typed relationship row requires source_label, target_label, and "
                f"rel_type (row {position})"
            )
        if not relation.get("source_id") or not relation.get("target_id"):
            raise ValueError(
                f"typed relationship row requires source_id and target_id (row {position})"
            )
        row = dict(relation)
        properties = row.get("properties")
        property_scope = properties.get("project_id") if isinstance(properties, Mapping) else None
        project_id = row.get("project_id") or property_scope or default_project_id
        normalized_scope = project_id_lookup_key(project_id)
        if normalized_scope is None:
            raise ValueError(
                f"typed relationship row requires project_id (row {position})"
            )
        row["project_id"] = str(project_id).strip()
        row["project_id_normalized"] = normalized_scope
        # Cypher groups equal maps in ``WITH row, count(...)``. Preserve the
        # input ordinal so the endpoint audit measures node cardinality for
        # each submitted relation instead of accidentally counting duplicate
        # relation rows as duplicate nodes.
        row["_contract_row_position"] = position
        group = RelationshipGroup(
            str(source_label), str(target_label), str(relationship_type)
        )
        groups[group].append(row)
    return dict(groups)


def compile_relationship_upsert(group: RelationshipGroup) -> str:
    """Compile one indexable relationship MERGE query from validated identifiers."""

    return (
        "UNWIND $rows AS row "
        f"MATCH (a:{group.source_label} "
        "{id: row.source_id, project_id_normalized: row.project_id_normalized}) "
        "WITH row, a "
        f"MATCH (b:{group.target_label} "
        "{id: row.target_id, project_id_normalized: row.project_id_normalized}) "
        f"MERGE (a)-[r:{group.relationship_type}]->(b) "
        "SET r += coalesce(row.properties, {}), "
        "r.project_id = row.project_id, "
        "r.project_id_normalized = row.project_id_normalized "
        "RETURN count(r) AS count"
    )


def compile_relationship_endpoint_audit(group: RelationshipGroup) -> str:
    """Compile a read-only query that identifies non-unique endpoints.

    The mutation path fails closed when its returned count differs from the
    submitted row count. This companion query records whether each bad row
    has a missing or duplicate source/target without retrying the mutation.
    """

    return (
        "UNWIND $rows AS row "
        f"OPTIONAL MATCH (a:{group.source_label} "
        "{id: row.source_id, project_id_normalized: row.project_id_normalized}) "
        "WITH row, count(a) AS source_matches "
        f"OPTIONAL MATCH (b:{group.target_label} "
        "{id: row.target_id, project_id_normalized: row.project_id_normalized}) "
        "WITH row, source_matches, count(b) AS target_matches "
        "WHERE source_matches <> 1 OR target_matches <> 1 "
        "RETURN row.source_id AS source_id, row.target_id AS target_id, "
        "row.project_id_normalized AS project_id_normalized, "
        "source_matches, target_matches LIMIT 20"
    )


@dataclass(frozen=True, order=True)
class EvidenceEdgeGroup:
    """One staging-plane evidence edge shape.

    Unlike ``RelationshipGroup`` the endpoints are keyed by their own identity
    property (``CallSite`` by ``site_id``, ``BuildConfiguration`` by
    ``config_fingerprint``, everything else by ``id``) and the merged edge may
    carry its own identity property (``evidence_id``, ``statement_id``, …).
    An empty ``edge_property`` means the endpoint pair itself is the merge
    identity (pattern merge).
    """

    source_label: str
    source_property: str
    target_label: str
    target_property: str
    relationship_type: str
    edge_property: str = ""

    def __post_init__(self) -> None:
        for kind, identifier in (
            ("source label", self.source_label),
            ("target label", self.target_label),
            ("relationship type", self.relationship_type),
            ("source property", self.source_property),
            ("target property", self.target_property),
        ):
            validate_cypher_identifier(identifier, kind=kind)
        if self.edge_property:
            validate_cypher_identifier(self.edge_property, kind="edge property")
        for role, label, prop in (
            ("source", self.source_label, self.source_property),
            ("target", self.target_label, self.target_property),
        ):
            if not CODE_GRAPH_SCHEMA.has_identity_index(label, prop):
                raise ValueError(
                    f"{role} label {label!r} has no required {prop!r} index in "
                    f"schema {CODE_GRAPH_SCHEMA.name}@{CODE_GRAPH_SCHEMA.fingerprint}"
                )

    @property
    def state_key(self) -> str:
        edge_suffix = f":{self.edge_property}" if self.edge_property else ""
        return (
            f"call_evidence:edges:{self.source_label}:{self.source_property}:"
            f"{self.relationship_type}:{self.target_label}:"
            f"{self.target_property}{edge_suffix}"
        )


_EVIDENCE_EDGE_ROW_FIELDS = (
    "source_label",
    "source_property",
    "target_label",
    "target_property",
    "rel_type",
)


def group_evidence_edges(
    edges: Iterable[Mapping[str, Any]],
) -> Dict[EvidenceEdgeGroup, List[Dict[str, Any]]]:
    """Group self-describing evidence edge rows by their edge shape.

    Every row requires ``source_label``/``source_property``/``source_id``,
    ``target_label``/``target_property``/``target_id``, and ``rel_type``; the
    optional ``edge_property`` names the merged edge's identity property.
    """

    groups: Dict[EvidenceEdgeGroup, List[Dict[str, Any]]] = defaultdict(list)
    for position, edge in enumerate(edges):
        missing = [
            field
            for field in _EVIDENCE_EDGE_ROW_FIELDS
            if not str(edge.get(field) or "").strip()
        ]
        if missing or not str(edge.get("source_id") or "").strip() or not str(
            edge.get("target_id") or ""
        ).strip():
            raise ValueError(
                "evidence edge row requires source/target label, property, id, "
                f"and rel_type (row {position})"
            )
        row = dict(edge)
        project_id = row.get("project_id") or (row.get("props") or {}).get(
            "project_id"
        )
        normalized_scope = project_id_lookup_key(project_id)
        if normalized_scope is None:
            raise ValueError(
                f"evidence edge row requires project_id (row {position})"
            )
        row["project_id"] = str(project_id).strip()
        row["project_id_normalized"] = normalized_scope
        edge_property = str(edge.get("edge_property") or "")
        edge_id = str(edge.get("edge_id") or "")
        if edge_property and not edge_id:
            # A keyed merge without its key value would collapse distinct
            # edges onto the endpoint identity and silently lose evidence.
            raise ValueError(
                "evidence edge row with an edge_property requires a non-empty "
                f"edge_id (row {position})"
            )
        row["edge_id"] = edge_id or row["source_id"]
        group = EvidenceEdgeGroup(
            str(edge["source_label"]),
            str(edge["source_property"]),
            str(edge["target_label"]),
            str(edge["target_property"]),
            str(edge["rel_type"]),
            edge_property,
        )
        groups[group].append(row)
    return dict(groups)


def compile_evidence_edge_upsert(group: EvidenceEdgeGroup) -> str:
    """Compile one indexable evidence-edge MERGE from validated identifiers."""

    edge_pattern = (
        f"MERGE (a)-[r:{group.relationship_type} "
        f"{{{group.edge_property}: row.edge_id}}]->(b) "
        if group.edge_property
        else f"MERGE (a)-[r:{group.relationship_type}]->(b) "
    )
    return (
        "UNWIND $rows AS row "
        f"MATCH (a:{group.source_label} "
        f"{{{group.source_property}: row.source_id, "
        "project_id_normalized: row.project_id_normalized}) "
        "WITH row, a "
        f"MATCH (b:{group.target_label} "
        f"{{{group.target_property}: row.target_id, "
        "project_id_normalized: row.project_id_normalized}) "
        + edge_pattern +
        "SET r += coalesce(row.props, {}), "
        "r.project_id = row.project_id, "
        "r.updated_at = datetime() "
        "RETURN count(r) AS count"
    )


def compile_evidence_edge_readback(group: EvidenceEdgeGroup) -> str:
    """Compile the deterministic readback for one evidence-edge batch."""

    edge_match = (
        f"-[r:{group.relationship_type} {{{group.edge_property}: row.edge_id}}]->"
        if group.edge_property
        else f"-[r:{group.relationship_type}]->"
    )


def compile_evidence_endpoint_audit(group: EvidenceEdgeGroup) -> str:
    """Compile a read-only cardinality audit for evidence-edge endpoints."""

    return (
        "UNWIND $rows AS row "
        f"OPTIONAL MATCH (a:{group.source_label} "
        f"{{{group.source_property}: row.source_id, "
        "project_id_normalized: row.project_id_normalized}) "
        "WITH row, count(a) AS source_matches "
        f"OPTIONAL MATCH (b:{group.target_label} "
        f"{{{group.target_property}: row.target_id, "
        "project_id_normalized: row.project_id_normalized}) "
        "WITH row, source_matches, count(b) AS target_matches "
        "WHERE source_matches <> 1 OR target_matches <> 1 "
        "RETURN row.source_id AS source_id, row.target_id AS target_id, "
        "row.project_id_normalized AS project_id_normalized, "
        "source_matches, target_matches LIMIT 20"
    )
    return (
        "UNWIND $rows AS row "
        f"OPTIONAL MATCH (a:{group.source_label} "
        f"{{{group.source_property}: row.source_id, "
        "project_id_normalized: row.project_id_normalized}}) "
        + edge_match +
        f"(b:{group.target_label} "
        f"{{{group.target_property}: row.target_id, "
        "project_id_normalized: row.project_id_normalized}}) "
        "RETURN count(r) AS count"
    )


__all__ = [
    "EvidenceEdgeGroup",
    "RelationshipGroup",
    "compile_evidence_edge_readback",
    "compile_evidence_edge_upsert",
    "compile_evidence_endpoint_audit",
    "compile_relationship_endpoint_audit",
    "compile_relationship_upsert",
    "group_evidence_edges",
    "group_typed_relations",
]

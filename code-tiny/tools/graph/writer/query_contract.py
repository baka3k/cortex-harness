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


__all__ = [
    "RelationshipGroup",
    "compile_relationship_endpoint_audit",
    "compile_relationship_upsert",
    "group_typed_relations",
]

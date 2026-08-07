"""Safe Cypher construction for label-qualified relationship mutations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

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
) -> Dict[RelationshipGroup, List[Dict[str, Any]]]:
    """Group rows by both endpoint labels and type; unlabeled rows are unsafe."""

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
        group = RelationshipGroup(
            str(source_label), str(target_label), str(relationship_type)
        )
        groups[group].append(dict(relation))
    return dict(groups)


def compile_relationship_upsert(group: RelationshipGroup) -> str:
    """Compile one indexable relationship MERGE query from validated identifiers."""

    return (
        "UNWIND $rows AS row "
        f"MATCH (a:{group.source_label} {{id: row.source_id}}) "
        "WITH row, a "
        f"MATCH (b:{group.target_label} {{id: row.target_id}}) "
        f"MERGE (a)-[r:{group.relationship_type}]->(b) "
        "SET r += coalesce(row.properties, {}) "
        "RETURN count(r) AS count"
    )


__all__ = [
    "RelationshipGroup",
    "compile_relationship_upsert",
    "group_typed_relations",
]

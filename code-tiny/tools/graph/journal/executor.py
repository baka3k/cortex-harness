"""Trusted compiler for replaying persisted graph-write descriptors."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from tools.graph.schema.manifest import validate_cypher_identifier
from tools.graph.writer.query_contract import (
    EvidenceEdgeGroup,
    compile_evidence_edge_upsert,
    compile_relationship_upsert,
    group_evidence_edges,
    group_typed_relations,
)

from .models import JournalError, TerminalErrorCode
from .operation import GraphWriteOperation


def compile_persisted_mutation(
    operation: GraphWriteOperation,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Compile allowlisted operation kinds; persisted Cypher is never executed."""

    materialized = [dict(row) for row in rows]
    if operation.reconciliation == "node_identity":
        if not operation.node_label or not operation.identity_property:
            raise _unsupported(operation)
        validate_cypher_identifier(operation.node_label, kind="node label")
        validate_cypher_identifier(operation.identity_property, kind="identity property")
        validate_cypher_identifier(
            operation.row_identity_property, kind="row identity property"
        )
        verb = "MERGE" if operation.mutation_kind == "merge" else "MATCH"
        row_value = (
            f"coalesce(row.{operation.row_properties_property}, {{}})"
            if operation.row_properties_property
            else "row"
        )
        if operation.row_properties_property:
            validate_cypher_identifier(
                operation.row_properties_property, kind="row properties property"
            )
        query = (
            "UNWIND $rows AS row "
            f"{verb} (n:{operation.node_label} "
            f"{{{operation.identity_property}: row.{operation.row_identity_property}}}) "
            f"SET n += {row_value}, n.updated_at = datetime() "
            "RETURN count(n) AS count"
        )
        return query, {"rows": materialized}
    if operation.reconciliation == "typed_relationship":
        groups = group_typed_relations(materialized)
        if len(groups) != 1:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "persisted relationship batch must contain one endpoint/type triple",
            )
        group, grouped_rows = next(iter(groups.items()))
        return compile_relationship_upsert(group), {"rows": grouped_rows}
    if operation.reconciliation == "evidence_edge":
        groups = group_evidence_edges(materialized)
        if len(groups) != 1:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "persisted evidence-edge batch must contain one edge shape",
            )
        group, grouped_rows = next(iter(groups.items()))
        return compile_evidence_edge_upsert(group), {"rows": grouped_rows}
    if operation.reconciliation == "repository_file":
        return (
            "UNWIND $rows AS row "
            "MATCH (repository:Repository {name: row.repo, "
            "project_id_normalized: row.project_id_normalized}) "
            "MATCH (file:File {id: row.id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MERGE (repository)-[edge:HAS_FILE]->(file) "
            "RETURN count(edge) AS count",
            {"rows": materialized},
        )
    if operation.reconciliation == "call_edge":
        return (
            "UNWIND $rows AS row "
            "MATCH (caller:Function {id: row.caller_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MATCH (callee:Function {id: row.callee_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MERGE (caller)-[edge:CALLS]->(callee) "
            "SET edge.count = row.count, edge.call_type = row.call_type, "
            "edge.project_id = row.project_id, "
            "edge.project_id_normalized = row.project_id_normalized, "
            "edge.updated_at = datetime() "
            "RETURN count(edge) AS count",
            {"rows": materialized},
        )
    if operation.reconciliation == "call_site":
        return (
            "UNWIND $rows AS row "
            "MATCH (caller:Function {id: row.caller_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MATCH (callee:Function {id: row.callee_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MERGE (caller)-[edge:CALLS {site_id: row.site_id}]->(callee) "
            "SET edge += coalesce(row.props, {}) "
            "RETURN count(edge) AS count",
            {"rows": materialized},
        )
    if operation.reconciliation == "possible_call_site":
        return (
            "UNWIND $rows AS row "
            "MATCH (caller:Function {id: row.caller_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MATCH (callee:Function {id: row.callee_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "MERGE (caller)-[edge:POSSIBLE_CALLS {site_id: row.site_id}]->(callee) "
            "SET edge += coalesce(row.props, {}) "
            "RETURN count(edge) AS count",
            {"rows": materialized},
        )
    if operation.reconciliation == "file_cleanup":
        if operation.version != 2:
            raise _unsupported(operation)
        if not operation.node_label:
            raise _unsupported(operation)
        validate_cypher_identifier(operation.node_label, kind="node label")
        return (
            "UNWIND $rows AS row "
            f"OPTIONAL MATCH (n:{operation.node_label}) "
            "WHERE n.project_id = row.project_id "
            "AND (coalesce(n.file_path, '') IN row.paths "
            "OR coalesce(n.path, '') IN row.paths "
            "OR (n:File AND n.id IN row.paths)) "
            "WITH row, collect(DISTINCT n) AS nodes "
            "FOREACH (node IN nodes | DETACH DELETE node) "
            "RETURN count(*) AS count",
            {"rows": materialized},
        )
    if operation.reconciliation == "orphan_unknown_cleanup":
        if operation.version != 2:
            raise _unsupported(operation)
        return (
            "UNWIND $rows AS row "
            "OPTIONAL MATCH (u:UnknownFunction) "
            "WHERE u.project_id = row.project_id "
            "AND NOT ()-[:UNKNOWN_CALL]->(u) "
            "WITH row, collect(u) AS nodes "
            "FOREACH (node IN nodes | DETACH DELETE node) "
            "RETURN count(*) AS count",
            {"rows": materialized},
        )
    raise _unsupported(operation)


def _unsupported(operation: GraphWriteOperation) -> JournalError:
    return JournalError(
        TerminalErrorCode.INVALID_CONTRACT,
        f"operation {operation.operation_key} has no trusted replay compiler",
    )


def result_count(records: Sequence[Mapping[str, Any]]) -> int:
    return int(records[0].get("count", 0)) if records else 0

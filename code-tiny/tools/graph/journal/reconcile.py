"""Allowlisted deterministic readback queries for ambiguous graph writes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from tools.graph.writer.query_contract import (
    compile_evidence_edge_readback,
    compile_evidence_endpoint_audit,
    compile_relationship_endpoint_audit,
    group_evidence_edges,
    group_typed_relations,
)

from .operation import GraphWriteOperation


def compile_endpoint_audit(
    operation: GraphWriteOperation,
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Compile a read-only endpoint-cardinality audit for one staged edge batch."""

    materialized = [dict(row) for row in rows]
    if operation.reconciliation == "typed_relationship":
        groups = group_typed_relations(materialized)
        if len(groups) != 1:
            raise ValueError("one endpoint audit batch must contain one edge shape")
        group, grouped_rows = next(iter(groups.items()))
        return compile_relationship_endpoint_audit(group), {"rows": grouped_rows}
    if operation.reconciliation == "repository_file":
        return (
            "UNWIND $rows AS row "
            "OPTIONAL MATCH (a:Repository {name: row.repo, "
            "project_id_normalized: row.project_id_normalized}) "
            "WITH row, count(a) AS source_matches "
            "OPTIONAL MATCH (b:File {id: row.id, "
            "project_id_normalized: row.project_id_normalized}) "
            "WITH row, source_matches, count(b) AS target_matches "
            "WHERE source_matches <> 1 OR target_matches <> 1 "
            "RETURN row.repo AS source_id, row.id AS target_id, "
            "source_matches, target_matches LIMIT 20",
            {"rows": materialized},
        )
    if operation.reconciliation in {
        "call_edge",
        "call_site",
        "possible_call_site",
    }:
        return (
            "UNWIND $rows AS row "
            "OPTIONAL MATCH (a:Function {id: row.caller_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "WITH row, count(a) AS source_matches "
            "OPTIONAL MATCH (b:Function {id: row.callee_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "WITH row, source_matches, count(b) AS target_matches "
            "WHERE source_matches <> 1 OR target_matches <> 1 "
            "RETURN row.caller_id AS source_id, row.callee_id AS target_id, "
            "source_matches, target_matches LIMIT 20",
            {"rows": materialized},
        )
    if operation.reconciliation == "evidence_edge":
        groups = group_evidence_edges(materialized)
        if len(groups) != 1:
            raise ValueError("one evidence audit batch must contain one edge shape")
        group, grouped_rows = next(iter(groups.items()))
        return compile_evidence_endpoint_audit(group), {"rows": grouped_rows}
    if operation.phase.value in {"relationships", "calls"}:
        raise ValueError(
            f"operation {operation.operation_key} has no endpoint audit contract"
        )
    return None


def compile_reconciliation_readback(
    operation: GraphWriteOperation,
    rows: Sequence[Mapping[str, Any]],
    *,
    job_id: str | None = None,
    artifact_sha256: str = "",
    run_id: str = "",
    generation: str = "",
) -> tuple[str, dict[str, Any]] | None:
    """Compile a payload-only readback; never deserialize stored Cypher."""

    if job_id:
        return (
            "MATCH (receipt:GraphWriteReceipt {id: $job_id}) "
            "WHERE receipt.operation_key = $operation_key "
            "AND receipt.row_count = $expected_count "
            "AND receipt.artifact_sha256 = $artifact_sha256 "
            "AND receipt.run_id = $run_id "
            "AND receipt.generation = $generation "
            "RETURN count(receipt) AS count",
            {
                "job_id": job_id,
                "operation_key": operation.operation_key,
                "expected_count": len(rows),
                "artifact_sha256": artifact_sha256,
                "run_id": run_id,
                "generation": generation,
            },
        )

    if operation.reconciliation == "node_identity":
        assert operation.node_label and operation.identity_property
        return (
            "UNWIND $rows AS row "
            f"OPTIONAL MATCH (n:{operation.node_label} "
            f"{{{operation.identity_property}: row.{operation.row_identity_property}}}) "
            "RETURN count(n) AS count",
            {"rows": list(rows)},
        )
    if operation.reconciliation == "typed_relationship":
        groups = group_typed_relations(rows)
        if len(groups) != 1:
            raise ValueError("one journal relationship batch must contain one label triple")
        group = next(iter(groups))
        return (
            "UNWIND $rows AS row "
            f"OPTIONAL MATCH (a:{group.source_label} {{id: row.source_id}})"
            f"-[r:{group.relationship_type}]->"
            f"(b:{group.target_label} {{id: row.target_id}}) "
            "RETURN count(r) AS count",
            {"rows": list(rows)},
        )
    if operation.reconciliation == "evidence_edge":
        groups = group_evidence_edges(rows)
        if len(groups) != 1:
            raise ValueError("one evidence-edge batch must contain one edge shape")
        group = next(iter(groups))
        return compile_evidence_edge_readback(group), {"rows": list(rows)}
    if operation.reconciliation == "repository_file":
        return (
            "UNWIND $rows AS row "
            "OPTIONAL MATCH (r:Repository {name: row.repo, "
            "project_id_normalized: row.project_id_normalized})"
            "-[edge:HAS_FILE]->(f:File {id: row.id, "
            "project_id_normalized: row.project_id_normalized}) "
            "RETURN count(edge) AS count",
            {"rows": list(rows)},
        )
    if operation.reconciliation == "call_edge":
        return (
            "UNWIND $rows AS row "
            "OPTIONAL MATCH (caller:Function {id: row.caller_id, "
            "project_id_normalized: row.project_id_normalized})"
            "-[r:CALLS]->(callee:Function {id: row.callee_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "WHERE r.count = row.count AND r.call_type = row.call_type "
            "RETURN count(r) AS count",
            {"rows": list(rows)},
        )
    if operation.reconciliation == "call_site":
        return (
            "UNWIND $rows AS row "
            "OPTIONAL MATCH (caller:Function {id: row.caller_id, "
            "project_id_normalized: row.project_id_normalized})"
            "-[r:CALLS {site_id: row.site_id}]->"
            "(callee:Function {id: row.callee_id, "
            "project_id_normalized: row.project_id_normalized}) "
            "RETURN count(r) AS count",
            {"rows": list(rows)},
        )
    return None


def readback_count(records: Sequence[Mapping[str, Any]]) -> int:
    return int(records[0].get("count", 0)) if records else 0

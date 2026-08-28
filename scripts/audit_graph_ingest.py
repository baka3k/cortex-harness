#!/usr/bin/env python3
"""Read-only graph-ingest integrity audit for rollout and recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.journal.identity import canonical_json, sha256_hex  # noqa: E402
from tools.graph.journal.sqlite_store import inspect_journal  # noqa: E402
from tools.graph.schema import CODE_GRAPH_SCHEMA  # noqa: E402


_PRODUCERS_COMPLETE_ID = "__journal_all_producers_complete__"


def _manifest_totals(connection: sqlite3.Connection, table: str, run_id: str) -> dict[str, int | bool]:
    row = connection.execute(
        f"""
        SELECT COUNT(*) AS emitted,
          SUM(CASE WHEN disposition = 'staged_unique' THEN 1 ELSE 0 END) AS staged_unique,
          SUM(CASE WHEN disposition = 'declared_duplicate' THEN 1 ELSE 0 END) AS declared_duplicate,
          SUM(CASE WHEN disposition = 'conflict' THEN 1 ELSE 0 END) AS conflict,
          SUM(CASE WHEN disposition = 'rejected' THEN 1 ELSE 0 END) AS rejected,
          SUM(acked) AS acked, SUM(graph_verified) AS graph_verified
        FROM {table} WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    totals = {key: int(row[key] or 0) for key in row.keys()}
    totals["conserved"] = bool(
        totals["emitted"]
        == totals["staged_unique"]
        + totals["declared_duplicate"]
        + totals["conflict"]
        + totals["rejected"]
        and totals["staged_unique"] == totals["acked"]
        and totals["acked"] == totals["graph_verified"]
    )
    return totals


def _load_journal_evidence(path: Path, requested_run_id: str | None) -> tuple[list[dict[str, object]], set[tuple[object, ...]], bool]:
    summaries = inspect_journal(path)
    selected = [
        item for item in summaries
        if requested_run_id is None or item.get("run_id") == requested_run_id
    ]
    requested_run_missing = bool(requested_run_id and not selected)
    expected_receipts: set[tuple[object, ...]] = set()
    connection = sqlite3.connect(f"{path.expanduser().resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for item in selected:
            run_id = str(item["run_id"])
            node = _manifest_totals(connection, "node_manifest", run_id)
            edge = _manifest_totals(connection, "edge_manifest", run_id)
            producer_row = connection.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open "
                "FROM producer_completion "
                "WHERE run_id = ? AND producer_id != ?",
                (run_id, _PRODUCERS_COMPLETE_ID),
            ).fetchone()
            production_complete = connection.execute(
                "SELECT 1 FROM producer_completion "
                "WHERE run_id = ? AND producer_id = ? AND status = 'complete'",
                (run_id, _PRODUCERS_COMPLETE_ID),
            ).fetchone()
            audit_row = connection.execute(
                "SELECT status, manifest_digest, receipt_count "
                "FROM endpoint_audit WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            endpoint_rows = connection.execute(
                """
                SELECT em.manifest_id, em.job_id, em.producer_id, em.row_ordinal,
                       em.scope, em.relationship_type, em.identity_type,
                       em.identity_json, em.payload_digest, em.disposition,
                       ep.role, ep.scope AS endpoint_scope, ep.node_label,
                       ep.identity_property,
                       ep.identity_type AS endpoint_identity_type,
                       ep.identity_json AS endpoint_identity_json, ep.required
                FROM edge_manifest AS em
                LEFT JOIN edge_endpoint AS ep
                  ON ep.run_id = em.run_id
                 AND ep.edge_manifest_id = em.manifest_id
                WHERE em.run_id = ?
                ORDER BY em.manifest_id, ep.role
                """,
                (run_id,),
            ).fetchall()
            endpoint_manifest_digest = sha256_hex(
                canonical_json([dict(row) for row in endpoint_rows])
            )
            endpoint_audit_valid = bool(
                audit_row
                and audit_row["status"] == "sealed"
                and production_complete is not None
                and int(producer_row["open"] or 0) == 0
                and audit_row["manifest_digest"] == endpoint_manifest_digest
                and int(audit_row["receipt_count"]) == int(edge["emitted"])
            )
            item["conservation"] = {
                "node": node,
                "edge": edge,
                "producers": {
                    "total": int(producer_row["total"] or 0),
                    "open": int(producer_row["open"] or 0),
                },
                "conserved": bool(node["conserved"] and edge["conserved"]),
            }
            item["endpoint_audit"] = str(audit_row["status"]) if audit_row else None
            item["endpoint_audit_evidence"] = {
                "valid": endpoint_audit_valid,
                "manifest_digest": endpoint_manifest_digest,
                "audited_rows": int(edge["emitted"]),
                "production_complete": production_complete is not None,
            }
            metadata = json.loads(
                connection.execute(
                    "SELECT metadata_json FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
            )
            generation = str(metadata.get("generation") or "")
            for batch in connection.execute(
                "SELECT job_id, operation_key, expected_count, artifact_sha256 "
                "FROM batches WHERE run_id = ? AND status = 'done'",
                (run_id,),
            ):
                expected_receipts.add(
                    (
                        str(batch["job_id"]),
                        str(batch["operation_key"]),
                        int(batch["expected_count"]),
                        str(batch["artifact_sha256"]),
                        run_id,
                        generation,
                    )
                )
    finally:
        connection.close()
    return selected, expected_receipts, requested_run_missing


def _journal_is_valid(item: dict[str, object]) -> bool:
    conservation = item.get("conservation") or {}
    node = conservation.get("node") or {}
    edge = conservation.get("edge") or {}
    producers = conservation.get("producers") or {}
    return bool(
        item.get("status") == "drained"
        and item.get("endpoint_audit") == "sealed"
        and (item.get("endpoint_audit_evidence") or {}).get("valid") is True
        and conservation.get("conserved")
        and not node.get("conflict")
        and not node.get("rejected")
        and not edge.get("conflict")
        and not edge.get("rejected")
        and not producers.get("open")
    )


async def audit(args: argparse.Namespace) -> dict[str, object]:
    journals: list[dict[str, object]] = []
    expected_receipts: set[tuple[object, ...]] = set()
    requested_run_missing = False
    if args.journal:
        journals, expected_receipts, requested_run_missing = _load_journal_evidence(
            Path(args.journal), args.run_id
        )

    driver = FalkorDBDriver(
        path=Path(args.path).resolve() if args.path else None,
        uri=args.uri,
        password=args.password,
        ssl=args.ssl,
        graph=args.graph,
    )
    duplicates: list[dict[str, object]] = []
    try:
        counts: dict[str, int] = {}
        for name, query in {
            "nodes": "MATCH (n) RETURN count(n) AS count",
            "business_nodes": (
                "MATCH (n) WHERE NOT n:GraphWriteReceipt RETURN count(n) AS count"
            ),
            "relationships": "MATCH ()-[r]->() RETURN count(r) AS count",
            "files": "MATCH (n:File) RETURN count(n) AS count",
            "receipts": "MATCH (n:GraphWriteReceipt) RETURN count(n) AS count",
            "invalid_receipts": (
                "MATCH (n:GraphWriteReceipt) "
                "WHERE n.artifact_sha256 IS NULL OR n.run_id IS NULL "
                "OR n.generation IS NULL OR n.operation_key IS NULL "
                "RETURN count(n) AS count"
            ),
        }.items():
            records, _, _ = await driver.execute_query(query, {}, args.graph)
            counts[name] = int(records[0]["count"]) if records else 0

        identities = sorted(
            {
                (index.label, property_name)
                for index in CODE_GRAPH_SCHEMA.indexes
                if index.required and index.index_type == "range"
                for property_name in index.properties
                if property_name != "project_id_normalized"
            }
        )
        for label, property_name in identities:
            query = (
                f"MATCH (n:{label}) WHERE n.{property_name} IS NOT NULL "
                f"WITH n.project_id_normalized AS scope, n.{property_name} AS identity, "
                "count(n) AS duplicates WHERE duplicates > 1 "
                "RETURN scope, identity, duplicates LIMIT 20"
            )
            records, _, _ = await driver.execute_query(query, {}, args.graph)
            duplicates.extend(
                {
                    "label": label,
                    "property": property_name,
                    "scope": record.get("scope"),
                    "identity": record.get("identity"),
                    "duplicates": int(record.get("duplicates") or 0),
                }
                for record in records
            )

        actual_receipts: set[tuple[object, ...]] = set()
        if journals:
            records, _, _ = await driver.execute_query(
                "MATCH (receipt:GraphWriteReceipt) "
                "WHERE receipt.run_id IN $run_ids "
                "RETURN receipt.id AS job_id, "
                "receipt.operation_key AS operation_key, "
                "receipt.row_count AS row_count, "
                "receipt.artifact_sha256 AS artifact_sha256, "
                "receipt.run_id AS run_id, receipt.generation AS generation",
                {"run_ids": [str(item["run_id"]) for item in journals]},
                args.graph,
            )
            actual_receipts = {
                (
                    str(record.get("job_id") or ""),
                    str(record.get("operation_key") or ""),
                    int(record.get("row_count") or 0),
                    str(record.get("artifact_sha256") or ""),
                    str(record.get("run_id") or ""),
                    str(record.get("generation") or ""),
                )
                for record in records
            }
    finally:
        driver.close()

    invalid_journals = [item for item in journals if not _journal_is_valid(item)]
    missing_receipts = sorted(expected_receipts - actual_receipts)
    unexpected_receipts = sorted(actual_receipts - expected_receipts)
    missing_receipt_coverage = bool(
        counts["business_nodes"] > 0
        and (
            not journals
            or requested_run_missing
            or missing_receipts
            or unexpected_receipts
        )
    )
    incomplete = bool(
        duplicates
        or counts["invalid_receipts"]
        or missing_receipt_coverage
        or invalid_journals
        or requested_run_missing
    )
    return {
        "schema_version": 1,
        "mode": "read_only",
        "target": "local" if args.path else "remote",
        "graph": args.graph,
        "counts": counts,
        "duplicate_identities": duplicates,
        "missing_receipt_coverage": missing_receipt_coverage,
        "requested_run_missing": requested_run_missing,
        "receipt_coverage": {
            "expected": len(expected_receipts),
            "matched": len(expected_receipts & actual_receipts),
            "missing": len(missing_receipts),
            "unexpected": len(unexpected_receipts),
        },
        "journals": journals,
        "incomplete": incomplete,
        "recommendation": "rebuild" if incomplete else "eligible_for_validation",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--path")
    target.add_argument("--uri")
    parser.add_argument("--password")
    parser.add_argument("--ssl", action="store_true")
    parser.add_argument("--graph", required=True)
    parser.add_argument("--journal")
    parser.add_argument("--run-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(audit(args))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if report["incomplete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only graph-ingest integrity audit for rollout and recovery."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.journal.sqlite_store import inspect_journal  # noqa: E402
from tools.graph.schema import CODE_GRAPH_SCHEMA  # noqa: E402


async def audit(args: argparse.Namespace) -> dict[str, object]:
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
    finally:
        driver.close()

    journals = inspect_journal(Path(args.journal)) if args.journal else []
    if args.run_id:
        journals = [item for item in journals if item.get("run_id") == args.run_id]
    incomplete_journals = [
        item for item in journals if item.get("status") != "drained"
    ]
    missing_receipt_coverage = bool(
        counts["business_nodes"] > 0 and counts["receipts"] == 0
    )
    incomplete = bool(
        duplicates
        or counts["invalid_receipts"]
        or missing_receipt_coverage
        or incomplete_journals
    )
    return {
        "schema_version": 1,
        "mode": "read_only",
        "target": "local" if args.path else "remote",
        "graph": args.graph,
        "counts": counts,
        "duplicate_identities": duplicates,
        "missing_receipt_coverage": missing_receipt_coverage,
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

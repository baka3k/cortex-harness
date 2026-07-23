"""Backfill case-insensitive project-scope keys in graph and Qdrant stores.

Dry-run is the default. Pass ``--apply`` to update records and create indexes.
Raw ``project_id`` values, graph identities, Qdrant point IDs, and vectors are
never modified.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import requests


CODE_TINY = Path(__file__).resolve().parents[1]
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.project_scope import (  # noqa: E402
    PROJECT_ID_NORMALIZED_FIELD,
    normalize_project_id,
    project_id_lookup_key,
)
from tools.graph import GraphDriverFactory, GraphProvider  # noqa: E402


_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ScopeInventory:
    eligible: int = 0
    missing_project_id: int = 0
    already_normalized: int = 0
    needs_update: int = 0
    updated: int = 0
    collisions: Dict[str, list[str]] = field(default_factory=dict)


def build_scope_inventory(
    rows: Iterable[Mapping[str, Any]],
    *,
    count_key: str = "count",
) -> ScopeInventory:
    """Summarize raw/normalized project identifiers from aggregate rows."""
    inventory = ScopeInventory()
    variants: Dict[str, set[str]] = defaultdict(set)
    for row in rows:
        count = max(0, int(row.get(count_key) or 0))
        raw = normalize_project_id(row.get("project_id"))
        if raw is None:
            inventory.missing_project_id += count
            continue
        lookup_key = project_id_lookup_key(raw)
        if lookup_key is None:  # defensive; raw is already known non-empty
            inventory.missing_project_id += count
            continue
        inventory.eligible += count
        variants[lookup_key].add(raw)
        if row.get(PROJECT_ID_NORMALIZED_FIELD) == lookup_key:
            inventory.already_normalized += count
        else:
            inventory.needs_update += count
    inventory.collisions = {
        key: sorted(values)
        for key, values in sorted(variants.items())
        if len(values) > 1
    }
    return inventory


async def backfill_graph(
    driver: Any,
    *,
    database: Optional[str],
    apply: bool,
) -> ScopeInventory:
    """Inventory and optionally backfill all project-scoped graph nodes."""
    records, _, _ = await driver.execute_query(
        "MATCH (n) "
        "RETURN n.project_id AS project_id, "
        f"n.{PROJECT_ID_NORMALIZED_FIELD} AS {PROJECT_ID_NORMALIZED_FIELD}, "
        "count(n) AS count",
        database=database,
    )
    inventory = build_scope_inventory(records)
    if not apply:
        return inventory

    if inventory.needs_update:
        raw_values = sorted(
            {
                normalize_project_id(row.get("project_id"))
                for row in records
                if normalize_project_id(row.get("project_id")) is not None
            }
        )
        for raw in raw_values:
            normalized = project_id_lookup_key(raw)
            updated, _, _ = await driver.execute_query(
                "MATCH (n) WHERE n.project_id = $raw "
                f"AND (n.{PROJECT_ID_NORMALIZED_FIELD} IS NULL "
                f"OR n.{PROJECT_ID_NORMALIZED_FIELD} <> $normalized) "
                f"SET n.{PROJECT_ID_NORMALIZED_FIELD} = $normalized "
                "RETURN count(n) AS count",
                {"raw": raw, "normalized": normalized},
                database=database,
            )
            inventory.updated += int((updated or [{}])[0].get("count", 0))

    label_rows, _, _ = await driver.execute_query(
        "MATCH (n) WHERE n.project_id IS NOT NULL "
        "RETURN DISTINCT labels(n) AS labels",
        database=database,
    )
    labels = sorted(
        {
            str(label)
            for row in label_rows
            for label in (row.get("labels") or [])
            if _LABEL_RE.fullmatch(str(label))
        }
    )
    if labels:
        await driver.create_indexes(
            [
                {"label": label, "property": PROJECT_ID_NORMALIZED_FIELD}
                for label in labels
            ],
            database=database,
        )
    return inventory


def _qdrant_collections(
    session: Any,
    qdrant_url: str,
    timeout: float,
) -> list[str]:
    response = session.get(f"{qdrant_url.rstrip('/')}/collections", timeout=timeout)
    response.raise_for_status()
    result = response.json().get("result") or {}
    return sorted(
        str(item["name"])
        for item in result.get("collections", [])
        if isinstance(item, Mapping) and item.get("name")
    )


def _set_qdrant_payload(
    session: Any,
    *,
    qdrant_url: str,
    collection: str,
    point_ids: Sequence[Any],
    normalized: str,
    timeout: float,
) -> None:
    response = session.post(
        f"{qdrant_url.rstrip('/')}/collections/{collection}/points/payload?wait=true",
        json={
            "payload": {PROJECT_ID_NORMALIZED_FIELD: normalized},
            "points": list(point_ids),
        },
        timeout=timeout,
    )
    response.raise_for_status()


def _ensure_qdrant_project_index(
    session: Any,
    *,
    qdrant_url: str,
    collection: str,
    timeout: float,
) -> None:
    response = session.put(
        f"{qdrant_url.rstrip('/')}/collections/{collection}/index?wait=true",
        json={
            "field_name": PROJECT_ID_NORMALIZED_FIELD,
            "field_schema": "keyword",
        },
        timeout=timeout,
    )
    response.raise_for_status()


def backfill_qdrant_collection(
    session: Any,
    *,
    qdrant_url: str,
    collection: str,
    apply: bool,
    page_size: int,
    batch_size: int,
    timeout: float,
) -> ScopeInventory:
    """Inventory and optionally backfill one Qdrant collection."""
    inventory = ScopeInventory()
    variants: Dict[str, set[str]] = defaultdict(set)
    effective_batch_size = max(1, batch_size)
    offset: Any = None
    while True:
        body: Dict[str, Any] = {
            "limit": max(1, page_size),
            "with_payload": ["project_id", PROJECT_ID_NORMALIZED_FIELD],
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        response = session.post(
            f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll",
            json=body,
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json().get("result") or {}
        points = result.get("points") or []
        pending: Dict[str, list[Any]] = defaultdict(list)
        for point in points:
            payload = point.get("payload") or {}
            raw = normalize_project_id(payload.get("project_id"))
            if raw is None:
                inventory.missing_project_id += 1
                continue
            normalized = project_id_lookup_key(raw)
            if normalized is None:  # defensive; raw is already known non-empty
                inventory.missing_project_id += 1
                continue
            inventory.eligible += 1
            variants[normalized].add(raw)
            if payload.get(PROJECT_ID_NORMALIZED_FIELD) == normalized:
                inventory.already_normalized += 1
            else:
                inventory.needs_update += 1
                pending[normalized].append(point.get("id"))

        if apply:
            for normalized, point_ids in sorted(pending.items()):
                for start in range(0, len(point_ids), effective_batch_size):
                    batch = [
                        point_id
                        for point_id in point_ids[start:start + effective_batch_size]
                        if point_id is not None
                    ]
                    if not batch:
                        continue
                    _set_qdrant_payload(
                        session,
                        qdrant_url=qdrant_url,
                        collection=collection,
                        point_ids=batch,
                        normalized=normalized,
                        timeout=timeout,
                    )
                    inventory.updated += len(batch)
        offset = result.get("next_page_offset")
        if offset is None or not points:
            break

    inventory.collisions = {
        key: sorted(values)
        for key, values in sorted(variants.items())
        if len(values) > 1
    }
    if not apply:
        return inventory

    _ensure_qdrant_project_index(
        session,
        qdrant_url=qdrant_url,
        collection=collection,
        timeout=timeout,
    )
    return inventory


def _graph_config(args: argparse.Namespace) -> Dict[str, Any]:
    if args.provider == "falkordb":
        return {
            "uri": args.falkordb_uri,
            "host": args.falkordb_host,
            "port": args.falkordb_port,
            "user": args.falkordb_user,
            "password": args.falkordb_password,
            "graph": args.database,
            "database": args.database,
        }
    return {
        "uri": args.neo4j_uri,
        "user": args.neo4j_user,
        "password": args.neo4j_password,
        "database": args.database,
    }


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    report: Dict[str, Any] = {"mode": "apply" if args.apply else "dry-run"}
    if not args.skip_graph:
        provider = (
            GraphProvider.FALKORDB
            if args.provider == "falkordb"
            else GraphProvider.NEO4J
        )
        driver = await GraphDriverFactory.create_driver(provider, _graph_config(args))
        try:
            report["graph"] = asdict(
                await backfill_graph(
                    driver,
                    database=args.database,
                    apply=args.apply,
                )
            )
        finally:
            close_result = driver.close()
            if hasattr(close_result, "__await__"):
                await close_result

    if args.qdrant_url:
        session = requests.Session()
        collections = args.collection or _qdrant_collections(
            session, args.qdrant_url, args.timeout
        )
        report["qdrant"] = {
            collection: asdict(
                backfill_qdrant_collection(
                    session,
                    qdrant_url=args.qdrant_url,
                    collection=collection,
                    apply=args.apply,
                    page_size=args.page_size,
                    batch_size=args.batch_size,
                    timeout=args.timeout,
                )
            )
            for collection in collections
        }
    return report


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply updates; default is dry-run")
    parser.add_argument("--skip-graph", action="store_true")
    parser.add_argument(
        "--provider",
        choices=("neo4j", "falkordb"),
        default=os.environ.get("CODE_GRAPH_PROVIDER", "falkordb").strip().lower(),
    )
    parser.add_argument("--database", default=os.environ.get("GRAPH_DB", "cortext"))
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER", ""))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS", ""))
    parser.add_argument("--falkordb-uri", default=os.environ.get("FALKORDB_URI"))
    parser.add_argument("--falkordb-host", default=os.environ.get("FALKORDB_HOST", "localhost"))
    parser.add_argument("--falkordb-port", type=int, default=int(os.environ.get("FALKORDB_PORT", "6379")))
    parser.add_argument("--falkordb-user", default=os.environ.get("FALKORDB_USER", ""))
    parser.add_argument("--falkordb-password", default=os.environ.get("FALKORDB_PASSWORD", ""))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", ""))
    parser.add_argument("--collection", action="append", default=[])
    parser.add_argument("--page-size", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(_run(args))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Sinh fixture parity cho Phase 01 spike (FalkorDB Rust client).

Chạy bộ query đọc đa dạng qua **đúng client falkordb-py** mà
``falkordb_driver.py`` dùng, chuẩn hoá record bằng
``_normalize_falkordb_value``/``_result_key`` của driver → fixture JSON.
Rust spike đọc fixture này và so property-by-property.

Usage:
    .venv/bin/python scripts/rust_parity/gen_falkordb_spike_fixtures.py \
        [--host 127.0.0.1] [--port 6379] [--graph stock] \
        [--out scripts/rust_parity/fixtures/falkordb_spike_stock.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "code-tiny"))
sys.path.insert(0, str(REPO_ROOT))

from falkordb import FalkorDB  # noqa: E402

from tools.graph.driver.falkordb_driver import (  # noqa: E402
    _normalize_falkordb_value,
    _result_key,
)

KEYWORD_SEARCH_QUERY = (
    "MATCH (n) WHERE ($project_id IS NULL OR n.project_id_normalized "
    "STARTS WITH $project_id_normalized) AND any(q IN $qs WHERE "
    "toLower(coalesce(n.name, '')) CONTAINS q OR "
    "toLower(coalesce(n.qualified_name, '')) CONTAINS q) "
    "RETURN n LIMIT $limit"
)


def build_queries() -> list[dict]:
    """10+ query đọc đa dạng theo phase-01.md: keyword search, fulltext,
    id lookup, procedures, label/property filter, aggregate, multi-hop,
    edge, path, scalar battery."""
    return [
        {
            "name": "keyword_search",
            "query": KEYWORD_SEARCH_QUERY,
            "params": {
                "qs": ["stock"],
                "project_id": "stock",
                "project_id_normalized": "stock",
                "limit": 5,
            },
        },
        {
            "name": "legacy_fulltext_queryNodes",
            "query": (
                "CALL db.idx.fulltext.queryNodes('Function', 'handle') "
                "YIELD node, score RETURN node, score LIMIT 5"
            ),
            "params": {},
        },
        {
            "name": "id_lookup",
            "query": "MATCH (n) WHERE id(n) = $id RETURN n",
            "params": {"id": 1},
        },
        {
            "name": "db_labels",
            "query": "CALL db.labels() YIELD label RETURN label AS label",
            "params": {},
        },
        {
            "name": "db_relationship_types",
            "query": (
                "CALL db.relationshipTypes() YIELD relationshipType "
                "RETURN relationshipType AS rel_type"
            ),
            "params": {},
        },
        {
            "name": "label_filter_order_limit",
            "query": (
                "MATCH (n:Function) WHERE n.project_id_normalized STARTS WITH $scope "
                "RETURN n ORDER BY n.name LIMIT $limit"
            ),
            "params": {"scope": "stock", "limit": 3},
        },
        {
            "name": "count_by_label",
            "query": (
                "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt "
                "ORDER BY cnt DESC, label LIMIT 8"
            ),
            "params": {},
        },
        {
            "name": "multi_hop_calls",
            "query": (
                "MATCH (a:Function)-[:CALLS]->(b:Function) "
                "WHERE a.project_id_normalized STARTS WITH $scope "
                "RETURN a, b LIMIT 3"
            ),
            "params": {"scope": "stock"},
        },
        {
            "name": "edge_return",
            "query": (
                "MATCH (p:Project)-[e:CONTAINS]->(m) "
                "RETURN e ORDER BY id(e) LIMIT 3"
            ),
            "params": {},
        },
        {
            "name": "path_return",
            "query": (
                "MATCH p=(a:Project)-[:CONTAINS]->(b) "
                "WHERE b.project_id_normalized STARTS WITH $scope "
                "RETURN p LIMIT 1"
            ),
            "params": {"scope": "stock"},
        },
        {
            "name": "scalar_battery",
            "query": (
                "RETURN true AS t, false AS f, null AS z, 1.5 AS d, 42 AS i, "
                "'text' AS s, [1, 'a', true] AS arr, {x: 1, y: 'z'} AS m"
            ),
            "params": {},
        },
    ]


def run_one(graph, query: str, params: dict) -> dict:
    """Chạy 1 query qua falkordb-py + normalize như driver."""
    try:
        result = graph.query(query, params=params or None, timeout=120000)
    except Exception as exc:  # noqa: BLE001 — parity cả error path
        return {"status": "error", "message_prefix": str(exc)[:60]}
    keys = [_result_key(item) for item in result.header]
    records = [
        {key: _normalize_falkordb_value(row[index]) for index, key in enumerate(keys)}
        for row in result.result_set
    ]
    return {"status": "ok", "keys": keys, "records": records}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--graph", default="stock")
    parser.add_argument(
        "--out",
        default=str(
            REPO_ROOT / "scripts/rust_parity/fixtures/falkordb_spike_stock.json"
        ),
    )
    args = parser.parse_args()

    client = FalkorDB(host=args.host, port=args.port)
    graph = client.select_graph(args.graph)

    queries = build_queries()
    fixture = {"graph": args.graph, "queries": []}
    for spec in queries:
        expected = run_one(graph, spec["query"], spec["params"])
        fixture["queries"].append(
            {
                "name": spec["name"],
                "query": spec["query"],
                "params": spec["params"],
                "expected": expected,
            }
        )
        status = expected["status"]
        size = (
            len(expected.get("records", []))
            if status == "ok"
            else expected.get("message_prefix", "")
        )
        print(f"  {spec['name']}: {status} ({size})")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"fixture → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config

from tools.graph import GraphDriverFactory, GraphProvider

_DEFAULT_ENTRY_NAMES = (
    "main",
    "winmain",
    "wmain",
    "dllmain",
)
_REL_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _normalize_patterns(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(values))


def _normalize_rel_types(raw: str) -> List[str]:
    values = [item.strip().upper() for item in (raw or "").split(",") if item.strip()]
    if not values:
        values = ["CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"]
    invalid = [item for item in values if not _REL_TYPE_RE.match(item)]
    if invalid:
        raise ValueError(f"Invalid relationship types: {', '.join(invalid)}")
    return list(dict.fromkeys(values))


def _build_rel_pattern(rel_types: Sequence[str]) -> str:
    return "|".join(rel_types)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate dead-code candidates from Neo4j graph using entrypoint reachability."
    )
    parser.add_argument("--project-id", required=True, help="Project ID stored in graph nodes")
    parser.add_argument("--root", default=".", help="Project root (for config discovery)")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB", "neo4j"))
    parser.add_argument(
        "--rel-types",
        default="CALLS,POSSIBLE_CALLS,CALLS_FUNCTION_POINTER",
        help="Comma-separated relationship types for reachability traversal.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=20,
        help="Traversal depth for reachability from entrypoints.",
    )
    parser.add_argument("--limit", type=int, default=5000, help="Max dead-code candidates to return.")
    parser.add_argument(
        "--entry-name-patterns",
        default="",
        help="Additional seed patterns by function name (contains, comma-separated).",
    )
    parser.add_argument(
        "--entry-qualified-patterns",
        default="",
        help="Additional seed patterns by qualified_name (contains, comma-separated).",
    )
    parser.add_argument(
        "--entry-file-patterns",
        default="",
        help="Additional seed patterns by file_path (contains, comma-separated).",
    )
    parser.add_argument(
        "--disable-default-entry-names",
        action="store_true",
        help="Disable default entry names (main/winmain/wmain/dllmain).",
    )
    parser.add_argument(
        "--no-exported-seed",
        action="store_true",
        help="Do not include exported=true functions as entry seeds.",
    )
    parser.add_argument(
        "--include-exported-dead",
        action="store_true",
        help="Include exported=true functions in dead-code candidates.",
    )
    parser.add_argument(
        "--only-zero-inbound",
        action="store_true",
        help="Keep only candidates with zero inbound edges from functions.",
    )
    parser.add_argument("--out-json", help="Write full report JSON to file.")
    parser.add_argument("--out-csv", help="Write candidate rows to CSV file.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _seed_predicate() -> str:
    return (
        "("
        "($use_default_entry_names AND toLower(coalesce(seed.name,'')) IN $default_entry_names) "
        "OR ($include_exported_seed AND coalesce(seed.exported,false)=true) "
        "OR ANY(pat IN $entry_name_patterns WHERE toLower(coalesce(seed.name,'')) CONTAINS pat) "
        "OR ANY(pat IN $entry_qualified_patterns WHERE toLower(coalesce(seed.qualified_name,'')) CONTAINS pat) "
        "OR ANY(pat IN $entry_file_patterns WHERE toLower(coalesce(seed.file_path,'')) CONTAINS pat)"
        ")"
    )


def _build_counts_query(rel_pattern: str, max_depth: int) -> str:
    predicate = _seed_predicate()
    return f"""
    MATCH (seed:Function {{project_id:$project_id}})
    WHERE {predicate}
    WITH collect(DISTINCT seed) AS seeds
    CALL (seeds) {{
      UNWIND (CASE WHEN size(seeds)=0 THEN [null] ELSE seeds END) AS s
      OPTIONAL MATCH (s)-[:{rel_pattern}*0..{max_depth}]->(r:Function {{project_id:$project_id}})
      RETURN [rid IN collect(DISTINCT r.id) WHERE rid IS NOT NULL] AS reachable_ids
    }}
    MATCH (f:Function {{project_id:$project_id}})
    WITH seeds, reachable_ids, count(f) AS total_functions
    MATCH (f:Function {{project_id:$project_id}})
    WHERE NOT f.id IN reachable_ids
      AND ($include_exported_dead OR coalesce(f.exported,false)=false)
    RETURN
      total_functions AS total_functions,
      size(seeds) AS seed_count,
      size(reachable_ids) AS reachable_count,
      count(f) AS dead_count
    """


def _build_dead_rows_query(rel_pattern: str, max_depth: int) -> str:
    predicate = _seed_predicate()
    return f"""
    MATCH (seed:Function {{project_id:$project_id}})
    WHERE {predicate}
    WITH collect(DISTINCT seed) AS seeds
    CALL (seeds) {{
      UNWIND (CASE WHEN size(seeds)=0 THEN [null] ELSE seeds END) AS s
      OPTIONAL MATCH (s)-[:{rel_pattern}*0..{max_depth}]->(r:Function {{project_id:$project_id}})
      RETURN [rid IN collect(DISTINCT r.id) WHERE rid IS NOT NULL] AS reachable_ids
    }}
    MATCH (f:Function {{project_id:$project_id}})
    WHERE NOT f.id IN reachable_ids
      AND ($include_exported_dead OR coalesce(f.exported,false)=false)
    OPTIONAL MATCH (:Function {{project_id:$project_id}})-[inRel:{rel_pattern}]->(f)
    WITH f, count(inRel) AS inbound_calls
    WHERE (NOT $only_zero_inbound) OR inbound_calls=0
    RETURN
      f.id AS id,
      coalesce(f.name, '') AS name,
      coalesce(f.qualified_name, '') AS qualified_name,
      coalesce(f.file_path, '') AS file_path,
      coalesce(f.start_line, -1) AS start_line,
      coalesce(f.end_line, -1) AS end_line,
      coalesce(f.kind, '') AS kind,
      coalesce(f.exported, false) AS exported,
      inbound_calls AS inbound_calls
    ORDER BY inbound_calls ASC, file_path ASC, start_line ASC
    LIMIT $limit
    """


def _build_seed_sample_query(rel_pattern: str, max_depth: int) -> str:
    del rel_pattern, max_depth
    predicate = _seed_predicate()
    return f"""
    MATCH (seed:Function {{project_id:$project_id}})
    WHERE {predicate}
    RETURN
      seed.id AS id,
      coalesce(seed.name, '') AS name,
      coalesce(seed.qualified_name, '') AS qualified_name,
      coalesce(seed.file_path, '') AS file_path
    ORDER BY file_path ASC, qualified_name ASC
    LIMIT $seed_sample_limit
    """


async def _run_report(args: argparse.Namespace) -> Dict[str, Any]:
    if not (args.neo4j_uri and args.neo4j_user and args.neo4j_password):
        raise ValueError("Missing Neo4j credentials. Provide --neo4j-uri --neo4j-user --neo4j-password.")
    if args.max_depth < 1 or args.max_depth > 50:
        raise ValueError("--max-depth must be in range [1, 50].")
    if args.limit < 1:
        raise ValueError("--limit must be >= 1.")

    requested_rel_types = _normalize_rel_types(args.rel_types)
    params = {
        "project_id": args.project_id,
        "use_default_entry_names": not args.disable_default_entry_names,
        "default_entry_names": [name.lower() for name in _DEFAULT_ENTRY_NAMES],
        "include_exported_seed": not args.no_exported_seed,
        "entry_name_patterns": _normalize_patterns(args.entry_name_patterns),
        "entry_qualified_patterns": _normalize_patterns(args.entry_qualified_patterns),
        "entry_file_patterns": _normalize_patterns(args.entry_file_patterns),
        "include_exported_dead": bool(args.include_exported_dead),
        "only_zero_inbound": bool(args.only_zero_inbound),
        "limit": int(args.limit),
        "seed_sample_limit": 100,
    }

    driver = await GraphDriverFactory.create_driver(
        GraphProvider.NEO4J,
        {
            "uri": args.neo4j_uri,
            "user": args.neo4j_user,
            "password": args.neo4j_password,
            "database": args.neo4j_db,
        },
    )

    started = time.time()
    try:
        rel_type_records, _, _ = await driver.execute_query(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType",
            database=args.neo4j_db,
        )
        available_rel_types = {str(row.get("relationshipType") or "") for row in rel_type_records}
        rel_types = [name for name in requested_rel_types if name in available_rel_types]
        if not rel_types:
            raise ValueError(
                "None of requested --rel-types exist in DB. requested=%s available_sample=%s"
                % (
                    ",".join(requested_rel_types),
                    ",".join(sorted(x for x in available_rel_types if x)[:20]),
                )
            )
        if args.verbose and len(rel_types) != len(requested_rel_types):
            missing = [name for name in requested_rel_types if name not in available_rel_types]
            print("[dead-code] skip missing rel_types: %s" % ",".join(missing))

        rel_pattern = _build_rel_pattern(rel_types)
        counts_query = _build_counts_query(rel_pattern, int(args.max_depth))
        rows_query = _build_dead_rows_query(rel_pattern, int(args.max_depth))
        seeds_query = _build_seed_sample_query(rel_pattern, int(args.max_depth))

        counts_records, _, _ = await driver.execute_query(counts_query, params, database=args.neo4j_db)
        dead_records, _, _ = await driver.execute_query(rows_query, params, database=args.neo4j_db)
        seed_records, _, _ = await driver.execute_query(seeds_query, params, database=args.neo4j_db)

        count_row = counts_records[0] if counts_records else {}
        dead_rows: List[Dict[str, Any]] = [dict(item) for item in dead_records]
        seeds_sample: List[Dict[str, Any]] = [dict(item) for item in seed_records]

        by_file = Counter(row.get("file_path") or "" for row in dead_rows)
        top_files = [
            {"file_path": path, "dead_count": count}
            for path, count in by_file.most_common(30)
            if path
        ]

        report: Dict[str, Any] = {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": round(time.time() - started, 4),
            "project_id": args.project_id,
            "neo4j_db": args.neo4j_db,
            "config": {
                "rel_types": rel_types,
                "max_depth": int(args.max_depth),
                "limit": int(args.limit),
                "use_default_entry_names": not args.disable_default_entry_names,
                "include_exported_seed": not args.no_exported_seed,
                "include_exported_dead": bool(args.include_exported_dead),
                "only_zero_inbound": bool(args.only_zero_inbound),
                "entry_name_patterns": params["entry_name_patterns"],
                "entry_qualified_patterns": params["entry_qualified_patterns"],
                "entry_file_patterns": params["entry_file_patterns"],
            },
            "summary": {
                "total_functions": int(count_row.get("total_functions") or 0),
                "seed_count": int(count_row.get("seed_count") or 0),
                "reachable_count": int(count_row.get("reachable_count") or 0),
                "dead_count": int(count_row.get("dead_count") or 0),
                "returned_candidates": len(dead_rows),
            },
            "seed_sample": seeds_sample,
            "top_files": top_files,
            "dead_functions": dead_rows,
        }
        return report
    finally:
        close_result = driver.close()
        if hasattr(close_result, "__await__"):
            await close_result


def _write_json(path: str, payload: Dict[str, Any]) -> str:
    output_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    os.replace(temp_path, output_path)
    return output_path


def _write_csv(path: str, rows: Sequence[Dict[str, Any]]) -> str:
    output_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id",
                "name",
                "qualified_name",
                "file_path",
                "start_line",
                "end_line",
                "kind",
                "exported",
                "inbound_calls",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row.get("id", ""),
                    "name": row.get("name", ""),
                    "qualified_name": row.get("qualified_name", ""),
                    "file_path": row.get("file_path", ""),
                    "start_line": row.get("start_line", ""),
                    "end_line": row.get("end_line", ""),
                    "kind": row.get("kind", ""),
                    "exported": row.get("exported", False),
                    "inbound_calls": row.get("inbound_calls", 0),
                }
            )
    return output_path


async def _async_main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = await _run_report(args)
    except Exception as exc:
        print(f"[dead-code][error] {exc}", file=sys.stderr)
        return 2

    summary = report.get("summary", {})
    print(
        "[dead-code] project=%s total=%s seeds=%s reachable=%s dead=%s returned=%s"
        % (
            report.get("project_id"),
            summary.get("total_functions", 0),
            summary.get("seed_count", 0),
            summary.get("reachable_count", 0),
            summary.get("dead_count", 0),
            summary.get("returned_candidates", 0),
        )
    )
    if report.get("top_files"):
        print("[dead-code] top files:")
        for item in report["top_files"][:10]:
            print("  - %s: %s" % (item.get("file_path"), item.get("dead_count")))

    if args.out_json:
        out_json = _write_json(args.out_json, report)
        print(f"[dead-code] json: {out_json}")
    if args.out_csv:
        out_csv = _write_csv(args.out_csv, report.get("dead_functions", []))
        print(f"[dead-code] csv: {out_csv}")
    if not args.out_json and not args.out_csv:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--root", default=".")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    _config_path = _pre_args.config or os.path.join(
        _pre_args.root, ".cortext-harness", "config", "dev.json"
    )
    load_harness_config(_config_path)
    raise SystemExit(main())

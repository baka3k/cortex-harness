#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config

from tools.common.analyzer_cache import safe_cache_root
from tools.common.git_diff import write_manifest_paths
from tools.sync.owner_manifest import SUPPORTED_PARSERS, build_owner_maps


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._")
    return cleaned or "project"


# Maps external/variant parser names to canonical SUPPORTED_PARSERS names.
_PARSER_ALIASES: Dict[str, str] = {
    "android_java": "android",
    "android_kotlin": "android",
    "android_mixed": "android",
    "ts_backend": "ts",
    "_refactor_ts": "ts",
}


def _parse_parsers(raw_value: str) -> List[str]:
    text = (raw_value or "auto").strip().lower()
    if text == "auto":
        return sorted(SUPPORTED_PARSERS)
    values = [item.strip() for item in text.split(",") if item.strip()]
    # Normalize aliases to canonical parser names before validation.
    normalized = [_PARSER_ALIASES.get(v, v) for v in values]
    unsupported = sorted(set(normalized) - SUPPORTED_PARSERS)
    if unsupported:
        raise ValueError(f"Unsupported parser(s): {', '.join(unsupported)}")
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(normalized))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build parser-owner manifests for mixed-language ingest")
    parser.add_argument("--root", required=True, help="Project root")
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"), help="Project id for output scope")
    parser.add_argument("--parsers", default="auto", help="auto or comma-separated parser list")
    parser.add_argument(
        "--sql-owner-mode",
        choices=["heuristic", "prefer-sql", "prefer-plsql"],
        default="heuristic",
        help="Ownership strategy for SQL-like files (.sql/.ddl/.dml/.psql).",
    )
    parser.add_argument(
        "--vb-owner-mode",
        choices=["heuristic", "prefer-vb6", "prefer-vba"],
        default="heuristic",
        help="Ownership strategy for VB6/VBA overlap files (.bas/.cls/.frm).",
    )
    parser.add_argument("--output-dir", help="Directory for generated manifests")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--summary-path", help="Optional JSON summary path")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _resolve_output_dir(args: argparse.Namespace, project_id: str) -> str:
    if args.output_dir:
        return os.path.abspath(args.output_dir)
    cache_root = safe_cache_root(args.cache_dir, "owner_manifests", project_root=args.root)
    return os.path.join(cache_root, _safe_segment(project_id))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"Root not found: {root}", file=sys.stderr)
        return 2
    try:
        parsers = _parse_parsers(args.parsers)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    project_id = args.project_id or os.path.basename(root)
    output_dir = _resolve_output_dir(args, project_id)
    os.makedirs(output_dir, exist_ok=True)

    result = build_owner_maps(root=root, parsers=parsers, sql_owner_mode=args.sql_owner_mode, vb_owner_mode=args.vb_owner_mode)

    manifests: Dict[str, Dict[str, str]] = {}
    for parser in parsers:
        changed = result.owned_by_parser.get(parser, set())
        deleted = result.deleted_by_parser.get(parser, set())
        changed_path = os.path.join(output_dir, f"{parser}_changed_owner.json")
        deleted_path = os.path.join(output_dir, f"{parser}_deleted_owner.json")
        write_manifest_paths(changed_path, changed)
        write_manifest_paths(deleted_path, deleted)
        manifests[parser] = {
            "changed_manifest": changed_path,
            "deleted_manifest": deleted_path,
            "changed_count": len(changed),
            "deleted_count": len(deleted),
        }
        if args.verbose:
            print(
                "[owner] parser=%s changed=%d deleted=%d"
                % (parser, len(changed), len(deleted))
            )

    sql_plsql_overlap = result.owned_by_parser.get("sql", set()) & result.owned_by_parser.get("plsql", set())
    summary = {
        "root": root,
        "project_id": project_id,
        "sql_owner_mode": args.sql_owner_mode,
        "output_dir": output_dir,
        "parsers": parsers,
        "manifests": manifests,
        "sql_decisions_count": len(result.sql_decisions),
        "sql_to_plsql_count": sum(1 for item in result.sql_decisions.values() if item.owner == "plsql"),
        "unassigned_count": len(result.unassigned),
        "overlap_sql_plsql_count": len(sql_plsql_overlap),
        "sample_sql_to_plsql": sorted(
            [path for path, decision in result.sql_decisions.items() if decision.owner == "plsql"]
        )[:50],
    }

    if args.summary_path:
        summary_path = os.path.abspath(args.summary_path)
    else:
        summary_path = os.path.join(output_dir, "owner_manifest_summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=True, indent=2)
        handle.write("\n")

    print(
        "[owner][done] project=%s parsers=%d output=%s summary=%s"
        % (project_id, len(parsers), output_dir, summary_path)
    )
    return 0


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

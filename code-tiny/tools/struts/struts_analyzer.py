from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args
from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.struts.pipeline import run_struts_analysis


_GRAPH_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


async def _close_driver(driver) -> None:
    result = driver.close()
    if hasattr(result, "__await__"):
        await result


async def write_graph(args: argparse.Namespace, result) -> Dict[str, int]:
    """Replace the project's Struts overlay facts using the shared graph driver."""

    driver = await create_graph_driver_from_args(args)
    if driver is None:
        return {}
    try:
        writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )
        records, _, _ = await driver.execute_query(
            "MATCH (n:StrutsFact {project_id: $project_id}) DETACH DELETE n RETURN count(n) AS count",
            {"project_id": result.project_id},
            args.neo4j_db,
        )
        counts: Dict[str, int] = {
            "deleted_nodes": int((records or [{}])[0].get("count", 0)),
        }

        facts_by_kind: Dict[str, List[dict]] = defaultdict(list)
        for fact in result.semantic_facts:
            if not _GRAPH_IDENTIFIER_RE.fullmatch(fact.kind):
                raise ValueError(f"Unsafe Struts graph label: {fact.kind!r}")
            facts_by_kind[fact.kind].append(fact.to_graph_node())
        for kind, rows in sorted(facts_by_kind.items()):
            counts[kind] = await writer.write_nodes_batch(
                f"struts:{kind}",
                (
                    "UNWIND $rows AS row "
                    f"MERGE (n:StrutsFact:{kind} {{id: row.id}}) "
                    "SET n += row"
                ),
                rows,
            )

        relations: List[dict] = []
        for relationship in result.relationships:
            if not _GRAPH_IDENTIFIER_RE.fullmatch(relationship.type):
                raise ValueError(f"Unsafe Struts relationship type: {relationship.type!r}")
            row = relationship.to_graph_row()
            properties = dict(row.pop("properties", {}))
            properties.update(
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"from_id", "to_id", "from_label", "to_label", "type"}
                }
            )
            relations.append(
                {
                    "source_id": row["from_id"],
                    "target_id": row["to_id"],
                    "source_label": row["from_label"],
                    "target_label": row["to_label"],
                    "rel_type": row["type"],
                    "properties": properties,
                }
            )
        counts["relationships"] = await writer.write_relations_typed(relations)
        return counts
    finally:
        await _close_driver(driver)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apache Struts 2 semantic analyzer", allow_abbrev=False)
    parser.add_argument("--root", required=True, help="Project root to analyze")
    parser.add_argument("--project-id", default="struts-project")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--selected-path", action="append", default=[], help="Limit scanning to a relative path")
    parser.add_argument("--output", help="Write the semantic graph JSON to this file")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    parser.add_argument("--commit-sha-before", default="")
    parser.add_argument("--commit-sha-after", default="")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--enable-message-scan", action="store_true")
    parser.add_argument("--disable-message-scan", action="store_true")
    parser.add_argument("--message-output-dir")
    parser.add_argument("--message-qdrant-collection")
    parser.add_argument("--neo4j-uri")
    parser.add_argument("--neo4j-user")
    parser.add_argument("--neo4j-password")
    parser.add_argument("--neo4j-db")
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--verbose", action="store_true")
    add_graph_provider_args(parser)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = run_struts_analysis(
        root=args.root,
        project_id=args.project_id,
        project_name=args.project_name,
        selected_paths=args.selected_path,
    )
    payload = result.to_dict()
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
    elif not (args.commit_sha_before or args.commit_sha_after or args.incremental):
        print(serialized)
    if any(item.severity == "error" for item in result.diagnostics):
        return 1
    should_write_graph = bool(
        args.commit_sha_before
        or args.commit_sha_after
        or args.incremental
        or args.neo4j_uri
        or args.falkordb_uri
        or os.environ.get("FALKORDB_URI")
        or os.environ.get("NEO4J_URI")
    )
    if not should_write_graph:
        return 0
    try:
        counts = asyncio.run(write_graph(args, result))
    except Exception as exc:  # noqa: BLE001
        print(f"[struts] ERROR: graph write failed: {exc}", file=sys.stderr)
        return 3
    print(
        "[SCAN_RESULT] parser=struts facts=%d relationships=%d diagnostics=%d graph=%d"
        % (
            len(result.semantic_facts),
            len(result.relationships),
            len(result.diagnostics),
            sum(counts.values()),
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

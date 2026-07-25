#!/usr/bin/env python3
"""CLI entrypoint for the non-vector project-topology overlay."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.git_diff import load_manifest_paths
from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    prepare_graph_args,
)
from tools.graph.writer.project_topology_writer import ProjectTopologyWriter
from tools.project_topology.pipeline import analyze_project
from tools.project_topology.registry import descriptor_candidates


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Static project topology and descriptor analyzer",
        allow_abbrev=False,
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--commit-sha-before", default="")
    parser.add_argument("--commit-sha-after", default="")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest", default="")
    parser.add_argument("--deleted-files-manifest", default="")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--disable-message-scan", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--neo4j-batch-size", type=int, default=500)
    add_graph_provider_args(parser)
    return parser.parse_args(argv)


def _manifest_paths(path: str, root: str) -> tuple[str, ...]:
    if not path:
        return ()
    return descriptor_candidates(load_manifest_paths(path, root))


async def _write_graph(args: argparse.Namespace, result) -> dict[str, int]:
    if not prepare_graph_args(args):
        return {}
    driver = await create_graph_driver_from_args(args)
    if driver is None:
        return {}
    try:
        await driver.create_indexes(
            [
                {"label": "ProjectModule", "property": "id"},
                {"label": "ProjectModule", "property": "project_id_normalized"},
                {"label": "BuildDescriptor", "property": "id"},
                {"label": "Dependency", "property": "id"},
                {"label": "FrameworkInstance", "property": "id"},
                {"label": "GrpcEndpoint", "property": "id"},
            ],
            database=args.neo4j_db,
        )
        writer = ProjectTopologyWriter(
            driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
        )
        # Any descriptor change can alter parent/child resolution. Rebuilding
        # only topology-owned state is deterministic and cannot delete source
        # or framework facts owned by another analyzer.
        await writer.cleanup_project(result.project_id)
        return await writer.write(result)
    finally:
        closed = driver.close()
        if hasattr(closed, "__await__"):
            await closed


def _summary(result, *, changed: Sequence[str], deleted: Sequence[str]) -> dict:
    return {
        "project_id": result.project_id,
        "root": result.root,
        "changed_descriptors": list(changed),
        "deleted_descriptors": list(deleted),
        "modules": len(result.modules),
        "descriptors": len(result.descriptors),
        "dependencies": len(result.dependencies),
        "endpoints": len(result.endpoints),
        "frameworks": len(result.frameworks),
        "diagnostics": [item.to_dict() for item in result.diagnostics],
        "coverage": {
            "mode": "static_allowlist",
            "build_execution": False,
            "secret_values": False,
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = str(Path(args.root).resolve())
    if not os.path.isdir(root):
        print(f"Root not found: {root}", file=sys.stderr)
        return 2
    project_id = str(args.project_id or os.path.basename(root)).strip()
    changed = _manifest_paths(args.changed_files_manifest, root)
    deleted = _manifest_paths(args.deleted_files_manifest, root)
    # Full recomputation is deliberate for both full and affected incremental
    # runs so deleted parent descriptors and module moves cannot leave stale
    # topology. The orchestrator skips this process entirely on no-change runs.
    result = analyze_project(root, project_id)
    summary = _summary(result, changed=changed, deleted=deleted)
    if not args.dry_run:
        summary["graph_writes"] = asyncio.run(_write_graph(args, result))
    payload = json.dumps(summary, ensure_ascii=True, sort_keys=True)
    print(f"[project_topology] {payload}")
    if args.summary_output:
        output = Path(args.summary_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

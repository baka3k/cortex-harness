#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files
from tools.common.primary_vector_sync import documents_from_rows, sync_vector_documents, vector_configured
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args
from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.jp1.models import Jp1AnalysisResult
from tools.jp1.pipeline import run_jp1_analysis


def build_graph_rows(result: Jp1AnalysisResult, *, project_name: str, repo: str) -> dict[str, list[dict[str, Any]]]:
    common = {"project_id": result.project_id, "project_name": project_name, "language": "jp1", "repo": repo, "build_system": "jp1"}
    units: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for file in result.files:
        for unit in file.units:
            units.append({"id": unit.unit_id, "name": unit.name, "file_path": unit.file_path, "type": unit.unit_type, "comment": unit.comment, "exec_target": unit.exec_target, "start_line": unit.start_line, "end_line": unit.end_line, **common})
        for relation in file.relations:
            relations.append({"source_id": relation.source_id, "source_label": relation.source_label, "target_id": relation.target_id, "target_label": relation.target_label, "rel_type": relation.rel_type, "properties": {"line": relation.line, "raw_target": relation.raw_target, "resolved": relation.resolved}})
    return {"units": units, "relations": relations}


async def _write_graph(args: argparse.Namespace, result: Jp1AnalysisResult) -> dict[str, int]:
    if not prepare_graph_args(args):
        return {}
    driver = await create_graph_driver_from_args(args)
    if driver is None:
        raise RuntimeError("Graph provider was requested but no driver was created")
    try:
        writer = LanguageCodeWriter(driver, database=args.neo4j_db, batch_size=args.neo4j_batch_size, verbose=args.verbose)
        rows = build_graph_rows(result, project_name=args.project_name or result.project_id, repo=args.repo or result.project_id)
        cleanup_paths = sorted(set(result.changed_paths) | set(result.deleted_paths))
        if args.incremental and cleanup_paths:
            await cleanup_neo4j_for_files(
                driver=driver,
                database=args.neo4j_db,
                project_id=result.project_id,
                file_paths=cleanup_paths,
                verbose=args.verbose,
            )
        query = "UNWIND $rows AS row MERGE (n:Jp1Unit {id: row.id}) SET n += row"
        required_relations = [
            row for row in rows["relations"] if row.get("properties", {}).get("resolved") is not False
        ]
        unresolved_count = len(rows["relations"]) - len(required_relations)
        counts = {
            "Jp1Unit": await writer.write_nodes_batch("jp1:units", query, rows["units"]),
            "relations": await writer.write_relations_typed(
                required_relations, project_id=result.project_id
            ),
            "unresolved_relations": unresolved_count,
        }
        if args.verbose and unresolved_count:
            print(f"[graph] optional unresolved JP1 relations skipped={unresolved_count}")
        return counts
    finally:
        close = getattr(driver, "close", None)
        if close:
            result_close = close()
            if hasattr(result_close, "__await__"):
                await result_close


def _sync_vectors(args: argparse.Namespace, result: Jp1AnalysisResult) -> int:
    if not vector_configured(args.qdrant_url):
        return 0
    repo = args.repo or result.project_id
    rows = build_graph_rows(result, project_name=args.project_name or result.project_id, repo=repo)
    documents = documents_from_rows(rows, parser="jp1", root_scope=repo, max_chars=args.max_embed_chars)
    return sync_vector_documents(
        documents,
        url=args.qdrant_url,
        collection=args.qdrant_collection,
        model_name=args.embed_model or "jinaai/jina-embeddings-v3",
        device=args.device,
        embed_batch_size=args.batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        parser="jp1",
        project_id=result.project_id,
        root_scope=repo,
        cleanup_paths=sorted(set(result.changed_paths) | set(result.deleted_paths)),
        full_replace=not args.incremental,
        timeout=args.qdrant_timeout,
        retries=args.qdrant_retries,
        retry_sleep=args.qdrant_retry_sleep,
        verbose=args.verbose,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static JP1/AJS jobnet analyzer", allow_abbrev=False)
    parser.add_argument("path", nargs="?")
    parser.add_argument("--root")
    parser.add_argument("--project-id", "--project_id", dest="project_id")
    parser.add_argument("--project-name", "--project_name", dest="project_name")
    parser.add_argument("--repo")
    parser.add_argument("--build-system", "--build_system", dest="build_system", default="jp1")
    parser.add_argument("--commit-sha-before", default="")
    parser.add_argument("--commit-sha-after", default="")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--cache-dir")
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", "-o")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--neo4j-uri")
    parser.add_argument("--neo4j-user")
    parser.add_argument("--neo4j-password")
    parser.add_argument("--neo4j-db")
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-collection", default="jp1_units")
    parser.add_argument("--qdrant-url")
    parser.add_argument("--embed-model", default="")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-embed-chars", type=int, default=4000)
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.add_argument("--enable-message-scan", action="store_true")
    parser.add_argument("--disable-message-scan", action="store_true")
    parser.add_argument("--message-output-dir")
    parser.add_argument("--message-qdrant-collection", default="")
    add_graph_provider_args(parser)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    root = os.path.realpath(args.root or args.path or "")
    if not os.path.isdir(root):
        print("A valid --root directory is required", file=sys.stderr)
        return 2
    changed = load_manifest_paths(args.changed_files_manifest, root) if args.incremental and args.changed_files_manifest else None
    deleted = load_manifest_paths(args.deleted_files_manifest, root) if args.incremental and args.deleted_files_manifest else ()
    result = run_jp1_analysis(root, project_id=args.project_id or os.path.basename(root), changed_paths=changed, deleted_paths=deleted)
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    if not args.dry_run:
        await _write_graph(args, result)
        vector_count = _sync_vectors(args, result)
    else:
        vector_count = 0
    if args.dry_run or args.pretty:
        print(payload)
    vector_status = "success" if vector_configured(args.qdrant_url) and not args.dry_run else "disabled"
    print(f"[SCAN_RESULT] parser=jp1 files={len(result.files)} functions=0 vectors={vector_count} vector_status={vector_status}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

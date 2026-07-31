#!/usr/bin/env python3
"""CLI entry point for the pseudo-INI (`KEY:VALUE`) batch-config analyzer.

Follows the argparse/graph-persistence conventions of
`tools/shell/shell_analyzer.py` / `tools/jp1/jp1_analyzer.py`. Persists config
files as `File` nodes, entries as generic `ConfigEntry` symbol rows, and
`DEFINES_CONFIG` facts as generic `RelationEdge` rows (no new writer methods
required).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Optional

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

try:
    from tools.common.git_diff import load_manifest_paths
except Exception:  # pragma: no cover
    load_manifest_paths = None

from tools.batchconfig.ini_parser import build_relations, parse_ini_file
from tools.batchconfig.models import SUPPORTED_EXTENSIONS
from tools.common.incremental_cleanup import cleanup_neo4j_for_files
from tools.common.primary_vector_sync import (
    documents_from_rows,
    sync_vector_documents,
    vector_configured,
)
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args
from tools.graph.writer.language_writer import LanguageCodeWriter


_SCAN_SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vs", ".vscode", "node_modules",
    "__pycache__", ".cache", "build", "dist", "out", "target", "tmp", "temp",
}


def _scan_ini_files(root: str) -> List[str]:
    matches: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
        for filename in filenames:
            if filename.lower().endswith(SUPPORTED_EXTENSIONS):
                matches.append(os.path.join(dirpath, filename))
    return matches


def analyze_root(root: str, *, selected_rel_paths: Optional[List[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    files_rows: List[Dict[str, Any]] = []
    entry_rows: List[Dict[str, Any]] = []
    relation_rows: List[Dict[str, Any]] = []

    for path in _scan_ini_files(root):
        rel_path = os.path.relpath(path, root)
        if selected_rel_paths is not None and rel_path not in selected_rel_paths:
            continue
        config = parse_ini_file(path, root)
        files_rows.append(
            {
                "file_path": config.file_path,
                "code": config.code,
                "comment": config.comment,
                "start_line": config.start_line,
                "end_line": config.end_line,
                "parse_meta": {
                    "source_encoding": config.source_encoding,
                    "source_encoding_lossy": config.source_encoding_lossy,
                    "entry_count": len(config.entries),
                },
            }
        )
        for entry in config.entries:
            entry_rows.append(
                {
                    "symbol_id": f"config_entry::{config.file_path}:{entry.key}",
                    "qualified_name": f"{config.file_path}::{entry.key}",
                    "name": entry.key,
                    "kind": "config_entry",
                    "scope_name": None,
                    "file_path": config.file_path,
                    "start_line": entry.line,
                    "end_line": entry.line,
                    "arity": 0,
                    "code": f"{entry.key}:{entry.value}",
                    "comment": "",
                }
            )
        for relation in build_relations(config):
            relation_rows.append(
                {
                    "source_id": relation.source_id,
                    "source_label": relation.source_label,
                    "target_id": relation.target_id,
                    "target_label": relation.target_label,
                    "rel_type": relation.rel_type,
                    "properties": relation.properties,
                }
            )

    return {"files": files_rows, "functions": entry_rows, "relations": relation_rows}


async def _write_graph(args: argparse.Namespace, rows: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    if not prepare_graph_args(args):
        if args.verbose:
            print("[graph] disabled; missing graph connection settings")
        return {}

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
        cleanup_targets = sorted(
            set(getattr(args, "_selected_rel_paths", []) or []) | set(getattr(args, "_deleted_rel_paths", []) or [])
        )
        if args.incremental and cleanup_targets:
            await cleanup_neo4j_for_files(
                driver=driver,
                database=args.neo4j_db,
                project_id=args.project_id,
                file_paths=cleanup_targets,
                verbose=args.verbose,
            )
        counts: Dict[str, int] = {}
        if rows["files"]:
            counts["files"] = await writer.write_files(rows["files"])
        if rows["functions"]:
            counts["functions"] = await writer.write_functions(rows["functions"])
        if rows["relations"]:
            counts["relations"] = await writer.write_relations(rows["relations"])
        return counts
    finally:
        close = getattr(driver, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result


def _sync_vectors(args: argparse.Namespace, rows: Dict[str, List[Dict[str, Any]]]) -> int:
    if not vector_configured(args.qdrant_url):
        return 0
    documents = documents_from_rows(rows, parser="batchconfig", root_scope=args.repo or "", max_chars=args.max_embed_chars)
    cleanup_targets = sorted(
        set(getattr(args, "_selected_rel_paths", []) or [])
        | set(getattr(args, "_deleted_rel_paths", []) or [])
    )
    return sync_vector_documents(
        documents,
        url=args.qdrant_url,
        collection=args.qdrant_collection,
        model_name=args.embed_model or "jinaai/jina-embeddings-v3",
        device=args.device,
        embed_batch_size=args.batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        parser="batchconfig",
        project_id=args.project_id,
        root_scope=args.repo or "",
        cleanup_paths=cleanup_targets,
        full_replace=not args.incremental,
        timeout=args.qdrant_timeout,
        retries=args.qdrant_retries,
        retry_sleep=args.qdrant_retry_sleep,
        verbose=args.verbose,
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pseudo-INI (KEY:VALUE) batch-config analyzer")
    parser.add_argument("--root", required=True, help="Root directory to scan for .ini files")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    add_graph_provider_args(parser)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "batchconfig_entries"))
    parser.add_argument("--embed-model", default=os.environ.get("CODE_EMBEDDING_MODEL", ""))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", "4")))
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", "4000")))
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--incremental", action="store_true", help="Enable incremental scan mode")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    args.root = os.path.abspath(args.root)

    selected_rel_paths: Optional[List[str]] = None
    deleted_rel_paths: List[str] = []
    if args.incremental and args.changed_files_manifest:
        if load_manifest_paths is None:
            print("Incremental mode requires tools.common.git_diff", file=sys.stderr)
            return 2
        selected_rel_paths = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        selected_rel_paths = [item for item in selected_rel_paths if item.endswith(SUPPORTED_EXTENSIONS)]
    if args.incremental and args.deleted_files_manifest:
        if load_manifest_paths is None:
            print("Incremental mode requires tools.common.git_diff", file=sys.stderr)
            return 2
        deleted_rel_paths = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
        deleted_rel_paths = [item for item in deleted_rel_paths if item.endswith(SUPPORTED_EXTENSIONS)]
    args._selected_rel_paths = selected_rel_paths or []
    args._deleted_rel_paths = deleted_rel_paths

    rows = analyze_root(args.root, selected_rel_paths=selected_rel_paths)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "file_count": len(rows["files"]),
                    "entry_count": len(rows["functions"]),
                    "relation_count": len(rows["relations"]),
                },
                indent=2,
            )
        )
        return 0

    try:
        counts = await _write_graph(args, rows)
    except Exception as exc:  # pragma: no cover
        print(f"Batchconfig graph persistence failed: {exc}", file=sys.stderr)
        return 3
    if counts and args.verbose:
        print(f"[graph] written {counts}")

    try:
        vector_count = _sync_vectors(args, rows)
    except Exception as exc:  # pragma: no cover
        print(f"Batchconfig vector persistence failed: {exc}", file=sys.stderr)
        return 4

    print(
        f"[SCAN_RESULT] parser=batchconfig files={len(rows['files'])} functions={len(rows['functions'])} "
        f"relations={len(rows['relations'])} vectors={vector_count} "
        f"vector_status={'success' if vector_configured(args.qdrant_url) else 'disabled'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

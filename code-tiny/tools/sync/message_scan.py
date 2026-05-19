#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config
from tools.common.git_diff import load_manifest_paths
from tools.common.analyzer_cache import safe_cache_root
from tools.common.message_scan import (
    DEFAULT_MESSAGE_VECTOR_SIZE,
    SUPPORTED_PARSERS,
    run_message_scan_pipeline,
)
from tools.graph import GraphDriverFactory, GraphProvider


def _safe_segment(value: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._")
    return cleaned or "project"


def _parse_parsers(raw_value: str) -> List[str]:
    text = (raw_value or "auto").strip().lower()
    if text == "auto":
        return sorted(SUPPORTED_PARSERS)
    values = [item.strip() for item in text.split(",") if item.strip()]
    unsupported = sorted(set(values) - SUPPORTED_PARSERS)
    if unsupported:
        raise ValueError(f"Unsupported parser(s): {', '.join(unsupported)}")
    return list(dict.fromkeys(values))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone multi-language message scanner")
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--project-id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--parsers", default="auto", help="auto or comma-separated parser list")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="Ignore local cache paths for this run and use an isolated temporary cache scope.",
    )
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-vector-size", type=int, default=DEFAULT_MESSAGE_VECTOR_SIZE)
    parser.add_argument("--qdrant-batch-size", type=int, default=256)
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2
    parsers = _parse_parsers(args.parsers)
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    repo = args.repo or os.path.abspath(args.root)
    default_message_collection = f"{_safe_segment(project_id)}_mess"
    qdrant_collection = args.message_qdrant_collection or default_message_collection

    changed_files: List[str] = []
    deleted_files: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_files = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        if args.deleted_files_manifest:
            deleted_files = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
        if args.verbose:
            print(
                "[message][diff] incremental changed=%d deleted=%d"
                % (len(changed_files), len(deleted_files))
            )

    effective_cache_dir = args.cache_dir
    if args.ignore_cache:
        run_cache_root = safe_cache_root(args.cache_dir, "message_scan", project_root=args.root)
        effective_cache_dir = os.path.join(
            run_cache_root,
            "ignore_runs",
            f"run_{int(time.time() * 1000)}",
        )
        os.makedirs(effective_cache_dir, exist_ok=True)
        if args.verbose:
            print(
                "[cache] ignore-cache enabled; using isolated cache dir: %s"
                % effective_cache_dir
            )

    driver = None
    if args.neo4j_uri and args.neo4j_user and args.neo4j_password:
        driver = await GraphDriverFactory.create_driver(
            GraphProvider.NEO4J,
            {
                "uri": args.neo4j_uri,
                "user": args.neo4j_user,
                "password": args.neo4j_password,
                "database": args.neo4j_db,
            },
        )

    exit_code = 0
    try:
        total_messages = 0
        for idx, parser in enumerate(parsers):
            summary = await run_message_scan_pipeline(
                root=args.root,
                parser=parser,
                project_id=project_id,
                project_name=project_name,
                language=parser,
                repo=repo,
                build_system=args.build_system or "",
                incremental=bool(args.incremental),
                changed_files=changed_files,
                deleted_files=deleted_files,
                driver=driver,
                neo4j_database=args.neo4j_db,
                qdrant_url=args.qdrant_url,
                qdrant_collection=qdrant_collection,
                output_dir=args.message_output_dir,
                cache_dir=effective_cache_dir,
                commit_sha_before=args.commit_sha_before or "",
                commit_sha_after=args.commit_sha_after or "",
                qdrant_vector_size=args.qdrant_vector_size,
                qdrant_batch_size=args.qdrant_batch_size,
                qdrant_timeout=args.qdrant_timeout,
                qdrant_retries=args.qdrant_retries,
                qdrant_retry_sleep=args.qdrant_retry_sleep,
                replace_existing_on_full=(idx == 0),
                verbose=args.verbose,
            )
            total_messages += int(summary.get("message_count", 0))
            print(
                "[message][summary] parser=%s messages=%s neo4j=%s qdrant=%s artifact=%s"
                % (
                    parser,
                    summary.get("message_count", 0),
                    summary.get("neo4j_upserted", 0),
                    summary.get("qdrant_upserted", 0),
                    summary.get("artifact_path", ""),
                )
            )
        print(
            "[message][done] parsers=%d total_messages=%d qdrant_collection=%s"
            % (len(parsers), total_messages, qdrant_collection)
        )
    except Exception as exc:
        exit_code = 1
        print(f"[message][error] {exc}", file=sys.stderr)
    finally:
        if driver:
            close_result = driver.close()
            if hasattr(close_result, "__await__"):
                await close_result
    return exit_code


if __name__ == "__main__":
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--root", default=".")
    _pre.add_argument("--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    _config_path = _pre_args.config or os.path.join(
        _pre_args.root, ".cortext-harness", "config", "dev.json"
    )
    load_harness_config(_config_path)
    raise SystemExit(asyncio.run(main()))

#!/usr/bin/env python3
"""CortexHarness CLI entry point for staged COBOL semantic analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.cobol.parser_runtime import CobolRuntimeError, preflight  # noqa: E402
from tools.cobol.pipeline import analyze_project, select_incremental_result, write_graph_facts  # noqa: E402
from tools.cobol.qdrant import sync_qdrant  # noqa: E402
from tools.cobol.resolver import DependencyIndex  # noqa: E402
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


def _manifest_paths(path: str | None, root: Path) -> list[str]:
    if not path:
        return []
    manifest = Path(path)
    if not manifest.is_file():
        raise ValueError(f"manifest not found: {manifest}")
    text = manifest.read_text(encoding="utf-8")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(value, Mapping):
        value = value.get("files", value.get("paths", []))
    if not isinstance(value, list):
        raise ValueError(f"manifest must contain a list of paths: {manifest}")
    normalized: list[str] = []
    for raw in value:
        candidate = Path(str(raw))
        absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            relative = absolute.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"manifest path is outside the project root: {raw}") from exc
        normalized.append(relative.as_posix())
    return sorted(set(normalized))


async def _close_driver(driver) -> None:
    result = driver.close()
    if hasattr(result, "__await__"):
        await result


async def write_graph(args: argparse.Namespace, result, cleanup_paths: Sequence[str]) -> dict[str, int]:
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
        counts = await write_graph_facts(
            writer,
            result,
            project_name=args.project_name or result.project_id,
            repo=args.repo or Path(args.root).resolve().name,
            build_system=args.build_system or "cobol",
        )
        if cleanup_paths:
            keep_ids = [node.id for node in result.nodes]
            query = (
                "MATCH (n {project_id: $project_id}) "
                "WHERE (n.file_path IN $paths OR n.path IN $paths) "
                "AND NOT n.id IN $keep_ids "
                "DETACH DELETE n RETURN count(n) AS count"
            )
            await driver.execute_query(
                query,
                {
                    "project_id": result.project_id,
                    "paths": sorted(set(cleanup_paths)),
                    "keep_ids": keep_ids,
                },
                args.neo4j_db,
            )
        return counts
    finally:
        await _close_driver(driver)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="COBOL semantic analyzer", allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default="cobol")
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", "cobol"))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--cobol-language-library", default=os.environ.get("COBOL_LANGUAGE_LIBRARY"))
    parser.add_argument("--copybook-root", action="append", default=[])
    parser.add_argument("--copybook-extension", action="append", default=[])
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION"))
    parser.add_argument("--embed-model", default=os.environ.get("EMBEDDING_MODEL"))
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-embed-chars", type=int, default=800)
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    add_graph_provider_args(parser)
    parser.add_argument("--enable-message-scan", action="store_true")
    parser.add_argument("--disable-message-scan", action="store_true")
    parser.add_argument("--message-output-dir")
    parser.add_argument("--message-qdrant-collection")
    parser.add_argument("--facts-output")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"[cobol] ERROR: root not found: {root}", file=sys.stderr)
        return 2
    try:
        runtime = preflight(args.cobol_language_library)
    except CobolRuntimeError as exc:
        print(f"[cobol] ERROR: {exc}", file=sys.stderr)
        return 2
    if args.preflight:
        print(
            "[cobol] preflight ok provider=%s platform=%s architecture=%s abi=%s"
            % (runtime.provider, runtime.platform, runtime.architecture, runtime.grammar_abi)
        )
        return 0
    project_id = args.project_id or root.name
    changed: list[str] = []
    deleted: list[str] = []
    cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else root / ".cortex" / "cobol"
    dependency_cache = cache_root / f"{project_id}-copybook-dependencies.json"
    try:
        if args.incremental:
            changed = _manifest_paths(args.changed_files_manifest, root)
            deleted = _manifest_paths(args.deleted_files_manifest, root)
        old_index = None
        if args.incremental and dependency_cache.is_file() and not args.ignore_cache:
            try:
                old_index = DependencyIndex.load(dependency_cache)
            except (OSError, ValueError) as exc:
                if args.verbose:
                    print(f"[cobol] WARNING: ignoring dependency cache: {exc}", file=sys.stderr)
        result, dependency_index = analyze_project(
            root,
            project_id=project_id,
            language_library=args.cobol_language_library,
            copybook_roots=tuple(Path(path) for path in args.copybook_root),
            copybook_extensions=tuple(args.copybook_extension) or (".cpy", ".copy", ".cbl", ".cob"),
        )
        if args.incremental:
            impacted = dependency_index.impacted_files(changed, deleted)
            if old_index:
                impacted.update(old_index.impacted_files(changed, deleted))
            if not old_index and any(Path(path).suffix.lower() in {".cpy", ".copy"} for path in (*changed, *deleted)):
                impacted.update(node.file_path for node in result.nodes)
            result = select_incremental_result(result, impacted)
        else:
            impacted = set()
    except (CobolRuntimeError, OSError, RuntimeError, ValueError) as exc:
        print(f"[cobol] ERROR: staged analysis failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            "Dry run: parser=cobol files=%d nodes=%d edges=%d diagnostics=%d"
            % (result.summary.processed_files, len(result.nodes), len(result.edges), len(result.diagnostics))
        )
        return 0
    output = Path(args.facts_output) if args.facts_output else cache_root / "facts.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.to_json(), encoding="utf-8")
    cleanup_paths = sorted(set(deleted) | (impacted if args.incremental else set()))
    try:
        counts = asyncio.run(write_graph(args, result, cleanup_paths))
    except Exception as exc:  # noqa: BLE001
        print(f"[cobol] ERROR: graph write failed after staged analysis: {exc}", file=sys.stderr)
        return 3
    vector_count = 0
    if args.qdrant_url and args.qdrant_collection:
        try:
            vector_count = sync_qdrant(
                result,
                url=args.qdrant_url,
                collection=args.qdrant_collection,
                model_name=args.embed_model or "jinaai/jina-embeddings-v3",
                device=args.device,
                batch_size=args.batch_size,
                max_chars=args.max_embed_chars,
                cleanup_paths=cleanup_paths,
                full_replace=not args.incremental,
                verbose=args.verbose,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[cobol] ERROR: Qdrant sync failed: {exc}", file=sys.stderr)
            return 4
    try:
        dependency_index.save(dependency_cache)
    except OSError as exc:
        print(f"[cobol] WARNING: dependency cache was not updated: {exc}", file=sys.stderr)
    print(
        "[SCAN_RESULT] parser=cobol files=%d nodes=%d edges=%d diagnostics=%d graph=%d vectors=%d artifact=%s"
        % (
            result.summary.processed_files,
            len(result.nodes),
            len(result.edges),
            len(result.diagnostics),
            sum(counts.values()),
            vector_count,
            output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

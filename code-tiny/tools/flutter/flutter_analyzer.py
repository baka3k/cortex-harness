#!/usr/bin/env python3
"""CortexHarness entry point for Python-only Dart and Flutter analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import List, Mapping, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.flutter.cache import DependencyIndex, select_incremental_facts  # noqa: E402
from tools.flutter.dart_parser import analyze_project, create_parser, parser_version  # noqa: E402
from tools.flutter.detector import detect_flutter_project, project_package_name  # noqa: E402
from tools.flutter.normalizer import normalize_facts  # noqa: E402
from tools.flutter.pipeline import write_canonical_batch  # noqa: E402
from tools.flutter.protocol import ProtocolError, parse_jsonl, record_to_dict, serialize_records  # noqa: E402
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402


ANALYZER_VERSION = "0.1.0"


def _manifest_paths(path: str | None, root: Path) -> List[str]:
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
    normalized = []
    for raw in value:
        candidate = Path(str(raw))
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(f"manifest path is outside the project root: {raw}") from exc
        normalized.append(candidate.as_posix())
    return sorted(set(normalized))


def write_fact_artifact(path: Path, facts) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "header": record_to_dict(facts.header),
        "nodes": [record_to_dict(item) for item in facts.nodes],
        "edges": [record_to_dict(item) for item in facts.edges],
        "diagnostics": [record_to_dict(item) for item in facts.diagnostics],
        "summary": record_to_dict(facts.summary),
    }
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def _close_driver(driver) -> None:
    result = driver.close()
    if hasattr(result, "__await__"):
        await result


async def write_graph(args: argparse.Namespace, facts, cleanup_paths: Sequence[str]) -> dict[str, int]:
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
        batch = normalize_facts(
            facts,
            project_name=args.project_name,
            repo=args.repo or str(Path(args.root).resolve()),
            build_system=args.build_system or "flutter",
        )
        counts = await write_canonical_batch(writer, batch)
        if cleanup_paths:
            rows = (*batch.files, *batch.classes, *batch.types, *batch.functions, *batch.fields)
            keep_ids = [row["id"] for row in rows]
            query = (
                "MATCH (n {project_id: $project_id}) "
                "WHERE (n.file_path IN $paths OR n.path IN $paths) "
                "AND NOT n.id IN $keep_ids "
                "DETACH DELETE n RETURN count(n) AS count"
            )
            await driver.execute_query(
                query,
                {
                    "project_id": facts.header.project_id,
                    "paths": sorted(set(cleanup_paths)),
                    "keep_ids": keep_ids,
                },
                args.neo4j_db,
            )
        return counts
    finally:
        await _close_driver(driver)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dart and Flutter semantic analyzer", allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--mode", choices=("dart", "flutter", "all"), default=os.environ.get("FLUTTER_ANALYZER_MODE", "dart"))
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION"))
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
        print(f"Root not found: {root}", file=sys.stderr)
        return 2
    project_id = args.project_id or root.name
    if args.mode == "flutter":
        try:
            flutter_project = detect_flutter_project(root)
        except ValueError as exc:
            print(f"[flutter] ERROR: {exc}", file=sys.stderr)
            return 2
        if flutter_project is None:
            print(f"[flutter] skipped: {root} is not a Flutter project")
            return 0
    dart_files = sorted(path for path in root.rglob("*.dart") if ".dart_tool" not in path.parts)
    if args.dry_run:
        print(f"Dry run: mode={args.mode} dart_files={len(dart_files)} root={root}")
        return 0
    try:
        if args.preflight:
            create_parser()
            print(f"[flutter] preflight ok runtime=python parser=tree-sitter-dart/{parser_version()}")
            return 0
        changed = _manifest_paths(args.changed_files_manifest, root) if args.incremental else []
        deleted = _manifest_paths(args.deleted_files_manifest, root) if args.incremental else []
        cache_root = Path(args.cache_dir).expanduser() if args.cache_dir else root / ".cortex" / "flutter"
        dependency_cache = cache_root / f"{project_id}-dart-dependencies.json"
        impacted: set[str] = set()
        if args.incremental:
            if dependency_cache.is_file() and not args.ignore_cache:
                try:
                    impacted = DependencyIndex.load(dependency_cache).impacted_files(changed, deleted)
                except (OSError, ValueError) as exc:
                    if args.verbose:
                        print(
                            f"[flutter] WARNING: ignoring dependency cache {dependency_cache}: {exc}",
                            file=sys.stderr,
                        )
                    impacted = {path.relative_to(root).as_posix() for path in dart_files}
                    impacted.update(deleted)
            else:
                impacted = {path.relative_to(root).as_posix() for path in dart_files}
                impacted.update(deleted)
        complete_facts = analyze_project(
            root,
            project_id=project_id,
            package_name=project_package_name(root),
            mode=args.mode,
        )
        records = [
            complete_facts.header,
            *complete_facts.nodes,
            *complete_facts.edges,
            *complete_facts.diagnostics,
            complete_facts.summary,
        ]
        complete_facts = parse_jsonl(serialize_records(records).splitlines())
        updated_dependency_index = DependencyIndex.from_facts(complete_facts)
        facts = select_incremental_facts(complete_facts, impacted) if args.incremental else complete_facts
    except (ImportError, OSError, ProtocolError, RuntimeError, ValueError) as exc:
        print(f"[flutter] ERROR: {exc}", file=sys.stderr)
        return 2
    output = Path(args.facts_output) if args.facts_output else root / ".cortex" / "flutter" / f"{args.mode}-facts.json"
    write_fact_artifact(output, facts)
    cleanup_paths = sorted(impacted | set(deleted)) if args.incremental else []
    try:
        counts = asyncio.run(write_graph(args, facts, cleanup_paths))
    except Exception as exc:  # noqa: BLE001
        print(f"[flutter] ERROR: graph write failed after staged analysis: {exc}", file=sys.stderr)
        return 3
    try:
        updated_dependency_index.save(dependency_cache)
    except OSError as exc:
        print(f"[flutter] WARNING: dependency cache was not updated: {exc}", file=sys.stderr)
    print(
        "[SCAN_RESULT] parser=%s files=%d nodes=%d edges=%d diagnostics=%d graph=%s artifact=%s"
        % (
            args.mode,
            facts.summary.processed_files,
            len(facts.nodes),
            len(facts.edges),
            len(facts.diagnostics),
            sum(counts.values()),
            output,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

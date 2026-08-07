from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.git_diff import load_manifest_paths
from tools.graph import GraphDriverFactory, GraphProvider, add_require_neo4j_argument, resolve_require_neo4j
from tools.graph.core.provider_runtime import add_graph_provider_arguments, create_graph_driver_from_args, graph_writes_disabled
from tools.graph.writer.servlet_jsp_writer import ServletJspFactWriter
from tools.servlet_jsp.cache import (
    generation_snapshot_checksum,
    generation_snapshot_path,
    load_generation_snapshot,
    preview_artifact_path,
    secure_atomic_json_write,
    write_generation_snapshot,
    write_preview_artifact,
)
from tools.servlet_jsp.models import ResourceBudgets, ServletJspAnalysisResult, ServletJspDependencyIndex, stable_digest
from tools.servlet_jsp.pipeline import run_servlet_jsp_analysis


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Servlet/JSP semantic overlay analyzer", allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", "--project_id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", "--project_name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", "--build_system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest", default="")
    parser.add_argument("--deleted-files-manifest", default="")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--servlet-jsp-preview-output", default="")
    parser.add_argument("--diagnostics-output", default="")
    parser.add_argument("--fail-on", choices=("error", "partial", "truncation"), default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    # Accepted for parity with shared analyzer invocations; Servlet/JSP v1
    # deliberately creates no separate vector collection.
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_CODE_PATH"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION"))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.set_defaults(enable_message_scan=False)
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    add_graph_provider_arguments(parser)
    add_require_neo4j_argument(parser)
    return parser.parse_args(argv)


def analyze(args: argparse.Namespace) -> ServletJspAnalysisResult:
    deleted: Sequence[str] = ()
    if args.deleted_files_manifest:
        deleted = tuple(load_manifest_paths(args.deleted_files_manifest, args.root))
    return run_servlet_jsp_analysis(
        root=args.root,
        project_id=args.project_id,
        project_name=args.project_name,
        deleted_paths=deleted,
    )


async def _modules_to_apply(
    *,
    writer: ServletJspFactWriter,
    args: argparse.Namespace,
    result: ServletJspAnalysisResult,
    budgets: ResourceBudgets,
    previously_active: set[str],
) -> set[str]:
    current = {module.module_id: module for module in result.modules}
    if not getattr(args, "incremental", False):
        return set(current)
    if getattr(args, "ignore_cache", False):
        return set(current)

    changed_paths = _load_incremental_paths(getattr(args, "changed_files_manifest", ""), args.root)
    deleted_paths = _load_incremental_paths(getattr(args, "deleted_files_manifest", ""), args.root)
    requested_paths = changed_paths | deleted_paths
    if not requested_paths:
        return set(current)

    affected = set(current) - previously_active
    matched_requested_paths: set[str] = set()
    for module_id in sorted(set(current) & previously_active):
        state = await writer.get_active_generation(result.project_id, module_id)
        generation_id = str(state.get("active_generation") or "")
        checksum = str(state.get("snapshot_checksum") or "")
        if not generation_id or not checksum:
            return set(current)
        snapshot_path = generation_snapshot_path(args.cache_dir, result.root, result.project_id, module_id, generation_id)
        snapshot, status = load_generation_snapshot(
            snapshot_path,
            root=result.root,
            project_id=result.project_id,
            module_id=module_id,
            generation_id=generation_id,
            expected_checksum=checksum,
            budgets=budgets,
        )
        if snapshot is None:
            if not getattr(args, "quiet", False):
                print(f"[servlet_jsp] incremental snapshot fallback module={module_id} status={status}", file=sys.stderr)
            return set(current)
        module_matches = {path for path in requested_paths if _module_matches_paths(current[module_id], {path})}
        snapshot_matches = {
            path
            for path in requested_paths
            if _snapshot_mentions_tokens(snapshot, _expand_snapshot_dependency_closure(snapshot, {path}))
        }
        matched = module_matches | snapshot_matches
        if matched:
            affected.add(module_id)
            matched_requested_paths.update(matched)

    # A manifest entry outside every known module can still be shared build or
    # deployment metadata. Rebuild all modules rather than risk stale facts.
    if matched_requested_paths != requested_paths:
        return set(current)
    return affected


def _load_incremental_paths(manifest_path: str, root: str) -> set[str]:
    if not manifest_path:
        return set()
    return {_normalize_project_path(path) for path in load_manifest_paths(manifest_path, root) if path}


def _module_matches_paths(module: Any, paths: set[str]) -> bool:
    module_files = {
        _normalize_project_path(path)
        for field_name in ("java_files", "descriptor_files", "jsp_files", "properties_files", "build_files", "static_files")
        for path in getattr(module, field_name)
    }
    if module_files & paths:
        return True
    prefix = _normalize_project_path(module.rel_path)
    return prefix in {"", "."} or any(path == prefix or path.startswith(prefix + "/") for path in paths)


def _expand_snapshot_dependency_closure(snapshot: Dict[str, Any], paths: set[str]) -> set[str]:
    result = snapshot.get("result") or {}
    adjacency: Dict[str, set[str]] = {}

    def connect(left: object, right: object) -> None:
        first = str(left)
        second = str(right)
        if not first or not second:
            return
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)

    for fact in result.get("semantic_facts") or ():
        source = fact.get("source") or {}
        connect(_normalize_project_path(str(source.get("file_path") or "")), fact.get("stable_id") or "")
    dependency_index = result.get("dependency_index") or {}
    for category in ("files", "components", "mappings", "views", "state_slots"):
        for source, targets in (dependency_index.get(category) or {}).items():
            for target in targets or ():
                connect(source, target)

    closure = {_normalize_project_path(path) for path in paths if path}
    queue = list(sorted(closure))
    while queue:
        token = queue.pop(0)
        for neighbor in sorted(adjacency.get(token, ())):
            if neighbor not in closure:
                closure.add(neighbor)
                queue.append(neighbor)
    return closure


def _snapshot_mentions_tokens(snapshot: Dict[str, Any], tokens: set[str]) -> bool:
    result = snapshot.get("result") or {}
    known: set[str] = set()
    for artifact in result.get("artifacts") or ():
        known.add(_normalize_project_path(str(artifact.get("file_path") or "")))
    for fact in result.get("semantic_facts") or ():
        known.add(str(fact.get("stable_id") or ""))
        source = fact.get("source") or {}
        known.add(_normalize_project_path(str(source.get("file_path") or "")))
    known.discard("")
    return bool(known & tokens)


def _module_dependency_index(
    result: ServletJspAnalysisResult,
    module_facts: Sequence[Any],
) -> ServletJspDependencyIndex:
    allowed = {fact.stable_id for fact in module_facts}
    allowed.update(_normalize_project_path(fact.source.file_path) for fact in module_facts if fact.source.file_path)
    values: Dict[str, Dict[str, tuple[str, ...]]] = {}
    for category in ("files", "components", "mappings", "views", "state_slots"):
        category_values = getattr(result.dependency_index, category)
        values[category] = {
            key: tuple(targets)
            for key, targets in category_values.items()
            if key in allowed
        }
    return ServletJspDependencyIndex(**values)


def _normalize_project_path(path: str) -> str:
    value = (path or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/")


async def _prior_semantic_ids(
    *,
    writer: ServletJspFactWriter,
    args: argparse.Namespace,
    result: ServletJspAnalysisResult,
    budgets: ResourceBudgets,
    module_id: str,
) -> set[str]:
    if getattr(args, "ignore_cache", False):
        return set()
    state = await writer.get_active_generation(result.project_id, module_id)
    generation_id = str(state.get("active_generation") or "")
    checksum = str(state.get("snapshot_checksum") or "")
    if not generation_id or not checksum:
        return set()
    snapshot, _ = load_generation_snapshot(
        generation_snapshot_path(args.cache_dir, result.root, result.project_id, module_id, generation_id),
        root=result.root,
        project_id=result.project_id,
        module_id=module_id,
        generation_id=generation_id,
        expected_checksum=checksum,
        budgets=budgets,
    )
    if snapshot is None:
        return set()
    payload = snapshot.get("result") or {}
    return {
        str(item.get("stable_id"))
        for category in ("semantic_facts", "relationships")
        for item in payload.get(category) or ()
        if item.get("stable_id")
    }


async def _create_driver(args: argparse.Namespace):
    if graph_writes_disabled():
        return None, args.neo4j_db
    if args.graph_provider == "falkordb":
        driver = await create_graph_driver_from_args(args)
        verify = getattr(driver, "verify_connection", None)
        if verify is not None and not await verify():
            await _close_driver(driver)
            raise RuntimeError("FalkorDB connection verification failed")
        return driver, args.falkordb_graph
    credentials = bool(args.neo4j_uri and args.neo4j_user and args.neo4j_password)
    if not credentials:
        if resolve_require_neo4j(args):
            raise ValueError("--require-neo4j is on but Neo4j credentials are incomplete")
        return None, args.neo4j_db
    driver = await GraphDriverFactory.create_driver(
        GraphProvider.NEO4J,
        {"uri": args.neo4j_uri, "user": args.neo4j_user, "password": args.neo4j_password, "database": args.neo4j_db},
    )
    return driver, args.neo4j_db


async def apply_graph(args: argparse.Namespace, result: ServletJspAnalysisResult, budgets: ResourceBudgets) -> Dict[str, Any]:
    driver, database = await _create_driver(args)
    if driver is None:
        return {"stage": "graphless", "baseline_advanced": False, "applied": 0, "created": 0, "updated": 0, "deleted": 0, "preserved": len(result.semantic_facts)}
    summary: Dict[str, Any] = {"stage": "apply", "baseline_advanced": False, "applied": 0, "created": 0, "updated": 0, "deleted": 0, "preserved": 0}
    try:
        writer = ServletJspFactWriter(driver, database=database, batch_size=args.neo4j_batch_size, verbose=args.verbose)
        previously_active = set(await writer.list_active_modules(result.project_id))
        current_modules = {module.module_id for module in result.modules}
        stale_modules = previously_active - current_modules
        modules_to_apply = await _modules_to_apply(
            writer=writer,
            args=args,
            result=result,
            budgets=budgets,
            previously_active=previously_active,
        )
        for module in result.modules:
            module_facts = [item for item in result.semantic_facts if item.module_id == module.module_id]
            module_relationships = [item for item in result.relationships if item.module_id == module.module_id]
            if module.module_id not in modules_to_apply:
                summary["preserved"] += len(module_facts) + len(module_relationships)
                continue
            prior_ids = await _prior_semantic_ids(writer=writer, args=args, result=result, budgets=budgets, module_id=module.module_id) if module.module_id in previously_active else set()
            current_ids = {item.stable_id for item in (*module_facts, *module_relationships)}
            summary["created"] += len(current_ids - prior_ids)
            summary["updated"] += len(current_ids & prior_ids)
            summary["deleted"] += len(prior_ids - current_ids)
            module_result = replace(
                result,
                modules=(module,),
                artifacts=tuple(item for item in result.artifacts if item.module_id == module.module_id),
                semantic_facts=tuple(module_facts),
                relationships=tuple(module_relationships),
                dependency_index=_module_dependency_index(result, module_facts),
            )
            generation_id = _module_generation_id(module_result, budgets)
            snapshot_path = generation_snapshot_path(args.cache_dir, result.root, result.project_id, module.module_id, generation_id)
            checksum = generation_snapshot_checksum(module_result, module_id=module.module_id, generation_id=generation_id, budgets=budgets)
            await writer.stage_generation(
                project_id=result.project_id,
                module_id=module.module_id,
                generation_id=generation_id,
                node_rows=[item.to_graph_node(generation_id) for item in module_facts],
                relationship_rows=[item.to_graph_row(generation_id) for item in module_relationships],
            )
            provider = args.graph_provider
            if not args.quiet:
                print(f"[{provider}] servlet_jsp_facts {len(module_facts)}/{len(module_facts)}")
                print(f"[{provider}] servlet_jsp_relationships {len(module_relationships)}/{len(module_relationships)}")
            await writer.promote_generation(
                project_id=result.project_id,
                module_id=module.module_id,
                generation_id=generation_id,
                snapshot_checksum=checksum,
                coverage_status=result.coverage_status,
            )
            written_checksum = write_generation_snapshot(snapshot_path, module_result, module_id=module.module_id, generation_id=generation_id, budgets=budgets)
            if written_checksum != checksum:
                raise RuntimeError("Applied Servlet/JSP snapshot checksum changed during serialization")
            cleanup = await writer.cleanup_inactive_generations(result.project_id, module.module_id)
            if not args.quiet:
                print(f"[cleanup][{provider}] deleted_nodes={cleanup.get('deleted_nodes', 0)} deleted_unknown_functions=0")
            summary["applied"] += len(module_facts) + len(module_relationships)
        for module_id in sorted(stale_modules):
            prior_ids = await _prior_semantic_ids(writer=writer, args=args, result=result, budgets=budgets, module_id=module_id)
            summary["deleted"] += len(prior_ids)
            tombstone = _tombstone_result(result)
            generation_id = _tombstone_generation_id(tombstone, budgets, module_id)
            snapshot_path = generation_snapshot_path(args.cache_dir, result.root, result.project_id, module_id, generation_id)
            checksum = generation_snapshot_checksum(tombstone, module_id=module_id, generation_id=generation_id, budgets=budgets)
            await writer.stage_generation(
                project_id=result.project_id,
                module_id=module_id,
                generation_id=generation_id,
                node_rows=[],
                relationship_rows=[],
            )
            provider = args.graph_provider
            if not args.quiet:
                print(f"[{provider}] servlet_jsp_facts 0/0")
                print(f"[{provider}] servlet_jsp_relationships 0/0")
            await writer.promote_generation(
                project_id=result.project_id,
                module_id=module_id,
                generation_id=generation_id,
                snapshot_checksum=checksum,
                coverage_status="empty",
            )
            written_checksum = write_generation_snapshot(snapshot_path, tombstone, module_id=module_id, generation_id=generation_id, budgets=budgets)
            if written_checksum != checksum:
                raise RuntimeError("Applied Servlet/JSP tombstone snapshot checksum changed during serialization")
            cleanup = await writer.cleanup_inactive_generations(result.project_id, module_id)
            if not args.quiet:
                print(f"[cleanup][{provider}] deleted_nodes={cleanup.get('deleted_nodes', 0)} deleted_unknown_functions=0")
        if not current_modules and not stale_modules:
            if not args.quiet:
                print(f"[cleanup][{args.graph_provider}] deleted_nodes=0 deleted_unknown_functions=0")
        summary["baseline_advanced"] = bool(modules_to_apply or stale_modules)
        summary["stage"] = "complete"
        return summary
    finally:
        await _close_driver(driver)


def _module_generation_id(result: ServletJspAnalysisResult, budgets: ResourceBudgets) -> str:
    payload = json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return stable_digest(result.parser_version, budgets.fingerprint(), payload, length=24)


def _tombstone_result(result: ServletJspAnalysisResult) -> ServletJspAnalysisResult:
    return ServletJspAnalysisResult(
        project_id=result.project_id,
        project_name=result.project_name,
        root=result.root,
        coverage_status="empty",
        parser_version=result.parser_version,
    )


def _tombstone_generation_id(tombstone: ServletJspAnalysisResult, budgets: ResourceBudgets, module_id: str) -> str:
    return stable_digest("tombstone", module_id, _module_generation_id(tombstone, budgets), length=24)


async def _close_driver(driver: Any) -> None:
    value = driver.close()
    if hasattr(value, "__await__"):
        await value


def _failure_code(args: argparse.Namespace, result: ServletJspAnalysisResult) -> int:
    if args.fail_on == "error" and any(item.severity == "error" for item in result.diagnostics):
        return 4
    if args.fail_on == "partial" and result.coverage_status != "complete":
        return 5
    if args.fail_on == "truncation" and result.truncation_count:
        return 6
    return 0


def _summary(result: ServletJspAnalysisResult, preview: str, graph: Dict[str, Any]) -> str:
    payload = {
        "analyzer": "servlet_jsp",
        "modules": len(result.modules),
        "artifacts": len(result.artifacts),
        "facts": len(result.semantic_facts),
        "relationships": len(result.relationships),
        "diagnostics": len(result.diagnostics),
        "coverage_status": result.coverage_status,
        "truncation_count": result.truncation_count,
        "baseline_advanced": bool(graph.get("baseline_advanced")),
        "stage": graph.get("stage", "preview"),
        "applied": int(graph.get("applied", 0)),
        "created": int(graph.get("created", 0)),
        "updated": int(graph.get("updated", 0)),
        "deleted": int(graph.get("deleted", 0)),
        "preserved": int(graph.get("preserved", 0)),
        "preview": preview,
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    args.root = os.path.realpath(os.path.abspath(args.root))
    if not os.path.isdir(args.root):
        print(f"[servlet_jsp] ERROR: root not found: {args.root}", file=sys.stderr)
        return 2
    args.project_id = args.project_id or os.path.basename(args.root)
    args.project_name = args.project_name or args.project_id
    budgets = ResourceBudgets()
    try:
        result = analyze(args)
        preview = args.servlet_jsp_preview_output or preview_artifact_path(args.cache_dir, args.root, args.project_id)
        write_preview_artifact(preview, result)
        if args.diagnostics_output:
            secure_atomic_json_write(args.diagnostics_output, {"artifact_role": "diagnostics", "diagnostics": [item.__dict__ for item in result.diagnostics]})
        failed = _failure_code(args, result)
        if failed:
            print(_summary(result, preview, {"stage": "validation_failed", "baseline_advanced": False}), file=sys.stderr)
            return failed
        graph = {"stage": "preview" if args.dry_run else "graphless", "baseline_advanced": False, "applied": 0, "created": 0, "updated": 0, "deleted": 0, "preserved": len(result.semantic_facts)}
        if not args.dry_run:
            graph = asyncio.run(apply_graph(args, result, budgets))
        if not args.quiet:
            print(_summary(result, preview, graph))
        return 0
    except ValueError as exc:
        print(f"[servlet_jsp] ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"[servlet_jsp] ERROR: {exc!r}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

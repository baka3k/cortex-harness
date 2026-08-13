from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from tools.common.aspnet.identity import stable_digest
from tools.common.aspnet.models import AnalysisResult
from tools.common.aspnet.safe_formats import redact_value
from tools.graph import GraphDriverFactory, GraphProvider, add_require_neo4j_argument, resolve_require_neo4j
from tools.graph.cli import create_graph_driver_from_args
from tools.graph.core.provider_runtime import add_graph_provider_arguments
from tools.graph.writer.aspnet_writer import AspNetFactWriter


def add_shared_arguments(parser: argparse.ArgumentParser, *, output_flag: str) -> None:
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
    parser.add_argument(output_flag, dest="preview_output", default="")
    parser.add_argument("--diagnostics-output", default="")
    parser.add_argument("--semantic", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--roslyn-worker-project", default=os.environ.get("ASPNET_ROSLYN_WORKER_PROJECT", ""))
    parser.add_argument("--fail-on", choices=("error", "partial", "truncation"), default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION"))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.set_defaults(enable_message_scan=False)
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"))
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION"))
    add_graph_provider_arguments(parser)
    add_require_neo4j_argument(parser)


def load_manifest(path: str, root: str) -> tuple[str, ...]:
    if not path:
        return ()
    from tools.common.git_diff import load_manifest_paths

    return tuple(sorted(load_manifest_paths(path, root)))


def write_outputs(args: argparse.Namespace, result: AnalysisResult) -> None:
    if args.preview_output:
        _atomic_write(args.preview_output, result.to_json())
    if args.diagnostics_output:
        payload = json.dumps(
            redact_value("diagnostics", [
                {
                    "code": item.code,
                    "message": item.message,
                    "severity": item.severity,
                    "file_path": item.source.file_path,
                    "start_line": item.source.start_line,
                    "details": item.details,
                }
                for item in result.diagnostics
            ]),
            ensure_ascii=True, sort_keys=True, indent=2,
        ) + "\n"
        _atomic_write(args.diagnostics_output, payload)


def fail_code(args: argparse.Namespace, result: AnalysisResult) -> int:
    if args.fail_on == "error" and any(item.severity == "error" for item in result.diagnostics):
        return 4
    if args.fail_on == "partial" and result.coverage_status != "complete":
        return 4
    if args.fail_on == "truncation" and any("truncat" in item.code for item in result.diagnostics):
        return 4
    return 0


async def apply_graph(args: argparse.Namespace, result: AnalysisResult) -> Dict[str, int | str]:
    driver, database = await _create_driver(args)
    if driver is None:
        return {"stage": "graphless", "nodes": 0, "relationships": 0}
    totals = {"stage": "applied", "nodes": 0, "relationships": 0, "preserved_modules": 0}
    try:
        writer = AspNetFactWriter(driver, database=database, batch_size=args.neo4j_batch_size, verbose=args.verbose)
        for module in result.modules:
            active_state = await writer.active_state(
                result.project_id, module.module_id, result.framework,
            )
            is_deleted_module_cleanup = any(
                str(item).endswith(":deleted") for item in module.evidence
            )
            module_coverage = result.module_coverage.get(module.module_id, result.coverage_status)
            if (
                module_coverage != "complete"
                and active_state.get("active_generation")
                and active_state.get("coverage_status") == "complete"
                and not is_deleted_module_cleanup
            ):
                totals["stage"] = "preserved_complete"
                totals["preserved_modules"] = int(totals["preserved_modules"]) + 1
                continue
            facts = [item for item in result.facts if item.module_id == module.module_id]
            relationships = [item for item in result.relationships if item.module_id == module.module_id]
            checksum = hashlib.sha256(
                json.dumps(
                    {
                        "facts": [item.stable_id for item in facts],
                        "relationships": [item.stable_id for item in relationships],
                        "coverage": module_coverage,
                    },
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            generation_id = stable_digest(result.parser_version, module.module_id, checksum)
            await writer.stage_generation(
                project_id=result.project_id, module_id=module.module_id, framework=result.framework,
                generation_id=generation_id,
                node_rows=[item.to_graph_node(generation_id) for item in facts],
                relationship_rows=[item.to_graph_row(generation_id) for item in relationships],
            )
            await writer.promote_generation(
                project_id=result.project_id, module_id=module.module_id, framework=result.framework,
                generation_id=generation_id, snapshot_checksum=checksum,
                coverage_status=module_coverage,
            )
            await writer.cleanup_inactive_generations(result.project_id, module.module_id, result.framework)
            totals["nodes"] = int(totals["nodes"]) + len(facts)
            totals["relationships"] = int(totals["relationships"]) + len(relationships)
    finally:
        closed = driver.close()
        if hasattr(closed, "__await__"):
            await closed
    return totals


async def _create_driver(args: argparse.Namespace):
    if args.graph_provider == "falkordb":
        driver = await create_graph_driver_from_args(args)
        if driver is None:
            return None, args.falkordb_graph
        verify = getattr(driver, "verify_connection", None)
        if verify is not None and not await verify():
            closed = driver.close()
            if hasattr(closed, "__await__"):
                await closed
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


def _atomic_write(path: str, payload: str) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)

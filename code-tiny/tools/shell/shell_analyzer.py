#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Iterable

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files
from tools.common.primary_vector_sync import documents_from_rows, sync_vector_documents, vector_configured
from tools.common.project_scope import project_id_lookup_key
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args
from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.shell.mapping import load_program_mappings
from tools.shell.models import ProgramMapping, ShellAnalysisResult
from tools.shell.pipeline import run_shell_analysis


def _program_node_id(project_id: str, program_id: str) -> str:
    return f"batch-program::{project_id}:{program_id}"


def build_graph_rows(
    result: ShellAnalysisResult,
    *,
    project_name: str,
    repo: str,
    program_mappings: Iterable[ProgramMapping] = (),
) -> dict[str, list[dict[str, Any]]]:
    common = {
        "project_id": result.project_id,
        "project_name": project_name,
        "language": "shell",
        "repo": repo,
        "build_system": "shell",
    }
    scripts: list[dict[str, Any]] = []
    functions: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    programs: dict[str, dict[str, Any]] = {}
    referenced_files: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    implementation_keys: set[tuple[str, str]] = set()
    mappings = {mapping.program_id: mapping for mapping in program_mappings}
    for file in result.files:
        scripts.append({"id": file.file_path, "name": os.path.basename(file.file_path), "file_path": file.file_path, "encoding": file.encoding, **common})
        for function in file.functions:
            functions.append({"id": function.symbol_id, "name": function.name, "file_path": function.file_path, "start_line": function.start_line, "end_line": function.end_line, "code": function.code, **common})
            relations.append({"source_id": file.file_path, "source_label": "ShellScript", "target_id": function.symbol_id, "target_label": "ShellFunction", "rel_type": "CONTAINS", "project_id": result.project_id, "properties": {}})
        for invocation in file.invocations:
            mapping = mappings.get(invocation.command_name)
            program_node_id = (
                _program_node_id(result.project_id, mapping.program_id)
                if mapping is not None
                else ""
            )
            invocations.append(
                {
                    "id": invocation.symbol_id,
                    "name": invocation.command_name or invocation.raw_command,
                    "command_name": invocation.command_name,
                    "raw_command": invocation.raw_command,
                    "file_path": invocation.file_path,
                    "line": invocation.line,
                    "ordinal": invocation.ordinal,
                    "dynamic": invocation.dynamic,
                    "resolution_status": "mapped_candidate" if mapping else "unresolved",
                    **common,
                }
            )
            relations.append(
                {
                    "source_id": invocation.source_id,
                    "source_label": invocation.source_label,
                    "target_id": invocation.symbol_id,
                    "target_label": "ShellInvocation",
                    "rel_type": "HAS_INVOCATION",
                    "project_id": result.project_id,
                    "properties": {
                        "line": invocation.line,
                        "raw_command": invocation.raw_command,
                    },
                }
            )
            if mapping is None:
                continue
            programs.setdefault(
                program_node_id,
                {
                    "id": program_node_id,
                    "name": mapping.program_id,
                    "program_id": mapping.program_id,
                    "source_path": mapping.source_path,
                    "evidence_hash": mapping.evidence_hash,
                    **common,
                },
            )
            relations.append(
                {
                    "source_id": invocation.symbol_id,
                    "source_label": "ShellInvocation",
                    "target_id": program_node_id,
                    "target_label": "BatchProgram",
                    "rel_type": "RESOLVES_TO",
                    "project_id": result.project_id,
                    "properties": {"evidence_hash": mapping.evidence_hash},
                }
            )
            implementation_key = (program_node_id, mapping.source_path)
            if implementation_key not in implementation_keys:
                implementation_keys.add(implementation_key)
                relations.append(
                    {
                        "source_id": program_node_id,
                        "source_label": "BatchProgram",
                        "target_id": mapping.source_path,
                        "target_label": "File",
                        "rel_type": "IMPLEMENTED_BY",
                        "project_id": result.project_id,
                        "properties": {"evidence_hash": mapping.evidence_hash},
                    }
                )
        for relation in file.relations:
            relations.append({"source_id": relation.source_id, "source_label": relation.source_label, "target_id": relation.target_id, "target_label": relation.target_label, "rel_type": relation.rel_type, "project_id": result.project_id, "properties": {"line": relation.line, "raw_target": relation.raw_target, "resolved": relation.resolved}})
            if relation.target_label == "File" and relation.resolved:
                referenced_files.setdefault(
                    relation.target_id,
                    {
                        "id": relation.target_id,
                        "name": os.path.basename(relation.target_id),
                        "file_path": relation.target_id,
                        **common,
                    },
                )
    return {
        "scripts": scripts,
        "functions": functions,
        "invocations": invocations,
        "programs": list(programs.values()),
        "files": list(referenced_files.values()),
        "relations": relations,
    }


async def _materialized_file_ids(
    driver: Any,
    *,
    database: str | None,
    project_id: str,
    file_ids: Iterable[str],
) -> set[str]:
    candidates = sorted(set(file_ids))
    if not candidates:
        return set()
    records, _, _ = await driver.execute_query(
        "MATCH (f:File) "
        "WHERE f.project_id_normalized = $project_id_normalized "
        "AND f.id IN $file_ids "
        "RETURN f.id AS id",
        {
            "project_id": project_id,
            "project_id_normalized": project_id_lookup_key(project_id),
            "file_ids": candidates,
        },
        database,
    )
    return {str(record["id"]) for record in records if record.get("id")}


async def _verified_lineage_rows(
    driver: Any,
    rows: dict[str, list[dict[str, Any]]],
    *,
    database: str | None,
    project_id: str,
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    implementation_relations = [
        relation
        for relation in rows["relations"]
        if relation["rel_type"] == "IMPLEMENTED_BY"
    ]
    materialized = await _materialized_file_ids(
        driver,
        database=database,
        project_id=project_id,
        file_ids=(relation["target_id"] for relation in implementation_relations),
    )
    verified_programs = {
        relation["source_id"]
        for relation in implementation_relations
        if relation["target_id"] in materialized
    }
    filtered_relations: list[dict[str, Any]] = []
    skipped = 0
    for relation in rows["relations"]:
        if relation["rel_type"] == "IMPLEMENTED_BY" and relation["source_id"] not in verified_programs:
            skipped += 1
            continue
        if relation["rel_type"] == "RESOLVES_TO" and relation["target_id"] not in verified_programs:
            skipped += 1
            continue
        filtered_relations.append(relation)
    verified_invocation_ids = {
        relation["source_id"]
        for relation in filtered_relations
        if relation["rel_type"] == "RESOLVES_TO"
    }
    for invocation in rows["invocations"]:
        if invocation["resolution_status"] == "mapped_candidate":
            invocation["resolution_status"] = (
                "verified"
                if invocation["id"] in verified_invocation_ids
                else "mapped_source_missing"
            )
    rows["programs"] = [
        program for program in rows["programs"] if program["id"] in verified_programs
    ]
    rows["relations"] = filtered_relations
    return rows, skipped


async def _write_graph(
    args: argparse.Namespace,
    result: ShellAnalysisResult,
    program_mappings: Iterable[ProgramMapping],
) -> dict[str, int]:
    if not prepare_graph_args(args):
        return {}
    driver = await create_graph_driver_from_args(args)
    if driver is None:
        raise RuntimeError("Graph provider was requested but no driver was created")
    try:
        writer = LanguageCodeWriter(driver, database=args.neo4j_db, batch_size=args.neo4j_batch_size, verbose=args.verbose)
        await writer.ensure_schema()
        rows = build_graph_rows(
            result,
            project_name=args.project_name or result.project_id,
            repo=args.repo or result.project_id,
            program_mappings=program_mappings,
        )
        rows, missing_lineage_count = await _verified_lineage_rows(
            driver,
            rows,
            database=args.neo4j_db,
            project_id=result.project_id,
        )
        cleanup_paths = sorted(set(result.changed_paths) | set(result.deleted_paths))
        if args.incremental and cleanup_paths:
            await cleanup_neo4j_for_files(
                driver=driver,
                database=args.neo4j_db,
                project_id=result.project_id,
                file_paths=cleanup_paths,
                verbose=args.verbose,
            )
        script_query = "UNWIND $rows AS row MERGE (n:ShellScript {id: row.id}) SET n += row"
        function_query = "UNWIND $rows AS row MERGE (n:ShellFunction {id: row.id}) SET n += row"
        invocation_query = "UNWIND $rows AS row MERGE (n:ShellInvocation {id: row.id}) SET n += row"
        program_query = "UNWIND $rows AS row MERGE (n:BatchProgram {id: row.id}) SET n += row"
        file_query = "UNWIND $rows AS row MERGE (n:File {id: row.id}) SET n += row"
        counts = {
            "ShellScript": await writer.write_nodes_batch("shell:scripts", script_query, rows["scripts"]),
            "ShellFunction": await writer.write_nodes_batch("shell:functions", function_query, rows["functions"]),
            "ShellInvocation": await writer.write_nodes_batch("shell:invocations", invocation_query, rows["invocations"]),
            "BatchProgram": await writer.write_nodes_batch("shell:programs", program_query, rows["programs"]),
            "File": await writer.write_nodes_batch("shell:referenced-files", file_query, rows["files"]),
        }
        required_relations = [
            row for row in rows["relations"] if row.get("properties", {}).get("resolved") is not False
        ]
        unresolved_count = (
            len(rows["relations"]) - len(required_relations) + missing_lineage_count
        )
        counts["relations"] = await writer.write_relations_typed(
            required_relations, project_id=result.project_id
        )
        counts["unresolved_relations"] = unresolved_count
        if args.verbose and unresolved_count:
            print(f"[graph] optional unresolved shell relations skipped={unresolved_count}")
        return counts
    finally:
        close = getattr(driver, "close", None)
        if close:
            result_close = close()
            if hasattr(result_close, "__await__"):
                await result_close


def _sync_vectors(
    args: argparse.Namespace,
    result: ShellAnalysisResult,
    program_mappings: Iterable[ProgramMapping],
) -> int:
    if not vector_configured(args.qdrant_url):
        return 0
    repo = args.repo or result.project_id
    rows = build_graph_rows(
        result,
        project_name=args.project_name or result.project_id,
        repo=repo,
        program_mappings=program_mappings,
    )
    documents = documents_from_rows(rows, parser="shell", root_scope=repo, max_chars=args.max_embed_chars)
    return sync_vector_documents(
        documents,
        url=args.qdrant_url,
        collection=args.qdrant_collection,
        model_name=args.embed_model or "jinaai/jina-embeddings-v3",
        device=args.device,
        embed_batch_size=args.batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        parser="shell",
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
    parser = argparse.ArgumentParser(description="Static shell-script analyzer", allow_abbrev=False)
    parser.add_argument("path", nargs="?")
    parser.add_argument("--root")
    parser.add_argument("--project-id", "--project_id", dest="project_id")
    parser.add_argument("--project-name", "--project_name", dest="project_name")
    parser.add_argument("--repo")
    parser.add_argument("--build-system", "--build_system", dest="build_system", default="shell")
    parser.add_argument("--commit-sha-before", default="")
    parser.add_argument("--commit-sha-after", default="")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--cache-dir")
    parser.add_argument(
        "--program-mapping-ledger",
        help="External JSON ledger containing program_id, source_path, and evidence_hash",
    )
    parser.add_argument(
        "--program-mapping-id-field",
        default=os.environ.get("SHELL_PROGRAM_MAPPING_ID_FIELD", "program_id"),
        help="Ledger field containing the logical program identifier",
    )
    parser.add_argument(
        "--program-mapping-source-field",
        default=os.environ.get("SHELL_PROGRAM_MAPPING_SOURCE_FIELD", "source_path"),
        help="Ledger field containing the source path",
    )
    parser.add_argument(
        "--program-mapping-evidence-field",
        default=os.environ.get("SHELL_PROGRAM_MAPPING_EVIDENCE_FIELD", "evidence_hash"),
        help="Optional ledger field containing a row evidence hash",
    )
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
    parser.add_argument("--qdrant-url")
    parser.add_argument("--qdrant-collection", default="shell_functions")
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
    result = run_shell_analysis(root, project_id=args.project_id or os.path.basename(root), changed_paths=changed, deleted_paths=deleted)
    try:
        program_mappings = (
            load_program_mappings(
                args.program_mapping_ledger,
                root=root,
                program_id_field=args.program_mapping_id_field,
                source_path_field=args.program_mapping_source_field,
                evidence_hash_field=args.program_mapping_evidence_field,
            )
            if args.program_mapping_ledger
            else ()
        )
    except ValueError as exc:
        print(f"Invalid --program-mapping-ledger: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    if not args.dry_run:
        await _write_graph(args, result, program_mappings)
        vector_count = _sync_vectors(args, result, program_mappings)
    else:
        vector_count = 0
    if args.dry_run or args.pretty:
        print(payload)
    function_count = sum(len(file.functions) for file in result.files)
    vector_status = "success" if vector_configured(args.qdrant_url) and not args.dry_run else "disabled"
    print(f"[SCAN_RESULT] parser=shell files={len(result.files)} functions={function_count} vectors={vector_count} vector_status={vector_status}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

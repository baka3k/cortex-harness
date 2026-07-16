#!/usr/bin/env python3
"""CLI and persistence adapter for the Perl Tree-sitter analyzer."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files
from tools.common.primary_vector_sync import (
    documents_from_rows,
    sync_vector_documents,
    vector_configured,
)
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args
from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.perl.models import AnalysisResult, Diagnostic, SymbolRecord, _to_primitive
from tools.perl.pipeline import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES,
    run_perl_analysis,
)


def _common_fields(
    *,
    project_id: str,
    project_name: str,
    repo: str,
    build_system: str,
) -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "project_name": project_name,
        "language": "perl",
        "repo": repo,
        "build_system": build_system,
    }


def _symbol_label(symbol: SymbolRecord) -> str:
    return {"package": "Namespace", "subroutine": "Function", "variable": "Field"}[symbol.kind]


def build_graph_rows(
    result: AnalysisResult,
    *,
    project_name: str,
    repo: str,
    build_system: str = "perl",
) -> Dict[str, List[Dict[str, Any]]]:
    """Map normalized Perl records to existing canonical writer rows."""
    common = _common_fields(
        project_id=result.project_id,
        project_name=project_name,
        repo=repo,
        build_system=build_system,
    )
    files: List[Dict[str, Any]] = []
    namespaces: List[Dict[str, Any]] = []
    functions: List[Dict[str, Any]] = []
    fields: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    calls: List[Dict[str, Any]] = []
    package_ids: Dict[Tuple[str, str], str] = {}

    for record in result.files:
        files.append(
            {
                "id": record.file_path,
                "path": record.file_path,
                "start_line": 1,
                "end_line": record.line_count,
                "code": "",
                "comment": "",
                "summary": f"Perl source ({record.coverage} coverage)",
                "note": (
                    f"parser={record.parser_version}; grammar={record.grammar_version}; "
                    f"sha256={record.content_sha256}; errors={record.error_count}"
                ),
                **common,
            }
        )

    for symbol in result.symbols:
        base = {
            "id": symbol.symbol_id,
            "name": symbol.name,
            "qualified_name": symbol.fq_name,
            "file_path": symbol.file_path,
            "start_line": symbol.span.start_line,
            "end_line": symbol.span.end_line,
            "code": symbol.code,
            "comment": symbol.documentation,
            "summary": symbol.documentation or f"Perl {symbol.kind}: {symbol.fq_name}",
            "note": (
                f"declaration_kind={symbol.declaration_kind}; "
                f"prototype={symbol.prototype}; attributes={','.join(symbol.attributes)}"
            ),
            **common,
        }
        if symbol.kind == "package":
            package_ids[(symbol.fq_name, symbol.file_path)] = symbol.symbol_id
            namespaces.append(base)
        elif symbol.kind == "subroutine":
            functions.append(
                {
                    **base,
                    "kind": "function",
                    "class_name": "",
                    "package_name": symbol.package,
                    "scope_name": symbol.scope,
                    "start_byte": symbol.span.start_byte,
                    "end_byte": symbol.span.end_byte,
                    "arity": symbol.arity,
                    "exported": False,
                    "external": False,
                    "builtin": False,
                    "react_role": "",
                    "middleware_kind": "",
                }
            )
        elif symbol.kind == "variable":
            fields.append(
                {
                    **base,
                    "scope_name": symbol.scope,
                    "type_signature": symbol.declaration_kind,
                }
            )
        relations.append(
            {
                "source_id": symbol.file_path,
                "source_label": "File",
                "target_id": symbol.symbol_id,
                "target_label": _symbol_label(symbol),
                "rel_type": "CONTAINS",
                "properties": {"project_id": result.project_id},
            }
        )

    for symbol in result.symbols:
        if symbol.kind not in {"subroutine", "variable"}:
            continue
        package_id = package_ids.get((symbol.package, symbol.file_path))
        if package_id:
            relations.append(
                {
                    "source_id": package_id,
                    "source_label": "Namespace",
                    "target_id": symbol.symbol_id,
                    "target_label": _symbol_label(symbol),
                    "rel_type": "DECLARES",
                    "properties": {"project_id": result.project_id},
                }
            )

    for item in result.imports:
        if item.resolved_path:
            relations.append(
                {
                    "source_id": item.file_path,
                    "source_label": "File",
                    "target_id": item.resolved_path,
                    "target_label": "File",
                    "rel_type": "IMPORTS",
                    "properties": {
                        "module": item.module,
                        "kind": item.kind,
                        "conditional": item.is_conditional,
                    },
                }
            )
    for reference in result.references:
        if (
            reference.resolution_status == "resolved"
            and reference.source_symbol_id
            and reference.target_symbol_id
        ):
            calls.append(
                {
                    "caller_id": reference.source_symbol_id,
                    "callee_id": reference.target_symbol_id,
                    "call_type": reference.kind,
                }
            )

    relation_key = lambda row: (
        row["source_label"],
        row["source_id"],
        row["rel_type"],
        row["target_label"],
        row["target_id"],
    )
    call_key = lambda row: (row["caller_id"], row["callee_id"], row["call_type"])
    return {
        "files": sorted(files, key=lambda row: row["id"]),
        "namespaces": sorted(namespaces, key=lambda row: row["id"]),
        "functions": sorted(functions, key=lambda row: row["id"]),
        "fields": sorted(fields, key=lambda row: row["id"]),
        "relations": list({relation_key(row): row for row in relations}.values()),
        "calls": list({call_key(row): row for row in calls}.values()),
    }


async def _write_graph(args: argparse.Namespace, result: AnalysisResult) -> Dict[str, int]:
    if not prepare_graph_args(args):
        return {}
    driver = await create_graph_driver_from_args(args)
    if driver is None:
        raise RuntimeError("Graph provider was requested but no driver was created.")
    try:
        project_name = args.project_name or result.project_id
        repo = args.repo or f"{project_name}/{os.path.basename(args.root)}"
        rows = build_graph_rows(
            result,
            project_name=project_name,
            repo=repo,
            build_system=args.build_system or "perl",
        )
        cleanup_targets = sorted(set(result.changed_paths) | set(result.deleted_paths))
        if args.incremental and cleanup_targets:
            await cleanup_neo4j_for_files(
                driver=driver,
                database=args.neo4j_db,
                project_id=result.project_id,
                file_paths=cleanup_targets,
                verbose=args.verbose,
            )
        writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )
        return await writer.write_all(
            namespaces=rows["namespaces"],
            files=rows["files"],
            functions=rows["functions"],
            fields=rows["fields"],
            relations=rows["relations"],
            calls=rows["calls"],
            use_full_writers=True,
        )
    finally:
        close = getattr(driver, "close", None)
        if close:
            closed = close()
            if hasattr(closed, "__await__"):
                await closed


def _sync_vectors(args: argparse.Namespace, result: AnalysisResult) -> int:
    if not vector_configured(args.qdrant_url):
        return 0
    project_name = args.project_name or result.project_id
    repo = args.repo or f"{project_name}/{os.path.basename(args.root)}"
    rows = build_graph_rows(
        result,
        project_name=project_name,
        repo=repo,
        build_system=args.build_system or "perl",
    )
    documents = documents_from_rows(
        rows,
        parser="perl",
        root_scope=repo,
        max_chars=args.max_embed_chars,
    )
    cleanup_targets = sorted(set(result.changed_paths) | set(result.deleted_paths))
    if not getattr(args, "_scanned_directory", False) and not cleanup_targets:
        cleanup_targets = sorted({item.payload["file_path"] for item in documents})
    return sync_vector_documents(
        documents,
        url=args.qdrant_url,
        collection=args.qdrant_collection,
        model_name=args.embed_model or "jinaai/jina-embeddings-v3",
        device=args.device,
        embed_batch_size=args.batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        parser="perl",
        project_id=result.project_id,
        root_scope=repo,
        cleanup_paths=cleanup_targets,
        full_replace=not args.incremental and getattr(args, "_scanned_directory", False),
        timeout=args.qdrant_timeout,
        retries=args.qdrant_retries,
        retry_sleep=args.qdrant_retry_sleep,
        verbose=args.verbose,
    )


def _output_path(root: str, raw_path: str) -> str:
    root_real = os.path.realpath(os.path.abspath(root))
    candidate = raw_path if os.path.isabs(raw_path) else os.path.join(root_real, raw_path)
    parent = os.path.realpath(os.path.abspath(os.path.dirname(candidate) or root_real))
    if os.path.commonpath((root_real, parent)) != root_real:
        raise ValueError("output path must remain inside the analysis root")
    os.makedirs(parent, exist_ok=True)
    return os.path.abspath(candidate)


def _write_text_atomic(path: str, text: str) -> None:
    temp_path = path + f".{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Perl 5 Tree-sitter structural analyzer",
        allow_abbrev=False,
    )
    parser.add_argument("path", nargs="?", help="Perl source file or project directory")
    parser.add_argument("--root", help="Project root")
    parser.add_argument("--project-id", "--project_id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project-name", "--project_name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--language", default="perl")
    parser.add_argument("--build-system", "--build_system", dest="build_system", default="perl")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""))
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""))
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--cache-dir", default=os.environ.get("PERL_ANALYZER_CACHE_DIR"))
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--include-docs", action="store_true")
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-snippet-chars", type=int, default=4000)
    parser.add_argument("--max-doc-chars", type=int, default=8000)
    parser.add_argument("--fail-on-partial", action="store_true")
    parser.add_argument("--output", "-o")
    parser.add_argument("--diagnostics-output")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    add_graph_provider_args(parser)
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "perl_functions"))
    parser.add_argument("--embed-model", default=os.environ.get("CODE_EMBEDDING_MODEL", ""))
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "cpu"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", "4")))
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", "4000")))
    parser.add_argument("--qdrant-batch-size", type=int, default=int(os.environ.get("QDRANT_BATCH_SIZE", "128")))
    parser.add_argument("--qdrant-timeout", type=float, default=float(os.environ.get("QDRANT_TIMEOUT", "300")))
    parser.add_argument("--qdrant-retries", type=int, default=int(os.environ.get("QDRANT_RETRIES", "3")))
    parser.add_argument("--qdrant-retry-sleep", type=float, default=float(os.environ.get("QDRANT_RETRY_SLEEP", "2")))
    parser.set_defaults(enable_message_scan=False)
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--message-output-dir")
    parser.add_argument("--message-qdrant-collection", default="")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    raw_root = args.root or args.path
    if not raw_root:
        print("Either path or --root is required.", file=sys.stderr)
        return 2
    path = os.path.realpath(os.path.abspath(raw_root))
    args._scanned_directory = os.path.isdir(path)
    if os.path.isfile(path):
        args.root = os.path.dirname(path)
        implicit_changed: Optional[Iterable[str]] = [os.path.basename(path)]
    elif os.path.isdir(path):
        args.root = path
        implicit_changed = None
    else:
        print(f"Analysis path does not exist: {raw_root}", file=sys.stderr)
        return 2
    project_id = args.project_id or os.path.basename(args.root) or "perl-project"
    try:
        changed: Optional[Iterable[str]] = implicit_changed
        deleted: Iterable[str] = ()
        if args.incremental:
            changed = load_manifest_paths(args.changed_files_manifest, args.root) if args.changed_files_manifest else ()
            deleted = load_manifest_paths(args.deleted_files_manifest, args.root) if args.deleted_files_manifest else ()
        result = run_perl_analysis(
            args.root,
            project_id=project_id,
            changed_paths=changed,
            deleted_paths=deleted,
            cache_dir=args.cache_dir,
            ignore_cache=args.ignore_cache,
            include_docs=args.include_docs,
            max_file_bytes=args.max_file_bytes,
            max_total_bytes=args.max_total_bytes,
            max_files=args.max_files,
            max_snippet_chars=args.max_snippet_chars,
            max_doc_chars=args.max_doc_chars,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Perl analysis failed: {exc}", file=sys.stderr)
        return 3

    payload = result.to_json(pretty=args.pretty)
    try:
        if args.output:
            _write_text_atomic(_output_path(args.root, args.output), payload + "\n")
        if args.diagnostics_output:
            diagnostic_payload = json.dumps(
                [_to_primitive(item) for item in result.diagnostics],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            _write_text_atomic(_output_path(args.root, args.diagnostics_output), diagnostic_payload + "\n")
    except (OSError, ValueError) as exc:
        print(f"Unable to write analyzer output: {exc}", file=sys.stderr)
        return 2

    if args.fail_on_partial and result.coverage == "partial":
        print(payload)
        return 3
    counts: Dict[str, int] = {}
    if not args.dry_run:
        try:
            counts = await _write_graph(args, result)
        except Exception as exc:
            print(f"Perl graph persistence failed: {exc}", file=sys.stderr)
            return 4
        try:
            vector_count = _sync_vectors(args, result)
        except Exception as exc:
            print(f"Perl vector persistence failed: {exc}", file=sys.stderr)
            return 5
    else:
        vector_count = 0
    if args.dry_run or args.pretty or not counts:
        print(payload)
    print(
        f"[SCAN_RESULT] parser=perl files={len(result.files)} "
        f"functions={sum(1 for item in result.symbols if item.kind == 'subroutine')} "
        f"classes={sum(1 for item in result.symbols if item.kind == 'package')} "
        f"vectors={vector_count} vector_status="
        f"{'success' if vector_configured(args.qdrant_url) and not args.dry_run else 'disabled'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

from __future__ import annotations


try:
    from tools.common.scan_ignore import matches_extra_ignore
except Exception:  # standalone use outside code-tiny — no extra ignores
    def matches_extra_ignore(_name: str) -> bool:
        return False

import argparse
import asyncio
import concurrent.futures
import gc
import json
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import torch

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.harness_config import load_harness_config

from tools.common.analyzer_cache import file_signature, load_parse_cache, safe_cache_root, write_parse_cache
from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files, cleanup_qdrant_with_writer
from tools.common.message_scan import default_message_collection_name, run_message_scan_pipeline
from tools.common.project_scope import enrich_project_scope
from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args
from tools.graph.writer.language_writer import LanguageCodeWriter
from tools.vb.vb_common import (
    PARSE_CACHE_VERSION,
    asdict_class,
    asdict_constant,
    asdict_enum,
    asdict_event,
    asdict_file,
    asdict_function,
    asdict_interface,
    asdict_namespace,
    asdict_property,
    asdict_variable,
    dataclass_from_payload,
    get_vb6_parser,
    get_vba_parser,
    get_vbnet_parser,
    get_vbscript_parser,
    parse_vb_file,
    resolve_calls,
)
from tools.vb.vb6_antlr_adapter import (
    ensure_worker_built as ensure_vb6_antlr_worker_built,
    parse_vb6_files_with_antlr,
)
from tools.common.call_evidence import callsite_site_id
from tools.vb.vb6_resolver import resolve_vb6_calls
from tools.vb.vb_path_classifier import VBPathClassifier
from tools.vb.vb_roslyn_adapter import parse_vbnet_files_with_roslyn


def _stable_point_id(symbol_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, symbol_id))


_PARSER_FACTORY: Dict[str, Callable[[], Any]] = {
    "vbnet": get_vbnet_parser,
    "vb6": get_vb6_parser,
    "vba": get_vba_parser,
    "vbscript": get_vbscript_parser,
}

_SOURCE_EXTS: Dict[str, Tuple[str, ...]] = {
    "vbnet": (".vb",),
    "vb6": (".bas", ".cls", ".frm", ".ctl", ".pag"),
    "vba": (".bas", ".cls", ".frm"),
    "vbscript": (".vbs", ".wsf", ".asp", ".hta"),
}

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".cache",
    "build",
    "dist",
    "out",
    "target",
}


def _scan_vb_files(root: str, dialect: str) -> List[str]:
    classifier = VBPathClassifier(root)
    exts = set(_SOURCE_EXTS.get(dialect, ()))
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name not in _SKIP_DIRS and not name.startswith(".")
            and not matches_extra_ignore(name)
        ]
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/")
            ext = os.path.splitext(name.lower())[1]
            if exts and ext not in exts:
                continue
            parser = classifier.select_parser_for_path(rel)
            if parser == dialect:
                files.append(os.path.join(root, rel))
    return sorted(files)


def _resolve_embed_device(requested: str) -> str:
    if requested and requested.lower() not in {"", "auto"}:
        return requested
    if torch.cuda.is_available():
        return "cuda"
    mps_backend = getattr(torch.backends, "mps", None)
    if mps_backend and mps_backend.is_available():
        return "mps"
    return "cpu"


def _parse_single_file(
    abs_path: str,
    root: str,
    parser_factory: Callable[[], Parser],
    dialect: str,
    cache_root: str,
    use_cache: bool,
    vbnet_parser_engine: str = "auto",
    vbnet_semantic: str = "auto",
    fallback_reason: str = "",
) -> Optional[Dict[str, Any]]:
    """Parse a single file (thread-safe)."""
    from tools.vb.vb_common import (
        CallEdge,
        ClassDef,
        ConstantDef,
        EnumDef,
        EventDef,
        FileDef,
        FunctionDef,
        InterfaceDef,
        NamespaceDef,
        PropertyDef,
        RelationEdge,
    )

    rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
    signature = file_signature(abs_path)

    payload = None
    if use_cache:
        cached = load_parse_cache(cache_root, rel_path, signature)
        if cached and cached.get("parse_cache_version") == PARSE_CACHE_VERSION:
            payload = cached

    if payload is None:
        try:
            (
                functions,
                calls,
                classes,
                namespaces,
                relations,
                properties,
                events,
                interfaces,
                enums,
                constants,
                variables,
                file_def,
                parse_meta,
            ) = parse_vb_file(
                abs_path,
                root,
                parser_factory,
                dialect,
                vbnet_parser_engine=vbnet_parser_engine,
                vbnet_semantic=vbnet_semantic,
                fallback_reason=fallback_reason,
            )
            payload = {
                "functions": [f.__dict__ for f in functions],
                "calls": [c.__dict__ for c in calls],
                "classes": [c.__dict__ for c in classes],
                "namespaces": [n.__dict__ for n in namespaces],
                "relations": [r.__dict__ for r in relations],
                "properties": [p.__dict__ for p in properties],
                "events": [e.__dict__ for e in events],
                "interfaces": [i.__dict__ for i in interfaces],
                "enums": [e.__dict__ for e in enums],
                "constants": [c.__dict__ for c in constants],
                "variables": [v.__dict__ for v in variables],
                "file_def": file_def.__dict__,
                "parse_meta": parse_meta,
                "parse_cache_version": PARSE_CACHE_VERSION,
            }
        except Exception:
            # Skip files that fail to parse
            return None

    return payload


async def _parse_files_parallel(
    files: List[str],
    root: str,
    parse_fn: Callable[[], Parser],
    dialect: str,
    cache_dir: Optional[str],
    parse_cache: bool,
    vbnet_parser_engine: str = "auto",
    vbnet_semantic: str = "auto",
    verbose: bool = False,
    max_workers: int = 4,
) -> List[Dict[str, Any]]:
    """Parse multiple files in parallel using thread pool."""
    parse_cache_root = safe_cache_root(cache_dir, f"{dialect}_analyzer", project_root=root)

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        tasks = [
            loop.run_in_executor(
                executor,
                _parse_single_file,
                abs_path,
                root,
                parse_fn,
                dialect,
                parse_cache_root,
                parse_cache,
                vbnet_parser_engine,
                vbnet_semantic,
            )
            for abs_path in files
        ]
        total = len(tasks)
        payloads: List[Dict[str, Any]] = []
        completed = 0
        progress_step = max(1, total // 20) if total > 0 else 1
        for task in asyncio.as_completed(tasks):
            payload = await task
            completed += 1
            if payload is not None:
                payloads.append(payload)
            if verbose and (completed == 1 or completed % progress_step == 0 or completed == total):
                skipped = completed - len(payloads)
                print(
                    f"[parse][progress] parser={dialect} completed={completed}/{total} ok={len(payloads)} skipped={skipped}",
                    flush=True,
                )

    return payloads


def _payload_from_parsed(
    *,
    functions: List[Any],
    calls: List[Any],
    classes: List[Any],
    namespaces: List[Any],
    relations: List[Any],
    properties: List[Any],
    events: List[Any],
    interfaces: List[Any],
    enums: List[Any],
    constants: List[Any],
    variables: List[Any],
    file_def: Any,
    parse_meta: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "functions": [f.__dict__ for f in functions],
        "calls": [c.__dict__ for c in calls],
        "classes": [c.__dict__ for c in classes],
        "namespaces": [n.__dict__ for n in namespaces],
        "relations": [r.__dict__ for r in relations],
        "properties": [p.__dict__ for p in properties],
        "events": [e.__dict__ for e in events],
        "interfaces": [i.__dict__ for i in interfaces],
        "enums": [e.__dict__ for e in enums],
        "constants": [c.__dict__ for c in constants],
        "variables": [v.__dict__ for v in variables],
        "file_def": file_def.__dict__,
        "parse_meta": parse_meta,
        "parse_cache_version": PARSE_CACHE_VERSION,
    }


def _hydrate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    from tools.vb.vb_common import (
        CallEdge,
        ClassDef,
        ConstantDef,
        EnumDef,
        EventDef,
        FileDef,
        FunctionDef,
        InterfaceDef,
        NamespaceDef,
        PropertyDef,
        RelationEdge,
        VariableDef,
    )

    return {
        "functions": [dataclass_from_payload(FunctionDef, item) for item in payload.get("functions", [])],
        "calls": [dataclass_from_payload(CallEdge, item) for item in payload.get("calls", [])],
        "classes": [dataclass_from_payload(ClassDef, item) for item in payload.get("classes", [])],
        "namespaces": [dataclass_from_payload(NamespaceDef, item) for item in payload.get("namespaces", [])],
        "relations": [dataclass_from_payload(RelationEdge, item) for item in payload.get("relations", [])],
        "properties": [dataclass_from_payload(PropertyDef, item) for item in payload.get("properties", [])],
        "events": [dataclass_from_payload(EventDef, item) for item in payload.get("events", [])],
        "interfaces": [dataclass_from_payload(InterfaceDef, item) for item in payload.get("interfaces", [])],
        "enums": [dataclass_from_payload(EnumDef, item) for item in payload.get("enums", [])],
        "constants": [dataclass_from_payload(ConstantDef, item) for item in payload.get("constants", [])],
        "variables": [dataclass_from_payload(VariableDef, item) for item in payload.get("variables", [])],
        "file_def": dataclass_from_payload(FileDef, payload.get("file_def", {})),
        "parse_meta": dict(payload.get("parse_meta", {})),
        "parse_cache_version": payload.get("parse_cache_version", ""),
    }


def _ensure_parse_meta_defaults(
    payload: Dict[str, Any],
    *,
    parser_engine: str,
    requested_engine: str,
    semantic_mode: str,
    fallback_reason: str = "",
    workspace_kind: str = "none",
    solution_or_project_path: str = "",
    semantic_enabled: bool = False,
    semantic_errors: Optional[List[str]] = None,
) -> None:
    parse_meta = dict(payload.get("parse_meta", {}) or {})
    parse_meta.setdefault("parser_language", "vbnet_roslyn" if parser_engine == "roslyn" else "vbnet_tree_sitter")
    parse_meta.setdefault("parse_cache_version", PARSE_CACHE_VERSION)
    parse_meta.setdefault("has_error", False)
    parse_meta.setdefault("error_nodes", 0)
    parse_meta.setdefault("line_count", 0)
    parse_meta["parser_engine"] = parser_engine
    parse_meta["semantic_mode"] = semantic_mode
    parse_meta["semantic_enabled"] = bool(parse_meta.get("semantic_enabled", semantic_enabled))
    parse_meta["fallback_reason"] = fallback_reason
    parse_meta.setdefault("worker_elapsed_ms", 0)
    parse_meta["workspace_kind"] = str(parse_meta.get("workspace_kind", workspace_kind) or workspace_kind)
    parse_meta["solution_or_project_path"] = str(
        parse_meta.get("solution_or_project_path", solution_or_project_path) or solution_or_project_path
    )
    parse_meta["semantic_errors"] = list(semantic_errors or parse_meta.get("semantic_errors") or [])
    parse_meta.setdefault("resolution_source", "semantic" if semantic_enabled else "syntax")
    parse_meta["requested_engine"] = requested_engine
    payload["parse_meta"] = parse_meta
    payload["parse_cache_version"] = PARSE_CACHE_VERSION


def _is_valid_payload_shape(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    required = ("functions", "calls", "classes", "file_def", "parse_meta")
    return all(key in payload for key in required)


async def _parse_vbnet_with_roslyn_batch(
    *,
    parse_files: List[str],
    root: str,
    parse_fn: Callable[[], Parser],
    cache_dir: Optional[str],
    parse_cache: bool,
    vbnet_parser_engine: str,
    vbnet_semantic: str,
    vbnet_roslyn_worker_project: Optional[str],
    vbnet_roslyn_timeout_sec: float,
    vbnet_roslyn_workspace_timeout_ms: int,
    vbnet_roslyn_file_timeout_ms: int,
    verbose: bool,
) -> List[Dict[str, Any]]:
    parse_cache_root = safe_cache_root(cache_dir, "vbnet_analyzer", project_root=root)
    payload_by_rel: Dict[str, Dict[str, Any]] = {}
    misses: List[Tuple[str, str, Dict[str, int]]] = []

    for abs_path in parse_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        signature = file_signature(abs_path)
        if parse_cache:
            cached = load_parse_cache(parse_cache_root, rel_path, signature)
            if cached and cached.get("parse_cache_version") == PARSE_CACHE_VERSION:
                payload_by_rel[rel_path] = cached
                continue
        misses.append((rel_path, abs_path, signature))

    worker_meta: Dict[str, Any] = {
        "workspace_kind": "none",
        "solution_or_project_path": "",
        "semantic_enabled": False,
        "semantic_errors": [],
    }
    roslyn_payloads: Dict[str, Dict] = {}
    roslyn_errors: Dict[str, str] = {}
    roslyn_batch_error = ""

    if misses:
        if verbose:
            print(
                f"[parse][engine] parser=vbnet engine=roslyn semantic={vbnet_semantic} files={len(misses)}",
                flush=True,
            )
        try:
            roslyn_payloads, roslyn_errors, worker_meta = parse_vbnet_files_with_roslyn(
                root=root,
                files=[item[1] for item in misses],
                semantic_mode=vbnet_semantic,
                worker_project_path=vbnet_roslyn_worker_project,
                timeout_sec=vbnet_roslyn_timeout_sec,
                workspace_timeout_ms=vbnet_roslyn_workspace_timeout_ms,
                file_timeout_ms=vbnet_roslyn_file_timeout_ms,
                parse_cache_version=PARSE_CACHE_VERSION,
                verbose=verbose,
            )
        except Exception as exc:
            roslyn_batch_error = str(exc)
            if verbose:
                print(f"[parse][fallback] parser=vbnet reason=batch_error detail={roslyn_batch_error}", flush=True)

    for rel_path, abs_path, signature in misses:
        payload = roslyn_payloads.get(rel_path)
        fallback_reason = ""
        if not payload or not _is_valid_payload_shape(payload):
            fallback_reason = roslyn_errors.get(rel_path) or roslyn_batch_error or "roslyn_payload_missing_or_invalid"
            if verbose:
                print(f"[parse][fallback] parser=vbnet file={rel_path} reason={fallback_reason}", flush=True)
            (
                functions,
                calls,
                classes,
                namespaces,
                relations,
                properties,
                events,
                interfaces,
                enums,
                constants,
                variables,
                file_def,
                parse_meta,
            ) = parse_vb_file(
                abs_path,
                root,
                parse_fn,
                "vbnet",
                vbnet_parser_engine="regex",
                vbnet_semantic=vbnet_semantic,
                fallback_reason=fallback_reason,
            )
            payload = _payload_from_parsed(
                functions=functions,
                calls=calls,
                classes=classes,
                namespaces=namespaces,
                relations=relations,
                properties=properties,
                events=events,
                interfaces=interfaces,
                enums=enums,
                constants=constants,
                variables=variables,
                file_def=file_def,
                parse_meta=parse_meta,
            )
            _ensure_parse_meta_defaults(
                payload,
                parser_engine="regex",
                requested_engine=vbnet_parser_engine,
                semantic_mode=vbnet_semantic,
                fallback_reason=fallback_reason,
                workspace_kind=str(worker_meta.get("workspace_kind") or "none"),
                solution_or_project_path=str(worker_meta.get("solution_or_project_path") or ""),
                semantic_enabled=False,
                semantic_errors=list(worker_meta.get("semantic_errors") or []),
            )
        else:
            _ensure_parse_meta_defaults(
                payload,
                parser_engine="roslyn",
                requested_engine=vbnet_parser_engine,
                semantic_mode=vbnet_semantic,
                workspace_kind=str(worker_meta.get("workspace_kind") or "none"),
                solution_or_project_path=str(worker_meta.get("solution_or_project_path") or ""),
                semantic_enabled=bool(worker_meta.get("semantic_enabled", False)),
                semantic_errors=list(worker_meta.get("semantic_errors") or []),
            )

        payload_by_rel[rel_path] = payload
        if parse_cache:
            write_parse_cache(parse_cache_root, rel_path, signature, payload)

    hydrated: List[Dict[str, Any]] = []
    for abs_path in parse_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        payload = payload_by_rel.get(rel_path)
        if payload:
            hydrated.append(_hydrate_payload(payload))
    return hydrated


async def _parse_vb6_with_antlr_batch(
    *,
    parse_files: List[str],
    all_source_files: List[str],
    root: str,
    parse_fn: Callable[[], Parser],
    cache_dir: Optional[str],
    parse_cache: bool,
    vb6_parser_engine: str,
    vb6_antlr_timeout_sec: float,
    vb6_antlr_workspace_timeout_ms: int,
    verbose: bool,
) -> List[Dict[str, Any]]:
    """VB6 whole-program ANTLR batch with per-file regex fallback cascade.

    AD-02 (review checklist item): the worker ALWAYS receives the complete
    project file list — ``all_source_files``, never just the cache-miss or
    changed files — because cross-module resolution inside the worker's single
    Program needs every module. The parse cache is used only to serve/refresh
    per-file OUTPUT payloads; it must never shrink the worker input.
    """

    parse_cache_root = safe_cache_root(cache_dir, "vb6_analyzer", project_root=root)
    payload_by_rel: Dict[str, Dict[str, Any]] = {}
    signatures: Dict[str, Dict[str, int]] = {}

    for abs_path in parse_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        signatures[rel_path] = file_signature(abs_path)

    worker_payloads: Dict[str, Dict] = {}
    worker_errors: Dict[str, str] = {}
    worker_meta: Dict[str, Any] = {
        "workspace_kind": "none",
        "solution_or_project_path": "",
    }
    worker_batch_error = ""

    if all_source_files:
        if verbose:
            print(
                f"[parse][engine] parser=vb6 engine=antlr files={len(all_source_files)} "
                f"(requested={len(parse_files)})",
                flush=True,
            )
        try:
            worker_payloads, worker_errors, worker_meta = parse_vb6_files_with_antlr(
                root=root,
                files=all_source_files,
                timeout_sec=vb6_antlr_timeout_sec,
                workspace_timeout_ms=vb6_antlr_workspace_timeout_ms,
                parse_cache_version=PARSE_CACHE_VERSION,
                verbose=verbose,
            )
        except Exception as exc:
            worker_batch_error = str(exc)
            if verbose:
                print(f"[parse][fallback] parser=vb6 reason=batch_error detail={worker_batch_error}", flush=True)

    for abs_path in parse_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        signature = signatures[rel_path]
        payload = worker_payloads.get(rel_path)
        fallback_reason = ""

        if payload is None or not _is_valid_payload_shape(payload):
            fallback_reason = (
                worker_errors.get(rel_path)
                or worker_batch_error
                or "vb6_antlr_payload_missing_or_invalid"
            )
            # a fresh worker miss may still be served from cache (recovery
            # path only — the worker already ran over the whole project)
            if parse_cache:
                cached = load_parse_cache(parse_cache_root, rel_path, signature)
                if cached and cached.get("parse_cache_version") == PARSE_CACHE_VERSION:
                    payload = cached
                    fallback_reason = ""
        if payload is None or not _is_valid_payload_shape(payload):
            if verbose:
                print(f"[parse][fallback] parser=vb6 file={rel_path} reason={fallback_reason}", flush=True)
            (
                functions,
                calls,
                classes,
                namespaces,
                relations,
                properties,
                events,
                interfaces,
                enums,
                constants,
                variables,
                file_def,
                parse_meta,
            ) = parse_vb_file(
                abs_path,
                root,
                parse_fn,
                "vb6",
                fallback_reason=fallback_reason,
            )
            payload = _payload_from_parsed(
                functions=functions,
                calls=calls,
                classes=classes,
                namespaces=namespaces,
                relations=relations,
                properties=properties,
                events=events,
                interfaces=interfaces,
                enums=enums,
                constants=constants,
                variables=variables,
                file_def=file_def,
                parse_meta=parse_meta,
            )
            _ensure_parse_meta_defaults(
                payload,
                parser_engine="regex",
                requested_engine=vb6_parser_engine,
                semantic_mode="off",
                fallback_reason=fallback_reason,
                workspace_kind=str(worker_meta.get("workspace_kind") or "none"),
                solution_or_project_path=str(worker_meta.get("solution_or_project_path") or ""),
            )
        else:
            _ensure_parse_meta_defaults(
                payload,
                parser_engine="antlr",
                requested_engine=vb6_parser_engine,
                semantic_mode="off",
                workspace_kind=str(worker_meta.get("workspace_kind") or "vbp"),
                solution_or_project_path=str(worker_meta.get("solution_or_project_path") or ""),
            )
            if worker_meta.get("implements_map") is not None:
                payload["parse_meta"].setdefault("project_implements_map", worker_meta["implements_map"])

        payload_by_rel[rel_path] = payload
        if parse_cache:
            write_parse_cache(parse_cache_root, rel_path, signature, payload)

    hydrated: List[Dict[str, Any]] = []
    for abs_path in parse_files:
        rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
        payload = payload_by_rel.get(rel_path)
        if payload:
            hydrated.append(_hydrate_payload(payload))
    return hydrated


def _vb6_external_symbol_name(call: Any) -> str:
    """Display name for a placeholder external/late-bound callee."""

    member = str(getattr(call, "callee_member", "") or "").strip()
    if member:
        return member
    name = str(getattr(call, "callee_name", "") or "").strip()
    return name.rsplit(".", 1)[-1].lstrip(".") or name or "unknown"


def _vb6_external_symbol_id(call: Any) -> str:
    """Stable per-project placeholder Function id (plan 4.4 option a)."""

    return f"external::vb6/{_vb6_external_symbol_name(call).lower()}"


async def build_call_graph(
    root: str,
    *,
    dialect: str,
    code_writer: Optional[LanguageCodeWriter],
    qdrant_writer: Optional[Any],
    embedder: Optional[Any],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    cache_dir: Optional[str],
    parse_cache: bool,
    incremental: bool,
    changed_files: Sequence[str],
    deleted_files: Sequence[str],
    verbose: bool,
    embed_batch_size: int,
    qdrant_batch_size: int,
    parallel_workers: int = 4,
    vbnet_parser_engine: str = "auto",
    vbnet_semantic: str = "auto",
    vbnet_roslyn_worker_project: Optional[str] = None,
    vbnet_roslyn_timeout_sec: float = 600.0,
    vbnet_roslyn_workspace_timeout_ms: int = 120000,
    vbnet_roslyn_file_timeout_ms: int = 60000,
    vb6_parser_engine: str = "auto",
    vb6_antlr_timeout_sec: float = 600.0,
    vb6_antlr_workspace_timeout_ms: int = 300000,
) -> None:
    start_time = time.time()

    all_source_files = _scan_vb_files(root, dialect)
    changed_set = {item.replace("\\", "/") for item in changed_files}
    deleted_set = {item.replace("\\", "/") for item in deleted_files}

    if incremental:
        parse_files = [
            path
            for path in all_source_files
            if os.path.relpath(path, root).replace("\\", "/") in changed_set
        ]
        if dialect == "vb6":
            # AD-09: incremental sync must be able to rebuild INCOMING edges
            # from unchanged callers into changed files. The ANTLR batch runs
            # over the whole project anyway (AD-02), so keep every payload and
            # filter only node writes/embedding by the changed set below.
            parse_files = all_source_files
    else:
        parse_files = all_source_files

    cleanup_paths = sorted(changed_set | deleted_set)
    if cleanup_paths and code_writer:
        await cleanup_neo4j_for_files(
            driver=code_writer.driver,
            database=code_writer.database,
            project_id=project_id,
            file_paths=cleanup_paths,
            verbose=verbose,
        )
    if cleanup_paths and qdrant_writer:
        cleanup_qdrant_with_writer(
            writer=qdrant_writer,
            project_id=project_id,
            file_paths=cleanup_paths,
            verbose=verbose,
        )

    parse_fn = _PARSER_FACTORY[dialect]
    if verbose:
        print(
            f"[parse][start] parser={dialect} files={len(parse_files)} parallel_workers={parallel_workers} cache={'on' if parse_cache else 'off'}",
            flush=True,
        )

    # vb6 engine resolution (AD-07): auto = antlr when java+worker are ready,
    # else regex with ONE loud warning — never a silent degrade.
    vb6_engine_effective = vb6_parser_engine
    if dialect == "vb6" and vb6_parser_engine == "auto":
        try:
            ensure_vb6_antlr_worker_built(verbose=verbose)
            vb6_engine_effective = "antlr"
        except Exception as exc:
            vb6_engine_effective = "regex"
            print(
                f"[vb6][engine] antlr unavailable ({str(exc).splitlines()[0][:200]}), falling back to regex",
                flush=True,
            )
    if dialect == "vb6" and vb6_parser_engine == "antlr":
        # explicit engine: fail loudly if the worker cannot be built
        ensure_vb6_antlr_worker_built(verbose=verbose)
    if dialect == "vbnet" and verbose:
        engine_for_run = "regex" if vbnet_parser_engine == "regex" else "roslyn"
        print(
            f"[parse][engine] parser=vbnet engine={engine_for_run} semantic={vbnet_semantic}",
            flush=True,
        )

    # VB6 ANTLR path (plan 260917-1200): whole-program worker with per-file
    # regex fallback cascade; worker input is always the full project (AD-02).
    if dialect == "vb6" and vb6_engine_effective == "antlr":
        payloads = await _parse_vb6_with_antlr_batch(
            parse_files=parse_files,
            all_source_files=all_source_files,
            root=root,
            parse_fn=parse_fn,
            cache_dir=cache_dir,
            parse_cache=parse_cache,
            vb6_parser_engine=vb6_parser_engine,
            vb6_antlr_timeout_sec=vb6_antlr_timeout_sec,
            vb6_antlr_workspace_timeout_ms=vb6_antlr_workspace_timeout_ms,
            verbose=verbose,
        )
    # Roslyn path for VB.NET (phase A/B): use batch worker then fallback to regex per file.
    elif dialect == "vbnet" and vbnet_parser_engine != "regex":
        payloads = await _parse_vbnet_with_roslyn_batch(
            parse_files=parse_files,
            root=root,
            parse_fn=parse_fn,
            cache_dir=cache_dir,
            parse_cache=parse_cache,
            vbnet_parser_engine=vbnet_parser_engine,
            vbnet_semantic=vbnet_semantic,
            vbnet_roslyn_worker_project=vbnet_roslyn_worker_project,
            vbnet_roslyn_timeout_sec=vbnet_roslyn_timeout_sec,
            vbnet_roslyn_workspace_timeout_ms=vbnet_roslyn_workspace_timeout_ms,
            vbnet_roslyn_file_timeout_ms=vbnet_roslyn_file_timeout_ms,
            verbose=verbose,
        )
    # Use parallel processing for non-vbnet or forced regex mode.
    elif parallel_workers > 1:
        payloads_raw = await _parse_files_parallel(
            files=parse_files,
            root=root,
            parse_fn=parse_fn,
            dialect=dialect,
            cache_dir=cache_dir,
            parse_cache=parse_cache,
            vbnet_parser_engine=vbnet_parser_engine,
            vbnet_semantic=vbnet_semantic,
            verbose=verbose,
            max_workers=parallel_workers,
        )
        payloads = [_hydrate_payload(item) for item in payloads_raw]
    else:
        parse_cache_root = safe_cache_root(cache_dir, f"{dialect}_analyzer", project_root=root)
        # Sequential processing
        payloads: List[Dict[str, Any]] = []
        for abs_path in parse_files:
            rel_path = os.path.relpath(abs_path, root).replace("\\", "/")
            signature = file_signature(abs_path)
            payload_raw: Optional[Dict[str, Any]] = None
            if parse_cache:
                cached = load_parse_cache(parse_cache_root, rel_path, signature)
                if cached and cached.get("parse_cache_version") == PARSE_CACHE_VERSION:
                    payload_raw = cached
            if payload_raw is None:
                (
                    functions,
                    calls,
                    classes,
                    namespaces,
                    relations,
                    properties,
                    events,
                    interfaces,
                    enums,
                    constants,
                    variables,
                    file_def,
                    parse_meta,
                ) = parse_vb_file(
                    abs_path,
                    root,
                    parse_fn,
                    dialect,
                    vbnet_parser_engine=vbnet_parser_engine,
                    vbnet_semantic=vbnet_semantic,
                )
                payload_raw = _payload_from_parsed(
                    functions=functions,
                    calls=calls,
                    classes=classes,
                    namespaces=namespaces,
                    relations=relations,
                    properties=properties,
                    events=events,
                    interfaces=interfaces,
                    enums=enums,
                    constants=constants,
                    variables=variables,
                    file_def=file_def,
                    parse_meta=parse_meta,
                )
                if parse_cache:
                    write_parse_cache(parse_cache_root, rel_path, signature, payload_raw)
            payloads.append(_hydrate_payload(payload_raw))

    if verbose:
        print(
            f"[parse][done] parser={dialect} parsed={len(payloads)}/{len(parse_files)}",
            flush=True,
        )

    all_functions = [func for payload in payloads for func in payload["functions"]]
    all_calls = [call for payload in payloads for call in payload["calls"]]
    if dialect == "vb6":
        resolve_vb6_calls(all_functions, all_calls, payloads=payloads, verbose=verbose)
    else:
        resolve_calls(all_functions, all_calls)

    # M6: engine + resolution summary is ALWAYS printed (never verbose-only)
    if dialect == "vb6":
        engine_counts: Dict[str, int] = {}
        fallback_count = 0
        for payload in payloads:
            meta = payload.get("parse_meta", {}) or {}
            engine = str(meta.get("parser_engine") or "regex")
            if meta.get("fallback_reason"):
                engine = f"{engine}_fallback"
                fallback_count += 1
            engine_counts[engine] = engine_counts.get(engine, 0) + 1
        engine_label = "+".join(
            f"{name}({count})" if len(engine_counts) > 1 else name
            for name, count in sorted(engine_counts.items())
        )
        total_calls = len(all_calls)
        resolved_calls = sum(1 for call in all_calls if call.callee_id)
        possible_calls = sum(
            1 for call in all_calls
            if call.resolution_status in {"ambiguous", "late_bound", "external", "unresolved"}
        )
        rate = 100.0 * resolved_calls / total_calls if total_calls > 0 else 0.0
        print(
            f"[vb6][summary] engine={engine_label} files={len(payloads)} fallback={fallback_count} "
            f"callsites={total_calls} resolved={resolved_calls} possible={possible_calls} rate={rate:.1f}%",
            flush=True,
        )

    # Debug: print call resolution stats
    if verbose:
        total_calls = len(all_calls)
        resolved_calls = sum(1 for call in all_calls if call.callee_id)
        print(f"[vb] calls: {total_calls} total, {resolved_calls} resolved ({100*resolved_calls/total_calls if total_calls > 0 else 0:.1f}%)")

    if code_writer:
        # Ensure indexes exist before writing data (critical for performance)
        index_specs = [
            {"label": "Function", "property": "id"},
            {"label": "File", "property": "id"},
            {"label": "Class", "property": "id"},
            {"label": "Namespace", "property": "id"},
            {"label": "Property", "property": "id"},
            {"label": "Event", "property": "id"},
            {"label": "Interface", "property": "id"},
            {"label": "Enum", "property": "id"},
            {"label": "Constant", "property": "id"},
            {"label": "Variable", "property": "id"},
        ]
        try:
            await code_writer.driver.create_indexes(index_specs, database=code_writer.database)
        except Exception as exc:
            if verbose:
                print(f"[graph] index ensure skipped: {exc}")

        projects = [{"id": project_id, "name": project_name, "language": language, "repo": repo, "root": root, "build_system": build_system}]
        files_rows: List[Dict[str, Any]] = []
        namespaces_rows: List[Dict[str, Any]] = []
        types_rows: List[Dict[str, Any]] = []
        functions_rows: List[Dict[str, Any]] = []
        relations_rows: List[Dict[str, Any]] = []
        calls_rows: List[Dict[str, Any]] = []
        possible_rows: List[Dict[str, Any]] = []
        external_symbol_names: set = set()
        properties_rows: List[Dict[str, Any]] = []
        events_rows: List[Dict[str, Any]] = []
        interfaces_rows: List[Dict[str, Any]] = []
        enums_rows: List[Dict[str, Any]] = []
        constants_rows: List[Dict[str, Any]] = []
        variables_rows: List[Dict[str, Any]] = []

        semantic_provider = "vb6_antlr_worker"
        if any(
            str((payload.get("parse_meta") or {}).get("parser_engine") or "regex") == "regex"
            for payload in payloads
        ):
            semantic_provider = "vb6_regex"

        # VB6 (plan 260917-1200): incremental syncs keep whole-project payloads
        # (AD-09) — only node writes and embedding are filtered to the changed
        # set; edges are selected source-or-target below.
        vb6_node_filter = None
        if dialect == "vb6" and incremental:
            vb6_node_filter = changed_set

        def _vb6_write_file(payload_file_path: str) -> bool:
            if vb6_node_filter is None:
                return True
            return payload_file_path.replace("\\", "/") in vb6_node_filter

        for payload in payloads:
            file_def = payload["file_def"]
            payload_rel = str(file_def.file_path or "").replace("\\", "/")
            if not _vb6_write_file(payload_rel):
                continue
            file_row = asdict_file(file_def, project_id, project_name, language, repo, build_system)
            files_rows.append(file_row)
            relations_rows.append({"source_id": project_id, "target_id": file_row["id"], "rel_type": "CONTAINS", "properties": {}})

            for ns in payload["namespaces"]:
                row = asdict_namespace(ns, project_id, project_name, language, repo, build_system)
                namespaces_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for cls in payload["classes"]:
                row = asdict_class(cls, project_id, project_name, language, repo, build_system)
                types_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})
                if dialect == "vb6":
                    # a VB6 class/form module contains every procedure in it
                    # (labels are explicit because class nodes ride the types
                    # lane and may be absent from an incremental batch)
                    for fn in payload["functions"]:
                        relations_rows.append({
                            "source_id": row["id"],
                            "source_label": "Type",
                            "target_id": fn.symbol_id,
                            "target_label": "Function",
                            "rel_type": "CONTAINS",
                            "properties": {},
                        })

            for fn in payload["functions"]:
                row = asdict_function(fn, project_id, project_name, language, repo, build_system)
                functions_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for rel in payload["relations"]:
                relations_rows.append({
                    "source_id": rel.source_id,
                    "target_id": rel.target_id,
                    "rel_type": rel.rel_type,
                    "properties": dict(rel.properties),
                })

            for call in payload["calls"]:
                if dialect != "vb6":
                    if call.callee_id:
                        calls_rows.append({
                            "caller_id": call.caller_id,
                            "callee_id": call.callee_id,
                            "call_type": "call_expression",
                        })

            for prop in payload.get("properties", []):
                row = asdict_property(prop, project_id, project_name, language, repo, build_system)
                properties_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for event in payload.get("events", []):
                row = asdict_event(event, project_id, project_name, language, repo, build_system)
                events_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for iface in payload.get("interfaces", []):
                row = asdict_interface(iface, project_id, project_name, language, repo, build_system)
                interfaces_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for enum in payload.get("enums", []):
                row = asdict_enum(enum, project_id, project_name, language, repo, build_system)
                enums_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for const in payload.get("constants", []):
                row = asdict_constant(const, project_id, project_name, language, repo, build_system)
                constants_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

            for var in payload.get("variables", []):
                row = asdict_variable(var, project_id, project_name, language, repo, build_system)
                variables_rows.append(row)
                relations_rows.append({"source_id": file_row["id"], "target_id": row["id"], "rel_type": "CONTAINS", "properties": {}})

        if dialect == "vb6":
            # VB6 two-tier publication (plan 4.4): deterministic targets become
            # CALLS; every weak call survives as POSSIBLE_CALLS. This loop runs
            # over ALL payloads — including unchanged files — because AD-09
            # re-publishes edges by source OR target, and incoming edges from
            # unchanged callers must be rebuilt after incremental cleanup.
            for payload in payloads:
                edge_file = str(payload["file_def"].file_path or "").replace("\\", "/")
                for call in payload["calls"]:
                    if call.callee_id and call.resolution_status != "ambiguous":
                        calls_rows.append({
                            "project_id": project_id,
                            "caller_id": call.caller_id,
                            "callee_id": call.callee_id,
                            "call_type": call.call_type or "call_expression",
                            "resolution_status": call.resolution_status,
                        })
                        continue
                    targets = call.candidate_ids or []
                    if not targets:
                        targets = [_vb6_external_symbol_id(call)]
                        external_symbol_names.add(_vb6_external_symbol_name(call))
                    for target_id in targets:
                        # site identity is callee-INDEPENDENT (one callsite, n
                        # candidate rows share the id; uuid5 over
                        # caller/file/line/column/type — same key shape as
                        # callsite_site_id with an empty callee)
                        possible_rows.append({
                            "project_id": project_id,
                            "caller_id": call.caller_id,
                            "callee_id": target_id,
                            "site_id": callsite_site_id(
                                call.caller_id,
                                "",
                                edge_file,
                                call.call_line,
                                call.site_column,
                                call.call_type or "call",
                            ),
                            "props": {
                                "file_path": edge_file,
                                "line": call.call_line,
                                "column": call.site_column,
                                "arity": int(call.callee_arity or 0),
                                "call_type": call.call_type or "call_expression",
                                "callee_name": call.callee_name,
                                # free-text vb6 status (AD-04): never a custom
                                # resolution_class — standard vocabulary only
                                "resolution_status": call.resolution_status or "unresolved",
                                "resolution_class": "lexical_candidate",
                                "semantic_provider": semantic_provider,
                                "candidates": json.dumps(call.candidate_ids or [], ensure_ascii=True),
                            },
                        })

        if dialect == "vb6":
            # IMPLEMENTS edges (plan 4.5 / AD-10): Class -> Interface using the
            # worker's implements map and the emitted interface nodes.
            interface_ids: Dict[str, str] = {}
            interface_files: Dict[str, str] = {}
            for payload in payloads:
                payload_file = str(payload["file_def"].file_path or "").replace("\\", "/")
                for iface in payload.get("interfaces", []):
                    interface_ids[(iface.name or "").lower()] = iface.symbol_id
                    interface_files[(iface.name or "").lower()] = payload_file
            for payload in payloads:
                payload_file = str(payload["file_def"].file_path or "").replace("\\", "/")
                meta = payload.get("parse_meta") or {}
                implemented = list(meta.get("implements") or [])
                if not implemented or not payload["classes"]:
                    continue
                class_id = payload["classes"][0].symbol_id
                for iface_name in implemented:
                    iface_key = (iface_name or "").lower()
                    target = interface_ids.get(iface_key)
                    if not target:
                        continue
                    if vb6_node_filter is not None:
                        # AD-09 applies to IMPLEMENTS too: when either the
                        # implementing class OR the interface file changed,
                        # cleanup destroyed the edge and it must be re-published
                        iface_file = interface_files.get(iface_key, "")
                        if payload_file not in vb6_node_filter and iface_file not in vb6_node_filter:
                            continue
                    relations_rows.append({
                        "source_id": class_id,
                        "source_label": "Type",
                        "target_id": target,
                        "target_label": "Interface",
                        "rel_type": "IMPLEMENTS",
                        "properties": {},
                    })

            # placeholder external/late-bound targets (plan 4.4 option a):
            # POSSIBLE_CALLS is Function->Function, so weak callees without a
            # project target get a per-project external_symbol node.
            from tools.vb.vb_common import FunctionDef as _VbFunctionDef

            for name in sorted(external_symbol_names):
                placeholder = _VbFunctionDef(
                    symbol_id=f"external::vb6/{name.lower()}",
                    qualified_name=name,
                    name=name,
                    kind="external_symbol",
                    class_name=None,
                    namespace_name=None,
                    file_path="",
                    start_line=0,
                    end_line=0,
                    arity=-1,
                    code="",
                )
                functions_rows.append(
                    asdict_function(placeholder, project_id, project_name, language, repo, build_system)
                )

            if vb6_node_filter is not None:
                # AD-09 (red-team F1): re-publish every edge whose source OR
                # target file is in the changed set. Incremental cleanup DETACH
                # DELETEs nodes of changed files, which also destroys INCOMING
                # edges from unchanged callers — those must be rebuilt here.
                def _edge_file(symbol_id: str) -> str:
                    return symbol_id.rsplit("@", 1)[1] if "@" in symbol_id else ""

                calls_rows = [
                    row for row in calls_rows
                    if _edge_file(row["caller_id"]) in vb6_node_filter
                    or _edge_file(row["callee_id"]) in vb6_node_filter
                ]
                possible_rows = [
                    row for row in possible_rows
                    if _edge_file(row["caller_id"]) in vb6_node_filter
                    or _edge_file(row["callee_id"]) in vb6_node_filter
                ]

        await code_writer.write_all(
            projects=projects,
            namespaces=namespaces_rows or None,
            files=files_rows or None,
            types=types_rows or None,
            functions=functions_rows or None,
            properties=properties_rows or None,
            events=events_rows or None,
            interfaces=interfaces_rows or None,
            enums=enums_rows or None,
            constants=constants_rows or None,
            variables=variables_rows or None,
            relations=relations_rows or None,
            calls=calls_rows or None,
            use_full_writers=True,
            files_variant="with_imports",
        )

        if dialect == "vb6" and possible_rows:
            await code_writer.write_possible_calls_with_site(possible_rows)

        if dialect == "vb6":
            # placeholder lifecycle (red-team F8): drop external_symbol nodes
            # nothing points at anymore so they cannot accumulate per sync
            try:
                await code_writer.driver.execute_query(
                    """
                    MATCH (f:Function {kind: 'external_symbol'})
                    WHERE f.project_id = $project_id
                      AND NOT ()-[:POSSIBLE_CALLS]->(f)
                      AND NOT ()-[:CALLS]->(f)
                    DETACH DELETE f
                    RETURN count(f) AS pruned
                    """,
                    {"project_id": project_id},
                    code_writer.database,
                )
            except Exception:
                pass

        if verbose:
            print(f"[graph] write stats: {len(functions_rows)} functions, {len(calls_rows)} calls, {len(possible_rows)} possible_calls, {len(relations_rows)} relations")

    if qdrant_writer and embedder:
        qdrant_writer.ensure_collection()
        items: List[Tuple[str, Dict[str, Any]]] = []

        for payload in payloads:
            if dialect == "vb6" and incremental:
                payload_rel = str(payload["file_def"].file_path or "").replace("\\", "/")
                if payload_rel not in changed_set:
                    continue
            for fn in payload["functions"]:
                text = fn.note or fn.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(fn.symbol_id),
                            "payload": {
                                "node_type": "function",
                                "symbol_id": fn.symbol_id,
                                "qualified_name": fn.qualified_name,
                                "name": fn.name,
                                "kind": fn.kind,
                                "file_path": fn.file_path,
                                "start_line": fn.start_line,
                                "end_line": fn.end_line,
                                "comment": fn.comment,
                                "summary": fn.summary,
                                "note": fn.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            for cls in payload["classes"]:
                text = cls.note or cls.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(cls.symbol_id),
                            "payload": {
                                "node_type": "class",
                                "symbol_id": cls.symbol_id,
                                "qualified_name": cls.qualified_name,
                                "name": cls.name,
                                "kind": cls.kind,
                                "file_path": cls.file_path,
                                "start_line": cls.start_line,
                                "end_line": cls.end_line,
                                "comment": cls.comment,
                                "summary": cls.summary,
                                "note": cls.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            for prop in payload.get("properties", []):
                text = prop.note or prop.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(prop.symbol_id),
                            "payload": {
                                "node_type": "property",
                                "symbol_id": prop.symbol_id,
                                "qualified_name": prop.qualified_name,
                                "name": prop.name,
                                "kind": prop.kind,
                                "file_path": prop.file_path,
                                "start_line": prop.start_line,
                                "end_line": prop.end_line,
                                "parameters": prop.parameters,
                                "return_type": prop.return_type,
                                "comment": prop.comment,
                                "summary": prop.summary,
                                "note": prop.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            for event in payload.get("events", []):
                text = event.note or event.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(event.symbol_id),
                            "payload": {
                                "node_type": "event",
                                "symbol_id": event.symbol_id,
                                "qualified_name": event.qualified_name,
                                "name": event.name,
                                "file_path": event.file_path,
                                "start_line": event.start_line,
                                "end_line": event.end_line,
                                "parameters": event.parameters,
                                "comment": event.comment,
                                "summary": event.summary,
                                "note": event.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            for iface in payload.get("interfaces", []):
                text = iface.note or iface.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(iface.symbol_id),
                            "payload": {
                                "node_type": "interface",
                                "symbol_id": iface.symbol_id,
                                "qualified_name": iface.qualified_name,
                                "name": iface.name,
                                "file_path": iface.file_path,
                                "start_line": iface.start_line,
                                "end_line": iface.end_line,
                                "base_interfaces": iface.base_interfaces,
                                "comment": iface.comment,
                                "summary": iface.summary,
                                "note": iface.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            for enum in payload.get("enums", []):
                text = enum.note or enum.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(enum.symbol_id),
                            "payload": {
                                "node_type": "enum",
                                "symbol_id": enum.symbol_id,
                                "qualified_name": enum.qualified_name,
                                "name": enum.name,
                                "file_path": enum.file_path,
                                "start_line": enum.start_line,
                                "end_line": enum.end_line,
                                "members": enum.members,
                                "comment": enum.comment,
                                "summary": enum.summary,
                                "note": enum.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            for const in payload.get("constants", []):
                text = const.note or const.code or ""
                items.append(
                    (
                        text,
                        {
                            "id": _stable_point_id(const.symbol_id),
                            "payload": {
                                "node_type": "constant",
                                "symbol_id": const.symbol_id,
                                "qualified_name": const.qualified_name,
                                "name": const.name,
                                "file_path": const.file_path,
                                "line_number": const.line_number,
                                "value": const.value,
                                "type_name": const.type_name,
                                "comment": const.comment,
                                "summary": const.summary,
                                "note": const.note,
                                "project_id": project_id,
                                "project_name": project_name,
                                "language": language,
                                "repo": repo,
                                "build_system": build_system,
                            },
                        },
                    )
                )
            fd = payload["file_def"]
            items.append(
                (
                    fd.note or fd.summary or fd.code[:500],
                    {
                        "id": _stable_point_id(f"file::{fd.file_path}"),
                        "payload": {
                            "node_type": "file",
                            "symbol_id": f"file::{fd.file_path}",
                            "file_path": fd.file_path,
                            "comment": fd.comment,
                            "summary": fd.summary,
                            "note": fd.note,
                            "project_id": project_id,
                            "project_name": project_name,
                            "language": language,
                            "repo": repo,
                            "build_system": build_system,
                        },
                    },
                )
            )

        total_batches = (len(items) + qdrant_batch_size - 1) // qdrant_batch_size if items else 0
        for idx in range(0, len(items), qdrant_batch_size):
            batch = items[idx : idx + qdrant_batch_size]
            texts = [text for text, _ in batch]
            vectors = embedder.embed(texts, batch_size=embed_batch_size, verbose=False)
            points = []
            for (_, point), vector in zip(batch, vectors):
                points.append({"id": point["id"], "vector": vector, "payload": point["payload"]})
            qdrant_writer.upsert(enrich_project_scope(points))
            del texts, vectors, points, batch
            batch_no = (idx // qdrant_batch_size) + 1
            if batch_no % 20 == 0:
                gc.collect()
            if verbose:
                if batch_no == 1 or batch_no % 10 == 0 or batch_no == total_batches:
                    print(
                        f"[embed][progress] parser={dialect} batch={batch_no}/{total_batches} points={len(points)}",
                        flush=True,
                    )

    print(
        f"[SCAN_RESULT] parser={dialect} files={len(payloads)} functions={len(all_functions)} classes={sum(len(p['classes']) for p in payloads)}",
        flush=True,
    )
    if verbose:
        print(f"[done] Total time: {time.time() - start_time:.2f}s")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Visual Basic call graph analyzer")
    parser.add_argument("--dialect", required=True, choices=sorted(_PARSER_FACTORY.keys()))
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", default=None, help="Path to harness dev.json config (default: <root>/.cortext-harness/config/dev.json)")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    add_graph_provider_args(parser)
    parser.add_argument("--qdrant-url", default=os.environ.get("QDRANT_CODE_PATH"))
    parser.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", "vb_functions"))
    parser.add_argument(
        "--embed-model",
        default=os.environ.get("CODE_EMBEDDING_MODEL")
        or os.environ.get("JINA_MODEL_PATH")
        or "jinaai/jina-embeddings-v3",
    )
    parser.add_argument("--max-embed-chars", type=int, default=int(os.environ.get("MAX_EMBED_CHARS", 4000)))
    parser.add_argument("--chunk-embed", action="store_true")
    parser.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("EMBED_BATCH_SIZE", 8)))
    parser.add_argument("--neo4j-batch-size", type=int, default=1000)
    parser.add_argument("--qdrant-batch-size", type=int, default=128)
    parser.add_argument("--parallel-workers", type=int, default=4,
                       help="Number of parallel workers for file parsing (1 for sequential)")
    parser.add_argument(
        "--vbnet-parser-engine",
        choices=("auto", "roslyn", "regex"),
        default="auto",
        help="VB.NET parser engine selection (default: auto)",
    )
    parser.add_argument(
        "--vbnet-semantic",
        choices=("auto", "on", "off"),
        default="auto",
        help="VB.NET Roslyn semantic mode (default: auto)",
    )
    parser.add_argument(
        "--vbnet-roslyn-worker-project",
        default=os.environ.get("VBNET_ROSLYN_WORKER_PROJECT", ""),
        help="Path to Roslyn worker .csproj (optional)",
    )
    parser.add_argument(
        "--vbnet-roslyn-timeout-sec",
        type=float,
        default=os.environ.get("VBNET_ROSLYN_TIMEOUT_SEC", "600"),
        help="Timeout in seconds for Roslyn worker subprocess",
    )
    parser.add_argument(
        "--vbnet-roslyn-workspace-timeout-ms",
        type=int,
        default=os.environ.get("VBNET_ROSLYN_WORKSPACE_TIMEOUT_MS", "120000"),
        help="Timeout in ms for Roslyn workspace load",
    )
    parser.add_argument(
        "--vbnet-roslyn-file-timeout-ms",
        type=int,
        default=os.environ.get("VBNET_ROSLYN_FILE_TIMEOUT_MS", "60000"),
        help="Timeout in ms for each file parse in Roslyn worker",
    )
    parser.add_argument(
        "--vb6-parser-engine",
        choices=("auto", "antlr", "regex"),
        default=os.environ.get("VB6_PARSER_ENGINE", "auto"),
        help="VB6 parser engine selection: antlr (whole-program ProLeap worker), regex, or auto (default)",
    )
    parser.add_argument(
        "--vb6-antlr-timeout-sec",
        type=float,
        default=os.environ.get("VB6_ANTLR_TIMEOUT_SEC", "600"),
        help="Timeout in seconds for the VB6 ANTLR worker subprocess",
    )
    parser.add_argument(
        "--vb6-antlr-workspace-timeout-ms",
        type=int,
        default=os.environ.get("VB6_ANTLR_WORKSPACE_TIMEOUT_MS", "300000"),
        help="Timeout in ms for the VB6 ANTLR whole-program batch",
    )
    parser.add_argument("--qdrant-timeout", type=float, default=300.0)
    parser.add_argument("--qdrant-retries", type=int, default=3)
    parser.add_argument("--qdrant-retry-sleep", type=float, default=2.0)
    parser.set_defaults(enable_message_scan=True)
    parser.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true")
    parser.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false")
    parser.add_argument("--message-output-dir", default=os.environ.get("MESSAGE_OUTPUT_DIR"),
                        help="Directory for message scan artifacts (default: ./.cache/message_scan_artifacts)")
    parser.add_argument("--message-qdrant-collection", default=os.environ.get("MESSAGE_QDRANT_COLLECTION", ""),
                        help="Qdrant collection for message embeddings")
    parser.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    parser.add_argument("--disable-parse-cache", action="store_true")
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    parser.add_argument("--project_id", dest="project_id")
    parser.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    parser.add_argument("--project_name", dest="project_name")
    parser.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE"))
    parser.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    parser.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    parser.add_argument("--build_system", dest="build_system")
    parser.add_argument("--commit-sha-before", default=os.environ.get("GIT_COMMIT_SHA_BEFORE", ""),
                        help="Git commit SHA before changes (for incremental sync metadata)")
    parser.add_argument("--commit-sha-after", default=os.environ.get("GIT_COMMIT_SHA_AFTER", ""),
                        help="Git commit SHA after changes (for incremental sync metadata)")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest")
    parser.add_argument("--deleted-files-manifest")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isdir(args.root):
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    changed_manifest_files: List[str] = []
    deleted_manifest_files: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_manifest_files = sorted(load_manifest_paths(args.changed_files_manifest, args.root))
        if args.deleted_files_manifest:
            deleted_manifest_files = sorted(load_manifest_paths(args.deleted_files_manifest, args.root))
        if args.verbose:
            print("[diff] incremental manifests changed=%d deleted=%d" % (len(changed_manifest_files), len(deleted_manifest_files)))

    if args.dry_run:
        files = _scan_vb_files(args.root, args.dialect)
        if args.incremental and changed_manifest_files:
            manifest_set = set(changed_manifest_files)
            files = [
                file_path
                for file_path in files
                if os.path.relpath(file_path, args.root).replace("\\", "/") in manifest_set
            ]
            print(f"Dry run (incremental): {len(files)} {args.dialect} files selected (manifest={len(changed_manifest_files)})")
        else:
            print(f"Dry run: {len(files)} {args.dialect} files found")
        return 0

    code_writer: Optional[LanguageCodeWriter] = None
    driver = None
    if prepare_graph_args(args):
        driver = await create_graph_driver_from_args(args)
        code_writer = LanguageCodeWriter(
            driver=driver,
            database=args.neo4j_db,
            batch_size=args.neo4j_batch_size,
            verbose=args.verbose,
        )

    qdrant_writer: Optional[Any] = None
    embedder: Optional[Any] = None
    if args.qdrant_url:
        from tools.python.python_analyzer import CodeEmbedder, QdrantWriter

        args.device = _resolve_embed_device(args.device)
        if args.verbose:
            print(f"[embed] device: {args.device}")
        embedder = CodeEmbedder(args.embed_model, args.device, args.max_embed_chars, args.chunk_embed)
        qdrant_writer = QdrantWriter(
            args.qdrant_url,
            args.qdrant_collection,
            vector_size=embedder.vector_size,
            timeout=args.qdrant_timeout,
            retries=args.qdrant_retries,
            retry_sleep=args.qdrant_retry_sleep,
        )

    effective_cache_dir = args.cache_dir
    parse_cache = not args.disable_parse_cache
    if args.ignore_cache:
        run_cache_root = safe_cache_root(effective_cache_dir, f"{args.dialect}_analyzer", project_root=args.root)
        effective_cache_dir = os.path.join(run_cache_root, "ignore_runs", f"run_{int(time.time() * 1000)}")
        os.makedirs(effective_cache_dir, exist_ok=True)
        parse_cache = False
        if args.verbose:
            print("[cache] ignore-cache enabled; using isolated cache dir: %s" % effective_cache_dir)

    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or args.dialect
    repo = args.repo or os.path.abspath(args.root)
    build_system = args.build_system or ""

    await build_call_graph(
        args.root,
        dialect=args.dialect,
        code_writer=code_writer,
        qdrant_writer=qdrant_writer,
        embedder=embedder,
        project_id=project_id,
        project_name=project_name,
        language=language,
        repo=repo,
        build_system=build_system,
        cache_dir=effective_cache_dir,
        parse_cache=parse_cache,
        incremental=args.incremental,
        changed_files=changed_manifest_files,
        deleted_files=deleted_manifest_files,
        verbose=args.verbose,
        embed_batch_size=args.batch_size,
        qdrant_batch_size=args.qdrant_batch_size,
        parallel_workers=args.parallel_workers,
        vbnet_parser_engine=args.vbnet_parser_engine,
        vbnet_semantic=args.vbnet_semantic,
        vbnet_roslyn_worker_project=(args.vbnet_roslyn_worker_project or None),
        vbnet_roslyn_timeout_sec=args.vbnet_roslyn_timeout_sec,
        vbnet_roslyn_workspace_timeout_ms=args.vbnet_roslyn_workspace_timeout_ms,
        vbnet_roslyn_file_timeout_ms=args.vbnet_roslyn_file_timeout_ms,
        vb6_parser_engine=args.vb6_parser_engine,
        vb6_antlr_timeout_sec=args.vb6_antlr_timeout_sec,
        vb6_antlr_workspace_timeout_ms=args.vb6_antlr_workspace_timeout_ms,
    )

    if args.enable_message_scan:
        message_qdrant_collection = args.message_qdrant_collection or default_message_collection_name(args.qdrant_collection)
        message_summary = await run_message_scan_pipeline(
            root=args.root,
            parser=args.dialect,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            incremental=args.incremental,
            changed_files=changed_manifest_files,
            deleted_files=deleted_manifest_files,
            driver=driver,
            neo4j_database=args.neo4j_db,
            qdrant_url=args.qdrant_url,
            qdrant_collection=message_qdrant_collection if args.qdrant_url else None,
            qdrant_vector_size=embedder.vector_size if embedder else 1024,
            embed_texts=embedder.embed if embedder else None,
            output_dir=args.message_output_dir,
            cache_dir=effective_cache_dir,
            commit_sha_before=args.commit_sha_before,
            commit_sha_after=args.commit_sha_after,
            verbose=args.verbose,
        )
        if args.verbose:
            print(
                "[message] records=%d neo4j=%d qdrant=%d artifact=%s"
                % (
                    int(message_summary.get("message_count", 0)),
                    int(message_summary.get("neo4j_written", 0)),
                    int(message_summary.get("qdrant_written", 0)),
                    message_summary.get("artifact_path", ""),
                )
            )

    return 0


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

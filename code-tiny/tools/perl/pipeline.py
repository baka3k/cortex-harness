"""Deterministic Perl project scanning, caching, and incremental orchestration."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from tools.common.analyzer_cache import safe_cache_root

from .models import (
    ANALYZER_VERSION,
    SUPPORTED_EXTENSIONS,
    AnalysisResult,
    Diagnostic,
    ParsedFile,
    _to_primitive,
    parsed_file_from_dict,
)
from .parser_runtime import capabilities
from .perl_parser import PerlTreeSitterParser
from .resolver import affected_file_closure, resolve_project


DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_FILES = 10_000
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".idea",
    ".vscode",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


def _safe_relative_path(root: str, raw_path: str, *, must_exist: bool = False) -> Optional[str]:
    root_real = os.path.realpath(os.path.abspath(root))
    raw = (raw_path or "").strip()
    if not raw:
        return None
    candidate = raw if os.path.isabs(raw) else os.path.join(root_real, raw)
    candidate_real = os.path.realpath(os.path.abspath(candidate))
    try:
        if os.path.commonpath((root_real, candidate_real)) != root_real:
            return None
    except ValueError:
        return None
    if must_exist and not os.path.isfile(candidate_real):
        return None
    return os.path.relpath(candidate_real, root_real).replace("\\", "/")


def normalize_manifest_paths(root: str, paths: Iterable[str]) -> Tuple[str, ...]:
    normalized = []
    for raw_path in paths:
        rel_path = _safe_relative_path(root, raw_path)
        if rel_path and rel_path.lower().endswith(SUPPORTED_EXTENSIONS):
            normalized.append(rel_path)
    return tuple(sorted(set(normalized)))


def scan_perl_files(root: str, *, max_files: int = DEFAULT_MAX_FILES) -> Tuple[Tuple[str, ...], Tuple[Diagnostic, ...]]:
    root_real = os.path.realpath(os.path.abspath(root))
    if not os.path.isdir(root_real):
        raise ValueError(f"Perl analysis root is not a directory: {root}")
    paths: List[str] = []
    diagnostics: List[Diagnostic] = []
    for current_root, dirnames, filenames in os.walk(root_real, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _SKIP_DIRS and not os.path.islink(os.path.join(current_root, name))
        )
        for filename in sorted(filenames):
            if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
                continue
            absolute = os.path.join(current_root, filename)
            if os.path.islink(absolute):
                diagnostics.append(
                    Diagnostic(
                        code="perl.scan.symlink_skipped",
                        severity="warning",
                        message="Symlinked Perl source was skipped.",
                        file_path=os.path.relpath(absolute, root_real).replace("\\", "/"),
                    )
                )
                continue
            rel_path = _safe_relative_path(root_real, absolute, must_exist=True)
            if rel_path is not None:
                paths.append(rel_path)
            if len(paths) >= max_files:
                diagnostics.append(
                    Diagnostic(
                        code="perl.scan.file_budget",
                        severity="warning",
                        message=f"Source discovery stopped at the {max_files}-file budget.",
                    )
                )
                return tuple(sorted(paths)), tuple(sorted(diagnostics, key=lambda item: (item.code, item.file_path)))
    return tuple(sorted(paths)), tuple(sorted(diagnostics, key=lambda item: (item.code, item.file_path)))


def _cache_path(
    cache_root: str,
    *,
    project_id: str,
    rel_path: str,
    content_digest: str,
    include_docs: bool,
    max_snippet_chars: int,
    max_doc_chars: int,
) -> str:
    grammar = capabilities()
    fingerprint = "\0".join(
        (
            ANALYZER_VERSION,
            grammar.grammar_version,
            grammar.runtime_version,
            project_id,
            rel_path,
            content_digest,
            str(int(include_docs)),
            str(max_snippet_chars),
            str(max_doc_chars),
        )
    )
    return os.path.join(cache_root, hashlib.sha256(fingerprint.encode("utf-8")).hexdigest() + ".json")


def _read_cached(path: str) -> Optional[ParsedFile]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return parsed_file_from_dict(payload)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _write_cached(path: str, parsed: ParsedFile) -> None:
    temp_path = path + f".{os.getpid()}.tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(_to_primitive(parsed), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def run_perl_analysis(
    root: str,
    *,
    project_id: str,
    changed_paths: Optional[Iterable[str]] = None,
    deleted_paths: Optional[Iterable[str]] = None,
    cache_dir: Optional[str] = None,
    ignore_cache: bool = False,
    include_docs: bool = False,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    max_snippet_chars: int = 4000,
    max_doc_chars: int = 8000,
) -> AnalysisResult:
    """Analyze a Perl project without requiring graph, vector, or network services."""
    root_real = os.path.realpath(os.path.abspath(root))
    if not project_id or not project_id.strip():
        raise ValueError("project_id is required")
    if max_file_bytes <= 0 or max_total_bytes <= 0 or max_files <= 0:
        raise ValueError("analysis budgets must be positive")
    source_paths, scan_diagnostics = scan_perl_files(root_real, max_files=max_files)
    changed = normalize_manifest_paths(root_real, changed_paths or ())
    deleted = normalize_manifest_paths(root_real, deleted_paths or ())
    parser = PerlTreeSitterParser(
        max_snippet_chars=max_snippet_chars,
        max_doc_chars=max_doc_chars,
    )
    cache_root = safe_cache_root(cache_dir, "perl-analyzer", project_root=root_real)
    parsed_files: List[ParsedFile] = []
    diagnostics: List[Diagnostic] = list(scan_diagnostics)
    total_bytes = 0

    for rel_path in source_paths:
        absolute = os.path.join(root_real, rel_path)
        try:
            size = os.path.getsize(absolute)
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    code="perl.scan.read_error",
                    severity="warning",
                    message=f"Unable to stat source: {exc}",
                    file_path=rel_path,
                )
            )
            continue
        if total_bytes >= max_total_bytes:
            diagnostics.append(
                Diagnostic(
                    code="perl.scan.total_byte_budget",
                    severity="warning",
                    message=f"Total source budget {max_total_bytes} bytes was reached.",
                    file_path=rel_path,
                )
            )
            break
        read_limit = min(size, max_file_bytes, max_total_bytes - total_bytes)
        try:
            with open(absolute, "rb") as handle:
                source = handle.read(read_limit)
        except OSError as exc:
            diagnostics.append(
                Diagnostic(
                    code="perl.scan.read_error",
                    severity="warning",
                    message=f"Unable to read source: {exc}",
                    file_path=rel_path,
                )
            )
            continue
        total_bytes += len(source)
        truncated = size > len(source)
        content_digest = hashlib.sha256(source).hexdigest()
        cache_path = _cache_path(
            cache_root,
            project_id=project_id,
            rel_path=rel_path,
            content_digest=content_digest,
            include_docs=include_docs,
            max_snippet_chars=max_snippet_chars,
            max_doc_chars=max_doc_chars,
        )
        cached = None if ignore_cache else _read_cached(cache_path)
        if cached is not None:
            parsed = cached
        else:
            parsed = parser.parse_bytes(
                project_id=project_id,
                file_path=rel_path,
                source=source,
                include_docs=include_docs,
                truncated=truncated,
            )
            _write_cached(cache_path, parsed)
        parsed_files.append(parsed)
        if truncated:
            diagnostics.append(
                Diagnostic(
                    code="perl.scan.file_byte_budget",
                    severity="warning",
                    message=f"Source was truncated at {len(source)} of {size} bytes.",
                    file_path=rel_path,
                )
            )

    resolution = resolve_project(parsed_files)
    if changed:
        affected = set(affected_file_closure(changed, resolution.dependency_index))
    elif changed_paths is not None:
        affected = set()
    else:
        affected = {item.file.file_path for item in resolution.parsed_files}
    selected = tuple(item for item in resolution.parsed_files if item.file.file_path in affected)
    selected_paths = {item.file.file_path for item in selected}

    files = tuple(item.file for item in selected)
    symbols = tuple(sorted((symbol for item in selected for symbol in item.symbols)))
    imports = tuple(sorted((record for item in selected for record in item.imports)))
    references = tuple(sorted((record for item in selected for record in item.references)))
    documentation = tuple(sorted((record for item in selected for record in item.documentation)))
    selected_diagnostics = [diag for item in selected for diag in item.diagnostics]
    selected_diagnostics.extend(
        diag for diag in resolution.diagnostics if not diag.file_path or diag.file_path in selected_paths
    )
    selected_diagnostics.extend(diagnostics)
    selected_diagnostics = sorted(
        set(selected_diagnostics),
        key=lambda item: (
            item.code,
            item.file_path,
            item.span.start_byte if item.span else -1,
            item.message,
        ),
    )

    if not files and not source_paths:
        coverage = "empty"
    elif any(item.coverage == "partial" for item in files) or any(
        item.severity in {"warning", "error"} for item in selected_diagnostics
    ):
        coverage = "partial"
    else:
        coverage = "complete"
    counters = tuple(
        sorted(
            {
                "discovered_files": len(source_paths),
                "returned_files": len(files),
                "symbols": len(symbols),
                "imports": len(imports),
                "references": len(references),
                "diagnostics": len(selected_diagnostics),
                "input_bytes": total_bytes,
            }.items()
        )
    )
    return AnalysisResult(
        project_id=project_id.strip(),
        normalized_root=".",
        analyzer_version=ANALYZER_VERSION,
        capabilities=capabilities(),
        coverage=coverage,
        files=files,
        symbols=symbols,
        imports=imports,
        references=references,
        documentation=documentation,
        diagnostics=tuple(selected_diagnostics),
        dependency_index=resolution.dependency_index,
        changed_paths=changed,
        deleted_paths=deleted,
        counters=counters,
    )

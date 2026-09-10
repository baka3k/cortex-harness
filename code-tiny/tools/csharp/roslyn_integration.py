"""Roslyn-first integration helper for `csharp_analyzer.py`.

The analyzer stays as the orchestrator. This module provides:
- `RoslynFirstRunner`: a small facade that tries the Roslyn worker, caches per-file
  payloads, and falls back to a Tree-sitter callable when the worker fails or
  returns no evidence for a particular file.
- `_roslyn_cache_key`: backend-aware cache signature so Tree-sitter payloads and
  Roslyn payloads do not collide.

The Tree-sitter callable is injected so the analyzer's existing
`parse_csharp_file` keeps being the fallback of record.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple


from .roslyn_adapter import (
    CSHARP_ROSLYN_CACHE_VERSION,
    analyze_csharp_files,
    is_roslyn_available,
    parse_provenance,
)
from .models import roslyn_evidence_to_payload, project_metadata_to_payload


TreeSitterCallable = Callable[[str, str], Tuple[
    List[Any],  # functions
    List[Any],  # calls
    List[Any],  # types
    List[Any],  # namespaces
    List[Any],  # relations
    Any,        # file_def
    Dict[str, Any],  # parse_meta
]]


@dataclass(frozen=True)
class RoslynRunnerOptions:
    enabled: bool = True
    semantic_mode: str = "auto"
    worker_project_path: Optional[str] = None
    timeout_sec: float = 600.0
    workspace_timeout_ms: int = 120_000
    file_timeout_ms: int = 60_000
    max_file_bytes: int = 2 * 1024 * 1024
    verbose: bool = False

    def should_run(self) -> bool:
        return self.enabled


@dataclass
class RoslynPayloadCache:
    """Holds per-relpath Roslyn payloads plus worker metadata."""

    success_by_relpath: Dict[str, Dict[str, Any]]
    errors_by_relpath: Dict[str, str]
    project_metadata: Dict[str, Any]
    provenance: Dict[str, Any]

    @property
    def backend(self) -> str:
        return str(self.provenance.get("parser_language") or "csharp_tree_sitter")


class RoslynFirstRunner:
    """Coordinates Roslyn-first extraction across all files in a run."""

    def __init__(
        self,
        root: str,
        options: RoslynRunnerOptions,
        tree_sitter_fallback: TreeSitterCallable,
    ) -> None:
        self.root = root
        self.options = options
        self.tree_sitter_fallback = tree_sitter_fallback
        self.cache: Optional[RoslynPayloadCache] = None
        self.last_error: Optional[str] = None

    def try_load(
        self,
        files: Iterable[str],
        *,
        project_path: str = "",
    ) -> Optional[RoslynPayloadCache]:
        if not self.options.should_run():
            return None
        try:
            payload = analyze_csharp_files(
                root=self.root,
                files=files,
                semantic_mode=self.options.semantic_mode,
                project_path=project_path,
                worker_project_path=self.options.worker_project_path,
                timeout_sec=self.options.timeout_sec,
                workspace_timeout_ms=self.options.workspace_timeout_ms,
                file_timeout_ms=self.options.file_timeout_ms,
                max_file_bytes=self.options.max_file_bytes,
                verbose=self.options.verbose,
            )
        except (RuntimeError, OSError) as exc:
            self.last_error = str(exc)
            if self.options.verbose:
                print(f"[csharp][roslyn] unavailable, falling back to tree-sitter: {exc}")
            return None

        provenance = parse_provenance(payload)
        success: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {}
        for result in payload.get("results", []):
            rel_path = result.get("file_path") or ""
            if not rel_path:
                continue
            if not result.get("ok", False):
                errors[rel_path] = result.get("error") or "Roslyn reported ok=false"
                continue
            evidence = result.get("evidence") or {}
            converted = roslyn_evidence_to_payload(evidence)
            converted["parse_meta"].update(provenance)
            converted["parse_meta"]["file_path"] = rel_path
            success[rel_path] = converted

        cache = RoslynPayloadCache(
            success_by_relpath=success,
            errors_by_relpath=errors,
            project_metadata=project_metadata_to_payload(payload.get("project")),
            provenance=provenance,
        )
        self.cache = cache
        if self.options.verbose:
            print(
                f"[csharp][roslyn] worker={payload.get('workspace_kind')} "
                f"coverage={payload.get('coverage_status')} files={len(success)}"
            )
        return cache

    def payload_for(self, file_path: str, root: str) -> Optional[Dict[str, Any]]:
        if self.cache is None:
            return None
        rel = os.path.relpath(file_path, root).replace("\\", "/")
        return self.cache.success_by_relpath.get(rel)


def roslyn_cache_signature(file_signature: str) -> str:
    """Backend-aware cache key for parse payloads."""
    return f"{file_signature}|schema:{CSHARP_ROSLYN_CACHE_VERSION}|backend:csharp_roslyn_workspace"


def tree_sitter_cache_signature(file_signature: str) -> str:
    """Backend-aware cache key for Tree-sitter payloads."""
    return f"{file_signature}|schema:v2026-03-09-1|backend:csharp_tree_sitter"


def is_roslyn_runtime_available() -> bool:
    """Used by orchestrators that want a one-shot probe before kicking off a run."""
    return is_roslyn_available()

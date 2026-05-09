"""DependencyAgent — import-graph construction and incremental file expansion.

Responsibilities:
- Extract module specifier strings from TypeScript source text.
- Resolve relative specifiers to relative file paths within the repository.
- Build a per-file import dependency graph for the entire scanned tree.
- Expand a changed-file set to include all transitively impacted files.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Set

from tools.ts.utils.file_utils import _TS_SOURCE_EXTENSIONS


def _extract_module_specifiers_from_text(text: str) -> List[str]:
    """Extract import/require specifier strings from raw source text."""
    specifiers: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//") or line.startswith("/*") or line.startswith("*"):
            continue
        import_match = re.match(
            r"^(?:import|export)\s+(?:.+?\s+from\s+)?[\"'](?P<spec>[^\"']+)[\"']",
            line,
        )
        if import_match:
            specifiers.append(import_match.group("spec"))
        for req_match in re.finditer(
            r"(?:require|import)\(\s*[\"'](?P<spec>[^\"']+)[\"']\s*\)", line
        ):
            specifiers.append(req_match.group("spec"))
    return specifiers


def _resolve_ts_module_specifier(
    source_rel_path: str,
    specifier: str,
    file_set: set,
) -> Optional[str]:
    """Resolve a relative TypeScript import specifier to a repo-relative path.

    Returns None for non-relative (npm package) specifiers or when no match
    can be found within the known file set.
    """
    if not specifier or not specifier.startswith("."):
        return None
    base_dir = os.path.dirname(source_rel_path)
    candidate = os.path.normpath(os.path.join(base_dir, specifier)).replace("\\", "/")
    if candidate in file_set:
        return candidate
    root_candidate, ext = os.path.splitext(candidate)
    probes: List[str] = []
    if ext:
        probes.append(candidate)
    else:
        probes.extend(f"{candidate}{suffix}" for suffix in _TS_SOURCE_EXTENSIONS)
    probes.append(f"{candidate}.d.ts")
    probes.extend(f"{candidate}/index{suffix}" for suffix in _TS_SOURCE_EXTENSIONS)
    probes.append(f"{candidate}/index.d.ts")
    for path in probes:
        normalized = os.path.normpath(path).replace("\\", "/")
        if normalized in file_set:
            return normalized
    if ext in {".ts", ".tsx", ".mts", ".cts"}:
        return None
    if not ext:
        for fallback_ext in (".ts", ".tsx", ".d.ts"):
            normalized = f"{root_candidate}{fallback_ext}".replace("\\", "/")
            if normalized in file_set:
                return normalized
    return None


def _collect_ts_import_graph(
    all_ts_files: List[str],
    root: str,
) -> Dict[str, List[str]]:
    """Build a ``{rel_path: [dep_rel_path, ...]}`` mapping for every TS file."""
    rel_paths = [os.path.relpath(path, root).replace("\\", "/") for path in all_ts_files]
    file_set = set(rel_paths)
    deps_by_file: Dict[str, List[str]] = {}
    for abs_path, rel_path in zip(all_ts_files, rel_paths):
        resolved: Set[str] = set()
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as handle:
                text = handle.read()
        except OSError:
            deps_by_file[rel_path] = []
            continue
        for specifier in _extract_module_specifiers_from_text(text):
            dep = _resolve_ts_module_specifier(rel_path, specifier, file_set)
            if dep:
                resolved.add(dep)
        resolved.discard(rel_path)
        deps_by_file[rel_path] = sorted(resolved)
    return deps_by_file


def _expand_impacted_files_by_imports(
    changed_existing: Set[str],
    deps_by_file: Dict[str, List[str]],
) -> Set[str]:
    """Return the set of files that transitively import any file in *changed_existing*."""
    reverse_map: Dict[str, Set[str]] = {}
    for source, deps in deps_by_file.items():
        for dep in deps:
            reverse_map.setdefault(dep, set()).add(source)

    impacted: Set[str] = set()
    queue: List[str] = list(changed_existing)
    seen: Set[str] = set(changed_existing)
    while queue:
        current = queue.pop(0)
        for dependent in sorted(reverse_map.get(current, set())):
            if dependent in seen:
                continue
            seen.add(dependent)
            impacted.add(dependent)
            queue.append(dependent)
    return impacted


# ─── DependencyAgent class facade ────────────────────────────────────────────

class DependencyAgent:
    """Object-oriented facade over the module-level dependency functions."""

    def collect_import_graph(
        self,
        all_ts_files: List[str],
        root: str,
    ) -> Dict[str, List[str]]:
        return _collect_ts_import_graph(all_ts_files, root)

    def expand_impacted(
        self,
        changed: Set[str],
        deps_by_file: Dict[str, List[str]],
    ) -> Set[str]:
        return _expand_impacted_files_by_imports(changed, deps_by_file)

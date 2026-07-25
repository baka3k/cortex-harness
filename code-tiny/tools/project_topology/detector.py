"""Safe descriptor discovery and bounded file loading."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional, Tuple

from .models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorType,
    DiagnosticCode,
    ParseDepth,
    normalize_file_path,
)
from .registry import DescriptorSpec, descriptor_spec_for_path


SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "__pycache__",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        ".gradle",
        ".dart_tool",
    }
)
MAX_DESCRIPTOR_FILES = 10_000


def iter_descriptor_paths(root: Path) -> Iterator[Tuple[str, DescriptorSpec]]:
    root = root.resolve()
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIRS and not name.startswith(".")
        )
        for filename in sorted(filenames):
            absolute = Path(current) / filename
            if absolute.is_symlink():
                continue
            relative = absolute.relative_to(root).as_posix()
            spec = descriptor_spec_for_path(relative)
            if spec is not None:
                yield relative, spec


def _error_output(
    *,
    project_id: str,
    path: str,
    spec: DescriptorSpec,
    diagnostic: AnalysisDiagnostic,
) -> DescriptorParseOutput:
    module_path = str(Path(path).parent).replace("\\", "/") or "."
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.UNKNOWN,
        role=spec.role,
        parser=spec.name,
        parse_depth=ParseDepth.UNSUPPORTED,
        summary=diagnostic.message,
        confidence=ConfidenceLevel.LOW,
        diagnostics=(diagnostic,),
    )
    return DescriptorParseOutput(descriptor=descriptor, diagnostics=(diagnostic,))


def parse_descriptor_file(
    *,
    root: Path,
    project_id: str,
    path: str,
    spec: Optional[DescriptorSpec] = None,
) -> DescriptorParseOutput:
    root = root.resolve()
    normalized = normalize_file_path(path)
    selected = spec or descriptor_spec_for_path(normalized)
    if selected is None:
        raise ValueError(f"no descriptor parser registered for {path}")
    unresolved = root / normalized
    if unresolved.is_symlink():
        return _error_output(
            project_id=project_id,
            path=normalized,
            spec=selected,
            diagnostic=AnalysisDiagnostic(
                DiagnosticCode.MODULE_PATH_ESCAPE,
                "Symbolic-link descriptors are not followed.",
                severity="error",
                file_path=normalized,
                module_path=str(Path(normalized).parent),
            ),
        )
    absolute = unresolved.resolve()
    try:
        absolute.relative_to(root)
    except ValueError:
        return _error_output(
            project_id=project_id,
            path=normalized,
            spec=selected,
            diagnostic=AnalysisDiagnostic(
                DiagnosticCode.MODULE_PATH_ESCAPE,
                "Descriptor path escapes the repository root.",
                severity="error",
                file_path=normalized,
                module_path=".",
            ),
        )
    try:
        size = absolute.stat().st_size
    except OSError as exc:
        return _error_output(
            project_id=project_id,
            path=normalized,
            spec=selected,
            diagnostic=AnalysisDiagnostic(
                DiagnosticCode.IO_ERROR,
                f"Unable to read descriptor metadata: {exc}",
                severity="error",
                file_path=normalized,
                module_path=str(Path(normalized).parent),
            ),
        )
    if size > selected.max_bytes:
        return _error_output(
            project_id=project_id,
            path=normalized,
            spec=selected,
            diagnostic=AnalysisDiagnostic(
                DiagnosticCode.DESCRIPTOR_TOO_LARGE,
                f"Descriptor exceeds the {selected.max_bytes}-byte safety limit.",
                file_path=normalized,
                module_path=str(Path(normalized).parent),
                details={"size": size, "limit": selected.max_bytes},
            ),
        )
    try:
        text = absolute.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _error_output(
            project_id=project_id,
            path=normalized,
            spec=selected,
            diagnostic=AnalysisDiagnostic(
                DiagnosticCode.IO_ERROR,
                f"Unable to read descriptor: {exc}",
                severity="error",
                file_path=normalized,
                module_path=str(Path(normalized).parent),
            ),
        )
    return selected.parse(project_id=project_id, path=normalized, text=text)


__all__ = [
    "MAX_DESCRIPTOR_FILES",
    "SKIP_DIRS",
    "iter_descriptor_paths",
    "parse_descriptor_file",
]

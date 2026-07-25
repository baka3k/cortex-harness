"""Project topology analysis pipeline."""

from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Iterable, Optional

from .detector import (
    MAX_DESCRIPTOR_FILES,
    iter_descriptor_paths,
    parse_descriptor_file,
)
from .models import (
    AnalysisDiagnostic,
    DiagnosticCode,
    TopologyAnalysisResult,
)
from .registry import descriptor_candidates, descriptor_spec_for_path
from .resolver import resolve_topology


def analyze_project(
    root: str | Path,
    project_id: str,
    *,
    changed_paths: Optional[Iterable[str]] = None,
    deleted_paths: Optional[Iterable[str]] = None,
) -> TopologyAnalysisResult:
    """Analyze descriptor topology without executing project build tooling."""

    root_path = Path(root).resolve()
    if not project_id.strip():
        raise ValueError("project_id is required")
    outputs = []
    scan_diagnostics = []
    if changed_paths is None:
        discovered = tuple(
            islice(iter_descriptor_paths(root_path), MAX_DESCRIPTOR_FILES + 1)
        )
        candidates = discovered[:MAX_DESCRIPTOR_FILES]
        if len(discovered) > MAX_DESCRIPTOR_FILES:
            scan_diagnostics.append(
                AnalysisDiagnostic(
                    DiagnosticCode.LIMIT_EXCEEDED,
                    "Descriptor discovery reached the project file-count limit.",
                    details={"limit": MAX_DESCRIPTOR_FILES},
                )
            )
    else:
        selected = descriptor_candidates(changed_paths)
        candidates = tuple(
            (path, descriptor_spec_for_path(path))
            for path in selected[:MAX_DESCRIPTOR_FILES]
            if (root_path / path).is_file()
        )
        if len(selected) > MAX_DESCRIPTOR_FILES:
            scan_diagnostics.append(
                AnalysisDiagnostic(
                    DiagnosticCode.LIMIT_EXCEEDED,
                    "Changed descriptor manifest reached the file-count limit.",
                    details={"limit": MAX_DESCRIPTOR_FILES},
                )
            )
    for path, spec in candidates:
        if spec is not None:
            outputs.append(
                parse_descriptor_file(
                    root=root_path,
                    project_id=project_id,
                    path=path,
                    spec=spec,
                )
            )
    descriptors = tuple(sorted((item.descriptor for item in outputs), key=lambda item: item.path))
    dependencies = tuple(
        dependency
        for output in outputs
        for dependency in output.dependencies
    )
    endpoints = tuple(
        sorted(
            (endpoint for output in outputs for endpoint in output.endpoints),
            key=lambda item: item.id,
        )
    )
    extraction_diagnostics = tuple(
        diagnostic
        for output in outputs
        for diagnostic in output.diagnostics
    )
    modules, resolved_dependencies, special_files, frameworks, resolver_diagnostics = (
        resolve_topology(
            project_id=project_id,
            descriptors=descriptors,
            dependencies=dependencies,
        )
    )
    deleted = tuple(sorted(descriptor_candidates(deleted_paths or ())))
    diagnostics = (
        list(scan_diagnostics)
        + list(extraction_diagnostics)
        + list(resolver_diagnostics)
    )
    if deleted:
        diagnostics.append(
            AnalysisDiagnostic(
                DiagnosticCode.UNRESOLVED_REFERENCE,
                "Deleted descriptor paths require topology-owned graph cleanup before write.",
                details={"deleted_paths": list(deleted)},
            )
        )
    return TopologyAnalysisResult(
        project_id=project_id,
        root=str(root_path),
        modules=modules,
        descriptors=descriptors,
        dependencies=resolved_dependencies,
        endpoints=endpoints,
        special_files=special_files,
        frameworks=frameworks,
        diagnostics=tuple(diagnostics),
    )


__all__ = ["analyze_project"]

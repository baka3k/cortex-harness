"""Bounded CMake statement extraction without executing CMake."""

from __future__ import annotations

import re
from typing import Dict, List

from ..models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DependencyFact,
    DependencyScope,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    DiagnosticCode,
    ModuleKind,
    ParseDepth,
    SourceEvidence,
    normalize_module_path,
    safe_summary,
)
from .common import dynamic_diagnostics, evidence, line_number, module_path_for_file


_COMMAND_RE = re.compile(r"(?ims)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)")
_TOKEN_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|([^\s;]+)')


def _tokens(value: str) -> List[str]:
    return [next(item for item in match.groups() if item is not None) for match in _TOKEN_RE.finditer(value)]


def parse_cmake(*, project_id: str, path: str, text: str) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics = list(dynamic_diagnostics(text, path=path, module_path=module_path))
    project_name = ""
    subdirectories: List[str] = []
    targets: List[Dict[str, object]] = []
    dependencies: List[DependencyFact] = []
    target_kinds: Dict[str, ModuleKind] = {}
    for match in _COMMAND_RE.finditer(text):
        command = match.group(1).lower()
        args = _tokens(match.group(2))
        if not args:
            continue
        if any("$<" in value for value in args):
            diagnostics.append(
                AnalysisDiagnostic(
                    DiagnosticCode.DYNAMIC_EXPRESSION,
                    "CMake generator expression was retained as unresolved evidence.",
                    file_path=path,
                    module_path=module_path,
                    details={"line": line_number(text, match.start())},
                )
            )
        if command == "project":
            project_name = args[0]
        elif command == "add_subdirectory":
            subdirectories.append(
                normalize_module_path(
                    f"{module_path}/{args[0]}" if module_path != "." else args[0]
                )
            )
        elif command in {"add_executable", "add_library"}:
            kind = (
                ModuleKind.NATIVE_EXECUTABLE
                if command == "add_executable"
                else ModuleKind.NATIVE_LIBRARY
            )
            target_kinds[args[0]] = kind
            targets.append({"name": args[0], "kind": kind.value, "sources": args[1:]})
        elif command in {"target_link_libraries", "add_dependencies"} and len(args) > 1:
            source_target = args[0]
            for target in args[1:]:
                if target.upper() in {"PUBLIC", "PRIVATE", "INTERFACE"}:
                    continue
                dependencies.append(
                    DependencyFact.create(
                        project_id=project_id,
                        source_module_path=module_path,
                        target=target,
                        scope=DependencyScope.COMPILE,
                        source=path,
                        evidence=(SourceEvidence(path, line_number(text, match.start())),),
                        properties={"source_target": source_target},
                    )
                )
    module_kind = (
        next(iter(target_kinds.values()))
        if len(set(target_kinds.values())) == 1
        else ModuleKind.UNKNOWN
    )
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.CMAKE,
        role=DescriptorRole.TOPOLOGY,
        parser="cmake",
        parse_depth=ParseDepth.DEPENDENCY,
        summary=safe_summary(f"CMake project {project_name or module_path}"),
        properties={
            "project_name": project_name,
            "declared_modules": sorted(set(subdirectories)),
            "targets": targets,
            "module_kind": module_kind.value,
            "build_system": "cmake",
        },
        confidence=ConfidenceLevel.MEDIUM if diagnostics else ConfidenceLevel.HIGH,
        evidence=evidence(path),
        diagnostics=tuple(diagnostics),
    )
    return DescriptorParseOutput(
        descriptor=descriptor,
        dependencies=tuple(sorted(dependencies, key=lambda item: item.id)),
        diagnostics=tuple(diagnostics),
    )

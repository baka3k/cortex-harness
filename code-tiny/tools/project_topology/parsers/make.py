"""Conservative Make target/prerequisite extraction."""

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
    ParseDepth,
    SourceEvidence,
    safe_summary,
)
from .common import evidence, line_number, module_path_for_file


_TARGET_RE = re.compile(r"(?m)^([A-Za-z0-9_./%+-][^:=\n]*?)\s*:(?![=])\s*([^\n#]*)")
_INCLUDE_RE = re.compile(r"(?m)^\s*-?include\s+([^\n#]+)")
_VARIABLE_RE = re.compile(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*([^\n#]*)")
_DYNAMIC_RE = re.compile(r"\$\((?:shell|eval|call)\b|`[^`]+`", re.IGNORECASE)


def parse_make(*, project_id: str, path: str, text: str) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics: List[AnalysisDiagnostic] = []
    for match in _DYNAMIC_RE.finditer(text):
        diagnostics.append(
            AnalysisDiagnostic(
                DiagnosticCode.UNSUPPORTED_CONSTRUCT,
                "Dynamic Make expression was not evaluated.",
                file_path=path,
                module_path=module_path,
                details={"line": line_number(text, match.start())},
            )
        )
    targets: List[Dict[str, object]] = []
    dependencies: List[DependencyFact] = []
    for match in _TARGET_RE.finditer(text):
        names = [item for item in match.group(1).split() if "$" not in item]
        prerequisites = [item for item in match.group(2).split() if "$" not in item]
        for name in names:
            targets.append({"name": name, "prerequisites": prerequisites})
            for target in prerequisites:
                dependencies.append(
                    DependencyFact.create(
                        project_id=project_id,
                        source_module_path=module_path,
                        target=target,
                        scope=DependencyScope.BUILD,
                        source=path,
                        evidence=(SourceEvidence(path, line_number(text, match.start())),),
                        properties={"make_target": name},
                    )
                )
    variables = {
        name: value.strip()
        for name, value in _VARIABLE_RE.findall(text)
        if not _DYNAMIC_RE.search(value)
    }
    includes = [
        item
        for value in _INCLUDE_RE.findall(text)
        for item in value.split()
        if "$" not in item
    ]
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.MAKE,
        role=DescriptorRole.TOPOLOGY,
        parser="make",
        parse_depth=ParseDepth.DEPENDENCY,
        summary=safe_summary(f"Make build with {len(targets)} literal targets"),
        properties={
            "targets": targets,
            "includes": sorted(set(includes)),
            "variables": variables,
            "build_system": "make",
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

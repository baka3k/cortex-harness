"""Static Ant project/target extraction."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List

from ..models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DependencyFact,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    DiagnosticCode,
    ParseDepth,
    safe_summary,
)
from .common import evidence, module_path_for_file


def parse_ant(*, project_id: str, path: str, text: str) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics: List[AnalysisDiagnostic] = []
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        diagnostics.append(
            AnalysisDiagnostic(
                DiagnosticCode.XML_UNSAFE,
                "DOCTYPE and entity declarations are not permitted in Ant XML.",
                severity="error",
                file_path=path,
                module_path=module_path,
            )
        )
        root = None
    else:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            diagnostics.append(
                AnalysisDiagnostic(
                    DiagnosticCode.MALFORMED_DESCRIPTOR,
                    f"Malformed Ant build file: {exc}",
                    severity="error",
                    file_path=path,
                    module_path=module_path,
                )
            )
            root = None
    targets = []
    imports = []
    dependencies: List[DependencyFact] = []
    project_name = ""
    if root is not None:
        project_name = root.attrib.get("name", "")
        for node in root.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local == "target":
                target = node.attrib.get("name", "")
                depends = [
                    item.strip()
                    for item in node.attrib.get("depends", "").split(",")
                    if item.strip()
                ]
                if target:
                    targets.append({"name": target, "depends": depends})
            elif local == "import" and node.attrib.get("file"):
                imports.append(node.attrib["file"])
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.ANT_BUILD,
        role=DescriptorRole.TOPOLOGY,
        parser="ant",
        parse_depth=ParseDepth.TOPOLOGY if root is not None else ParseDepth.IDENTITY,
        summary=safe_summary(f"Ant project {project_name or module_path}"),
        properties={
            "project_name": project_name,
            "targets": targets,
            "imports": sorted(set(imports)),
            "build_system": "ant",
        },
        confidence=ConfidenceLevel.LOW if root is None else ConfidenceLevel.HIGH,
        evidence=evidence(path),
        diagnostics=tuple(diagnostics),
    )
    return DescriptorParseOutput(
        descriptor=descriptor,
        dependencies=tuple(dependencies),
        diagnostics=tuple(diagnostics),
    )

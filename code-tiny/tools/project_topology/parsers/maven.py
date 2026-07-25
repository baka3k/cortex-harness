"""Namespace-safe, bounded Maven POM topology extraction."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, Iterable, List, Optional

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
    normalize_module_path,
    safe_summary,
)
from .common import evidence, module_path_for_file


_PROPERTY_RE = re.compile(r"\$\{([^}]+)\}")


def _local(element: ET.Element, name: str) -> Optional[ET.Element]:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == name:
            return child
    return None


def _children(element: ET.Element, name: str) -> Iterable[ET.Element]:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == name:
            yield child


def _text(element: Optional[ET.Element], default: str = "") -> str:
    return (element.text or "").strip() if element is not None else default


def _resolve(value: str, properties: Dict[str, str]) -> str:
    return _PROPERTY_RE.sub(lambda match: properties.get(match.group(1), match.group(0)), value)


def _scope(value: str) -> DependencyScope:
    lowered = value.lower()
    return {
        "test": DependencyScope.TEST,
        "runtime": DependencyScope.RUNTIME,
        "provided": DependencyScope.PROVIDED,
        "compile": DependencyScope.COMPILE,
    }.get(lowered, DependencyScope.UNKNOWN)


def parse_maven(
    *,
    project_id: str,
    path: str,
    text: str,
) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics: List[AnalysisDiagnostic] = []
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        diagnostic = AnalysisDiagnostic(
            DiagnosticCode.XML_UNSAFE,
            "DOCTYPE and entity declarations are not permitted in descriptor XML.",
            severity="error",
            file_path=path,
            module_path=module_path,
        )
        descriptor = DescriptorFact.create(
            project_id=project_id,
            module_path=module_path,
            path=path,
            descriptor_type=DescriptorType.MAVEN_POM,
            role=DescriptorRole.IDENTITY,
            parser="maven",
            parse_depth=ParseDepth.IDENTITY,
            summary="Unsafe Maven descriptor rejected",
            confidence=ConfidenceLevel.LOW,
            evidence=evidence(path),
            diagnostics=(diagnostic,),
        )
        return DescriptorParseOutput(descriptor=descriptor, diagnostics=(diagnostic,))
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        diagnostic = AnalysisDiagnostic(
            DiagnosticCode.MALFORMED_DESCRIPTOR,
            f"Malformed Maven POM: {exc}",
            severity="error",
            file_path=path,
            module_path=module_path,
        )
        descriptor = DescriptorFact.create(
            project_id=project_id,
            module_path=module_path,
            path=path,
            descriptor_type=DescriptorType.MAVEN_POM,
            role=DescriptorRole.IDENTITY,
            parser="maven",
            parse_depth=ParseDepth.IDENTITY,
            summary="Malformed Maven descriptor",
            confidence=ConfidenceLevel.LOW,
            evidence=evidence(path),
            diagnostics=(diagnostic,),
        )
        return DescriptorParseOutput(descriptor=descriptor, diagnostics=(diagnostic,))

    properties: Dict[str, str] = {}
    properties_node = _local(root, "properties")
    if properties_node is not None:
        properties = {
            child.tag.rsplit("}", 1)[-1]: _text(child)
            for child in properties_node
        }
    parent = _local(root, "parent")
    parent_group = _text(_local(parent, "groupId")) if parent is not None else ""
    parent_version = _text(_local(parent, "version")) if parent is not None else ""
    group_id = _resolve(_text(_local(root, "groupId"), parent_group), properties)
    artifact_id = _resolve(_text(_local(root, "artifactId")), properties)
    version = _resolve(_text(_local(root, "version"), parent_version), properties)
    packaging = _text(_local(root, "packaging"), "jar")
    modules_node = _local(root, "modules")
    declared_modules = []
    if modules_node is not None:
        for item in _children(modules_node, "module"):
            target = _text(item)
            if target:
                declared_modules.append(
                    normalize_module_path(
                        f"{module_path}/{target}" if module_path != "." else target
                    )
                )

    dependencies: List[DependencyFact] = []
    dependencies_node = _local(root, "dependencies")
    if dependencies_node is not None:
        for item in _children(dependencies_node, "dependency"):
            dep_group = _resolve(_text(_local(item, "groupId")), properties)
            dep_artifact = _resolve(_text(_local(item, "artifactId")), properties)
            dep_version = _resolve(_text(_local(item, "version")), properties)
            dep_scope = _scope(_text(_local(item, "scope"), "compile"))
            target = ":".join(value for value in (dep_group, dep_artifact, dep_version) if value)
            dependencies.append(
                DependencyFact.create(
                    project_id=project_id,
                    source_module_path=module_path,
                    target=target,
                    scope=dep_scope,
                    source=path,
                    evidence=evidence(path),
                    properties={"group_id": dep_group, "artifact_id": dep_artifact},
                )
            )
            if "${" in target:
                diagnostics.append(
                    AnalysisDiagnostic(
                        DiagnosticCode.UNRESOLVED_REFERENCE,
                        f"Unresolved Maven property in dependency {target}.",
                        file_path=path,
                        module_path=module_path,
                    )
                )
    module_kind = (
        ModuleKind.MAVEN_MODULE
        if packaging in {"pom", "jar", "war", "ear"}
        else ModuleKind.UNKNOWN
    )
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.MAVEN_POM,
        role=DescriptorRole.DEPENDENCY,
        parser="maven",
        parse_depth=ParseDepth.DEPENDENCY,
        summary=safe_summary(f"Maven {group_id}:{artifact_id}:{version} ({packaging})"),
        properties={
            "group_id": group_id,
            "artifact_id": artifact_id,
            "version": version,
            "coordinate": ":".join(value for value in (group_id, artifact_id, version) if value),
            "packaging": packaging,
            "module_kind": module_kind.value,
            "declared_modules": sorted(set(declared_modules)),
            "build_system": "maven",
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

"""Conservative Gradle Groovy/Kotlin DSL topology extraction."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Dict, List

from ..models import (
    ConfidenceLevel,
    DependencyFact,
    DependencyScope,
    DescriptorFact,
    DescriptorParseOutput,
    DescriptorRole,
    DescriptorType,
    ModuleKind,
    ParseDepth,
    SourceEvidence,
    normalize_module_path,
    safe_summary,
)
from .common import dynamic_diagnostics, evidence, line_number, module_path_for_file


_ROOT_NAME_RE = re.compile(r"\brootProject\.name\s*=\s*['\"]([^'\"]+)['\"]")
_INCLUDE_RE = re.compile(r"\binclude\s*(?:\(|\s)\s*([^\n)]+)\)?")
_QUOTED_RE = re.compile(r"['\"]([^'\"]+)['\"]")
_PROJECT_DIR_RE = re.compile(
    r"\bproject\s*\(\s*['\"](:[^'\"]+)['\"]\s*\)\.projectDir\s*=\s*"
    r"(?:file\s*\(\s*)?['\"]([^'\"]+)['\"]"
)
_INCLUDED_BUILD_RE = re.compile(r"\bincludeBuild\s*\(\s*['\"]([^'\"]+)['\"]")
_PLUGIN_RE = re.compile(
    r"(?:id\s*\(?\s*['\"]([^'\"]+)['\"]|alias\s*\(\s*libs\.plugins\.([A-Za-z0-9_.-]+))"
)
_NAMESPACE_RE = re.compile(r"\b(namespace|applicationId)\s*(?:=|\s)\s*['\"]([^'\"]+)['\"]")
_PROJECT_DEP_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(?\s*(?:project\s*\(\s*(?:path\s*:\s*)?['\"](:[^'\"]+)['\"]\s*\)|"
    r"projects\.([A-Za-z0-9_.]+))"
)
_EXTERNAL_DEP_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(?\s*['\"]"
    r"([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+(?::[^'\"]+)?)['\"]"
)
_DYNAMIC_FEATURE_RE = re.compile(r"\bdynamicFeatures\s*(?:=|\+=)\s*setOf\s*\(([^)]*)\)")
_SOURCE_SET_RE = re.compile(r"\bsourceSets\s*(?:\{|\.)([A-Za-z0-9_-]+)")


def _gradle_path(value: str) -> str:
    stripped = value.strip().strip(":")
    return normalize_module_path(stripped.replace(":", "/"))


def _scope(configuration: str) -> DependencyScope:
    lowered = configuration.lower()
    if "test" in lowered:
        return DependencyScope.TEST
    if "runtime" in lowered:
        return DependencyScope.RUNTIME
    if "compileonly" in lowered or "provided" in lowered:
        return DependencyScope.PROVIDED
    if "plugin" in lowered or "classpath" in lowered:
        return DependencyScope.PLUGIN
    return DependencyScope.COMPILE


def parse_gradle_settings(
    *,
    project_id: str,
    path: str,
    text: str,
) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics = dynamic_diagnostics(text, path=path, module_path=module_path)
    root_match = _ROOT_NAME_RE.search(text)
    declared: List[str] = []
    for match in _INCLUDE_RE.finditer(text):
        declared.extend(
            _gradle_path(value)
            for value in _QUOTED_RE.findall(match.group(1))
            if value.strip(":")
        )
    project_dirs: Dict[str, str] = {
        _gradle_path(name): normalize_module_path(
            str(PurePosixPath(module_path) / target)
            if module_path != "."
            else target
        )
        for name, target in _PROJECT_DIR_RE.findall(text)
    }
    declared = [project_dirs.get(item, item) for item in declared]
    included_builds = [
        normalize_module_path(
            str(PurePosixPath(module_path) / value)
            if module_path != "."
            else value
        )
        for value in _INCLUDED_BUILD_RE.findall(text)
    ]
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.GRADLE_SETTINGS,
        role=DescriptorRole.TOPOLOGY,
        parser="gradle",
        parse_depth=ParseDepth.TOPOLOGY,
        summary=safe_summary(f"Gradle settings for {root_match.group(1) if root_match else module_path}"),
        properties={
            "root_name": root_match.group(1) if root_match else "",
            "declared_modules": sorted(set(declared)),
            "project_dirs": project_dirs,
            "included_builds": sorted(set(included_builds)),
            "build_system": "gradle",
        },
        confidence=ConfidenceLevel.MEDIUM if diagnostics else ConfidenceLevel.HIGH,
        evidence=evidence(path),
        diagnostics=diagnostics,
    )
    return DescriptorParseOutput(descriptor=descriptor, diagnostics=diagnostics)


def _module_kind(plugins: List[str]) -> ModuleKind:
    lowered = {item.lower() for item in plugins}
    if {"com.android.dynamic-feature", "android-dynamic-feature"} & lowered:
        return ModuleKind.ANDROID_DYNAMIC_FEATURE
    if {"com.android.application", "android-application"} & lowered:
        return ModuleKind.ANDROID_APPLICATION
    if {"com.android.library", "android-library"} & lowered:
        return ModuleKind.ANDROID_LIBRARY
    if any("application" == item or item.endswith(".application") for item in lowered):
        return ModuleKind.JVM_APPLICATION
    if any(
        marker in item
        for item in lowered
        for marker in ("java-library", "kotlin.jvm", "org.jetbrains.kotlin")
    ):
        return ModuleKind.JVM_LIBRARY
    return ModuleKind.UNKNOWN


def parse_gradle_build(
    *,
    project_id: str,
    path: str,
    text: str,
) -> DescriptorParseOutput:
    module_path = module_path_for_file(path)
    diagnostics = dynamic_diagnostics(text, path=path, module_path=module_path)
    plugins = [first or second for first, second in _PLUGIN_RE.findall(text)]
    coordinates = {key: value for key, value in _NAMESPACE_RE.findall(text)}
    dependencies: List[DependencyFact] = []
    for match in _PROJECT_DEP_RE.finditer(text):
        configuration, colon_path, accessor = match.groups()
        target_path = _gradle_path(colon_path or accessor.replace(".", ":"))
        dependencies.append(
            DependencyFact.create(
                project_id=project_id,
                source_module_path=module_path,
                target=target_path,
                target_module_path=target_path,
                internal=True,
                scope=_scope(configuration),
                source=path,
                evidence=(SourceEvidence(path, line_number(text, match.start())),),
            )
        )
    for match in _EXTERNAL_DEP_RE.finditer(text):
        configuration, coordinate = match.groups()
        dependencies.append(
            DependencyFact.create(
                project_id=project_id,
                source_module_path=module_path,
                target=coordinate,
                internal=False,
                scope=_scope(configuration),
                source=path,
                evidence=(SourceEvidence(path, line_number(text, match.start())),),
            )
        )
    dynamic_features = [
        _gradle_path(value)
        for match in _DYNAMIC_FEATURE_RE.findall(text)
        for value in _QUOTED_RE.findall(match)
    ]
    for target in dynamic_features:
        dependencies.append(
            DependencyFact.create(
                project_id=project_id,
                source_module_path=module_path,
                target=target,
                target_module_path=target,
                internal=True,
                scope=DependencyScope.COMPILE,
                source=path,
                properties={"dynamic_feature": True},
                evidence=evidence(path),
            )
        )
    module_kind = _module_kind(plugins)
    descriptor = DescriptorFact.create(
        project_id=project_id,
        module_path=module_path,
        path=path,
        descriptor_type=DescriptorType.GRADLE_BUILD,
        role=DescriptorRole.DEPENDENCY,
        parser="gradle",
        parse_depth=ParseDepth.DEPENDENCY,
        summary=safe_summary(f"Gradle {module_kind.value} module at {module_path}"),
        properties={
            "plugins": sorted(set(plugins)),
            "module_kind": module_kind.value,
            "namespace": coordinates.get("namespace", ""),
            "application_id": coordinates.get("applicationId", ""),
            "source_sets": sorted(set(_SOURCE_SET_RE.findall(text))),
            "dynamic_features": sorted(set(dynamic_features)),
            "build_system": "gradle",
        },
        confidence=ConfidenceLevel.MEDIUM if diagnostics else ConfidenceLevel.HIGH,
        evidence=evidence(path),
        diagnostics=diagnostics,
    )
    return DescriptorParseOutput(
        descriptor=descriptor,
        dependencies=tuple(sorted(dependencies, key=lambda item: item.id)),
        diagnostics=diagnostics,
    )

"""Deterministic module and dependency resolution across descriptor formats."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Sequence, Set, Tuple

from .models import (
    AnalysisDiagnostic,
    ConfidenceLevel,
    DependencyFact,
    DescriptorFact,
    DiagnosticCode,
    FrameworkInstanceFact,
    ModuleFact,
    ModuleKind,
    SourceEvidence,
    SpecialFileFact,
    deterministic_unique,
    normalize_module_path,
    stable_fact_id,
)


_KIND_PRIORITY = {
    ModuleKind.ANDROID_APPLICATION: 100,
    ModuleKind.ANDROID_DYNAMIC_FEATURE: 95,
    ModuleKind.ANDROID_LIBRARY: 90,
    ModuleKind.JVM_APPLICATION: 80,
    ModuleKind.JVM_LIBRARY: 75,
    ModuleKind.NATIVE_EXECUTABLE: 70,
    ModuleKind.NATIVE_LIBRARY: 65,
    ModuleKind.MAVEN_MODULE: 60,
    ModuleKind.PACKAGE: 50,
    ModuleKind.DATABASE: 40,
    ModuleKind.ROOT: 10,
    ModuleKind.UNKNOWN: 0,
}

_FRAMEWORK_MARKERS = {
    "spring": ("spring", "springframework"),
    "mybatis": ("mybatis",),
    "servlet_jsp": ("servlet", "jakarta.servlet", "javax.servlet"),
    "struts": ("struts",),
    "flutter": ("flutter",),
    "aspnet_core": ("microsoft.aspnetcore",),
    "aspnet_framework": ("system.web",),
    "fastapi_django": ("fastapi", "django"),
    "express_js": ("express",),
    "laravel": ("laravel", "illuminate"),
    "database_sql": ("dbt", "flyway", "liquibase"),
    "database_plsql": ("oracle", "plsql"),
}


def _kind(value: object) -> ModuleKind:
    try:
        return ModuleKind(str(value))
    except ValueError:
        return ModuleKind.UNKNOWN


def _module_paths(descriptors: Sequence[DescriptorFact]) -> Set[str]:
    paths = {item.module_path for item in descriptors}
    for descriptor in descriptors:
        declared = descriptor.properties.get("declared_modules", ())
        if isinstance(declared, (list, tuple, set)):
            paths.update(normalize_module_path(value) for value in declared)
    return paths or {"."}


def _detect_cycles(dependencies: Sequence[DependencyFact]) -> Tuple[Tuple[str, ...], ...]:
    graph: Dict[str, Set[str]] = {}
    for item in dependencies:
        if item.internal and item.target_module_path:
            graph.setdefault(item.source_module_path, set()).add(item.target_module_path)
    cycles: Set[Tuple[str, ...]] = set()

    def visit(node: str, stack: List[str], active: Set[str], seen: Set[str]) -> None:
        if node in active:
            start = stack.index(node)
            cycle = tuple(stack[start:] + [node])
            rotations = [cycle[index:-1] + cycle[:index] + (cycle[index],) for index in range(len(cycle) - 1)]
            cycles.add(min(rotations))
            return
        if node in seen:
            return
        active.add(node)
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            visit(target, stack, active, seen)
        stack.pop()
        active.remove(node)
        seen.add(node)

    seen: Set[str] = set()
    for node in sorted(graph):
        visit(node, [], set(), seen)
    return tuple(sorted(cycles))


def resolve_topology(
    *,
    project_id: str,
    descriptors: Sequence[DescriptorFact],
    dependencies: Sequence[DependencyFact],
) -> Tuple[
    Tuple[ModuleFact, ...],
    Tuple[DependencyFact, ...],
    Tuple[SpecialFileFact, ...],
    Tuple[FrameworkInstanceFact, ...],
    Tuple[AnalysisDiagnostic, ...],
]:
    paths = _module_paths(descriptors)
    by_path: Dict[str, List[DescriptorFact]] = {path: [] for path in paths}
    for descriptor in descriptors:
        by_path.setdefault(descriptor.module_path, []).append(descriptor)

    coordinate_to_path = {
        str(descriptor.properties.get("coordinate")): descriptor.module_path
        for descriptor in descriptors
        if descriptor.properties.get("coordinate")
    }
    resolved_dependencies: List[DependencyFact] = []
    for dependency in dependencies:
        target_path = dependency.target_module_path
        if target_path and normalize_module_path(target_path) in paths:
            resolved_dependencies.append(
                replace(
                    dependency,
                    internal=True,
                    target_module_path=normalize_module_path(target_path),
                )
            )
            continue
        target_coordinate = ":".join(dependency.target.split(":")[:2])
        coordinate_match = next(
            (
                module_path
                for coordinate, module_path in coordinate_to_path.items()
                if ":".join(coordinate.split(":")[:2]) == target_coordinate
            ),
            None,
        )
        if coordinate_match:
            resolved_dependencies.append(
                replace(dependency, internal=True, target_module_path=coordinate_match)
            )
        else:
            resolved_dependencies.append(dependency)

    diagnostics: List[AnalysisDiagnostic] = []
    modules: List[ModuleFact] = []
    frameworks: List[FrameworkInstanceFact] = []
    for module_path in sorted(paths):
        facts = sorted(by_path.get(module_path, ()), key=lambda item: item.path)
        kinds = [_kind(item.properties.get("module_kind")) for item in facts]
        concrete_kinds = {kind for kind in kinds if kind != ModuleKind.UNKNOWN}
        selected_kind = max(concrete_kinds or {ModuleKind.ROOT if module_path == "." else ModuleKind.UNKNOWN}, key=lambda value: _KIND_PRIORITY[value])
        module_diagnostics: List[AnalysisDiagnostic] = []
        if len(concrete_kinds) > 1:
            diagnostic = AnalysisDiagnostic(
                DiagnosticCode.AMBIGUOUS_MODULE_KIND,
                "Conflicting descriptor evidence produced more than one module kind.",
                file_path=facts[0].path if facts else "",
                module_path=module_path,
                details={"kinds": sorted(item.value for item in concrete_kinds)},
            )
            module_diagnostics.append(diagnostic)
            diagnostics.append(diagnostic)
        build_systems = deterministic_unique(
            str(item.properties.get("build_system", "")) for item in facts
        )
        names = [
            str(
                item.properties.get("root_name")
                or item.properties.get("artifact_id")
                or item.properties.get("name")
                or ""
            )
            for item in facts
        ]
        languages = []
        if selected_kind.value.startswith("android"):
            languages.extend(("java", "kotlin"))
        elif selected_kind.value.startswith("jvm") or selected_kind == ModuleKind.MAVEN_MODULE:
            languages.extend(("java", "kotlin"))
        elif selected_kind.value.startswith("native"):
            languages.extend(("c", "cplus"))
        marker_text = " ".join(
            [
                *(item.parser for item in facts),
                *(item.path for item in facts),
                *(str(item.properties) for item in facts),
                *(
                    item.target
                    for item in resolved_dependencies
                    if item.source_module_path == module_path
                ),
            ]
        ).lower()
        detected_frameworks = deterministic_unique(
            framework
            for framework, markers in _FRAMEWORK_MARKERS.items()
            if any(marker in marker_text for marker in markers)
        )
        module = ModuleFact.create(
            project_id=project_id,
            module_path=module_path,
            name=next((name for name in names if name), ""),
            kind=selected_kind,
            languages=deterministic_unique(languages),
            frameworks=detected_frameworks,
            build_systems=build_systems,
            descriptor_ids=tuple(item.id for item in facts),
            confidence=(
                ConfidenceLevel.MEDIUM
                if module_diagnostics or any(item.diagnostics for item in facts)
                else ConfidenceLevel.HIGH
            ),
            diagnostics=tuple(module_diagnostics),
        )
        modules.append(module)
        for framework in detected_frameworks:
            frameworks.append(
                FrameworkInstanceFact(
                    id=stable_fact_id(project_id, "framework-instance", module.id, framework),
                    project_id=project_id,
                    module_id=module.id,
                    framework=framework,
                    confidence=ConfidenceLevel.MEDIUM,
                    evidence=tuple(
                        SourceEvidence(item.path)
                        for item in facts
                        if framework in str(item.properties).lower()
                        or framework in item.parser.lower()
                    )[:10],
                    dimensions={
                        "configuration": "partial",
                        "endpoints": "partial",
                        "security": "unknown",
                        "persistence": "partial",
                        "messaging_jobs": "unknown",
                        "ui_resources": "partial",
                        "deployment": "unknown",
                    },
                )
            )

    module_ids = {item.module_path: item.id for item in modules}
    special_files = tuple(
        SpecialFileFact(
            descriptor_id=descriptor.id,
            project_id=project_id,
            module_id=module_ids[descriptor.module_path],
            path=descriptor.path,
            role=descriptor.role,
            parser=descriptor.parser,
            parse_depth=descriptor.parse_depth,
            canonical=descriptor.canonical,
            generated=descriptor.generated,
            secret_bearing=descriptor.secret_bearing,
            redacted=descriptor.redacted,
            safe_summary=descriptor.summary,
            diagnostics=descriptor.diagnostics,
        )
        for descriptor in sorted(descriptors, key=lambda item: item.path)
    )
    for cycle in _detect_cycles(resolved_dependencies):
        diagnostics.append(
            AnalysisDiagnostic(
                DiagnosticCode.UNRESOLVED_REFERENCE,
                "Internal module dependency cycle detected.",
                module_path=cycle[0],
                details={"cycle": list(cycle)},
            )
        )
    return (
        tuple(modules),
        tuple(sorted(resolved_dependencies, key=lambda item: item.id)),
        special_files,
        tuple(sorted(frameworks, key=lambda item: item.id)),
        tuple(diagnostics),
    )


__all__ = ["resolve_topology"]

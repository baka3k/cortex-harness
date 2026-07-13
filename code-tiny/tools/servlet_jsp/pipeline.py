from __future__ import annotations

import os
import resource
import sys
import time
from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tools.servlet_jsp.detector import ServletJspProjectDetector
from tools.servlet_jsp.java_identity import JavaIdentityProvider
from tools.servlet_jsp.java_semantics import analyze_java_file
from tools.servlet_jsp.jsp_parser import parse_jsp_file
from tools.servlet_jsp.models import (
    Diagnostic,
    ResourceBudgets,
    ServletJspAnalysisResult,
    ServletJspArtifact,
    ServletJspDependencyIndex,
    ServletJspFact,
    ServletJspModule,
    SourceSpan,
    stable_semantic_id,
)
from tools.servlet_jsp.parser_runtime import check_parser_capabilities
from tools.servlet_jsp.properties_parser import parse_properties_file
from tools.servlet_jsp.resolver import resolve_servlet_jsp_module
from tools.servlet_jsp.web_xml_parser import parse_web_xml_file


_ARTIFACT_GROUPS = {
    "java_files": "java",
    "descriptor_files": "web_xml",
    "jsp_files": "jsp",
    "properties_files": "properties",
    "build_files": "build",
    "static_files": "static",
}


def run_servlet_jsp_analysis(
    *,
    root: str,
    project_id: str,
    project_name: str,
    budgets: Optional[ResourceBudgets] = None,
    selected_paths: Optional[Sequence[str]] = None,
    deleted_paths: Sequence[str] = (),
) -> ServletJspAnalysisResult:
    """Run the complete deterministic Servlet/JSP semantic overlay pipeline."""

    started_at = time.monotonic()
    effective_budgets = budgets or ResourceBudgets()
    foundation = run_servlet_jsp_foundation(
        root=root,
        project_id=project_id,
        project_name=project_name,
        budgets=effective_budgets,
        selected_paths=selected_paths,
        deleted_paths=deleted_paths,
    )
    _check_operational_budgets(started_at, effective_budgets)
    fact_by_id = {item.stable_id: item for item in foundation.semantic_facts}
    relationship_by_id = {item.stable_id: item for item in foundation.relationships}
    diagnostics = list(foundation.diagnostics)
    file_dependencies: Dict[str, set[str]] = {key: set(values) for key, values in foundation.dependency_index.files.items()}
    component_dependencies: Dict[str, set[str]] = {}
    mapping_dependencies: Dict[str, set[str]] = {}
    view_dependencies: Dict[str, set[str]] = {}
    state_dependencies: Dict[str, set[str]] = {}
    identity_provider = JavaIdentityProvider(foundation.root)
    truncation_count = foundation.truncation_count
    ambiguity_count = foundation.ambiguity_count
    missing_anchor_count = foundation.missing_anchor_count

    for module in foundation.modules:
        remaining_facts = max(0, effective_budgets.max_facts_per_project - len(fact_by_id))
        remaining_relationships = max(0, effective_budgets.max_relationships_per_project - len(relationship_by_id))
        remaining_diagnostics = max(0, effective_budgets.max_diagnostics_per_project - len(diagnostics))
        module_budgets = replace(
            effective_budgets,
            max_facts_per_project=remaining_facts,
            max_relationships_per_project=remaining_relationships,
            max_diagnostics_per_project=remaining_diagnostics,
            max_diagnostics_per_file=min(effective_budgets.max_diagnostics_per_file, remaining_diagnostics),
        )
        java_results = [
            analyze_java_file(
                root=foundation.root,
                project_id=project_id,
                project_name=project_name,
                module_id=module.module_id,
                file_path=path,
                budgets=module_budgets,
                identity_provider=identity_provider,
            )
            for path in module.java_files
        ]
        web_results = [
            parse_web_xml_file(
                root=foundation.root,
                file_path=path,
                project_id=project_id,
                project_name=project_name,
                module_id=module.module_id,
                module_path=module.rel_path,
                budgets=module_budgets,
            )
            for path in module.descriptor_files
        ]
        jsp_results = [parse_jsp_file(foundation.root, path, budgets=module_budgets) for path in module.jsp_files]
        properties_results = [parse_properties_file(foundation.root, path, budgets=module_budgets) for path in module.properties_files]
        resolved = resolve_servlet_jsp_module(
            project_id=project_id,
            project_name=project_name,
            module=module,
            java_results=java_results,
            web_results=web_results,
            jsp_results=jsp_results,
            properties_results=properties_results,
            budgets=module_budgets,
        )
        for fact in resolved.facts:
            fact_by_id[fact.stable_id] = fact
        for relationship in resolved.relationships:
            relationship_by_id[relationship.stable_id] = relationship
        diagnostics.extend(resolved.diagnostics)
        _merge_index(file_dependencies, resolved.dependency_index.files)
        _merge_index(component_dependencies, resolved.dependency_index.components)
        _merge_index(mapping_dependencies, resolved.dependency_index.mappings)
        _merge_index(view_dependencies, resolved.dependency_index.views)
        _merge_index(state_dependencies, resolved.dependency_index.state_slots)
        truncation_count += resolved.truncation_count
        ambiguity_count += resolved.ambiguity_count
        missing_anchor_count += resolved.missing_anchor_count
        _check_operational_budgets(started_at, effective_budgets)

    semantic_facts = sorted(fact_by_id.values(), key=lambda item: item.stable_id)
    if len(semantic_facts) > effective_budgets.max_facts_per_project:
        diagnostics.append(Diagnostic("servlet_jsp.budget.facts", f"Fact budget {effective_budgets.max_facts_per_project} reached", "warning"))
        semantic_facts = semantic_facts[: effective_budgets.max_facts_per_project]
        truncation_count += 1
    allowed_fact_ids = {item.stable_id for item in semantic_facts}
    relationships = sorted(
        (
            item
            for item in relationship_by_id.values()
            if (not item.from_generated or item.from_id in allowed_fact_ids)
            and (not item.to_generated or item.to_id in allowed_fact_ids)
        ),
        key=lambda item: item.stable_id,
    )
    if len(relationships) > effective_budgets.max_relationships_per_project:
        diagnostics.append(Diagnostic("servlet_jsp.budget.relationships", f"Relationship budget {effective_budgets.max_relationships_per_project} reached", "warning"))
        relationships = relationships[: effective_budgets.max_relationships_per_project]
        truncation_count += 1
    dependency_index, dependency_truncated = _bounded_dependency_index(
        ServletJspDependencyIndex(
            files=_frozen_index(file_dependencies),
            components=_frozen_index(component_dependencies),
            mappings=_frozen_index(mapping_dependencies),
            views=_frozen_index(view_dependencies),
            state_slots=_frozen_index(state_dependencies),
        ),
        effective_budgets.max_dependency_entries,
    )
    if dependency_truncated:
        diagnostics.append(Diagnostic("servlet_jsp.budget.dependencies", f"Dependency entry budget {effective_budgets.max_dependency_entries} reached", "warning"))
        truncation_count += 1
    bounded_diagnostics, diagnostics_truncated = _bounded_project_diagnostics(
        diagnostics,
        effective_budgets.max_diagnostics_per_project,
    )
    if diagnostics_truncated:
        truncation_count += 1
    diagnostics = bounded_diagnostics
    _check_operational_budgets(started_at, effective_budgets)
    mandatory_missing = any(item.mandatory and not item.available for item in foundation.parser_capabilities)
    if not foundation.modules:
        coverage = "empty"
    elif mandatory_missing or truncation_count or any(item.severity == "error" for item in diagnostics):
        coverage = "partial"
    else:
        coverage = "complete"
    return ServletJspAnalysisResult(
        project_id=project_id,
        project_name=project_name,
        root=foundation.root,
        modules=foundation.modules,
        artifacts=foundation.artifacts,
        parser_capabilities=foundation.parser_capabilities,
        semantic_facts=tuple(semantic_facts),
        relationships=tuple(relationships),
        dependency_index=dependency_index,
        diagnostics=tuple(sorted(_dedupe_diagnostics(diagnostics), key=lambda item: (item.file_path, item.start_line, item.code, item.message))),
        coverage_status=coverage,
        missing_anchor_count=missing_anchor_count,
        ambiguity_count=ambiguity_count,
        truncation_count=truncation_count,
    )


def run_servlet_jsp_foundation(
    *,
    root: str,
    project_id: str,
    project_name: str,
    budgets: Optional[ResourceBudgets] = None,
    selected_paths: Optional[Sequence[str]] = None,
    deleted_paths: Sequence[str] = (),
) -> ServletJspAnalysisResult:
    started_at = time.monotonic()
    project_root = os.path.realpath(os.path.abspath(root))
    effective_budgets = budgets or ResourceBudgets()
    detector = ServletJspProjectDetector(project_root)
    discovered = detector.discover_modules()
    capabilities, capability_diagnostics = check_parser_capabilities()
    selected = {_normalize(path) for path in selected_paths or () if path}
    modules: List[ServletJspModule] = []
    artifacts: List[ServletJspArtifact] = []
    facts: List[ServletJspFact] = []
    diagnostics: List[Diagnostic] = list(capability_diagnostics)

    total_artifacts = 0
    total_source_bytes = 0
    artifact_budget_reported = False
    byte_budget_reported = False
    for item in sorted(discovered, key=lambda row: str(row["rel_path"])):
        _check_operational_budgets(started_at, effective_budgets)
        bounded_paths: Dict[str, Tuple[str, ...]] = {}
        for attr in _ARTIFACT_GROUPS:
            accepted: List[str] = []
            for file_path in _filtered(item[attr], selected):
                if total_artifacts >= effective_budgets.max_artifacts_per_project:
                    if not artifact_budget_reported:
                        diagnostics.append(Diagnostic("servlet_jsp.budget.artifacts", f"Artifact budget {effective_budgets.max_artifacts_per_project} reached", "warning", file_path, hint="Increase the deterministic artifact budget or narrow the scan."))
                        artifact_budget_reported = True
                    continue
                try:
                    source_bytes = os.path.getsize(os.path.join(project_root, file_path))
                except OSError:
                    source_bytes = 0
                if total_source_bytes + source_bytes > effective_budgets.max_total_source_bytes:
                    if not byte_budget_reported:
                        diagnostics.append(Diagnostic("servlet_jsp.budget.total_source_bytes", f"Total source byte budget {effective_budgets.max_total_source_bytes} reached", "warning", file_path))
                        byte_budget_reported = True
                    continue
                accepted.append(file_path)
                total_artifacts += 1
                total_source_bytes += source_bytes
            bounded_paths[attr] = tuple(accepted)
        module = ServletJspModule(
            module_id=str(item["module_id"]),
            root=project_root,
            rel_path=str(item["rel_path"]),
            java_files=bounded_paths["java_files"],
            descriptor_files=bounded_paths["descriptor_files"],
            jsp_files=bounded_paths["jsp_files"],
            properties_files=bounded_paths["properties_files"],
            build_files=bounded_paths["build_files"],
            static_files=bounded_paths["static_files"],
            evidence=tuple(item["evidence"]),
            confidence=float(item["confidence"]),
        )
        if not any(
            (module.java_files, module.descriptor_files, module.jsp_files, module.properties_files, module.build_files, module.static_files)
        ):
            continue
        modules.append(module)
        facts.append(_module_fact(project_id, project_name, module))
        for attr, kind in _ARTIFACT_GROUPS.items():
            for file_path in getattr(module, attr):
                evidence = detector.detect_path(file_path).evidence
                artifact = ServletJspArtifact(
                    kind=_artifact_kind(kind, file_path),
                    file_path=file_path,
                    module_id=module.module_id,
                    module_path=module.rel_path,
                    evidence=evidence,
                    confidence=module.confidence,
                    source=SourceSpan(file_path),
                )
                artifacts.append(artifact)
                if artifact.kind in {"web_xml", "jsp", "jspx", "jsp_fragment", "static"}:
                    facts.append(_artifact_fact(project_id, project_name, artifact))

    for path in sorted({_normalize(item) for item in deleted_paths if item}):
        diagnostics.append(
            Diagnostic(
                "servlet_jsp.incremental.deleted_path",
                "Deleted path requires applied-snapshot dependency expansion and module regeneration",
                "info",
                path,
            )
        )
    mandatory_missing = any(item.mandatory and not item.available for item in capabilities)
    facts = sorted(facts, key=lambda item: item.stable_id)
    if len(facts) > effective_budgets.max_facts_per_project:
        diagnostics.append(Diagnostic("servlet_jsp.budget.facts", f"Fact budget {effective_budgets.max_facts_per_project} reached", "warning"))
        facts = facts[: effective_budgets.max_facts_per_project]
    truncation_count = sum(1 for item in diagnostics if ".budget." in item.code)
    bounded_diagnostics, diagnostics_truncated = _bounded_project_diagnostics(
        diagnostics,
        effective_budgets.max_diagnostics_per_project,
    )
    if diagnostics_truncated:
        truncation_count += 1
    diagnostics = bounded_diagnostics
    if not modules:
        coverage = "empty"
    elif mandatory_missing or truncation_count:
        coverage = "partial"
    else:
        coverage = "complete"
    dependency_index = ServletJspDependencyIndex(
        files={item.file_path: () for item in sorted(artifacts, key=lambda artifact: artifact.file_path)}
    )
    _check_operational_budgets(started_at, effective_budgets)
    return ServletJspAnalysisResult(
        project_id=project_id,
        project_name=project_name,
        root=project_root,
        modules=tuple(sorted(modules, key=lambda item: item.rel_path)),
        artifacts=tuple(sorted(artifacts, key=lambda item: (item.module_id, item.file_path, item.kind))),
        parser_capabilities=tuple(capabilities),
        semantic_facts=tuple(facts),
        relationships=(),
        dependency_index=dependency_index,
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.file_path, item.start_line, item.code, item.message))),
        coverage_status=coverage,
        truncation_count=truncation_count,
    )


def _module_fact(project_id: str, project_name: str, module: ServletJspModule) -> ServletJspFact:
    return ServletJspFact(
        kind="ServletJspModule",
        stable_id=stable_semantic_id("module", project_id, module.module_id, module.rel_path),
        name=module.rel_path,
        source=SourceSpan(module.rel_path if module.rel_path != "." else ""),
        project_id=project_id,
        project_name=project_name,
        module_id=module.module_id,
        confidence=module.confidence,
        extraction_method="module_detector",
        properties={"module_path": module.rel_path, "evidence": list(module.evidence)},
    )


def _artifact_fact(project_id: str, project_name: str, artifact: ServletJspArtifact) -> ServletJspFact:
    return ServletJspFact(
        kind="WebDescriptor" if artifact.kind == "web_xml" else "JSPView" if artifact.kind in {"jsp", "jspx", "jsp_fragment"} else "WebTarget",
        stable_id=stable_semantic_id("artifact", project_id, artifact.module_id, artifact.kind, artifact.file_path),
        name=os.path.basename(artifact.file_path),
        source=artifact.source,
        project_id=project_id,
        project_name=project_name,
        module_id=artifact.module_id,
        confidence=artifact.confidence,
        extraction_method="artifact_detector",
        properties={"artifact_kind": artifact.kind, "module_path": artifact.module_path, "evidence": list(artifact.evidence)},
    )


def _filtered(paths: Iterable[str], selected: set[str]) -> Tuple[str, ...]:
    values = tuple(sorted(_normalize(path) for path in paths))
    if not selected:
        return values
    return tuple(path for path in values if path in selected)


def _artifact_kind(default: str, file_path: str) -> str:
    lower = file_path.lower()
    if lower.endswith(".jspx"):
        return "jspx"
    if lower.endswith(".jspf"):
        return "jsp_fragment"
    return default


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _merge_index(target: Dict[str, set[str]], source: Dict[str, Tuple[str, ...]]) -> None:
    for key, values in source.items():
        target.setdefault(key, set()).update(values)


def _frozen_index(values: Dict[str, set[str]]) -> Dict[str, Tuple[str, ...]]:
    return {key: tuple(sorted(items)) for key, items in sorted(values.items())}


def _dedupe_diagnostics(values: Iterable[Diagnostic]) -> List[Diagnostic]:
    rows: Dict[Tuple[str, str, int, int, str], Diagnostic] = {}
    for item in values:
        rows[(item.code, item.file_path, item.start_line, item.end_line, item.message)] = item
    return list(rows.values())


def _bounded_project_diagnostics(values: Iterable[Diagnostic], maximum: int) -> Tuple[List[Diagnostic], bool]:
    rows = sorted(
        _dedupe_diagnostics(values),
        key=lambda item: (item.file_path, item.start_line, item.code, item.message),
    )
    limit = max(0, maximum)
    if len(rows) <= limit:
        return rows, False
    if limit == 0:
        return [], True
    marker = Diagnostic(
        "servlet_jsp.budget.diagnostics",
        f"Project diagnostic budget {limit} reached",
        "warning",
    )
    return [*rows[: limit - 1], marker], True


def _check_operational_budgets(started_at: float, budgets: ResourceBudgets) -> None:
    if budgets.max_wall_time_seconds > 0 and time.monotonic() - started_at > budgets.max_wall_time_seconds:
        raise RuntimeError(f"Servlet/JSP wall-time limit {budgets.max_wall_time_seconds:g}s exceeded")
    if budgets.max_peak_rss_bytes <= 0:
        return
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(usage if sys.platform == "darwin" else usage * 1024)
    if peak_bytes > budgets.max_peak_rss_bytes:
        raise RuntimeError(f"Servlet/JSP peak-RSS limit {budgets.max_peak_rss_bytes} bytes exceeded")


def _bounded_dependency_index(index: ServletJspDependencyIndex, maximum: int) -> Tuple[ServletJspDependencyIndex, bool]:
    remaining = max(0, maximum)
    truncated = False
    bounded: Dict[str, Dict[str, Tuple[str, ...]]] = {}
    for field_name in ("files", "components", "mappings", "views", "state_slots"):
        output: Dict[str, Tuple[str, ...]] = {}
        values = getattr(index, field_name)
        for key, targets in sorted(values.items()):
            accepted = tuple(targets[:remaining])
            if accepted:
                output[key] = accepted
            remaining -= len(accepted)
            if len(accepted) != len(targets):
                truncated = True
        bounded[field_name] = output
    return ServletJspDependencyIndex(**bounded), truncated

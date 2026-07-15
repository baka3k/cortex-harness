from __future__ import annotations

import os
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

from tools.aspnet_framework.artifact_parsers import parse_legacy_markup, parse_resx, parse_web_config
from tools.aspnet_framework.detector import AspNetFrameworkDetector
from tools.aspnet_framework.resolver import connect_request_pipeline, resolve_roslyn_evidence
from tools.common.aspnet.models import (
    AnalysisModule, AnalysisResult, DependencyIndex, Diagnostic, ParserCapability,
    SemanticFact, SemanticRelationship, SourceSpan, dedupe_facts, dedupe_relationships,
)
from tools.common.aspnet.identity import module_id as make_module_id
from tools.common.aspnet.project_metadata import ModuleDetection, infer_deleted_module_path
from tools.common.aspnet.roslyn_adapter import analyze_csharp_files


FRAMEWORK = "aspnet_framework"


def run_aspnet_framework_analysis(
    *,
    root: str,
    project_id: str,
    project_name: str | None = None,
    semantic_mode: str = "auto",
    deleted_paths: Sequence[str] = (),
    selected_paths: Sequence[str] = (),
    worker_project_path: str | None = None,
    verbose: bool = False,
) -> AnalysisResult:
    root_abs = os.path.realpath(os.path.abspath(root))
    project_name = project_name or project_id
    detector = AspNetFrameworkDetector(root_abs)
    detections = detector.discover_modules()
    selected = {path.replace("\\", "/") for path in selected_paths if path}
    if selected:
        detections = tuple(
            item for item in detections
            if any(_in_module(path, item.module_path) for path in selected)
        )
    modules: List[AnalysisModule] = []
    facts: List[SemanticFact] = []
    relationships: List[SemanticRelationship] = []
    diagnostics: List[Diagnostic] = []
    capabilities: List[ParserCapability] = []
    module_coverage: Dict[str, str] = {}
    dependencies: Dict[str, set[str]] = defaultdict(set)
    for detection in detections:
        module_coverage[detection.module_id] = "complete"
        modules.append(AnalysisModule(
            detection.module_id, detection.module_path, FRAMEWORK,
            tuple(sorted((*detection.evidence, *detection.supporting_evidence))),
            detection.confidence, detection.artifacts,
        ))
        csharp_files = [path for path in detection.artifacts if path.lower().endswith(".cs")]
        try:
            roslyn = analyze_csharp_files(
                root=root_abs, files=csharp_files, semantic_mode=semantic_mode,
                project_path=_module_project_path(detection),
                worker_project_path=worker_project_path,
                verbose=verbose,
            )
            capabilities.append(ParserCapability(
                "roslyn_csharp", True, True, semantic_mode,
                str(roslyn.get("coverage_status") or "partial"),
                f"workspace={roslyn.get('workspace_kind', 'none')} semantic={bool(roslyn.get('semantic_enabled'))}",
            ))
            if str(roslyn.get("coverage_status") or "partial") != "complete":
                module_coverage[detection.module_id] = "partial"
            resolved_facts, resolved_relationships = resolve_roslyn_evidence(
                payload=roslyn, project_id=project_id, project_name=project_name,
                module_id=detection.module_id,
            )
            facts.extend(resolved_facts)
            relationships.extend(resolved_relationships)
            for item in roslyn.get("diagnostics") or ():
                diagnostics.append(Diagnostic(
                    str(item.get("code") or "aspnet_framework.roslyn"),
                    str(item.get("message") or "Roslyn diagnostic"),
                    str(item.get("severity") or "warning"),
                    SourceSpan(str(item.get("file_path") or "")),
                ))
        except Exception as exc:
            capabilities.append(ParserCapability("roslyn_csharp", False, True, semantic_mode, "unavailable", str(exc)))
            module_coverage[detection.module_id] = "partial"
            diagnostics.append(Diagnostic(
                "aspnet_framework.roslyn_unavailable", str(exc),
                "error" if semantic_mode == "on" else "warning", SourceSpan(detection.module_path),
            ))
        for path in detection.artifacts:
            lower = path.lower()
            parsed = None
            if lower.endswith((".aspx", ".ascx", ".master", ".asmx", ".ashx")) or os.path.basename(lower) == "global.asax":
                parsed = parse_legacy_markup(
                    root=root_abs, path=path, project_id=project_id,
                    project_name=project_name, module_id=detection.module_id,
                )
            elif os.path.basename(lower) == "web.config":
                parsed = parse_web_config(
                    root=root_abs, path=path, project_id=project_id,
                    project_name=project_name, module_id=detection.module_id,
                )
            elif lower.endswith(".resx"):
                parsed = parse_resx(
                    root=root_abs, path=path, project_id=project_id,
                    project_name=project_name, module_id=detection.module_id,
                )
            if parsed is None:
                continue
            facts.extend(parsed.facts)
            relationships.extend(parsed.relationships)
            diagnostics.extend(parsed.diagnostics)
            if any(item.severity == "error" for item in parsed.diagnostics):
                module_coverage[detection.module_id] = "partial"
            for source, targets in (parsed.dependencies or {}).items():
                dependencies[source].update(targets)
        for deleted in deleted_paths:
            if _in_module(deleted, detection.module_path):
                diagnostics.append(Diagnostic(
                    "aspnet_framework.deleted_artifact", "Deleted artifact is included in module invalidation",
                    "info", SourceSpan(deleted),
                ))
    live_module_ids = {item.module_id for item in modules}
    for deleted in sorted({path.replace("\\", "/") for path in deleted_paths if path}):
        invalidated = detector.detect_path(deleted, include_undetected=True)
        inferred_path = infer_deleted_module_path(FRAMEWORK, deleted) if invalidated is None else ""
        invalidated_id = invalidated.module_id if invalidated else (
            make_module_id(FRAMEWORK, inferred_path) if inferred_path else ""
        )
        invalidated_path = invalidated.module_path if invalidated else inferred_path
        if not invalidated_id or invalidated_id in live_module_ids:
            continue
        modules.append(AnalysisModule(
            invalidated_id, invalidated_path, FRAMEWORK,
            (f"{deleted}:deleted",), 0.0, (),
        ))
        module_coverage[invalidated_id] = "partial"
        live_module_ids.add(invalidated_id)
        diagnostics.append(Diagnostic(
            "aspnet_framework.deleted_module_cleanup",
            "The module no longer matches ASP.NET Framework; an empty generation will remove stale overlay facts",
            "info", SourceSpan(deleted),
        ))
    relationships = list(connect_request_pipeline(facts, relationships))
    facts_tuple = dedupe_facts(facts)
    rels_tuple = dedupe_relationships(relationships)
    has_errors = any(item.severity == "error" for item in diagnostics)
    partial = has_errors or any(item.status not in {"complete", "ok"} for item in capabilities)
    coverage = "empty" if not modules else "partial" if partial else "complete"
    return AnalysisResult(
        project_id=project_id, project_name=project_name, framework=FRAMEWORK,
        modules=tuple(sorted(modules, key=lambda item: item.module_id)),
        facts=facts_tuple, relationships=rels_tuple,
        capabilities=tuple(capabilities), diagnostics=tuple(diagnostics),
        dependency_index=DependencyIndex(files={key: tuple(sorted(value)) for key, value in sorted(dependencies.items())}),
        module_coverage=module_coverage,
        coverage_status=coverage,
    )


def _in_module(path: str, module_path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    module = module_path.replace("\\", "/").strip("/")
    return module in {"", "."} or normalized == module or normalized.startswith(module + "/")


def _module_project_path(detection: ModuleDetection) -> str:
    evidence_projects = sorted({
        item.rsplit(":", 1)[0]
        for item in (*detection.evidence, *detection.supporting_evidence)
        if item.rsplit(":", 1)[0].lower().endswith(".csproj")
    })
    if evidence_projects:
        return evidence_projects[0]
    projects = sorted(path for path in detection.artifacts if path.lower().endswith(".csproj"))
    return projects[0] if projects else ""

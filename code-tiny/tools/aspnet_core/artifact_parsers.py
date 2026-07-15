from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from tools.common.aspnet.builders import fact, relationship
from tools.common.aspnet.models import Diagnostic, SemanticFact, SemanticRelationship, SourceSpan
from tools.common.aspnet.safe_formats import flatten_json, parse_json_file, read_bounded_text


FRAMEWORK = "aspnet_core"
_PAGE_RE = re.compile(r"^\s*@page(?:\s+([\"'])(.*?)\1)?", re.MULTILINE)
_MODEL_RE = re.compile(r"^\s*@model\s+([^\s]+)", re.MULTILINE)
_LAYOUT_RE = re.compile(r"\bLayout\s*=\s*[\"']([^\"']+)[\"']")
_PARTIAL_RE = re.compile(
    r"(?:<partial\s+[^>]*name\s*=\s*[\"']([^\"']+)[\"']|PartialAsync\s*\(\s*[\"']([^\"']+)[\"'])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ArtifactParseResult:
    facts: Tuple[SemanticFact, ...] = ()
    relationships: Tuple[SemanticRelationship, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    dependencies: Dict[str, Tuple[str, ...]] | None = None


def parse_razor(
    *, root: str, path: str, project_id: str, project_name: str, module_id: str,
) -> ArtifactParseResult:
    try:
        text, relative, truncated = read_bounded_text(root, path, 2 * 1024 * 1024)
    except (OSError, ValueError) as exc:
        return ArtifactParseResult(diagnostics=(Diagnostic(
            "aspnet_core.razor.read_error", str(exc), "error", SourceSpan(path)
        ),))
    page_match = _PAGE_RE.search(text)
    kind = "RazorPage" if page_match or "/pages/" in f"/{relative.lower()}" else "View"
    route = page_match.group(2) if page_match and page_match.group(2) else ""
    model_match = _MODEL_RE.search(text)
    page = fact(
        kind=kind, name=relative, framework=FRAMEWORK, project_id=project_id,
        project_name=project_name, module_id=module_id, source=SourceSpan(relative),
        coordinates=(route,), extraction_method="razor_source",
        properties={"path": relative, "route": route, "model": model_match.group(1) if model_match else ""},
    )
    facts: List[SemanticFact] = [page]
    relationships: List[SemanticRelationship] = []
    dependencies: set[str] = set()
    if page_match:
        endpoint = fact(
            kind="HttpEndpoint", name=route or relative, framework=FRAMEWORK,
            project_id=project_id, project_name=project_name, module_id=module_id,
            source=SourceSpan(relative), coordinates=("razor-page", route),
            extraction_method="razor_source", properties={"route": route, "http_method": "GET|POST"},
        )
        route_fact = fact(
            kind="Route", name=route or relative, framework=FRAMEWORK,
            project_id=project_id, project_name=project_name, module_id=module_id,
            source=SourceSpan(relative), coordinates=("route", route),
            extraction_method="razor_source", properties={"route": route},
        )
        facts.extend((endpoint, route_fact))
        relationships.extend((
            relationship(relationship_type="MAPPED_TO", source_fact=endpoint, target_fact=route_fact),
            relationship(relationship_type="HANDLED_BY", source_fact=endpoint, target_fact=page),
        ))
    layout_match = _LAYOUT_RE.search(text)
    if layout_match:
        layout_name = layout_match.group(1)
        layout = fact(
            kind="Layout", name=layout_name, framework=FRAMEWORK, project_id=project_id,
            project_name=project_name, module_id=module_id, source=SourceSpan(relative),
            coordinates=("layout", layout_name), confidence=0.9, resolution_status="unresolved",
            extraction_method="razor_source", properties={"path": layout_name},
        )
        facts.append(layout)
        relationships.append(relationship(
            relationship_type="RENDERS", source_fact=page, target_fact=layout,
            confidence=0.9, resolution_status="unresolved", reason="Razor Layout assignment",
        ))
        dependencies.add(layout_name)
    for index, partial_match in enumerate(_PARTIAL_RE.finditer(text)):
        partial_name = partial_match.group(1) or partial_match.group(2) or ""
        partial = fact(
            kind="PartialView", name=partial_name, framework=FRAMEWORK, project_id=project_id,
            project_name=project_name, module_id=module_id,
            source=SourceSpan(relative, text.count("\n", 0, partial_match.start()) + 1),
            coordinates=("partial", partial_name, index), confidence=0.9, resolution_status="unresolved",
            extraction_method="razor_source", properties={"path": partial_name},
        )
        facts.append(partial)
        relationships.append(relationship(
            relationship_type="RENDERS", source_fact=page, target_fact=partial,
            confidence=0.9, resolution_status="unresolved", reason="Razor partial reference",
        ))
        dependencies.add(partial_name)
    diagnostics = (Diagnostic(
        "aspnet_core.razor.truncated", "Razor source exceeded the 2 MiB budget", "warning", SourceSpan(relative)
    ),) if truncated else ()
    return ArtifactParseResult(
        facts=tuple(facts), relationships=tuple(relationships), diagnostics=diagnostics,
        dependencies={relative: tuple(sorted(dependencies))},
    )


def parse_appsettings(
    *, root: str, path: str, project_id: str, project_name: str, module_id: str,
) -> ArtifactParseResult:
    try:
        value, relative, truncated, duplicates = parse_json_file(root, path, 1024 * 1024)
    except Exception as exc:
        return ArtifactParseResult(diagnostics=(Diagnostic(
            "aspnet_core.config.parse_error", str(exc), "error", SourceSpan(path)
        ),))
    environment = os.path.basename(relative)[len("appsettings") : -len(".json")].strip(".") or "default"
    facts = tuple(
        fact(
            kind="ConfigurationKey", name=key, framework=FRAMEWORK, project_id=project_id,
            project_name=project_name, module_id=module_id, source=SourceSpan(relative),
            coordinates=(environment, key), extraction_method="safe_json",
            properties={"config_key": key, "value": item, "environment": environment, "provenance": relative},
        )
        for key, item in sorted(flatten_json(value).items())
        if key
    )
    diagnostics: List[Diagnostic] = []
    if truncated:
        diagnostics.append(Diagnostic(
            "aspnet_core.config.truncated", "appsettings file exceeded the 1 MiB budget", "warning", SourceSpan(relative)
        ))
    for key in duplicates:
        diagnostics.append(Diagnostic(
            "aspnet_core.config.duplicate_key", f"Duplicate JSON key: {key}", "warning", SourceSpan(relative)
        ))
    return ArtifactParseResult(facts=facts, diagnostics=tuple(diagnostics), dependencies={relative: ()})

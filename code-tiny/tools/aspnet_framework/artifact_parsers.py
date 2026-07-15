from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

from tools.common.aspnet.builders import fact, relationship
from tools.common.aspnet.models import Diagnostic, SemanticFact, SemanticRelationship, SourceSpan
from tools.common.aspnet.safe_formats import local_name, parse_xml_file, read_bounded_text, redact_value


FRAMEWORK = "aspnet_framework"
_DIRECTIVE_RE = re.compile(r"<%@\s*(Page|Control|Master|WebService|WebHandler)\b(.*?)%>", re.I | re.S)
_ATTRIBUTE_RE = re.compile(r"([A-Za-z_:][\w:.-]*)\s*=\s*([\"'])(.*?)\2", re.S)
_SERVER_TAG_RE = re.compile(r"<(?P<tag>[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*)\b(?P<attrs>[^>]*)>", re.I | re.S)
_EVENT_RE = re.compile(r"\b(On[A-Z][A-Za-z0-9_]*)\s*=\s*([\"'])(.*?)\2")


@dataclass(frozen=True)
class ArtifactParseResult:
    facts: Tuple[SemanticFact, ...] = ()
    relationships: Tuple[SemanticRelationship, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    dependencies: Dict[str, Tuple[str, ...]] | None = None


def parse_legacy_markup(
    *, root: str, path: str, project_id: str, project_name: str, module_id: str,
) -> ArtifactParseResult:
    diagnostics: List[Diagnostic] = []
    try:
        text, relative, truncated = read_bounded_text(root, path, 2 * 1024 * 1024)
    except (OSError, ValueError) as exc:
        return ArtifactParseResult(diagnostics=(Diagnostic(
            "aspnet_framework.markup.read_error", str(exc), "error", SourceSpan(path)
        ),))
    if truncated:
        diagnostics.append(Diagnostic(
            "aspnet_framework.markup.truncated", "Markup exceeded the 2 MiB budget", "warning", SourceSpan(relative)
        ))
    match = _DIRECTIVE_RE.search(text)
    directive = match.group(1).lower() if match else ""
    attributes = {
        item.group(1): item.group(3)
        for item in _ATTRIBUTE_RE.finditer(match.group(2) if match else "")
    }
    kind = "WebFormPage"
    if directive in {"webservice", "webhandler"} or relative.lower().endswith((".asmx", ".ashx")):
        kind = "HttpHandler"
    name = attributes.get("Inherits") or os.path.splitext(os.path.basename(relative))[0]
    page = fact(
        kind=kind, name=name, framework=FRAMEWORK, project_id=project_id,
        project_name=project_name, module_id=module_id, source=SourceSpan(relative),
        confidence=1.0 if match else 0.7,
        resolution_status="resolved" if attributes.get("Inherits") else "partial",
        extraction_method="legacy_markup",
        properties={
            "artifact_kind": directive or os.path.splitext(relative)[1].lstrip("."),
            "code_behind": attributes.get("CodeBehind") or attributes.get("CodeFile") or "",
            "inherits": attributes.get("Inherits") or "",
            "master_page_file": attributes.get("MasterPageFile") or "",
            "language": attributes.get("Language") or "",
        },
    )
    facts: List[SemanticFact] = [page]
    relationships: List[SemanticRelationship] = []
    master_path = attributes.get("MasterPageFile")
    if master_path:
        layout = fact(
            kind="Layout", name=master_path, framework=FRAMEWORK, project_id=project_id,
            project_name=project_name, module_id=module_id, source=SourceSpan(relative),
            coordinates=("master", master_path), confidence=0.9, resolution_status="unresolved",
            extraction_method="legacy_markup", properties={"path": master_path},
        )
        facts.append(layout)
        relationships.append(relationship(
            relationship_type="RENDERS", source_fact=page, target_fact=layout,
            confidence=0.9, resolution_status="unresolved", reason="MasterPageFile directive",
        ))
    for index, tag_match in enumerate(_SERVER_TAG_RE.finditer(text)):
        tag = tag_match.group("tag")
        attrs = tag_match.group("attrs")
        attr_values = {item.group(1): item.group(3) for item in _ATTRIBUTE_RE.finditer(attrs)}
        control_name = attr_values.get("ID") or f"{tag}#{index}"
        control = fact(
            kind="PartialView", name=control_name, framework=FRAMEWORK, project_id=project_id,
            project_name=project_name, module_id=module_id,
            source=SourceSpan(relative, text.count("\n", 0, tag_match.start()) + 1),
            coordinates=(tag, index), confidence=0.85, resolution_status="partial",
            extraction_method="legacy_markup",
            properties={"tag_name": tag, "control_id": attr_values.get("ID") or ""},
        )
        facts.append(control)
        relationships.append(relationship(
            relationship_type="DEPENDS_ON", source_fact=page, target_fact=control,
            confidence=0.85, resolution_status="partial", reason="server control declaration",
        ))
        for event_match in _EVENT_RE.finditer(attrs):
            handler = fact(
                kind="PageHandler", name=event_match.group(3), framework=FRAMEWORK,
                project_id=project_id, project_name=project_name, module_id=module_id,
                source=control.source, coordinates=(control_name, event_match.group(1)),
                confidence=0.9, resolution_status="unresolved", extraction_method="legacy_markup",
                properties={"event": event_match.group(1), "control_id": control_name},
            )
            facts.append(handler)
            relationships.append(relationship(
                relationship_type="POSTS_BACK_TO", source_fact=control, target_fact=handler,
                confidence=0.9, resolution_status="unresolved", reason="server control event",
            ))
    if re.search(r"\bViewState\b|EnableViewState\s*=", text, re.I):
        state = fact(
            kind="SessionState", name="ViewState", framework=FRAMEWORK, project_id=project_id,
            project_name=project_name, module_id=module_id, source=SourceSpan(relative),
            coordinates=("ViewState",), confidence=0.9, extraction_method="legacy_markup",
            properties={"state_kind": "view_state"},
        )
        facts.append(state)
        relationships.append(relationship(
            relationship_type="WRITES_SESSION", source_fact=page, target_fact=state,
            confidence=0.75, resolution_status="partial", reason="ViewState usage",
        ))
    return ArtifactParseResult(
        facts=tuple(facts), relationships=tuple(relationships), diagnostics=tuple(diagnostics),
        dependencies={relative: tuple(sorted({item.source.file_path for item in facts if item.source.file_path != relative}))},
    )


def parse_web_config(
    *, root: str, path: str, project_id: str, project_name: str, module_id: str,
) -> ArtifactParseResult:
    try:
        root_element, relative, truncated = parse_xml_file(root, path, 1024 * 1024)
    except (OSError, ValueError, Exception) as exc:
        return ArtifactParseResult(diagnostics=(Diagnostic(
            "aspnet_framework.config.parse_error", str(exc), "error", SourceSpan(path)
        ),))
    diagnostics: List[Diagnostic] = []
    if truncated:
        diagnostics.append(Diagnostic(
            "aspnet_framework.config.truncated", "web.config exceeded the 1 MiB budget", "warning", SourceSpan(relative)
        ))
    facts: List[SemanticFact] = []
    relationships: List[SemanticRelationship] = []
    section_stack: list[str] = []

    def visit(element, path_parts: tuple[str, ...]) -> None:
        tag = local_name(element.tag)
        current = path_parts + (tag,)
        key = ":".join(current)
        attributes = {str(name): redact_value(str(name), value) for name, value in sorted(element.attrib.items())}
        if element.attrib or (element.text or "").strip():
            config_fact = fact(
                kind="ConfigurationKey", name=key, framework=FRAMEWORK,
                project_id=project_id, project_name=project_name, module_id=module_id,
                source=SourceSpan(relative), coordinates=(key, tuple(sorted(element.attrib.items()))),
                confidence=1.0, extraction_method="safe_xml",
                properties={"config_key": key, "attributes": attributes, "value": redact_value(key, (element.text or "").strip())},
            )
            facts.append(config_fact)
            lower_tag = tag.lower()
            if lower_tag in {"add", "handler"} and any(token in ":".join(current).lower() for token in ("httphandlers", "handlers")):
                handler_name = str(element.attrib.get("type") or element.attrib.get("name") or element.attrib.get("path") or key)
                handler = fact(
                    kind="HttpHandler", name=handler_name, framework=FRAMEWORK,
                    project_id=project_id, project_name=project_name, module_id=module_id,
                    source=SourceSpan(relative), coordinates=("handler", handler_name),
                    confidence=0.95, resolution_status="unresolved", extraction_method="safe_xml",
                    properties={"path": element.attrib.get("path") or "", "verb": element.attrib.get("verb") or ""},
                )
                facts.append(handler)
                relationships.append(relationship(
                    relationship_type="LOADS_FROM", source_fact=handler, target_fact=config_fact,
                    confidence=0.95, reason="web.config handler declaration",
                ))
            if lower_tag in {"add", "module"} and "modules" in ":".join(current).lower():
                module_name = str(element.attrib.get("type") or element.attrib.get("name") or key)
                module = fact(
                    kind="HttpModule", name=module_name, framework=FRAMEWORK,
                    project_id=project_id, project_name=project_name, module_id=module_id,
                    source=SourceSpan(relative), coordinates=("module", module_name),
                    confidence=0.95, resolution_status="unresolved", extraction_method="safe_xml",
                    properties={"position": len([item for item in facts if item.kind == "HttpModule"])},
                )
                facts.append(module)
                relationships.append(relationship(
                    relationship_type="LOADS_FROM", source_fact=module, target_fact=config_fact,
                    confidence=0.95, reason="web.config module declaration",
                ))
        for child in list(element):
            visit(child, current)

    visit(root_element, ())
    return ArtifactParseResult(
        facts=tuple(facts), relationships=tuple(relationships), diagnostics=tuple(diagnostics),
        dependencies={relative: ()},
    )


def parse_resx(
    *, root: str, path: str, project_id: str, project_name: str, module_id: str,
) -> ArtifactParseResult:
    try:
        root_element, relative, truncated = parse_xml_file(root, path, 1024 * 1024)
    except Exception as exc:
        return ArtifactParseResult(diagnostics=(Diagnostic(
            "aspnet_framework.resx.parse_error", str(exc), "warning", SourceSpan(path)
        ),))
    facts: List[SemanticFact] = []
    for element in root_element:
        if local_name(element.tag) != "data":
            continue
        name = str(element.attrib.get("name") or "")
        value = next(((child.text or "") for child in element if local_name(child.tag) == "value"), "")
        facts.append(fact(
            kind="ConfigurationKey", name=f"resource:{name}", framework=FRAMEWORK,
            project_id=project_id, project_name=project_name, module_id=module_id,
            source=SourceSpan(relative), coordinates=("resource", name), extraction_method="safe_xml",
            properties={"config_key": name, "value": redact_value(name, value), "resource": True},
        ))
    diagnostics = (Diagnostic(
        "aspnet_framework.resx.truncated", "Resource file exceeded budget", "warning", SourceSpan(relative)
    ),) if truncated else ()
    return ArtifactParseResult(facts=tuple(facts), diagnostics=diagnostics, dependencies={relative: ()})

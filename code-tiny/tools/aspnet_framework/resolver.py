from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from tools.common.aspnet.builders import fact, relationship
from tools.common.aspnet.identity import relationship_id
from tools.common.aspnet.models import SemanticFact, SemanticRelationship, SourceSpan


FRAMEWORK = "aspnet_framework"
_APPLICATION_EVENTS = {
    "Application_Start", "Session_Start", "Application_Error", "Application_BeginRequest",
    "Application_EndRequest", "Application_AuthenticateRequest", "Application_AuthorizeRequest",
    "BeginRequest", "EndRequest", "AuthenticateRequest", "AuthorizeRequest",
}
_HTTP_ATTRIBUTES = {"HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "AcceptVerbs"}


def resolve_roslyn_evidence(
    *,
    payload: Dict[str, Any],
    project_id: str,
    project_name: str,
    module_id: str,
) -> tuple[Tuple[SemanticFact, ...], Tuple[SemanticRelationship, ...]]:
    facts: List[SemanticFact] = []
    relationships: List[SemanticRelationship] = []
    for result in payload.get("results") or ():
        if not result.get("ok") or not isinstance(result.get("evidence"), dict):
            continue
        file_path = str(result.get("file_path") or "")
        evidence = result["evidence"]
        type_facts: Dict[str, SemanticFact] = {}
        for type_item in evidence.get("types") or ():
            name = str(type_item.get("name") or "")
            qualified = str(type_item.get("qualified_name") or name)
            bases = tuple(str(item) for item in type_item.get("base_types") or ())
            kind = ""
            if name.endswith("Controller") or any("Controller" in item for item in bases):
                kind = "Controller"
            elif any("IHttpModule" in item for item in bases):
                kind = "HttpModule"
            elif any(token in item for item in bases for token in ("IHttpHandler", "IHttpAsyncHandler")):
                kind = "HttpHandler"
            elif any(token in name.lower() for token in ("service", "repository")):
                kind = "Repository" if "repository" in name.lower() else "Service"
            if not kind:
                continue
            source = SourceSpan(file_path, int(type_item.get("start_line") or 1))
            anchor = str(type_item.get("canonical_symbol_id") or "")
            item_fact = fact(
                kind=kind, name=name, framework=FRAMEWORK, project_id=project_id,
                project_name=project_name, module_id=module_id, source=source,
                coordinates=(qualified,), confidence=1.0,
                extraction_method="roslyn_semantic" if payload.get("semantic_enabled") else "roslyn_syntax",
                source_symbol_id=anchor,
                properties={"qualified_name": qualified, "base_types": bases},
            )
            type_facts[qualified] = item_fact
            facts.append(item_fact)
            if anchor:
                relationships.append(_semantic_of(item_fact, anchor, "Type"))

        for member in evidence.get("members") or ():
            if str(member.get("kind") or "") != "method":
                continue
            name = str(member.get("name") or "")
            qualified = str(member.get("qualified_name") or name)
            accessibility = str(member.get("accessibility") or "")
            attributes = tuple(str(item).split(".")[-1].removesuffix("Attribute") for item in member.get("attributes") or ())
            source = SourceSpan(file_path, int(member.get("start_line") or 1))
            owner = _owner_for_member(qualified, type_facts)
            member_kind = ""
            if name in _APPLICATION_EVENTS or re.match(r"(?:Application|Session)_[A-Za-z]+$", name):
                member_kind = "ApplicationEvent"
            elif name == "ProcessRequest" and accessibility == "public":
                member_kind = "Action"
            elif owner and owner.kind == "Controller" and accessibility == "public" and (
                set(attributes) & _HTTP_ATTRIBUTES or not name.startswith("_")
            ) and "NonAction" not in attributes:
                member_kind = "Action"
            if not member_kind:
                continue
            anchor = str(member.get("canonical_symbol_id") or "")
            item_fact = fact(
                kind=member_kind, name=name, framework=FRAMEWORK, project_id=project_id,
                project_name=project_name, module_id=module_id, source=source,
                coordinates=(qualified,), confidence=1.0,
                extraction_method="roslyn_semantic" if payload.get("semantic_enabled") else "roslyn_syntax",
                source_symbol_id=anchor,
                properties={"qualified_name": qualified, "attributes": attributes},
            )
            facts.append(item_fact)
            if anchor:
                relationships.append(_semantic_of(item_fact, anchor, "Function"))
            if owner:
                relationships.append(relationship(
                    relationship_type="HANDLED_BY" if member_kind == "Action" else "INITIALIZES",
                    source_fact=item_fact, target_fact=owner, reason="compiler member ownership",
                ))

        module_position = 0
        for invocation in evidence.get("invocations") or ():
            expression = str(invocation.get("expression") or "")
            name = expression.split(".")[-1]
            line = int(invocation.get("start_line") or 1)
            constants = [str(item) for item in invocation.get("constant_arguments") or () if str(item)]
            arguments = [str(item) for item in invocation.get("arguments") or ()]
            source = SourceSpan(file_path, line)
            if name in {"MapRoute", "MapHttpRoute", "MapPageRoute"}:
                route_value = next((item for item in constants if "/" in item or "{" in item), "")
                route_fact = fact(
                    kind="Route", name=route_value or f"{name}@{line}", framework=FRAMEWORK,
                    project_id=project_id, project_name=project_name, module_id=module_id,
                    source=source, coordinates=(name, tuple(arguments)), confidence=0.95 if route_value else 0.65,
                    resolution_status="resolved" if route_value else "dynamic", extraction_method="roslyn_invocation",
                    properties={"route": route_value, "mapping_kind": name, "arguments": arguments},
                )
                endpoint = fact(
                    kind="HttpEndpoint", name=route_value or f"dynamic:{line}", framework=FRAMEWORK,
                    project_id=project_id, project_name=project_name, module_id=module_id,
                    source=source, coordinates=("endpoint", name, tuple(arguments)),
                    confidence=route_fact.confidence, resolution_status=route_fact.resolution_status,
                    extraction_method="roslyn_invocation",
                    properties={"route": route_value, "arguments": arguments},
                )
                facts.extend((route_fact, endpoint))
                relationships.append(relationship(
                    relationship_type="MAPPED_TO", source_fact=endpoint, target_fact=route_fact,
                    confidence=route_fact.confidence, resolution_status=route_fact.resolution_status,
                ))
            elif name in {"AddModule", "RegisterModule", "UseStageMarker"}:
                module_fact = fact(
                    kind="HttpModule", name=constants[0] if constants else (arguments[0] if arguments else name),
                    framework=FRAMEWORK, project_id=project_id, project_name=project_name,
                    module_id=module_id, source=source, coordinates=(name, tuple(arguments)),
                    confidence=0.7, resolution_status="partial", extraction_method="roslyn_invocation",
                    properties={"position": module_position, "registration": name},
                )
                module_position += 1
                facts.append(module_fact)
    return tuple(facts), tuple(relationships)


def connect_request_pipeline(
    facts: Iterable[SemanticFact], relationships: Iterable[SemanticRelationship]
) -> tuple[SemanticRelationship, ...]:
    values = list(relationships)
    endpoints = sorted((item for item in facts if item.kind == "HttpEndpoint"), key=lambda item: item.stable_id)
    modules = sorted(
        (item for item in facts if item.kind == "HttpModule"),
        key=lambda item: (int(item.properties.get("position", 10_000)), item.source.file_path, item.source.start_line),
    )
    handlers = sorted((item for item in facts if item.kind in {"HttpHandler", "Controller", "WebFormPage"}), key=lambda item: item.stable_id)
    for endpoint in endpoints:
        for position, module in enumerate(modules):
            values.append(relationship(
                relationship_type="PASSES_THROUGH", source_fact=endpoint, target_fact=module,
                confidence=0.7, resolution_status="partial", reason="declared Framework request pipeline",
                properties={"position": position},
            ))
        argument_text = " ".join(str(item) for item in endpoint.properties.get("arguments", ())).lower()
        matched_handlers = [
            item for item in handlers
            if item.source.file_path and item.source.file_path.lower() in argument_text
        ]
        if matched_handlers:
            for handler in matched_handlers:
                values.append(relationship(
                    relationship_type="HANDLED_BY", source_fact=endpoint, target_fact=handler,
                    confidence=0.95, resolution_status="resolved", reason="constant route target",
                ))
        elif len(handlers) == 1:
            values.append(relationship(
                relationship_type="HANDLED_BY", source_fact=endpoint, target_fact=handlers[0],
                confidence=0.75, resolution_status="partial", reason="single handler candidate",
            ))
    return tuple(values)


def _owner_for_member(qualified: str, owners: Dict[str, SemanticFact]) -> SemanticFact | None:
    matches = [item for key, item in owners.items() if qualified.startswith(key + ".")]
    return max(matches, key=lambda item: len(str(item.properties.get("qualified_name") or "")), default=None)


def _semantic_of(source_fact: SemanticFact, anchor: str, label: str) -> SemanticRelationship:
    return SemanticRelationship(
        stable_id=relationship_id(
            FRAMEWORK, source_fact.project_id, source_fact.module_id,
            "SEMANTIC_OF", source_fact.stable_id, anchor,
        ),
        relationship_type="SEMANTIC_OF",
        from_id=source_fact.stable_id,
        to_id=anchor,
        from_label=source_fact.kind,
        to_label=label,
        framework=FRAMEWORK,
        project_id=source_fact.project_id,
        module_id=source_fact.module_id,
        source=source_fact.source,
        confidence=1.0,
        reason="canonical C# source coordinate",
        to_generated=False,
    )

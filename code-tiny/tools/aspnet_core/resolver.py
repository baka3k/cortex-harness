from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from tools.common.aspnet.builders import fact, relationship
from tools.common.aspnet.identity import relationship_id
from tools.common.aspnet.models import SemanticFact, SemanticRelationship, SourceSpan


FRAMEWORK = "aspnet_core"
_HTTP_METHODS = {
    "MapGet": "GET", "MapPost": "POST", "MapPut": "PUT", "MapDelete": "DELETE",
    "MapPatch": "PATCH", "MapMethods": "MULTI", "Map": "ANY",
}
_HTTP_ATTRIBUTES = {"HttpGet", "HttpPost", "HttpPut", "HttpDelete", "HttpPatch", "AcceptVerbs"}
_TERMINAL_MIDDLEWARE = {"Run", "UseEndpoints"}


def resolve_roslyn_evidence(
    *, payload: Dict[str, Any], project_id: str, project_name: str, module_id: str,
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
            elif name.endswith("Model") and any("PageModel" in item for item in bases):
                kind = "RazorPage"
            elif "Repository" in name:
                kind = "Repository"
            elif name.endswith("Service"):
                kind = "Service"
            elif name.endswith(("Model", "Dto", "Request", "Response")):
                kind = "Model"
            if not kind:
                continue
            source = SourceSpan(file_path, int(type_item.get("start_line") or 1))
            anchor = str(type_item.get("canonical_symbol_id") or "")
            item_fact = fact(
                kind=kind, name=name, framework=FRAMEWORK, project_id=project_id,
                project_name=project_name, module_id=module_id, source=source,
                coordinates=(qualified,), extraction_method="roslyn_semantic" if payload.get("semantic_enabled") else "roslyn_syntax",
                source_symbol_id=anchor, properties={"qualified_name": qualified, "base_types": bases},
            )
            type_facts[qualified] = item_fact
            facts.append(item_fact)
            if anchor:
                relationships.append(_semantic_of(item_fact, anchor, "Type"))

        attributes_by_line: Dict[int, list[Dict[str, Any]]] = {}
        for attribute in evidence.get("attributes") or ():
            attributes_by_line.setdefault(int(attribute.get("start_line") or 1), []).append(attribute)
        for member in evidence.get("members") or ():
            if str(member.get("kind") or "") != "method":
                continue
            name = str(member.get("name") or "")
            qualified = str(member.get("qualified_name") or name)
            line = int(member.get("start_line") or 1)
            accessibility = str(member.get("accessibility") or "")
            attrs = tuple(str(item).split(".")[-1].removesuffix("Attribute") for item in member.get("attributes") or ())
            source = SourceSpan(file_path, line)
            owner = _owner_for_member(qualified, type_facts)
            kind = ""
            if owner and owner.kind == "Controller" and accessibility == "public" and (
                set(attrs) & _HTTP_ATTRIBUTES or not name.startswith("_")
            ) and "NonAction" not in attrs:
                kind = "Action"
            elif owner and owner.kind == "RazorPage" and accessibility == "public" and re.match(
                r"On(?:Get|Post|Put|Delete|Patch)", name
            ):
                kind = "PageHandler"
            if not kind:
                continue
            anchor = str(member.get("canonical_symbol_id") or "")
            member_fact = fact(
                kind=kind, name=name, framework=FRAMEWORK, project_id=project_id,
                project_name=project_name, module_id=module_id, source=source,
                coordinates=(qualified,),
                extraction_method="roslyn_semantic" if payload.get("semantic_enabled") else "roslyn_syntax",
                source_symbol_id=anchor,
                properties={"qualified_name": qualified, "attributes": attrs},
            )
            facts.append(member_fact)
            if anchor:
                relationships.append(_semantic_of(member_fact, anchor, "Function"))
            if owner:
                relationships.append(relationship(
                    relationship_type="DEPENDS_ON", source_fact=member_fact, target_fact=owner,
                    reason="compiler member ownership",
                ))
            route_attribute = next(
                (
                    item for item in attributes_by_line.get(line, [])
                    if str(item.get("name") or "").split(".")[-1].removesuffix("Attribute") in ({"Route"} | _HTTP_ATTRIBUTES)
                ),
                None,
            )
            if route_attribute:
                args = [str(item).strip("\"'") for item in route_attribute.get("arguments") or ()]
                route = args[0] if args else ""
                endpoint, route_fact = _endpoint_and_route(
                    project_id, project_name, module_id, source,
                    route or f"{owner.name if owner else 'action'}/{name}",
                    _method_from_attributes(attrs), (qualified,),
                    "attribute_route", "resolved" if route else "partial",
                )
                facts.extend((endpoint, route_fact))
                relationships.extend((
                    relationship(relationship_type="MAPPED_TO", source_fact=endpoint, target_fact=route_fact),
                    relationship(relationship_type="HANDLED_BY", source_fact=endpoint, target_fact=member_fact),
                ))

        middleware: List[SemanticFact] = []
        endpoints: List[SemanticFact] = []
        for invocation in evidence.get("invocations") or ():
            expression = str(invocation.get("expression") or "")
            name = expression.split(".")[-1]
            line = int(invocation.get("start_line") or 1)
            constants = [str(item) for item in invocation.get("constant_arguments") or () if str(item)]
            arguments = [str(item) for item in invocation.get("arguments") or ()]
            source = SourceSpan(file_path, line)
            if name.startswith("Use") or name in {"Run", "MapWhen"}:
                middleware_fact = fact(
                    kind="Middleware", name=name, framework=FRAMEWORK, project_id=project_id,
                    project_name=project_name, module_id=module_id, source=source,
                    coordinates=(name, tuple(arguments)), confidence=0.95,
                    resolution_status="partial" if name in {"MapWhen"} else "resolved",
                    extraction_method="roslyn_invocation",
                    properties={
                        "position": len(middleware), "branch": name in {"MapWhen"},
                        "terminal": name in _TERMINAL_MIDDLEWARE, "arguments": arguments,
                    },
                )
                middleware.append(middleware_fact)
                facts.append(middleware_fact)
            if name in _HTTP_METHODS:
                route = constants[0] if constants else ""
                endpoint, route_fact = _endpoint_and_route(
                    project_id, project_name, module_id, source,
                    route or f"dynamic:{line}", _HTTP_METHODS[name], tuple(arguments),
                    "minimal_api", "resolved" if route else "dynamic",
                )
                facts.extend((endpoint, route_fact))
                endpoints.append(endpoint)
                relationships.append(relationship(
                    relationship_type="MAPPED_TO", source_fact=endpoint, target_fact=route_fact,
                    confidence=1.0 if route else 0.6,
                    resolution_status="resolved" if route else "dynamic",
                ))
                handler_name = arguments[1] if len(arguments) > 1 else (arguments[0] if arguments else f"handler@{line}")
                handler = fact(
                    kind="Action", name=handler_name, framework=FRAMEWORK, project_id=project_id,
                    project_name=project_name, module_id=module_id, source=source,
                    coordinates=("minimal-handler", tuple(arguments)), confidence=0.75,
                    resolution_status="partial", extraction_method="roslyn_invocation",
                    properties={"handler_expression": handler_name, "minimal_api": True},
                )
                facts.append(handler)
                relationships.append(relationship(
                    relationship_type="HANDLED_BY", source_fact=endpoint, target_fact=handler,
                    confidence=0.75, resolution_status="partial", reason="Minimal API handler expression",
                ))
            if name.startswith("Add") and name in {
                "AddSingleton", "AddScoped", "AddTransient", "AddDbContext", "AddHostedService",
            }:
                service_name = next((item for item in arguments if item), name)
                service_kind = "Repository" if "DbContext" in name or "Repository" in service_name else "Service"
                service = fact(
                    kind=service_kind, name=service_name, framework=FRAMEWORK, project_id=project_id,
                    project_name=project_name, module_id=module_id, source=source,
                    coordinates=(name, tuple(arguments)), confidence=0.85, resolution_status="partial",
                    extraction_method="roslyn_invocation",
                    properties={"lifetime": name.removeprefix("Add").lower(), "registration": name},
                )
                facts.append(service)
            if name in {"GetSection", "GetValue", "GetConnectionString"} and constants:
                config = fact(
                    kind="ConfigurationKey", name=constants[0], framework=FRAMEWORK,
                    project_id=project_id, project_name=project_name, module_id=module_id,
                    source=source, coordinates=(name, constants[0]), confidence=1.0,
                    extraction_method="roslyn_invocation", properties={"config_key": constants[0], "consumer": expression},
                )
                facts.append(config)
        for endpoint in endpoints:
            for position, middleware_fact in enumerate(middleware):
                relationships.append(relationship(
                    relationship_type="PASSES_THROUGH", source_fact=endpoint, target_fact=middleware_fact,
                    reason="ordered host pipeline", properties={"position": position},
                ))
    return tuple(facts), tuple(relationships)


def _endpoint_and_route(
    project_id: str, project_name: str, module_id: str, source: SourceSpan,
    route: str, method: str, coordinates: tuple[object, ...], mapping_kind: str,
    resolution_status: str,
) -> tuple[SemanticFact, SemanticFact]:
    confidence = 1.0 if resolution_status == "resolved" else 0.65
    endpoint = fact(
        kind="HttpEndpoint", name=f"{method} {route}", framework=FRAMEWORK,
        project_id=project_id, project_name=project_name, module_id=module_id,
        source=source, coordinates=(method, route, *coordinates), confidence=confidence,
        resolution_status=resolution_status, extraction_method="roslyn_invocation",
        properties={"route": route, "http_method": method, "mapping_kind": mapping_kind},
    )
    route_fact = fact(
        kind="Route", name=route, framework=FRAMEWORK, project_id=project_id,
        project_name=project_name, module_id=module_id, source=source,
        coordinates=(mapping_kind, route), confidence=confidence,
        resolution_status=resolution_status, extraction_method="roslyn_invocation",
        properties={"route": route, "http_method": method},
    )
    return endpoint, route_fact


def _owner_for_member(qualified: str, owners: Dict[str, SemanticFact]) -> SemanticFact | None:
    matches = [item for key, item in owners.items() if qualified.startswith(key + ".")]
    return max(matches, key=lambda item: len(str(item.properties.get("qualified_name") or "")), default=None)


def _method_from_attributes(attributes: Iterable[str]) -> str:
    for attribute in attributes:
        if attribute.startswith("Http"):
            return attribute.removeprefix("Http").upper()
    return "ANY"


def _semantic_of(source_fact: SemanticFact, anchor: str, label: str) -> SemanticRelationship:
    return SemanticRelationship(
        stable_id=relationship_id(
            FRAMEWORK, source_fact.project_id, source_fact.module_id,
            "SEMANTIC_OF", source_fact.stable_id, anchor,
        ),
        relationship_type="SEMANTIC_OF", from_id=source_fact.stable_id, to_id=anchor,
        from_label=source_fact.kind, to_label=label, framework=FRAMEWORK,
        project_id=source_fact.project_id, module_id=source_fact.module_id,
        source=source_fact.source, confidence=1.0,
        reason="canonical C# source coordinate", to_generated=False,
    )

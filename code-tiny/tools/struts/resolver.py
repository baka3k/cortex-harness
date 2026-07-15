from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from tools.struts.models import (
    ActionConfig,
    Diagnostic,
    InterceptorConfig,
    InterceptorRef,
    InterceptorStackConfig,
    PackageConfig,
    ResultConfig,
    SourceSpan,
    StrutsFact,
    StrutsRelationship,
    ValidationRule,
    WebFilterConfig,
    stable_id,
)


_VIEW_RESULT_TYPES = {"dispatcher", "freemarker", "velocity", "tiles"}
_REDIRECT_RESULT_TYPES = {"redirect", "redirectaction"}


@dataclass(frozen=True)
class StrutsResolution:
    facts: Tuple[StrutsFact, ...]
    relationships: Tuple[StrutsRelationship, ...]
    diagnostics: Tuple[Diagnostic, ...]


class _Collector:
    def __init__(self, project_id: str, project_name: str, module_id: str) -> None:
        self.project_id = project_id
        self.project_name = project_name
        self.module_id = module_id
        self.facts: Dict[str, StrutsFact] = {}
        self.relationships: Dict[str, StrutsRelationship] = {}

    def fact(
        self,
        kind: str,
        name: str,
        source: SourceSpan,
        *identity: object,
        properties: Mapping[str, object] | None = None,
        extraction_method: str = "struts_xml",
        resolution_status: str = "resolved",
        confidence: float = 1.0,
    ) -> StrutsFact:
        fact_id = stable_id(kind, self.project_id, self.module_id, *identity)
        fact = StrutsFact(
            kind=kind,
            stable_id=fact_id,
            name=name,
            source=source,
            project_id=self.project_id,
            project_name=self.project_name,
            module_id=self.module_id,
            confidence=confidence,
            extraction_method=extraction_method,
            resolution_status=resolution_status,
            properties=properties or {},
        )
        self.facts[fact_id] = fact
        return fact

    def rel(
        self,
        source_fact: StrutsFact,
        target_fact: StrutsFact,
        relationship_type: str,
        source: SourceSpan,
        *,
        properties: Mapping[str, object] | None = None,
        reason: str = "",
    ) -> None:
        payload = properties or {}
        relationship_id = stable_id(
            "relationship",
            self.project_id,
            self.module_id,
            source_fact.stable_id,
            relationship_type,
            target_fact.stable_id,
            repr(sorted(payload.items())),
        )
        self.relationships[relationship_id] = StrutsRelationship(
            stable_id=relationship_id,
            from_id=source_fact.stable_id,
            to_id=target_fact.stable_id,
            from_label=source_fact.kind,
            to_label=target_fact.kind,
            type=relationship_type,
            project_id=self.project_id,
            module_id=self.module_id,
            source=source,
            reason=reason,
            properties=payload,
        )


def _lineage(
    package: PackageConfig,
    packages: Mapping[str, PackageConfig],
    diagnostics: List[Diagnostic],
    trail: Tuple[str, ...] = (),
) -> Tuple[PackageConfig, ...]:
    if package.name in trail:
        diagnostics.append(
            Diagnostic(
                "struts.package.inheritance_cycle",
                f"Package inheritance cycle: {' -> '.join(trail + (package.name,))}",
                "error",
                package.source.file_path,
            )
        )
        return ()
    result: List[PackageConfig] = []
    for parent_name in package.extends:
        parent = packages.get(parent_name)
        if parent is None:
            diagnostics.append(
                Diagnostic(
                    "struts.package.parent_missing",
                    f"Package {package.name!r} extends missing package {parent_name!r}",
                    "warning",
                    package.source.file_path,
                )
            )
            continue
        result.extend(_lineage(parent, packages, diagnostics, trail + (package.name,)))
    result.append(package)
    deduped: Dict[str, PackageConfig] = {}
    for item in result:
        deduped[item.name] = item
    return tuple(deduped.values())


def _named(lineage: Sequence[PackageConfig], attribute: str) -> Dict[str, object]:
    values: Dict[str, object] = {}
    for package in lineage:
        for item in getattr(package, attribute):
            values[item.name] = item
    return values


def _default(lineage: Sequence[PackageConfig], attribute: str) -> str:
    for package in reversed(lineage):
        value = str(getattr(package, attribute))
        if value:
            return value
    return ""


def _expand_refs(
    refs: Sequence[InterceptorRef],
    interceptor_defs: Mapping[str, InterceptorConfig],
    stack_defs: Mapping[str, InterceptorStackConfig],
    diagnostics: List[Diagnostic],
    source: SourceSpan,
    trail: Tuple[str, ...] = (),
    inherited_params: Mapping[str, str] | None = None,
) -> List[tuple[InterceptorConfig, Dict[str, str], str]]:
    expanded: List[tuple[InterceptorConfig, Dict[str, str], str]] = []
    inherited = dict(inherited_params or {})
    for ref in refs:
        if ref.name in stack_defs:
            if ref.name in trail:
                diagnostics.append(
                    Diagnostic(
                        "struts.interceptor_stack.cycle",
                        f"Interceptor stack cycle: {' -> '.join(trail + (ref.name,))}",
                        "error",
                        source.file_path,
                    )
                )
                continue
            stack_params = dict(inherited)
            stack_params.update(ref.params)
            expanded.extend(
                _expand_refs(
                    stack_defs[ref.name].refs,
                    interceptor_defs,
                    stack_defs,
                    diagnostics,
                    source,
                    trail + (ref.name,),
                    stack_params,
                )
            )
            continue
        interceptor = interceptor_defs.get(ref.name)
        if interceptor is None:
            diagnostics.append(
                Diagnostic(
                    "struts.interceptor.unresolved",
                    f"Unable to resolve interceptor or stack {ref.name!r}",
                    "warning",
                    source.file_path,
                )
            )
            continue
        effective_params = dict(interceptor.params)
        for key, value in inherited.items():
            prefix = f"{ref.name}."
            effective_params[key[len(prefix) :] if key.startswith(prefix) else key] = value
        effective_params.update(ref.params)
        expanded.append((interceptor, effective_params, ref.name))
    return expanded


def _route(namespace: str, action_name: str, extension: str) -> str:
    namespace = "/" + namespace.strip("/") if namespace.strip("/") else ""
    action_name = action_name.lstrip("/")
    suffix = f".{extension}" if extension and not action_name.endswith(f".{extension}") else ""
    return f"{namespace}/{action_name}{suffix}" or "/"


def _result_type(result: ResultConfig, default_type: str) -> str:
    return (result.type_name or default_type or "dispatcher").strip()


def _validation_matches(rule: ValidationRule, action: ActionConfig) -> bool:
    simple_class = action.class_name.rsplit(".", 1)[-1]
    target_match = rule.target in {simple_class, action.name}
    method_match = not rule.method or rule.method == action.method
    return target_match and method_match


def resolve_struts_project(
    *,
    project_id: str,
    project_name: str,
    module_id: str,
    packages: Sequence[PackageConfig],
    constants: Mapping[str, str],
    web_filters: Sequence[WebFilterConfig],
    validation_rules: Sequence[ValidationRule],
) -> StrutsResolution:
    collector = _Collector(project_id, project_name, module_id)
    diagnostics: List[Diagnostic] = []
    package_by_name: Dict[str, PackageConfig] = {}
    for package in packages:
        if not package.name:
            diagnostics.append(
                Diagnostic("struts.package.name_missing", "Ignoring unnamed Struts package", "warning", package.source.file_path)
            )
            continue
        if package.name in package_by_name:
            diagnostics.append(
                Diagnostic(
                    "struts.package.duplicate",
                    f"Duplicate package {package.name!r}; the later declaration wins",
                    "warning",
                    package.source.file_path,
                )
            )
        package_by_name[package.name] = package

    filter_facts: List[StrutsFact] = []
    filter_patterns: List[str] = []
    for web_filter in web_filters:
        class_lower = web_filter.class_name.lower()
        if "struts" not in class_lower or not ("filter" in class_lower or "dispatcher" in class_lower):
            continue
        filter_fact = collector.fact(
            "Plugin",
            web_filter.name,
            web_filter.source,
            "web-filter",
            web_filter.name,
            properties={
                "plugin_type": "web_filter",
                "class_name": web_filter.class_name,
                "url_patterns": web_filter.url_patterns,
                "init_params": web_filter.init_params,
            },
            extraction_method="web_xml",
        )
        filter_facts.append(filter_fact)
        filter_patterns.extend(web_filter.url_patterns)

    extension_value = constants.get("struts.action.extension", "action")
    extension = next((item.strip() for item in extension_value.split(",") if item.strip()), "")
    package_facts: Dict[str, StrutsFact] = {}
    for package in package_by_name.values():
        package_facts[package.name] = collector.fact(
            "Package",
            package.name,
            package.source,
            package.name,
            properties={
                "namespace": package.namespace,
                "extends": package.extends,
                "default_interceptor_stack": package.default_interceptor_ref,
                "default_result_type": package.default_result_type,
            },
        )
    for package in package_by_name.values():
        for parent_name in package.extends:
            if parent_name in package_facts:
                collector.rel(
                    package_facts[package.name],
                    package_facts[parent_name],
                    "EXTENDS",
                    package.source,
                    reason="Struts package inheritance",
                )

    matched_validation_ids: set[int] = set()
    for package in package_by_name.values():
        lineage = _lineage(package, package_by_name, diagnostics)
        interceptor_defs = _named(lineage, "interceptors")
        stack_defs = _named(lineage, "interceptor_stacks")
        result_type_defs = _named(lineage, "result_types")
        global_results = _named(lineage, "global_results")
        default_stack = _default(lineage, "default_interceptor_ref")
        default_result_type = _default(lineage, "default_result_type")
        if not default_result_type:
            default_result_type = next(
                (item.name for item in result_type_defs.values() if getattr(item, "default", False)),
                "dispatcher",
            )

        for action in package.actions:
            if not action.name:
                diagnostics.append(
                    Diagnostic("struts.action.name_missing", "Ignoring unnamed action", "warning", action.source.file_path)
                )
                continue
            route = _route(package.namespace, action.name, extension)
            action_fact = collector.fact(
                "Action",
                action.name,
                action.source,
                package.name,
                action.name,
                action.class_name,
                action.method,
                properties={
                    "class_name": action.class_name,
                    "method": action.method,
                    "namespace": package.namespace,
                    "route": route,
                    "wildcard": "*" in action.name,
                    "params": action.params,
                },
            )
            collector.rel(package_facts[package.name], action_fact, "CONTAINS", action.source)
            endpoint_fact = collector.fact(
                "HttpEndpoint",
                route,
                action.source,
                route,
                properties={
                    "path": route,
                    "http_method": "ALL",
                    "action_extension": extension,
                    "filter_patterns": tuple(dict.fromkeys(filter_patterns)),
                    "wildcard": "*" in route,
                },
            )
            collector.rel(endpoint_fact, action_fact, "MAPPED_TO", action.source, reason="Struts action mapping")
            for filter_fact in filter_facts:
                collector.rel(endpoint_fact, filter_fact, "PASSES_THROUGH", action.source, reason="web.xml Struts filter mapping")

            declared_refs = action.interceptor_refs or ((InterceptorRef(default_stack),) if default_stack else ())
            expanded = _expand_refs(
                declared_refs,
                interceptor_defs,  # type: ignore[arg-type]
                stack_defs,  # type: ignore[arg-type]
                diagnostics,
                action.source,
            )
            if declared_refs:
                effective_name = f"{package.name}/{action.name}:effective"
                stack_fact = collector.fact(
                    "InterceptorStack",
                    effective_name,
                    action.source,
                    package.name,
                    action.name,
                    "effective-stack",
                    properties={"declared_refs": tuple(ref.name for ref in declared_refs)},
                    extraction_method="struts_resolution",
                )
                collector.rel(action_fact, stack_fact, "USES_INTERCEPTOR_STACK", action.source)
                for order, (interceptor, effective_params, ref_name) in enumerate(expanded):
                    interceptor_fact = collector.fact(
                        "Interceptor",
                        interceptor.name,
                        package.source,
                        package.name,
                        interceptor.name,
                        properties={"class_name": interceptor.class_name, "params": interceptor.params},
                    )
                    collector.rel(
                        stack_fact,
                        interceptor_fact,
                        "CONTAINS",
                        action.source,
                        properties={
                            "order": order,
                            "reference": ref_name,
                            "params": effective_params,
                            "include_methods": effective_params.get("includeMethods", ""),
                            "exclude_methods": effective_params.get("excludeMethods", ""),
                        },
                        reason="Resolved interceptor execution order",
                    )

            effective_results = dict(global_results)
            effective_results.update({result.name: result for result in action.results})
            for result_name, candidate in sorted(effective_results.items()):
                result = candidate  # type: ignore[assignment]
                type_name = _result_type(result, default_result_type)
                result_fact = collector.fact(
                    "Result",
                    result_name,
                    action.source,
                    package.name,
                    action.name,
                    result_name,
                    properties={
                        "result_type": type_name,
                        "location": result.location,
                        "params": result.params,
                        "global": result_name in global_results and result_name not in {item.name for item in action.results},
                    },
                )
                collector.rel(
                    action_fact,
                    result_fact,
                    "RETURNS_RESULT",
                    action.source,
                    properties={"result_name": result_name},
                )
                result_type = result_type_defs.get(type_name)
                if result_type is not None:
                    type_fact = collector.fact(
                        "ResultType",
                        type_name,
                        package.source,
                        package.name,
                        type_name,
                        properties={"class_name": result_type.class_name, "params": result_type.params},  # type: ignore[attr-defined]
                    )
                    collector.rel(result_fact, type_fact, "INSTANCE_OF", action.source)
                normalized_type = type_name.lower()
                if result.location and normalized_type in _VIEW_RESULT_TYPES:
                    view_fact = collector.fact(
                        "View",
                        result.location,
                        action.source,
                        result.location,
                        properties={"template_type": normalized_type},
                    )
                    collector.rel(result_fact, view_fact, "RESOLVES_TO", action.source)
                elif result.location and normalized_type in _REDIRECT_RESULT_TYPES | {"chain"}:
                    target_route = _route(package.namespace, result.location, extension)
                    target_fact = collector.fact(
                        "HttpEndpoint",
                        target_route,
                        action.source,
                        target_route,
                        properties={"path": target_route, "http_method": "ALL", "synthetic_target": True},
                        resolution_status="unresolved",
                        confidence=0.7,
                    )
                    relation_type = "CHAINS_TO" if normalized_type == "chain" else "REDIRECTS_TO"
                    collector.rel(result_fact, target_fact, relation_type, action.source)

            effective_exceptions = []
            for ancestor in lineage:
                effective_exceptions.extend(ancestor.exception_mappings)
            effective_exceptions.extend(action.exception_mappings)
            for mapping in effective_exceptions:
                exception_fact = collector.fact(
                    "ExceptionMapping",
                    mapping.exception,
                    action.source,
                    package.name,
                    action.name,
                    mapping.exception,
                    mapping.result,
                    properties={"exception": mapping.exception, "result": mapping.result},
                )
                collector.rel(action_fact, exception_fact, "HANDLES_EXCEPTION", action.source)

            for index, rule in enumerate(validation_rules):
                if not _validation_matches(rule, action):
                    continue
                matched_validation_ids.add(index)
                rule_name = f"{rule.field_name or '<action>'}:{rule.validator_type}"
                validation_fact = collector.fact(
                    "ValidationRule",
                    rule_name,
                    rule.source,
                    package.name,
                    action.name,
                    rule.method,
                    rule.field_name,
                    rule.validator_type,
                    rule.message_key,
                    properties={
                        "target": rule.target,
                        "method": rule.method,
                        "field_name": rule.field_name,
                        "validator_type": rule.validator_type,
                        "message": rule.message,
                        "message_key": rule.message_key,
                        "params": rule.params,
                    },
                    extraction_method="validation_xml",
                )
                collector.rel(action_fact, validation_fact, "VALIDATES_WITH", rule.source)

    for index, rule in enumerate(validation_rules):
        if index not in matched_validation_ids:
            diagnostics.append(
                Diagnostic(
                    "struts.validation.action_unresolved",
                    f"Validation target {rule.target!r} did not match a configured action",
                    "info",
                    rule.source.file_path,
                )
            )

    return StrutsResolution(
        facts=tuple(sorted(collector.facts.values(), key=lambda item: item.stable_id)),
        relationships=tuple(sorted(collector.relationships.values(), key=lambda item: item.stable_id)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.file_path, item.code, item.message))),
    )


__all__ = ["StrutsResolution", "resolve_struts_project"]

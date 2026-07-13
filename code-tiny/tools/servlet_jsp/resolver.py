from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.servlet_jsp.java_semantics import JavaSemanticAnalysisResult
from tools.servlet_jsp.jsp_parser import JspParseResult
from tools.servlet_jsp.models import (
    Diagnostic,
    ResourceBudgets,
    ServletJspDependencyIndex,
    ServletJspFact,
    ServletJspModule,
    ServletJspRelationship,
    SourceSpan,
    redact_value,
    stable_semantic_id,
)
from tools.servlet_jsp.properties_parser import PropertiesParseResult
from tools.servlet_jsp.web_xml_parser import WebXmlParseResult, WebXmlRecord


_COMPONENT_KINDS = {"Servlet", "Filter", "Listener"}
_CALLBACK_KINDS = {"ServletHandler", "ServletLifecycle", "FilterCallback", "FilterLifecycle", "ListenerCallback"}


class _ResolutionBudgetReached(RuntimeError):
    pass


@dataclass(frozen=True)
class ServletJspResolution:
    facts: Tuple[ServletJspFact, ...]
    relationships: Tuple[ServletJspRelationship, ...]
    dependency_index: ServletJspDependencyIndex
    diagnostics: Tuple[Diagnostic, ...]
    truncation_count: int = 0
    ambiguity_count: int = 0
    missing_anchor_count: int = 0


class _Collector:
    def __init__(self, project_id: str, project_name: str, module_id: str, budgets: ResourceBudgets) -> None:
        self.project_id = project_id
        self.project_name = project_name
        self.module_id = module_id
        self.max_facts = max(0, budgets.max_facts_per_project)
        self.max_relationships = max(0, budgets.max_relationships_per_project)
        self.fact_budget_hit = False
        self.relationship_budget_hit = False
        self.facts: Dict[str, ServletJspFact] = {}
        self.relationships: Dict[str, ServletJspRelationship] = {}

    def fact(self, fact: ServletJspFact) -> ServletJspFact:
        existing = self.facts.get(fact.stable_id)
        if existing is None and len(self.facts) >= self.max_facts:
            self.fact_budget_hit = True
            raise _ResolutionBudgetReached
        if existing is None or _fact_rank(fact) > _fact_rank(existing):
            self.facts[fact.stable_id] = fact
        return self.facts[fact.stable_id]

    def make_fact(
        self,
        kind: str,
        identity: Sequence[object],
        name: str,
        source: SourceSpan,
        *,
        extraction_method: str = "servlet_jsp_resolver",
        resolution_status: str = "resolved",
        raw_value: str = "",
        resolved_value: str = "",
        source_symbol_id: str = "",
        confidence: float = 1.0,
        properties: Optional[Dict[str, Any]] = None,
    ) -> ServletJspFact:
        return self.fact(
            ServletJspFact(
                kind=kind,
                stable_id=stable_semantic_id(kind, self.project_id, self.module_id, *identity),
                name=name,
                source=source,
                project_id=self.project_id,
                project_name=self.project_name,
                module_id=self.module_id,
                extraction_method=extraction_method,
                resolution_status=resolution_status,
                raw_value=raw_value,
                resolved_value=resolved_value,
                source_symbol_id=source_symbol_id,
                confidence=confidence,
                properties=dict(properties or {}),
            )
        )

    def rel(
        self,
        source: ServletJspFact | Tuple[str, str],
        target: ServletJspFact | Tuple[str, str],
        rel_type: str,
        span: SourceSpan,
        *,
        occurrence: Sequence[object] = (),
        reason: str = "",
        resolution_status: str = "resolved",
        confidence: float = 1.0,
        properties: Optional[Dict[str, Any]] = None,
        from_generated: bool = True,
        to_generated: bool = True,
    ) -> ServletJspRelationship:
        from_id, from_label = (source.stable_id, source.kind) if isinstance(source, ServletJspFact) else source
        to_id, to_label = (target.stable_id, target.kind) if isinstance(target, ServletJspFact) else target
        stable_id = stable_semantic_id(
            "relationship",
            self.project_id,
            self.module_id,
            rel_type,
            from_label,
            from_id,
            to_label,
            to_id,
            *occurrence,
        )
        relationship = ServletJspRelationship(
            stable_id=stable_id,
            from_id=from_id,
            to_id=to_id,
            from_label=from_label,
            to_label=to_label,
            type=rel_type,
            project_id=self.project_id,
            module_id=self.module_id,
            source=span,
            confidence=confidence,
            resolution_status=resolution_status,
            reason=reason,
            properties=dict(properties or {}),
            from_generated=from_generated,
            to_generated=to_generated,
        )
        if stable_id not in self.relationships and len(self.relationships) >= self.max_relationships:
            self.relationship_budget_hit = True
            raise _ResolutionBudgetReached
        self.relationships[stable_id] = relationship
        return relationship


class _BoundedDiagnostics(list[Diagnostic]):
    def __init__(self, maximum: int) -> None:
        super().__init__()
        self.maximum = max(0, maximum)
        self.truncated = False

    def append(self, item: Diagnostic) -> None:
        if len(self) < self.maximum:
            super().append(item)
        else:
            self.truncated = True

    def extend(self, values: Iterable[Diagnostic]) -> None:
        for item in values:
            self.append(item)

    def ensure_budget_marker(self) -> None:
        if not self.truncated or self.maximum == 0:
            return
        marker = Diagnostic(
            "servlet_jsp.budget.diagnostics",
            f"Project diagnostic budget {self.maximum} reached",
            "warning",
        )
        if len(self) >= self.maximum:
            self[-1] = marker
        else:
            super().append(marker)


def resolve_servlet_jsp_module(
    *,
    project_id: str,
    project_name: str,
    module: ServletJspModule,
    java_results: Sequence[JavaSemanticAnalysisResult] = (),
    web_results: Sequence[WebXmlParseResult] = (),
    jsp_results: Sequence[JspParseResult] = (),
    properties_results: Sequence[PropertiesParseResult] = (),
    budgets: Optional[ResourceBudgets] = None,
) -> ServletJspResolution:
    """Resolve one module into deterministic Servlet/JSP graph facts.

    Java Class and Function nodes remain canonical external endpoints. Framework
    callbacks and parser-only operation records are consumed here, not copied.
    """

    effective = budgets or ResourceBudgets()
    out = _Collector(project_id, project_name, module.module_id, effective)
    diagnostics = _BoundedDiagnostics(effective.max_diagnostics_per_project)
    dependency_files: Dict[str, set[str]] = {}
    dependency_components: Dict[str, set[str]] = {}
    dependency_mappings: Dict[str, set[str]] = {}
    dependency_views: Dict[str, set[str]] = {}
    dependency_states: Dict[str, set[str]] = {}
    java_facts = [fact for result in java_results for fact in result.facts]
    descriptors = [result.descriptor for result in web_results if result.descriptor is not None]
    diagnostics.extend(item for result in java_results for item in result.diagnostics)
    diagnostics.extend(item for result in web_results for item in result.diagnostics)
    diagnostics.extend(item for result in jsp_results for item in result.diagnostics)
    diagnostics.extend(item for result in properties_results for item in result.diagnostics)
    metadata_complete = any(descriptor.metadata_complete is True for descriptor in descriptors)

    try:
        components, component_names = _resolve_components(out, java_facts, descriptors, metadata_complete, diagnostics)
        handlers = [fact for fact in java_facts if fact.kind == "ServletHandler"]
        callbacks = [fact for fact in java_facts if fact.kind in _CALLBACK_KINDS]
        callback_components = {fact.stable_id: components.get(str(fact.properties.get("component_id") or "")) for fact in callbacks}

        mapping_rows = _servlet_mapping_rows(descriptors)
        if not metadata_complete:
            for component in components.values():
                if component.kind != "Servlet":
                    continue
                for index, pattern in enumerate(_strings(component.properties.get("url_patterns"))):
                    mapping_rows.append((component.name, pattern, component.source, "annotation", index, component.stable_id))

        endpoints: List[ServletJspFact] = []
        for servlet_name, pattern, source, provenance, order, mapping_owner in mapping_rows:
            servlet = component_names.get(("Servlet", servlet_name))
            if servlet is None and mapping_owner:
                servlet = components.get(mapping_owner)
            mapping = out.make_fact(
                "ServletMapping",
                (mapping_owner or servlet_name, pattern, provenance, source.file_path, source.start_line, order),
                f"{servlet_name}:{pattern}",
                source,
                properties={
                    "servlet_name": servlet_name,
                    "url_patterns": [pattern],
                    "raw_url_pattern": pattern,
                    "mapping_kind": _mapping_kind(pattern),
                    "descriptor_order": order,
                    "provenance": provenance,
                },
            )
            if servlet is None:
                diagnostics.append(Diagnostic("servlet_jsp.resolve.servlet_mapping_unresolved", f"No servlet declaration matches {servlet_name!r}", "warning", source.file_path, source.start_line, source.end_line))
                continue
            out.rel(mapping, servlet, "MAPS_TO", source, occurrence=(source.file_path, source.start_line, order), reason="servlet mapping declaration", properties={"mapping_kind": _mapping_kind(pattern), "descriptor_order": order, "provenance": provenance})
            dependency_mappings.setdefault(mapping.stable_id, set()).add(servlet.stable_id)
            servlet_handlers = [fact for fact in handlers if str(fact.properties.get("component_id") or "") == servlet.stable_id]
            if not servlet_handlers:
                endpoint = _endpoint(out, servlet, mapping, pattern, "ALL", (), source)
                endpoints.append(endpoint)
                out.rel(servlet, endpoint, "HANDLES", source, occurrence=(mapping.stable_id, "ALL"), reason="servlet URL mapping")
            for handler in servlet_handlers:
                method = str(handler.properties.get("http_method") or "ALL")
                endpoint = _endpoint(out, servlet, mapping, pattern, method, (handler,), source)
                endpoints.append(endpoint)
                out.rel(servlet, endpoint, "HANDLES", source, occurrence=(mapping.stable_id, method, handler.stable_id), reason="servlet URL mapping and callback")
                if handler.source_symbol_id:
                    out.rel(endpoint, (handler.source_symbol_id, "Function"), "SEMANTIC_OF", handler.source, occurrence=(handler.stable_id,), reason="endpoint handler Java anchor", to_generated=False)
                dependency_components.setdefault(servlet.stable_id, set()).add(endpoint.stable_id)

        # Multiple mapping declarations can resolve to the same semantic endpoint.
        # Downstream resolution and relationship budgets operate on endpoints, not
        # declaration occurrences.
        endpoints = sorted({endpoint.stable_id: endpoint for endpoint in endpoints}.values(), key=lambda item: item.stable_id)

        _resolve_filters(out, components, component_names, descriptors, endpoints, metadata_complete, effective, diagnostics, dependency_mappings)
        view_by_path = _resolve_views(out, module, jsp_results, endpoints, dependency_files, dependency_views, diagnostics, effective)
        _resolve_jsp_servlets(out, endpoints, component_names, view_by_path, dependency_views)
        _resolve_java_operations(out, java_facts, endpoints, view_by_path, dependency_files, dependency_states)
        _resolve_lifecycle(out, callbacks, callback_components, dependency_components)
        _resolve_descriptor_configuration(out, descriptors, component_names, endpoints, view_by_path, dependency_files)
        _resolve_properties(out, properties_results, endpoints, view_by_path, dependency_files)
    except _ResolutionBudgetReached:
        pass

    if out.fact_budget_hit:
        diagnostics.append(Diagnostic("servlet_jsp.budget.facts", f"Fact budget {effective.max_facts_per_project} reached", "warning"))
    if out.relationship_budget_hit:
        diagnostics.append(Diagnostic("servlet_jsp.budget.relationships", f"Relationship budget {effective.max_relationships_per_project} reached", "warning"))
    diagnostics.ensure_budget_marker()

    relationship_budget_hit = any(item.code == "servlet_jsp.budget.filter_chains" for item in diagnostics)
    truncation_count = sum(result.truncation_count for result in java_results) + sum(1 for result in web_results if result.truncated) + sum(1 for result in jsp_results if result.truncated) + sum(1 for result in properties_results if result.truncated)
    if relationship_budget_hit:
        truncation_count += 1
    truncation_count += sum(1 for item in diagnostics if item.code == "servlet_jsp.budget.include_edges")
    truncation_count += int(out.fact_budget_hit) + int(out.relationship_budget_hit) + int(diagnostics.truncated)
    return ServletJspResolution(
        facts=tuple(sorted(out.facts.values(), key=lambda item: item.stable_id)),
        relationships=tuple(sorted(out.relationships.values(), key=lambda item: item.stable_id)),
        dependency_index=ServletJspDependencyIndex(
            files=_freeze_index(dependency_files),
            components=_freeze_index(dependency_components),
            mappings=_freeze_index(dependency_mappings),
            views=_freeze_index(dependency_views),
            state_slots=_freeze_index(dependency_states),
        ),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.file_path, item.start_line, item.code, item.message))),
        truncation_count=truncation_count,
        ambiguity_count=sum(result.ambiguity_count for result in java_results),
        missing_anchor_count=sum(result.missing_anchor_count for result in java_results),
    )


def _resolve_components(
    out: _Collector,
    java_facts: Sequence[ServletJspFact],
    descriptors: Sequence[Any],
    metadata_complete: bool,
    diagnostics: List[Diagnostic],
) -> Tuple[Dict[str, ServletJspFact], Dict[Tuple[str, str], ServletJspFact]]:
    descriptor_records: List[Tuple[str, WebXmlRecord]] = []
    for descriptor in descriptors:
        descriptor_records.extend(("Servlet", row) for row in descriptor.servlets)
        descriptor_records.extend(("Filter", row) for row in descriptor.filters)
        descriptor_records.extend(("Listener", row) for row in descriptor.listeners)
    records_by_class: Dict[Tuple[str, str], List[WebXmlRecord]] = {}
    for kind, record in descriptor_records:
        fqcn = str(record.get(_class_key(kind)) or "")
        if fqcn:
            records_by_class.setdefault((kind, fqcn), []).append(record)

    components: Dict[str, ServletJspFact] = {}
    names: Dict[Tuple[str, str], ServletJspFact] = {}
    for fact in java_facts:
        if fact.kind not in _COMPONENT_KINDS:
            continue
        fqcn = str(fact.properties.get("fqcn") or "")
        rows = records_by_class.get((fact.kind, fqcn), [])
        annotation_registered = bool(fact.properties.get("annotation")) and not metadata_complete
        if not rows and not annotation_registered:
            continue
        merged = _normalized_component_properties(fact.kind, fact.properties)
        merged["evidence"] = list(_strings(fact.properties.get("evidence")))
        declaration_sources = ["annotation"] if annotation_registered else []
        for record in rows:
            descriptor_props = _normalized_component_properties(fact.kind, record.values)
            for key in ("url_patterns", "servlet_names", "dispatcher_types"):
                merged[key] = list(dict.fromkeys([*_strings(merged.get(key)), *_strings(descriptor_props.get(key))]))
            for key, value in descriptor_props.items():
                if key not in {"url_patterns", "servlet_names", "dispatcher_types"} and value not in (None, "", []):
                    merged[key] = value
            declaration_sources.append("web.xml")
        name = str(merged.get("component_name") or fact.name)
        merged.update({"component_class": fqcn, _class_key(fact.kind): fqcn, _name_key(fact.kind): name, "declaration_sources": sorted(set(declaration_sources))})
        component = out.fact(replace(fact, name=name, properties=merged, extraction_method="servlet_jsp_resolver"))
        components[component.stable_id] = component
        names[(component.kind, component.name)] = component
        if component.source_symbol_id:
            out.rel(component, (component.source_symbol_id, "Class"), "SEMANTIC_OF", component.source, reason="component Java anchor", to_generated=False)

    for kind, record in descriptor_records:
        fqcn = str(record.get(_class_key(kind)) or "")
        existing = next((component for component in components.values() if component.kind == kind and str(component.properties.get("component_class") or "") == fqcn and fqcn), None)
        if existing is not None:
            names[(kind, str(record.get(_name_key(kind)) or existing.name))] = existing
            continue
        name = str(record.get(_name_key(kind)) or fqcn.rsplit(".", 1)[-1] or record.name)
        jsp_file = _normalize(str(record.get("jsp_file") or ""))
        identity = (fqcn,) if fqcn else (name, jsp_file) if jsp_file else (name,)
        props = _normalized_component_properties(kind, record.values)
        props.update({"component_class": fqcn, _class_key(kind): fqcn, _name_key(kind): name, "component_name": name, "declaration_sources": ["web.xml"]})
        component = out.make_fact(kind, identity, name, record.source, extraction_method="tree_sitter_xml", resolution_status="resolved" if fqcn or record.get("jsp_file") else "unresolved", properties=props)
        components[component.stable_id] = component
        names[(kind, name)] = component
        if not fqcn and not record.get("jsp_file"):
            diagnostics.append(Diagnostic("servlet_jsp.resolve.component_class_missing", f"{kind} {name!r} has no implementation class", "warning", record.source.file_path, record.source.start_line, record.source.end_line))
    return components, names


def _servlet_mapping_rows(descriptors: Sequence[Any]) -> List[Tuple[str, str, SourceSpan, str, int, str]]:
    rows: List[Tuple[str, str, SourceSpan, str, int, str]] = []
    for descriptor in descriptors:
        for record in descriptor.servlet_mappings:
            for pattern_index, pattern in enumerate(_strings(record.get("url_patterns"))):
                rows.append((str(record.get("servlet_name") or record.name), pattern, record.source, "web.xml", int(record.get("descriptor_order", record.order)), f"{record.stable_id}:{pattern_index}"))
    return rows


def _endpoint(out: _Collector, servlet: ServletJspFact, mapping: ServletJspFact, pattern: str, method: str, handlers: Sequence[ServletJspFact], source: SourceSpan) -> ServletJspFact:
    symbols = [item.source_symbol_id for item in handlers if item.source_symbol_id]
    names = [str(item.properties.get("method_name") or item.name) for item in handlers]
    return out.make_fact(
        "ApiEndpoint",
        (servlet.stable_id, pattern, method),
        f"{method} {pattern}",
        source,
        source_symbol_id=symbols[0] if len(symbols) == 1 else "",
        properties={
            "path": pattern,
            "raw_url_pattern": pattern,
            "mapping_kind": _mapping_kind(pattern),
            "http_method": method,
            "handler_names": names,
            "handler_symbol_ids": symbols,
            "servlet_class": str(servlet.properties.get("servlet_class") or servlet.properties.get("component_class") or ""),
            "servlet_name": servlet.name,
            "controller_class": str(servlet.properties.get("servlet_class") or servlet.properties.get("component_class") or ""),
            "declaration_sources": list(_strings(servlet.properties.get("declaration_sources"))),
        },
    )


def _resolve_filters(
    out: _Collector,
    components: Mapping[str, ServletJspFact],
    names: Mapping[Tuple[str, str], ServletJspFact],
    descriptors: Sequence[Any],
    endpoints: Sequence[ServletJspFact],
    metadata_complete: bool,
    budgets: ResourceBudgets,
    diagnostics: List[Diagnostic],
    dependency_mappings: Dict[str, set[str]],
) -> None:
    rules: List[Tuple[int, int, int, ServletJspFact, str, str, Tuple[str, ...], SourceSpan, str]] = []
    for descriptor in descriptors:
        for record in descriptor.filter_mappings:
            filter_fact = names.get(("Filter", str(record.get("filter_name") or record.name)))
            if filter_fact is None:
                diagnostics.append(Diagnostic("servlet_jsp.resolve.filter_mapping_unresolved", f"No filter declaration matches {record.name!r}", "warning", record.source.file_path, record.source.start_line, record.source.end_line))
                continue
            dispatchers = tuple(_strings(record.get("dispatchers"))) or ("REQUEST",)
            order = int(record.get("descriptor_order", record.order))
            for index, pattern in enumerate(_strings(record.get("url_patterns"))):
                rules.append((0, order, index, filter_fact, "url", pattern, dispatchers, record.source, record.stable_id))
            for index, servlet_name in enumerate(_strings(record.get("servlet_names"))):
                rules.append((1, order, index, filter_fact, "servlet", servlet_name, dispatchers, record.source, record.stable_id))
    if not metadata_complete:
        for component in components.values():
            if component.kind != "Filter" or "annotation" not in _strings(component.properties.get("declaration_sources")):
                continue
            dispatchers = tuple(_strings(component.properties.get("dispatcher_types"))) or ("REQUEST",)
            for index, pattern in enumerate(_strings(component.properties.get("url_patterns"))):
                rules.append((2, 0, index, component, "url", pattern, dispatchers, component.source, component.stable_id))
            for index, servlet_name in enumerate(_strings(component.properties.get("servlet_names"))):
                rules.append((2, 0, index, component, "servlet", servlet_name, dispatchers, component.source, component.stable_id))

    resolved_rules: List[Tuple[int, int, ServletJspFact, ServletJspFact, str, str, Tuple[str, ...], SourceSpan]] = []
    for group, order, index, filter_fact, mapping_kind, value, dispatchers, source, owner in sorted(rules, key=lambda row: (row[0], row[1], row[2], row[3].stable_id)):
        contract_mapping_kind = "url-pattern" if mapping_kind == "url" else "servlet-name"
        mapping = out.make_fact(
            "FilterMapping",
            (owner, mapping_kind, value, source.file_path, source.start_line, index),
            f"{filter_fact.name}:{value}",
            source,
            properties={
                "filter_name": filter_fact.name,
                "url_patterns": [value] if mapping_kind == "url" else [],
                "servlet_names": [value] if mapping_kind == "servlet" else [],
                "mapping_kind": contract_mapping_kind,
                "descriptor_order": order,
                "dispatcher_types": list(dispatchers),
                "order_status": "unknown" if group == 2 else "exact",
                "provenance": "annotation" if group == 2 else "web.xml",
            },
        )
        out.rel(mapping, filter_fact, "MAPS_TO", source, occurrence=(owner, mapping_kind, value, index), reason="filter mapping declaration", properties={"mapping_kind": contract_mapping_kind, "descriptor_order": order, "dispatcher_types": list(dispatchers), "provenance": "annotation" if group == 2 else "web.xml"})
        dependency_mappings.setdefault(mapping.stable_id, set()).add(filter_fact.stable_id)
        resolved_rules.append((group, order, mapping, filter_fact, mapping_kind, value, dispatchers, source))

    count = 0
    budget_reported = False
    chain_index: Dict[Tuple[str, str], int] = {}
    for group, order, mapping, filter_fact, mapping_kind, value, dispatchers, source in resolved_rules:
        for endpoint in endpoints:
            matched = _pattern_matches(value, str(endpoint.properties.get("path") or "")) if mapping_kind == "url" else value == str(endpoint.properties.get("servlet_name") or "")
            if not matched:
                continue
            for dispatcher in dispatchers:
                if count >= budgets.max_endpoint_filter_relationships:
                    if not budget_reported:
                        diagnostics.append(Diagnostic("servlet_jsp.budget.filter_chains", f"Endpoint/filter relationship budget {budgets.max_endpoint_filter_relationships} reached", "warning", source.file_path, source.start_line, source.end_line))
                        budget_reported = True
                    return
                count += 1
                chain_key = (endpoint.stable_id, dispatcher)
                order_index = chain_index.get(chain_key, 0)
                chain_index[chain_key] = order_index + 1
                relationship_properties: Dict[str, Any] = {
                    "occurrence_key": f"{mapping.stable_id}:{value}:{endpoint.stable_id}:{dispatcher}",
                    "order_status": "unknown" if group == 2 else "exact",
                    "mapping_kind": "url-pattern" if mapping_kind == "url" else "servlet-name",
                    "dispatcher_types": [dispatcher],
                    "dispatch_type": dispatcher,
                    "async_supported": filter_fact.properties.get("async_supported"),
                    "declaration_source": "annotation" if group == 2 else "web.xml",
                    "descriptor_order": order,
                }
                if group != 2:
                    relationship_properties["order_index"] = order_index
                out.rel(endpoint, filter_fact, "PASSES_THROUGH", source, occurrence=(mapping.stable_id, value, endpoint.stable_id, dispatcher), reason="effective filter chain mapping", properties=relationship_properties)


def _resolve_views(
    out: _Collector,
    module: ServletJspModule,
    results: Sequence[JspParseResult],
    endpoints: Sequence[ServletJspFact],
    dependency_files: Dict[str, set[str]],
    dependency_views: Dict[str, set[str]],
    diagnostics: List[Diagnostic],
    budgets: ResourceBudgets,
) -> Dict[str, ServletJspFact]:
    views: Dict[str, ServletJspFact] = {}
    include_edges = 0
    include_budget_reported = False
    for result in results:
        path = _normalize(result.file_path)
        view = out.make_fact(
            "JSPView",
            ("artifact", _jsp_kind(path), path),
            os.path.basename(path),
            SourceSpan(path),
            extraction_method="tree_sitter_jsp",
            resolution_status="resolved" if result.complete else "partial",
            properties={"artifact_kind": _jsp_kind(path), "module_path": module.rel_path, "coverage_status": "complete" if result.complete else "partial", "truncated": result.truncated},
        )
        # Match the foundation artifact identity exactly.
        generated_id = view.stable_id
        view = out.fact(replace(view, stable_id=stable_semantic_id("artifact", out.project_id, out.module_id, _jsp_kind(path), path)))
        out.facts.pop(generated_id, None)
        views[path] = view

    # Target linking is a second pass so scan order cannot change whether a
    # static JSP reference resolves to JSPView or falls back to WebTarget.
    for result in results:
        path = _normalize(result.file_path)
        view = views[path]
        for index, expression in enumerate(result.expressions):
            safe_expression = str(redact_value(expression.raw, expression.raw))
            expr = out.make_fact(
                "JspExpression",
                (path, expression.span.start_line, expression.span.start_column, index, expression.raw),
                f"EL at {expression.span.start_line}:{expression.span.start_column}",
                expression.span,
                extraction_method="jsp_el",
                raw_value=safe_expression,
                properties={"expression": safe_expression, "variables": [redact_value(item, item) for item in expression.el.variables] if expression.el else [], "property_paths": [redact_value(item, item) for item in expression.el.property_paths] if expression.el else [], "functions": [redact_value(item.raw, item.raw) for item in expression.functions]},
            )
            out.rel(view, expr, "DECLARES", expression.span, occurrence=(index,), reason="JSP expression occurrence")
            for state_index, read in enumerate(expression.el.state_reads if expression.el else ()):
                slot = _state_slot(out, read.scope, read.name or "*", expression.span, read.dynamic)
                out.rel(expr, slot, "READS", expression.span, occurrence=(index, state_index, read.raw), reason="JSP EL implicit-object read", resolution_status="dynamic" if read.dynamic else "resolved", properties={"correlation_status": "possible", "raw_value": redact_value(read.raw, read.raw)})
                dependency_views.setdefault(view.stable_id, set()).add(slot.stable_id)
        for index, region in enumerate((*result.actions, *result.tags)):
            prefix, _, local_name = region.name.partition(":")
            tag = out.make_fact(
                "JspTag",
                (path, region.span.start_line, region.span.start_column, index, region.name),
                region.name,
                region.span,
                extraction_method="jsp_state_machine",
                properties={
                    "tag_name": region.name,
                    "prefix": prefix if local_name else "",
                    "uri": region.taglib_uri,
                    "attributes": region.attributes,
                    "target_kind": region.semantic_kind,
                },
            )
            out.rel(view, tag, "DECLARES", region.span, occurrence=("tag", index, region.name), reason="JSP tag occurrence")
        for index, operation in enumerate(result.scriptlet_operations):
            if operation.scope:
                slot = _state_slot(out, operation.scope, operation.name or "*", operation.span, operation.resolution_status != "resolved")
                rel_type = "WRITES" if "set" in operation.kind.lower() else "READS"
                out.rel(view, slot, rel_type, operation.span, occurrence=("scriptlet", index, operation.kind), reason="JSP scriptlet state access", resolution_status=operation.resolution_status, properties={"correlation_status": "possible", "raw_value": operation.raw})
        for index, target in enumerate(result.targets):
            if target.kind == "include":
                if include_edges >= budgets.max_include_edges_per_module:
                    if not include_budget_reported:
                        diagnostics.append(Diagnostic("servlet_jsp.budget.include_edges", f"Include edge budget {budgets.max_include_edges_per_module} reached", "warning", target.span.file_path, target.span.start_line, target.span.end_line))
                        include_budget_reported = True
                    continue
                include_edges += 1
            target_method = target.method or ("GET" if target.kind in {"link", "redirect"} else "")
            target_fact = _target_fact(out, target.resolved_path, target.raw_value, target.classification, target.resolution_status, target.span, views, endpoints=endpoints, method=target_method)
            rel_type = {"include": "INCLUDES", "forward": "FORWARDS_TO", "redirect": "REDIRECTS_TO", "form": "SUBMITS_TO", "link": "LINKS_TO", "resource": "LINKS_TO"}.get(target.kind, "LINKS_TO")
            out.rel(view, target_fact, rel_type, target.span, occurrence=(index, target.kind, target.raw_value), reason="JSP target", resolution_status=target.resolution_status, properties={"raw_value": redact_value(target.raw_value, target.raw_value), "resolved_value": target.resolved_path})
            if target.resolved_path:
                dependency_files.setdefault(path, set()).add(target.resolved_path)
                dependency_views.setdefault(view.stable_id, set()).add(target_fact.stable_id)
        for dependency in result.dependencies:
            if dependency.target_path:
                dependency_files.setdefault(path, set()).add(dependency.target_path)
    return views


def _resolve_jsp_servlets(
    out: _Collector,
    endpoints: Sequence[ServletJspFact],
    component_names: Mapping[Tuple[str, str], ServletJspFact],
    views: Mapping[str, ServletJspFact],
    dependencies: Dict[str, set[str]],
) -> None:
    for endpoint in endpoints:
        servlet = component_names.get(("Servlet", str(endpoint.properties.get("servlet_name") or "")))
        jsp_file = str(servlet.properties.get("jsp_file") or "") if servlet is not None else ""
        if not jsp_file:
            continue
        view = _view_for_path(views, _normalize(jsp_file))
        if view is None:
            continue
        out.rel(endpoint, view, "FORWARDS_TO", servlet.source, occurrence=(servlet.stable_id, jsp_file), reason="descriptor jsp-file servlet target")
        dependencies.setdefault(view.stable_id, set()).add(endpoint.stable_id)


def _resolve_java_operations(out: _Collector, facts: Sequence[ServletJspFact], endpoints: Sequence[ServletJspFact], views: Mapping[str, ServletJspFact], dependency_files: Dict[str, set[str]], dependency_states: Dict[str, set[str]]) -> None:
    for fact in facts:
        if fact.kind in {"DispatchOperation", "RedirectOperation"} and fact.source_symbol_id:
            operation = str(fact.properties.get("operation") or "")
            target_value = str(fact.properties.get("target") or fact.resolved_value or "")
            target_method = "GET" if operation == "redirect" else ""
            target = _target_fact(out, _normalize(target_value), fact.raw_value, "java", fact.resolution_status, fact.source, views, endpoints=endpoints, method=target_method)
            rel_type = "FORWARDS_TO" if operation == "forward" else "INCLUDES" if operation == "include" else "REDIRECTS_TO"
            out.rel((fact.source_symbol_id, "Function"), target, rel_type, fact.source, occurrence=(fact.stable_id,), reason="Servlet API navigation", resolution_status=fact.resolution_status, properties={"raw_value": redact_value(fact.raw_value, fact.raw_value), "resolved_value": target_value}, from_generated=False)
            if target_value:
                dependency_files.setdefault(fact.source.file_path, set()).add(_normalize(target_value))
        elif fact.kind in {"StateAccess", "CookieAccess"} and fact.source_symbol_id:
            scope = str(fact.properties.get("scope") or "unknown")
            key = str(fact.properties.get("key") or fact.resolved_value or "*")
            slot = _state_slot(out, scope, key, fact.source, fact.resolution_status != "resolved")
            rel_type = "WRITES" if str(fact.properties.get("access") or "read") == "write" else "READS"
            out.rel((fact.source_symbol_id, "Function"), slot, rel_type, fact.source, occurrence=(fact.stable_id,), reason="Servlet state access", resolution_status=fact.resolution_status, properties={"correlation_status": "possible", "raw_value": redact_value(fact.raw_value, fact.raw_value)}, from_generated=False)
            dependency_states.setdefault(slot.stable_id, set()).add(fact.source.file_path)


def _resolve_lifecycle(out: _Collector, callbacks: Sequence[ServletJspFact], component_by_callback: Mapping[str, Optional[ServletJspFact]], dependencies: Dict[str, set[str]]) -> None:
    for callback in callbacks:
        if callback.kind not in {"ServletLifecycle", "FilterLifecycle", "ListenerCallback"}:
            continue
        component = component_by_callback.get(callback.stable_id)
        if component is None:
            continue
        event_name = str(callback.properties.get("lifecycle_event") or callback.name)
        event = out.make_fact("LifecycleEvent", (event_name,), event_name, callback.source, properties={"event_kind": event_name})
        out.rel(component, event, "INITIALIZES", callback.source, occurrence=(callback.stable_id,), reason="component lifecycle callback")
        if callback.source_symbol_id:
            out.rel((callback.source_symbol_id, "Function"), event, "HANDLES_LIFECYCLE", callback.source, occurrence=(callback.stable_id,), reason="lifecycle Java handler", from_generated=False)
        dependencies.setdefault(component.stable_id, set()).add(event.stable_id)


def _resolve_descriptor_configuration(out: _Collector, descriptors: Sequence[Any], component_names: Mapping[Tuple[str, str], ServletJspFact], endpoints: Sequence[ServletJspFact], views: Mapping[str, ServletJspFact], dependency_files: Dict[str, set[str]]) -> None:
    for descriptor in descriptors:
        descriptor_fact = out.make_fact(
            "WebDescriptor",
            ("artifact", "web_xml", descriptor.file_path),
            os.path.basename(descriptor.file_path),
            descriptor.source,
            extraction_method="tree_sitter_xml",
            raw_value=descriptor.doctype,
            resolved_value=descriptor.file_path,
            properties={"module_path": descriptor.module_path, "namespace": descriptor.namespace, "version": descriptor.version, "metadata_complete": descriptor.metadata_complete, "provenance": "web.xml"},
        )
        generated_id = descriptor_fact.stable_id
        descriptor_fact = out.fact(replace(descriptor_fact, stable_id=stable_semantic_id("artifact", out.project_id, out.module_id, "web_xml", descriptor.file_path)))
        out.facts.pop(generated_id, None)
        for kind, records in (("Servlet", descriptor.servlets), ("Filter", descriptor.filters), ("Listener", descriptor.listeners)):
            for record in records:
                component = component_names.get((kind, str(record.get(_name_key(kind)) or record.name)))
                if component is not None:
                    out.rel(descriptor_fact, component, "DECLARES", record.source, occurrence=(record.stable_id,), reason="web.xml component declaration", properties={"provenance": "web.xml", "descriptor_order": int(record.get("descriptor_order", record.order))})
        for record in descriptor.context_params + descriptor.session_configs + descriptor.login_configs:
            config = out.make_fact("WebConfiguration", (record.stable_id,), record.name, record.source, extraction_method="tree_sitter_xml", properties={"config_kind": record.kind, "config_key": record.name, "config_value": str(record.get("param_value") or record.get("session_timeout") or record.get("auth_method") or ""), "auth_method": str(record.get("auth_method") or ""), "realm_name": str(record.get("realm_name") or ""), "provenance": "web.xml"})
            out.rel(descriptor_fact, config, "CONFIGURES", record.source, occurrence=(record.stable_id,), reason="web.xml configuration", properties={"provenance": "web.xml", "descriptor_order": int(record.get("descriptor_order", record.order))})
        for record in descriptor.welcome_files:
            welcome = out.make_fact("WelcomePage", (record.stable_id,), record.name, record.source, extraction_method="tree_sitter_xml", properties={"path": str(record.get("path") or record.name), "order_index": int(record.get("welcome_order", record.order)), "provenance": "web.xml"})
            out.rel(descriptor_fact, welcome, "DECLARES", record.source, occurrence=(record.stable_id,), reason="welcome-file declaration", properties={"provenance": "web.xml", "descriptor_order": int(record.get("descriptor_order", record.order))})
            target = _target_fact(out, _normalize(str(record.get("path") or record.name)), str(record.get("path") or record.name), "descriptor", "resolved", record.source, views, endpoints=endpoints, method="GET")
            out.rel(welcome, target, "RESOLVES_TO", record.source, occurrence=(record.stable_id,), reason="welcome-file target")
        for record in descriptor.error_pages:
            location = str(record.get("location") or "")
            error = out.make_fact("ErrorPage", (record.stable_id,), record.name, record.source, extraction_method="tree_sitter_xml", properties={"error_code": str(record.get("error_code") or ""), "exception_type": str(record.get("exception_type") or ""), "location": location, "provenance": "web.xml"})
            out.rel(descriptor_fact, error, "DECLARES", record.source, occurrence=(record.stable_id,), reason="error-page declaration", properties={"provenance": "web.xml", "descriptor_order": int(record.get("descriptor_order", record.order))})
            if location:
                target = _target_fact(out, _normalize(location), location, "descriptor", "resolved", record.source, views, endpoints=endpoints)
                out.rel(error, target, "RESOLVES_TO", record.source, occurrence=(record.stable_id,), reason="error-page target")
                dependency_files.setdefault(descriptor.file_path, set()).add(_normalize(location))
        authorities: Dict[str, ServletJspFact] = {}
        for record in descriptor.security_roles:
            role = str(record.get("role_name") or record.name)
            authorities[role] = out.make_fact("Authority", (role,), role, record.source, extraction_method="tree_sitter_xml", properties={"role": role, "provenance": "web.xml"})
        for record in descriptor.security_constraints:
            collection_semantics: List[Dict[str, Any]] = []
            for collection_index, collection in enumerate(record.get("web_resource_collections", []) or []):
                values = collection.get("values", {}) if isinstance(collection, dict) else {}
                collection_semantics.append({
                    "stable_id": str(collection.get("stable_id") or f"{record.stable_id}:{collection_index}") if isinstance(collection, dict) else f"{record.stable_id}:{collection_index}",
                    "name": str(values.get("web_resource_name") or f"collection[{collection_index}]"),
                    "index": collection_index,
                    "url_patterns": list(_strings(values.get("url_patterns"))),
                    "methods": sorted(set(_strings(values.get("http_methods")))),
                    "method_omissions": sorted(set(_strings(values.get("http_method_omissions")))),
                })
            constraint = out.make_fact(
                "SecurityConstraint",
                (record.stable_id,),
                record.name,
                record.source,
                extraction_method="tree_sitter_xml",
                properties={
                    "methods": sorted({method for collection in collection_semantics for method in collection["methods"]}),
                    "method_omissions": sorted({method for collection in collection_semantics for method in collection["method_omissions"]}),
                    "resource_collections": collection_semantics,
                    "transport_guarantee": list(_strings(record.get("transport_guarantees"))),
                    "provenance": "web.xml",
                },
            )
            out.rel(descriptor_fact, constraint, "DECLARES", record.source, occurrence=(record.stable_id,), reason="security constraint", properties={"provenance": "web.xml", "descriptor_order": int(record.get("descriptor_order", record.order))})
            for collection in collection_semantics:
                patterns = tuple(collection["url_patterns"])
                methods = set(collection["methods"])
                omissions = set(collection["method_omissions"])
                for endpoint in endpoints:
                    method = str(endpoint.properties.get("http_method") or "ALL").upper()
                    if patterns and not any(_pattern_matches(pattern, str(endpoint.properties.get("path") or "")) for pattern in patterns):
                        continue
                    if method != "ALL" and methods and method not in methods:
                        continue
                    if method != "ALL" and omissions and method in omissions:
                        continue
                    occurrence = (
                        record.stable_id,
                        collection["stable_id"],
                        collection["index"],
                        endpoint.stable_id,
                        *collection["methods"],
                        "omissions",
                        *collection["method_omissions"],
                    )
                    out.rel(
                        constraint,
                        endpoint,
                        "PROTECTS",
                        record.source,
                        occurrence=occurrence,
                        reason="security URL and HTTP method constraint",
                        properties={
                            "occurrence_key": f"{record.stable_id}:{collection['stable_id']}:{endpoint.stable_id}",
                            "resource_collection": collection["name"],
                            "resource_collection_index": collection["index"],
                            "methods": collection["methods"],
                            "method_omissions": collection["method_omissions"],
                            "provenance": "web.xml",
                            "descriptor_order": int(record.get("descriptor_order", record.order)),
                        },
                    )
            for role in _strings(record.get("role_names")):
                authority = authorities.get(role) or out.make_fact("Authority", (role,), role, record.source, extraction_method="tree_sitter_xml", properties={"role": role, "provenance": "web.xml"})
                out.rel(constraint, authority, "REQUIRES_AUTHORITY", record.source, occurrence=(record.stable_id, role), reason="auth-constraint role")


def _resolve_properties(out: _Collector, results: Sequence[PropertiesParseResult], endpoints: Sequence[ServletJspFact], views: Mapping[str, ServletJspFact], dependency_files: Dict[str, set[str]]) -> None:
    for result in results:
        for index, target in enumerate(result.targets):
            source = out.make_fact("WebConfiguration", (result.file_path, target.source_key), target.source_key, target.source, extraction_method="properties", raw_value=target.raw_value, resolved_value=target.resolved_path, properties={"config_kind": "property", "config_key": target.source_key, "config_value": target.raw_value})
            destination = _target_fact(out, target.resolved_path, target.raw_value, target.classification, target.resolution_status, target.source, views, endpoints=endpoints)
            out.rel(source, destination, "RESOLVES_TO", target.source, occurrence=(index, target.source_key), reason="properties path target", resolution_status=target.resolution_status, properties={"raw_value": target.raw_value, "resolved_value": target.resolved_path})
            if target.resolved_path:
                dependency_files.setdefault(result.file_path, set()).add(target.resolved_path)


def _target_fact(out: _Collector, resolved: str, raw: str, classification: str, status: str, source: SourceSpan, views: Mapping[str, ServletJspFact], *, endpoints: Sequence[ServletJspFact] = (), method: str = "") -> ServletJspFact:
    normalized = _normalize(resolved)
    matched_view = _view_for_path(views, normalized)
    if matched_view is not None:
        return matched_view
    matched_endpoint = _endpoint_for_path(endpoints, normalized, method)
    if matched_endpoint is not None:
        return matched_endpoint
    safe_raw = str(redact_value(raw, raw))
    dynamic = status == "dynamic" or not normalized
    identity: Tuple[object, ...] = (normalized or raw, classification)
    if dynamic:
        identity += (source.file_path, source.start_line, source.start_column)
    return out.make_fact("WebTarget", identity, normalized or safe_raw or "dynamic target", source, resolution_status=status or "unresolved", raw_value=safe_raw, resolved_value=normalized, properties={"target": normalized or safe_raw, "target_kind": classification, "dynamic": dynamic})


def _endpoint_for_path(endpoints: Sequence[ServletJspFact], path: str, method: str) -> Optional[ServletJspFact]:
    normalized = _normalize(path.split("?", 1)[0].split("#", 1)[0])
    if not normalized:
        return None
    candidates: List[Tuple[Tuple[int, int, int], ServletJspFact]] = []
    for endpoint in endpoints:
        endpoint_path = str(endpoint.properties.get("path") or "")
        pattern_rank = _servlet_pattern_match_rank(endpoint_path, f"/{normalized}")
        if pattern_rank is None:
            continue
        endpoint_method = str(endpoint.properties.get("http_method") or "ALL").upper()
        if method and endpoint_method not in {method.upper(), "ALL"}:
            continue
        method_rank = 1 if method and endpoint_method == method.upper() else 0
        candidates.append(((*pattern_rank, method_rank), endpoint))
    if not candidates:
        return None
    best_rank = max(rank for rank, _ in candidates)
    matches = {endpoint.stable_id: endpoint for rank, endpoint in candidates if rank == best_rank}
    return next(iter(matches.values())) if len(matches) == 1 else None


def _view_for_path(views: Mapping[str, ServletJspFact], path: str) -> Optional[ServletJspFact]:
    if not path:
        return None
    if path in views:
        return views[path]
    suffix = f"/{path}"
    candidates = [view for candidate, view in views.items() if candidate.endswith(suffix)]
    return candidates[0] if len(candidates) == 1 else None


def _state_slot(out: _Collector, scope: str, key: str, source: SourceSpan, dynamic: bool) -> ServletJspFact:
    normalized_key = key or "*"
    identity: Tuple[object, ...] = (scope, normalized_key, source.file_path, source.start_line, source.start_column) if dynamic else (scope, normalized_key)
    safe_key = str(redact_value(normalized_key, normalized_key))
    return out.make_fact("StateSlot", identity, f"{scope}:{safe_key}", source, resolution_status="dynamic" if dynamic else "resolved", raw_value=safe_key if dynamic else "", resolved_value="" if dynamic else safe_key, properties={"scope": scope, "key": safe_key, "dynamic": dynamic, "correlation_status": "possible"})


def _normalized_component_properties(kind: str, values: Mapping[str, Any]) -> Dict[str, Any]:
    result = {
        "component_name": str(values.get(_name_key(kind)) or ""),
        "component_class": str(values.get(_class_key(kind)) or ""),
        "url_patterns": list(_strings(values.get("url_patterns"))),
        "servlet_names": list(_strings(values.get("servlet_names"))),
        "dispatcher_types": list(_strings(values.get("dispatchers"))) or list(_strings(values.get("dispatcher_types"))),
        "async_supported": values.get("async_supported"),
        "init_params": _redacted_init_params(values.get("init_params") or []),
        "jsp_file": str(values.get("jsp_file") or ""),
        "load_on_startup": str(values.get("load_on_startup") or ""),
    }
    return result


def _redacted_init_params(rows: Any) -> List[Any]:
    sanitized: List[Any] = []
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if not isinstance(row, Mapping):
            sanitized.append(row)
            continue
        values = dict(row)
        nested = values.get("values") if isinstance(values.get("values"), Mapping) else values
        name = str(nested.get("name") or nested.get("param_name") or nested.get("raw_name") or "")
        if str(redact_value(name, name)) == "[REDACTED]":
            for key in ("name", "param_name", "raw_name", "value", "param_value", "raw_value"):
                if key in nested:
                    nested[key] = "[REDACTED]"
            if nested is not values:
                values["values"] = nested
        sanitized.append(values)
    return sanitized


def _class_key(kind: str) -> str:
    return {"Servlet": "servlet_class", "Filter": "filter_class", "Listener": "listener_class"}[kind]


def _name_key(kind: str) -> str:
    return {"Servlet": "servlet_name", "Filter": "filter_name", "Listener": "listener_class"}[kind]


def _mapping_kind(pattern: str) -> str:
    if not pattern or "${" in pattern or "#{" in pattern:
        return "dynamic"
    if pattern == "/":
        return "default"
    if pattern.startswith("*."):
        return "extension"
    if pattern.endswith("/*"):
        return "path-prefix"
    return "exact"


def _pattern_matches(filter_pattern: str, endpoint_pattern: str) -> bool:
    filter_kind = _mapping_kind(filter_pattern)
    endpoint_kind = _mapping_kind(endpoint_pattern)
    if filter_kind == "dynamic" or endpoint_kind == "dynamic":
        return False
    if filter_kind == "default" or endpoint_kind == "default":
        return True
    if filter_kind == "exact":
        return _servlet_pattern_match_rank(endpoint_pattern, filter_pattern) is not None
    if endpoint_kind == "exact":
        return _servlet_pattern_match_rank(filter_pattern, endpoint_pattern) is not None
    if filter_kind == "path-prefix" and endpoint_kind == "path-prefix":
        first = filter_pattern[:-2]
        second = endpoint_pattern[:-2]
        return first == second or first.startswith(second + "/") or second.startswith(first + "/")
    if filter_kind == "extension" and endpoint_kind == "extension":
        first = filter_pattern[1:]
        second = endpoint_pattern[1:]
        return first.endswith(second) or second.endswith(first)
    # A path-prefix set and an extension set always have a non-empty
    # intersection (for example /foo/* and *.jsp intersect at /foo/a.jsp).
    return {filter_kind, endpoint_kind} == {"path-prefix", "extension"}


def _servlet_pattern_match_rank(pattern: str, request_path: str) -> Optional[Tuple[int, int]]:
    kind = _mapping_kind(pattern)
    if kind == "dynamic":
        return None
    if kind == "exact":
        return (4, len(pattern)) if pattern == request_path else None
    if kind == "path-prefix":
        prefix = pattern[:-2]
        if request_path == prefix or request_path.startswith(prefix + "/"):
            return (3, len(prefix))
        return None
    if kind == "extension":
        extension = pattern[1:]
        return (2, len(extension)) if request_path.endswith(extension) else None
    return (1, 0)


def _strings(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(item) for item in value if str(item))


def _normalize(path: str) -> str:
    value = (path or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.lstrip("/")


def _jsp_kind(path: str) -> str:
    lower = path.lower()
    return "jspx" if lower.endswith(".jspx") else "jsp_fragment" if lower.endswith(".jspf") else "jsp"


def _freeze_index(values: Mapping[str, set[str]]) -> Dict[str, Tuple[str, ...]]:
    return {key: tuple(sorted(items)) for key, items in sorted(values.items())}


def _fact_rank(fact: ServletJspFact) -> Tuple[int, int, float]:
    return (1 if fact.source_symbol_id else 0, 1 if fact.resolution_status == "resolved" else 0, fact.confidence)

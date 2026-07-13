from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from tools.servlet_jsp.java_identity import JavaIdentityIndex, JavaIdentityProvider
from tools.servlet_jsp.models import (
    Diagnostic,
    ResourceBudgets,
    ServletJspFact,
    ServletJspRelationship,
    SourceSpan,
    stable_semantic_id,
)
from tools.servlet_jsp.parser_runtime import parse_java_bytes


_WEB_ANNOTATIONS = {
    "javax.servlet.annotation.WebServlet": "Servlet",
    "jakarta.servlet.annotation.WebServlet": "Servlet",
    "javax.servlet.annotation.WebFilter": "Filter",
    "jakarta.servlet.annotation.WebFilter": "Filter",
    "javax.servlet.annotation.WebListener": "Listener",
    "jakarta.servlet.annotation.WebListener": "Listener",
}
_SERVLET_TYPES = {
    *(f"{prefix}.{name}" for prefix in ("javax.servlet", "jakarta.servlet") for name in ("Servlet", "GenericServlet")),
    *(f"{prefix}.http.HttpServlet" for prefix in ("javax.servlet", "jakarta.servlet")),
}
_FILTER_TYPES = {f"{prefix}.Filter" for prefix in ("javax.servlet", "jakarta.servlet")}
_LISTENER_SIMPLE_NAMES = {
    "ServletContextListener",
    "ServletContextAttributeListener",
    "ServletRequestListener",
    "ServletRequestAttributeListener",
    "HttpSessionListener",
    "HttpSessionAttributeListener",
    "HttpSessionIdListener",
}
_LISTENER_TYPES = {
    f"{prefix}.{name}"
    for prefix in ("javax.servlet", "jakarta.servlet")
    for name in _LISTENER_SIMPLE_NAMES
    if not name.startswith("HttpSession")
} | {
    f"{prefix}.http.{name}"
    for prefix in ("javax.servlet", "jakarta.servlet")
    for name in _LISTENER_SIMPLE_NAMES
    if name.startswith("HttpSession")
}
_REQUEST_TYPES = {
    f"{prefix}.ServletRequest" for prefix in ("javax.servlet", "jakarta.servlet")
} | {f"{prefix}.http.HttpServletRequest" for prefix in ("javax.servlet", "jakarta.servlet")}
_RESPONSE_TYPES = {
    f"{prefix}.ServletResponse" for prefix in ("javax.servlet", "jakarta.servlet")
} | {f"{prefix}.http.HttpServletResponse" for prefix in ("javax.servlet", "jakarta.servlet")}
_SESSION_TYPES = {f"{prefix}.http.HttpSession" for prefix in ("javax.servlet", "jakarta.servlet")}
_CONTEXT_TYPES = {f"{prefix}.ServletContext" for prefix in ("javax.servlet", "jakarta.servlet")}
_DISPATCHER_TYPES = {f"{prefix}.RequestDispatcher" for prefix in ("javax.servlet", "jakarta.servlet")}
_FILTER_CHAIN_TYPES = {f"{prefix}.FilterChain" for prefix in ("javax.servlet", "jakarta.servlet")}
_COOKIE_TYPES = {f"{prefix}.http.Cookie" for prefix in ("javax.servlet", "jakarta.servlet")}
_CONFIG_TYPES = {
    *(f"{prefix}.{name}" for prefix in ("javax.servlet", "jakarta.servlet") for name in ("ServletConfig", "FilterConfig")),
}
_EVENT_ROLES = {
    **{f"{prefix}.ServletContextEvent": "context_event" for prefix in ("javax.servlet", "jakarta.servlet")},
    **{f"{prefix}.ServletRequestEvent": "request_event" for prefix in ("javax.servlet", "jakarta.servlet")},
    **{f"{prefix}.http.HttpSessionEvent": "session_event" for prefix in ("javax.servlet", "jakarta.servlet")},
}
_SERVLET_HANDLERS = {
    "doGet": "GET",
    "doPost": "POST",
    "doPut": "PUT",
    "doDelete": "DELETE",
    "doHead": "HEAD",
    "doOptions": "OPTIONS",
    "doTrace": "TRACE",
    "service": "ALL",
}
_LISTENER_CALLBACKS = {
    "contextInitialized",
    "contextDestroyed",
    "requestInitialized",
    "requestDestroyed",
    "sessionCreated",
    "sessionDestroyed",
    "sessionIdChanged",
    "attributeAdded",
    "attributeRemoved",
    "attributeReplaced",
}


@dataclass(frozen=True)
class JavaSemanticAnalysisResult:
    """Framework-only facts for one Java file; generic Java nodes are never copied."""

    file_path: str
    facts: Tuple[ServletJspFact, ...] = ()
    relationships: Tuple[ServletJspRelationship, ...] = ()
    diagnostics: Tuple[Diagnostic, ...] = ()
    coverage_status: str = "empty"
    missing_anchor_count: int = 0
    ambiguity_count: int = 0
    truncation_count: int = 0

    @property
    def semantic_facts(self) -> Tuple[ServletJspFact, ...]:
        return self.facts


@dataclass
class _TypeInfo:
    node: Any
    class_path: str
    fqcn: str
    name: str
    annotations: Tuple[Tuple[str, str, Any], ...]
    super_raw: Tuple[str, ...]
    super_resolved: Tuple[str, ...]
    roles: Set[str] = field(default_factory=set)
    evidence: Set[str] = field(default_factory=set)
    parent_paths: Tuple[str, ...] = ()


class _Diagnostics:
    def __init__(self, file_path: str, limit: int) -> None:
        self.file_path = file_path
        self.limit = max(0, limit)
        self.rows: List[Diagnostic] = []
        self.suppressed = 0

    def add(
        self,
        code: str,
        message: str,
        node: Any = None,
        *,
        severity: str = "warning",
        hint: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if len(self.rows) >= self.limit:
            self.suppressed += 1
            return
        start = node.start_point[0] + 1 if node is not None else 1
        end = node.end_point[0] + 1 if node is not None else start
        self.rows.append(
            Diagnostic(code, message, severity, self.file_path, start, end, hint, details or {})
        )

    def finish(self) -> Tuple[Diagnostic, ...]:
        if self.suppressed and self.limit:
            marker = Diagnostic(
                "servlet_jsp.java.diagnostic_budget",
                f"Suppressed {self.suppressed} diagnostics after reaching the per-file limit",
                "warning",
                self.file_path,
            )
            if len(self.rows) < self.limit:
                self.rows.append(marker)
            elif self.rows:
                self.rows[-1] = marker
        return tuple(sorted(self.rows, key=lambda item: (item.start_line, item.code, item.message)))


class _LiteralResolver:
    def __init__(self, definitions: Mapping[str, str], max_steps: int) -> None:
        self.definitions = dict(definitions)
        self.values: Dict[str, Any] = {}
        self.max_steps = max(0, max_steps)
        self.steps = 0
        self.truncated = False
        while True:
            progressed = False
            for name in sorted(self.definitions):
                if name in self.values:
                    continue
                value = self._evaluate(self.definitions[name], self.values)
                if value is not None:
                    self.values[name] = value
                    progressed = True
                if self.truncated:
                    return
            if not progressed:
                return

    def resolve(self, expression: str, extra: Optional[Mapping[str, Any]] = None) -> Any:
        values = dict(self.values)
        if extra:
            values.update(extra)
        return self._evaluate(expression, values)

    def _evaluate(self, expression: str, values: Mapping[str, Any]) -> Any:
        self.steps += 1
        if self.steps > self.max_steps:
            self.truncated = True
            return None
        text = _strip_parentheses((expression or "").strip())
        if not text:
            return None
        if _is_string_literal(text):
            return _decode_java_string(text)
        if text in {"true", "false"}:
            return text == "true"
        if re.fullmatch(r"-?\d+", text):
            return int(text)
        if re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", text):
            return values.get(text, values.get(text.rsplit(".", 1)[-1]))
        parts = _split_top_level(text, "+")
        if len(parts) > 1:
            resolved = [self._evaluate(part, values) for part in parts]
            if all(isinstance(item, (str, int, bool)) for item in resolved):
                return "".join(str(item).lower() if isinstance(item, bool) else str(item) for item in resolved)
        return None


def analyze_java_file(
    *,
    root: str,
    project_id: str,
    module_id: str,
    file_path: str = "",
    rel_path: str = "",
    project_name: str = "",
    budgets: Optional[ResourceBudgets] = None,
    identity_provider: Optional[JavaIdentityProvider] = None,
) -> JavaSemanticAnalysisResult:
    """Analyze one Java file and return deterministic Servlet/JSP overlay facts.

    ``file_path`` accepts either a project-relative or absolute path. ``rel_path``
    is retained as a keyword alias for overlay callers that already use that name.
    """

    effective_budgets = budgets or ResourceBudgets()
    project_root = os.path.realpath(os.path.abspath(root))
    requested = file_path or rel_path
    normalized, absolute = _normalize_input_path(project_root, requested)
    diagnostics = _Diagnostics(normalized or requested, effective_budgets.max_diagnostics_per_file)
    if not requested:
        diagnostics.add("servlet_jsp.java.missing_path", "A Java file path is required", severity="error")
        return JavaSemanticAnalysisResult("", diagnostics=diagnostics.finish(), coverage_status="unavailable")
    if not absolute or not _inside_root(project_root, absolute):
        diagnostics.add("servlet_jsp.java.outside_root", "Java source is outside the project root", severity="error")
        return JavaSemanticAnalysisResult(normalized, diagnostics=diagnostics.finish(), coverage_status="unavailable")
    if not os.path.isfile(absolute):
        diagnostics.add("servlet_jsp.java.missing_file", "Java source file is missing", severity="error")
        return JavaSemanticAnalysisResult(normalized, diagnostics=diagnostics.finish(), coverage_status="unavailable")
    size = os.path.getsize(absolute)
    if size > effective_budgets.max_source_bytes:
        diagnostics.add(
            "servlet_jsp.java.source_budget",
            f"Java source exceeds the {effective_budgets.max_source_bytes}-byte file budget",
            severity="error",
        )
        return JavaSemanticAnalysisResult(
            normalized,
            diagnostics=diagnostics.finish(),
            coverage_status="partial",
            truncation_count=1,
        )
    with open(absolute, "rb") as handle:
        source = handle.read()
    try:
        tree = parse_java_bytes(source)
    except Exception as exc:  # noqa: BLE001
        diagnostics.add("servlet_jsp.java.parser_unavailable", str(exc), severity="error")
        return JavaSemanticAnalysisResult(normalized, diagnostics=diagnostics.finish(), coverage_status="unavailable")

    error_nodes = [node for node in _walk(tree.root_node) if node.type == "ERROR" or getattr(node, "is_missing", False)]
    if tree.root_node.has_error:
        diagnostics.add(
            "servlet_jsp.java.parse_error",
            f"Tree-sitter recovered from {len(error_nodes) or 1} Java syntax error(s)",
            error_nodes[0] if error_nodes else tree.root_node,
            details={"error_count": len(error_nodes) or 1},
        )
    package_name = _package_name(tree.root_node, source)
    imports = _imports(tree.root_node, source)
    infos = _type_infos(tree.root_node, source, package_name, imports)
    _propagate_roles(infos)
    constants = _constant_definitions(infos, source)
    resolver = _LiteralResolver(constants, effective_budgets.max_constant_steps_per_file)
    if resolver.truncated:
        diagnostics.add(
            "servlet_jsp.java.constant_budget",
            "Static-final constant propagation reached its deterministic step limit",
        )

    identity: Optional[JavaIdentityIndex]
    try:
        identity = (identity_provider or JavaIdentityProvider(project_root)).index_file(absolute)
    except Exception as exc:  # noqa: BLE001
        identity = None
        diagnostics.add("servlet_jsp.java.identity_failed", str(exc), severity="error")

    facts: List[ServletJspFact] = []
    relationships: List[ServletJspRelationship] = []
    missing_anchors = 0
    ambiguities = 0
    method_anchor_cache: Dict[Tuple[str, int], Tuple[str, str]] = {}
    component_ids: Dict[Tuple[str, str], str] = {}

    for info in sorted(infos.values(), key=lambda item: (item.node.start_byte, item.class_path)):
        for role in sorted(info.roles):
            class_id = identity.class_id(info.fqcn) if identity else ""
            status = "resolved" if class_id else "unresolved"
            if not class_id:
                missing_anchors += 1
                diagnostics.add(
                    "servlet_jsp.java.class_anchor_missing",
                    f"No canonical Java Class ID found for {info.fqcn}",
                    info.node,
                )
            props, annotation_status = _component_properties(role, info, resolver, diagnostics)
            if annotation_status != "resolved" and status == "resolved":
                status = "partial"
            component_id = stable_semantic_id(role, project_id, module_id, info.fqcn)
            component_ids[(info.class_path, role)] = component_id
            fact = ServletJspFact(
                kind=role,
                stable_id=component_id,
                name=str(props.get("component_name") or info.name),
                source=_span(info.node, normalized),
                project_id=project_id,
                project_name=project_name,
                module_id=module_id,
                confidence=1.0 if class_id else 0.75,
                extraction_method="tree_sitter_java",
                resolution_status=status,
                source_symbol_id=class_id,
                properties=props,
            )
            facts.append(fact)
            if class_id:
                relationships.append(
                    _semantic_relationship(fact, "Class", class_id, project_id, module_id, "class", info.node)
                )

    def method_anchor(class_path: str, method_node: Any) -> Tuple[str, str]:
        key = (class_path, method_node.start_byte)
        if key not in method_anchor_cache:
            name = _node_text(method_node.child_by_field_name("name"), source)
            arity = _java_arity(method_node)
            method_anchor_cache[key] = (
                identity.method_id(class_path, name, arity, method_node.start_point[0] + 1)
                if identity
                else ("", "missing")
            )
        return method_anchor_cache[key]

    for info in sorted(infos.values(), key=lambda item: (item.node.start_byte, item.class_path)):
        for role in sorted(info.roles):
            component_id = component_ids[(info.class_path, role)]
            for declared, method_node, inherited in _component_methods(info, role, infos, source):
                name = _node_text(method_node.child_by_field_name("name"), source)
                callback = _callback_kind(role, name, method_node, source, imports)
                if callback is None:
                    continue
                method_id, anchor_status = method_anchor(declared.class_path, method_node)
                if not method_id:
                    if anchor_status == "ambiguous":
                        ambiguities += 1
                        code = "servlet_jsp.java.method_anchor_ambiguous"
                    else:
                        missing_anchors += 1
                        code = "servlet_jsp.java.method_anchor_missing"
                    diagnostics.add(code, f"Canonical Java Function anchor is {anchor_status} for {declared.class_path}.{name}", method_node)
                fact = _callback_fact(
                    role=role,
                    callback=callback,
                    component_id=component_id,
                    declaring=declared,
                    method_node=method_node,
                    method_id=method_id,
                    anchor_status=anchor_status,
                    inherited=inherited,
                    source=source,
                    file_path=normalized,
                    project_id=project_id,
                    project_name=project_name,
                    module_id=module_id,
                )
                facts.append(fact)
                if method_id:
                    relationships.append(
                        _semantic_relationship(fact, "Function", method_id, project_id, module_id, "method", method_node)
                    )

        for method_node in _direct_methods(info):
            method_id, anchor_status = method_anchor(info.class_path, method_node)
            if not method_id and anchor_status == "ambiguous":
                ambiguities += 1
            op_facts, op_relationships = _extract_method_operations(
                info=info,
                method_node=method_node,
                method_id=method_id,
                anchor_status=anchor_status,
                source=source,
                imports=imports,
                resolver=resolver,
                file_path=normalized,
                project_id=project_id,
                project_name=project_name,
                module_id=module_id,
                diagnostics=diagnostics,
            )
            facts.extend(op_facts)
            relationships.extend(op_relationships)

    diagnostic_rows = diagnostics.finish()
    coverage = "empty" if not facts else "partial" if tree.root_node.has_error or resolver.truncated or missing_anchors else "complete"
    return JavaSemanticAnalysisResult(
        normalized,
        tuple(sorted(facts, key=lambda item: item.stable_id)),
        tuple(sorted(relationships, key=lambda item: item.stable_id)),
        diagnostic_rows,
        coverage,
        missing_anchors,
        ambiguities,
        int(resolver.truncated),
    )


def _type_infos(root: Any, source: bytes, package_name: str, imports: Sequence[str]) -> Dict[str, _TypeInfo]:
    infos: Dict[str, _TypeInfo] = {}
    for node, class_path in _iter_type_declarations(root, source):
        name = class_path.rsplit(".", 1)[-1]
        fqcn = f"{package_name}.{class_path}" if package_name else class_path
        annotations = tuple(_annotations(node, source, imports))
        raw_supers = tuple(_super_types(node, source))
        resolved = tuple(_resolve_type(item, imports) for item in raw_supers)
        info = _TypeInfo(node, class_path, fqcn, name, annotations, raw_supers, resolved)
        for annotation_name, _, _ in annotations:
            role = _WEB_ANNOTATIONS.get(annotation_name)
            if role:
                info.roles.add(role)
                info.evidence.add(f"annotation:{annotation_name}")
        for type_name in resolved:
            if type_name in _SERVLET_TYPES:
                info.roles.add("Servlet")
                info.evidence.add(f"inherits:{type_name}")
            if type_name in _FILTER_TYPES:
                info.roles.add("Filter")
                info.evidence.add(f"inherits:{type_name}")
            if type_name in _LISTENER_TYPES:
                info.roles.add("Listener")
                info.evidence.add(f"inherits:{type_name}")
        infos[class_path] = info
    local_names: Dict[str, List[str]] = {}
    for path, info in infos.items():
        local_names.setdefault(info.name, []).append(path)
        local_names.setdefault(info.fqcn, []).append(path)
    for info in infos.values():
        parents: List[str] = []
        for raw, resolved in zip(info.super_raw, info.super_resolved):
            candidates = local_names.get(resolved, ()) or local_names.get(_erase_type(raw).rsplit(".", 1)[-1], ())
            if len(candidates) == 1:
                parents.append(candidates[0])
        info.parent_paths = tuple(parents)
    return infos


def _propagate_roles(infos: Mapping[str, _TypeInfo]) -> None:
    for _ in range(len(infos) + 1):
        changed = False
        for info in infos.values():
            for parent_path in info.parent_paths:
                parent = infos.get(parent_path)
                if parent is None:
                    continue
                before = len(info.roles)
                info.roles.update(parent.roles)
                if len(info.roles) != before:
                    info.evidence.add(f"project_inheritance:{parent.fqcn}")
                    changed = True
        if not changed:
            return


def _component_properties(
    role: str,
    info: _TypeInfo,
    resolver: _LiteralResolver,
    diagnostics: _Diagnostics,
) -> Tuple[Dict[str, Any], str]:
    props: Dict[str, Any] = {
        "fqcn": info.fqcn,
        "class_path": info.class_path,
        "component_name": info.name,
        "url_patterns": [],
        "raw_url_patterns": [],
        "init_params": [],
        "async_supported": None,
        "super_types": list(info.super_resolved),
        "evidence": sorted(info.evidence),
    }
    status = "resolved"
    wanted = f"Web{role}"
    annotations = [item for item in info.annotations if item[0].rsplit(".", 1)[-1] == wanted]
    if not annotations:
        return props, status
    annotation_name, raw_args, annotation_node = annotations[0]
    props["annotation"] = annotation_name
    props["raw_annotation_arguments"] = raw_args
    pairs = _annotation_pairs(raw_args)
    for key, expression in pairs:
        if key == "name":
            value = resolver.resolve(expression)
            if isinstance(value, str):
                props["component_name"] = value
            else:
                props["raw_component_name"] = expression
                status = "partial"
                _dynamic_diagnostic(diagnostics, "component name", expression, annotation_node)
        elif key in {"value", "urlPatterns"} and role in {"Servlet", "Filter"}:
            for raw in _array_items(expression):
                value = resolver.resolve(raw)
                if isinstance(value, str):
                    if value not in props["url_patterns"]:
                        props["url_patterns"].append(value)
                else:
                    props["raw_url_patterns"].append(raw)
                    status = "partial"
                    _dynamic_diagnostic(diagnostics, "URL pattern", raw, annotation_node)
        elif key == "asyncSupported":
            value = resolver.resolve(expression)
            if isinstance(value, bool):
                props["async_supported"] = value
            else:
                props["raw_async_supported"] = expression
                status = "partial"
        elif key == "initParams":
            for nested_args in _nested_annotation_arguments(expression, "WebInitParam"):
                values = dict(_annotation_pairs(nested_args))
                name_raw = values.get("name", "")
                value_raw = values.get("value", "")
                name = resolver.resolve(name_raw)
                value = resolver.resolve(value_raw)
                row = {
                    "name": name if isinstance(name, str) else "",
                    "value": value if isinstance(value, str) else "",
                    "raw_name": name_raw,
                    "raw_value": value_raw,
                    "description": resolver.resolve(values.get("description", "")) or "",
                    "resolution_status": "resolved" if isinstance(name, str) and isinstance(value, str) else "unresolved",
                }
                props["init_params"].append(row)
                if row["resolution_status"] != "resolved":
                    status = "partial"
                    _dynamic_diagnostic(diagnostics, "init parameter", f"{name_raw}={value_raw}", annotation_node)
        elif key == "servletNames" and role == "Filter":
            props["servlet_names"] = [resolver.resolve(item) or "" for item in _array_items(expression)]
            props["raw_servlet_names"] = _array_items(expression)
        elif key == "dispatcherTypes" and role == "Filter":
            props["dispatcher_types"] = [item.rsplit(".", 1)[-1].strip() for item in _array_items(expression)]
    for name, raw, _ in info.annotations:
        simple = name.rsplit(".", 1)[-1]
        if simple in {"MultipartConfig", "ServletSecurity", "DeclareRoles", "RunAs"}:
            props.setdefault("supplemental_annotations", []).append({"name": name, "arguments": raw})
    return props, status


def _callback_kind(
    role: str,
    method_name: str,
    method_node: Any,
    source: bytes,
    imports: Sequence[str],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    parameter_types = [_resolve_type(item, imports) for item in _parameter_types(method_node, source)]
    if (
        role == "Servlet"
        and method_name in _SERVLET_HANDLERS
        and len(parameter_types) == 2
        and parameter_types[0] in _REQUEST_TYPES
        and parameter_types[1] in _RESPONSE_TYPES
    ):
        return "ServletHandler", {"http_method": _SERVLET_HANDLERS[method_name], "callback_kind": "handler"}
    if role == "Servlet" and method_name == "init" and (
        not parameter_types or (len(parameter_types) == 1 and parameter_types[0] in _CONFIG_TYPES)
    ):
        return "ServletLifecycle", {"lifecycle_event": method_name, "callback_kind": "lifecycle"}
    if role == "Servlet" and method_name == "destroy" and not parameter_types:
        return "ServletLifecycle", {"lifecycle_event": method_name, "callback_kind": "lifecycle"}
    if (
        role == "Filter"
        and method_name == "doFilter"
        and len(parameter_types) == 3
        and parameter_types[0] in _REQUEST_TYPES
        and parameter_types[1] in _RESPONSE_TYPES
        and parameter_types[2] in _FILTER_CHAIN_TYPES
    ):
        return "FilterCallback", {"callback_kind": "filter_chain"}
    if role == "Filter" and method_name == "init" and len(parameter_types) == 1 and parameter_types[0] in _CONFIG_TYPES:
        return "FilterLifecycle", {"lifecycle_event": method_name, "callback_kind": "lifecycle"}
    if role == "Filter" and method_name == "destroy" and not parameter_types:
        return "FilterLifecycle", {"lifecycle_event": method_name, "callback_kind": "lifecycle"}
    expected_arity = 2 if method_name == "sessionIdChanged" else 1
    if role == "Listener" and method_name in _LISTENER_CALLBACKS and len(parameter_types) == expected_arity:
        return "ListenerCallback", {"lifecycle_event": method_name, "callback_kind": "listener"}
    return None


def _callback_fact(
    *,
    role: str,
    callback: Tuple[str, Dict[str, Any]],
    component_id: str,
    declaring: _TypeInfo,
    method_node: Any,
    method_id: str,
    anchor_status: str,
    inherited: bool,
    source: bytes,
    file_path: str,
    project_id: str,
    project_name: str,
    module_id: str,
) -> ServletJspFact:
    kind, callback_props = callback
    name = _node_text(method_node.child_by_field_name("name"), source)
    anchor = method_id or f"{file_path}:{declaring.class_path}:{method_node.start_point[0] + 1}:{name}"
    properties = {
        **callback_props,
        "component_id": component_id,
        "component_kind": role,
        "method_name": name,
        "declaring_class": declaring.fqcn,
        "parameter_types": _parameter_types(method_node, source),
        "inherited": inherited,
        "anchor_status": anchor_status,
    }
    return ServletJspFact(
        kind=kind,
        stable_id=stable_semantic_id(kind, project_id, module_id, component_id, anchor),
        name=name,
        source=_span(method_node, file_path),
        project_id=project_id,
        project_name=project_name,
        module_id=module_id,
        confidence=1.0 if method_id else 0.65,
        extraction_method="tree_sitter_java",
        resolution_status="resolved" if method_id else anchor_status,
        source_symbol_id=method_id,
        properties=properties,
    )


def _extract_method_operations(
    *,
    info: _TypeInfo,
    method_node: Any,
    method_id: str,
    anchor_status: str,
    source: bytes,
    imports: Sequence[str],
    resolver: _LiteralResolver,
    file_path: str,
    project_id: str,
    project_name: str,
    module_id: str,
    diagnostics: _Diagnostics,
) -> Tuple[List[ServletJspFact], List[ServletJspRelationship]]:
    facts: List[ServletJspFact] = []
    relationships: List[ServletJspRelationship] = []
    roles: Dict[str, str] = {}
    for parameter in _parameter_nodes(method_node):
        name = _node_text(parameter.child_by_field_name("name"), source)
        role = _role_for_type(_resolve_type(_node_text(parameter.child_by_field_name("type"), source), imports))
        if name and role:
            roles[name] = role
    dispatcher_targets: Dict[str, Tuple[str, str]] = {}
    local_constants: Dict[str, Any] = {}
    body = method_node.child_by_field_name("body")
    for node in _walk(body) if body is not None else ():
        if node.type != "local_variable_declaration":
            continue
        type_name = _resolve_type(_node_text(node.child_by_field_name("type"), source), imports)
        declared_role = _role_for_type(type_name)
        is_final = "final" in _node_text(node, source).split()
        for declarator in [child for child in node.named_children if child.type == "variable_declarator"]:
            name = _node_text(declarator.child_by_field_name("name"), source)
            value_node = declarator.child_by_field_name("value")
            if name and declared_role:
                roles[name] = declared_role
            inferred = _expression_role(value_node, roles, source, bool(info.roles)) if value_node is not None else ""
            if name and inferred:
                roles[name] = inferred
            if name and is_final and value_node is not None:
                value = resolver.resolve(_node_text(value_node, source), local_constants)
                if value is not None:
                    local_constants[name] = value
            if name and value_node is not None and value_node.type == "method_invocation":
                target = _dispatcher_lookup(value_node, roles, dispatcher_targets, source, bool(info.roles))
                if target is not None:
                    dispatcher_targets[name] = target

    method_name = _node_text(method_node.child_by_field_name("name"), source)

    def emit(
        kind: str,
        display: str,
        node: Any,
        *,
        raw: str = "",
        resolved: str = "",
        value_status: str = "resolved",
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        status = value_status if method_id else ("ambiguous" if anchor_status == "ambiguous" else "unresolved")
        anchor = method_id or f"{file_path}:{info.class_path}:{method_node.start_point[0] + 1}:{method_name}"
        fact = ServletJspFact(
            kind=kind,
            stable_id=stable_semantic_id(kind, project_id, module_id, anchor, file_path, node.start_byte, display),
            name=display,
            source=_span(node, file_path),
            project_id=project_id,
            project_name=project_name,
            module_id=module_id,
            confidence=1.0 if method_id else 0.65,
            extraction_method="tree_sitter_java",
            resolution_status=status,
            raw_value=raw,
            resolved_value=resolved,
            source_symbol_id=method_id,
            properties={
                "declaring_class": info.fqcn,
                "method_name": method_name,
                "anchor_status": anchor_status,
                **(properties or {}),
            },
        )
        facts.append(fact)
        if method_id:
            relationships.append(_semantic_relationship(fact, "Function", method_id, project_id, module_id, "operation", node))

    for invocation in [node for node in _walk(body) if node.type == "method_invocation"] if body is not None else ():
        name = _node_text(invocation.child_by_field_name("name"), source)
        receiver = invocation.child_by_field_name("object")
        receiver_role = _expression_role(receiver, roles, source, bool(info.roles))
        args = _invocation_args(invocation)
        if name in {"addServlet", "addFilter", "addListener"} and receiver_role == "context":
            diagnostics.add(
                "servlet_jsp.java.dynamic_registration",
                f"Programmatic {name} registration is preserved as unsupported dynamic evidence",
                invocation,
            )
            continue
        if name in {"forward", "include"}:
            target = _dispatcher_lookup(receiver, roles, dispatcher_targets, source, bool(info.roles))
            if target is None:
                continue
            raw, resolved = target
            if raw and not resolved:
                resolved_value = resolver.resolve(raw, local_constants)
                resolved = resolved_value if isinstance(resolved_value, str) else ""
            status = "resolved" if resolved else "unresolved"
            if status == "unresolved":
                _dynamic_diagnostic(diagnostics, f"dispatcher {name} target", raw or _node_text(receiver, source), invocation)
            emit(
                "DispatchOperation",
                name,
                invocation,
                raw=raw,
                resolved=resolved,
                value_status=status,
                properties={"operation": name, "target": resolved, "raw_target": raw},
            )
            continue
        if name == "sendRedirect" and receiver_role == "response":
            raw = _node_text(args[0], source) if args else ""
            value = resolver.resolve(raw, local_constants)
            resolved = value if isinstance(value, str) else ""
            status = "resolved" if resolved else "unresolved"
            if status == "unresolved":
                _dynamic_diagnostic(diagnostics, "redirect target", raw, invocation)
            emit(
                "RedirectOperation",
                "sendRedirect",
                invocation,
                raw=raw,
                resolved=resolved,
                value_status=status,
                properties={"operation": "redirect", "target": resolved, "raw_target": raw},
            )
            continue
        if name in {"getParameter", "getParameterValues"} and receiver_role == "request":
            _emit_state_access(emit, diagnostics, resolver, local_constants, invocation, args, source, "parameter", "read", name)
            continue
        if name in {"getAttribute", "setAttribute", "removeAttribute"} and receiver_role in {"request", "session", "context"}:
            access = "read" if name == "getAttribute" else "write"
            _emit_state_access(emit, diagnostics, resolver, local_constants, invocation, args, source, receiver_role, access, name)
            continue
        if name == "getCookies" and receiver_role == "request":
            emit(
                "CookieAccess",
                "getCookies",
                invocation,
                resolved="*",
                properties={"scope": "cookie", "access": "read", "operation": name, "key": "*", "enumeration": True},
            )
            continue
        if name == "addCookie" and receiver_role == "response":
            raw, resolved = _cookie_name(args[0] if args else None, source, resolver, local_constants)
            status = "resolved" if resolved else "unresolved"
            if status == "unresolved":
                _dynamic_diagnostic(diagnostics, "cookie name", raw, invocation)
            emit(
                "CookieAccess",
                "addCookie",
                invocation,
                raw=raw,
                resolved=resolved,
                value_status=status,
                properties={"scope": "cookie", "access": "write", "operation": name, "key": resolved, "raw_key": raw},
            )
            continue
        if name in {"write", "print", "println", "printf", "append"} and receiver_role == "response_writer":
            raw = _node_text(args[0], source) if args else ""
            emit(
                "ResponseWrite",
                name,
                invocation,
                raw=raw,
                properties={"operation": name, "payload_expression": raw},
            )
            continue
        if name in {"sendError", "setStatus", "setContentType", "setHeader", "addHeader"} and receiver_role == "response":
            emit(
                "ResponseWrite",
                name,
                invocation,
                raw=", ".join(_node_text(arg, source) for arg in args),
                properties={"operation": name, "response_metadata": True},
            )
    return facts, relationships


def _emit_state_access(
    emit: Any,
    diagnostics: _Diagnostics,
    resolver: _LiteralResolver,
    local_constants: Mapping[str, Any],
    invocation: Any,
    args: Sequence[Any],
    source: bytes,
    scope: str,
    access: str,
    operation: str,
) -> None:
    raw = _node_text(args[0], source) if args else ""
    value = resolver.resolve(raw, local_constants)
    resolved = value if isinstance(value, str) else ""
    status = "resolved" if resolved else "unresolved"
    if status == "unresolved":
        _dynamic_diagnostic(diagnostics, f"{scope} state key", raw, invocation)
    emit(
        "StateAccess",
        f"{scope}:{operation}",
        invocation,
        raw=raw,
        resolved=resolved,
        value_status=status,
        properties={
            "scope": scope,
            "access": access,
            "operation": operation,
            "key": resolved,
            "raw_key": raw,
            "value_expression": _node_text(args[1], source) if access == "write" and len(args) > 1 else "",
        },
    )


def _component_methods(
    info: _TypeInfo,
    role: str,
    infos: Mapping[str, _TypeInfo],
    source: bytes,
) -> Iterable[Tuple[_TypeInfo, Any, bool]]:
    seen_signatures: Set[Tuple[str, Tuple[str, ...]]] = set()
    queue: List[Tuple[_TypeInfo, bool]] = [(info, False)]
    visited: Set[str] = set()
    while queue:
        current, inherited = queue.pop(0)
        if current.class_path in visited:
            continue
        visited.add(current.class_path)
        for method in _direct_methods(current):
            name = _node_text(method.child_by_field_name("name"), source)
            signature = (name, tuple(_parameter_types(method, source)))
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                yield current, method, inherited
        for parent_path in current.parent_paths:
            parent = infos.get(parent_path)
            if parent is not None and role in parent.roles:
                queue.append((parent, True))


def _semantic_relationship(
    fact: ServletJspFact,
    target_label: str,
    target_id: str,
    project_id: str,
    module_id: str,
    role: str,
    node: Any,
) -> ServletJspRelationship:
    return ServletJspRelationship(
        stable_id=stable_semantic_id("relationship", project_id, module_id, "SEMANTIC_OF", fact.stable_id, target_id, role),
        from_id=fact.stable_id,
        to_id=target_id,
        from_label=fact.kind,
        to_label=target_label,
        type="SEMANTIC_OF",
        project_id=project_id,
        module_id=module_id,
        source=fact.source,
        reason=f"Servlet/JSP {role} semantic anchor",
        to_generated=False,
    )


def _constant_definitions(infos: Mapping[str, _TypeInfo], source: bytes) -> Dict[str, str]:
    definitions: Dict[str, str] = {}
    simple: Dict[str, List[str]] = {}
    for info in infos.values():
        body = info.node.child_by_field_name("body")
        for field_node in body.named_children if body is not None else ():
            if field_node.type != "field_declaration":
                continue
            modifiers = _node_text(next((child for child in field_node.children if child.type == "modifiers"), None), source)
            if "static" not in modifiers.split() or "final" not in modifiers.split():
                continue
            for declarator in [child for child in field_node.named_children if child.type == "variable_declarator"]:
                name = _node_text(declarator.child_by_field_name("name"), source)
                value = _node_text(declarator.child_by_field_name("value"), source)
                if not name or not value:
                    continue
                for key in (f"{info.class_path}.{name}", f"{info.fqcn}.{name}"):
                    definitions[key] = value
                simple.setdefault(name, []).append(value)
    for name, values in simple.items():
        if len(set(values)) == 1:
            definitions[name] = values[0]
    return definitions


def _dispatcher_lookup(
    expression: Any,
    roles: Mapping[str, str],
    dispatcher_targets: Mapping[str, Tuple[str, str]],
    source: bytes,
    component_context: bool,
) -> Optional[Tuple[str, str]]:
    if expression is None:
        return None
    text = _node_text(expression, source)
    if expression.type == "identifier":
        if text in dispatcher_targets:
            return dispatcher_targets[text]
        return ("", "") if roles.get(text) == "dispatcher" else None
    if expression.type != "method_invocation":
        return None
    name = _node_text(expression.child_by_field_name("name"), source)
    if name not in {"getRequestDispatcher", "getNamedDispatcher"}:
        return None
    receiver = expression.child_by_field_name("object")
    role = _expression_role(receiver, roles, source, component_context)
    if role not in {"request", "context"} and not (receiver is None and component_context):
        return None
    args = _invocation_args(expression)
    return (_node_text(args[0], source), "") if args else ("", "")


def _expression_role(expression: Any, roles: Mapping[str, str], source: bytes, component_context: bool) -> str:
    if expression is None:
        return ""
    text = _node_text(expression, source)
    if expression.type == "identifier":
        return roles.get(text, "")
    if expression.type == "field_access":
        return roles.get(text.rsplit(".", 1)[-1], "")
    if expression.type in {"parenthesized_expression", "cast_expression"}:
        for child in reversed(expression.named_children):
            role = _expression_role(child, roles, source, component_context)
            if role:
                return role
        return ""
    if expression.type != "method_invocation":
        return ""
    name = _node_text(expression.child_by_field_name("name"), source)
    receiver = expression.child_by_field_name("object")
    receiver_role = _expression_role(receiver, roles, source, component_context)
    if name == "getSession" and receiver_role in {"request", "session_event"}:
        return "session"
    if name in {"getServletContext", "getContext"} and (receiver_role in {"request", "config", "context_event"} or component_context):
        return "context"
    if name == "getServletRequest" and receiver_role == "request_event":
        return "request"
    if name in {"getWriter", "getOutputStream"} and receiver_role == "response":
        return "response_writer"
    if name in {"getRequestDispatcher", "getNamedDispatcher"} and (receiver_role in {"request", "context"} or (receiver is None and component_context)):
        return "dispatcher"
    return ""


def _role_for_type(type_name: str) -> str:
    if type_name in _REQUEST_TYPES:
        return "request"
    if type_name in _RESPONSE_TYPES:
        return "response"
    if type_name in _SESSION_TYPES:
        return "session"
    if type_name in _CONTEXT_TYPES:
        return "context"
    if type_name in _DISPATCHER_TYPES:
        return "dispatcher"
    if type_name in _COOKIE_TYPES:
        return "cookie"
    if type_name in _CONFIG_TYPES:
        return "config"
    return _EVENT_ROLES.get(type_name, "")


def _cookie_name(
    argument: Any,
    source: bytes,
    resolver: _LiteralResolver,
    local_constants: Mapping[str, Any],
) -> Tuple[str, str]:
    if argument is None:
        return "", ""
    if argument.type == "object_creation_expression":
        type_name = _node_text(argument.child_by_field_name("type"), source).rsplit(".", 1)[-1]
        args = argument.child_by_field_name("arguments")
        values = list(args.named_children) if args is not None else []
        if type_name == "Cookie" and values:
            raw = _node_text(values[0], source)
            resolved = resolver.resolve(raw, local_constants)
            return raw, resolved if isinstance(resolved, str) else ""
    return _node_text(argument, source), ""


def _dynamic_diagnostic(diagnostics: _Diagnostics, description: str, raw: str, node: Any) -> None:
    diagnostics.add(
        "servlet_jsp.java.dynamic_value",
        f"Unresolved dynamic {description}: {(raw or '<missing>')[:200]}",
        node,
        details={"semantic_role": description, "raw_expression": (raw or "")[:1000]},
    )


def _annotations(node: Any, source: bytes, imports: Sequence[str]) -> Iterable[Tuple[str, str, Any]]:
    modifiers = next((child for child in node.children if child.type == "modifiers"), None)
    for child in modifiers.named_children if modifiers is not None else ():
        if child.type not in {"annotation", "marker_annotation"}:
            continue
        name = _node_text(child.child_by_field_name("name"), source)
        arguments = child.child_by_field_name("arguments")
        yield _resolve_annotation(name, imports), _node_text(arguments, source), child


def _annotation_pairs(raw_arguments: str) -> List[Tuple[str, str]]:
    text = raw_arguments.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    rows: List[Tuple[str, str]] = []
    for part in _split_top_level(text, ","):
        key, value = _split_assignment(part)
        rows.append((key or "value", value.strip()))
    return rows


def _nested_annotation_arguments(expression: str, simple_name: str) -> List[str]:
    rows: List[str] = []
    pattern = re.compile(r"@(?:[A-Za-z_$][\w$]*\.)*" + re.escape(simple_name) + r"\s*\(")
    for match in pattern.finditer(expression):
        start = match.end() - 1
        end = _matching_delimiter(expression, start, "(", ")")
        if end >= 0:
            rows.append(expression[start : end + 1])
    return rows


def _array_items(expression: str) -> List[str]:
    text = expression.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return [item.strip() for item in _split_top_level(text, ",") if item.strip()]


def _split_assignment(text: str) -> Tuple[str, str]:
    parts = _split_top_level(text, "=")
    return (parts[0].strip(), "=".join(parts[1:]).strip()) if len(parts) > 1 else ("", text)


def _split_top_level(text: str, delimiter: str) -> List[str]:
    rows: List[str] = []
    start = 0
    stack: List[str] = []
    quote = ""
    escaped = False
    matching = {"(": ")", "[": "]", "{": "}", "<": ">"}
    closers = set(matching.values())
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char in matching:
            stack.append(matching[char])
            continue
        if char in closers:
            if stack and char == stack[-1]:
                stack.pop()
            continue
        if char == delimiter and not stack:
            rows.append(text[start:index])
            start = index + 1
    rows.append(text[start:])
    return rows


def _matching_delimiter(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _resolve_annotation(name: str, imports: Sequence[str]) -> str:
    if name in _WEB_ANNOTATIONS or name.startswith(("javax.servlet.annotation.", "jakarta.servlet.annotation.")):
        return name
    direct = [item for item in imports if item.endswith(f".{name}")]
    if len(direct) == 1:
        return direct[0]
    for prefix in ("javax.servlet.annotation", "jakarta.servlet.annotation"):
        if f"{prefix}.*" in imports:
            return f"{prefix}.{name}"
    return name


def _resolve_type(raw: str, imports: Sequence[str]) -> str:
    name = _erase_type(raw)
    if not name:
        return ""
    if name.startswith(("javax.servlet.", "jakarta.servlet.")):
        return name
    simple = name.rsplit(".", 1)[-1]
    direct = [item for item in imports if not item.endswith(".*") and item.endswith(f".{simple}")]
    if len(direct) == 1:
        return direct[0]
    known = _SERVLET_TYPES | _FILTER_TYPES | _LISTENER_TYPES | _REQUEST_TYPES | _RESPONSE_TYPES | _SESSION_TYPES | _CONTEXT_TYPES | _DISPATCHER_TYPES | _FILTER_CHAIN_TYPES | _COOKIE_TYPES | _CONFIG_TYPES | set(_EVENT_ROLES)
    candidates = [f"{item[:-2]}.{simple}" for item in imports if item.endswith(".*") and f"{item[:-2]}.{simple}" in known]
    return candidates[0] if len(candidates) == 1 else name


def _erase_type(raw: str) -> str:
    text = re.sub(r"<.*>", "", raw or "")
    return text.replace("[]", "").replace("...", "").strip().removeprefix("extends ").removeprefix("implements ").strip()


def _super_types(node: Any, source: bytes) -> List[str]:
    rows: List[str] = []
    for child in node.named_children:
        if child.type not in {"superclass", "super_interfaces", "extends_interfaces"}:
            continue
        text = _node_text(child, source)
        text = re.sub(r"^(?:extends|implements)\s+", "", text)
        rows.extend(item.strip() for item in _split_top_level(text, ",") if item.strip())
    return rows


def _iter_type_declarations(node: Any, source: bytes, stack: Tuple[str, ...] = ()) -> Iterable[Tuple[Any, str]]:
    if node.type in {"class_declaration", "interface_declaration"}:
        name = _node_text(node.child_by_field_name("name"), source)
        path = stack + ((name,) if name else ())
        if node.type == "class_declaration" and name:
            yield node, ".".join(path)
        body = node.child_by_field_name("body")
        for child in body.named_children if body is not None else ():
            yield from _iter_type_declarations(child, source, path)
        return
    for child in node.named_children:
        yield from _iter_type_declarations(child, source, stack)


def _direct_methods(info: _TypeInfo) -> List[Any]:
    body = info.node.child_by_field_name("body")
    return [child for child in body.named_children if child.type == "method_declaration"] if body is not None else []


def _parameter_nodes(method_node: Any) -> List[Any]:
    parameters = method_node.child_by_field_name("parameters")
    return [child for child in parameters.named_children if child.type in {"formal_parameter", "spread_parameter"}] if parameters is not None else []


def _parameter_types(method_node: Any, source: bytes) -> List[str]:
    return [_node_text(item.child_by_field_name("type"), source) for item in _parameter_nodes(method_node)]


def _java_arity(method_node: Any) -> int:
    return sum(1 for item in _parameter_nodes(method_node) if item.type == "formal_parameter")


def _invocation_args(invocation: Any) -> List[Any]:
    arguments = invocation.child_by_field_name("arguments")
    return list(arguments.named_children) if arguments is not None else []


def _package_name(root: Any, source: bytes) -> str:
    for child in root.named_children:
        if child.type == "package_declaration":
            return _node_text(child, source).removeprefix("package").rstrip(";").strip()
    return ""


def _imports(root: Any, source: bytes) -> Tuple[str, ...]:
    rows: List[str] = []
    for child in root.named_children:
        if child.type == "import_declaration":
            text = _node_text(child, source)
            text = re.sub(r"^import\s+(?:static\s+)?", "", text).rstrip(";").strip()
            rows.append(text)
    return tuple(rows)


def _walk(node: Any) -> Iterable[Any]:
    if node is None:
        return
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _node_text(node: Any, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()


def _span(node: Any, file_path: str) -> SourceSpan:
    return SourceSpan(
        file_path,
        node.start_point[0] + 1,
        node.end_point[0] + 1,
        node.start_point[1] + 1,
        node.end_point[1] + 1,
    )


def _normalize_input_path(root: str, path: str) -> Tuple[str, str]:
    if not path:
        return "", ""
    absolute = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    try:
        relative = os.path.relpath(absolute, root).replace("\\", "/")
    except ValueError:
        relative = path.replace("\\", "/")
    return relative, absolute


def _inside_root(root: str, path: str) -> bool:
    try:
        return os.path.commonpath((root, path)) == root
    except ValueError:
        return False


def _is_string_literal(text: str) -> bool:
    return len(text) >= 2 and text[0] == text[-1] == '"'


def _decode_java_string(text: str) -> Optional[str]:
    try:
        value = ast.literal_eval(text)
        return value if isinstance(value, str) else None
    except (SyntaxError, ValueError):
        return None


def _strip_parentheses(text: str) -> str:
    while text.startswith("(") and text.endswith(")") and _matching_delimiter(text, 0, "(", ")") == len(text) - 1:
        text = text[1:-1].strip()
    return text

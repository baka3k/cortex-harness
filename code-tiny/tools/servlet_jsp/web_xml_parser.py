from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from tools.servlet_jsp.models import (
    Diagnostic,
    ResourceBudgets,
    SENSITIVE_KEY_RE,
    ServletJspFact,
    ServletJspRelationship,
    SourceSpan,
    stable_digest,
    stable_semantic_id,
)
from tools.servlet_jsp.parser_runtime import parse_xml_bytes
from tools.servlet_jsp.path_resolver import normalize_relative_path, read_bounded_file, resolve_project_path


_SUPPORTED_VERSIONS = {"2.3", "2.4", "2.5", "3.0", "3.1", "4.0", "5.0", "6.0", "6.1"}
_KNOWN_NAMESPACES = {
    "",
    "http://java.sun.com/xml/ns/j2ee",
    "http://java.sun.com/xml/ns/javaee",
    "http://xmlns.jcp.org/xml/ns/javaee",
    "https://jakarta.ee/xml/ns/jakartaee",
}
_DOCTYPE_VERSION_RE = re.compile(
    r"(?:web-app[_-](?P<major>\d+)[_-](?P<minor>\d+)|Web\s+Application\s+(?P<version>\d+\.\d+))",
    re.IGNORECASE,
)
_ENTITY_DECL_RE = re.compile(r"<!ENTITY\b", re.IGNORECASE)
_ENTITY_RE = re.compile(r"&(?:#(?P<decimal>\d+)|#x(?P<hex>[0-9a-fA-F]+)|(?P<name>[A-Za-z_:][\w:.-]*));")
_XML_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "apos": "'", "quot": '"'}
_RAW_TEXT_LIMIT = 4096


# Known Servlet descriptor names are retained without noisy warnings even when
# Phase 03 does not assign them first-class semantics yet. Truly vendor-specific
# names remain visible through ``unknown_elements`` and bounded diagnostics.
_KNOWN_ELEMENT_NAMES = {
    "absolute-ordering",
    "async-supported",
    "auth-constraint",
    "auth-method",
    "comment",
    "connection-factory-resource",
    "context-param",
    "cookie-config",
    "deny-uncovered-http-methods",
    "description",
    "data-source",
    "default-content-type",
    "deferred-syntax-allowed-as-literal",
    "dispatcher",
    "display-name",
    "distributable",
    "domain",
    "ejb-local-ref",
    "ejb-ref",
    "enabled",
    "encoding",
    "env-entry",
    "error-code",
    "error-page",
    "exception-type",
    "extension",
    "file-size-threshold",
    "filter",
    "filter-class",
    "filter-mapping",
    "filter-name",
    "form-error-page",
    "form-login-config",
    "form-login-page",
    "http-method",
    "http-method-omission",
    "http-only",
    "icon",
    "include-coda",
    "include-prelude",
    "injection-target",
    "injection-target-class",
    "injection-target-name",
    "init-param",
    "jsp-config",
    "jsp-file",
    "jsp-property-group",
    "large-icon",
    "listener",
    "listener-class",
    "load-on-startup",
    "locale-encoding-mapping-list",
    "locale-encoding-mapping",
    "locale",
    "location",
    "login-config",
    "max-age",
    "max-file-size",
    "max-request-size",
    "message-destination",
    "message-destination-ref",
    "mime-type",
    "mime-mapping",
    "multipart-config",
    "name",
    "ordering",
    "param-name",
    "param-value",
    "path",
    "persistence-context-ref",
    "persistence-unit-ref",
    "post-construct",
    "pre-destroy",
    "realm-name",
    "request-character-encoding",
    "resource-env-ref",
    "resource-ref",
    "response-character-encoding",
    "role-link",
    "role-name",
    "run-as",
    "security-constraint",
    "security-role",
    "security-role-ref",
    "secure",
    "service-ref",
    "servlet",
    "servlet-class",
    "servlet-mapping",
    "servlet-name",
    "session-config",
    "session-timeout",
    "scripting-invalid",
    "small-icon",
    "taglib",
    "taglib-location",
    "taglib-uri",
    "tracking-mode",
    "trim-directive-whitespaces",
    "transport-guarantee",
    "url-pattern",
    "user-data-constraint",
    "web-app",
    "web-resource-collection",
    "web-resource-name",
    "welcome-file",
    "welcome-file-list",
}


@dataclass(frozen=True)
class WebXmlRecord:
    """Lossless-enough descriptor record consumed by the later resolver."""

    stable_id: str
    kind: str
    name: str
    order: int
    source: SourceSpan
    raw_text: str
    values: Dict[str, Any] = field(default_factory=dict)
    children: Tuple["WebXmlRecord", ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@dataclass(frozen=True)
class WebXmlDescriptorData:
    stable_id: str
    file_path: str
    module_path: str
    namespace: str
    version: str
    metadata_complete: Optional[bool]
    metadata_complete_raw: str
    doctype: str
    source: SourceSpan
    root_attributes: Dict[str, str] = field(default_factory=dict)
    servlets: Tuple[WebXmlRecord, ...] = ()
    servlet_mappings: Tuple[WebXmlRecord, ...] = ()
    filters: Tuple[WebXmlRecord, ...] = ()
    filter_mappings: Tuple[WebXmlRecord, ...] = ()
    listeners: Tuple[WebXmlRecord, ...] = ()
    context_params: Tuple[WebXmlRecord, ...] = ()
    welcome_files: Tuple[WebXmlRecord, ...] = ()
    error_pages: Tuple[WebXmlRecord, ...] = ()
    session_configs: Tuple[WebXmlRecord, ...] = ()
    security_constraints: Tuple[WebXmlRecord, ...] = ()
    security_roles: Tuple[WebXmlRecord, ...] = ()
    login_configs: Tuple[WebXmlRecord, ...] = ()
    other_elements: Tuple[WebXmlRecord, ...] = ()
    unknown_elements: Tuple[WebXmlRecord, ...] = ()

    @property
    def records(self) -> Tuple[WebXmlRecord, ...]:
        rows: List[WebXmlRecord] = []
        for values in (
            self.servlets,
            self.servlet_mappings,
            self.filters,
            self.filter_mappings,
            self.listeners,
            self.context_params,
            self.welcome_files,
            self.error_pages,
            self.session_configs,
            self.security_constraints,
            self.security_roles,
            self.login_configs,
            self.other_elements,
        ):
            rows.extend(values)
        return tuple(
            sorted(
                rows,
                key=lambda item: (
                    item.values.get("descriptor_order", item.order),
                    item.order,
                    item.source.start_line,
                    item.source.start_column,
                ),
            )
        )


@dataclass(frozen=True)
class WebXmlParseResult:
    descriptor: Optional[WebXmlDescriptorData]
    facts: Tuple[ServletJspFact, ...]
    relationships: Tuple[ServletJspRelationship, ...]
    diagnostics: Tuple[Diagnostic, ...]
    claimed: bool = False
    truncated: bool = False

    @property
    def semantic_facts(self) -> Tuple[ServletJspFact, ...]:
        return self.facts

    @property
    def raw_descriptor(self) -> Optional[WebXmlDescriptorData]:
        return self.descriptor

    @property
    def servlet_declarations(self) -> Tuple[WebXmlRecord, ...]:
        return self.descriptor.servlets if self.descriptor else ()

    @property
    def servlet_mappings(self) -> Tuple[WebXmlRecord, ...]:
        return self.descriptor.servlet_mappings if self.descriptor else ()

    @property
    def filter_declarations(self) -> Tuple[WebXmlRecord, ...]:
        return self.descriptor.filters if self.descriptor else ()

    @property
    def filter_mappings(self) -> Tuple[WebXmlRecord, ...]:
        return self.descriptor.filter_mappings if self.descriptor else ()


class _Diagnostics:
    def __init__(self, file_path: str, limit: int) -> None:
        self.file_path = file_path
        self.limit = max(1, int(limit))
        self.rows: List[Diagnostic] = []
        self.dropped = 0

    def add(
        self,
        code: str,
        message: str,
        severity: str = "warning",
        node: Any = None,
        *,
        hint: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if len(self.rows) >= self.limit - 1:
            self.dropped += 1
            return
        span = _span(node, self.file_path) if node is not None else SourceSpan(self.file_path)
        self.rows.append(
            Diagnostic(
                code,
                message,
                severity,
                self.file_path,
                span.start_line,
                span.end_line,
                hint,
                details or {},
            )
        )

    def finish(self) -> Tuple[Diagnostic, ...]:
        if self.dropped:
            self.rows.append(
                Diagnostic(
                    "servlet_jsp.web_xml.diagnostics_truncated",
                    f"Diagnostic budget reached; {self.dropped} additional diagnostics omitted",
                    "warning",
                    self.file_path,
                    hint="Increase max_diagnostics_per_file to retain more malformed/unknown regions.",
                    details={"dropped_count": self.dropped, "limit": self.limit},
                )
            )
        return tuple(self.rows[: self.limit])


@dataclass
class _Context:
    source: bytes
    file_path: str
    project_id: str
    project_name: str
    module_id: str
    module_path: str
    diagnostics: _Diagnostics

    def record(
        self,
        kind: str,
        name: str,
        order: int,
        node: Any,
        values: Optional[Dict[str, Any]] = None,
        children: Sequence[WebXmlRecord] = (),
    ) -> WebXmlRecord:
        record_id = stable_semantic_id(
            kind,
            self.project_id,
            self.module_id,
            self.file_path,
            node.start_point[0] + 1,
            node.start_point[1] + 1,
            order,
        )
        payload = dict(values or {})
        payload.setdefault("descriptor_order", order)
        payload.setdefault("child_values", _direct_child_value_rows(node, self.source, self.file_path))
        raw, was_capped = _bounded_raw(node, self.source)
        param_name = str(payload.get("param_name", ""))
        if param_name and SENSITIVE_KEY_RE.search(param_name):
            payload["param_value"] = "[REDACTED]"
            payload["param_value_redacted"] = True
            payload["child_values"] = tuple(
                {**row, "value": "[REDACTED]"} if row.get("name") == "param-value" else row
                for row in payload["child_values"]
            )
            raw = "[REDACTED]"
            was_capped = False
        if was_capped:
            payload["raw_text_truncated"] = True
            payload["raw_text_sha256"] = hashlib.sha256(_node_bytes(node, self.source)).hexdigest()
        return WebXmlRecord(record_id, kind, name, order, _span(node, self.file_path), raw, payload, tuple(children))


def parse_web_xml_file(
    root: str,
    file_path: str = "",
    project_id: str = "",
    project_name: str = "",
    module_id: str = "",
    module_path: str = "",
    budgets: Optional[ResourceBudgets] = None,
    *,
    rel_path: str = "",
) -> WebXmlParseResult:
    """Parse one project-relative ``web.xml`` without resolving DTDs/entities.

    The function reads only through the root-confined resolver. Tree-sitter XML
    tokenizes declarations and entity references but has no entity loader, so
    neither local files nor network resources named by the descriptor are read.
    """

    effective_budgets = budgets or ResourceBudgets()
    requested_path = normalize_relative_path(file_path or rel_path)
    diagnostic_path = requested_path or file_path or rel_path
    diagnostics = _Diagnostics(diagnostic_path, effective_budgets.max_diagnostics_per_file)
    if not requested_path:
        diagnostics.add("servlet_jsp.web_xml.invalid_path", "Descriptor path is empty", "error")
        return WebXmlParseResult(None, (), (), diagnostics.finish())

    resolution = resolve_project_path(root, requested_path, require_exists=True)
    if resolution.status != "resolved":
        diagnostics.add(
            "servlet_jsp.web_xml.path_rejected" if resolution.status == "rejected" else "servlet_jsp.web_xml.file_unavailable",
            resolution.message or f"Unable to read descriptor: {resolution.status}",
            "error",
            details={"status": resolution.status, "reference": requested_path},
        )
        return WebXmlParseResult(None, (), (), diagnostics.finish())

    try:
        source, truncated = read_bounded_file(resolution.absolute_path, effective_budgets.max_source_bytes)
    except OSError as exc:
        diagnostics.add("servlet_jsp.web_xml.read_failed", str(exc), "error")
        return WebXmlParseResult(None, (), (), diagnostics.finish())
    if truncated:
        diagnostics.add(
            "servlet_jsp.web_xml.source_truncated",
            f"Descriptor exceeds max_source_bytes={effective_budgets.max_source_bytes}",
            "warning",
            hint="Increase max_source_bytes to parse the complete descriptor.",
        )

    module_path = normalize_relative_path(module_path) or _infer_module_path(requested_path)
    module_id = module_id or f"servlet_jsp::module::{stable_digest(project_id, module_path or '.')}"
    project_name = project_name or project_id
    context = _Context(source, requested_path, project_id, project_name, module_id, module_path, diagnostics)
    try:
        tree = parse_xml_bytes(source)
    except Exception as exc:  # noqa: BLE001 - parser capability failures become data diagnostics
        diagnostics.add("servlet_jsp.web_xml.parser_unavailable", str(exc), "error")
        return WebXmlParseResult(None, (), (), diagnostics.finish(), truncated=truncated)

    if tree.root_node.has_error:
        diagnostics.add("servlet_jsp.web_xml.parse_error", "Tree-sitter reported XML syntax errors", "error", tree.root_node)
        for error_node in _syntax_error_nodes(tree.root_node):
            diagnostics.add(
                "servlet_jsp.web_xml.malformed_region",
                "Malformed XML region retained as a diagnostic",
                "error",
                error_node,
            )

    root_element = _document_element(tree.root_node)
    if root_element is None:
        diagnostics.add("servlet_jsp.web_xml.no_root", "XML document has no structurally bounded root element", "error", tree.root_node)
        return WebXmlParseResult(None, (), (), diagnostics.finish(), truncated=truncated)
    root_name = _local_name(_element_name(root_element, source))
    if root_name != "web-app":
        diagnostics.add(
            "servlet_jsp.web_xml.unclaimed_root",
            f"Expected web-app root, found {root_name or '<unknown>'}",
            "warning",
            root_element,
        )
        return WebXmlParseResult(None, (), (), diagnostics.finish(), claimed=False, truncated=truncated)

    attrs = _attrs(root_element, source)
    namespace = _root_namespace(_element_name(root_element, source), attrs)
    if namespace not in _KNOWN_NAMESPACES:
        diagnostics.add(
            "servlet_jsp.web_xml.unknown_namespace",
            f"Unrecognized web-app namespace {namespace!r}; local-name extraction continues",
            "warning",
            root_element,
        )
    doctype = _doctype(source)
    version = attrs.get("version", "").strip() or _doctype_version(doctype)
    if version and version not in _SUPPORTED_VERSIONS:
        diagnostics.add(
            "servlet_jsp.web_xml.unsupported_version",
            f"Servlet descriptor version {version!r} is preserved but has no selected merge semantics",
            "warning",
            root_element,
            details={"supported_versions": sorted(_SUPPORTED_VERSIONS)},
        )
    metadata_raw = attrs.get("metadata-complete", "").strip()
    metadata_complete = _optional_bool(metadata_raw)
    if metadata_raw and metadata_complete is None:
        diagnostics.add(
            "servlet_jsp.web_xml.invalid_metadata_complete",
            f"Invalid metadata-complete value {metadata_raw!r}",
            "warning",
            root_element,
        )
    if _ENTITY_DECL_RE.search(doctype):
        diagnostics.add(
            "servlet_jsp.web_xml.entity_declaration_ignored",
            "Internal entity declarations are inert and are never expanded",
            "warning",
            root_element,
        )

    parsed = _parse_children(context, root_element)
    descriptor_id = stable_semantic_id("web_descriptor", project_id, module_id, requested_path)
    descriptor = WebXmlDescriptorData(
        stable_id=descriptor_id,
        file_path=requested_path,
        module_path=module_path,
        namespace=namespace,
        version=version,
        metadata_complete=metadata_complete,
        metadata_complete_raw=metadata_raw,
        doctype=doctype,
        source=_span(root_element, requested_path),
        root_attributes=attrs,
        **parsed,
    )
    facts, relationships = _build_generic_facts(context, descriptor)
    return WebXmlParseResult(
        descriptor,
        tuple(facts),
        tuple(relationships),
        diagnostics.finish(),
        claimed=True,
        truncated=truncated or diagnostics.dropped > 0,
    )


def _parse_children(context: _Context, root: Any) -> Dict[str, Tuple[WebXmlRecord, ...]]:
    buckets: Dict[str, List[WebXmlRecord]] = {
        "servlets": [],
        "servlet_mappings": [],
        "filters": [],
        "filter_mappings": [],
        "listeners": [],
        "context_params": [],
        "welcome_files": [],
        "error_pages": [],
        "session_configs": [],
        "security_constraints": [],
        "security_roles": [],
        "login_configs": [],
        "other_elements": [],
        "unknown_elements": [],
    }
    counters: Dict[str, int] = {}
    seen_servlets: Dict[str, WebXmlRecord] = {}
    seen_filters: Dict[str, WebXmlRecord] = {}
    unknown_nodes = list(_unknown_elements(root, context.source))
    unknown_keys = {(item.start_byte, item.end_byte) for item in unknown_nodes}
    for unknown_order, node in enumerate(unknown_nodes):
        name = _element_name(node, context.source)
        local = _local_name(name)
        record = context.record("unknown_element", local or name, unknown_order, node, {"qualified_name": name})
        buckets["unknown_elements"].append(record)
        context.diagnostics.add(
            "servlet_jsp.web_xml.unknown_element",
            f"Unknown descriptor element {name!r} preserved",
            "warning",
            node,
            details={"qualified_name": name, "raw_sha256": hashlib.sha256(_node_bytes(node, context.source)).hexdigest()},
        )

    for descriptor_order, node in enumerate(_child_elements(root)):
        tag = _local_name(_element_name(node, context.source))
        category_order = counters.get(tag, 0)
        counters[tag] = category_order + 1
        common = {"descriptor_order": descriptor_order}
        if tag == "servlet":
            record = _component_record(context, node, "servlet", category_order, common)
            buckets["servlets"].append(record)
            name = str(record.values.get("servlet_name", ""))
            if name in seen_servlets and name:
                context.diagnostics.add("servlet_jsp.web_xml.duplicate_servlet", f"Duplicate servlet-name {name!r} preserved", "warning", node)
            elif name:
                seen_servlets[name] = record
        elif tag == "servlet-mapping":
            buckets["servlet_mappings"].append(_mapping_record(context, node, "servlet_mapping", category_order, common))
        elif tag == "filter":
            record = _component_record(context, node, "filter", category_order, common)
            buckets["filters"].append(record)
            name = str(record.values.get("filter_name", ""))
            if name in seen_filters and name:
                context.diagnostics.add("servlet_jsp.web_xml.duplicate_filter", f"Duplicate filter-name {name!r} preserved", "warning", node)
            elif name:
                seen_filters[name] = record
        elif tag == "filter-mapping":
            buckets["filter_mappings"].append(_mapping_record(context, node, "filter_mapping", category_order, common))
        elif tag == "listener":
            values = {**common, "listener_class": _first_child_text(node, context.source, "listener-class")}
            buckets["listeners"].append(context.record("listener", values["listener_class"] or f"listener[{category_order}]", category_order, node, values))
        elif tag == "context-param":
            buckets["context_params"].append(_param_record(context, node, "context_param", category_order, common))
        elif tag == "welcome-file-list":
            for child in _children_named(node, context.source, "welcome-file"):
                welcome_order = len(buckets["welcome_files"])
                path = _direct_text(child, context.source, context.diagnostics)
                buckets["welcome_files"].append(
                    context.record(
                        "welcome_file",
                        path or f"welcome[{welcome_order}]",
                        welcome_order,
                        child,
                        {**common, "welcome_order": welcome_order, "path": path},
                    )
                )
        elif tag == "error-page":
            values = {
                **common,
                "error_code": _first_child_text(node, context.source, "error-code"),
                "exception_type": _first_child_text(node, context.source, "exception-type"),
                "location": _first_child_text(node, context.source, "location"),
            }
            name = values["error_code"] or values["exception_type"] or f"error-page[{category_order}]"
            buckets["error_pages"].append(context.record("error_page", name, category_order, node, values))
        elif tag == "session-config":
            buckets["session_configs"].append(_session_record(context, node, category_order, common))
        elif tag == "security-constraint":
            buckets["security_constraints"].append(_security_constraint_record(context, node, category_order, common))
        elif tag == "security-role":
            role = _first_child_text(node, context.source, "role-name")
            values = {**common, "role_name": role, "descriptions": _child_texts(node, context.source, "description")}
            buckets["security_roles"].append(context.record("security_role", role or f"role[{category_order}]", category_order, node, values))
        elif tag == "login-config":
            buckets["login_configs"].append(_login_record(context, node, category_order, common))
        elif (node.start_byte, node.end_byte) not in unknown_keys:
            values = {**common, "element_name": _element_name(node, context.source)}
            buckets["other_elements"].append(context.record("descriptor_element", tag, category_order, node, values))
    return {key: tuple(value) for key, value in buckets.items()}


def _component_record(
    context: _Context,
    node: Any,
    component: str,
    order: int,
    common: Dict[str, Any],
) -> WebXmlRecord:
    name_key = f"{component}_name"
    class_key = f"{component}_class"
    init_params = [
        _param_record(context, child, f"{component}_init_param", index, {"owner_kind": component})
        for index, child in enumerate(_children_named(node, context.source, "init-param"))
    ]
    async_raw = _first_child_text(node, context.source, "async-supported")
    values: Dict[str, Any] = {
        **common,
        "declaration_order": order,
        name_key: _first_child_text(node, context.source, f"{component}-name"),
        class_key: _first_child_text(node, context.source, f"{component}-class"),
        "async_supported": _optional_bool(async_raw),
        "async_supported_raw": async_raw,
        "init_params": [_record_payload(item) for item in init_params],
        "descriptions": _child_texts(node, context.source, "description"),
    }
    children: List[WebXmlRecord] = list(init_params)
    if component == "servlet":
        load_raw = _first_child_text(node, context.source, "load-on-startup")
        values.update(
            {
                "jsp_file": _first_child_text(node, context.source, "jsp-file"),
                "load_on_startup": load_raw,
                "load_on_startup_value": _optional_int(load_raw),
                "run_as_roles": tuple(
                    _first_child_text(item, context.source, "role-name")
                    for item in _children_named(node, context.source, "run-as")
                ),
                "security_role_refs": tuple(
                    {
                        "role_name": _first_child_text(item, context.source, "role-name"),
                        "role_link": _first_child_text(item, context.source, "role-link"),
                        "source": _span_dict(_span(item, context.file_path)),
                    }
                    for item in _children_named(node, context.source, "security-role-ref")
                ),
            }
        )
        multipart_nodes = _children_named(node, context.source, "multipart-config")
        multipart_rows: List[WebXmlRecord] = []
        for index, multipart in enumerate(multipart_nodes):
            multipart_values = {
                "location": _first_child_text(multipart, context.source, "location"),
                "max_file_size": _first_child_text(multipart, context.source, "max-file-size"),
                "max_request_size": _first_child_text(multipart, context.source, "max-request-size"),
                "file_size_threshold": _first_child_text(multipart, context.source, "file-size-threshold"),
            }
            multipart_rows.append(context.record("multipart_config", "multipart", index, multipart, multipart_values))
        children.extend(multipart_rows)
        values["multipart_configs"] = [_record_payload(item) for item in multipart_rows]
        if values["servlet_class"] and values["jsp_file"]:
            context.diagnostics.add(
                "servlet_jsp.web_xml.servlet_class_and_jsp_file",
                "Servlet declares both servlet-class and jsp-file; both values are preserved",
                "warning",
                node,
            )
    name = str(values.get(name_key, "")) or f"{component}[{order}]"
    return context.record(component, name, order, node, values, children)


def _mapping_record(
    context: _Context,
    node: Any,
    kind: str,
    order: int,
    common: Dict[str, Any],
) -> WebXmlRecord:
    if kind == "servlet_mapping":
        servlet_name = _first_child_text(node, context.source, "servlet-name")
        values = {
            **common,
            "mapping_order": order,
            "servlet_name": servlet_name,
            "url_patterns": _child_texts(node, context.source, "url-pattern"),
        }
        return context.record(kind, servlet_name or f"servlet-mapping[{order}]", order, node, values)
    filter_name = _first_child_text(node, context.source, "filter-name")
    dispatcher_values = _child_texts(node, context.source, "dispatcher")
    values = {
        **common,
        "mapping_order": order,
        "filter_name": filter_name,
        "url_patterns": _child_texts(node, context.source, "url-pattern"),
        "servlet_names": _child_texts(node, context.source, "servlet-name"),
        "dispatchers": tuple(value.upper() for value in dispatcher_values),
        "dispatcher_values": dispatcher_values,
    }
    return context.record(kind, filter_name or f"filter-mapping[{order}]", order, node, values)


def _param_record(
    context: _Context,
    node: Any,
    kind: str,
    order: int,
    common: Optional[Dict[str, Any]] = None,
) -> WebXmlRecord:
    name = _first_child_text(node, context.source, "param-name")
    value = _first_child_text(node, context.source, "param-value")
    values = {
        **(common or {}),
        "param_name": name,
        "param_value": value,
        "descriptions": _child_texts(node, context.source, "description"),
    }
    return context.record(kind, name or f"param[{order}]", order, node, values)


def _session_record(
    context: _Context,
    node: Any,
    order: int,
    common: Dict[str, Any],
) -> WebXmlRecord:
    cookie_rows: List[WebXmlRecord] = []
    for cookie_order, cookie in enumerate(_children_named(node, context.source, "cookie-config")):
        cookie_values = {
            key.replace("-", "_"): _first_child_text(cookie, context.source, key)
            for key in ("name", "domain", "path", "comment", "http-only", "secure", "max-age")
        }
        cookie_values["http_only_value"] = _optional_bool(cookie_values["http_only"])
        cookie_values["secure_value"] = _optional_bool(cookie_values["secure"])
        cookie_rows.append(context.record("cookie_config", "session-cookie", cookie_order, cookie, cookie_values))
    values = {
        **common,
        "session_timeout": _first_child_text(node, context.source, "session-timeout"),
        "tracking_modes": tuple(value.upper() for value in _child_texts(node, context.source, "tracking-mode")),
        "cookie_configs": [_record_payload(item) for item in cookie_rows],
    }
    return context.record("session_config", "session", order, node, values, cookie_rows)


def _security_constraint_record(
    context: _Context,
    node: Any,
    order: int,
    common: Dict[str, Any],
) -> WebXmlRecord:
    collections: List[WebXmlRecord] = []
    for collection_order, collection in enumerate(_children_named(node, context.source, "web-resource-collection")):
        name = _first_child_text(collection, context.source, "web-resource-name")
        values = {
            "web_resource_name": name,
            "descriptions": _child_texts(collection, context.source, "description"),
            "url_patterns": _child_texts(collection, context.source, "url-pattern"),
            "http_methods": tuple(value.upper() for value in _child_texts(collection, context.source, "http-method")),
            "http_method_omissions": tuple(value.upper() for value in _child_texts(collection, context.source, "http-method-omission")),
        }
        collections.append(context.record("web_resource_collection", name or f"collection[{collection_order}]", collection_order, collection, values))
    auth_nodes = _children_named(node, context.source, "auth-constraint")
    role_names: List[str] = []
    for auth in auth_nodes:
        role_names.extend(_child_texts(auth, context.source, "role-name"))
    transport_guarantees: List[str] = []
    for user_data in _children_named(node, context.source, "user-data-constraint"):
        transport_guarantees.extend(_child_texts(user_data, context.source, "transport-guarantee"))
    values = {
        **common,
        "display_names": _child_texts(node, context.source, "display-name"),
        "web_resource_collections": [_record_payload(item) for item in collections],
        "auth_constraint_present": bool(auth_nodes),
        "role_names": tuple(role_names),
        "transport_guarantees": tuple(value.upper() for value in transport_guarantees),
    }
    return context.record("security_constraint", f"security[{order}]", order, node, values, collections)


def _login_record(
    context: _Context,
    node: Any,
    order: int,
    common: Dict[str, Any],
) -> WebXmlRecord:
    form_rows: List[WebXmlRecord] = []
    for form_order, form in enumerate(_children_named(node, context.source, "form-login-config")):
        values = {
            "form_login_page": _first_child_text(form, context.source, "form-login-page"),
            "form_error_page": _first_child_text(form, context.source, "form-error-page"),
        }
        form_rows.append(context.record("form_login_config", "form-login", form_order, form, values))
    values = {
        **common,
        "auth_method": _first_child_text(node, context.source, "auth-method"),
        "realm_name": _first_child_text(node, context.source, "realm-name"),
        "form_login_configs": [_record_payload(item) for item in form_rows],
    }
    return context.record("login_config", "login", order, node, values, form_rows)


def _build_generic_facts(
    context: _Context,
    descriptor: WebXmlDescriptorData,
) -> Tuple[List[ServletJspFact], List[ServletJspRelationship]]:
    facts: List[ServletJspFact] = [
        ServletJspFact(
            kind="WebDescriptor",
            stable_id=descriptor.stable_id,
            name=os.path.basename(descriptor.file_path),
            source=descriptor.source,
            project_id=context.project_id,
            project_name=context.project_name,
            module_id=context.module_id,
            extraction_method="tree_sitter_xml",
            raw_value=descriptor.doctype,
            resolved_value=descriptor.file_path,
            properties={
                "module_path": descriptor.module_path,
                "namespace": descriptor.namespace,
                "version": descriptor.version,
                "metadata_complete": descriptor.metadata_complete,
                "metadata_complete_raw": descriptor.metadata_complete_raw,
                "root_attributes": descriptor.root_attributes,
                "descriptor_order_preserved": True,
            },
        )
    ]
    relationships: List[ServletJspRelationship] = []
    label_for_kind = {
        "servlet": "Servlet",
        "servlet_mapping": "ServletMapping",
        "filter": "Filter",
        "filter_mapping": "FilterMapping",
        "listener": "Listener",
        "welcome_file": "WelcomePage",
        "error_page": "ErrorPage",
        "security_constraint": "SecurityConstraint",
        "security_role": "Authority",
    }
    for record in descriptor.records:
        label = label_for_kind.get(record.kind, "WebConfiguration")
        properties = dict(record.values)
        properties.update({"descriptor_id": descriptor.stable_id, "record_kind": record.kind, "provenance": "web.xml"})
        fact = ServletJspFact(
            kind=label,
            stable_id=record.stable_id,
            name=record.name,
            source=record.source,
            project_id=context.project_id,
            project_name=context.project_name,
            module_id=context.module_id,
            extraction_method="tree_sitter_xml",
            raw_value=record.raw_text,
            resolved_value=record.name,
            properties=properties,
        )
        facts.append(fact)
        relationships.append(_relationship(context, descriptor.stable_id, "WebDescriptor", fact, "DECLARES", record.source, record.order))
        for child_order, child in enumerate(record.children):
            child_fact = ServletJspFact(
                kind="WebConfiguration",
                stable_id=child.stable_id,
                name=child.name,
                source=child.source,
                project_id=context.project_id,
                project_name=context.project_name,
                module_id=context.module_id,
                extraction_method="tree_sitter_xml",
                raw_value=child.raw_text,
                resolved_value=child.name,
                properties={**child.values, "owner_id": record.stable_id, "record_kind": child.kind, "provenance": "web.xml"},
            )
            facts.append(child_fact)
            relationships.append(_relationship(context, record.stable_id, label, child_fact, "CONFIGURES", child.source, child_order))
    return facts, relationships


def _relationship(
    context: _Context,
    from_id: str,
    from_label: str,
    target: ServletJspFact,
    relationship_type: str,
    source: SourceSpan,
    occurrence: int,
) -> ServletJspRelationship:
    rel_id = stable_semantic_id(
        "relationship",
        context.project_id,
        context.module_id,
        relationship_type,
        from_id,
        target.stable_id,
        context.file_path,
        source.start_line,
        source.start_column,
        occurrence,
    )
    return ServletJspRelationship(
        stable_id=rel_id,
        from_id=from_id,
        to_id=target.stable_id,
        from_label=from_label,
        to_label=target.kind,
        type=relationship_type,
        project_id=context.project_id,
        module_id=context.module_id,
        source=source,
        reason="web.xml descriptor provenance",
        properties={"provenance": "web.xml", "occurrence_order": occurrence},
    )


def _record_payload(record: WebXmlRecord) -> Dict[str, Any]:
    return {
        "stable_id": record.stable_id,
        "kind": record.kind,
        "name": record.name,
        "order": record.order,
        "source": _span_dict(record.source),
        "values": record.values,
        "raw_text": record.raw_text,
    }


def _document_element(root: Any) -> Any:
    if root.type == "element":
        return root
    for child in root.children:
        if child.type == "element":
            return child
    return None


def _child_elements(node: Any) -> List[Any]:
    content = next((child for child in node.children if child.type == "content"), None)
    if content is None:
        return []
    return [child for child in content.named_children if child.type == "element"]


def _descendant_elements(node: Any) -> Iterable[Any]:
    for child in _child_elements(node):
        yield child
        yield from _descendant_elements(child)


def _unknown_elements(root: Any, source: bytes) -> Iterable[Any]:
    for node in _descendant_elements(root):
        if _local_name(_element_name(node, source)) not in _KNOWN_ELEMENT_NAMES:
            yield node


def _children_named(node: Any, source: bytes, local_name: str) -> List[Any]:
    return [child for child in _child_elements(node) if _local_name(_element_name(child, source)) == local_name]


def _first_child_text(node: Any, source: bytes, local_name: str) -> str:
    values = _children_named(node, source, local_name)
    return _direct_text(values[0], source) if values else ""


def _child_texts(node: Any, source: bytes, local_name: str) -> Tuple[str, ...]:
    return tuple(_direct_text(child, source) for child in _children_named(node, source, local_name))


def _direct_child_value_rows(node: Any, source: bytes, file_path: str) -> Tuple[Dict[str, Any], ...]:
    rows: List[Dict[str, Any]] = []
    for child in _child_elements(node):
        if _child_elements(child):
            continue
        rows.append(
            {
                "name": _local_name(_element_name(child, source)),
                "qualified_name": _element_name(child, source),
                "value": _direct_text(child, source),
                "source": _span_dict(_span(child, file_path)),
            }
        )
    return tuple(rows)


def _direct_text(node: Any, source: bytes, diagnostics: Optional[_Diagnostics] = None) -> str:
    content = next((child for child in node.children if child.type == "content"), None)
    if content is None:
        return ""
    parts: List[str] = []
    for child in content.children:
        if child.type in {"CharData", "CData", "CharRef"}:
            parts.append(_decode_xml_entities(_text(child, source)))
        elif child.type == "CDSect":
            for descendant in child.children:
                if descendant.type == "CData":
                    parts.append(_text(descendant, source))
        elif child.type == "EntityRef":
            raw = _text(child, source)
            decoded = _decode_xml_entities(raw)
            parts.append(decoded)
            if diagnostics is not None and decoded == raw:
                diagnostics.add(
                    "servlet_jsp.web_xml.entity_reference_ignored",
                    f"Entity reference {raw!r} was retained without expansion",
                    "warning",
                    child,
                )
    return "".join(parts).strip()


def _decode_xml_entities(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name:
            return _XML_ENTITIES.get(name, match.group(0))
        try:
            number = int(match.group("hex"), 16) if match.group("hex") else int(match.group("decimal"), 10)
            if number == 0 or number > 0x10FFFF or 0xD800 <= number <= 0xDFFF:
                return match.group(0)
            return chr(number)
        except (TypeError, ValueError):
            return match.group(0)

    return _ENTITY_RE.sub(replace, value)


def _element_name(node: Any, source: bytes) -> str:
    tag = next((child for child in node.children if child.type in {"STag", "EmptyElemTag"}), None)
    if tag is None:
        return ""
    name = next((child for child in tag.children if child.type == "Name"), None)
    return _text(name, source)


def _attrs(node: Any, source: bytes) -> Dict[str, str]:
    tag = next((child for child in node.children if child.type in {"STag", "EmptyElemTag"}), None)
    attrs: Dict[str, str] = {}
    if tag is None:
        return attrs
    for child in tag.children:
        if child.type != "Attribute":
            continue
        name = next((item for item in child.children if item.type == "Name"), None)
        value = next((item for item in child.children if item.type == "AttValue"), None)
        key = _text(name, source)
        raw = _text(value, source)
        if len(raw) >= 2 and raw[0] in {'"', "'"} and raw[-1] == raw[0]:
            raw = raw[1:-1]
        attrs[key] = _decode_xml_entities(raw)
    return attrs


def _root_namespace(qualified_name: str, attrs: Dict[str, str]) -> str:
    if ":" in qualified_name:
        prefix = qualified_name.split(":", 1)[0]
        return attrs.get(f"xmlns:{prefix}", "")
    return attrs.get("xmlns", "")


def _local_name(name: str) -> str:
    return (name or "").rsplit(":", 1)[-1]


def _doctype(source: bytes) -> str:
    text = source.decode("utf-8", errors="replace")
    match = re.search(r"<!DOCTYPE\b", text, re.IGNORECASE)
    if not match:
        return ""
    quote = ""
    bracket_depth = 0
    for index in range(match.start(), len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "[":
            bracket_depth += 1
        elif char == "]" and bracket_depth:
            bracket_depth -= 1
        elif char == ">" and bracket_depth == 0:
            return text[match.start() : index + 1]
    return text[match.start() :]


def _doctype_version(doctype: str) -> str:
    match = _DOCTYPE_VERSION_RE.search(doctype or "")
    if not match:
        return ""
    if match.group("version"):
        return match.group("version")
    return f"{match.group('major')}.{match.group('minor')}"


def _syntax_error_nodes(root: Any) -> Iterable[Any]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            yield node
        stack.extend(reversed(node.children))


def _optional_bool(value: str) -> Optional[bool]:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    return None


def _optional_int(value: str) -> Optional[int]:
    try:
        return int((value or "").strip())
    except ValueError:
        return None


def _infer_module_path(file_path: str) -> str:
    normalized = normalize_relative_path(file_path)
    if normalized == "WEB-INF/web.xml":
        return "."
    for marker in ("/src/main/webapp/WEB-INF/web.xml", "/WEB-INF/web.xml"):
        if normalized.endswith(marker):
            return normalized[: -len(marker)] or "."
    return normalize_relative_path(os.path.dirname(normalized)) or "."


def _span(node: Any, file_path: str) -> SourceSpan:
    return SourceSpan(
        file_path,
        node.start_point[0] + 1,
        node.end_point[0] + 1,
        node.start_point[1] + 1,
        node.end_point[1] + 1,
    )


def _span_dict(span: SourceSpan) -> Dict[str, Any]:
    return {
        "file_path": span.file_path,
        "start_line": span.start_line,
        "end_line": span.end_line,
        "start_column": span.start_column,
        "end_column": span.end_column,
    }


def _bounded_raw(node: Any, source: bytes) -> Tuple[str, bool]:
    raw = _node_bytes(node, source)
    capped = len(raw) > _RAW_TEXT_LIMIT
    return raw[:_RAW_TEXT_LIMIT].decode("utf-8", errors="replace"), capped


def _node_bytes(node: Any, source: bytes) -> bytes:
    return source[node.start_byte : node.end_byte]


def _text(node: Any, source: bytes) -> str:
    if node is None:
        return ""
    return _node_bytes(node, source).decode("utf-8", errors="replace")


__all__ = [
    "WebXmlDescriptorData",
    "WebXmlParseResult",
    "WebXmlRecord",
    "parse_web_xml_file",
]

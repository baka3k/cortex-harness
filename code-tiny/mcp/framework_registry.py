"""Canonical parser capabilities shared by Unified MCP backends."""

from __future__ import annotations

import hashlib
import os
import re

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Tuple


CORE_RELATIONSHIPS = (
    "CALLS", "USES_TYPE", "REFERENCES", "INHERITS", "ALIASES", "ALIAS_OF",
)
GENERIC_LABELS = frozenset({
    "Project", "Repository", "Module", "Package", "File", "Namespace",
    "Class", "Interface", "Enum", "Type", "Function", "Method", "Field",
    "Alias", "Template", "FunctionType", "Event", "Resource", "UIControl",
    "ProjectModule", "BuildDescriptor", "Dependency", "FrameworkInstance",
    "GrpcService", "GrpcEndpoint",
})
GENERIC_SEARCHABLE_PROPERTIES = (
    "name", "qualified_name", "target_name", "signature", "type_signature",
    "file_path", "path", "summary", "code", "comment",
    "module_id", "module_path", "visibility", "is_public_api",
    "descriptor_type", "role", "parse_depth", "protocol", "framework",
)
GENERIC_RELATIONSHIPS = (
    "CALLS", "DECLARES", "CONTAINS", "DEPENDS_ON", "IMPORTS", "EXPORTS",
    "IMPLEMENTS", "EXTENDS", "ALIASES", "ALIAS_OF",
    "HAS_DESCRIPTOR", "EXPOSES_API", "EXPOSES_ENDPOINT", "USES_FRAMEWORK",
)
CONTEXT_RELATIONSHIPS = frozenset(
    {
        "HAS_DESCRIPTOR",
        "EXPOSES_API",
        "EXPOSES_ENDPOINT",
        "USES_FRAMEWORK",
    }
)
CPLUS_RELATIONSHIPS = (
    "CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER", "DECLARES", "CONTAINS",
    "USES_RESOURCE", "BINDS_CONTROL", "HANDLES_CONTROL", "OWNS_DIALOG", "DEPENDS_ON",
    "ALIASES", "ALIAS_OF", "EXEC_SQL",
)
ANDROID_RELATIONSHIPS = (
    "CALLS", "DECLARES", "CONTAINS", "USES_RESOURCE", "DECLARES_ROUTE",
    "STARTS_WITH_ROUTE", "ROUTE_CALLS", "DECLARES_COMPONENT", "STARTS_COMPONENT",
    "STARTS_INTENT", "SENDS_BROADCAST", "REGISTERS_RECEIVER",
    "DECLARES_INTENT_ACTION", "SENDS_HANDLER_MESSAGE", "ACTION_TARGETS_COMPONENT",
    "EMITS_EVENT", "HANDLES_EVENT", "ANNOTATED_WITH", "DEPENDS_ON",
    "TAKES_FUNCTION", "IMPLEMENTS", "EXTENDS",
)
WEB_LABELS = GENERIC_LABELS | frozenset({
    "ApiCall", "ApiEndpoint", "HttpEndpoint", "Route", "Controller",
    "Service", "Repository", "Middleware", "Database",
})
WEB_RELATIONSHIPS = tuple(dict.fromkeys((
    *GENERIC_RELATIONSHIPS, "CALLS_API", "MATCHES", "HANDLES", "HANDLED_BY",
    "MAPPED_TO", "USES", "QUERIES", "RETURNS", "INJECTS",
)))
WEB_SEARCHABLE_PROPERTIES = tuple(dict.fromkeys((
    *GENERIC_SEARCHABLE_PROPERTIES, "route", "path", "url_pattern",
    "http_method", "framework", "handler_name", "controller_name",
)))
DATABASE_LABELS = GENERIC_LABELS | frozenset({"Table", "View", "Procedure"})
DATABASE_RELATIONSHIPS = tuple(dict.fromkeys((
    *GENERIC_RELATIONSHIPS, "READS_FROM", "WRITES_TO", "REFERENCES_TABLE",
)))
DATABASE_SEARCHABLE_PROPERTIES = tuple(dict.fromkeys((
    *GENERIC_SEARCHABLE_PROPERTIES, "schema_name", "dialect", "object_kind", "declared",
)))

GENERIC_FEATURES = frozenset({
    "graph_search", "graph_paths", "graph_flow", "semantic_search",
    "graph_exploration", "dependency_planning",
    "module_queries", "public_api_queries", "endpoint_inventory_queries",
    "architecture_summary_queries", "special_file_queries",
    "framework_context_queries",
})
FRAMEWORK_FEATURES = GENERIC_FEATURES | frozenset({
    "framework_query", "profile_labels", "profile_relationships",
})
_ALLOWED_BACKENDS = frozenset({"android", "cplus"})
_ALLOWED_SUPPORT_LEVELS = frozenset({"full", "partial", "generic"})
_ALLOWED_DIMENSION_LEVELS = frozenset({"full", "partial", "generic", "none"})
SUPPORT_DIMENSIONS = ("symbols", "calls", "endpoints", "database")
PUBLIC_QUERY_ENGINES = MappingProxyType({
    "android": "android_graph",
    "cplus": "graph_generic",
    "shell": "graph_generic",
    "jp1": "graph_generic",
    "batchconfig": "graph_generic",
    "fast": "fast_graph",
})
CAPABILITY_CONTRACT_VERSION = 1
_DIMENSION_LABEL_EVIDENCE = MappingProxyType({
    "symbols": frozenset({
        "File", "Namespace", "Package", "Module", "Class", "Interface", "Enum",
        "Type", "Function", "Method", "Field", "Alias", "Template", "FunctionType",
        "CobolProgram", "CobolSection", "CobolParagraph", "CobolDataItem", "CobolCopybook",
        "Widget", "Screen", "AndroidComponent", "Project", "Repository",
    }),
    "calls": frozenset(),
    "endpoints": frozenset({"ApiEndpoint", "HttpEndpoint", "GrpcEndpoint", "Route"}),
    "database": frozenset({
        "Table", "View", "Procedure", "Database", "DataRepository", "Repository",
        "MyBatisStatement", "SqlStatement", "CobolSqlStatement",
    }),
})
_DIMENSION_RELATIONSHIP_EVIDENCE = MappingProxyType({
    "symbols": frozenset(),
    "calls": frozenset({
        "CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER", "ROUTE_CALLS", "PERFORMS",
        "STARTS_COMPONENT", "SENDS_MESSAGE", "TARGETS_ENDPOINT",
    }),
    "endpoints": frozenset({
        "HANDLES", "HANDLED_BY", "MAPPED_TO", "SEMANTIC_OF", "DECLARES_ROUTE",
        "STARTS_WITH_ROUTE", "MATCHES", "TARGETS_ENDPOINT",
    }),
    "database": frozenset({
        "READS_FROM", "WRITES_TO", "REFERENCES_TABLE", "QUERIES", "DECLARES_QUERY",
        "DERIVES_QUERY", "BINDS_STATEMENT", "DECLARES_STATEMENT",
    }),
})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class FrameworkQueryConfig:
    """Backward-compatible name for a canonical parser capability profile."""

    name: str
    aliases: FrozenSet[str]
    labels: FrozenSet[str]
    relationships: Tuple[str, ...]
    searchable_properties: Tuple[str, ...]
    generation_scoped: bool = False
    backend: str = "cplus"
    support_level: str = "full"
    default_query_profiles: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    features: FrozenSet[str] = GENERIC_FEATURES
    support: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = self.name.strip().lower()
        aliases = frozenset(str(alias).strip().lower() for alias in self.aliases if str(alias).strip())
        profiles = {
            str(profile).strip(): tuple(dict.fromkeys(relationships))
            for profile, relationships in self.default_query_profiles.items()
            if str(profile).strip()
        }
        default_relationships = tuple(
            dict.fromkeys(
                (
                    *CORE_RELATIONSHIPS,
                    *(
                        relationship
                        for relationship in self.relationships
                        if relationship not in CONTEXT_RELATIONSHIPS
                    ),
                )
            )
        )
        profiles.setdefault("default", default_relationships)
        profiles.setdefault("find_callers_of_endpoint", ("CALLS_API", "MATCHES"))
        profiles.setdefault(
            "get_api_call_chain",
            tuple(dict.fromkeys((
                *default_relationships, "CALLS_API", "MATCHES", "HANDLES", "SEMANTIC_OF",
                "DECLARES_QUERY", "DERIVES_QUERY", "QUERIES", "BINDS_STATEMENT",
                "DECLARES_STATEMENT", "READS_FROM", "WRITES_TO", "REFERENCES_TABLE",
            ))),
        )
        profiles.setdefault("find_screen_workflows", ("NAVIGATE",))
        profiles.setdefault(
            "find_workflows_containing",
            tuple(dict.fromkeys(("HAS_STEP", *default_relationships))),
        )
        profiles.setdefault(
            "analyze_workflow_impact",
            tuple(dict.fromkeys((*default_relationships, "HAS_STEP", "NAVIGATE"))),
        )
        profiles.setdefault(
            "get_project_modules", ("HAS_DESCRIPTOR", "DEPENDS_ON")
        )
        profiles.setdefault("get_public_apis", ("EXPOSES_API",))
        profiles.setdefault("get_endpoints", ("EXPOSES_ENDPOINT",))
        profiles.setdefault(
            "get_module_architecture_summary",
            (
                "HAS_DESCRIPTOR",
                "DEPENDS_ON",
                "EXPOSES_API",
                "EXPOSES_ENDPOINT",
                "USES_FRAMEWORK",
            ),
        )
        profiles.setdefault("get_project_special_files", ("HAS_DESCRIPTOR",))
        profiles.setdefault("get_framework_context", ("USES_FRAMEWORK",))
        legacy_level = self.support_level.strip().lower()
        default_symbol_level = "generic" if legacy_level == "generic" else "full"
        default_call_level = (
            "generic" if legacy_level == "generic"
            else "partial" if legacy_level == "partial"
            else "full"
        )
        endpoint_level = (
            "none" if "endpoint_queries" not in self.features
            else "full" if legacy_level == "full"
            else "partial"
        )
        database_semantics = bool(
            {"Table", "View", "Procedure", "Database", "DataRepository"} & set(self.labels)
            or {"READS_FROM", "WRITES_TO", "REFERENCES_TABLE", "QUERIES"} & set(self.relationships)
        )
        database_level = (
            "none" if not database_semantics
            else "full" if legacy_level == "full"
            else "partial"
        )
        support = {
            "symbols": default_symbol_level,
            "calls": default_call_level,
            "endpoints": endpoint_level,
            "database": database_level,
            **{
                str(key).strip().lower(): str(value).strip().lower()
                for key, value in self.support.items()
            },
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "backend", self.backend.strip().lower())
        object.__setattr__(self, "support_level", legacy_level)
        object.__setattr__(self, "default_query_profiles", MappingProxyType(profiles))
        object.__setattr__(self, "support", MappingProxyType(support))

    def relationships_for(self, tool_name: Optional[str] = None) -> Tuple[str, ...]:
        key = str(tool_name or "").strip()
        return self.default_query_profiles.get(key, self.default_query_profiles["default"])

    def to_dict(self) -> Dict[str, object]:
        return {
            "canonical_parser": self.name,
            "aliases": sorted(self.aliases),
            "query_engine": query_engine_for_backend(self.backend),
            "support_level": self.support_level,
            "support": dict(self.support),
            "labels": sorted(self.labels),
            "relationships": list(self.relationships_for()),
            "searchable_properties": list(self.searchable_properties),
            "default_query_profiles": {
                name: list(relationships)
                for name, relationships in sorted(self.default_query_profiles.items())
            },
            "features": sorted(self.features),
            "generation_scoped": self.generation_scoped,
        }


def query_engine_for_backend(backend: Optional[str]) -> str:
    """Return the public query-engine name for an internal dispatch backend."""
    internal_name = str(backend or "cplus").strip().lower() or "cplus"
    return PUBLIC_QUERY_ENGINES.get(internal_name, "graph_generic")


def schema_fingerprint(
    available_labels: Optional[Iterable[str]],
    available_relationships: Optional[Iterable[str]],
) -> Optional[str]:
    """Return an order-independent fingerprint for an inspectable graph schema."""
    if available_labels is None or available_relationships is None:
        return None
    labels = sorted({str(value).strip() for value in available_labels if str(value).strip()})
    relationships = sorted({
        str(value).strip().upper()
        for value in available_relationships
        if str(value).strip()
    })
    material = (
        f"v{CAPABILITY_CONTRACT_VERSION}|labels=" + "\x1f".join(labels)
        + "|relationships=" + "\x1f".join(relationships)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def capability_schema_contract(
    capability: FrameworkQueryConfig,
) -> Dict[str, Dict[str, Tuple[str, ...]]]:
    """Build the provider-schema evidence contract for one parser profile."""
    profile_labels = set(capability.labels)
    profile_relationships = set(capability.relationships_for())
    contracts: Dict[str, Dict[str, Tuple[str, ...]]] = {}
    for dimension in SUPPORT_DIMENSIONS:
        label_candidates = (
            profile_labels
            if dimension == "symbols"
            else profile_labels & set(_DIMENSION_LABEL_EVIDENCE[dimension])
        )
        contracts[dimension] = {
            "labels_any": tuple(sorted(label_candidates)),
            "relationships_any": tuple(sorted(
                profile_relationships & set(_DIMENSION_RELATIONSHIP_EVIDENCE[dimension])
            )),
        }
    return contracts


def evaluate_capability_schema(
    capability: FrameworkQueryConfig,
    *,
    available_labels: Optional[Iterable[str]],
    available_relationships: Optional[Iterable[str]],
) -> Dict[str, object]:
    """Compare advertised parser support with the active provider schema."""
    label_set = (
        {str(value).strip() for value in available_labels if str(value).strip()}
        if available_labels is not None else None
    )
    relationship_set = (
        {str(value).strip().upper() for value in available_relationships if str(value).strip()}
        if available_relationships is not None else None
    )
    contracts = capability_schema_contract(capability)
    dimensions: Dict[str, Dict[str, object]] = {}
    for dimension in SUPPORT_DIMENSIONS:
        advertised = capability.support[dimension]
        contract = contracts[dimension]
        required_labels = tuple(contract["labels_any"])
        required_relationships = tuple(contract["relationships_any"])
        if advertised == "none":
            dimensions[dimension] = {
                "advertised": advertised,
                "observed": "not_applicable",
                "effective": "none",
                "labels_any": list(required_labels),
                "relationships_any": list(required_relationships),
                "matched_labels": [],
                "matched_relationships": [],
                "missing_labels_any": [],
                "missing_relationships_any": [],
            }
            continue

        matched_labels = sorted(label_set & set(required_labels)) if label_set is not None else []
        matched_relationships = (
            sorted(relationship_set & set(required_relationships))
            if relationship_set is not None else []
        )
        checks: list[Optional[bool]] = []
        if required_labels:
            checks.append(None if label_set is None else bool(matched_labels))
        if required_relationships:
            checks.append(None if relationship_set is None else bool(matched_relationships))
        if not checks:
            checks.append(False)

        if any(value is None for value in checks):
            observed = "unknown"
            effective = "unknown"
        elif all(checks):
            observed = "available"
            effective = advertised
        else:
            observed = "unavailable"
            effective = "none"
        dimensions[dimension] = {
            "advertised": advertised,
            "observed": observed,
            "effective": effective,
            "labels_any": list(required_labels),
            "relationships_any": list(required_relationships),
            "matched_labels": matched_labels,
            "matched_relationships": matched_relationships,
            "missing_labels_any": (
                list(required_labels)
                if required_labels and label_set is not None and not matched_labels else []
            ),
            "missing_relationships_any": (
                list(required_relationships)
                if required_relationships and relationship_set is not None and not matched_relationships else []
            ),
        }

    schema_status = (
        "available"
        if label_set is not None and relationship_set is not None
        else "unavailable"
        if label_set is None and relationship_set is None
        else "partial"
    )
    return {
        "contract_version": CAPABILITY_CONTRACT_VERSION,
        "schema_status": schema_status,
        "schema_fingerprint": schema_fingerprint(label_set, relationship_set),
        "dimensions": dimensions,
    }


def _generic_profile(
    name: str,
    aliases: Iterable[str],
    *,
    relationships: Tuple[str, ...] = GENERIC_RELATIONSHIPS,
    backend: str = "cplus",
    support_level: str = "generic",
    features: FrozenSet[str] = GENERIC_FEATURES,
) -> FrameworkQueryConfig:
    return FrameworkQueryConfig(
        name=name,
        aliases=frozenset(aliases),
        backend=backend,
        support_level=support_level,
        labels=GENERIC_LABELS,
        relationships=relationships,
        searchable_properties=GENERIC_SEARCHABLE_PROPERTIES,
        features=features,
    )


CAPABILITIES: Dict[str, FrameworkQueryConfig] = {
    "android": _generic_profile(
        "android",
        {"android", "android-kotlin", "kotlin-android"},
        relationships=ANDROID_RELATIONSHIPS,
        backend="android",
        support_level="full",
        features=GENERIC_FEATURES | frozenset({"android_queries"}),
    ),
    "cplus": FrameworkQueryConfig(
        name="cplus",
        aliases=frozenset({"cplus", "cpp", "c++", "c", "clang", "pro*c", "proc"}),
        labels=GENERIC_LABELS | frozenset({"Table", "SqlStatement"}),
        relationships=CPLUS_RELATIONSHIPS,
        searchable_properties=GENERIC_SEARCHABLE_PROPERTIES,
        support_level="full",
    ),
    "python": FrameworkQueryConfig(
        name="python",
        aliases=frozenset({"python", "py", "fastapi", "django", "flask"}),
        labels=WEB_LABELS,
        relationships=WEB_RELATIONSHIPS,
        searchable_properties=WEB_SEARCHABLE_PROPERTIES,
        support_level="partial",
        support={"symbols": "full", "calls": "partial", "endpoints": "partial", "database": "none"},
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "javascript": FrameworkQueryConfig(
        name="javascript",
        aliases=frozenset({"javascript", "js", "node", "nodejs", "express", "express.js"}),
        labels=WEB_LABELS,
        relationships=WEB_RELATIONSHIPS,
        searchable_properties=WEB_SEARCHABLE_PROPERTIES,
        support_level="partial",
        support={"symbols": "full", "calls": "partial", "endpoints": "partial", "database": "none"},
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "typescript": FrameworkQueryConfig(
        name="typescript",
        aliases=frozenset({"typescript", "ts", "tsx", "nestjs", "nest.js"}),
        labels=WEB_LABELS,
        relationships=WEB_RELATIONSHIPS,
        searchable_properties=WEB_SEARCHABLE_PROPERTIES,
        support_level="full",
        support={"symbols": "full", "calls": "full", "endpoints": "none", "database": "none"},
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "php": FrameworkQueryConfig(
        name="php",
        aliases=frozenset({"php", "laravel", "symfony"}),
        labels=WEB_LABELS,
        relationships=WEB_RELATIONSHIPS,
        searchable_properties=WEB_SEARCHABLE_PROPERTIES,
        support_level="partial",
        support={"symbols": "full", "calls": "partial", "endpoints": "partial", "database": "none"},
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "csharp": _generic_profile(
        "csharp", {"csharp", "c#", "cs", "dotnet", ".net"},
        support_level="full",
    ),
    "sql": FrameworkQueryConfig(
        name="sql",
        aliases=frozenset({"sql"}),
        labels=DATABASE_LABELS,
        relationships=DATABASE_RELATIONSHIPS,
        searchable_properties=DATABASE_SEARCHABLE_PROPERTIES,
        support_level="full",
        support={"symbols": "full", "calls": "none", "database": "full", "endpoints": "none"},
        features=GENERIC_FEATURES | frozenset({"database_queries"}),
    ),
    "plsql": FrameworkQueryConfig(
        name="plsql",
        aliases=frozenset({"plsql", "pl/sql", "oracle-plsql"}),
        labels=DATABASE_LABELS,
        relationships=DATABASE_RELATIONSHIPS,
        searchable_properties=DATABASE_SEARCHABLE_PROPERTIES,
        support_level="full",
        support={"symbols": "full", "calls": "none", "database": "full", "endpoints": "none"},
        features=GENERIC_FEATURES | frozenset({"database_queries"}),
    ),
    "jvm": _generic_profile("jvm", {"jvm", "java", "kotlin"}, relationships=ANDROID_RELATIONSHIPS),
    "go": _generic_profile("go", {"go"}),
    "perl": _generic_profile("perl", {"perl"}),
    "shell": _generic_profile("shell", {"shell", "sh", "bash", "posix-shell"}),
    "jp1": _generic_profile("jp1", {"jp1", "ajs", "jobnet"}),
    "batchconfig": _generic_profile("batchconfig", {"ini", "batchconfig", "config"}),
    "rust": _generic_profile("rust", {"rust"}),
    "swift": _generic_profile("swift", {"swift"}),
    "delphi": _generic_profile("delphi", {"delphi", "pascal"}),
    "vbnet": _generic_profile("vbnet", {"vbnet"}),
    "visual_basic": _generic_profile(
        "visual_basic", {"visual_basic", "vb6", "vba", "vbscript"}
    ),
    "cobol": FrameworkQueryConfig(
        name="cobol",
        aliases=frozenset({"cobol", "cobol85", "ibm-cobol", "gnucobol"}),
        labels=GENERIC_LABELS | frozenset({
            "CobolProgram", "CobolSection", "CobolParagraph", "CobolDataItem",
            "CobolCopybook", "CobolFile", "CobolSqlStatement", "CobolCicsCommand",
        }),
        relationships=(
            "DEFINES", "INCLUDES", "REFERENCES", "CALLS", "PERFORMS",
            "PERFORMS_THRU", "RETURNS", "GOES_TO", "GOES_TO_DYNAMIC",
            "FALLS_THROUGH", "ALTERS", "CONDITIONAL", "EXITS", "READS", "WRITES",
        ),
        searchable_properties=(
            "name", "qualified_name", "file_path", "path", "raw_text",
            "operation", "assignment", "picture", "storage",
        ),
        features=FRAMEWORK_FEATURES,
    ),
    "spring": FrameworkQueryConfig(
        name="spring",
        aliases=frozenset({"spring", "spring-boot", "spring_boot"}),
        labels=GENERIC_LABELS | frozenset({
            "SpringModule", "SpringApplication", "SpringConfiguration", "SpringBean",
            "JpaEntity", "TransactionBoundary", "MessageDestination", "ScheduledTask",
            "AsyncBoundary", "ApplicationEvent", "SecurityFilterChain", "SecurityRule",
            "Authority", "Aspect", "Advice", "Pointcut", "ValidationConstraint",
            "CacheRegion", "CacheOperation", "ApiEndpoint", "Controller", "Service",
            "DataRepository", "Database", "Middleware", "MessageEndpoint",
        }),
        relationships=(
            "SEMANTIC_OF", "HANDLES", "DECLARES_QUERY", "DERIVES_QUERY",
            "MANAGES_ENTITY", "RELATES_TO_ENTITY", "APPLIES_TO", "PROTECTS",
            "QUERIES", "IMPLEMENTS_REPOSITORY", "CONSUMES_FROM", "PUBLISHES_TO",
            "PUBLISHES_EVENT", "LISTENS_TO", "EXECUTES_ASYNC", "RUNS",
        ),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "raw_value", "resolved_value",
        ))),
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "servlet_jsp": FrameworkQueryConfig(
        name="servlet_jsp",
        aliases=frozenset({"servlet_jsp", "servlet-jsp", "servlet", "jsp"}),
        labels=GENERIC_LABELS | frozenset({
            "ServletJspModule", "WebDescriptor", "Servlet", "ServletMapping", "Filter",
            "FilterMapping", "Listener", "JSPView", "JspTag", "JspExpression", "ApiEndpoint",
            "StateSlot", "LifecycleEvent", "SecurityConstraint", "ErrorPage", "WelcomePage",
            "Authority", "WebTarget", "WebConfiguration",
        }),
        relationships=(
            "SEMANTIC_OF", "HANDLES", "MAPS_TO", "PASSES_THROUGH", "FORWARDS_TO",
            "READS", "WRITES", "RESOLVES_TO", "USES", "DECLARES", "PROTECTS",
        ),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "raw_value", "resolved_value",
            "url_pattern", "http_method",
        ))),
        generation_scoped=True,
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "mybatis": FrameworkQueryConfig(
        name="mybatis",
        aliases=frozenset({"mybatis", "my-batis"}),
        labels=GENERIC_LABELS | frozenset({
            "MyBatisModule", "MyBatisArtifact", "MyBatisMapper", "MyBatisMapperMethod",
            "MyBatisParameter", "MyBatisJavaProperty", "MyBatisXmlDocument",
            "MyBatisStatement", "MyBatisSqlFragment", "MyBatisResultMap",
            "MyBatisResultMapping", "MyBatisInclude", "MyBatisDynamicNode", "MyBatisConfig",
            "MyBatisSqlStatement", "DatabaseTable", "DatabaseColumn", "MyBatisSqlJoin",
            "MyBatisSqlParameter", "MyBatisSqlProvider", "MyBatisSpringBridge",
            "MyBatisExtension", "MyBatisCache",
        }),
        relationships=(
            "SEMANTIC_OF", "DECLARES_METHOD", "DECLARES_STATEMENT", "BINDS_STATEMENT",
            "READS_FROM", "WRITES_TO", "REFERENCES_TABLE", "REFERENCES_COLUMN",
            "JOINS_WITH", "DEPENDS_ON_PARAMETER", "USES_RESULT_MAP", "HAS_RESULT_MAPPING",
            "MAPS_PROPERTY", "MAPS_COLUMN", "NESTED_SELECT", "HAS_ASSOCIATION",
            "HAS_COLLECTION", "EXTENDS_RESULT_MAP",
        ),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "raw_value", "resolved_value", "sql",
        ))),
        features=FRAMEWORK_FEATURES | frozenset({"persistence_queries"}),
    ),
    "struts": FrameworkQueryConfig(
        name="struts",
        aliases=frozenset({"struts", "struts2", "apache-struts", "apache_struts"}),
        labels=GENERIC_LABELS | frozenset({
            "StrutsFact", "Plugin", "Package", "Action", "HttpEndpoint", "InterceptorStack",
            "Interceptor", "Result", "ResultType", "View", "ExceptionMapping", "ValidationRule",
        }),
        relationships=(
            "CONTAINS", "EXTENDS", "MAPPED_TO", "PASSES_THROUGH", "USES_INTERCEPTOR_STACK",
            "RETURNS_RESULT", "INSTANCE_OF", "RESOLVES_TO", "CHAINS_TO", "REDIRECTS_TO",
            "HANDLES_EXCEPTION", "VALIDATES_WITH",
        ),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "route", "class_name", "method",
            "namespace", "result_type", "location", "validator_type",
        ))),
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "flutter": FrameworkQueryConfig(
        name="flutter",
        aliases=frozenset({"dart", "flutter", "flutter-dart", "flutter_dart"}),
        labels=GENERIC_LABELS,
        relationships=("CONTAINS", "IMPORTS", "EXPORTS", "EXTENDS", "CALLS"),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "package_name", "class_name",
        ))),
        features=FRAMEWORK_FEATURES,
    ),
    "aspnet_framework": FrameworkQueryConfig(
        name="aspnet_framework",
        aliases=frozenset({
            "aspnet_framework", "aspnet-framework", "asp.net-framework", "aspnetframework",
        }),
        labels=GENERIC_LABELS | frozenset({
            "HttpEndpoint", "Route", "Middleware", "Controller", "Action", "RazorPage",
            "PageHandler", "WebFormPage", "HttpHandler", "HttpModule", "Filter", "Result",
            "View", "Layout", "PartialView", "Service", "Repository", "Model", "ViewModel",
            "ValidationRule", "ConfigurationKey", "SessionState", "ApplicationEvent",
            "AuthenticationScheme", "AuthorizationPolicy",
        }),
        relationships=(
            "SEMANTIC_OF", "MAPPED_TO", "HANDLED_BY", "PASSES_THROUGH", "INVOKES", "INJECTS",
            "VALIDATES_WITH", "RENDERS", "REDIRECTS_TO", "FORWARDS_TO", "LOADS_FROM",
            "DEPENDS_ON", "READS_CONFIG", "WRITES_SESSION", "POSTS_BACK_TO", "INITIALIZES",
            "RETURNS_RESULT",
        ),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "route", "http_method", "config_key",
            "resolution_status", "framework",
        ))),
        generation_scoped=True,
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
    "aspnet_core": FrameworkQueryConfig(
        name="aspnet_core",
        aliases=frozenset({"aspnet_core", "aspnet-core", "asp.net-core", "aspnetcore"}),
        labels=GENERIC_LABELS | frozenset({
            "HttpEndpoint", "Route", "Middleware", "Controller", "Action", "RazorPage",
            "PageHandler", "WebFormPage", "HttpHandler", "HttpModule", "Filter", "Result",
            "View", "Layout", "PartialView", "Service", "Repository", "Model", "ViewModel",
            "ValidationRule", "ConfigurationKey", "SessionState", "ApplicationEvent",
            "AuthenticationScheme", "AuthorizationPolicy",
        }),
        relationships=(
            "SEMANTIC_OF", "MAPPED_TO", "HANDLED_BY", "PASSES_THROUGH", "INVOKES", "INJECTS",
            "VALIDATES_WITH", "RENDERS", "REDIRECTS_TO", "FORWARDS_TO", "LOADS_FROM",
            "DEPENDS_ON", "READS_CONFIG", "WRITES_SESSION", "POSTS_BACK_TO", "INITIALIZES",
            "RETURNS_RESULT",
        ),
        searchable_properties=tuple(dict.fromkeys((
            *GENERIC_SEARCHABLE_PROPERTIES, "route", "http_method", "config_key",
            "resolution_status", "framework", "position", "lifetime",
        ))),
        generation_scoped=True,
        features=FRAMEWORK_FEATURES | frozenset({"endpoint_queries"}),
    ),
}


def validate_capability_registry(
    capabilities: Optional[Mapping[str, FrameworkQueryConfig]] = None,
) -> Dict[str, FrameworkQueryConfig]:
    """Validate registry invariants and return its deterministic alias index."""

    registry = capabilities or CAPABILITIES
    aliases: Dict[str, FrameworkQueryConfig] = {}
    errors = []
    for key, config in registry.items():
        if key != config.name:
            errors.append(f"Registry key '{key}' must equal canonical name '{config.name}'.")
        if config.name not in config.aliases:
            errors.append(f"Canonical parser '{config.name}' must be one of its aliases.")
        if config.backend not in _ALLOWED_BACKENDS:
            errors.append(f"Parser '{config.name}' references unsupported backend '{config.backend}'.")
        if config.support_level not in _ALLOWED_SUPPORT_LEVELS:
            errors.append(f"Parser '{config.name}' has invalid support level '{config.support_level}'.")
        if set(config.support) != set(SUPPORT_DIMENSIONS):
            errors.append(
                f"Parser '{config.name}' must declare support for {SUPPORT_DIMENSIONS}."
            )
        invalid_support = {
            key: value
            for key, value in config.support.items()
            if value not in _ALLOWED_DIMENSION_LEVELS
        }
        if invalid_support:
            errors.append(
                f"Parser '{config.name}' has invalid dimensional support {invalid_support}."
            )
        identifiers = (
            *config.labels,
            *config.relationships,
            *config.searchable_properties,
            *(relationship for values in config.default_query_profiles.values() for relationship in values),
        )
        invalid = sorted({value for value in identifiers if not _IDENTIFIER_RE.match(value)})
        if invalid:
            errors.append(f"Parser '{config.name}' has invalid graph identifiers: {invalid}.")
        for alias in config.aliases:
            previous = aliases.get(alias)
            if previous and previous.name != config.name:
                errors.append(
                    f"Parser alias '{alias}' collides between '{previous.name}' and '{config.name}'."
                )
            aliases[alias] = config
    if errors:
        raise ValueError("Invalid MCP capability registry: " + " ".join(errors))
    return aliases


_ALIAS_INDEX = validate_capability_registry()
FRAMEWORKS: Dict[str, FrameworkQueryConfig] = {
    name: config
    for name, config in CAPABILITIES.items()
    if "framework_query" in config.features
}


def capability_for_parser(parser_type: Optional[str]) -> Optional[FrameworkQueryConfig]:
    parser = str(parser_type or "").strip().lower()
    return _ALIAS_INDEX.get(parser)


def framework_for_parser(parser_type: Optional[str]) -> Optional[FrameworkQueryConfig]:
    config = capability_for_parser(parser_type)
    return config if config and "framework_query" in config.features else None


def parser_aliases(backend: Optional[str] = None) -> FrozenSet[str]:
    backend_name = str(backend or "").strip().lower()
    return frozenset(
        alias
        for alias, config in _ALIAS_INDEX.items()
        if not backend_name or config.backend == backend_name
    )


def searchable_labels(parser_type: Optional[str] = None) -> Tuple[str, ...]:
    if parser_type:
        config = capability_for_parser(parser_type)
        return tuple(sorted(config.labels)) if config else ()
    return tuple(sorted({label for config in CAPABILITIES.values() for label in config.labels}))


def searchable_properties(parser_type: Optional[str] = None) -> Tuple[str, ...]:
    if parser_type:
        config = capability_for_parser(parser_type)
        return config.searchable_properties if config else ()
    return tuple(dict.fromkeys(
        prop for config in CAPABILITIES.values() for prop in config.searchable_properties
    ))


def default_relationships(
    parser_type: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> Tuple[str, ...]:
    config = capability_for_parser(parser_type)
    return config.relationships_for(tool_name) if config else CORE_RELATIONSHIPS


def capability_catalog() -> Tuple[Dict[str, object], ...]:
    return tuple(CAPABILITIES[name].to_dict() for name in sorted(CAPABILITIES))


def servlet_active_generation_predicate(alias: str) -> str:
    provider = (
        os.environ.get("CODE_GRAPH_PROVIDER")
        or os.environ.get("GRAPH_PROVIDER")
        or "falkordb"
    ).strip().lower()
    if provider in {"falkor", "falkordb", "falkor-db"}:
        # FalkorDB cleanup removes inactive generations during promotion.
        return "true"
    return (
        f"(coalesce({alias}.framework, '') <> 'servlet_jsp' OR EXISTS {{ "
        f"MATCH (state:ServletJspAnalysisState {{project_id: {alias}.project_id, module_id: {alias}.module_id}}) "
        f"WHERE state.active_generation = {alias}.generation_id }})"
    )


__all__ = [
    "ANDROID_RELATIONSHIPS", "CAPABILITIES", "CAPABILITY_CONTRACT_VERSION",
    "CORE_RELATIONSHIPS", "CPLUS_RELATIONSHIPS",
    "FRAMEWORKS", "PUBLIC_QUERY_ENGINES", "SUPPORT_DIMENSIONS", "FrameworkQueryConfig",
    "capability_catalog", "capability_for_parser", "capability_schema_contract",
    "evaluate_capability_schema", "query_engine_for_backend", "schema_fingerprint",
    "default_relationships", "framework_for_parser", "parser_aliases", "searchable_labels",
    "searchable_properties", "servlet_active_generation_predicate", "validate_capability_registry",
]

"""Typed schema compilation for the LadybugDB embedded graph provider.

LadybugDB (Kuzu lineage) is schema-first: ``CREATE NODE TABLE`` /
``CREATE REL TABLE`` must run before the first write.  This module compiles
the property inventory of the writer layer into DDL statements using the
hybrid strategy selected in the LadybugDB provider plan:

* well-known properties become typed columns (queryable, PK-indexed);
* every table carries a ``_properties STRING`` JSON spill column for
  heterogeneous writer payloads;
* relationship tables declare every endpoint label pair the writers use.

The inventory was produced by the Phase 0 spike (see
``docs/plans/260916-ladybugdb-graph-provider/phase-00-spike-report.md``).
Unknown labels encountered at runtime fall back to the default node shape;
unknown relationship endpoints are created on demand by the driver.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tools.graph.schema.manifest import validate_cypher_identifier


SPILL_PROPERTY = "_properties"

# Property-name driven column types shared by every table.
_INT64_PROPERTIES = frozenset(
    {
        "start_line",
        "end_line",
        "start_byte",
        "end_byte",
        "arity",
        "line_number",
        "count",
        "chunk_index",
        "start_char",
        "end_char",
        "order",
        "depth",
    }
)
_DOUBLE_PROPERTIES = frozenset({"confidence", "cohesion_score", "coupling_score", "score"})
_BOOL_PROPERTIES = frozenset(
    {
        "exported",
        "is_public_api",
        "external",
        "builtin",
        "is_global",
        "is_shared",
        "topology_owned",
        "active",
        "is_public",
        "is_abstract",
        "generated",
        "redacted",
        "secret_bearing",
        "canonical",
    }
)
# String-typed columns whose values may be lists/dicts; they are JSON-encoded
# on write and decoded on read to preserve FalkorDB round-trip shapes.
_JSON_PROPERTIES = frozenset(
    {
        "imports",
        "exports",
        "jsx_tags",
        "jsx_components",
        "members",
        "base_interfaces",
        "parameters",
        "param_schema",
        "props",
        "coordinate",
        "config",
        "fingerprints",
        "call_data",
    }
)


# Columns that are genuine LIST(STRING) values: queries filter with
# ``x IN coalesce(n.frameworks, [])`` which cannot work against JSON strings.
_LIST_PROPERTIES = frozenset(
    {
        "frameworks",
        "build_systems",
        "languages",
        "source_roots",
    }
)


def column_type(property_name: str) -> str:
    """Return the LadybugDB column type for a well-known property name."""

    if property_name in _LIST_PROPERTIES:
        return "STRING[]"
    if property_name in _INT64_PROPERTIES:
        return "INT64"
    if property_name in _DOUBLE_PROPERTIES:
        return "DOUBLE"
    if property_name in _BOOL_PROPERTIES:
        return "BOOL"
    return "STRING"


# Core columns shared by nearly every node label written by language_writer.
CORE_NODE_COLUMNS: Tuple[str, ...] = (
    "name",
    "file_path",
    "start_line",
    "end_line",
    "code",
    "comment",
    "summary",
    "note",
    "project_id",
    "project_id_normalized",
    "project_name",
    "language",
    "repo",
    "build_system",
    "updated_at",
)

# Framework writers (spring/mybatis/aspnet/servlet-jsp) stamp a fixed set of
# routing columns next to fully dynamic payloads.
FRAMEWORK_NODE_COLUMNS: Tuple[str, ...] = (
    "name",
    "symbol_id",
    "semantic_id",
    "module_id",
    "framework",
    "generation_id",
    "project_id",
    "project_id_normalized",
    "project_name",
    "language",
    "repo",
    "build_system",
    "source_file",
    "updated_at",
)

# Columns carried by framework/topology relationship payloads. ``id`` is the
# rel merge key used by the aspnet/servlet-jsp/database-schema/topology
# writers (``MERGE (a)-[r:TYPE {id: row.id}]->(b)``).
CORE_REL_COLUMNS: Tuple[str, ...] = (
    "id",
    "site_id",
    "call_type",
    "count",
    "confidence",
    "resolution_status",
    "resolution_class",
    "source_file",
    "file_path",
    "start_line",
    "end_line",
    "order",
    "dialect",
    "framework",
    "semantic_id",
    "module_id",
    "generation_id",
    "symbol_id",
    "topology_owner",
    "project_id",
    "project_id_normalized",
    "updated_at",
)


def _core(extend: Sequence[str] = ()) -> List[str]:
    return list(CORE_NODE_COLUMNS) + [c for c in extend if c not in CORE_NODE_COLUMNS]


# label -> (pk_property, extra columns beyond CORE_NODE_COLUMNS + pk)
# PK overrides follow the CODE_GRAPH_SCHEMA identity registry.
_NODE_SPECS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "File": ("id", ("path", "node_type", "imports", "exports", "jsx_tags",
                    "jsx_components", "package_name")),
    "Project": ("project_id", ("node_type", "root", "qualified_name", "slug", "created_at", "id")),
    "Package": ("id", ("package_name", "qualified_name")),
    "Namespace": ("id", ("qualified_name", "package_name")),
    "Class": (
        "id",
        ("node_type", "qualified_name", "kind", "package_name", "visibility", "is_public_api",
         "visibility_source", "export_evidence", "signature", "module_id"),
    ),
    "Type": ("id", ("qualified_name", "kind", "package_name", "exported", "module_id")),
    "Function": (
        "id",
        ("node_type", "qualified_name", "kind", "class_name", "package_name", "scope_name",
         "start_byte", "end_byte", "arity", "exported", "visibility", "is_public_api",
         "visibility_source", "export_evidence", "signature", "external", "builtin",
         "react_role", "middleware_kind", "module_id", "symbol_id"),
    ),
    "Property": ("id", ("qualified_name", "kind", "scope_name", "class_name", "package_name",
                        "parameters", "return_type", "exported")),
    "Event": ("id", ("qualified_name", "kind", "scope_name", "class_name", "package_name",
                     "parameters", "exported", "namespace", "payload_example", "payload_schema",
                     "payload_type", "payload_version", "source", "version")),
    "Interface": ("id", ("qualified_name", "kind", "base_interfaces", "module_id")),
    "Enum": ("id", ("qualified_name", "kind", "scope_name", "class_name", "package_name", "members")),
    "Constant": ("id", ("qualified_name", "kind", "scope_name", "class_name", "package_name",
                        "line_number", "value", "type_name")),
    "Variable": ("id", ("qualified_name", "kind", "scope_name", "class_name", "package_name",
                        "line_number", "type_name", "is_global", "is_shared")),
    "FunctionType": ("id", ("type_signature",)),
    "Field": ("id", ("qualified_name", "scope_name", "type_signature")),
    "Alias": ("id", ("qualified_name", "kind", "target_name")),
    "Template": ("id", ()),
    "Navigator": ("id", ("var_name", "nav_type", "factory", "param_list_ref")),
    "RouteParam": ("id", ("param_list_name", "route", "type_str")),
    "Workflow": ("workflow_id", ("name", "domain", "description", "confidence", "entrypoint_id",
                                 "language", "project", "kind", "id")),
    "CallSite": ("site_id", ("id",)),
    "BuildConfiguration": ("config_fingerprint", ("project_id", "project_id_normalized", "tu_key", "id")),
    "SemanticCoverage": ("fingerprint", ("tu_key", "project_id", "project_id_normalized", "id")),
    "Repository": ("name", ("node_type", "project_id", "project_id_normalized", "id", "created_at")),
    "AspNetAnalysisState": ("id", ("project_id", "module_id", "framework", "active_generation",
                                   "snapshot_checksum", "coverage_status")),
    "ServletJspAnalysisState": ("id", ("project_id", "module_id", "framework", "active_generation",
                                       "snapshot_checksum", "coverage_status")),
    "ProjectModule": ("id", ("qualified_name", "path", "build_file", "topology_owned", "module_path",
                        "kind", "confidence", "diagnostics", "frameworks", "build_systems",
                        "languages", "source_roots")),
    "BuildDescriptor": ("id", ("topology_owned", "module_path", "canonical", "descriptor_type",
                           "diagnostics", "framework", "frameworks", "freshness", "generated",
                           "parse_depth", "parser", "redacted", "role", "secret_bearing", "status")),
    "Dependency": ("id", ("coordinate", "kind", "topology_owned")),
    "GrpcEndpoint": ("id", ("topology_owned", "module_path")),
    "GrpcService": ("id", ("topology_owned", "module_path")),
    "FrameworkInstance": ("id", ("topology_owned", "module_path", "confidence", "diagnostics",
                              "dimensions", "evidence", "facts", "framework", "version")),
    "GradleModule": ("id", ("application_id", "module_path", "module_type", "namespace")),
    "GradleDependency": ("id", ("artifact", "coordinate", "group", "version")),
    "Directory": ("id", ("path", "depth")),
    "AndroidManifest": ("id", ("package_name", "path", "module_path")),
    "AndroidComponent": ("id", ("class_name", "component_type", "direct_boot_aware",
                                "enabled", "exported", "intent_actions", "intent_categories",
                                "intent_data", "path", "permission", "process",
                                "target_activity")),
    "AndroidHandlerMessage": ("id", ("token",)),
    "AndroidIntentAction": ("id", ("action",)),
    "AndroidNavRoute": ("id", ("route",)),
    "AndroidResource": ("id", ("qualifier", "res_type")),
    "InfraNode": ("id", ("type", "description", "module_path", "cohesion_score", "coupling_score",
                         "status", "created_at")),
    "Document": ("id", ("title", "name", "content", "doc_type", "created_at",
                        "project_id", "project_id_normalized")),
    # Composite merge key {source_id, paragraph_id}: LadybugDB requires a
    # single-column PRIMARY KEY, so the driver injects a synthetic __pk
    # ("{source_id}::{paragraph_id}") into Paragraph MERGE patterns.
    "Paragraph": (
        "__pk",
        ("source_id", "paragraph_id", "text", "short", "created_at",
         "project_id", "project_id_normalized", "id"),
    ),
    "Entity": ("id", ("name", "type", "name_norm", "project_id", "project_id_normalized")),
}

# label -> merge fields whose concatenation forms the synthetic __pk column.
_COMPOSITE_MERGE_KEYS: Dict[str, Tuple[str, str]] = {
    "Paragraph": ("source_id", "paragraph_id"),
}

# Framework writer label universes: dynamic payloads on the framework column set.
_FRAMEWORK_LABEL_SETS: Tuple[Tuple[str, ...], ...] = (
    # spring_writer.SPRING_NODE_LABELS
    ("SpringModule", "SpringApplication", "SpringConfiguration", "SpringBean", "JpaEntity",
     "TransactionBoundary", "MessageDestination", "ScheduledTask", "AsyncBoundary",
     "ApplicationEvent", "SecurityFilterChain", "SecurityRule", "Authority", "Aspect", "Advice",
     "Pointcut", "ValidationConstraint", "CacheRegion", "CacheOperation", "ApiEndpoint",
     "Controller", "Service", "DataRepository", "Database", "Middleware", "MessageEndpoint"),
    # mybatis_writer.MYBATIS_NODE_LABELS
    ("MyBatisModule", "MybatisModule", "MyBatisMapper", "MyBatisStatement", "MyBatisResultMap",
     "MyBatisSql", "MyBatisParam", "MyBatisSelect", "MyBatisInsert", "MyBatisUpdate",
     "MyBatisDelete", "MyBatisProvider", "MyBatisAssociation", "MyBatisCollection",
     "MyBatisDiscriminator", "MyBatisTypeHandler", "MyBatisDataSource", "MyBatisTransactionManager",
     "MyBatisPlugin", "MyBatisCache", "DatabaseTable", "DatabaseColumn"),
    # aspnet models.ASPNET_NODE_LABELS
    ("HttpEndpoint", "Route", "Middleware", "Controller", "Action", "RazorPage", "PageHandler",
     "WebFormPage", "HttpHandler", "HttpModule", "Filter", "Result", "View", "Layout",
     "PartialView", "Service", "Repository", "Model", "ViewModel", "ValidationRule",
     "ConfigurationKey", "SessionState", "ApplicationEvent", "AuthenticationScheme",
     "AuthorizationPolicy"),
    # servlet_jsp_writer.SERVLET_JSP_NODE_LABELS
    ("ServletJspModule", "WebDescriptor", "Servlet", "Filter", "Listener", "ServletMapping",
     "FilterMapping", "JSPView", "JspExpression", "JspTag", "StateSlot", "LifecycleEvent",
     "ErrorPage", "WelcomePage", "SecurityConstraint", "Authority", "WebTarget",
     "WebConfiguration"),
    # web_framework / database_schema / topology writers
    ("ApiEndpoint2",),  # placeholder replaced below; ApiEndpoint already covered by aspnet set
)

# rel_type -> (endpoint pairs, columns beyond CORE_REL_COLUMNS + spill)
_ENDPOINT_PAIRS = Tuple[Tuple[str, str], ...]
_REL_SPECS: Dict[str, Tuple[_ENDPOINT_PAIRS, Tuple[str, ...]]] = {
    "HAS_FILE": ((("Repository", "File"),), ()),
    "CALLS": ((("Function", "Function"),), ()),
    "POSSIBLE_CALLS": ((("Function", "Function"),), ()),
    "HAS_ROUTE": ((("Navigator", "Function"),), ("param_schema", "name")),
    "HAS_STEP": (
        (("Workflow", "Function"), ("File", "Function"), ("ProjectModule", "Function")),
        (),
    ),
    "HAS_PARAGRAPH": ((("Document", "Paragraph"),), ()),
    "HAS_ENTITY": ((("Paragraph", "Entity"),),
                   (("Document", "Entity"),)),
    "RELATED": ((("Entity", "Entity"),), ("type", "source_id", "paragraph_id")),
    "CONTAINS": (
        (("File", "Function"), ("File", "Class"), ("File", "Namespace"),
         ("Project", "ProjectModule"), ("Project", "File"),
         ("ProjectModule", "Class"), ("ProjectModule", "Function"), ("ProjectModule", "Type"),
         ("ProjectModule", "Interface"), ("ProjectModule", "Namespace"), ("ProjectModule", "File"),
         ("ProjectModule", "ApiEndpoint"), ("ProjectModule", "HttpEndpoint"),
         ("ProjectModule", "Route"), ("ProjectModule", "ControllerAction"),
         ("ProjectModule", "ServletEndpoint"), ("ProjectModule", "AndroidManifest"),
         ("ProjectModule", "AndroidComponent"), ("ProjectModule", "AndroidResource"),
         ("Package", "File"),
         ("Class", "Function"), ("Class", "Class"), ("Namespace", "Class"),
         ("Namespace", "Namespace"), ("Package", "Class"), ("Package", "Function"),
         ("Package", "Namespace"), ("Package", "Type"), ("Package", "Interface"),
         ("Package", "Enum"), ("Package", "Constant"), ("Package", "Variable"),
         ("Package", "Property"), ("Package", "Event")),
        (),
    ),
    "SAME_MODULE": ((("ProjectModule", "ProjectModule"),), ()),
    "HAS_DESCRIPTOR": ((("Project", "BuildDescriptor"), ("ProjectModule", "BuildDescriptor")), ()),
    "DEPENDS_ON": ((("ProjectModule", "Dependency"), ("Dependency", "Dependency"),
                    ("ProjectModule", "ProjectModule")), ("scope",)),
    "EXPOSES_ENDPOINT": ((("ProjectModule", "GrpcEndpoint"), ("GrpcService", "GrpcEndpoint")), ()),
    "DECLARES_SERVICE": ((("ProjectModule", "GrpcService"),), ()),
    "HAS_RPC": ((("GrpcService", "GrpcEndpoint"),), ()),
    "USES_FRAMEWORK": ((("ProjectModule", "FrameworkInstance"), ("Project", "FrameworkInstance")), ()),
    "EXPOSES_API": ((("ProjectModule", "ApiEndpoint"), ("ProjectModule", "HttpEndpoint"),
                     ("ProjectModule", "Route"), ("ProjectModule", "ControllerAction"),
                     ("ProjectModule", "ServletEndpoint"),
                     ("ProjectModule", "Function"), ("ProjectModule", "Class"),
                     ("ProjectModule", "Type"), ("ProjectModule", "Interface"),
                     ("ProjectModule", "Enum"), ("ProjectModule", "Variable"),
                     ("ProjectModule", "Constant"), ("ProjectModule", "Property"),
                     ("ProjectModule", "Event")), ()),
    "HANDLED_BY": ((("ApiEndpoint", "Function"), ("HttpEndpoint", "Function"),
                    ("Route", "Function"), ("ServletEndpoint", "Function"),
                    ("ControllerAction", "Function"),
                    ("ApiEndpoint", "Class"), ("HttpEndpoint", "Class"),
                    ("Route", "Class"), ("ServletEndpoint", "Class")), ()),
    "HAS_CALLSITE": ((("Function", "CallSite"),), ("evidence_id",)),
    "RESOLVES_TO": ((("CallSite", "Function"),), ("evidence_id",)),
    "OBSERVED_AS": ((("Function", "CallSite"),), ("evidence_id",)),
    "IN_CONFIGURATION": ((("Function", "BuildConfiguration"), ("BuildConfiguration", "File")), ()),
    "MAPS_TO_SOURCE": ((("CallSite", "File"), ("BuildConfiguration", "File")), ()),
    "EXECUTES_SQL": ((("Function", "DatabaseTable"),), ("statement_id",)),
    "RESOLVES_HOST_DECLARATION": ((("CallSite", "Function"),), ("declaration_id",)),
    "HAS_REPOSITORY": ((("Project", "Repository"),), ()),
    "BELONGS_TO_PACKAGE": (
        (("Class", "Package"), ("Function", "Package"), ("Namespace", "Package"),
         ("Type", "Package"), ("Interface", "Package"), ("Enum", "Package"),
         ("Constant", "Package"), ("Variable", "Package"), ("Property", "Package"),
         ("Event", "Package"), ("Package", "Package")),
        ("created_at",),
    ),
    "BELONGS_TO_CLASS": ((("Function", "Class"), ("Property", "Class"), ("Event", "Class"),
                          ("Field", "Class"), ("Alias", "Class")), ()),
    "EXTENDS": ((("Class", "Class"), ("Class", "Interface"), ("Interface", "Interface"),
                 ("Type", "Type")), ("created_at",)),
    "IMPLEMENTS": ((("Class", "Interface"), ("Interface", "Interface"), ("Type", "Interface")),
                   ("created_at",)),
    "INHERITS_FROM": ((("Class", "Class"), ("Interface", "Interface"), ("Type", "Type")),
                      ("created_at",)),
    "MIXES_IN": ((("Class", "Class"),), ("created_at",)),
    "NESTED_IN": ((("Class", "Class"), ("Namespace", "Namespace")), ("created_at",)),
    "IN_NAMESPACE": ((("Class", "Namespace"), ("Function", "Namespace"),
                      ("Namespace", "Namespace"), ("Type", "Namespace"),
                      ("Interface", "Namespace"), ("Enum", "Namespace"),
                      ("Constant", "Namespace"), ("Variable", "Namespace")), ()),
    "USES": ((("Function", "Type"), ("Class", "Type"), ("Function", "Function"),
              ("Class", "Class"), ("Function", "Variable"), ("Function", "Constant")), ()),
    "USES_TYPE": ((("Function", "Type"), ("Class", "Type"), ("Type", "Type")), ()),
    "POINTER_TO": ((("Type", "Type"),), ()),
    "REFERENCE_TO": ((("Type", "Type"),), ()),
    "ALIAS_OF": ((("Alias", "Type"), ("Alias", "Alias")), ()),
    "DOCUMENTED_BY": ((("Function", "Document"), ("Class", "Document"), ("File", "Document")), ()),
    "IMPLEMENTS_LOGIC": ((("Function", "Document"),), ()),
    "SIMILAR_TO": ((("Function", "Function"),), ()),
    "BELONGS_TO": ((("InfraNode", "InfraNode"),), ("created_at",)),
    "HANDLES": ((("ApiEndpoint", "Function"), ("ApiEndpoint", "Class"),
                 ("HttpEndpoint", "Function"), ("Route", "Function"),
                 ("ServletEndpoint", "Function")), ()),
    "SEMANTIC_OF": ((("Function", "ApiEndpoint"), ("Class", "ApiEndpoint")), ()),
    "READS_FROM": ((("Function", "DatabaseTable"), ("Class", "DatabaseTable")), ()),
    "WRITES_TO": ((("Function", "DatabaseTable"),), ()),
    "REFERENCES_TABLE": ((("Function", "DatabaseTable"),), ()),
    "HAS_ATTRIBUTE": ((("DatabaseTable", "DatabaseColumn"),), ()),
}

# Spring (39), mybatis (18), aspnet (17) and servlet-jsp (19) relationship
# types all connect nodes inside the same framework family; they share the
# generic framework columns and accept on-demand endpoints via the driver's
# self-healing DDL.
_GENERIC_REL_LABEL = ("__generic__", ())


def _framework_labels() -> List[str]:
    labels: List[str] = []
    for group in _FRAMEWORK_LABEL_SETS:
        for label in group:
            if label not in labels:
                labels.append(label)
    return labels


def node_spec(label: str) -> Tuple[Optional[str], List[str]]:
    """Return ``(pk_property, columns)`` for a node label.

    Unknown labels (the CODE_GRAPH_SCHEMA manifest lists ~150 identity-only
    labels) fall back to the default shape: ``id`` PK + core columns + spill.
    """

    label = validate_cypher_identifier(label, kind="node label")
    spec = _NODE_SPECS.get(label)
    if spec is not None:
        pk, extras = spec
        columns = _core(extras)
        if pk is not None and pk not in columns and pk not in ("id",):
            columns.insert(0, pk)
        return pk, columns
    if label in {group_label for group in _FRAMEWORK_LABEL_SETS for group_label in group}:
        return "id", list(FRAMEWORK_NODE_COLUMNS)
    return "id", _core()


def rel_spec(rel_type: str) -> Tuple[_ENDPOINT_PAIRS, List[str]]:
    """Return ``(endpoint_pairs, columns)`` for a relationship type."""

    rel_type = validate_cypher_identifier(rel_type, kind="relationship type")
    spec = _REL_SPECS.get(rel_type)
    if spec is not None:
        pairs, extras = spec
        columns = [c for c in CORE_REL_COLUMNS]
        for extra in extras:
            if extra not in columns:
                columns.append(extra)
        return pairs, columns
    return (), [c for c in CORE_REL_COLUMNS]


def known_rel_type(rel_type: str) -> bool:
    return validate_cypher_identifier(rel_type, kind="relationship type") in _REL_SPECS


def compile_node_ddl(label: str) -> str:
    """Compile ``CREATE NODE TABLE IF NOT EXISTS`` for *label*."""

    label = validate_cypher_identifier(label, kind="node label")
    pk, columns = node_spec(label)
    parts: List[str] = []
    if pk is not None:
        parts.append(f"`{pk}` STRING PRIMARY KEY")
    for column in columns:
        if column == pk:
            continue
        parts.append(f"`{column}` {column_type(column)}")
    parts.append(f"`{SPILL_PROPERTY}` STRING")
    body = ", ".join(parts)
    return f"CREATE NODE TABLE IF NOT EXISTS `{label}`({body})"


def compile_rel_ddl(rel_type: str, endpoints: Iterable[Tuple[str, str]]) -> str:
    """Compile ``CREATE REL TABLE IF NOT EXISTS`` for *rel_type*."""

    rel_type = validate_cypher_identifier(rel_type, kind="relationship type")
    _, columns = rel_spec(rel_type)
    pairs: List[str] = []
    for source, target in endpoints:
        source = validate_cypher_identifier(source, kind="source label")
        target = validate_cypher_identifier(target, kind="target label")
        pairs.append(f"FROM `{source}` TO `{target}`")
    if not pairs:
        raise ValueError(f"relationship table {rel_type!r} needs at least one endpoint pair")
    parts = pairs + [f"`{column}` {column_type(column)}" for column in columns]
    parts.append(f"`{SPILL_PROPERTY}` STRING")
    return f"CREATE REL TABLE IF NOT EXISTS `{rel_type}`({', '.join(parts)})"


def label_columns(label: str) -> Tuple[Optional[str], frozenset[str]]:
    """Return ``(pk, frozenset(columns))`` used by the driver to split rows."""

    pk, columns = node_spec(label)
    return pk, frozenset(columns)


def composite_merge_key(label: str) -> Optional[Tuple[str, str]]:
    """Return the composite merge fields for labels without a natural PK."""

    return _COMPOSITE_MERGE_KEYS.get(label)


def merge_key_composite_value(label: str, fields: Dict[str, Any]) -> Optional[str]:
    """Build the synthetic ``__pk`` value from a row/param mapping."""

    merge = _COMPOSITE_MERGE_KEYS.get(label)
    if merge is None:
        return None
    try:
        return "::".join(str(fields[name]) for name in merge)
    except KeyError:
        return None


__all__ = [
    "CORE_NODE_COLUMNS",
    "CORE_REL_COLUMNS",
    "FRAMEWORK_NODE_COLUMNS",
    "SPILL_PROPERTY",
    "column_type",
    "compile_node_ddl",
    "compile_rel_ddl",
    "composite_merge_key",
    "label_columns",
    "merge_key_composite_value",
    "node_spec",
    "rel_spec",
]

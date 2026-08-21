"""Validated, provider-neutral schema metadata for code graph ingestion."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple


_CYPHER_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_cypher_identifier(value: str, *, kind: str = "identifier") -> str:
    """Return a validated Cypher identifier or fail before query construction."""

    if not isinstance(value, str) or not _CYPHER_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"unsafe Cypher {kind}: {value!r}")
    return value


@dataclass(frozen=True, order=True)
class SchemaIndex:
    """One required label/property lookup index."""

    label: str
    properties: Tuple[str, ...]
    index_type: str = "range"
    entity_type: str = "node"
    required: bool = True

    def __post_init__(self) -> None:
        validate_cypher_identifier(self.label, kind="label")
        if not self.properties:
            raise ValueError(f"schema index {self.label!r} has no properties")
        for prop in self.properties:
            validate_cypher_identifier(prop, kind="property")
        if self.index_type not in {"range", "fulltext"}:
            raise ValueError(f"unsupported index type: {self.index_type!r}")
        if self.entity_type != "node":
            raise ValueError(f"unsupported index entity type: {self.entity_type!r}")

    @property
    def key(self) -> Tuple[str, Tuple[str, ...], str, str]:
        return self.label, self.properties, self.index_type, self.entity_type

    def as_driver_dict(self) -> Dict[str, Any]:
        prop: Any = self.properties[0] if len(self.properties) == 1 else list(self.properties)
        return {"label": self.label, "property": prop, "type": self.index_type}


@dataclass(frozen=True)
class GraphSchemaManifest:
    """Versioned set of indexes required by one ingestion contract."""

    name: str
    version: int
    indexes: Tuple[SchemaIndex, ...]
    # Registered relationship types with their endpoint labels.  Additive:
    # manifests that predate the registry keep an empty tuple.  The registry
    # documents identity/routing meaning only; it never authorizes writes.
    relationship_types: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...], bool], ...] = ()

    def __post_init__(self) -> None:
        validate_cypher_identifier(self.name, kind="manifest name")
        if self.version < 1:
            raise ValueError("schema manifest version must be positive")
        keys = [index.key for index in self.indexes]
        if len(keys) != len(set(keys)):
            raise ValueError(f"schema manifest {self.name!r} contains duplicate indexes")
        rel_names = [rel[0] for rel in self.relationship_types]
        if len(rel_names) != len(set(rel_names)):
            raise ValueError(f"schema manifest {self.name!r} contains duplicate relationship types")
        for rel in self.relationship_types:
            validate_cypher_identifier(rel[0], kind="relationship type")

    @property
    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "version": self.version,
            "indexes": [
                {
                    "label": index.label,
                    "properties": list(index.properties),
                    "type": index.index_type,
                    "entity_type": index.entity_type,
                    "required": index.required,
                }
                for index in sorted(self.indexes)
            ],
            "relationship_types": [
                {
                    "type": rel[0],
                    "source_labels": list(rel[1]),
                    "target_labels": list(rel[2]),
                    "endpoint_policy": rel[3],
                }
                for rel in sorted(self.relationship_types)
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def driver_indexes(
        self, indexes: Iterable[SchemaIndex] | None = None
    ) -> list[Dict[str, Any]]:
        selected = self.indexes if indexes is None else tuple(indexes)
        return [index.as_driver_dict() for index in selected]

    def has_identity_index(self, label: str, property_name: str = "id") -> bool:
        return any(
            index.label == label
            and index.properties == (property_name,)
            and index.index_type == "range"
            and index.required
            for index in self.indexes
        )


def _id_indexes(labels: Iterable[str]) -> Tuple[SchemaIndex, ...]:
    return tuple(SchemaIndex(label, ("id",)) for label in labels)


# This registry contains identities used by the shared language writer and the
# direct analyzer mutation paths. Only identity/routing properties are indexed;
# descriptive properties intentionally remain unindexed.
CODE_GRAPH_SCHEMA = GraphSchemaManifest(
    name="code_graph",
    version=2,
    indexes=(
        SchemaIndex("Project", ("project_id",)),
        SchemaIndex("Repository", ("name",)),
        SchemaIndex("Workflow", ("workflow_id",)),
        *_id_indexes(
            (
                "Alias",
                "Action",
                "Advice",
                "AndroidComponent",
                "AndroidAnnotation",
                "AndroidHandlerMessage",
                "AndroidIntentAction",
                "AndroidManifest",
                "AndroidNavRoute",
                "AndroidResource",
                "ApiEndpoint",
                "ApplicationEvent",
                "AspNetAnalysisState",
                "Aspect",
                "AsyncBoundary",
                "AuthenticationScheme",
                "Authority",
                "AuthorizationPolicy",
                "BuildConfiguration",
                "BuildDescriptor",
                "CacheOperation",
                "CacheRegion",
                "CallSite",
                "Class",
                "CobolCicsCommand",
                "CobolCopybook",
                "CobolDataItem",
                "CobolFile",
                "CobolParagraph",
                "CobolProgram",
                "CobolSection",
                "CobolSqlStatement",
                "Constant",
                "ConfigurationKey",
                "Controller",
                "DataRepository",
                "Database",
                "DatabaseColumn",
                "DatabaseTable",
                "Dependency",
                "Directory",
                "Document",
                "Enum",
                "ErrorPage",
                "Event",
                "Field",
                "File",
                "Filter",
                "FilterMapping",
                "FrameworkInstance",
                "Function",
                "FunctionType",
                "GradleDependency",
                "GradleModule",
                "GrpcEndpoint",
                "GrpcService",
                "HttpEndpoint",
                "HttpHandler",
                "HttpModule",
                "Interface",
                "InfraNode",
                "JSPView",
                "JpaEntity",
                "Jp1Unit",
                "JspExpression",
                "JspTag",
                "Layout",
                "LifecycleEvent",
                "Listener",
                "Message",
                "MessageDestination",
                "MessageEndpoint",
                "Middleware",
                "Model",
                "MyBatisArtifact",
                "MyBatisCache",
                "MyBatisConfig",
                "MyBatisDynamicNode",
                "MyBatisExtension",
                "MyBatisInclude",
                "MyBatisJavaProperty",
                "MyBatisMapper",
                "MyBatisMapperMethod",
                "MyBatisModule",
                "MyBatisParameter",
                "MyBatisResultMap",
                "MyBatisResultMapping",
                "MyBatisSpringBridge",
                "MyBatisSqlFragment",
                "MyBatisSqlJoin",
                "MyBatisSqlParameter",
                "MyBatisSqlProvider",
                "MyBatisSqlStatement",
                "MyBatisStatement",
                "MyBatisXmlDocument",
                "Namespace",
                "Navigator",
                "Package",
                "PageHandler",
                "Paragraph",
                "ParseRun",
                "PartialView",
                "Pointcut",
                "Procedure",
                "ProjectModule",
                "Property",
                "RazorPage",
                "Repository",
                "Resource",
                "Result",
                "RouteParam",
                "Route",
                "ScheduledTask",
                "SemanticCoverage",
                "SecurityConstraint",
                "SecurityFilterChain",
                "SecurityRule",
                "Service",
                "BatchProgram",
                "ShellFunction",
                "ShellInvocation",
                "ShellScript",
                "Servlet",
                "ServletJspAnalysisState",
                "ServletJspModule",
                "ServletMapping",
                "SessionState",
                "SqlCursor",
                "SqlDirective",
                "SqlHostVariable",
                "SqlStatement",
                "SpringApplication",
                "SpringBean",
                "SpringConfiguration",
                "SpringModule",
                "StateSlot",
                "Table",
                "Template",
                "TransactionBoundary",
                "Type",
                "UIControl",
                "UnknownFunction",
                "ValidationConstraint",
                "ValidationRule",
                "Variable",
                "View",
                "ViewModel",
                "WebConfiguration",
                "WebDescriptor",
                "WebFormPage",
                "WebTarget",
                "WelcomePage",
            )
        ),
        SchemaIndex("ApiEndpoint", ("symbol_id",)),
        SchemaIndex("Controller", ("symbol_id",)),
        SchemaIndex("DataRepository", ("symbol_id",)),
        SchemaIndex("Database", ("symbol_id",)),
        SchemaIndex("Middleware", ("symbol_id",)),
        SchemaIndex("Service", ("symbol_id",)),
        SchemaIndex("BuildConfiguration", ("config_fingerprint",)),
        SchemaIndex("CallSite", ("site_id",)),
        SchemaIndex("SemanticCoverage", ("tu_key",)),
    ),
    relationship_types=(
        # Semantic call-evidence staging plane (Phase 04).  ``direct`` marks
        # relationships whose endpoints are authoritative identities; the
        # evidence plane keeps provenance on every edge.
        ("HAS_CALLSITE", ("Function",), ("CallSite",), True),
        ("RESOLVES_TO", ("CallSite",), ("Function",), True),
        ("OBSERVED_AS", ("CallSite",), ("Function",), False),
        ("IN_CONFIGURATION", ("CallSite",), ("BuildConfiguration",), True),
        ("MAPS_TO_SOURCE", ("CallSite",), ("File",), False),
        # Schema-owner-approved cross-domain evidence joins.  These never
        # redefine the nine Pro*C relationships or ``BINDS_PARAMETER``.
        ("EXECUTES_SQL", ("Function",), ("SqlStatement",), False),
        ("RESOLVES_HOST_DECLARATION", ("SqlHostVariable",), ("Function", "Variable", "Field"), False),
    ),
)


__all__ = [
    "CODE_GRAPH_SCHEMA",
    "GraphSchemaManifest",
    "SchemaIndex",
    "validate_cypher_identifier",
]

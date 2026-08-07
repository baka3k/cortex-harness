"""Provider-neutral graph schema setup.

One-time (idempotent) migration script that applies the shared range,
full-text, and uniqueness schema to Neo4j or FalkorDB so concurrent MERGE
operations are safe under parallelism.

WHY THIS IS NECESSARY
---------------------
Without a uniqueness constraint, Neo4j MERGE is NOT atomic under concurrency:

    Job A: check Project {project_id: "x"} exists → NO → CREATE
    Job B: check Project {project_id: "x"} exists → NO → CREATE
    Result: two Project nodes with the same project_id!

With a UNIQUE constraint, Neo4j acquires an exclusive index lock before the
MERGE decides to CREATE.  Concurrent jobs queue at the lock and all converge
on the SAME node — no application-level mutex or distributed lock required.

USAGE
-----
Run once after initial setup or when adding a new database:

    python setup_constraints.py
    python setup_constraints.py --neo4j-uri bolt://host:7687 --neo4j-db mydb
    python setup_constraints.py --graph-provider falkordb --falkordb-graph mygraph

Environment variables (fallbacks):
    NEO4J_URI       bolt://localhost:7687
    NEO4J_USER      neo4j
    NEO4J_PASSWORD  (empty)
    NEO4J_DB        neo4j
    GRAPH_PROVIDER  neo4j or falkordb
    FALKORDB_GRAPH  selected FalkorDB graph
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    normalize_graph_provider,
    prepare_graph_args,
)
from tools.graph.core.base import GraphProvider
from tools.graph.schema import CODE_GRAPH_SCHEMA

_FERNET_TOKEN_RE = re.compile(r'^gAAAAA')


def _maybe_decrypt_password(password: str) -> str:
    if not _FERNET_TOKEN_RE.match(password):
        return password
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        return password
    enc_pw = os.environ.get("HYPER_PACK_ENCRYPTION_PASSWORD", "my-secret-encryption-key-2026")
    try:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"static_salt_2026", iterations=100_000)
        key = base64.urlsafe_b64encode(kdf.derive(enc_pw.encode("utf-8")))
        return Fernet(key).decrypt(password.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        print(f"[setup_constraints] warning: could not decrypt NEO4J_PASS ({exc})", file=sys.stderr)
        return password


try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ClientError
except ImportError:
    GraphDatabase = None

    class ClientError(Exception):
        """Fallback used when only the FalkorDB provider is installed."""


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

# Constraints to DROP before (re-)creating — handles renaming of the MERGE key.
# Uses DROP … IF EXISTS so re-running is safe.
DROP_CONSTRAINTS: list[tuple[str, str]] = [
    # Old/incorrect variants may have used p.id; we standardize on p.project_id.
    # Drop by name first so the CREATE below can enforce the canonical property.
    (
        "unique_project_id",
        "DROP CONSTRAINT unique_project_id IF EXISTS",
    ),
]

# Each tuple: (constraint_name, cypher_statement)
# All statements use IF NOT EXISTS so re-running is safe.
CONSTRAINTS: list[tuple[str, str]] = [
    (
        "unique_project_id",
        (
            "CREATE CONSTRAINT unique_project_id IF NOT EXISTS "
            "FOR (p:Project) REQUIRE p.project_id IS UNIQUE"
        ),
    ),
    (
        "unique_repository_name",
        (
            "CREATE CONSTRAINT unique_repository_name IF NOT EXISTS "
            "FOR (r:Repository) REQUIRE r.name IS UNIQUE"
        ),
    ),
    (
        "unique_navigator_id",
        (
            "CREATE CONSTRAINT unique_navigator_id IF NOT EXISTS "
            "FOR (n:Navigator) REQUIRE n.id IS UNIQUE"
        ),
    ),
    (
        "unique_route_param_id",
        (
            "CREATE CONSTRAINT unique_route_param_id IF NOT EXISTS "
            "FOR (p:RouteParam) REQUIRE p.id IS UNIQUE"
        ),
    ),
    (
        "unique_project_module_id",
        (
            "CREATE CONSTRAINT unique_project_module_id IF NOT EXISTS "
            "FOR (m:ProjectModule) REQUIRE m.id IS UNIQUE"
        ),
    ),
    (
        "unique_build_descriptor_id",
        (
            "CREATE CONSTRAINT unique_build_descriptor_id IF NOT EXISTS "
            "FOR (d:BuildDescriptor) REQUIRE d.id IS UNIQUE"
        ),
    ),
    (
        "unique_dependency_id",
        (
            "CREATE CONSTRAINT unique_dependency_id IF NOT EXISTS "
            "FOR (d:Dependency) REQUIRE d.id IS UNIQUE"
        ),
    ),
    (
        "unique_framework_instance_id",
        (
            "CREATE CONSTRAINT unique_framework_instance_id IF NOT EXISTS "
            "FOR (f:FrameworkInstance) REQUIRE f.id IS UNIQUE"
        ),
    ),
    (
        "unique_grpc_endpoint_id",
        (
            "CREATE CONSTRAINT unique_grpc_endpoint_id IF NOT EXISTS "
            "FOR (e:GrpcEndpoint) REQUIRE e.id IS UNIQUE"
        ),
    ),
]

INDEXES: list[tuple[str, str]] = [
    (
        "cobol_program_project_name_idx",
        (
            "CREATE INDEX cobol_program_project_name_idx IF NOT EXISTS "
            "FOR (n:CobolProgram) ON (n.project_id, n.name)"
        ),
    ),
    (
        "cobol_paragraph_project_name_idx",
        (
            "CREATE INDEX cobol_paragraph_project_name_idx IF NOT EXISTS "
            "FOR (n:CobolParagraph) ON (n.project_id, n.name)"
        ),
    ),
    (
        "cobol_data_item_project_name_idx",
        (
            "CREATE INDEX cobol_data_item_project_name_idx IF NOT EXISTS "
            "FOR (n:CobolDataItem) ON (n.project_id, n.name)"
        ),
    ),
    (
        "cobol_copybook_project_name_idx",
        (
            "CREATE INDEX cobol_copybook_project_name_idx IF NOT EXISTS "
            "FOR (n:CobolCopybook) ON (n.project_id, n.name)"
        ),
    ),
    (
        "type_id_lookup",
        (
            "CREATE INDEX type_id_lookup IF NOT EXISTS "
            "FOR (t:Type) ON (t.id)"
        ),
    ),
    (
        "package_id_lookup",
        (
            "CREATE INDEX package_id_lookup IF NOT EXISTS "
            "FOR (p:Package) ON (p.id)"
        ),
    ),
    (
        "field_id_lookup",
        (
            "CREATE INDEX field_id_lookup IF NOT EXISTS "
            "FOR (f:Field) ON (f.id)"
        ),
    ),
    (
        "alias_id_lookup",
        (
            "CREATE INDEX alias_id_lookup IF NOT EXISTS "
            "FOR (a:Alias) ON (a.id)"
        ),
    ),
    (
        "template_id_lookup",
        (
            "CREATE INDEX template_id_lookup IF NOT EXISTS "
            "FOR (t:Template) ON (t.id)"
        ),
    ),
    (
        "function_type_id_lookup",
        (
            "CREATE INDEX function_type_id_lookup IF NOT EXISTS "
            "FOR (ft:FunctionType) ON (ft.id)"
        ),
    ),
    (
        "message_id_lookup",
        (
            "CREATE INDEX message_id_lookup IF NOT EXISTS "
            "FOR (m:Message) ON (m.id)"
        ),
    ),
    (
        "message_endpoint_id_lookup",
        (
            "CREATE INDEX message_endpoint_id_lookup IF NOT EXISTS "
            "FOR (m:MessageEndpoint) ON (m.id)"
        ),
    ),
    (
        "infra_node_id_lookup",
        (
            "CREATE INDEX infra_node_id_lookup IF NOT EXISTS "
            "FOR (i:InfraNode) ON (i.id)"
        ),
    ),
    (
        "project_id_lookup",
        (
            "CREATE INDEX project_id_lookup IF NOT EXISTS "
            "FOR (p:Project) ON (p.id)"
        ),
    ),
    (
        "repository_id_lookup",
        (
            "CREATE INDEX repository_id_lookup IF NOT EXISTS "
            "FOR (r:Repository) ON (r.id)"
        ),
    ),
    (
        "paragraph_id_lookup",
        (
            "CREATE INDEX paragraph_id_lookup IF NOT EXISTS "
            "FOR (p:Paragraph) ON (p.id)"
        ),
    ),
    (
        "chunk_id_lookup",
        (
            "CREATE INDEX chunk_id_lookup IF NOT EXISTS "
            "FOR (c:Chunk) ON (c.id)"
        ),
    ),
    (
        "slide_id_lookup",
        (
            "CREATE INDEX slide_id_lookup IF NOT EXISTS "
            "FOR (s:Slide) ON (s.id)"
        ),
    ),
    (
        "android_manifest_id_lookup",
        (
            "CREATE INDEX android_manifest_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidManifest) ON (a.id)"
        ),
    ),
    (
        "android_component_id_lookup",
        (
            "CREATE INDEX android_component_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidComponent) ON (a.id)"
        ),
    ),
    (
        "android_resource_id_lookup",
        (
            "CREATE INDEX android_resource_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidResource) ON (a.id)"
        ),
    ),
    (
        "gradle_module_id_lookup",
        (
            "CREATE INDEX gradle_module_id_lookup IF NOT EXISTS "
            "FOR (g:GradleModule) ON (g.id)"
        ),
    ),
    (
        "android_intent_action_id_lookup",
        (
            "CREATE INDEX android_intent_action_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidIntentAction) ON (a.id)"
        ),
    ),
    (
        "android_annotation_id_lookup",
        (
            "CREATE INDEX android_annotation_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidAnnotation) ON (a.id)"
        ),
    ),
    (
        "api_endpoint_path_method_project_idx",
        (
            "CREATE INDEX api_endpoint_path_method_project_idx IF NOT EXISTS "
            "FOR (ep:ApiEndpoint) ON (ep.path, ep.http_method, ep.project_id)"
        ),
    ),
    (
        "api_endpoint_symbol_id_idx",
        (
            "CREATE INDEX api_endpoint_symbol_id_idx IF NOT EXISTS "
            "FOR (ep:ApiEndpoint) ON (ep.symbol_id)"
        ),
    ),
    (
        "api_call_symbol_id_idx",
        (
            "CREATE INDEX api_call_symbol_id_idx IF NOT EXISTS "
            "FOR (ac:ApiCall) ON (ac.symbol_id)"
        ),
    ),
    (
        "api_call_project_id_idx",
        (
            "CREATE INDEX api_call_project_id_idx IF NOT EXISTS "
            "FOR (ac:ApiCall) ON (ac.project_id)"
        ),
    ),
    (
        "controller_symbol_id_idx",
        (
            "CREATE INDEX controller_symbol_id_idx IF NOT EXISTS "
            "FOR (c:Controller) ON (c.symbol_id)"
        ),
    ),
    (
        "service_symbol_id_idx",
        (
            "CREATE INDEX service_symbol_id_idx IF NOT EXISTS "
            "FOR (s:Service) ON (s.symbol_id)"
        ),
    ),
    (
        "database_symbol_id_idx",
        (
            "CREATE INDEX database_symbol_id_idx IF NOT EXISTS "
            "FOR (d:Database) ON (d.symbol_id)"
        ),
    ),
    (
        "data_repository_symbol_id_idx",
        (
            "CREATE INDEX data_repository_symbol_id_idx IF NOT EXISTS "
            "FOR (r:DataRepository) ON (r.symbol_id)"
        ),
    ),
    (
        "message_project_id_idx",
        (
            "CREATE INDEX message_project_id_idx IF NOT EXISTS "
            "FOR (m:Message) ON (m.project_id)"
        ),
    ),
]

# Framework overlays merge by stable ``id`` and always query within a project.
# Build these definitions from allow-listed labels so the same schema contract
# is deterministic and idempotent across reruns.
SPRING_LABELS: tuple[str, ...] = (
    "SpringModule", "SpringApplication", "SpringConfiguration", "SpringBean", "JpaEntity",
    "TransactionBoundary", "MessageDestination", "ScheduledTask", "AsyncBoundary",
    "ApplicationEvent", "SecurityFilterChain", "SecurityRule", "Authority", "Aspect",
    "Advice", "Pointcut", "ValidationConstraint", "CacheRegion", "CacheOperation",
)
MYBATIS_LABELS: tuple[str, ...] = (
    "MyBatisModule", "MyBatisArtifact", "MyBatisMapper", "MyBatisMapperMethod",
    "MyBatisParameter", "MyBatisJavaProperty", "MyBatisXmlDocument", "MyBatisStatement",
    "MyBatisSqlFragment", "MyBatisResultMap", "MyBatisResultMapping", "MyBatisInclude",
    "MyBatisDynamicNode", "MyBatisConfig", "MyBatisSqlStatement", "DatabaseTable",
    "DatabaseColumn", "MyBatisSqlJoin", "MyBatisSqlParameter", "MyBatisSqlProvider",
    "MyBatisSpringBridge", "MyBatisExtension", "MyBatisCache",
)
SERVLET_JSP_LABELS: tuple[str, ...] = (
    "ServletJspModule", "WebDescriptor", "Servlet", "Filter", "Listener", "ServletMapping",
    "FilterMapping", "JSPView", "JspExpression", "JspTag", "StateSlot", "LifecycleEvent",
    "ErrorPage", "WelcomePage", "SecurityConstraint", "WebTarget", "WebConfiguration",
)
STRUTS_LABELS: tuple[str, ...] = (
    "StrutsFact", "Plugin", "Package", "Action", "HttpEndpoint", "InterceptorStack",
    "Interceptor", "Result", "ResultType", "View", "ExceptionMapping", "ValidationRule",
)
ASPNET_LABELS: tuple[str, ...] = (
    "Route", "RazorPage", "PageHandler", "WebFormPage", "HttpHandler", "HttpModule",
    "Layout", "PartialView", "Repository", "Model", "ViewModel", "ConfigurationKey",
    "SessionState", "AuthenticationScheme", "AuthorizationPolicy",
)
for _framework, _labels in (
    ("spring", SPRING_LABELS),
    ("mybatis", MYBATIS_LABELS),
    ("servlet_jsp", SERVLET_JSP_LABELS),
    ("struts", STRUTS_LABELS),
    ("aspnet", ASPNET_LABELS),
):
    for _label in _labels:
        _token = re.sub(r"(?<!^)(?=[A-Z])", "_", _label).lower()
        CONSTRAINTS.append((
            f"unique_{_framework}_{_token}_id",
            f"CREATE CONSTRAINT unique_{_framework}_{_token}_id IF NOT EXISTS FOR (n:{_label}) REQUIRE n.id IS UNIQUE",
        ))
        INDEXES.append((
            f"{_framework}_{_token}_project_lookup",
            f"CREATE INDEX {_framework}_{_token}_project_lookup IF NOT EXISTS FOR (n:{_label}) ON (n.project_id)",
        ))
        if _framework == "servlet_jsp":
            INDEXES.append((
                f"servlet_jsp_{_token}_active_lookup",
                f"CREATE INDEX servlet_jsp_{_token}_active_lookup IF NOT EXISTS FOR (n:{_label}) ON (n.project_id, n.module_id, n.generation_id)",
            ))


PROC_LABELS: tuple[str, ...] = (
    "SqlStatement", "SqlDirective", "SqlCursor",
    "SqlHostVariable", "DatabaseTable",
)
for _label in PROC_LABELS:
    _token = re.sub(r"(?<!^)(?=[A-Z])", "_", _label).lower()
    CONSTRAINTS.append((
        f"unique_proc_{_token}_id",
        f"CREATE CONSTRAINT unique_proc_{_token}_id IF NOT EXISTS FOR (n:{_label}) REQUIRE n.id IS UNIQUE",
    ))
    INDEXES.append((
        f"proc_{_token}_project_lookup",
        f"CREATE INDEX proc_{_token}_project_lookup IF NOT EXISTS FOR (n:{_label}) ON (n.project_id)",
    ))

# Normal ingestion and this repair CLI consume the same identity-index
# manifest. Specialized lookup indexes above remain additive.
for _schema_index in CODE_GRAPH_SCHEMA.indexes:
    _token = re.sub(r"(?<!^)(?=[A-Z])", "_", _schema_index.label).lower()
    _property_token = "_".join(_schema_index.properties)
    _property_expr = ", ".join(f"n.{prop}" for prop in _schema_index.properties)
    INDEXES.append((
        f"manifest_{_token}_{_property_token}_lookup",
        (
            f"CREATE INDEX manifest_{_token}_{_property_token}_lookup IF NOT EXISTS "
            f"FOR (n:{_schema_index.label}) ON ({_property_expr})"
        ),
    ))

CONSTRAINTS.append((
    "unique_servlet_jsp_analysis_state_id",
    "CREATE CONSTRAINT unique_servlet_jsp_analysis_state_id IF NOT EXISTS FOR (s:ServletJspAnalysisState) REQUIRE s.id IS UNIQUE",
))
INDEXES.append((
    "servlet_jsp_analysis_state_active_lookup",
    "CREATE INDEX servlet_jsp_analysis_state_active_lookup IF NOT EXISTS FOR (s:ServletJspAnalysisState) ON (s.project_id, s.module_id, s.active_generation)",
))
CONSTRAINTS.append((
    "unique_aspnet_analysis_state_id",
    "CREATE CONSTRAINT unique_aspnet_analysis_state_id IF NOT EXISTS FOR (s:AspNetAnalysisState) REQUIRE s.id IS UNIQUE",
))
INDEXES.append((
    "aspnet_analysis_state_active_lookup",
    "CREATE INDEX aspnet_analysis_state_active_lookup IF NOT EXISTS FOR (s:AspNetAnalysisState) ON (s.project_id, s.module_id, s.framework, s.active_generation)",
))

_CORE_FULLTEXT_LABELS = (
    "Function", "Class", "Type", "Namespace", "Package", "File", "Field", "Alias", "Template",
    "FunctionType", "Event", "Project", "Property", "Interface", "Enum", "Constant", "Variable",
    "UnknownFunction", "Message", "MessageEndpoint", "AndroidManifest", "AndroidComponent",
    "AndroidResource", "GradleModule", "GradleDependency", "AndroidAnnotation", "AndroidNavRoute",
    "AndroidIntentAction", "AndroidHandlerMessage", "ApiEndpoint", "ApiCall", "Controller", "Service",
    "Database", "DataRepository", "Middleware",
)
_FULLTEXT_LABELS = tuple(dict.fromkeys((
    *_CORE_FULLTEXT_LABELS,
    *SPRING_LABELS,
    *MYBATIS_LABELS,
    *SERVLET_JSP_LABELS,
    *STRUTS_LABELS,
    *ASPNET_LABELS,
)))
_FULLTEXT_LABEL_CYPHER = "|".join(_FULLTEXT_LABELS)

FULLTEXT_INDEXES: list[tuple[str, str]] = [
    (
        "mcp_symbol_text_ft_v2",
        (
            "CREATE FULLTEXT INDEX mcp_symbol_text_ft_v2 IF NOT EXISTS "
            f"FOR (n:{_FULLTEXT_LABEL_CYPHER}) "
            "ON EACH [n.name, n.qualified_name, n.file_path, n.path, n.package_name, n.class_name, n.module_path, "
            "n.namespace, n.application_id, n.coordinate, n.group, n.artifact, n.version, n.res_type, "
            "n.component_type, n.route, n.action, n.token, n.http_method, n.url_pattern, n.raw_url_pattern, "
            "n.raw_value, n.resolved_value, n.sql]"
        ),
    ),
    (
        "mcp_symbol_code_ft_v2",
        (
            "CREATE FULLTEXT INDEX mcp_symbol_code_ft_v2 IF NOT EXISTS "
            f"FOR (n:{_FULLTEXT_LABEL_CYPHER}) "
            "ON EACH [n.code, n.sql, n.comment, n.summary, n.note, n.payload, n.response, n.explanation]"
        ),
    ),
]

BACKFILL_PROJECT_ID = (
    "MATCH (p:Project) "
    "WHERE p.project_id IS NULL AND p.id IS NOT NULL "
    "SET p.project_id = p.id "
    "RETURN count(p) AS count"
)


_INDEX_RE = re.compile(
    r"FOR\s*\([A-Za-z_][A-Za-z0-9_]*:(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)"
    r"\s*ON\s*\((?P<properties>[^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)
_CONSTRAINT_RE = re.compile(
    r"FOR\s*\([A-Za-z_][A-Za-z0-9_]*:(?P<label>[A-Za-z_][A-Za-z0-9_]*)\)"
    r"\s*REQUIRE\s*(?P<properties>.+?)\s+IS\s+UNIQUE",
    re.IGNORECASE | re.DOTALL,
)
_FULLTEXT_RE = re.compile(
    r"FOR\s*\([A-Za-z_][A-Za-z0-9_]*:(?P<labels>[A-Za-z0-9_|]+)\)"
    r"\s*ON\s+EACH\s*\[(?P<properties>[^]]+)\]",
    re.IGNORECASE | re.DOTALL,
)
_PROPERTY_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)\b")
_CONSTRAINT_READY = {"active", "enabled", "operational", "ready"}
_CONSTRAINT_FAILED = {"error", "failed", "failure"}


def _properties_from_expression(expression: str) -> tuple[str, ...]:
    properties = tuple(dict.fromkeys(_PROPERTY_RE.findall(expression)))
    if not properties:
        raise ValueError(f"Could not extract schema properties from: {expression!r}")
    return properties


def parse_range_index(statement: str) -> tuple[str, tuple[str, ...]]:
    """Return FalkorDB label/properties from one Neo4j range-index DDL string."""
    match = _INDEX_RE.search(statement)
    if not match:
        raise ValueError(f"Unsupported range-index statement: {statement}")
    return match.group("label"), _properties_from_expression(match.group("properties"))


def parse_unique_constraint(statement: str) -> tuple[str, tuple[str, ...]]:
    """Return FalkorDB label/properties from one Neo4j uniqueness DDL string."""
    match = _CONSTRAINT_RE.search(statement)
    if not match:
        raise ValueError(f"Unsupported unique-constraint statement: {statement}")
    return match.group("label"), _properties_from_expression(match.group("properties"))


def parse_fulltext_index(statement: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Expand a Neo4j multi-label full-text DDL into FalkorDB labels/properties."""
    match = _FULLTEXT_RE.search(statement)
    if not match:
        raise ValueError(f"Unsupported full-text statement: {statement}")
    labels = tuple(label for label in match.group("labels").split("|") if label)
    return labels, _properties_from_expression(match.group("properties"))


def _schema_exists_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(token in message for token in ("already exists", "already indexed", "already constrained"))


def _normalize_constraint_properties(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        value = [value]
    normalized = []
    for item in value or []:
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="replace")
        normalized.append(str(item))
    return tuple(normalized)


def wait_for_falkordb_constraint(
    graph: Any,
    label: str,
    properties: Sequence[str],
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Poll FalkorDB's asynchronous constraint state and fail on timeout/error."""
    expected_properties = tuple(properties)
    deadline = time.monotonic() + max(timeout, 0.0)
    last_status = "not listed"
    while True:
        constraints = graph.list_constraints()
        for item in constraints:
            item_label = str(item.get("label", ""))
            item_type = str(item.get("type", "")).casefold()
            entity_type = str(item.get("entitytype", item.get("entity_type", "NODE"))).casefold()
            item_properties = _normalize_constraint_properties(item.get("properties"))
            if (
                item_label == label
                and item_type == "unique"
                and entity_type in {"node", ""}
                and item_properties == expected_properties
            ):
                last_status = str(item.get("status", "")).casefold()
                if last_status in _CONSTRAINT_READY:
                    return dict(item)
                if last_status in _CONSTRAINT_FAILED:
                    raise RuntimeError(
                        f"FalkorDB constraint {label}{expected_properties} failed with status {last_status!r}"
                    )

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for FalkorDB constraint {label}{expected_properties}; "
                f"last status: {last_status}"
            )
        time.sleep(max(interval, 0.0))


def ensure_falkordb_unique_constraint(
    graph: Any,
    label: str,
    properties: Sequence[str],
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Create prerequisite range index and an idempotent async unique constraint."""
    properties = tuple(properties)
    try:
        graph.create_node_range_index(label, *properties)
    except Exception as exc:
        if not _schema_exists_error(exc):
            raise
    try:
        graph.create_node_unique_constraint(label, *properties)
    except Exception as exc:
        if not _schema_exists_error(exc):
            raise
    return wait_for_falkordb_constraint(
        graph,
        label,
        properties,
        timeout=timeout,
        interval=interval,
    )


def apply_falkordb_schema(
    driver: Any,
    *,
    constraint_statements: Iterable[tuple[str, str]] = CONSTRAINTS,
    index_statements: Iterable[tuple[str, str]] = INDEXES,
    fulltext_statements: Iterable[tuple[str, str]] = FULLTEXT_INDEXES,
    constraint_timeout: float = 30.0,
    poll_interval: float = 0.25,
) -> dict[str, int]:
    """Apply the shared schema through FalkorDB's native, idempotent APIs."""
    graph = driver.graph
    summary = {"constraints": 0, "indexes": 0, "fulltext_indexes": 0}

    for _, statement in index_statements:
        label, properties = parse_range_index(statement)
        try:
            graph.create_node_range_index(label, *properties)
        except Exception as exc:
            if not _schema_exists_error(exc):
                raise
        summary["indexes"] += 1

    for _, statement in constraint_statements:
        label, properties = parse_unique_constraint(statement)
        ensure_falkordb_unique_constraint(
            graph,
            label,
            properties,
            timeout=constraint_timeout,
            interval=poll_interval,
        )
        summary["constraints"] += 1

    for _, statement in fulltext_statements:
        labels, properties = parse_fulltext_index(statement)
        for label in labels:
            try:
                graph.create_node_fulltext_index(label, *properties)
            except Exception as exc:
                if not _schema_exists_error(exc):
                    raise
            summary["fulltext_indexes"] += 1

    return summary


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def apply_neo4j_schema(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: str,
) -> None:
    if GraphDatabase is None:
        raise RuntimeError("Neo4j provider requires the 'neo4j' Python package")
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, _maybe_decrypt_password(neo4j_password)))
    ok = 0
    skipped = 0
    backfilled = 0
    index_ok = 0
    index_skipped = 0
    fulltext_ok = 0
    fulltext_skipped = 0

    try:
        with driver.session(database=neo4j_db) as session:
            for name, stmt in DROP_CONSTRAINTS:
                try:
                    session.run(stmt)
                    print(f"  [DROPPED] {name}")
                except ClientError as exc:
                    print(f"  [SKIP-DROP] {name}  ({exc.code})")

            try:
                backfilled = int(session.run(BACKFILL_PROJECT_ID).single()["count"])
                print(f"  [OK]      backfill_project_id ({backfilled} node(s))")
            except ClientError as exc:
                print(f"  [SKIPPED] backfill_project_id  ({exc.code})")

            for name, stmt in CONSTRAINTS:
                try:
                    session.run(stmt)
                    print(f"  [OK]      {name}")
                    ok += 1
                except ClientError as exc:
                    # Constraint already exists, or Neo4j version does not support
                    # IF NOT EXISTS syntax — not fatal, schema is already correct.
                    print(f"  [SKIPPED] {name}  ({exc.code})")
                    skipped += 1

            for name, stmt in INDEXES:
                try:
                    session.run(stmt)
                    print(f"  [OK]      {name}")
                    index_ok += 1
                except ClientError as exc:
                    print(f"  [SKIPPED] {name}  ({exc.code})")
                    index_skipped += 1

            for name, stmt in FULLTEXT_INDEXES:
                try:
                    session.run(stmt)
                    print(f"  [OK]      {name}")
                    fulltext_ok += 1
                except ClientError as exc:
                    print(f"  [SKIPPED] {name}  ({exc.code})")
                    fulltext_skipped += 1
    finally:
        driver.close()

    print(
        "\nDone: "
        f"constraints {ok} applied/{skipped} skipped, "
        f"indexes {index_ok} applied/{index_skipped} skipped, "
        f"fulltext {fulltext_ok} applied/{fulltext_skipped} skipped, "
        f"{backfilled} backfilled."
    )


# Backward-compatible name used by older automation.
apply_constraints = apply_neo4j_schema


async def apply_selected_schema(args: argparse.Namespace) -> dict[str, int] | None:
    """Apply schema for the selected provider and close provider resources."""
    provider = normalize_graph_provider(args.graph_provider)
    if provider == GraphProvider.NEO4J:
        apply_neo4j_schema(
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_db=args.neo4j_db,
        )
        return None

    prepare_graph_args(args)
    driver = await create_graph_driver_from_args(args)
    if driver is None:
        raise RuntimeError("FalkorDB provider could not be configured")
    try:
        # Preserve the project-id backfill before constraints become active.
        driver.execute_query_sync(BACKFILL_PROJECT_ID, database=args.neo4j_db)
        summary = apply_falkordb_schema(
            driver,
            constraint_timeout=args.constraint_timeout,
            poll_interval=args.constraint_poll_interval,
        )
        print(
            "Done: "
            f"constraints {summary['constraints']} operational, "
            f"indexes {summary['indexes']} ensured, "
            f"fulltext {summary['fulltext_indexes']} ensured."
        )
        return summary
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply provider-neutral graph indexes and uniqueness constraints."
    )
    add_graph_provider_args(parser)
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j Bolt URI (default: bolt://localhost:7687).",
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USER", "neo4j"),
        help="Neo4j username.",
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASSWORD", ""),
        help="Neo4j password.",
    )
    parser.add_argument(
        "--neo4j-db",
        default=os.getenv("NEO4J_DB", "neo4j"),
        help="Neo4j database name.",
    )
    parser.add_argument(
        "--constraint-timeout",
        type=float,
        default=float(os.getenv("FALKORDB_CONSTRAINT_TIMEOUT", "30")),
        help="Seconds to wait for each asynchronous FalkorDB constraint.",
    )
    parser.add_argument(
        "--constraint-poll-interval",
        type=float,
        default=float(os.getenv("FALKORDB_CONSTRAINT_POLL_INTERVAL", "0.25")),
        help="Seconds between FalkorDB constraint-status checks.",
    )

    args = parser.parse_args()

    provider = normalize_graph_provider(args.graph_provider)
    target = args.neo4j_uri if provider == GraphProvider.NEO4J else args.falkordb_graph
    print(f"Applying {provider.value} schema to {target}")
    asyncio.run(apply_selected_schema(args))


if __name__ == "__main__":
    main()

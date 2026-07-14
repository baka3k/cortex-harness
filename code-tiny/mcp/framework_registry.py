"""Shared query contract for framework overlay graph facts."""

from __future__ import annotations

import os

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Optional, Tuple


CORE_RELATIONSHIPS = ("CALLS", "USES_TYPE", "REFERENCES", "INHERITS")


@dataclass(frozen=True)
class FrameworkQueryConfig:
    name: str
    aliases: FrozenSet[str]
    labels: FrozenSet[str]
    relationships: Tuple[str, ...]
    searchable_properties: Tuple[str, ...]
    generation_scoped: bool = False


FRAMEWORKS: Dict[str, FrameworkQueryConfig] = {
    "cobol": FrameworkQueryConfig(
        name="cobol",
        aliases=frozenset({"cobol", "cobol85", "ibm-cobol", "gnucobol"}),
        labels=frozenset({
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
    ),
    "spring": FrameworkQueryConfig(
        name="spring",
        aliases=frozenset({"spring", "spring-boot", "spring_boot"}),
        labels=frozenset({
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
        searchable_properties=(
            "name", "qualified_name", "file_path", "path", "raw_value", "resolved_value",
        ),
    ),
    "servlet_jsp": FrameworkQueryConfig(
        name="servlet_jsp",
        aliases=frozenset({"servlet_jsp", "servlet-jsp", "servlet", "jsp"}),
        labels=frozenset({
            "ServletJspModule", "WebDescriptor", "Servlet", "ServletMapping", "Filter",
            "FilterMapping", "Listener", "JSPView", "JspTag", "JspExpression", "ApiEndpoint",
            "StateSlot", "LifecycleEvent", "SecurityConstraint", "ErrorPage", "WelcomePage",
            "Authority", "WebTarget", "WebConfiguration",
        }),
        relationships=(
            "SEMANTIC_OF", "HANDLES", "MAPS_TO", "PASSES_THROUGH", "FORWARDS_TO",
            "READS", "WRITES", "RESOLVES_TO", "USES", "DECLARES", "PROTECTS",
        ),
        searchable_properties=(
            "name", "qualified_name", "file_path", "path", "raw_value", "resolved_value",
            "url_pattern", "http_method",
        ),
        generation_scoped=True,
    ),
    "mybatis": FrameworkQueryConfig(
        name="mybatis",
        aliases=frozenset({"mybatis", "my-batis"}),
        labels=frozenset({
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
        searchable_properties=(
            "name", "qualified_name", "file_path", "path", "raw_value", "resolved_value", "sql",
        ),
    ),
}


def framework_for_parser(parser_type: Optional[str]) -> Optional[FrameworkQueryConfig]:
    parser = str(parser_type or "").strip().lower()
    for config in FRAMEWORKS.values():
        if parser in config.aliases:
            return config
    return None


def parser_aliases() -> FrozenSet[str]:
    return frozenset(alias for config in FRAMEWORKS.values() for alias in config.aliases)


def searchable_labels(framework: Optional[str] = None) -> Tuple[str, ...]:
    if framework:
        config = FRAMEWORKS.get(framework.strip().lower())
        return tuple(sorted(config.labels)) if config else ()
    return tuple(sorted({label for config in FRAMEWORKS.values() for label in config.labels}))


def default_relationships(parser_type: Optional[str] = None) -> Tuple[str, ...]:
    config = framework_for_parser(parser_type)
    values: Iterable[str] = CORE_RELATIONSHIPS if config is None else (*CORE_RELATIONSHIPS, *config.relationships)
    return tuple(dict.fromkeys(values))


def servlet_active_generation_predicate(alias: str) -> str:
    provider = (
        os.environ.get("CODE_GRAPH_PROVIDER")
        or os.environ.get("GRAPH_PROVIDER")
        or "neo4j"
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
    "CORE_RELATIONSHIPS", "FRAMEWORKS", "FrameworkQueryConfig", "default_relationships",
    "framework_for_parser", "parser_aliases", "searchable_labels", "servlet_active_generation_predicate",
]

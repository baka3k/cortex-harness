from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple


MYBATIS_PARSER_VERSION = "mybatis-v2026-07-13-1"


def graph_property_value(value: Any) -> Any:
    """Convert a fact value to a Neo4j/FalkorDB property value."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        rows = list(value)
        if all(item is None or isinstance(item, (str, int, float, bool)) for item in rows):
            return rows
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class SourceSpan:
    file_path: str
    start_line: int = 1
    end_line: int = 1
    start_column: int = 1
    end_column: int = 1


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    file_path: str = ""
    start_line: int = 1
    end_line: int = 1


@dataclass(frozen=True)
class ParserCapability:
    language: str
    available: bool
    parser: str
    package: str = ""
    package_version: str = ""
    abi_version: str = ""
    status: str = "ok"
    message: str = ""


@dataclass(frozen=True)
class MyBatisArtifact:
    kind: str
    file_path: str
    module_path: str
    evidence: Tuple[str, ...]
    confidence: float
    source: SourceSpan


@dataclass(frozen=True)
class MyBatisModule:
    root: str
    rel_path: str
    mapper_xml_files: Tuple[str, ...]
    config_xml_files: Tuple[str, ...]
    java_files: Tuple[str, ...]
    build_files: Tuple[str, ...]
    spring_config_files: Tuple[str, ...]
    evidence: Tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class JavaSourceFact:
    file_path: str
    source_symbol_id: str
    package_name: str = ""
    declarations: Tuple[str, ...] = ()
    annotations: Tuple[str, ...] = ()
    imports: Tuple[str, ...] = ()
    parser_status: str = "not_parsed"


@dataclass(frozen=True)
class MyBatisAnnotationFact:
    name: str
    resolved_name: str
    raw_arguments: str
    source: SourceSpan


@dataclass(frozen=True)
class MyBatisMapperParameterFact:
    stable_id: str
    mapper_method_id: str
    name: str
    position: int
    java_type: str
    canonical_type: str
    param_alias: str = ""
    special_role: str = ""
    source: SourceSpan = field(default_factory=lambda: SourceSpan(""))
    annotations: Tuple[MyBatisAnnotationFact, ...] = ()


@dataclass(frozen=True)
class MyBatisMapperMethodFact:
    stable_id: str
    java_symbol_id: str
    mapper_fqcn: str
    name: str
    signature: str
    return_type: str
    parameter_types: Tuple[str, ...]
    source: SourceSpan
    bindable: bool = True
    overload_count: int = 1
    ambiguity_status: str = "unique"
    modifiers: Tuple[str, ...] = ()
    throws: Tuple[str, ...] = ()
    annotations: Tuple[MyBatisAnnotationFact, ...] = ()
    parameters: Tuple[MyBatisMapperParameterFact, ...] = ()
    has_body: bool = False


@dataclass(frozen=True)
class MyBatisMapperInterfaceFact:
    stable_id: str
    java_class_symbol_id: str
    name: str
    fqcn: str
    file_path: str
    source: SourceSpan
    package_name: str = ""
    type_parameters: Tuple[str, ...] = ()
    extended_interfaces: Tuple[str, ...] = ()
    modifiers: Tuple[str, ...] = ()
    imports: Tuple[str, ...] = ()
    annotations: Tuple[MyBatisAnnotationFact, ...] = ()
    methods: Tuple[MyBatisMapperMethodFact, ...] = ()


@dataclass(frozen=True)
class MyBatisJavaPropertyFact:
    stable_id: str
    java_type_fqcn: str
    property_name: str
    property_type: str
    source_kind: str
    source: SourceSpan
    readable: bool = True
    writable: bool = True
    source_symbol_id: str = ""


@dataclass(frozen=True)
class MyBatisXmlDocumentFact:
    file_path: str
    document_kind: str
    root_tag: str
    source: SourceSpan
    namespace: str = ""
    doctype: str = ""
    parser_status: str = "parsed"


@dataclass(frozen=True)
class MyBatisIncludeFact:
    stable_id: str
    owner_id: str
    refid: str
    resolved_refid: str
    source: SourceSpan
    properties: Dict[str, str] = field(default_factory=dict)
    resolution_status: str = "resolved"


@dataclass(frozen=True)
class MyBatisDynamicSqlNodeFact:
    stable_id: str
    owner_id: str
    tag: str
    node_kind: str
    source: SourceSpan
    order: int = 0
    text: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    test: str = ""
    branch_role: str = ""
    referenced_variables: Tuple[str, ...] = ()


@dataclass(frozen=True)
class MyBatisStatementFact:
    stable_id: str
    namespace: str
    statement_id: str
    statement_kind: str
    source: SourceSpan
    database_id: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    raw_body: str = ""
    expanded_body: str = ""
    includes: Tuple[MyBatisIncludeFact, ...] = ()
    dynamic_nodes: Tuple[MyBatisDynamicSqlNodeFact, ...] = ()
    parser_status: str = "parsed"


@dataclass(frozen=True)
class MyBatisSqlFragmentFact:
    stable_id: str
    namespace: str
    fragment_id: str
    source: SourceSpan
    database_id: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    raw_body: str = ""
    expanded_body: str = ""
    includes: Tuple[MyBatisIncludeFact, ...] = ()


@dataclass(frozen=True)
class MyBatisResultMappingFact:
    stable_id: str
    result_map_id: str
    mapping_kind: str
    source: SourceSpan
    property_name: str = ""
    column: str = ""
    java_type: str = ""
    jdbc_type: str = ""
    nested_select: str = ""
    nested_result_map: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MyBatisResultMapFact:
    stable_id: str
    namespace: str
    result_map_id: str
    source: SourceSpan
    java_type: str = ""
    extends: str = ""
    auto_mapping: str = ""
    mappings: Tuple[MyBatisResultMappingFact, ...] = ()


@dataclass(frozen=True)
class MyBatisConfigFact:
    stable_id: str
    file_path: str
    source: SourceSpan
    properties: Dict[str, str] = field(default_factory=dict)
    settings: Dict[str, str] = field(default_factory=dict)
    type_aliases: Dict[str, str] = field(default_factory=dict)
    type_handlers: Tuple[Dict[str, str], ...] = ()
    plugins: Tuple[Dict[str, str], ...] = ()
    environments: Tuple[Dict[str, str], ...] = ()
    database_id_provider: Dict[str, str] = field(default_factory=dict)
    mapper_registrations: Tuple[Dict[str, str], ...] = ()


@dataclass(frozen=True)
class MyBatisProviderFact:
    stable_id: str
    mapper_method_id: str
    namespace: str
    statement_id: str
    provider_kind: str
    source: SourceSpan
    provider_type: str = ""
    provider_method: str = ""
    raw_arguments: str = ""
    resolution_status: str = "runtime_generated"
    attributes: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MyBatisSpringBridgeFact:
    stable_id: str
    bridge_kind: str
    source: SourceSpan
    name: str = ""
    target: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    resolution_status: str = "evidence_only"


@dataclass(frozen=True)
class MyBatisExtensionFact:
    stable_id: str
    extension_kind: str
    source: SourceSpan
    name: str = ""
    java_type: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    resolution_status: str = "evidence_only"


@dataclass(frozen=True)
class MyBatisCacheFact:
    stable_id: str
    namespace: str
    cache_kind: str
    source: SourceSpan
    target_namespace: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    resolution_status: str = "evidence_only"


@dataclass(frozen=True)
class MyBatisSqlStatementSemanticFact:
    stable_id: str
    owner_statement_id: str
    source: SourceSpan
    crud: str = ""
    xml_statement_kind: str = ""
    database_id: str = ""
    raw_sql: str = ""
    normalized_sql: str = ""
    parser_status: str = "parsed"
    parser_error_count: int = 0
    has_textual_substitution: bool = False
    confidence: float = 1.0


@dataclass(frozen=True)
class MyBatisSqlTableFact:
    stable_id: str
    sql_statement_id: str
    raw_name: str
    normalized_name: str
    role: str
    source: SourceSpan
    alias: str = ""
    catalog: str = ""
    schema: str = ""
    is_cte: bool = False
    is_dynamic: bool = False
    dynamic_node_ids: Tuple[str, ...] = ()
    branch_roles: Tuple[str, ...] = ()
    resolution_status: str = "resolved"


@dataclass(frozen=True)
class MyBatisSqlColumnFact:
    stable_id: str
    sql_statement_id: str
    raw_name: str
    normalized_name: str
    role: str
    source: SourceSpan
    qualifier: str = ""
    table_ref: str = ""
    expression: str = ""
    dynamic_node_ids: Tuple[str, ...] = ()
    branch_roles: Tuple[str, ...] = ()
    resolution_status: str = "unresolved"


@dataclass(frozen=True)
class MyBatisSqlJoinFact:
    stable_id: str
    sql_statement_id: str
    source: SourceSpan
    join_type: str = "join"
    right_table: str = ""
    right_alias: str = ""
    condition: str = ""
    dynamic_node_ids: Tuple[str, ...] = ()
    branch_roles: Tuple[str, ...] = ()
    resolution_status: str = "unresolved"


@dataclass(frozen=True)
class MyBatisSqlParameterFact:
    stable_id: str
    sql_statement_id: str
    token: str
    parameter_kind: str
    source: SourceSpan
    name: str = ""
    options: Dict[str, str] = field(default_factory=dict)
    position: int = 0
    dynamic_node_ids: Tuple[str, ...] = ()
    branch_roles: Tuple[str, ...] = ()


@dataclass(frozen=True)
class XmlSourceFact:
    file_path: str
    root_tag: str = ""
    namespace: str = ""
    doctype: str = ""
    parser_status: str = "not_parsed"


@dataclass(frozen=True)
class SqlSourceFact:
    file_path: str
    statement_id: str = ""
    parser_status: str = "not_parsed"


@dataclass(frozen=True)
class MyBatisFact:
    kind: str
    stable_id: str
    name: str
    source: SourceSpan
    project_id: str
    project_name: str
    language: str = "mybatis"
    confidence: float = 1.0
    extraction_method: str = "mybatis_foundation"
    resolution_status: str = "resolved"
    raw_value: str = ""
    resolved_value: str = ""
    source_symbol_id: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_graph_node(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "id": self.stable_id,
            "symbol_id": self.stable_id,
            "name": self.name,
            "kind": self.kind,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "language": self.language,
            "framework": "mybatis",
            "file_path": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "resolution_status": self.resolution_status,
            "raw_value": self.raw_value,
            "resolved_value": self.resolved_value,
            "source_symbol_id": self.source_symbol_id,
            "parser_version": MYBATIS_PARSER_VERSION,
        }
        row.update({key: graph_property_value(value) for key, value in self.properties.items()})
        return row


@dataclass(frozen=True)
class MyBatisRelationship:
    from_id: str
    to_id: str
    type: str
    project_id: str
    source: SourceSpan
    confidence: float = 1.0
    resolution_status: str = "resolved"
    reason: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_graph_relationship(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "type": self.type,
            "project_id": self.project_id,
            "framework": "mybatis",
            "file_path": self.source.file_path,
            "start_line": self.source.start_line,
            "end_line": self.source.end_line,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "reason": self.reason,
        }
        row.update({key: graph_property_value(value) for key, value in self.properties.items()})
        return row


@dataclass(frozen=True)
class MyBatisDependencyIndex:
    namespaces: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    statements: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    fragments: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    result_maps: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    aliases: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    files: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class MyBatisAnalysisResult:
    project_id: str
    project_name: str
    root: str
    modules: Tuple[MyBatisModule, ...]
    artifacts: Tuple[MyBatisArtifact, ...]
    parser_capabilities: Tuple[ParserCapability, ...]
    java_facts: Tuple[JavaSourceFact, ...]
    mapper_interfaces: Tuple[MyBatisMapperInterfaceFact, ...]
    mapper_methods: Tuple[MyBatisMapperMethodFact, ...]
    mapper_parameters: Tuple[MyBatisMapperParameterFact, ...]
    java_properties: Tuple[MyBatisJavaPropertyFact, ...]
    xml_documents: Tuple[MyBatisXmlDocumentFact, ...]
    statements: Tuple[MyBatisStatementFact, ...]
    sql_fragments: Tuple[MyBatisSqlFragmentFact, ...]
    result_maps: Tuple[MyBatisResultMapFact, ...]
    result_mappings: Tuple[MyBatisResultMappingFact, ...]
    includes: Tuple[MyBatisIncludeFact, ...]
    dynamic_nodes: Tuple[MyBatisDynamicSqlNodeFact, ...]
    config_facts: Tuple[MyBatisConfigFact, ...]
    provider_facts: Tuple[MyBatisProviderFact, ...]
    spring_bridge_facts: Tuple[MyBatisSpringBridgeFact, ...]
    extension_facts: Tuple[MyBatisExtensionFact, ...]
    cache_facts: Tuple[MyBatisCacheFact, ...]
    sql_statement_semantics: Tuple[MyBatisSqlStatementSemanticFact, ...]
    sql_tables: Tuple[MyBatisSqlTableFact, ...]
    sql_columns: Tuple[MyBatisSqlColumnFact, ...]
    sql_joins: Tuple[MyBatisSqlJoinFact, ...]
    sql_parameters: Tuple[MyBatisSqlParameterFact, ...]
    xml_facts: Tuple[XmlSourceFact, ...]
    sql_facts: Tuple[SqlSourceFact, ...]
    semantic_facts: Tuple[MyBatisFact, ...]
    relationships: Tuple[MyBatisRelationship, ...]
    dependency_index: MyBatisDependencyIndex
    diagnostics: Tuple[Diagnostic, ...]
    parser_version: str = MYBATIS_PARSER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

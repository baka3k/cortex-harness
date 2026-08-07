from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from tools.mybatis.models import (
    Diagnostic,
    MyBatisJavaPropertyFact,
    MyBatisMapperInterfaceFact,
    MyBatisMapperMethodFact,
    MyBatisMapperParameterFact,
    MyBatisRelationship,
    MyBatisResultMapFact,
    MyBatisResultMappingFact,
    MyBatisSqlColumnFact,
    MyBatisSqlJoinFact,
    MyBatisSqlParameterFact,
    MyBatisSqlTableFact,
    MyBatisStatementFact,
)


@dataclass(frozen=True)
class MyBatisResolution:
    relationships: Tuple[MyBatisRelationship, ...]
    diagnostics: Tuple[Diagnostic, ...]


def resolve_mybatis_relationships(
    *,
    project_id: str,
    mapper_interfaces: Sequence[MyBatisMapperInterfaceFact],
    mapper_methods: Sequence[MyBatisMapperMethodFact],
    mapper_parameters: Sequence[MyBatisMapperParameterFact],
    statements: Sequence[MyBatisStatementFact],
    sql_tables: Sequence[MyBatisSqlTableFact],
    sql_columns: Sequence[MyBatisSqlColumnFact],
    sql_joins: Sequence[MyBatisSqlJoinFact],
    sql_parameters: Sequence[MyBatisSqlParameterFact],
    result_maps: Sequence[MyBatisResultMapFact],
    result_mappings: Sequence[MyBatisResultMappingFact],
    java_properties: Sequence[MyBatisJavaPropertyFact],
) -> MyBatisResolution:
    relationships: List[MyBatisRelationship] = []
    diagnostics: List[Diagnostic] = []

    mappers_by_namespace = {item.fqcn: item for item in mapper_interfaces}
    methods_by_mapper_name: Dict[Tuple[str, str], List[MyBatisMapperMethodFact]] = {}
    params_by_method: Dict[str, List[MyBatisMapperParameterFact]] = {}
    statements_by_ref: Dict[str, MyBatisStatementFact] = {}
    result_maps_by_ref: Dict[str, MyBatisResultMapFact] = {}
    columns_by_statement: Dict[str, List[MyBatisSqlColumnFact]] = {}
    properties_by_type_name: Dict[Tuple[str, str], MyBatisJavaPropertyFact] = {}

    for method in mapper_methods:
        methods_by_mapper_name.setdefault((method.mapper_fqcn, method.name), []).append(method)
    for param in mapper_parameters:
        params_by_method.setdefault(param.mapper_method_id, []).append(param)
    for stmt in statements:
        statements_by_ref[_statement_ref(stmt.namespace, stmt.statement_id)] = stmt
    for result_map in result_maps:
        result_maps_by_ref[_statement_ref(result_map.namespace, result_map.result_map_id)] = result_map
    for column in sql_columns:
        columns_by_statement.setdefault(column.sql_statement_id, []).append(column)
    for prop in java_properties:
        properties_by_type_name[(prop.java_type_fqcn, prop.property_name)] = prop

    for mapper in mapper_interfaces:
        relationships.append(_rel("MyBatisMapper", mapper.stable_id, "Class", mapper.java_class_symbol_id, "SEMANTIC_OF", project_id, mapper.source, reason="mapper interface anchor"))
        for method in [item for item in mapper_methods if item.mapper_fqcn == mapper.fqcn]:
            relationships.append(_rel("MyBatisMapper", mapper.stable_id, "MyBatisMapperMethod", method.stable_id, "DECLARES_METHOD", project_id, method.source, reason="mapper method declaration"))
            if method.java_symbol_id:
                relationships.append(_rel("MyBatisMapperMethod", method.stable_id, "Function", method.java_symbol_id, "SEMANTIC_OF", project_id, method.source, reason="mapper method Java anchor"))

    statement_bindings: Dict[str, List[MyBatisMapperMethodFact]] = {}
    for stmt in statements:
        mapper = mappers_by_namespace.get(stmt.namespace)
        if mapper is None:
            diagnostics.append(Diagnostic("mybatis.resolve.namespace_unresolved", f"No Java mapper interface found for namespace {stmt.namespace!r}", "warning", stmt.source.file_path, stmt.source.start_line, stmt.source.end_line))
        else:
            relationships.append(_rel("MyBatisMapper", mapper.stable_id, "MyBatisStatement", stmt.stable_id, "DECLARES_STATEMENT", project_id, stmt.source, reason="statement namespace matches mapper"))

        candidates = methods_by_mapper_name.get((stmt.namespace, stmt.statement_id), [])
        statement_bindings[stmt.stable_id] = candidates
        if len(candidates) == 1:
            relationships.append(_rel("MyBatisMapperMethod", candidates[0].stable_id, "MyBatisStatement", stmt.stable_id, "BINDS_STATEMENT", project_id, stmt.source, reason="method name matches statement id"))
        elif len(candidates) > 1:
            diagnostics.append(Diagnostic("mybatis.resolve.statement_ambiguous", f"Statement {stmt.statement_id!r} matches {len(candidates)} overloaded methods", "warning", stmt.source.file_path, stmt.source.start_line, stmt.source.end_line))
            for candidate in candidates:
                relationships.append(_rel("MyBatisMapperMethod", candidate.stable_id, "MyBatisStatement", stmt.stable_id, "BINDS_STATEMENT", project_id, stmt.source, resolution_status="ambiguous", reason="overloaded method name matches statement id", properties={"candidate_count": len(candidates)}))
        else:
            diagnostics.append(Diagnostic("mybatis.resolve.statement_method_unresolved", f"No mapper method found for statement {stmt.namespace}.{stmt.statement_id}", "warning", stmt.source.file_path, stmt.source.start_line, stmt.source.end_line))

        _resolve_statement_result_maps(project_id, stmt, result_maps_by_ref, relationships, diagnostics)

    for table in sql_tables:
        rel_type = "WRITES_TO" if table.role == "write" else "READS_FROM" if table.role in {"read", "cte_definition"} else "REFERENCES_TABLE"
        relationships.append(_rel("MyBatisSqlStatement", table.sql_statement_id, "DatabaseTable", table.stable_id, rel_type, project_id, table.source, resolution_status=table.resolution_status, reason=f"SQL table role {table.role}", properties={"role": table.role, "alias": table.alias}))
    for column in sql_columns:
        relationships.append(_rel("MyBatisSqlStatement", column.sql_statement_id, "DatabaseColumn", column.stable_id, "REFERENCES_COLUMN", project_id, column.source, resolution_status=column.resolution_status, reason=f"SQL column role {column.role}", properties={"role": column.role, "qualifier": column.qualifier}))
    for join in sql_joins:
        relationships.append(_rel("MyBatisSqlStatement", join.sql_statement_id, "MyBatisSqlJoin", join.stable_id, "JOINS_WITH", project_id, join.source, resolution_status=join.resolution_status, reason="SQL join", properties={"join_type": join.join_type, "right_table": join.right_table, "right_alias": join.right_alias}))

    _resolve_parameters(project_id, sql_parameters, statement_bindings, params_by_method, relationships, diagnostics)
    _resolve_result_mappings(project_id, statements, result_maps, result_mappings, result_maps_by_ref, statements_by_ref, columns_by_statement, properties_by_type_name, relationships, diagnostics)

    return MyBatisResolution(tuple(relationships), tuple(diagnostics))


def _resolve_statement_result_maps(project_id: str, stmt: MyBatisStatementFact, result_maps_by_ref: Dict[str, MyBatisResultMapFact], relationships: List[MyBatisRelationship], diagnostics: List[Diagnostic]) -> None:
    result_map_attr = stmt.attributes.get("resultMap", "")
    if not result_map_attr:
        return
    for ref in [item.strip() for item in result_map_attr.split(",") if item.strip()]:
        target = result_maps_by_ref.get(_qualified_ref(stmt.namespace, ref))
        if target is None:
            diagnostics.append(Diagnostic("mybatis.resolve.result_map_unresolved", f"Unable to resolve resultMap {ref!r}", "warning", stmt.source.file_path, stmt.source.start_line, stmt.source.end_line))
            continue
        relationships.append(_rel("MyBatisStatement", stmt.stable_id, "MyBatisResultMap", target.stable_id, "USES_RESULT_MAP", project_id, stmt.source, reason="statement resultMap attribute"))


def _resolve_parameters(project_id: str, sql_parameters: Sequence[MyBatisSqlParameterFact], statement_bindings: Dict[str, List[MyBatisMapperMethodFact]], params_by_method: Dict[str, List[MyBatisMapperParameterFact]], relationships: List[MyBatisRelationship], diagnostics: List[Diagnostic]) -> None:
    for sql_param in sql_parameters:
        owner_statement_id = sql_param.sql_statement_id.removeprefix("mybatis_sql_stmt::")
        methods = statement_bindings.get(owner_statement_id, [])
        if not methods:
            continue
        for method in methods:
            params = params_by_method.get(method.stable_id, [])
            match = _match_param(sql_param.name, params)
            if match is None:
                diagnostics.append(Diagnostic("mybatis.resolve.parameter_unresolved", f"Unable to resolve SQL parameter {sql_param.name!r} for method {method.name}", "warning", sql_param.source.file_path, sql_param.source.start_line, sql_param.source.end_line))
                continue
            status = "ambiguous" if len(methods) > 1 else "resolved"
            relationships.append(_rel("MyBatisSqlStatement", sql_param.sql_statement_id, "MyBatisParameter", match.stable_id, "DEPENDS_ON_PARAMETER", project_id, sql_param.source, resolution_status=status, reason="SQL placeholder resolved to mapper parameter", properties={"sql_parameter_id": sql_param.stable_id, "parameter_name": sql_param.name, "parameter_kind": sql_param.parameter_kind}))


def _resolve_result_mappings(project_id: str, statements: Sequence[MyBatisStatementFact], result_maps: Sequence[MyBatisResultMapFact], result_mappings: Sequence[MyBatisResultMappingFact], result_maps_by_ref: Dict[str, MyBatisResultMapFact], statements_by_ref: Dict[str, MyBatisStatementFact], columns_by_statement: Dict[str, List[MyBatisSqlColumnFact]], properties_by_type_name: Dict[Tuple[str, str], MyBatisJavaPropertyFact], relationships: List[MyBatisRelationship], diagnostics: List[Diagnostic]) -> None:
    maps_by_id = {item.stable_id: item for item in result_maps}
    statements_using_map: Dict[str, List[str]] = {}
    for stmt in statements:
        for ref in [item.strip() for item in stmt.attributes.get("resultMap", "").split(",") if item.strip()]:
            target = result_maps_by_ref.get(_qualified_ref(stmt.namespace, ref))
            if target is not None:
                statements_using_map.setdefault(target.stable_id, []).append(f"mybatis_sql_stmt::{stmt.stable_id}")
    for result_map in result_maps:
        if result_map.extends:
            target = result_maps_by_ref.get(_qualified_ref(result_map.namespace, result_map.extends))
            if target is None:
                diagnostics.append(Diagnostic("mybatis.resolve.result_map_extends_unresolved", f"Unable to resolve parent resultMap {result_map.extends!r}", "warning", result_map.source.file_path, result_map.source.start_line, result_map.source.end_line))
            else:
                relationships.append(_rel("MyBatisResultMap", result_map.stable_id, "MyBatisResultMap", target.stable_id, "EXTENDS_RESULT_MAP", project_id, result_map.source, reason="resultMap extends"))
    for mapping in result_mappings:
        result_map = maps_by_id.get(mapping.result_map_id)
        relationships.append(_rel("MyBatisResultMap", mapping.result_map_id, "MyBatisResultMapping", mapping.stable_id, "HAS_RESULT_MAPPING", project_id, mapping.source, reason="result-map mapping member"))
        if result_map is not None and mapping.property_name:
            prop = properties_by_type_name.get((result_map.java_type, mapping.property_name))
            if prop is not None:
                relationships.append(_rel("MyBatisResultMapping", mapping.stable_id, "MyBatisJavaProperty", prop.stable_id, "MAPS_PROPERTY", project_id, mapping.source, reason="result mapping property matches Java property"))
        if result_map is not None and mapping.column:
            for sql_statement_id in statements_using_map.get(result_map.stable_id, []):
                for column in columns_by_statement.get(sql_statement_id, []):
                    if column.role == "projection" and column.normalized_name == mapping.column.lower():
                        relationships.append(_rel("MyBatisResultMapping", mapping.stable_id, "DatabaseColumn", column.stable_id, "MAPS_COLUMN", project_id, mapping.source, resolution_status=column.resolution_status, reason="result mapping column matches SQL column", properties={"sql_statement_id": sql_statement_id}))
        if mapping.nested_select and result_map is not None:
            target_stmt = statements_by_ref.get(_qualified_ref(result_map.namespace, mapping.nested_select))
            if target_stmt is not None:
                relationships.append(_rel("MyBatisResultMapping", mapping.stable_id, "MyBatisStatement", target_stmt.stable_id, "NESTED_SELECT", project_id, mapping.source, reason="nested select mapping"))
        if mapping.nested_result_map and result_map is not None:
            target_map = result_maps_by_ref.get(_qualified_ref(result_map.namespace, mapping.nested_result_map))
            if target_map is not None:
                rel_type = "HAS_COLLECTION" if mapping.mapping_kind == "collection" else "HAS_ASSOCIATION"
                relationships.append(_rel("MyBatisResultMapping", mapping.stable_id, "MyBatisResultMap", target_map.stable_id, rel_type, project_id, mapping.source, reason="nested resultMap mapping"))


def _match_param(name: str, params: Sequence[MyBatisMapperParameterFact]) -> MyBatisMapperParameterFact | None:
    if not params:
        return None
    for param in params:
        aliases = {param.name, param.param_alias, f"param{param.position + 1}", f"arg{param.position}"}
        if name in aliases:
            return param
    if len(params) == 1 and "." in name:
        return params[0]
    return None


def _qualified_ref(namespace: str, refid: str) -> str:
    return refid if "." in refid else f"{namespace}.{refid}" if namespace else refid


def _statement_ref(namespace: str, name: str) -> str:
    return f"{namespace}.{name}" if namespace else name


def _rel(from_label: str, from_id: str, to_label: str, to_id: str, rel_type: str, project_id: str, source, *, confidence: float = 1.0, resolution_status: str = "resolved", reason: str = "", properties: Dict[str, object] | None = None) -> MyBatisRelationship:
    return MyBatisRelationship(
        from_label=from_label,
        from_id=from_id,
        to_label=to_label,
        to_id=to_id,
        type=rel_type,
        project_id=project_id,
        source=source,
        confidence=confidence,
        resolution_status=resolution_status,
        reason=reason,
        properties=dict(properties or {}),
    )

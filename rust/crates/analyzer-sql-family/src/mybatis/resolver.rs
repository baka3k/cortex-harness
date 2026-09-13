//! Port `tools/mybatis/resolver.py` — relationship resolution.

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::mybatis::models::{
    Diagnostic, JavaPropertyFact, MapperInterfaceFact, MapperMethodFact, MapperParameterFact,
    MyBatisRelationship, ResultMapFact, ResultMappingFact, SourceSpan, SqlColumnFact, SqlJoinFact,
    SqlParameterFact, SqlTableFact, StatementFact,
};

pub struct MyBatisResolution {
    pub relationships: Vec<MyBatisRelationship>,
    pub diagnostics: Vec<Diagnostic>,
}

#[allow(clippy::too_many_arguments)]
pub fn resolve_mybatis_relationships(
    project_id: &str,
    mapper_interfaces: &[MapperInterfaceFact],
    mapper_methods: &[MapperMethodFact],
    mapper_parameters: &[MapperParameterFact],
    statements: &[StatementFact],
    sql_tables: &[SqlTableFact],
    sql_columns: &[SqlColumnFact],
    sql_joins: &[SqlJoinFact],
    sql_parameters: &[SqlParameterFact],
    result_maps: &[ResultMapFact],
    result_mappings: &[ResultMappingFact],
    java_properties: &[JavaPropertyFact],
) -> MyBatisResolution {
    let mut relationships: Vec<MyBatisRelationship> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    let mut mappers_by_namespace: HashMap<&str, &MapperInterfaceFact> = HashMap::new();
    for item in mapper_interfaces {
        mappers_by_namespace.insert(item.fqcn.as_str(), item);
    }
    let mut methods_by_mapper_name: HashMap<(&str, &str), Vec<&MapperMethodFact>> = HashMap::new();
    let mut params_by_method: HashMap<&str, Vec<&MapperParameterFact>> = HashMap::new();
    let mut statements_by_ref: HashMap<String, &StatementFact> = HashMap::new();
    let mut result_maps_by_ref: HashMap<String, &ResultMapFact> = HashMap::new();
    let mut columns_by_statement: HashMap<&str, Vec<&SqlColumnFact>> = HashMap::new();
    let mut properties_by_type_name: HashMap<(&str, &str), &JavaPropertyFact> = HashMap::new();

    for method in mapper_methods {
        methods_by_mapper_name
            .entry((method.mapper_fqcn.as_str(), method.name.as_str()))
            .or_default()
            .push(method);
    }
    for param in mapper_parameters {
        params_by_method
            .entry(param.mapper_method_id.as_str())
            .or_default()
            .push(param);
    }
    for stmt in statements {
        statements_by_ref.insert(statement_ref(&stmt.namespace, &stmt.statement_id), stmt);
    }
    for result_map in result_maps {
        result_maps_by_ref.insert(
            statement_ref(&result_map.namespace, &result_map.result_map_id),
            result_map,
        );
    }
    for column in sql_columns {
        columns_by_statement
            .entry(column.sql_statement_id.as_str())
            .or_default()
            .push(column);
    }
    for prop in java_properties {
        properties_by_type_name.insert(
            (prop.java_type_fqcn.as_str(), prop.property_name.as_str()),
            prop,
        );
    }

    for mapper in mapper_interfaces {
        relationships.push(rel(
            "MyBatisMapper",
            &mapper.stable_id,
            "Class",
            &mapper.java_class_symbol_id,
            "SEMANTIC_OF",
            project_id,
            &mapper.source,
            1.0,
            "resolved",
            "mapper interface anchor",
            Vec::new(),
        ));
        for method in mapper_methods.iter().filter(|m| m.mapper_fqcn == mapper.fqcn) {
            relationships.push(rel(
                "MyBatisMapper",
                &mapper.stable_id,
                "MyBatisMapperMethod",
                &method.stable_id,
                "DECLARES_METHOD",
                project_id,
                &method.source,
                1.0,
                "resolved",
                "mapper method declaration",
                Vec::new(),
            ));
            if !method.java_symbol_id.is_empty() {
                relationships.push(rel(
                    "MyBatisMapperMethod",
                    &method.stable_id,
                    "Function",
                    &method.java_symbol_id,
                    "SEMANTIC_OF",
                    project_id,
                    &method.source,
                    1.0,
                    "resolved",
                    "mapper method Java anchor",
                    Vec::new(),
                ));
            }
        }
    }

    let mut statement_bindings: HashMap<String, Vec<&MapperMethodFact>> = HashMap::new();
    for stmt in statements {
        let mapper = mappers_by_namespace.get(stmt.namespace.as_str());
        match mapper {
            None => {
                diagnostics.push(Diagnostic::new(
                    "mybatis.resolve.namespace_unresolved",
                    &format!(
                        "No Java mapper interface found for namespace {:?}",
                        stmt.namespace
                    ),
                ));
            }
            Some(mapper) => {
                relationships.push(rel(
                    "MyBatisMapper",
                    &mapper.stable_id,
                    "MyBatisStatement",
                    &stmt.stable_id,
                    "DECLARES_STATEMENT",
                    project_id,
                    &stmt.source,
                    1.0,
                    "resolved",
                    "statement namespace matches mapper",
                    Vec::new(),
                ));
            }
        }

        let candidates = methods_by_mapper_name
            .get(&(
                stmt.namespace.as_str(),
                stmt.statement_id.as_str(),
            ))
            .cloned()
            .unwrap_or_default();
        statement_bindings.insert(stmt.stable_id.clone(), candidates.clone());
        if candidates.len() == 1 {
            relationships.push(rel(
                "MyBatisMapperMethod",
                &candidates[0].stable_id,
                "MyBatisStatement",
                &stmt.stable_id,
                "BINDS_STATEMENT",
                project_id,
                &stmt.source,
                1.0,
                "resolved",
                "method name matches statement id",
                Vec::new(),
            ));
        } else if candidates.len() > 1 {
            diagnostics.push(Diagnostic::new(
                "mybatis.resolve.statement_ambiguous",
                &format!(
                    "Statement {:?} matches {} overloaded methods",
                    stmt.statement_id,
                    candidates.len()
                ),
            ));
            for candidate in &candidates {
                relationships.push(rel(
                    "MyBatisMapperMethod",
                    &candidate.stable_id,
                    "MyBatisStatement",
                    &stmt.stable_id,
                    "BINDS_STATEMENT",
                    project_id,
                    &stmt.source,
                    1.0,
                    "ambiguous",
                    "overloaded method name matches statement id",
                    vec![(
                        "candidate_count".to_string(),
                        serde_json::json!(candidates.len()),
                    )],
                ));
            }
        } else {
            diagnostics.push(Diagnostic::new(
                "mybatis.resolve.statement_method_unresolved",
                &format!(
                    "No mapper method found for statement {}.{}",
                    stmt.namespace, stmt.statement_id
                ),
            ));
        }

        resolve_statement_result_maps(
            project_id,
            stmt,
            &result_maps_by_ref,
            &mut relationships,
            &mut diagnostics,
        );
    }

    for table in sql_tables {
        let rel_type = if table.role == "write" {
            "WRITES_TO"
        } else if table.role == "read" || table.role == "cte_definition" {
            "READS_FROM"
        } else {
            "REFERENCES_TABLE"
        };
        relationships.push(rel(
            "MyBatisSqlStatement",
            &table.sql_statement_id,
            "DatabaseTable",
            &table.stable_id,
            rel_type,
            project_id,
            &table.source,
            1.0,
            &table.resolution_status,
            &format!("SQL table role {}", table.role),
            vec![
                ("role".to_string(), serde_json::json!(table.role)),
                ("alias".to_string(), serde_json::json!(table.alias)),
            ],
        ));
    }
    for column in sql_columns {
        relationships.push(rel(
            "MyBatisSqlStatement",
            &column.sql_statement_id,
            "DatabaseColumn",
            &column.stable_id,
            "REFERENCES_COLUMN",
            project_id,
            &column.source,
            1.0,
            &column.resolution_status,
            &format!("SQL column role {}", column.role),
            vec![
                ("role".to_string(), serde_json::json!(column.role)),
                (
                    "qualifier".to_string(),
                    serde_json::json!(column.qualifier),
                ),
            ],
        ));
    }
    for join in sql_joins {
        relationships.push(rel(
            "MyBatisSqlStatement",
            &join.sql_statement_id,
            "MyBatisSqlJoin",
            &join.stable_id,
            "JOINS_WITH",
            project_id,
            &join.source,
            1.0,
            &join.resolution_status,
            "SQL join",
            vec![
                (
                    "join_type".to_string(),
                    serde_json::json!(join.join_type),
                ),
                (
                    "right_table".to_string(),
                    serde_json::json!(join.right_table),
                ),
                (
                    "right_alias".to_string(),
                    serde_json::json!(join.right_alias),
                ),
            ],
        ));
    }

    resolve_parameters(
        project_id,
        sql_parameters,
        &statement_bindings,
        &params_by_method,
        &mut relationships,
        &mut diagnostics,
    );
    resolve_result_mappings(
        project_id,
        statements,
        result_maps,
        result_mappings,
        &result_maps_by_ref,
        &statements_by_ref,
        &columns_by_statement,
        &properties_by_type_name,
        &mut relationships,
        &mut diagnostics,
    );

    MyBatisResolution {
        relationships,
        diagnostics,
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_statement_result_maps(
    project_id: &str,
    stmt: &StatementFact,
    result_maps_by_ref: &HashMap<String, &ResultMapFact>,
    relationships: &mut Vec<MyBatisRelationship>,
    diagnostics: &mut Vec<Diagnostic>,
) {
    let result_map_attr = stmt
        .attributes
        .get("resultMap")
        .cloned()
        .unwrap_or_default();
    if result_map_attr.is_empty() {
        return;
    }
    for reference in result_map_attr.split(',') {
        let reference = reference.trim();
        if reference.is_empty() {
            continue;
        }
        let target = result_maps_by_ref.get(&qualified_ref(&stmt.namespace, reference));
        match target {
            None => {
                diagnostics.push(Diagnostic::new(
                    "mybatis.resolve.result_map_unresolved",
                    &format!("Unable to resolve resultMap {reference:?}"),
                ));
            }
            Some(target) => {
                relationships.push(rel(
                    "MyBatisStatement",
                    &stmt.stable_id,
                    "MyBatisResultMap",
                    &target.stable_id,
                    "USES_RESULT_MAP",
                    project_id,
                    &stmt.source,
                    1.0,
                    "resolved",
                    "statement resultMap attribute",
                    Vec::new(),
                ));
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_parameters(
    project_id: &str,
    sql_parameters: &[SqlParameterFact],
    statement_bindings: &HashMap<String, Vec<&MapperMethodFact>>,
    params_by_method: &HashMap<&str, Vec<&MapperParameterFact>>,
    relationships: &mut Vec<MyBatisRelationship>,
    diagnostics: &mut Vec<Diagnostic>,
) {
    for sql_param in sql_parameters {
        let owner_statement_id = sql_param
            .sql_statement_id
            .strip_prefix("mybatis_sql_stmt::")
            .unwrap_or(&sql_param.sql_statement_id);
        let methods = statement_bindings
            .get(owner_statement_id)
            .cloned()
            .unwrap_or_default();
        if methods.is_empty() {
            continue;
        }
        for method in &methods {
            let empty: Vec<&MapperParameterFact> = Vec::new();
            let params = params_by_method
                .get(method.stable_id.as_str())
                .unwrap_or(&empty);
            let matched = match_param(&sql_param.name, params);
            match matched {
                None => {
                    diagnostics.push(Diagnostic::new(
                        "mybatis.resolve.parameter_unresolved",
                        &format!(
                            "Unable to resolve SQL parameter {:?} for method {}",
                            sql_param.name, method.name
                        ),
                    ));
                }
                Some(matched) => {
                    let status = if methods.len() > 1 {
                        "ambiguous"
                    } else {
                        "resolved"
                    };
                    relationships.push(rel(
                        "MyBatisSqlStatement",
                        &sql_param.sql_statement_id,
                        "MyBatisParameter",
                        &matched.stable_id,
                        "DEPENDS_ON_PARAMETER",
                        project_id,
                        &sql_param.source,
                        1.0,
                        status,
                        "SQL placeholder resolved to mapper parameter",
                        vec![
                            (
                                "sql_parameter_id".to_string(),
                                serde_json::json!(sql_param.stable_id),
                            ),
                            (
                                "parameter_name".to_string(),
                                serde_json::json!(sql_param.name),
                            ),
                            (
                                "parameter_kind".to_string(),
                                serde_json::json!(sql_param.parameter_kind),
                            ),
                        ],
                    ));
                }
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn resolve_result_mappings(
    project_id: &str,
    statements: &[StatementFact],
    result_maps: &[ResultMapFact],
    result_mappings: &[ResultMappingFact],
    result_maps_by_ref: &HashMap<String, &ResultMapFact>,
    statements_by_ref: &HashMap<String, &StatementFact>,
    columns_by_statement: &HashMap<&str, Vec<&SqlColumnFact>>,
    properties_by_type_name: &HashMap<(&str, &str), &JavaPropertyFact>,
    relationships: &mut Vec<MyBatisRelationship>,
    diagnostics: &mut Vec<Diagnostic>,
) {
    let maps_by_id: HashMap<&str, &ResultMapFact> = result_maps
        .iter()
        .map(|item| (item.stable_id.as_str(), item))
        .collect();
    let mut statements_using_map: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for stmt in statements {
        let attr = stmt
            .attributes
            .get("resultMap")
            .cloned()
            .unwrap_or_default();
        for reference in attr.split(',') {
            let reference = reference.trim();
            if reference.is_empty() {
                continue;
            }
            if let Some(target) = result_maps_by_ref.get(&qualified_ref(&stmt.namespace, reference))
            {
                statements_using_map
                    .entry(target.stable_id.clone())
                    .or_default()
                    .push(format!("mybatis_sql_stmt::{}", stmt.stable_id));
            }
        }
    }
    for result_map in result_maps {
        if !result_map.extends.is_empty() {
            let target =
                result_maps_by_ref.get(&qualified_ref(&result_map.namespace, &result_map.extends));
            match target {
                None => {
                    diagnostics.push(Diagnostic::new(
                        "mybatis.resolve.result_map_extends_unresolved",
                        &format!(
                            "Unable to resolve parent resultMap {:?}",
                            result_map.extends
                        ),
                    ));
                }
                Some(target) => {
                    relationships.push(rel(
                        "MyBatisResultMap",
                        &result_map.stable_id,
                        "MyBatisResultMap",
                        &target.stable_id,
                        "EXTENDS_RESULT_MAP",
                        project_id,
                        &result_map.source,
                        1.0,
                        "resolved",
                        "resultMap extends",
                        Vec::new(),
                    ));
                }
            }
        }
    }
    let mut emitted: HashSet<(String, String, String, String)> = HashSet::new();
    for mapping in result_mappings {
        let result_map = maps_by_id.get(mapping.result_map_id.as_str()).copied();
        relationships.push(rel(
            "MyBatisResultMap",
            &mapping.result_map_id,
            "MyBatisResultMapping",
            &mapping.stable_id,
            "HAS_RESULT_MAPPING",
            project_id,
            &mapping.source,
            1.0,
            "resolved",
            "result-map mapping member",
            Vec::new(),
        ));
        if let Some(result_map) = result_map
            && !mapping.property_name.is_empty()
        {
            {
                let prop = properties_by_type_name.get(&(
                    result_map.java_type.as_str(),
                    mapping.property_name.as_str(),
                ));
                if let Some(prop) = prop {
                    relationships.push(rel(
                        "MyBatisResultMapping",
                        &mapping.stable_id,
                        "MyBatisJavaProperty",
                        &prop.stable_id,
                        "MAPS_PROPERTY",
                        project_id,
                        &mapping.source,
                        1.0,
                        "resolved",
                        "result mapping property matches Java property",
                        Vec::new(),
                    ));
                }
            }
            if !mapping.column.is_empty()
                && let Some(sql_statement_ids) =
                    statements_using_map.get(&result_map.stable_id)
            {
                {
                    for sql_statement_id in sql_statement_ids {
                        if let Some(columns) =
                            columns_by_statement.get(sql_statement_id.as_str())
                        {
                            for column in columns {
                                if column.role == "projection"
                                    && column.normalized_name == mapping.column.to_lowercase()
                                {
                                    relationships.push(rel(
                                        "MyBatisResultMapping",
                                        &mapping.stable_id,
                                        "DatabaseColumn",
                                        &column.stable_id,
                                        "MAPS_COLUMN",
                                        project_id,
                                        &mapping.source,
                                        1.0,
                                        &column.resolution_status,
                                        "result mapping column matches SQL column",
                                        vec![(
                                            "sql_statement_id".to_string(),
                                            serde_json::json!(sql_statement_id),
                                        )],
                                    ));
                                }
                            }
                        }
                    }
                }
            }
        }
        if !mapping.nested_select.is_empty()
            && let Some(result_map) = result_map
        {
            {
                let target_stmt = statements_by_ref.get(&qualified_ref(
                    &result_map.namespace,
                    &mapping.nested_select,
                ));
                if let Some(target_stmt) = target_stmt {
                    relationships.push(rel(
                        "MyBatisResultMapping",
                        &mapping.stable_id,
                        "MyBatisStatement",
                        &target_stmt.stable_id,
                        "NESTED_SELECT",
                        project_id,
                        &mapping.source,
                        1.0,
                        "resolved",
                        "nested select mapping",
                        Vec::new(),
                    ));
                }
            }
        }
        if !mapping.nested_result_map.is_empty()
            && let Some(result_map) = result_map
        {
            {
                let target_map = result_maps_by_ref.get(&qualified_ref(
                    &result_map.namespace,
                    &mapping.nested_result_map,
                ));
                if let Some(target_map) = target_map {
                    let rel_type = if mapping.mapping_kind == "collection" {
                        "HAS_COLLECTION"
                    } else {
                        "HAS_ASSOCIATION"
                    };
                    relationships.push(rel(
                        "MyBatisResultMapping",
                        &mapping.stable_id,
                        "MyBatisResultMap",
                        &target_map.stable_id,
                        rel_type,
                        project_id,
                        &mapping.source,
                        1.0,
                        "resolved",
                        "nested resultMap mapping",
                        Vec::new(),
                    ));
                }
            }
        }
        let _ = &mut emitted;
    }
}

/// `_match_param`.
fn match_param<'a>(name: &str, params: &[&'a MapperParameterFact]) -> Option<&'a MapperParameterFact> {
    if params.is_empty() {
        return None;
    }
    for param in params {
        let mut aliases: Vec<String> = vec![
            param.name.clone(),
            param.param_alias.clone(),
            format!("param{}", param.position + 1),
            format!("arg{}", param.position),
        ];
        aliases.dedup();
        if aliases.contains(&name.to_string()) {
            return Some(param);
        }
    }
    if params.len() == 1 && name.contains('.') {
        return Some(params[0]);
    }
    None
}

/// `_qualified_ref`.
fn qualified_ref(namespace: &str, refid: &str) -> String {
    if refid.contains('.') {
        refid.to_string()
    } else if !namespace.is_empty() {
        format!("{namespace}.{refid}")
    } else {
        refid.to_string()
    }
}

/// `_statement_ref`.
fn statement_ref(namespace: &str, name: &str) -> String {
    if namespace.is_empty() {
        name.to_string()
    } else {
        format!("{namespace}.{name}")
    }
}

/// `_rel`.
#[allow(clippy::too_many_arguments)]
fn rel(
    from_label: &str,
    from_id: &str,
    to_label: &str,
    to_id: &str,
    rel_type: &str,
    project_id: &str,
    source: &SourceSpan,
    confidence: f64,
    resolution_status: &str,
    reason: &str,
    properties: Vec<(String, serde_json::Value)>,
) -> MyBatisRelationship {
    MyBatisRelationship {
        from_label: from_label.to_string(),
        from_id: from_id.to_string(),
        to_label: to_label.to_string(),
        to_id: to_id.to_string(),
        rel_type: rel_type.to_string(),
        project_id: project_id.to_string(),
        source: source.clone(),
        confidence,
        resolution_status: resolution_status.to_string(),
        reason: reason.to_string(),
        properties,
    }
}

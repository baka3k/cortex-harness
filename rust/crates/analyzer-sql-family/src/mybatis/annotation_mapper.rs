//! Port `tools/mybatis/annotation_mapper.py` — SQL/provider annotations trên
//! mapper methods (synthetic statements, result maps, providers).

use std::collections::BTreeMap;

use regex::Regex;

use crate::mybatis::models::{
    AnnotationFact, Diagnostic, MapperMethodFact, ProviderFact, ResultMapFact,
    ResultMappingFact, SourceSpan, StatementFact,
};

const SQL_ANNOTATIONS: [(&str, &str); 4] = [
    ("org.apache.ibatis.annotations.Select", "select"),
    ("org.apache.ibatis.annotations.Insert", "insert"),
    ("org.apache.ibatis.annotations.Update", "update"),
    ("org.apache.ibatis.annotations.Delete", "delete"),
];

const PROVIDER_ANNOTATIONS: [(&str, &str); 4] = [
    ("org.apache.ibatis.annotations.SelectProvider", "select"),
    ("org.apache.ibatis.annotations.InsertProvider", "insert"),
    ("org.apache.ibatis.annotations.UpdateProvider", "update"),
    ("org.apache.ibatis.annotations.DeleteProvider", "delete"),
];

pub struct AnnotationMapperAnalysis {
    pub statements: Vec<StatementFact>,
    pub result_maps: Vec<ResultMapFact>,
    pub result_mappings: Vec<ResultMappingFact>,
    pub providers: Vec<ProviderFact>,
    pub diagnostics: Vec<Diagnostic>,
}

/// `analyze_annotation_mappers`.
pub fn analyze_annotation_mappers(
    mapper_methods: &[MapperMethodFact],
    project_id: &str,
) -> AnnotationMapperAnalysis {
    let mut statements: Vec<StatementFact> = Vec::new();
    let mut result_maps: Vec<ResultMapFact> = Vec::new();
    let mut result_mappings: Vec<ResultMappingFact> = Vec::new();
    let mut providers: Vec<ProviderFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    for method in mapper_methods {
        let mut statement_attrs: BTreeMap<String, String> = BTreeMap::new();
        statement_attrs.insert("source_kind".to_string(), "annotation".to_string());
        statement_attrs.insert("java_method_id".to_string(), method.stable_id.clone());
        let mut synthetic_result_map_id = String::new();
        for annotation in &method.annotations {
            if annotation.resolved_name == "org.apache.ibatis.annotations.ResultMap" {
                let value = first_annotation_value(&annotation.raw_arguments);
                if !value.is_empty() {
                    statement_attrs.insert("resultMap".to_string(), value);
                }
            } else if annotation.resolved_name == "org.apache.ibatis.annotations.Results" {
                let (result_map, mappings) =
                    results_to_result_map(method, &annotation.raw_arguments, project_id);
                if let Some(result_map) = result_map {
                    synthetic_result_map_id = result_map.result_map_id.clone();
                    result_maps.push(result_map);
                    result_mappings.extend(mappings);
                }
            }
        }
        if !synthetic_result_map_id.is_empty() && !statement_attrs.contains_key("resultMap") {
            statement_attrs.insert("resultMap".to_string(), synthetic_result_map_id);
        }

        for annotation in &method.annotations {
            let statement_kind = SQL_ANNOTATIONS
                .iter()
                .find(|(name, _)| *name == annotation.resolved_name)
                .map(|(_, kind)| kind.to_string());
            if let Some(statement_kind) = statement_kind {
                let sql_text = sql_from_annotation(&annotation.raw_arguments);
                if sql_text.trim().is_empty() {
                    diagnostics.push(Diagnostic::new(
                        "mybatis.annotation.empty_sql",
                        &format!(
                            "{} on {}.{} has no static SQL literal",
                            annotation.name, method.mapper_fqcn, method.name
                        ),
                    ));
                    continue;
                }
                let stable_id = statement_id(project_id, method, &statement_kind);
                let mut attrs = statement_attrs.clone();
                attrs.insert("annotation".to_string(), annotation.resolved_name.clone());
                statements.push(StatementFact {
                    stable_id,
                    namespace: method.mapper_fqcn.clone(),
                    statement_id: method.name.clone(),
                    statement_kind,
                    source: annotation.source.clone(),
                    database_id: "annotation".to_string(),
                    attributes: attrs,
                    raw_body: sql_text.clone(),
                    expanded_body: sql_text,
                    includes: Vec::new(),
                    dynamic_nodes: Vec::new(),
                    parser_status: "parsed".to_string(),
                });
                continue;
            }

            let provider_kind = PROVIDER_ANNOTATIONS
                .iter()
                .find(|(name, _)| *name == annotation.resolved_name)
                .map(|(_, kind)| kind.to_string());
            if let Some(provider_kind) = provider_kind {
                let attrs = named_values(&annotation.raw_arguments);
                let provider_id = provider_id(project_id, method, &provider_kind);
                providers.push(ProviderFact {
                    stable_id: provider_id,
                    mapper_method_id: method.stable_id.clone(),
                    namespace: method.mapper_fqcn.clone(),
                    statement_id: method.name.clone(),
                    provider_kind,
                    source: annotation.source.clone(),
                    provider_type: attrs
                        .get("type")
                        .cloned()
                        .or_else(|| attrs.get("value").cloned())
                        .unwrap_or_default(),
                    provider_method: attrs.get("method").cloned().unwrap_or_default(),
                    raw_arguments: annotation.raw_arguments.clone(),
                    resolution_status: "runtime_generated".to_string(),
                    attributes: attrs,
                });
            }
        }
    }

    AnnotationMapperAnalysis {
        statements,
        result_maps,
        result_mappings,
        providers,
        diagnostics,
    }
}

fn statement_id(project_id: &str, method: &MapperMethodFact, statement_kind: &str) -> String {
    format!(
        "mybatis_stmt::{project_id}::{}::{}::annotation:{statement_kind}",
        method.mapper_fqcn, method.name
    )
}

fn provider_id(project_id: &str, method: &MapperMethodFact, provider_kind: &str) -> String {
    let digest = crate::pyutil::sha1_hex(format!("{}:{provider_kind}", method.stable_id).as_bytes())
        [..12]
        .to_string();
    format!(
        "mybatis_provider::{project_id}::{}::{}::{provider_kind}:{digest}",
        method.mapper_fqcn, method.name
    )
}

fn result_map_id(project_id: &str, method: &MapperMethodFact) -> String {
    format!(
        "mybatis_result_map::{project_id}::{}::{}::annotationResults",
        method.mapper_fqcn, method.name
    )
}

/// `_sql_from_annotation`.
fn sql_from_annotation(raw_arguments: &str) -> String {
    let values = string_literals(raw_arguments);
    let text = values
        .iter()
        .map(|value| value.trim())
        .filter(|value| !value.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    strip_script_wrapper(&text)
}

/// `_first_annotation_value`.
fn first_annotation_value(raw_arguments: &str) -> String {
    let values = string_literals(raw_arguments);
    values.into_iter().next().unwrap_or_default()
}

/// `_string_literals` — regex + ast.literal_eval unescape.
fn string_literals(raw_arguments: &str) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    let re = Regex::new(r#""(?:\\.|[^"\\])*""#).unwrap();
    for m in re.find_iter(raw_arguments) {
        let literal = m.as_str();
        rows.push(
            crate::pyutil::unescape_string_literal(literal)
                .unwrap_or_else(|| literal.trim_matches('"').to_string()),
        );
    }
    rows
}

/// `_strip_script_wrapper`.
fn strip_script_wrapper(text: &str) -> String {
    let stripped = text.trim();
    let re = Regex::new(r"(?is)^<script>\s*(.*?)\s*</script>$").unwrap();
    match re.captures(stripped) {
        Some(caps) => caps.get(1).unwrap().as_str().trim().to_string(),
        None => stripped.to_string(),
    }
}

/// `_named_values`.
fn named_values(raw_arguments: &str) -> BTreeMap<String, String> {
    let mut values: BTreeMap<String, String> = BTreeMap::new();
    let re = Regex::new(
        r#"(\w+)\s*=\s*(?:([A-Za-z_][\w.]*)(?:\.class)?|("(?:\\.|[^"\\])*"))"#,
    )
    .unwrap();
    for caps in re.captures_iter(raw_arguments) {
        let key = caps.get(1).unwrap().as_str().to_string();
        let identifier = caps.get(2).map(|m| m.as_str().to_string());
        let literal = caps.get(3).map(|m| m.as_str().to_string());
        let mut value = literal.clone().or(identifier).unwrap_or_default();
        if value.starts_with('"') {
            value = crate::pyutil::unescape_string_literal(&value)
                .unwrap_or_else(|| value.trim_matches('"').to_string());
        }
        values.insert(key, value);
    }
    let unnamed = string_literals(raw_arguments);
    if !unnamed.is_empty() && !values.contains_key("value") {
        values.insert("value".to_string(), unnamed[0].clone());
    }
    values
}

/// `_results_to_result_map`.
fn results_to_result_map(
    method: &MapperMethodFact,
    raw_arguments: &str,
    project_id: &str,
) -> (Option<ResultMapFact>, Vec<ResultMappingFact>) {
    let re = Regex::new(
        r"(?s)@(?:org\.apache\.ibatis\.annotations\.)?Result\s*\((.*?)\)",
    )
    .unwrap();
    let result_chunks: Vec<regex::Captures<'_>> = re.captures_iter(raw_arguments).collect();
    if result_chunks.is_empty() {
        return (None, Vec::new());
    }
    let map_id = result_map_id(project_id, method);
    let synthetic_name = format!("{}::annotationResults", method.name);
    let mut mappings: Vec<ResultMappingFact> = Vec::new();
    for (index, caps) in result_chunks.iter().enumerate() {
        let chunk = caps.get(1).unwrap().as_str();
        let attrs = named_values(chunk);
        let property_name = attrs.get("property").cloned().unwrap_or_default();
        let column = attrs.get("column").cloned().unwrap_or_default();
        mappings.push(ResultMappingFact {
            stable_id: format!("mybatis_result_mapping::{map_id}::{index}"),
            result_map_id: map_id.clone(),
            mapping_kind: "result".to_string(),
            source: method.source.clone(),
            property_name,
            column,
            java_type: attrs.get("javaType").cloned().unwrap_or_default(),
            jdbc_type: attrs.get("jdbcType").cloned().unwrap_or_default(),
            nested_select: String::new(),
            nested_result_map: String::new(),
            attributes: attrs,
        });
    }
    (
        Some(ResultMapFact {
            stable_id: map_id,
            namespace: method.mapper_fqcn.clone(),
            result_map_id: synthetic_name,
            source: method.source.clone(),
            java_type: unwrap_collection(&method.return_type),
            extends: String::new(),
            auto_mapping: String::new(),
            mappings: mappings.clone(),
        }),
        mappings,
    )
}

/// `_unwrap_collection`.
fn unwrap_collection(type_text: &str) -> String {
    let re = Regex::new(r"<\s*([A-Za-z_][\w.]*)\s*>").unwrap();
    match re.captures(type_text) {
        Some(caps) => caps.get(1).unwrap().as_str().to_string(),
        None => type_text.to_string(),
    }
}

// keep SourceSpan import used for signatures
#[allow(dead_code)]
fn _span_anchor(_: SourceSpan) {}
#[allow(dead_code)]
fn _annotation_anchor(_: AnnotationFact) {}

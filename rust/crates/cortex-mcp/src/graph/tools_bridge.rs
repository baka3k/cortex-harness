// Faithful port của Python bodies: giữ cấu trúc nguồn để đối chiếu parity;
// các lint style dưới đây được allow có chủ đích ở module graph.
#![allow(
    clippy::too_many_arguments,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::unnecessary_filter_map,
    clippy::needless_borrow,
    clippy::map_clone,
    clippy::obfuscated_if_else,
    clippy::unnecessary_lazy_evaluations,
    clippy::let_and_return,
    clippy::useless_format,
    clippy::manual_strip,
    clippy::unwrap_or_default
)]
//! Bridge + project-context tools — port của unified_mcp
//! `tool_find_callers_of_endpoint`, `tool_get_api_call_chain`,
//! `tool_find_workflows_containing` và `services/project_context_service.py`.

use std::collections::BTreeMap;

use serde_json::{json, Map, Value};

use super::{
    lookup_key, resolve_direct_capability_context, run_cypher_first,
};
use crate::framework_registry::{capability_for_parser, default_relationships};
use super::runtime;


fn value_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}


fn row_opt(row: &Map<String, Value>, key: &str) -> Option<Value> {
    row.get(key).cloned()
}

fn format_props(props: &[String]) -> String {
    format!("{{{}}}", props.join(", "))
}

fn payload_str_or_empty<'a>(payload: &'a Value, key: &str) -> &'a str {
    payload.get(key).and_then(Value::as_str).unwrap_or("")
}

// ---------------------------------------------------------------------------
// find_callers_of_endpoint
// ---------------------------------------------------------------------------

pub fn tool_find_callers_of_endpoint(
    runtime: &mut runtime::GraphRuntime,
    arguments: Value,
) -> Result<Value, String> {
    let endpoint_path = payload_str_or_empty(&arguments, "endpoint_path");
    if endpoint_path.is_empty() {
        return Ok(json!({
            "endpoint_path": "",
            "callers": [],
            "total": 0,
            "error": "endpoint_path is required",
        }));
    }
    let http_method = payload_str_or_empty(&arguments, "http_method");
    let be_project_id = payload_str_or_empty(&arguments, "be_project_id");
    let fe_project_id = payload_str_or_empty(&arguments, "fe_project_id");
    let project_id = payload_str_or_empty(&arguments, "project_id");
    let parser_type = payload_str_or_empty(&arguments, "parser_type");

    let effective_project_id = if !project_id.is_empty() {
        project_id
    } else if !be_project_id.is_empty() {
        be_project_id
    } else {
        fe_project_id
    };
    let database = super::tools_search::resolve_graph_database(if effective_project_id.is_empty() {
        None
    } else {
        Some(effective_project_id)
    });
    let context = resolve_direct_capability_context(
        runtime,
        "find_callers_of_endpoint",
        Some(parser_type),
        database.as_deref(),
        &["CALLS_API", "MATCHES"],
        &["ApiEndpoint", "ApiCall"],
    )?;
    if let Some(error) = context.error {
        return Ok(error);
    }

    let method_filter = http_method.trim().to_uppercase();
    let mut params: Map<String, Value> = Map::new();
    params.insert("path".to_string(), json!(endpoint_path));
    let mut ep_props: Vec<String> = vec!["path: $path".to_string()];
    if !be_project_id.is_empty() {
        ep_props.push("project_id_normalized: $be_project_normalized".to_string());
        params.insert("be_project".to_string(), json!(be_project_id));
    }

    let mut endpoint_match_lines: Vec<String> = Vec::new();
    if !method_filter.is_empty() && method_filter != "ALL" {
        params.insert("method".to_string(), json!(method_filter));
        let exact_props = format!(
            "{{{}, http_method: $method}}",
            ep_props.join(", ")
        );
        let all_props = format!(
            "{{{}, http_method: 'ALL'}}",
            ep_props.join(", ")
        );
        endpoint_match_lines.extend([
            "CALL () {".to_string(),
            format!("  MATCH (ep:ApiEndpoint {exact_props})"),
            "  RETURN ep".to_string(),
            "  UNION".to_string(),
            format!("  MATCH (ep:ApiEndpoint {all_props})"),
            "  RETURN ep".to_string(),
            "}".to_string(),
        ]);
    } else if method_filter == "ALL" {
        let all_props = format!(
            "{{{}, http_method: 'ALL'}}",
            ep_props.join(", ")
        );
        endpoint_match_lines.push(format!("MATCH (ep:ApiEndpoint {all_props})"));
    } else {
        endpoint_match_lines.push(format!(
            "MATCH (ep:ApiEndpoint {})",
            format_props(&ep_props)
        ));
    }

    let mut api_call_match = "MATCH (ac:ApiCall)-[m:MATCHES]->(ep)".to_string();
    if !fe_project_id.is_empty() {
        params.insert("fe_project".to_string(), json!(fe_project_id));
        api_call_match =
            "MATCH (ac:ApiCall {project_id_normalized: $fe_project_normalized})-[m:MATCHES]->(ep)"
                .to_string();
    }

    let cypher = [
        endpoint_match_lines,
        vec![
            format!("WITH ep WHERE {}", super::servlet_predicate("ep")),
            api_call_match,
            "MATCH (f:Function)-[:CALLS_API]->(ac)".to_string(),
            "RETURN f.name          AS function_name,".to_string(),
            "       f.qualified_name AS qualified_name,".to_string(),
            "       f.react_role    AS react_role,".to_string(),
            "       f.file_path     AS file_path,".to_string(),
            "       f.start_line    AS start_line,".to_string(),
            "       f.project_id    AS project_id,".to_string(),
            "       ac.url_pattern  AS url_pattern,".to_string(),
            "       m.confidence    AS confidence".to_string(),
            "ORDER BY m.confidence DESC".to_string(),
            "LIMIT 50".to_string(),
        ],
    ]
    .concat()
    .join("\n");

    let mut result = match run_cypher_first(runtime, &cypher, &params, super::runtime::dbs_slice(database.as_deref()).as_slice()) {
        Ok((_, callers)) => {
            let total = callers.len();
            let callers_json: Vec<Value> = callers.into_iter().map(Value::Object).collect();
            json!({
                "endpoint_path": endpoint_path,
                "callers": Value::Array(callers_json),
                "total": total,
            })
        }
        Err(error) => json!({
            "endpoint_path": endpoint_path,
            "callers": [],
            "total": 0,
            "error": super::normalize_driver_error(&error),
        }),
    };
    if let Some(object) = result.as_object_mut() {
        object.insert("capability".to_string(), context.routing);
        if let Some(diagnostics) = context.diagnostics {
            object.insert("capability_diagnostics".to_string(), diagnostics);
        }
    }
    Ok(result)
}

// ---------------------------------------------------------------------------
// get_api_call_chain
// ---------------------------------------------------------------------------

pub fn tool_get_api_call_chain(
    runtime: &mut runtime::GraphRuntime,
    arguments: Value,
) -> Result<Value, String> {
    let component_name = payload_str_or_empty(&arguments, "component_name");
    let endpoint_path = payload_str_or_empty(&arguments, "endpoint_path");
    let fe_project_id = payload_str_or_empty(&arguments, "fe_project_id");
    let be_project_id = payload_str_or_empty(&arguments, "be_project_id");
    let project_id = payload_str_or_empty(&arguments, "project_id");
    let max_depth_raw = payload_str_or_empty(&arguments, "max_depth");
    let parser_type = payload_str_or_empty(&arguments, "parser_type");

    let effective_project_id = if !project_id.is_empty() {
        project_id
    } else if !be_project_id.is_empty() {
        be_project_id
    } else {
        fe_project_id
    };
    let database = super::tools_search::resolve_graph_database(if effective_project_id.is_empty() {
        None
    } else {
        Some(effective_project_id)
    });
    let depth: i64 = max_depth_raw.parse().unwrap_or(5);

    if component_name.is_empty() && endpoint_path.is_empty() {
        return Ok(json!({
            "chains": [],
            "total": 0,
            "error": "component_name or endpoint_path required",
        }));
    }

    let context = resolve_direct_capability_context(
        runtime,
        "get_api_call_chain",
        Some(parser_type),
        database.as_deref(),
        &["CALLS", "CALLS_API", "MATCHES", "HANDLES"],
        &["ApiEndpoint"],
    )?;
    if let Some(error) = context.error {
        return Ok(error);
    }
    let selected_capability = capability_for_parser(context.selected_parser.as_deref());
    let flow_defaults: Vec<String> = match &selected_capability {
        Some(capability) => default_relationships(Some(capability.name.as_str()), None),
        None => vec!["CALLS".to_string()],
    };
    let flow_relationships = super::relationship_pattern(
        &context
            .relationships
            .iter()
            .filter(|relationship| flow_defaults.contains(relationship))
            .cloned()
            .collect::<Vec<_>>(),
        "CALLS",
    );

    let params: Map<String, Value>;
    let cypher;
    if !component_name.is_empty() {
        let mut fe_props = vec!["name: $component_name".to_string()];
        let mut params_map: Map<String, Value> = Map::new();
        params_map.insert("component_name".to_string(), json!(component_name));
        if !fe_project_id.is_empty() {
            fe_props.push("project_id_normalized: $fe_project_normalized".to_string());
            params_map.insert("fe_project".to_string(), json!(fe_project_id));
        }
        let mut endpoint_match = "MATCH (caller)-[:CALLS_API]->(ac:ApiCall)-[m:MATCHES]->(ep:ApiEndpoint)".to_string();
        if !be_project_id.is_empty() {
            endpoint_match = "MATCH (caller)-[:CALLS_API]->(ac:ApiCall)-[m:MATCHES]->(ep:ApiEndpoint {project_id_normalized: $be_project_normalized})".to_string();
            params_map.insert("be_project".to_string(), json!(be_project_id));
        }
        params = params_map;
        cypher = [
            vec![
                format!("MATCH (fe:Function {})", format_props(&fe_props)),
                format!("MATCH (fe)-[:{flow_relationships}*0..{depth}]->(caller:Function)"),
                endpoint_match,
                format!(
                    "WITH fe, caller, ac, m, ep WHERE {}",
                    super::servlet_predicate("ep")
                ),
                "OPTIONAL MATCH (ep)-[:HANDLES]->(forwardCtrl:Controller)".to_string(),
                "OPTIONAL MATCH (reverseCtrl:Controller)-[:HANDLES]->(ep)".to_string(),
                "OPTIONAL MATCH (ep)-[:SEMANTIC_OF]->(servletHandler:Function)".to_string(),
                "WITH fe, caller, ac, m, ep, coalesce(forwardCtrl, reverseCtrl, servletHandler) AS ctrl".to_string(),
                format!("OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..3]->(svc:Service)"),
                format!("OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..5]->(repo)"),
                "WHERE repo:Repository OR repo:DataRepository OR repo:MyBatisMapper OR repo:MyBatisMapperMethod".to_string(),
                "OPTIONAL MATCH (repo)-[:DECLARES_QUERY|DERIVES_QUERY|QUERIES|BINDS_STATEMENT|DECLARES_STATEMENT*0..3]-(persistence)".to_string(),
                "OPTIONAL MATCH (persistence)-[:READS_FROM|WRITES_TO|REFERENCES_TABLE]->(table:DatabaseTable)".to_string(),
                "OPTIONAL MATCH (repo)-[:QUERIES]->(dbnode:Database)".to_string(),
            ],
            vec![
                "RETURN fe.name        AS fe_component,".to_string(),
                "       caller.name    AS fe_api_caller,".to_string(),
                "       caller.file_path AS fe_file_path,".to_string(),
                "       ac.url_pattern AS url_pattern,".to_string(),
                "       ac.http_method AS http_method,".to_string(),
                "       m.confidence   AS match_confidence,".to_string(),
                "       ep.path        AS be_endpoint_path,".to_string(),
                "       ep.http_method AS be_method,".to_string(),
                "       ep.framework   AS be_framework,".to_string(),
                "       ctrl.name      AS be_controller,".to_string(),
                "       svc.name      AS be_service,".to_string(),
                "       repo.name      AS be_repository,".to_string(),
                "       dbnode.name    AS be_database,".to_string(),
                "       persistence.name AS persistence_fact,".to_string(),
                "       table.name     AS database_table".to_string(),
                "ORDER BY m.confidence DESC".to_string(),
                "LIMIT 30".to_string(),
            ],
        ]
        .concat()
        .join("\n");
    } else {
        let mut ep_props = vec!["path: $endpoint_path".to_string()];
        let mut params_map: Map<String, Value> = Map::new();
        params_map.insert("endpoint_path".to_string(), json!(endpoint_path));
        if !be_project_id.is_empty() {
            ep_props.push("project_id_normalized: $be_project_normalized".to_string());
            params_map.insert("be_project".to_string(), json!(be_project_id));
        }
        let mut api_call_match = "MATCH (ac:ApiCall)-[m:MATCHES]->(ep)".to_string();
        if !fe_project_id.is_empty() {
            api_call_match = "MATCH (ac:ApiCall {project_id_normalized: $fe_project_normalized})-[m:MATCHES]->(ep)".to_string();
            params_map.insert("fe_project".to_string(), json!(fe_project_id));
        }
        params = params_map;
        cypher = [
            vec![
                format!("MATCH (ep:ApiEndpoint {})", format_props(&ep_props)),
                format!(
                    "WITH ep WHERE {}",
                    super::servlet_predicate("ep")
                ),
                api_call_match,
                "MATCH (caller:Function)-[:CALLS_API]->(ac)".to_string(),
                "WITH caller, ac, m, ep".to_string(),
                "OPTIONAL MATCH (ep)-[:HANDLES]->(forwardCtrl:Controller)".to_string(),
                "OPTIONAL MATCH (reverseCtrl:Controller)-[:HANDLES]->(ep)".to_string(),
                "OPTIONAL MATCH (ep)-[:SEMANTIC_OF]->(servletHandler:Function)".to_string(),
                "WITH caller, ac, m, ep, coalesce(forwardCtrl, reverseCtrl, servletHandler) AS ctrl".to_string(),
                format!("OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..3]->(svc:Service)"),
                format!("OPTIONAL MATCH (ctrl)-[:{flow_relationships}*0..5]->(repo)"),
                "WHERE repo:Repository OR repo:DataRepository OR repo:MyBatisMapper OR repo:MyBatisMapperMethod".to_string(),
                "OPTIONAL MATCH (repo)-[:DECLARES_QUERY|DERIVES_QUERY|QUERIES|BINDS_STATEMENT|DECLARES_STATEMENT*0..3]-(persistence)".to_string(),
                "OPTIONAL MATCH (persistence)-[:READS_FROM|WRITES_TO|REFERENCES_TABLE]->(table:DatabaseTable)".to_string(),
                "OPTIONAL MATCH (repo)-[:QUERIES]->(dbnode:Database)".to_string(),
            ],
            vec![
                "RETURN caller.name    AS fe_component,".to_string(),
                "       caller.name    AS fe_api_caller,".to_string(),
                "       caller.file_path AS fe_file_path,".to_string(),
                "       ac.url_pattern AS url_pattern,".to_string(),
                "       ac.http_method AS http_method,".to_string(),
                "       m.confidence   AS match_confidence,".to_string(),
                "       ep.path        AS be_endpoint_path,".to_string(),
                "       ep.http_method AS be_method,".to_string(),
                "       ep.framework   AS be_framework,".to_string(),
                "       ctrl.name      AS be_controller,".to_string(),
                "       svc.name      AS be_service,".to_string(),
                "       repo.name      AS be_repository,".to_string(),
                "       dbnode.name    AS be_database,".to_string(),
                "       persistence.name AS persistence_fact,".to_string(),
                "       table.name     AS database_table".to_string(),
                "ORDER BY m.confidence DESC".to_string(),
                "LIMIT 30".to_string(),
            ],
        ]
        .concat()
        .join("\n");
    }

    let mut result = match run_cypher_first(
        runtime,
        &cypher,
        &params,
        super::runtime::dbs_slice(database.as_deref()).as_slice(),
    ) {
        Ok((_, rows)) => {
            let chains: Vec<Value> = rows
                .iter()
                .map(|row| {
                    let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
                    json!({
                        "fe_component": get("fe_component"),
                        "fe_api_caller": get("fe_api_caller"),
                        "fe_file_path": get("fe_file_path"),
                        "api_call": {
                            "url_pattern": get("url_pattern"),
                            "http_method": get("http_method"),
                        },
                        "match_confidence": get("match_confidence"),
                        "be_endpoint": {
                            "path": get("be_endpoint_path"),
                            "method": get("be_method"),
                            "framework": get("be_framework"),
                        },
                        "be_controller": get("be_controller"),
                        "be_service": get("be_service"),
                        "be_repository": get("be_repository"),
                        "be_database": get("be_database"),
                        "persistence_fact": get("persistence_fact"),
                        "database_table": get("database_table"),
                    })
                })
                .collect();
            json!({"chains": chains, "total": chains.len()})
        }
        Err(error) => json!({"chains": [], "total": 0, "error": super::normalize_driver_error(&error)}),
    };
    if let Some(object) = result.as_object_mut() {
        object.insert("capability".to_string(), context.routing);
        if let Some(diagnostics) = context.diagnostics {
            object.insert("capability_diagnostics".to_string(), diagnostics);
        }
    }
    Ok(result)
}

// ---------------------------------------------------------------------------
// find_workflows_containing
// ---------------------------------------------------------------------------

pub fn tool_find_workflows_containing(
    runtime: &mut runtime::GraphRuntime,
    arguments: Value,
) -> Result<Value, String> {
    let function_id = payload_str_or_empty(&arguments, "function_id");
    let project_id = payload_str_or_empty(&arguments, "project_id");
    let include_indirect = arguments
        .get("include_indirect")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let max_depth = arguments
        .get("max_depth")
        .and_then(Value::as_i64)
        .unwrap_or(4);
    let parser_type = payload_str_or_empty(&arguments, "parser_type");

    let database = super::tools_search::resolve_graph_database(if project_id.is_empty() {
        None
    } else {
        Some(project_id)
    });
    let capped = max_depth.min(4);
    let required: Vec<&str> = if include_indirect {
        vec!["HAS_STEP", "CALLS"]
    } else {
        vec!["HAS_STEP"]
    };
    let context = resolve_direct_capability_context(
        runtime,
        "find_workflows_containing",
        Some(parser_type),
        database.as_deref(),
        &required,
        &[],
    )?;
    if let Some(error) = context.error {
        return Ok(error);
    }
    let selected_capability = capability_for_parser(context.selected_parser.as_deref());
    let flow_defaults: Vec<String> = match &selected_capability {
        Some(capability) => default_relationships(Some(capability.name.as_str()), None),
        None => vec!["CALLS".to_string()],
    };
    let flow_relationships = super::relationship_pattern(
        &context
            .relationships
            .iter()
            .filter(|relationship| flow_defaults.contains(relationship))
            .cloned()
            .collect::<Vec<_>>(),
        "CALLS",
    );

    let direct_cypher = "\nMATCH (w:Workflow)-[s:HAS_STEP]->(f:Function)\nWHERE f.symbol_id = $id OR f.file_path = $id\nRETURN w.workflow_id                AS workflow_id,\n       w.name                       AS name,\n       coalesce(w.domain, '')       AS domain,\n       coalesce(w.confidence, 0.5)  AS confidence,\n       coalesce(s.order, -1)        AS step_index\nORDER BY w.confidence DESC\n";
    let indirect_cypher = format!("\nMATCH (w:Workflow)-[:HAS_STEP]->(entry:Function)\nMATCH path = (entry)-[:{flow_relationships}*1..{capped}]->(f:Function)\nWHERE (f.symbol_id = $id OR f.file_path = $id)\n  AND NOT w.workflow_id IN $direct_ids\nRETURN DISTINCT\n       w.workflow_id                AS workflow_id,\n       w.name                       AS name,\n       coalesce(w.domain, '')       AS domain,\n       coalesce(w.confidence, 0.5)  AS confidence,\n       length(path)                 AS call_depth\nORDER BY call_depth ASC, confidence DESC\nLIMIT 30\n");
    let direct_ids_value;
    let mut direct_params = Map::new();
    direct_params.insert("id".to_string(), json!(function_id));
    let mut result = match run_cypher_first(
        runtime,
        direct_cypher,
        &direct_params,
        super::runtime::dbs_slice(database.as_deref()).as_slice(),
    ) {
        Ok((_, direct_rows)) => {
            let direct_ids: Vec<Value> = direct_rows
                .iter()
                .filter_map(|row| row.get("workflow_id").cloned())
                .collect();
            direct_ids_value = direct_ids.clone();
            let mut indirect_rows: Vec<Map<String, Value>> = Vec::new();
            if include_indirect {
                let mut params = Map::new();
                params.insert("id".to_string(), json!(function_id));
                params.insert("direct_ids".to_string(), Value::Array(direct_ids));
                match run_cypher_first(
                    runtime,
                    &indirect_cypher,
                    &params,
                    super::runtime::dbs_slice(database.as_deref()).as_slice(),
                ) {
                    Ok((_, rows)) => indirect_rows = rows,
                    Err(error) => {
                        return Ok(json!({
                            "function_id": function_id,
                            "direct_workflows": [],
                            "indirect_workflows": [],
                            "total": 0,
                            "error": super::normalize_driver_error(&error),
                            "capability": context.routing,
                        }));
                    }
                }
            }
            let direct_json: Vec<Value> = direct_rows.into_iter().map(Value::Object).collect();
            let indirect_json: Vec<Value> = indirect_rows.into_iter().map(Value::Object).collect();
            let total = direct_json.len() as i64 + indirect_json.len() as i64;
            json!({
                "function_id": function_id,
                "direct_workflows": direct_json,
                "indirect_workflows": indirect_json,
                "total": total,
            })
        }
        Err(error) => {
            direct_ids_value = Vec::new();
            json!({
                "function_id": function_id,
                "direct_workflows": [],
                "indirect_workflows": [],
                "total": 0,
                "error": super::normalize_driver_error(&error),
            })
        }
    };
    let _ = direct_ids_value;
    if let Some(object) = result.as_object_mut() {
        object.insert("capability".to_string(), context.routing);
        if let Some(diagnostics) = context.diagnostics {
            object.insert("capability_diagnostics".to_string(), diagnostics);
        }
    }
    Ok(result)
}

// ---------------------------------------------------------------------------
// Project context tools (project_context_service.py)
// ---------------------------------------------------------------------------

fn positive_int(value: Option<&Value>, default: i64, maximum: i64) -> Result<i64, String> {
    let parsed = match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(default),
        _ => default,
    };
    if parsed < 0 {
        return Err("pagination values cannot be negative".to_string());
    }
    Ok(parsed.min(maximum))
}

fn list_value(value: Option<&Value>) -> Vec<Value> {
    match value {
        None => Vec::new(),
        Some(Value::Array(items)) => items.clone(),
        Some(Value::String(text)) => {
            let trimmed = text.trim();
            if trimmed.is_empty() {
                return Vec::new();
            }
            if trimmed.starts_with('[') || trimmed.starts_with('{') {
                if let Ok(decoded) = serde_json::from_str::<Value>(trimmed) {
                    return match decoded {
                        Value::Array(items) => items,
                        other => vec![other],
                    };
                }
            }
            vec![Value::String(text.clone())]
        }
        Some(other) => vec![other.clone()],
    }
}

fn mapping_value(value: Option<&Value>) -> Map<String, Value> {
    match value {
        Some(Value::Object(object)) => object.clone(),
        Some(Value::String(text)) if text.trim().starts_with('{') => serde_json::from_str::<Value>(text)
            .ok()
            .and_then(|decoded| decoded.as_object().cloned())
            .unwrap_or_default(),
        _ => Map::new(),
    }
}

fn total_of(rows: &[Map<String, Value>], fallback: i64) -> i64 {
    if let Some(first) = rows.first()
        && let Some(total) = first.get("total")
        && !total.is_null()
    {
        return total.as_i64().unwrap_or(fallback);
    }
    fallback
}

fn scope_normalized(project_id: &str) -> Result<(String, String), String> {
    let raw = project_id.trim();
    let normalized = lookup_key(Some(raw)).ok_or("project_id is required")?;
    Ok((raw.to_string(), normalized))
}

struct Ctx {
    raw: String,
    normalized: String,
    page_offset: i64,
    page_limit: i64,
}

fn context_of(
    arguments: &Value,
    offset_key: &str,
    limit_key: &str,
    default_limit: i64,
    max_limit: i64,
) -> Result<Ctx, String> {
    let project_id = payload_str_or_empty(arguments, "project_id");
    let (raw, normalized) = scope_normalized(project_id)?;
    let page_offset = positive_int(arguments.get(offset_key), 0, 1_000_000)?;
    let page_limit = positive_int(arguments.get(limit_key), default_limit, max_limit)?;
    Ok(Ctx {
        raw,
        normalized,
        page_offset,
        page_limit,
    })
}

fn run_query(
    runtime: &mut runtime::GraphRuntime,
    cypher: &str,
    params: &Map<String, Value>,
    database: Option<&str>,
) -> Result<Vec<Map<String, Value>>, String> {
    let dbs: Vec<String> = database.map(str::to_string).into_iter().collect();
    run_cypher_first(runtime, cypher, params, &dbs).map(|(_, rows)| rows)
}

fn get_project_modules_service(
    runtime: &mut runtime::GraphRuntime,
    arguments: &Value,
) -> Result<Value, String> {
    let ctx = context_of(arguments, "offset", "limit", 50, 200)?;
    let module_id = payload_str_or_empty(arguments, "module_id");
    let module_path = payload_str_or_empty(arguments, "module_path").replace('\\', "/");
    let include_dependencies = arguments
        .get("include_dependencies")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let params: Map<String, Value> = Map::from_iter([
        ("project_id_normalized".to_string(), json!(ctx.normalized)),
        ("module_id".to_string(), json!(module_id)),
        ("module_path".to_string(), json!(module_path)),
        ("offset".to_string(), json!(ctx.page_offset)),
        ("limit".to_string(), json!(ctx.page_limit)),
    ]);
    let count_rows = run_query(runtime, MODULES_COUNT, &params, None)?;
    let rows = run_query(runtime, MODULES_PAGE, &params, None)?;
    let mut modules: Vec<Value> = Vec::new();
    for row in &rows {
        let mut dependencies: Vec<Value> = Vec::new();
        for raw_dependency in list_value(row.get("dependencies")) {
            let Some(dep_object) = raw_dependency.as_object() else { continue };
            if dep_object.get("id").map(Value::is_null).unwrap_or(true) {
                continue;
            }
            let mut dependency = dep_object.clone();
            let target_labels: Vec<String> = list_value(dependency.get("target_labels"))
                .into_iter()
                .filter_map(|item| match item {
                    Value::String(text) => Some(text),
                    other => Some(other.to_string()),
                })
                .filter(|item| !item.is_empty())
                .collect();
            dependency.remove("target_labels");
            let internal = target_labels.iter().any(|label| label == "ProjectModule");
            dependency.insert("internal".to_string(), json!(internal));
            let target_kind = if internal {
                "ProjectModule".to_string()
            } else if target_labels.iter().any(|label| label == "Dependency") {
                "Dependency".to_string()
            } else {
                dependency
                    .get("target_kind")
                    .map(value_string)
                    .filter(|value| !value.is_empty())
                    .unwrap_or_else(|| "unknown".to_string())
            };
            dependency.insert("target_kind".to_string(), json!(target_kind));
            dependencies.push(Value::Object(dependency));
        }
        let get_list = |key: &str| -> Vec<String> {
            let mut items: Vec<String> = list_value(row.get(key))
                .into_iter()
                .filter_map(|item| match item {
                    Value::String(text) => Some(text),
                    other => Some(other.to_string()),
                })
                .filter(|item| !item.is_empty())
                .collect();
            items.sort();
            items
        };
        let mut descriptors: Vec<Map<String, Value>> = list_value(row.get("descriptors"))
            .into_iter()
            .filter_map(|item| item.as_object().cloned())
            .filter(|item| !item.get("id").map(Value::is_null).unwrap_or(true))
            .collect();
        descriptors.sort_by(|left, right| {
            let left_key = (
                left.get("path").map(value_string).unwrap_or_default(),
                left.get("id").map(value_string).unwrap_or_default(),
            );
            let right_key = (
                right.get("path").map(value_string).unwrap_or_default(),
                right.get("id").map(value_string).unwrap_or_default(),
            );
            left_key.cmp(&right_key)
        });
        let internal_dependencies: Vec<Value> = dependencies
            .iter()
            .filter(|item| item.get("internal").and_then(Value::as_bool).unwrap_or(false))
            .cloned()
            .collect();
        let external_dependencies: Vec<Value> = dependencies
            .iter()
            .filter(|item| !item.get("internal").and_then(Value::as_bool).unwrap_or(false))
            .cloned()
            .collect();
        let total = total_of(&count_rows, modules.len() as i64);
        let _ = total;
        modules.push(json!({
            "module_id": row.get("module_id").cloned().filter(|v| !v.is_null()).or_else(|| row.get("id").cloned()).unwrap_or(Value::Null),
            "name": row.get("name").cloned().filter(|v| !v.is_null()).unwrap_or(json!("")),
            "module_path": row.get("module_path").cloned().filter(|v| !v.is_null()).unwrap_or(json!(".")),
            "kind": row.get("kind").cloned().filter(|v| !v.is_null()).unwrap_or(json!("unknown")),
            "languages": get_list("languages"),
            "frameworks": get_list("frameworks"),
            "build_systems": get_list("build_systems"),
            "source_roots": get_list("source_roots"),
            "descriptor_ids": get_list("descriptor_ids"),
            "descriptors": descriptors.into_iter().map(Value::Object).collect::<Vec<_>>(),
            "dependencies": if include_dependencies { dependencies } else { Vec::new() },
            "internal_dependencies": if include_dependencies { internal_dependencies } else { Vec::new() },
            "external_dependencies": if include_dependencies { external_dependencies } else { Vec::new() },
            "confidence": row.get("confidence").cloned().filter(|v| !v.is_null()).unwrap_or(json!("unknown")),
            "diagnostics": list_value(row.get("diagnostics")),
        }));
    }
    let total = total_of(&count_rows, modules.len() as i64);
let has_more_value = ctx.page_offset + modules.len() as i64;
    Ok(json!({
        "ok": true,
        "project_id": ctx.raw,
        "modules": modules,
        "total": total,
        "offset": ctx.page_offset,
        "limit": ctx.page_limit,
        "has_more": has_more_value,
    }))
}

const MODULES_COUNT: &str = "\n            /* project_context:modules:count */\n            MATCH (m:ProjectModule)\n            WHERE m.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR m.id = $module_id)\n              AND ($module_path = '' OR m.module_path = $module_path)\n            RETURN count(m) AS total\n            ";
const MODULES_PAGE: &str = "\n            /* project_context:modules:page */\n            MATCH (m:ProjectModule)\n            WHERE m.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR m.id = $module_id)\n              AND ($module_path = '' OR m.module_path = $module_path)\n            OPTIONAL MATCH (m)-[:HAS_DESCRIPTOR]->(d:BuildDescriptor)\n            OPTIONAL MATCH (m)-[dep:DEPENDS_ON]->(target)\n            RETURN m.id AS module_id,\n                   m.name AS name,\n                   m.module_path AS module_path,\n                   m.kind AS kind,\n                   m.languages AS languages,\n                   m.frameworks AS frameworks,\n                   m.build_systems AS build_systems,\n                   m.source_roots AS source_roots,\n                   m.confidence AS confidence,\n                   m.diagnostics AS diagnostics,\n                   collect(DISTINCT d.id) AS descriptor_ids,\n                   collect(DISTINCT {\n                     id: d.id,\n                     path: d.file_path,\n                     descriptor_type: d.descriptor_type,\n                     role: d.role,\n                     parse_depth: d.parse_depth\n                   }) AS descriptors,\n                   collect(DISTINCT {\n                     id: dep.id,\n                     target_id: target.id,\n                     target_name: target.name,\n                     target_kind: labels(target)[0],\n                     scope: dep.scope,\n                     target_labels: labels(target)\n                   }) AS dependencies\n            ORDER BY m.module_path, m.id\n            SKIP $offset LIMIT $limit\n            ";

fn get_public_apis_service(
    runtime: &mut runtime::GraphRuntime,
    arguments: &Value,
) -> Result<Value, String> {
    let ctx = context_of(arguments, "offset", "limit", 50, 200)?;
    let module_id = payload_str_or_empty(arguments, "module_id");
    let kinds: Vec<String> = list_value(arguments.get("symbol_kinds"))
        .into_iter()
        .filter_map(|item| match item {
            Value::String(text) => Some(text),
            other => Some(other.to_string()),
        })
        .filter(|item| !item.is_empty())
        .collect();
    let language = payload_str_or_empty(arguments, "language").to_lowercase();
    let include_inferred = arguments
        .get("include_inferred")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let params: Map<String, Value> = Map::from_iter([
        ("project_id_normalized".to_string(), json!(ctx.normalized)),
        ("module_id".to_string(), json!(module_id)),
        ("symbol_kinds".to_string(), json!(kinds)),
        ("language".to_string(), json!(language)),
        ("include_inferred".to_string(), json!(include_inferred)),
        ("offset".to_string(), json!(ctx.page_offset)),
        ("limit".to_string(), json!(ctx.page_limit)),
    ]);
    let count_rows = run_query(runtime, PUBLIC_APIS_COUNT, &params, None)?;
    let rows = run_query(runtime, PUBLIC_APIS_PAGE, &params, None)?;
    let items: Vec<Value> = rows
        .iter()
        .map(|row| {
            let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
            let inferred = row_opt(row, "inferred").and_then(|v| v.as_bool()).unwrap_or(false);
            json!({
                "symbol_id": super::null_or(get("symbol_id"), get("id")),
                "name": super::null_str_or(get("name"), ""),
                "kind": if get("kind").is_null() {
                    if get("declaration_kind").is_null() { json!("Symbol") } else { get("declaration_kind") }
                } else {
                    get("kind")
                },
                "declaration_kind": super::null_str_or(get("declaration_kind"), ""),
                "signature": super::null_str_or(get("signature"), ""),
                "visibility": super::null_str_or(get("visibility"), "unknown"),
                "visibility_source": super::null_str_or(get("visibility_source"), ""),
                "evidence": super::null_str_or(get("evidence"), ""),
                "language": super::null_str_or(get("language"), ""),
                "file_path": super::null_str_or(get("file_path"), ""),
                "start_line": get("start_line"),
                "module_id": get("module_id").is_null().then(|| json!(module_id)).unwrap_or_else(|| get("module_id")),
                "inferred": inferred,
                "confidence": if get("confidence").is_null() {
                    json!(if inferred { "low" } else { "high" })
                } else {
                    get("confidence")
                },
            })
        })
        .collect();
    let total = total_of(&count_rows, items.len() as i64);
let has_more_value = ctx.page_offset + items.len() as i64;
    Ok(json!({
        "ok": true,
        "project_id": ctx.raw,
        "public_apis": items,
        "total": total,
        "offset": ctx.page_offset,
        "limit": ctx.page_limit,
        "has_more": has_more_value,
        "include_inferred": include_inferred,
    }))
}

const PUBLIC_APIS_COUNT: &str = "\n            /* project_context:public_apis:count */\n            MATCH (m:ProjectModule)-[:EXPOSES_API]->(symbol)\n            WHERE m.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR m.id = $module_id)\n            \n          AND (coalesce(symbol.is_public_api, false) = true\n               OR ($include_inferred = true AND symbol.visibility = 'inferred'))\n          AND (size($symbol_kinds) = 0 OR labels(symbol)[0] IN $symbol_kinds OR symbol.kind IN $symbol_kinds)\n          AND ($language = '' OR toLower(symbol.language) = $language)\n        \n            RETURN count(DISTINCT symbol) AS total\n            ";
const PUBLIC_APIS_PAGE: &str = "\n            /* project_context:public_apis:page */\n            MATCH (m:ProjectModule)-[:EXPOSES_API]->(symbol)\n            WHERE m.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR m.id = $module_id)\n            \n          AND (coalesce(symbol.is_public_api, false) = true\n               OR ($include_inferred = true AND symbol.visibility = 'inferred'))\n          AND (size($symbol_kinds) = 0 OR labels(symbol)[0] IN $symbol_kinds OR symbol.kind IN $symbol_kinds)\n          AND ($language = '' OR toLower(symbol.language) = $language)\n        \n            RETURN DISTINCT symbol.id AS symbol_id,\n                   symbol.name AS name,\n                   labels(symbol)[0] AS kind,\n                   symbol.kind AS declaration_kind,\n                   symbol.signature AS signature,\n                   symbol.visibility AS visibility,\n                   symbol.visibility_source AS visibility_source,\n                   symbol.export_evidence AS evidence,\n                   symbol.language AS language,\n                   symbol.file_path AS file_path,\n                   symbol.start_line AS start_line,\n                   symbol.module_id AS module_id,\n                   symbol.public_api_confidence AS confidence,\n                   coalesce(symbol.visibility = 'inferred', false) AS inferred\n            ORDER BY symbol.file_path, symbol.start_line, symbol.id\n            SKIP $offset LIMIT $limit\n            ";

fn get_endpoints_service(
    runtime: &mut runtime::GraphRuntime,
    arguments: &Value,
) -> Result<Value, String> {
    let ctx = context_of(arguments, "offset", "limit", 50, 200)?;
    let module_id = payload_str_or_empty(arguments, "module_id");
    let params: Map<String, Value> = Map::from_iter([
        ("project_id_normalized".to_string(), json!(ctx.normalized)),
        ("module_id".to_string(), json!(module_id)),
        ("protocol".to_string(), json!(payload_str_or_empty(arguments, "protocol").to_lowercase())),
        ("framework".to_string(), json!(payload_str_or_empty(arguments, "framework").to_lowercase())),
        ("http_method".to_string(), json!(payload_str_or_empty(arguments, "http_method").to_uppercase())),
        ("query".to_string(), json!(payload_str_or_empty(arguments, "query").to_lowercase())),
        ("offset".to_string(), json!(ctx.page_offset)),
        ("limit".to_string(), json!(ctx.page_limit)),
    ]);
    let count_rows = run_query(runtime, ENDPOINTS_COUNT, &params, None)?;
    let rows = run_query(runtime, ENDPOINTS_PAGE, &params, None)?;
    let mut dedup: BTreeMap<(String, String, String, String), Value> = BTreeMap::new();
    for row in &rows {
        let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
        let labels: Vec<String> = list_value(row.get("original_labels"))
            .into_iter()
            .filter_map(|item| match item {
                Value::String(text) => Some(text),
                other => Some(other.to_string()),
            })
            .filter(|item| !item.is_empty())
            .collect();
        let mut protocol_value = row_opt(row, "protocol").map(|v| value_string(&v)).unwrap_or_default().to_lowercase();
        if protocol_value.is_empty() {
            protocol_value = if labels.iter().any(|label| label == "GrpcEndpoint") {
                "grpc".to_string()
            } else {
                "http".to_string()
            };
        }
        let endpoint_id = if get("endpoint_id").is_null() { get("id") } else { get("endpoint_id") };
        let null_to_empty = |key: &str| get(key).is_null().then(|| json!("")).unwrap_or_else(|| get(key));
        let null_to_unknown = |key: &str, default: &str| {
            get(key).is_null().then(|| json!(default)).unwrap_or_else(|| get(key))
        };
        let mut sorted_labels = labels.clone();
        sorted_labels.sort();
        let item = json!({
            "endpoint_id": endpoint_id,
            "protocol": protocol_value,
            "method": row_opt(row, "method").map(|v| value_string(&v)).unwrap_or_default().to_uppercase(),
            "path": null_to_empty("path"),
            "name": null_to_empty("name"),
            "service": null_to_empty("service"),
            "framework": null_to_empty("framework"),
            "handler_id": null_to_empty("handler_id"),
            "security": Value::Object(mapping_value(row.get("security"))),
            "file_path": null_to_empty("file_path"),
            "start_line": get("start_line"),
            "module_id": null_to_unknown("module_id", module_id),
            "request_type": null_to_empty("request_type"),
            "response_type": null_to_empty("response_type"),
            "client_streaming": row_opt(row, "client_streaming").and_then(|v| v.as_bool()).unwrap_or(false),
            "server_streaming": row_opt(row, "server_streaming").and_then(|v| v.as_bool()).unwrap_or(false),
            "original_labels": sorted_labels,
            "confidence": null_to_unknown("confidence", "unknown"),
            "evidence": list_value(row.get("evidence")),
        });
        let key = (
            item.get("endpoint_id").map(value_string).unwrap_or_default(),
            protocol_value,
            item.get("method").map(value_string).unwrap_or_default(),
            {
                let path = item.get("path").map(value_string).unwrap_or_default();
                if path.is_empty() {
                    item.get("name").map(value_string).unwrap_or_default()
                } else {
                    path
                }
            },
        );
        dedup.insert(key, item);
    }
    let mut items: Vec<Value> = dedup.into_values().collect();
    items.sort_by(|left, right| {
        let key = |item: &Value| (
            item.get("protocol").map(value_string).unwrap_or_default(),
            item.get("path").map(value_string).unwrap_or_default(),
            item.get("name").map(value_string).unwrap_or_default(),
            item.get("endpoint_id").map(value_string).unwrap_or_default(),
        );
        key(left).cmp(&key(right))
    });
    let total = total_of(&count_rows, items.len() as i64);
let has_more_value = ctx.page_offset + items.len() as i64;
    Ok(json!({
        "ok": true,
        "project_id": ctx.raw,
        "endpoints": items,
        "total": total,
        "offset": ctx.page_offset,
        "limit": ctx.page_limit,
        "has_more": has_more_value,
    }))
}

const ENDPOINTS_COUNT: &str = "\n            /* project_context:endpoints:count */\n            MATCH (m:ProjectModule)-[:EXPOSES_ENDPOINT]->(endpoint)\n            WHERE m.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR m.id = $module_id)\n            \n          AND ($protocol = '' OR toLower(coalesce(endpoint.protocol, 'http')) = $protocol)\n          AND ($framework = '' OR toLower(coalesce(endpoint.framework, '')) = $framework)\n          AND ($http_method = '' OR toUpper(coalesce(endpoint.http_method, endpoint.method, '')) = $http_method)\n          AND ($query = '' OR toLower(coalesce(endpoint.path, endpoint.route, endpoint.name, '')) CONTAINS $query)\n        \n            RETURN count(DISTINCT endpoint) AS total\n            ";
const ENDPOINTS_PAGE: &str = "\n            /* project_context:endpoints:page */\n            MATCH (m:ProjectModule)-[:EXPOSES_ENDPOINT]->(endpoint)\n            WHERE m.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR m.id = $module_id)\n            \n          AND ($protocol = '' OR toLower(coalesce(endpoint.protocol, 'http')) = $protocol)\n          AND ($framework = '' OR toLower(coalesce(endpoint.framework, '')) = $framework)\n          AND ($http_method = '' OR toUpper(coalesce(endpoint.http_method, endpoint.method, '')) = $http_method)\n          AND ($query = '' OR toLower(coalesce(endpoint.path, endpoint.route, endpoint.name, '')) CONTAINS $query)\n        \n            OPTIONAL MATCH (endpoint)-[:HANDLED_BY|SEMANTIC_OF]->(handler)\n            RETURN DISTINCT endpoint.id AS endpoint_id,\n                   labels(endpoint) AS original_labels,\n                   coalesce(endpoint.protocol, 'http') AS protocol,\n                   coalesce(endpoint.http_method, endpoint.method, '') AS method,\n                   coalesce(endpoint.path, endpoint.route, '') AS path,\n                   endpoint.name AS name,\n                   endpoint.service AS service,\n                   endpoint.framework AS framework,\n                   coalesce(handler.id, endpoint.handler_id) AS handler_id,\n                   endpoint.security AS security,\n                   endpoint.file_path AS file_path,\n                   endpoint.start_line AS start_line,\n                   m.id AS module_id,\n                   endpoint.request_type AS request_type,\n                   endpoint.response_type AS response_type,\n                   endpoint.client_streaming AS client_streaming,\n                   endpoint.server_streaming AS server_streaming\n                   , endpoint.confidence AS confidence\n                   , endpoint.evidence AS evidence\n            ORDER BY protocol, path, name, endpoint_id\n            SKIP $offset LIMIT $limit\n            ";

fn get_special_files_service(
    runtime: &mut runtime::GraphRuntime,
    arguments: &Value,
) -> Result<Value, String> {
    let ctx = context_of(arguments, "offset", "limit", 50, 200)?;
    let module_id = payload_str_or_empty(arguments, "module_id");
    let params: Map<String, Value> = Map::from_iter([
        ("project_id_normalized".to_string(), json!(ctx.normalized)),
        ("module_id".to_string(), json!(module_id)),
        ("role".to_string(), json!(payload_str_or_empty(arguments, "role"))),
        ("parser".to_string(), json!(payload_str_or_empty(arguments, "parser"))),
        ("framework".to_string(), json!(payload_str_or_empty(arguments, "framework"))),
        ("parse_depth".to_string(), json!(payload_str_or_empty(arguments, "parse_depth"))),
        ("status".to_string(), json!(payload_str_or_empty(arguments, "status"))),
        (
            "include_generated".to_string(),
            json!(arguments.get("include_generated").and_then(Value::as_bool).unwrap_or(true)),
        ),
        ("offset".to_string(), json!(ctx.page_offset)),
        ("limit".to_string(), json!(ctx.page_limit)),
    ]);
    let count_rows = run_query(runtime, SPECIAL_FILES_COUNT, &params, None)?;
    let rows = run_query(runtime, SPECIAL_FILES_PAGE, &params, None)?;
    let items: Vec<Value> = rows
        .iter()
        .map(|row| {
            let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
            let secret_bearing = row_opt(row, "secret_bearing").and_then(|v| v.as_bool()).unwrap_or(false);
            let parse_depth = row_opt(row, "parse_depth").map(|v| value_string(&v));
            let mut sorted_frameworks: Vec<String> = list_value(row.get("frameworks"))
                .into_iter()
                .filter_map(|item| match item {
                    Value::String(text) => Some(text),
                    other => Some(other.to_string()),
                })
                .filter(|item| !item.is_empty())
                .collect();
            sorted_frameworks.sort();
            json!({
                "descriptor_id": if get("descriptor_id").is_null() { get("id") } else { get("descriptor_id") },
                "path": super::null_str_or(get("path"), ""),
                "role": super::null_str_or(get("role"), "configuration"),
                "parser": super::null_str_or(get("parser"), ""),
                "parse_depth": super::null_str_or(get("parse_depth"), "unknown"),
                "status": super::null_str_or(get("status"), "present"),
                "framework": super::null_str_or(get("framework"), ""),
                "frameworks": sorted_frameworks,
                "canonical": row_opt(row, "canonical").and_then(|v| v.as_bool()).unwrap_or(true),
                "generated": row_opt(row, "generated").and_then(|v| v.as_bool()).unwrap_or(false),
                "secret_bearing": secret_bearing,
                "redacted": row_opt(row, "redacted").and_then(|v| v.as_bool()).unwrap_or(false),
                "safe_summary": if secret_bearing {
                    json!("[redacted]")
                } else if get("safe_summary").is_null() {
                    json!("")
                } else {
                    get("safe_summary")
                },
                "freshness": super::null_str_or(get("freshness"), "unknown"),
                "diagnostics": list_value(row.get("diagnostics")),
                "module_id": get("module_id").is_null().then(|| json!(module_id)).unwrap_or_else(|| get("module_id")),
                "coverage_status": if parse_depth.as_deref().unwrap_or("unknown") == "unknown" {
                    json!("unknown")
                } else {
                    json!("supported")
                },
                "source_provenance": json!("indexed_graph"),
            })
        })
        .collect();
    let total = total_of(&count_rows, items.len() as i64);
let has_more_value = ctx.page_offset + items.len() as i64;
    Ok(json!({
        "ok": true,
        "project_id": ctx.raw,
        "special_files": items,
        "total": total,
        "offset": ctx.page_offset,
        "limit": ctx.page_limit,
        "has_more": has_more_value,
    }))
}

const SPECIAL_FILES_COUNT: &str = "\n            /* project_context:special_files:count */\n            MATCH (module:ProjectModule)-[:HAS_DESCRIPTOR]->(descriptor:BuildDescriptor)\n            WHERE module.project_id_normalized STARTS WITH $project_id_normalized\n            \n          AND ($module_id = '' OR module.id = $module_id)\n          AND ($role = '' OR descriptor.role = $role)\n          AND ($parser = '' OR descriptor.parser = $parser)\n          AND (\n            $framework = ''\n            OR descriptor.framework = $framework\n            OR $framework IN coalesce(descriptor.frameworks, [])\n          )\n          AND ($parse_depth = '' OR descriptor.parse_depth = $parse_depth)\n          AND ($status = '' OR coalesce(descriptor.status, 'present') = $status)\n          AND ($include_generated = true OR coalesce(descriptor.generated, false) = false)\n        \n            RETURN count(DISTINCT descriptor) AS total\n            ";
const SPECIAL_FILES_PAGE: &str = "\n            /* project_context:special_files:page */\n            MATCH (module:ProjectModule)-[:HAS_DESCRIPTOR]->(descriptor:BuildDescriptor)\n            WHERE module.project_id_normalized STARTS WITH $project_id_normalized\n            \n          AND ($module_id = '' OR module.id = $module_id)\n          AND ($role = '' OR descriptor.role = $role)\n          AND ($parser = '' OR descriptor.parser = $parser)\n          AND (\n            $framework = ''\n            OR descriptor.framework = $framework\n            OR $framework IN coalesce(descriptor.frameworks, [])\n          )\n          AND ($parse_depth = '' OR descriptor.parse_depth = $parse_depth)\n          AND ($status = '' OR coalesce(descriptor.status, 'present') = $status)\n          AND ($include_generated = true OR coalesce(descriptor.generated, false) = false)\n        \n            RETURN descriptor.id AS descriptor_id,\n                   descriptor.file_path AS path,\n                   descriptor.role AS role,\n                   descriptor.parser AS parser,\n                   descriptor.parse_depth AS parse_depth,\n                   coalesce(descriptor.status, 'present') AS status,\n                   descriptor.framework AS framework,\n                   descriptor.frameworks AS frameworks,\n                   coalesce(descriptor.canonical, true) AS canonical,\n                   coalesce(descriptor.generated, false) AS generated,\n                   coalesce(descriptor.secret_bearing, false) AS secret_bearing,\n                   coalesce(descriptor.redacted, false) AS redacted,\n                   descriptor.summary AS safe_summary,\n                   coalesce(descriptor.freshness, 'current') AS freshness,\n                   descriptor.diagnostics AS diagnostics,\n                   module.id AS module_id\n            ORDER BY path, descriptor_id\n            SKIP $offset LIMIT $limit\n            ";

fn get_framework_context_service(
    runtime: &mut runtime::GraphRuntime,
    arguments: &Value,
) -> Result<Value, String> {
    let ctx = context_of(arguments, "offset", "limit", 50, 200)?;
    let module_id = payload_str_or_empty(arguments, "module_id");
    let selected_dimensions: Vec<String> = {
        let mut dims: Vec<String> = list_value(arguments.get("dimensions"))
            .into_iter()
            .filter_map(|item| match item {
                Value::String(text) => Some(text),
                other => Some(other.to_string()),
            })
            .filter(|item| !item.is_empty())
            .collect();
        dims.sort();
        dims.dedup();
        dims
    };
    let params: Map<String, Value> = Map::from_iter([
        ("project_id_normalized".to_string(), json!(ctx.normalized)),
        ("module_id".to_string(), json!(module_id)),
        ("framework".to_string(), json!(payload_str_or_empty(arguments, "framework").to_lowercase())),
        ("offset".to_string(), json!(ctx.page_offset)),
        ("limit".to_string(), json!(ctx.page_limit)),
    ]);
    let count_rows = run_query(runtime, FRAMEWORKS_COUNT, &params, None)?;
    let rows = run_query(runtime, FRAMEWORKS_PAGE, &params, None)?;
    let items: Vec<Value> = rows
        .iter()
        .map(|row| {
            let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
            let dimensions_raw = mapping_value(row.get("dimensions"));
            let dimension_map: Map<String, Value> = if !selected_dimensions.is_empty() {
                selected_dimensions
                    .iter()
                    .map(|key| {
                        (
                            key.clone(),
                            dimensions_raw.get(key).cloned().unwrap_or(json!("unavailable")),
                        )
                    })
                    .collect()
            } else {
                dimensions_raw
            };
            let coverage_partial = dimension_map.values().any(|value| {
                matches!(
                    value.as_str(),
                    Some("partial") | Some("unknown") | Some("unavailable")
                )
            });
            json!({
                "instance_id": if get("instance_id").is_null() { get("id") } else { get("instance_id") },
                "framework": super::null_str_or(get("framework"), ""),
                "version": super::null_str_or(get("version"), ""),
                "confidence": super::null_str_or(get("confidence"), "unknown"),
                "module_id": get("module_id").is_null().then(|| json!(module_id)).unwrap_or_else(|| get("module_id")),
                "dimensions": Value::Object(dimension_map.clone()),
                "facts": Value::Object(mapping_value(row.get("facts"))),
                "evidence": list_value(row.get("evidence")),
                "diagnostics": list_value(row.get("diagnostics")),
                "coverage_status": if coverage_partial { json!("partial") } else { json!("supported") },
                "source_provenance": json!("indexed_graph"),
            })
        })
        .collect();
    let total = total_of(&count_rows, items.len() as i64);
let has_more_value = ctx.page_offset + items.len() as i64;
    Ok(json!({
        "ok": true,
        "project_id": ctx.raw,
        "frameworks": items,
        "total": total,
        "offset": ctx.page_offset,
        "limit": ctx.page_limit,
        "has_more": has_more_value,
    }))
}

const FRAMEWORKS_COUNT: &str = "\n            /* project_context:frameworks:count */\n            MATCH (module:ProjectModule)-[:USES_FRAMEWORK]->(instance:FrameworkInstance)\n            WHERE module.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR module.id = $module_id)\n              AND ($framework = '' OR toLower(instance.framework) = $framework)\n            RETURN count(DISTINCT instance) AS total\n            ";
const FRAMEWORKS_PAGE: &str = "\n            /* project_context:frameworks:page */\n            MATCH (module:ProjectModule)-[:USES_FRAMEWORK]->(instance:FrameworkInstance)\n            WHERE module.project_id_normalized STARTS WITH $project_id_normalized\n              AND ($module_id = '' OR module.id = $module_id)\n              AND ($framework = '' OR toLower(instance.framework) = $framework)\n            RETURN instance.id AS instance_id,\n                   instance.framework AS framework,\n                   instance.version AS version,\n                   instance.confidence AS confidence,\n                   instance.dimensions AS dimensions,\n                   instance.facts AS facts,\n                   instance.evidence AS evidence,\n                   instance.diagnostics AS diagnostics,\n                   module.id AS module_id\n            ORDER BY framework, module_id, instance_id\n            SKIP $offset LIMIT $limit\n            ";

fn get_architecture_summary_service(
    runtime: &mut runtime::GraphRuntime,
    arguments: &Value,
) -> Result<Value, String> {
    let project_id = payload_str_or_empty(arguments, "project_id");
    let module_id = payload_str_or_empty(arguments, "module_id");
    let all_modules_flag = arguments
        .get("all_modules")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let detail_level = arguments.get("detail_level")
        .and_then(Value::as_str)
        .map(str::to_string)
        // Python signature default (`detail_level: str = "standard"`) — chỉ
        // áp dụng khi caller KHÔNG gửi key; giá trị rỗng đi qua nguyên vẹn.
        .unwrap_or_else(|| "standard".to_string());
    let all_modules = all_modules_flag || module_id.trim().is_empty();
    let sample_limit = positive_int(arguments.get("item_limit"), 10, 50)?;
    let mut modules_args = arguments.clone();
    if let Some(object) = modules_args.as_object_mut() {
        object.insert("limit".to_string(), json!(200));
        object.insert("offset".to_string(), json!(0));
    }
    let modules = get_project_modules_service(runtime, &modules_args)?;
    let mut sample_args = arguments.clone();
    if let Some(object) = sample_args.as_object_mut() {
        object.insert("limit".to_string(), json!(sample_limit));
        object.insert("offset".to_string(), json!(0));
    }
    let public_apis = get_public_apis_service(runtime, &sample_args)?;
    let endpoints = get_endpoints_service(runtime, &sample_args)?;
    let special_files = get_special_files_service(runtime, &sample_args)?;
    let frameworks = get_framework_context_service(runtime, &sample_args)?;
    Ok(json!({
        "ok": true,
        "project_id": project_id,
        "module_id": module_id,
        "all_modules": all_modules,
        "detail_level": detail_level,
        "item_limit": sample_limit,
        "summary": {
            "modules": modules.get("modules").cloned().unwrap_or(json!([])),
            "module_total": modules.get("total").cloned().unwrap_or(json!(0)),
            "public_api_count": public_apis.get("total").cloned().unwrap_or(json!(0)),
            "public_api_sample": public_apis.get("public_apis").cloned().unwrap_or(json!([])),
            "endpoint_count": endpoints.get("total").cloned().unwrap_or(json!(0)),
            "endpoint_sample": endpoints.get("endpoints").cloned().unwrap_or(json!([])),
            "special_file_count": special_files.get("total").cloned().unwrap_or(json!(0)),
            "special_file_sample": special_files.get("special_files").cloned().unwrap_or(json!([])),
            "framework_count": frameworks.get("total").cloned().unwrap_or(json!(0)),
            "framework_sample": frameworks.get("frameworks").cloned().unwrap_or(json!([])),
        },
        "ingestion_provenance": {
            "source": "indexed_graph",
            "filesystem_rescan": false,
        },
    }))
}

/// Unified `_run_project_context_tool` (project-scoped path + fanout merge).
pub fn dispatch_project_context_tool(
    runtime: &mut runtime::GraphRuntime,
    tool_name: &str,
    arguments: Value,
) -> Result<Value, String> {
    let project_id = payload_str_or_empty(&arguments, "project_id");
    if project_id.trim().is_empty() {
        // Fanout per registered project (unified search contract).
        let projects = crate::project_registry::list_registered_projects(None)
            .map_err(|error| error.to_string())?;
        if projects.is_empty() {
            return Err(
                "project_id is omitted and no projects are registered, so there is nothing to search. \
                 Register a project or pass project_id explicitly."
                    .to_string(),
            );
        }
        let mut per_project: BTreeMap<String, Value> = BTreeMap::new();
        for project in &projects {
            let mut project_arguments = arguments.clone();
            if let Some(object) = project_arguments.as_object_mut() {
                object.insert("project_id".to_string(), json!(project));
            }
            let result = run_single_project_context(runtime, tool_name, &project_arguments)
                .unwrap_or_else(|error| {
                    json!({
                        "ok": false,
                        "error": {"type": "tool_execution_error", "message": error},
                    })
                });
            per_project.insert(project.clone(), result);
        }
        return Ok(merge_project_context_results(&per_project));
    }
    run_single_project_context(runtime, tool_name, &arguments)
}

fn run_single_project_context(
    runtime: &mut runtime::GraphRuntime,
    tool_name: &str,
    arguments: &Value,
) -> Result<Value, String> {
    let project_id = payload_str_or_empty(arguments, "project_id");
    let parser_type = payload_str_or_empty(arguments, "parser_type");
    let database = super::tools_search::resolve_graph_database(if project_id.is_empty() {
        None
    } else {
        Some(project_id)
    });
    let (required_labels, required_relationships): (Vec<&str>, Vec<&str>) = match tool_name {
        "get_project_modules" => (
            vec!["ProjectModule", "BuildDescriptor"],
            vec!["HAS_DESCRIPTOR"],
        ),
        "get_public_apis" => (vec!["ProjectModule"], vec!["EXPOSES_API"]),
        "get_endpoints" => (vec!["ProjectModule"], vec!["EXPOSES_ENDPOINT"]),
        "get_module_architecture_summary" => (vec!["ProjectModule"], vec![]),
        "get_project_special_files" => (
            vec!["ProjectModule", "BuildDescriptor"],
            vec!["HAS_DESCRIPTOR"],
        ),
        "get_framework_context" => (
            vec!["ProjectModule", "FrameworkInstance"],
            vec!["USES_FRAMEWORK"],
        ),
        other => return Err(format!("Unknown project-context tool: {other}")),
    };
    let context = resolve_direct_capability_context(
        runtime,
        tool_name,
        Some(parser_type),
        database.as_deref(),
        &required_relationships,
        &required_labels,
    )?;
    if let Some(error) = context.error {
        return Ok(error);
    }
    let mut result = match tool_name {
        "get_project_modules" => get_project_modules_service(runtime, arguments)?,
        "get_public_apis" => get_public_apis_service(runtime, arguments)?,
        "get_endpoints" => get_endpoints_service(runtime, arguments)?,
        "get_module_architecture_summary" => get_architecture_summary_service(runtime, arguments)?,
        "get_project_special_files" => get_special_files_service(runtime, arguments)?,
        "get_framework_context" => get_framework_context_service(runtime, arguments)?,
        other => return Err(format!("Unknown project-context tool: {other}")),
    };
    if let Some(object) = result.as_object_mut() {
        object.insert("capability".to_string(), context.routing);
        if let Some(diagnostics) = context.diagnostics {
            object.insert("capability_diagnostics".to_string(), diagnostics);
        }
    }
    Ok(result)
}

/// `_merge_project_context_results`.
fn merge_project_context_results(results: &BTreeMap<String, Value>) -> Value {
    let ok_projects: Vec<String> = results
        .iter()
        .filter(|(_, result)| !matches!(result.get("ok"), Some(Value::Bool(false))))
        .map(|(project, _)| project.clone())
        .collect();
    let failed_projects: BTreeMap<String, Value> = results
        .iter()
        .filter(|(_, result)| matches!(result.get("ok"), Some(Value::Bool(false))))
        .map(|(project, result)| (project.clone(), result.get("error").cloned().unwrap_or(Value::Null)))
        .collect();
    if ok_projects.is_empty() {
        let first_error = failed_projects.values().next().cloned().unwrap_or(Value::Null);
        return json!({
            "ok": false,
            "error": if first_error.is_null() {
                json!({"type": "project_context_unavailable", "message": "No registered projects produced a result."})
            } else {
                first_error
            },
        });
    }
    let mut merged = Map::new();
    merged.insert("ok".to_string(), json!(true));
    merged.insert("projects_searched".to_string(), json!(ok_projects));
    if !failed_projects.is_empty() {
        merged.insert(
            "projects_failed".to_string(),
            Value::Object(failed_projects.into_iter().collect()),
        );
    }
    let first_result = results
        .get(&ok_projects[0])
        .cloned()
        .unwrap_or(Value::Null);
    if let Some(object) = first_result.as_object() {
        for (key, value) in object {
            if matches!(key.as_str(), "ok" | "project_id" | "error") {
                continue;
            }
            if key == "summary" && value.is_object() {
                let summaries: Vec<Value> = ok_projects
                    .iter()
                    .map(|project| {
                        results
                            .get(project)
                            .and_then(|result| result.get("summary"))
                            .cloned()
                            .unwrap_or(json!({}))
                    })
                    .collect();
                merged.insert(
                    key.clone(),
                    merge_project_context_summary(&summaries, &ok_projects),
                );
                continue;
            }
            if PROJECT_CONTEXT_LIST_KEYS.contains(&key.as_str()) && value.is_array() {
                let mut items: Vec<Value> = Vec::new();
                let mut seen_ids: std::collections::BTreeSet<String> = Default::default();
                for project in &ok_projects {
                    let project_items = results
                        .get(project)
                        .and_then(|result| result.get(key))
                        .and_then(Value::as_array)
                        .cloned();
                    let Some(project_items) = project_items else { continue };
                    for item in project_items {
                        if let Some(item_object) = item.as_object() {
                            let item_id = item_object
                                .get("id")
                                .or_else(|| item_object.get("module_id"))
                                .map(value_string)
                                .filter(|id| !id.is_empty());
                            if let Some(item_id) = &item_id {
                                if seen_ids.contains(item_id) {
                                    continue;
                                }
                                seen_ids.insert(item_id.clone());
                            }
                            let mut merged_item = item_object.clone();
                            merged_item
                                .entry("project_id".to_string())
                                .or_insert(json!(project));
                            items.push(Value::Object(merged_item));
                        } else {
                            items.push(item);
                        }
                    }
                }
                merged.insert(key.clone(), Value::Array(items));
                continue;
            }
            if key == "total" && value.is_number() {
                let total: i64 = ok_projects
                    .iter()
                    .filter_map(|project| {
                        results
                            .get(project)
                            .and_then(|result| result.get("total"))
                            .and_then(Value::as_i64)
                    })
                    .sum();
                merged.insert(key.clone(), json!(total));
                continue;
            }
            if key == "has_more" && value.is_boolean() {
                let any_value = ok_projects.iter().any(|project| {
                    results
                        .get(project)
                        .and_then(|result| result.get("has_more"))
                        .and_then(Value::as_bool)
                        .unwrap_or(false)
                });
                merged.insert(key.clone(), json!(any_value));
                continue;
            }
            merged.insert(key.clone(), value.clone());
        }
    }
    Value::Object(merged)
}

const PROJECT_CONTEXT_LIST_KEYS: [&str; 5] = [
    "modules",
    "public_apis",
    "endpoints",
    "special_files",
    "frameworks",
];

fn merge_project_context_summary(summaries: &[Value], projects: &[String]) -> Value {
    let first = summaries
        .first()
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let mut merged = Map::new();
    for (key, value) in &first {
        let values: Vec<Value> = summaries
            .iter()
            .map(|summary| summary.get(key).cloned().unwrap_or(Value::Null))
            .collect();
        if value.is_array() {
            let mut items: Vec<Value> = Vec::new();
            for (project, project_items) in projects.iter().zip(&values) {
                let Some(list) = project_items.as_array() else { continue };
                for item in list {
                    if let Some(item_object) = item.as_object() {
                        let mut tagged = item_object.clone();
                        tagged.entry("project_id".to_string()).or_insert(json!(project));
                        items.push(Value::Object(tagged));
                    } else {
                        items.push(item.clone());
                    }
                }
            }
            merged.insert(key.clone(), Value::Array(items));
        } else if value.is_number()
            && (key.ends_with("_count") || key.ends_with("_total"))
        {
            let sum: i64 = values
                .iter()
                .filter_map(Value::as_i64)
                .sum();
            merged.insert(key.clone(), json!(sum));
        } else {
            merged.insert(key.clone(), value.clone());
        }
    }
    Value::Object(merged)
}

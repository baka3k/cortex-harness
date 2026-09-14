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
//! Search/introspection tools — port của `cplus_mcp.py`/`android_mcp.py`
//! `tool_search_functions`, `tool_search_by_code`, `tool_get_symbol`,
//! `tool_get_node_details`, `tool_list_possible_calls`, `tool_listup_*`,
//! `tool_list_up_entrypoint`, `tool_annotate_node`, `tool_get_ipc_message`,
//! `tool_list_databases` + unified `tool_inspect_parser_capabilities`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use super::{
    backend_string_list, is_database_not_found_error, normalize_content_mode, payload_bool,
    payload_str, payload_string, profile_search_labels, profile_text_properties, record_node,
    record_query_node, record_rel, resolve_db_candidates, run_cypher_first,
    Backend, LEGACY_SEARCH_FRAMEWORKS,
};
use crate::framework_registry::{
    capability_for_parser, capability_catalog, evaluate_capability_schema,
    query_engine_for_backend,
};
use crate::project_registry::resolve_project_targets;
use super::runtime;

pub const FULLTEXT_SYMBOL_TEXT_INDEX: &str = "mcp_symbol_text_ft_v2";
pub const FULLTEXT_SYMBOL_CODE_INDEX: &str = "mcp_symbol_code_ft_v2";

// ---------------------------------------------------------------------------
// search_functions
// ---------------------------------------------------------------------------

/// `tool_search_functions` (cplus + android bodies).
pub fn tool_search_functions(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let query = payload.get("query").cloned().unwrap_or(Value::Null);
    let mut limit = match payload.get("limit") {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(50),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(50),
        _ => match payload.get("top_k") {
            Some(Value::Number(number)) => number.as_i64().unwrap_or(50),
            Some(Value::String(text)) => text.trim().parse().unwrap_or(50),
            _ => 50,
        },
    };
    let _ = &mut limit;
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let query_text = match &query {
        Value::String(text) if !text.trim().is_empty() => text.clone(),
        _ => return Err("query is required.".to_string()),
    };
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let first_db = db_candidates
        .first()
        .cloned()
        .ok_or_else(|| "db is required (set via activate_project or provide explicitly).".to_string())?;
    let _ = first_db;
    let qs: Vec<String> = query_text
        .split('|')
        .map(|token| token.trim().to_lowercase())
        .filter(|token| !token.is_empty())
        .collect();
    let fanout = payload_bool(payload, "_fanout");

    let (used_db, rows) = match backend {
        Backend::Cplus => search_functions_cplus(runtime, backend, payload, &qs, &db_candidates, fanout)?,
        Backend::Android => search_functions_android(
            runtime,
            &qs,
            &db_candidates,
            fanout,
            project_id.as_deref(),
            payload
                .get("limit")
                .and_then(Value::as_i64)
                .or_else(|| payload.get("top_k").and_then(Value::as_i64))
                .unwrap_or(50),
        )?,
    };

    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let nodes: Vec<Value> = rows
        .iter()
        .map(|row| record_query_node(row, "n", &mode, include_raw_fields, prunes_note, backend))
        .collect();
    let ids: Vec<Value> = nodes
        .iter()
        .filter_map(|node| node.get("id").cloned())
        .filter(|id| !id.is_null())
        .collect();
    Ok(json!({"db": used_db, "results": nodes, "ids": ids}))
}

fn search_functions_cplus(
    runtime: &mut runtime::GraphRuntime,
    _backend: Backend,
    payload: &Value,
    qs: &[String],
    db_candidates: &[String],
    fanout: bool,
) -> Result<(Option<String>, Vec<Map<String, Value>>), String> {
    let project_id = payload_string(payload, "project_id");
    let framework = payload_str(payload, "framework")
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(|text| text.to_lowercase());
    let kinds = backend_string_list(payload.get("kinds"));
    let parser_capability = capability_for_parser(payload_str(payload, "parser_type"));
    if let Some(framework_name) = &framework {
        let framework_capability = capability_for_parser(Some(framework_name));
        let supports = framework_capability
            .map(|capability| {
                capability
                    .features
                    .iter()
                    .any(|feature| feature == "framework_query")
            })
            .unwrap_or(false);
        if !supports {
            return Err(format!(
                "framework '{framework_name}' is not a registered framework capability"
            ));
        }
    }
    let capability = if framework.is_some() {
        capability_for_parser(framework.as_deref())
    } else {
        parser_capability
    };
    let profile_labels: Vec<String> = profile_search_labels(capability.map(|item| item.name.as_str()))
        .into_iter()
        .collect();
    let profile_properties: Vec<String> = if fanout && profile_labels.is_empty() {
        crate::framework_registry::backend_text_property_union(Some("cplus"))
    } else {
        profile_text_properties(capability.map(|item| item.name.as_str()))
    };
    let label_predicate = Backend::Cplus.search_label_predicate("n", fanout);
    let property_names: Vec<String> = if profile_properties.is_empty() {
        vec![
            "name", "qualified_name", "file_path", "path", "raw_value", "resolved_value",
            "caption", "text", "summary",
        ]
        .into_iter()
        .map(str::to_string)
        .collect()
    } else {
        profile_properties
    };
    let property_predicate = property_names
        .iter()
        .map(|name| {
            format!("toLower(coalesce(n.{name}, '')) CONTAINS q")
        })
        .collect::<Vec<_>>()
        .join(" OR ");
    let fallback_cypher = format!(
        "MATCH (n) WHERE {label_predicate} \
         AND any(q IN $qs WHERE {property_predicate}) \
         AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized) \
         AND ($framework IS NULL OR n.framework = $framework OR n.framework IS NULL) \
         AND ($kinds IS NULL OR size($kinds) = 0 OR n.kind IN $kinds) \
         AND {servlet} \
         RETURN n, labels(n) AS labels LIMIT $limit",
        label_predicate = label_predicate,
        property_predicate = property_predicate,
        servlet = super::servlet_predicate("n"),
    );
    let fulltext_query = qs.join(" OR ");
    let node_label_predicate = Backend::Cplus.search_label_predicate("node", fanout);
    let fulltext_cypher = format!(
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score \
         WHERE {node_label_predicate} \
         AND ($project_id IS NULL OR node.project_id_normalized STARTS WITH $project_id_normalized) \
         AND ($framework IS NULL OR node.framework = $framework) \
         AND ($kinds IS NULL OR size($kinds) = 0 OR node.kind IN $kinds) \
         AND {servlet} \
         RETURN node AS n, labels(node) AS labels ORDER BY score DESC LIMIT $limit",
        node_label_predicate = node_label_predicate,
        servlet = super::servlet_predicate("node"),
    );

    let kinds_param: Value = if kinds.is_empty() {
        Value::Null
    } else {
        json!(kinds)
    };
    let framework_param: Value = match &framework {
        Some(name) => json!(name),
        None => Value::Null,
    };

    let limit = payload
        .get("limit")
        .and_then(Value::as_i64)
        .or_else(|| payload.get("top_k").and_then(Value::as_i64))
        .unwrap_or(50);
    let fulltext_result = run_cypher_first(
        runtime,
        &fulltext_cypher,
        &map_from([
            ("index_name", json!(FULLTEXT_SYMBOL_TEXT_INDEX)),
            ("query", json!(fulltext_query)),
            ("limit", json!(limit)),
            ("project_id", project_param(&project_id)),
            ("framework", framework_param.clone()),
            ("kinds", kinds_param.clone()),
        ]),
        db_candidates,
    );
    match fulltext_result {
        Ok((used_db, results)) => {
            if framework.is_some() || results.is_empty() {
                let (fallback_db, fallback_results) = run_cypher_first(
                    runtime,
                    &fallback_cypher,
                    &map_from([
                        ("qs", json!(qs)),
                        ("limit", json!(limit)),
                        ("project_id", project_param(&project_id)),
                        ("framework", framework_param),
                        ("kinds", kinds_param),
                    ]),
                    db_candidates,
                )?;
                let used_db = used_db.or(fallback_db);
                let mut by_id: Vec<Value> = Vec::new();
                let mut seen: BTreeSet<String> = BTreeSet::new();
                for (index, row) in results.iter().enumerate() {
                    let recorded = record_query_node(row, "n", "auto", false, true, Backend::Cplus);
                    let key = recorded
                        .get("id")
                        .map(super::canonical_marker)
                        .unwrap_or_else(|| json!(index).to_string());
                    if seen.insert(key) {
                        by_id.push(Value::Object(row.clone()));
                    }
                }
                let start = by_id.len();
                for (offset, row) in fallback_results.iter().enumerate() {
                    let recorded = record_query_node(row, "n", "auto", false, true, Backend::Cplus);
                    let key = recorded
                        .get("id")
                        .map(super::canonical_marker)
                        .unwrap_or_else(|| json!(start + offset).to_string());
                    if seen.insert(key) {
                        by_id.push(Value::Object(row.clone()));
                    }
                }
                let limit = payload
                    .get("limit")
                    .and_then(Value::as_i64)
                    .unwrap_or(50)
                    .max(0) as usize;
                by_id.truncate(limit);
                let rows: Vec<Map<String, Value>> = by_id
                    .into_iter()
                    .filter_map(|value| value.as_object().cloned())
                    .collect();
                return Ok((used_db, rows));
            }
            Ok((used_db, results))
        }
        Err(_) => run_cypher_first(
            runtime,
            &fallback_cypher,
            &map_from([
                ("qs", json!(qs)),
                ("limit", json!(limit)),
                ("project_id", project_param(&project_id)),
                ("framework", framework_param),
                ("kinds", kinds_param),
            ]),
            db_candidates,
        ),
    }
}

fn search_functions_android(
    runtime: &mut runtime::GraphRuntime,
    qs: &[String],
    db_candidates: &[String],
    fanout: bool,
    project_id: Option<&str>,
    limit: i64,
) -> Result<(Option<String>, Vec<Map<String, Value>>), String> {
    let label_predicate = Backend::Android.search_label_predicate("n", fanout);
    let search_predicate = ANDROID_SEARCH_PREDICATE;
    let fallback_cypher = format!(
        "MATCH (n) WHERE {label_predicate} AND ({search_predicate}) RETURN n LIMIT $limit"
    );
    let node_labels_predicate = label_predicate.replace("n:", "node:");
    let fulltext_query = qs.join(" OR ");
    let fulltext_cypher = format!(
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score \
         WHERE {node_labels_predicate} \
         RETURN node AS n ORDER BY score DESC LIMIT $limit",
    );
    match run_cypher_first(
        runtime,
        &fulltext_cypher,
        &map_from([
            ("index_name", json!(FULLTEXT_SYMBOL_TEXT_INDEX)),
            ("query", json!(fulltext_query)),
            ("limit", json!(limit)),
        ]),
        db_candidates,
    ) {
        Ok((used_db, results)) if !results.is_empty() => Ok((used_db, results)),
        Ok((_, _)) | Err(_) => run_cypher_first(
            runtime,
            &fallback_cypher,
            &map_from([("qs", json!(qs)), ("limit", json!(limit))]),
            db_candidates,
        )
        .map(|(db, rows)| {
            let _ = project_id;
            (db, rows)
        }),
    }
}

pub const ANDROID_SEARCH_PREDICATE: &str = "any(q IN $qs WHERE \
toLower(coalesce(n.name, '')) CONTAINS q OR \
toLower(coalesce(n.qualified_name, '')) CONTAINS q OR \
toLower(coalesce(n.package_name, '')) CONTAINS q OR \
toLower(coalesce(n.class_name, '')) CONTAINS q OR \
toLower(coalesce(n.module_path, '')) CONTAINS q OR \
toLower(coalesce(n.namespace, '')) CONTAINS q OR \
toLower(coalesce(n.application_id, '')) CONTAINS q OR \
toLower(coalesce(n.coordinate, '')) CONTAINS q OR \
toLower(coalesce(n.group, '')) CONTAINS q OR \
toLower(coalesce(n.artifact, '')) CONTAINS q OR \
toLower(coalesce(n.version, '')) CONTAINS q OR \
toLower(coalesce(n.res_type, '')) CONTAINS q OR \
toLower(coalesce(n.component_type, '')) CONTAINS q OR \
toLower(coalesce(n.route, '')) CONTAINS q OR \
toLower(coalesce(n.action, '')) CONTAINS q OR \
toLower(coalesce(n.token, '')) CONTAINS q OR \
toLower(coalesce(n.file_path, '')) CONTAINS q OR \
toLower(coalesce(n.path, '')) CONTAINS q)";

fn project_param(project_id: &Option<String>) -> Value {
    match project_id {
        Some(id) if !id.trim().is_empty() => json!(id),
        _ => Value::Null,
    }
}

fn map_from<'a>(items: impl IntoIterator<Item = (&'a str, Value)>) -> Map<String, Value> {
    items
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect()
}

// ---------------------------------------------------------------------------
// search_by_code
// ---------------------------------------------------------------------------

pub fn tool_search_by_code(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let query = match payload.get("query") {
        Some(Value::String(text)) if !text.trim().is_empty() => text.clone(),
        _ => return Err("query is required.".to_string()),
    };
    let limit = payload
        .get("limit")
        .and_then(Value::as_i64)
        .or_else(|| payload.get("top_k").and_then(Value::as_i64))
        .unwrap_or(50);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let qs: Vec<String> = query
        .split('|')
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(str::to_string)
        .collect();

    let fallback_cypher = match backend {
        Backend::Cplus => "MATCH (n) WHERE any(q IN $qs WHERE n.code CONTAINS q) AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized) RETURN n, labels(n) AS labels LIMIT $limit".to_string(),
        Backend::Android => "MATCH (n) WHERE any(q IN $qs WHERE n.code CONTAINS q) AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized) RETURN n LIMIT $limit".to_string(),
    };
    let fulltext_query = qs.join(" OR ");
    let fulltext_cypher = format!(
        "CALL db.index.fulltext.queryNodes($index_name, $query) YIELD node, score \
         WHERE ($project_id IS NULL OR node.project_id_normalized STARTS WITH $project_id_normalized) \
         {} LIMIT $limit",
        match backend {
            Backend::Cplus => "RETURN node AS n, labels(node) AS labels ORDER BY score DESC",
            Backend::Android => "RETURN node AS n ORDER BY score DESC",
        }
    );
    let fulltext_params = map_from([
        ("index_name", json!(FULLTEXT_SYMBOL_CODE_INDEX)),
        ("query", json!(fulltext_query)),
        ("limit", json!(limit)),
        ("project_id", project_param(&project_id)),
    ]);
    let fallback_params = map_from([
        ("qs", json!(qs)),
        ("limit", json!(limit)),
        ("project_id", project_param(&project_id)),
    ]);
    // Python control flow: fulltext trước; empty result HOẶC exception đều
    // rơi xuống fallback (cùng params shape, gồm `limit`).
    let (used_db, results) = match run_cypher_first(
        runtime,
        &fulltext_cypher,
        &fulltext_params,
        &db_candidates,
    ) {
        Ok((db, rows)) if !rows.is_empty() => (db, rows),
        Ok((_, _)) | Err(_) => {
            let (fallback_db, fallback_results) = run_cypher_first(
                runtime,
                &fallback_cypher,
                &fallback_params,
                &db_candidates,
            )?;
            (fallback_db, fallback_results)
        }
    };
    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let nodes: Vec<Value> = results
        .iter()
        .map(|row| record_query_node(row, "n", &mode, include_raw_fields, prunes_note, backend))
        .collect();
    Ok(json!({"db": used_db, "results": nodes}))
}

// ---------------------------------------------------------------------------
// get_symbol / get_node_details
// ---------------------------------------------------------------------------

pub fn tool_get_symbol(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let node_id = match payload.get("node_id") {
        Some(Value::Null) | None => return Err("node_id is required.".to_string()),
        Some(value) => value.to_string().trim_matches('"').to_string(),
    };
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let candidates = resolve_db_candidates(project_id.as_deref())?;
    let project_value = project_param(&project_id);
    for candidate in &candidates {
        let cypher = "\n        MATCH (n)\n        WHERE n.id = $id\n          AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)\n        RETURN n\n        LIMIT 1\n        ";
        let result = run_cypher_first(
            runtime,
            cypher,
            &map_from([("id", json!(node_id)), ("project_id", project_value.clone())]),
            std::slice::from_ref(candidate),
        );
        match result {
            Ok((_, rows)) => {
                if let Some(row) = rows.first()
                    && let Some(node) = row.get("n")
                {
                    // servlet_jsp active-generation guard (find_node_by_id).
                    if node.get("framework").and_then(Value::as_str) == Some("servlet_jsp") {
                        let active = check_servlet_active(runtime, candidate, node)?;
                        if !active {
                            continue;
                        }
                    }
                    let mode = normalize_content_mode(content_mode);
                    let prunes_note = backend == Backend::Cplus;
                    return Ok(json!({
                        "db": candidate,
                        "found": true,
                        "node": record_node(node, &mode, include_raw_fields, prunes_note, backend),
                    }));
                }
            }
            Err(error) => {
                if is_database_not_found_error(&error) {
                    continue;
                }
                return Err(error);
            }
        }
    }
    Ok(json!({
        "db": Value::Null,
        "found": false,
        "node": Value::Null,
        "message": format!("Node {node_id} not found in any db."),
    }))
}

fn check_servlet_active(
    runtime: &mut runtime::GraphRuntime,
    database: &str,
    node: &Value,
) -> Result<bool, String> {
    let cypher = "MATCH (s:ServletJspAnalysisState {project_id: $project_id, module_id: $module_id}) WHERE s.active_generation = $generation_id RETURN s.id AS id LIMIT 1";
    let params = map_from([
        (
            "project_id",
            node.get("project_id").cloned().unwrap_or(Value::Null),
        ),
        (
            "module_id",
            node.get("module_id").cloned().unwrap_or(Value::Null),
        ),
        (
            "generation_id",
            node.get("generation_id").cloned().unwrap_or(Value::Null),
        ),
    ]);
    let rows = runtime.execute_query(cypher, &super::prepare_params(&params), Some(database))?;
    Ok(!rows.is_empty())
}

pub fn tool_get_node_details(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let node_ids = backend_string_list(payload.get("node_ids"));
    if node_ids.is_empty() {
        return Err("node_ids must be a non-empty list.".to_string());
    }
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let candidates = resolve_db_candidates(project_id.as_deref())?;
    let project_value = project_param(&project_id);
    for candidate in &candidates {
        let cypher = "\n        MATCH (n)\n        WHERE n.id IN $ids\n          AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized)\n        RETURN n\n        ";
        let result = run_cypher_first(
            runtime,
            cypher,
            &map_from([("ids", json!(node_ids)), ("project_id", project_value.clone())]),
            std::slice::from_ref(candidate),
        );
        match result {
            Ok((_, rows)) => {
                let nodes: Vec<Value> = rows
                    .iter()
                    .filter_map(|row| row.get("n").cloned())
                    .collect();
                if !nodes.is_empty() {
                    // servlet_jsp batch guard.
                    let servlet_nodes: Vec<&Value> = nodes
                        .iter()
                        .filter(|node| {
                            node.get("framework").and_then(Value::as_str) == Some("servlet_jsp")
                        })
                        .collect();
                    let mut filtered = nodes.clone();
                    if !servlet_nodes.is_empty() {
                        let rows_param: Vec<Value> = servlet_nodes.iter().map(|node| (*node).clone()).collect();
                        let cypher_active = "UNWIND $rows AS row MATCH (s:ServletJspAnalysisState {project_id: row.project_id, module_id: row.module_id}) WHERE s.active_generation = row.generation_id RETURN row.id AS id";
                        let active_rows = runtime.execute_query(
                            cypher_active,
                            &super::prepare_params(&map_from([("rows", json!(rows_param))])),
                            Some(candidate),
                        )?;
                        let active_ids: BTreeSet<String> = active_rows
                            .iter()
                            .filter_map(|row| row.get("id").and_then(Value::as_str))
                            .map(str::to_string)
                            .collect();
                        filtered.retain(|node| {
                            node.get("framework").and_then(Value::as_str) != Some("servlet_jsp")
                                || node
                                    .get("id")
                                    .and_then(Value::as_str)
                                    .map(|id| active_ids.contains(id))
                                    .unwrap_or(false)
                        });
                    }
                    let mode = normalize_content_mode(content_mode);
                    let prunes_note = backend == Backend::Cplus;
                    let result_nodes: Vec<Value> = filtered
                        .iter()
                        .map(|node| record_node(node, &mode, include_raw_fields, prunes_note, backend))
                        .collect();
                    return Ok(json!({"db": candidate, "nodes": result_nodes}));
                }
            }
            Err(error) => {
                if is_database_not_found_error(&error) {
                    continue;
                }
                return Err(error);
            }
        }
    }
    Ok(json!({"db": candidates.first().cloned(), "nodes": []}))
}

// ---------------------------------------------------------------------------
// list_possible_calls
// ---------------------------------------------------------------------------

pub fn tool_list_possible_calls(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let limit = payload
        .get("limit")
        .and_then(Value::as_i64)
        .or_else(|| payload.get("top_k").and_then(Value::as_i64))
        .unwrap_or(200);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let project_value = project_param(&project_id);
    let cypher = "\n        MATCH (a:Function)-[r:POSSIBLE_CALLS]->(b:Function)\n        WHERE ($project_id IS NULL OR a.project_id_normalized STARTS WITH $project_id_normalized)\n        AND ($project_id IS NULL OR b.project_id_normalized STARTS WITH $project_id_normalized)\n        RETURN a, b, r\n        LIMIT $limit\n        ";
    let mut seen_ids: BTreeSet<String> = BTreeSet::new();
    for candidate in &db_candidates {
        let result = run_cypher_first(
            runtime,
            cypher,
            &map_from([("limit", json!(limit)), ("project_id", project_value.clone())]),
            std::slice::from_ref(candidate),
        );
        match result {
            Ok((_, rows)) => {
                let mut nodes: Vec<Value> = Vec::new();
                let mut edges: Vec<Value> = Vec::new();
                for row in &rows {
                    for key in ["a", "b"] {
                        if let Some(node) = row.get(key)
                            && let Some(id) = node.get("id").and_then(Value::as_str)
                            && seen_ids.insert(id.to_string())
                        {
                            nodes.push(record_node(node, &mode, include_raw_fields, prunes_note, backend));
                        }
                    }
                    if let Some(rel) = row.get("r") {
                        edges.push(record_rel(rel, None));
                    }
                }
                if !nodes.is_empty() || !edges.is_empty() {
                    return Ok(json!({"db": candidate, "nodes": nodes, "edges": edges}));
                }
            }
            Err(error) => {
                if is_database_not_found_error(&error) {
                    continue;
                }
                return Err(error);
            }
        }
    }
    Ok(json!({
        "db": db_candidates.first().cloned(),
        "nodes": [],
        "edges": [],
    }))
}

// ---------------------------------------------------------------------------
// listup tools
// ---------------------------------------------------------------------------

pub fn tool_listup_symbols_matching_file_path(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let modules = backend_string_list(payload.get("modules"));
    if modules.is_empty() {
        return Err("modules must be a non-empty list.".to_string());
    }
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let type_conditions = match backend {
        Backend::Cplus => Backend::Cplus
            .search_label_predicate("n", payload_bool(payload, "_fanout"))
            .trim_start_matches('(')
            .trim_end_matches(')')
            .to_string(),
        Backend::Android => Backend::Android.search_label_predicate("n", false)
            .trim_start_matches('(')
            .trim_end_matches(')')
            .to_string(),
    };
    let file_predicate = match backend {
        Backend::Cplus => "any(token IN $modules WHERE \
             toLower(coalesce(n.file_path, '')) CONTAINS toLower(token) OR \
             toLower(coalesce(n.path, '')) CONTAINS toLower(token))"
            .to_string(),
        Backend::Android => "any(token IN $modules WHERE \
             toLower(coalesce(n.file_path, '')) CONTAINS toLower(token) OR \
             toLower(coalesce(n.path, '')) CONTAINS toLower(token))"
            .to_string(),
    };
    let cypher = format!(
        "MATCH (n) WHERE ({type_conditions}) AND {file_predicate} \
         AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized) \
         RETURN n"
    );
    let (used_db, results) = run_cypher_first(
        runtime,
        &cypher,
        &map_from([
            ("modules", json!(modules)),
            ("project_id", project_param(&project_id)),
        ]),
        &db_candidates,
    )?;
    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let symbols: Vec<Value> = results
        .iter()
        .filter_map(|row| row.get("n").cloned())
        .map(|node| record_node(&node, &mode, include_raw_fields, prunes_note, backend))
        .collect();
    Ok(json!({"db": used_db, "symbols": symbols}))
}

pub fn tool_listup_class_matching_path(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let class_names = backend_string_list(payload.get("class_names"));
    if class_names.is_empty() {
        return Err("class_names must be a non-empty list.".to_string());
    }
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let cypher = "MATCH (c) WHERE (c:Class OR c:Type) AND any(token IN $classes WHERE toLower(c.name) CONTAINS toLower(token) OR toLower(c.qualified_name) CONTAINS toLower(token)) AND ($project_id IS NULL OR c.project_id_normalized STARTS WITH $project_id_normalized) OPTIONAL MATCH (c)-[:DECLARES]->(f:Function) WHERE ($project_id IS NULL OR f.project_id_normalized STARTS WITH $project_id_normalized) RETURN c, f";
    let (used_db, results) = run_cypher_first(
        runtime,
        cypher,
        &map_from([
            ("classes", json!(class_names)),
            ("project_id", project_param(&project_id)),
        ]),
        &db_candidates,
    )?;
    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let mut classes_seen: BTreeMap<String, Value> = BTreeMap::new();
    let mut class_order: Vec<String> = Vec::new();
    let mut functions: Vec<Value> = Vec::new();
    for row in &results {
        if let Some(class_node) = row.get("c") {
            let recorded = record_node(class_node, &mode, include_raw_fields, prunes_note, backend);
            let id = recorded.get("id").and_then(Value::as_str).map(str::to_string);
            if let Some(id) = id
                && !classes_seen.contains_key(&id)
            {
                class_order.push(id.clone());
                classes_seen.insert(id, recorded);
            }
        }
        if let Some(function) = row.get("f")
            && !function.is_null()
        {
            functions.push(record_node(function, &mode, include_raw_fields, prunes_note, backend));
        }
    }
    let classes: Vec<Value> = class_order
        .into_iter()
        .filter_map(|id| classes_seen.remove(&id))
        .collect();
    let _ = backend;
    Ok(json!({"db": used_db, "classes": classes, "functions": functions}))
}

pub fn tool_list_up_entrypoint(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let modules = backend_string_list(payload.get("modules"));
    if modules.is_empty() {
        return Err("modules must be a non-empty list.".to_string());
    }
    let limit = payload
        .get("limit")
        .and_then(Value::as_i64)
        .or_else(|| payload.get("top_k").and_then(Value::as_i64))
        .unwrap_or(200);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let cypher = "MATCH (caller:Function)-[:CALLS]->(f:Function) WHERE any(token IN $modules WHERE toLower(coalesce(f.file_path, '')) CONTAINS toLower(token)) AND none(token IN $modules WHERE toLower(coalesce(caller.file_path, '')) CONTAINS toLower(token)) AND (f.kind IS NULL OR f.kind <> 'lambda') AND ($project_id IS NULL OR f.project_id_normalized STARTS WITH $project_id_normalized) RETURN DISTINCT f LIMIT $limit";
    let (used_db, results) = run_cypher_first(
        runtime,
        cypher,
        &map_from([
            ("modules", json!(modules)),
            ("limit", json!(limit)),
            ("project_id", project_param(&project_id)),
        ]),
        &db_candidates,
    )?;
    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let functions: Vec<Value> = results
        .iter()
        .filter_map(|row| row.get("f").cloned())
        .map(|node| record_node(&node, &mode, include_raw_fields, prunes_note, backend))
        .collect();
    let _ = backend;
    Ok(json!({"db": used_db, "functions": functions}))
}

// ---------------------------------------------------------------------------
// annotate_node
// ---------------------------------------------------------------------------

pub fn tool_annotate_node(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let node_id = match payload.get("node_id") {
        Some(Value::Null) | None => return Err("node_id is required.".to_string()),
        Some(value) => match value {
            Value::String(text) => text.clone(),
            other => other.to_string(),
        },
    };
    let note = payload.get("note").cloned().unwrap_or(Value::Null);
    let tags = payload.get("tags").cloned().unwrap_or(Value::Null);
    let severity = payload.get("severity").cloned().unwrap_or(Value::Null);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode");
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let cypher = "MATCH (n) WHERE n.id = $id AND ($project_id IS NULL OR n.project_id_normalized STARTS WITH $project_id_normalized) SET n.note = $note, n.tags = $tags, n.severity = $severity RETURN n";
    let (used_db, result) = run_cypher_first(
        runtime,
        cypher,
        &map_from([
            ("id", json!(node_id)),
            ("note", note),
            ("tags", tags),
            ("severity", severity),
            ("project_id", project_param(&project_id)),
        ]),
        &db_candidates,
    )?;
    if result.is_empty() {
        return Ok(json!({
            "db": Value::Null,
            "node": Value::Null,
            "node_id": node_id,
            "annotated": false,
            "reason": "node_not_found",
            "message": format!("Node {node_id} not found; nothing was annotated."),
        }));
    }
    let mode = normalize_content_mode(content_mode);
    let prunes_note = backend == Backend::Cplus;
    let node = result
        .first()
        .and_then(|row| row.get("n").cloned())
        .unwrap_or(Value::Null);
    Ok(json!({
        "db": used_db,
        "found": true,
        "annotated": true,
        "node": record_node(&node, &mode, include_raw_fields, prunes_note, backend),
    }))
}

// ---------------------------------------------------------------------------
// get_ipc_message
// ---------------------------------------------------------------------------

pub fn tool_get_ipc_message(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let mut sender_queries = backend_string_list(payload.get("sender"));
    let mut receiver_queries = backend_string_list(payload.get("receiver"));
    if sender_queries.is_empty() {
        sender_queries = backend_string_list(payload.get("senders"));
    }
    if receiver_queries.is_empty() {
        receiver_queries = backend_string_list(payload.get("receivers"));
    }
    if sender_queries.is_empty() && receiver_queries.is_empty() {
        return Err("sender or receiver is required.".to_string());
    }
    let project_id = payload_string(payload, "project_id");
    let db_candidates = resolve_db_candidates(payload_str(payload, "db"))?;
    let project_value = match &project_id {
        Some(id) if !id.trim().is_empty() => json!(id.trim()),
        _ => json!(""),
    };
    let cypher = "\n    MATCH (m:Message)\n    WHERE ($project_id = '' OR coalesce(m.project_id_normalized, '') = $project_id)\n      AND (\n        size($sender_queries) = 0\n        OR any(q IN $sender_queries WHERE toLower(coalesce(m.sender, '')) CONTAINS toLower(q))\n      )\n      AND (\n        size($receiver_queries) = 0\n        OR any(q IN $receiver_queries WHERE toLower(coalesce(m.receiver, '')) CONTAINS toLower(q))\n      )\n    RETURN\n      m.id AS id,\n      m.name AS name,\n      m.sender AS sender,\n      m.receiver AS receiver,\n      m.payload AS payload,\n      m.response AS response,\n      m.explanation AS explanation,\n      m.file_path AS file_path,\n      m.line AS line,\n      m.confidence AS confidence,\n      m.language AS language\n    ORDER BY coalesce(m.confidence, 0.0) DESC, coalesce(m.file_path, ''), coalesce(m.line, 0)\n    LIMIT 500\n    ";
    let mut messages: Vec<Value> = Vec::new();
    let mut graph_ok = true;
    let ipc_params = map_from([
        ("project_id", project_value.clone()),
        ("sender_queries", json!(sender_queries)),
        ("receiver_queries", json!(receiver_queries)),
    ]);
    match run_cypher_first(runtime, cypher, &ipc_params, &db_candidates) {
        Ok((_, rows)) => {
            for row in rows {
                let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
                messages.push(json!({
                    "id": get("id"),
                    "name": get("name"),
                    "sender": get("sender"),
                    "receiver": get("receiver"),
                    "payload": get("payload"),
                    "response": get("response"),
                    "explanation": get("explanation"),
                    "source": {
                        "file": get("file_path"),
                        "line": get("line"),
                    },
                    "confidence": get("confidence"),
                    "language": get("language"),
                }));
            }
        }
        Err(_) => {
            graph_ok = false;
        }
    }
    let _ = backend;
    if !graph_ok {
        // JSON fallback: temp/ipc_messages.json — hiếm khi tồn tại; trả rỗng.
        messages = load_ipc_messages_json();
    }
    let matches_any = |field: &Value, queries: &[String]| -> bool {
        if field.is_null() {
            return false;
        }
        let lowered = match field {
            Value::String(text) => text.to_lowercase(),
            other => other.to_string().to_lowercase(),
        };
        queries
            .iter()
            .any(|query| lowered.contains(&query.to_lowercase()))
    };
    if !sender_queries.is_empty() && !receiver_queries.is_empty() {
        let matched: Vec<Value> = messages
            .into_iter()
            .filter(|message| {
                matches_any(message.get("sender").unwrap_or(&Value::Null), &sender_queries)
                    && matches_any(
                        message.get("receiver").unwrap_or(&Value::Null),
                        &receiver_queries,
                    )
            })
            .collect();
        return Ok(Value::Array(matched));
    }
    if !sender_queries.is_empty() {
        let mut receivers: Vec<Value> = Vec::new();
        let mut seen: BTreeSet<String> = BTreeSet::new();
        for message in &messages {
            if matches_any(message.get("sender").unwrap_or(&Value::Null), &sender_queries)
                && let Some(value) = message.get("receiver")
                && !value.is_null()
            {
                let text = match value {
                    Value::String(text) => text.clone(),
                    other => other.to_string(),
                };
                if seen.insert(text.clone()) {
                    receivers.push(json!(text));
                }
            }
        }
        return Ok(Value::Array(receivers));
    }
    let mut senders: Vec<Value> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for message in &messages {
        if matches_any(message.get("receiver").unwrap_or(&Value::Null), &receiver_queries)
            && let Some(value) = message.get("sender")
            && !value.is_null()
        {
            let text = match value {
                Value::String(text) => text.clone(),
                other => other.to_string(),
            };
            if seen.insert(text.clone()) {
                senders.push(json!(text));
            }
        }
    }
    Ok(Value::Array(senders))
}

fn load_ipc_messages_json() -> Vec<Value> {
    // IPC_MESSAGES_PATH = mcp/../../temp/ipc_messages.json.
    let repo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(std::path::Path::parent)
        .and_then(std::path::Path::parent)
        .map(std::path::Path::to_path_buf)
        .unwrap_or_default();
    let path = repo.join("temp").join("ipc_messages.json");
    let Ok(text) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(value) = serde_json::from_str::<Value>(&text) else {
        return Vec::new();
    };
    value
        .get("messages")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
        .into_iter()
        .filter(Value::is_object)
        .collect()
}

// ---------------------------------------------------------------------------
// list_databases + inspect_parser_capabilities
// ---------------------------------------------------------------------------

pub fn tool_list_databases(runtime: &mut runtime::GraphRuntime, _payload: Value) -> Result<Value, String> {
    let names = runtime.list_databases();
    let default_db = runtime_default_db_name();
    Ok(json!({"databases": names, "default": default_db}))
}

pub fn tool_inspect_parser_capabilities(
    runtime: &mut runtime::GraphRuntime,
    payload: &Value,
) -> Result<Value, String> {
    let parser_type = payload_str(payload, "parser_type").unwrap_or("");
    let project_id = payload_str(payload, "project_id").unwrap_or("");
    let selected_parser = crate::dispatch::normalize_parser_type(Some(parser_type))
        .or_else(|| {
            if project_id.is_empty() {
                return None;
            }
            resolve_project_targets(Some(project_id), None)
                .ok()
                .and_then(|targets| crate::dispatch::normalize_parser_type(Some(targets.parser_type.as_deref().unwrap_or(""))))
        })
        .unwrap_or_else(|| "cplus".to_string());
    let capability = capability_for_parser(Some(&selected_parser));
    let Some(capability) = capability else {
        let mut payload_map = Map::new();
        payload_map.insert("parser_type".to_string(), json!(selected_parser));
        return Ok(crate::dispatch::unsupported_parser_result(
            "inspect_parser_capabilities",
            &Value::Object(payload_map),
            &selected_parser,
        ));
    };

    let db_candidates = resolve_db_candidates(Some(project_id))?;
    let labels = runtime::list_node_labels(runtime, &db_candidates);
    let relationships = runtime::list_relationship_types(runtime, &db_candidates);
    let sorted_relationships_cache = {
        let mut sorted_relationships = relationships.clone().unwrap_or_default();
        sorted_relationships.sort();
        sorted_relationships
    };
    let evaluation = evaluate_capability_schema(
        capability,
        labels.as_deref(),
        relationships.as_deref(),
    );
    let dimensions = evaluation
        .get("dimensions")
        .cloned()
        .unwrap_or(json!({}));
    let effective_support: Map<String, Value> = dimensions
        .as_object()
        .map(|object| {
            object
                .iter()
                .filter_map(|(dimension, details)| {
                    details.get("effective").map(|value| (dimension.clone(), value.clone()))
                })
                .collect()
        })
        .unwrap_or_default();
    let mut unavailable_dimensions: Vec<String> = Vec::new();
    let mut unknown_dimensions: Vec<String> = Vec::new();
    if let Some(object) = dimensions.as_object() {
        for (dimension, details) in object {
            let advertised = details
                .get("advertised")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let observed = details
                .get("observed")
                .and_then(Value::as_str)
                .unwrap_or_default();
            if advertised != "none" && observed == "unavailable" {
                unavailable_dimensions.push(dimension.clone());
            }
            if advertised != "none" && observed == "unknown" {
                unknown_dimensions.push(dimension.clone());
            }
        }
    }
    let recommended_action = if !unknown_dimensions.is_empty() {
        "inspect_provider_schema"
    } else if !unavailable_dimensions.is_empty() {
        "run_incremental_sync"
    } else {
        "none"
    };
    Ok(json!({
        "ok": true,
        "requested_parser": selected_parser,
        "canonical_parser": capability.name,
        "query_engine": query_engine_for_backend(Some(capability.backend.as_str())),
        "db": resolve_graph_database(Some(project_id)),
        "advertised_support": Value::Object(
            capability
                .support
                .iter()
                .map(|(key, value)| (key.clone(), json!(value)))
                .collect(),
        ),
        "effective_support": Value::Object(effective_support),
        "schema_status": evaluation.get("schema_status").cloned().unwrap_or(Value::Null),
        "schema_fingerprint": evaluation.get("schema_fingerprint").cloned().unwrap_or(Value::Null),
        "contract_version": evaluation.get("contract_version").cloned().unwrap_or(Value::Null),
        "dimensions": dimensions,
        "available_labels": labels.unwrap_or_default().into_iter().collect::<BTreeSet<String>>().into_iter().collect::<Vec<String>>(),
        "available_relationships": sorted_relationships_cache,
        "unavailable_dimensions": unavailable_dimensions,
        "unknown_dimensions": unknown_dimensions,
        "recommended_action": recommended_action,
    }))
}

/// `_resolve_graph_database` (unified) — registry graph, fallback raw id.
pub fn resolve_graph_database(project_id: Option<&str>) -> Option<String> {
    let project_id = project_id?;
    let normalized = project_id.trim();
    if normalized.is_empty() {
        return None;
    }
    match resolve_project_targets(Some(normalized), None) {
        Ok(targets) => Some(targets.code_graph),
        Err(_) => Some(normalized.to_string()),
    }
}

fn runtime_default_db_name() -> String {
    super::runtime_default_db_name()
}

// Keep catalog import used for supported_parsers parity in dispatch.
#[allow(dead_code)]
fn _catalog_touch() -> Vec<Value> {
    capability_catalog()
}

// Silence unused import when features change.
#[allow(unused_imports)]
use LEGACY_SEARCH_FRAMEWORKS as _LEGACY_SEARCH_FRAMEWORKS;

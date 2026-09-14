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
//! Traversal tools — port của `cplus_mcp.py` `tool_query_subgraph`,
//! `tool_find_paths`, `tool_find_path_between_module`, `tool_trace_flow`,
//! `tool_trace_flow_between_module` (đúng semantics fanout + capability
//! default relationships).

use serde_json::{json, Map, Value};

use super::{
    build_rel_match, default_flow_rel_types, is_database_not_found_error, normalize_content_mode,
    normalize_depth, outcome_payload, payload_bool, payload_str, payload_string,
    paths_to_graph, resolve_db_candidates, resolve_rel_types_with_diagnostics,
    run_cypher_first, unsupported_relationship_result, Backend, ResolvedRelationships,
};
use crate::framework_registry::capability_for_parser;
use super::runtime;

/// `_profile_rel_types`.
fn profile_rel_types(
    parser_type: Option<&str>,
    profile: Option<&str>,
) -> Result<Option<Vec<String>>, String> {
    let normalized = profile.unwrap_or("").trim().to_lowercase();
    if normalized.is_empty() || normalized == "default" {
        return Ok(None);
    }
    let parser = parser_type
        .unwrap_or("cplus")
        .trim()
        .to_lowercase();
    let capability = match capability_for_parser(Some(&parser)) {
        Some(capability) => capability,
        None => return Ok(None),
    };
    if (normalized == "strict" || normalized == "conservative") && capability.name != "cplus" {
        return Err(format!(
            "query_profile {normalized:?} is only supported for C/C++/Pro*C; parser {:?} does not publish compatible call-evidence metadata",
            capability.name
        ));
    }
    let profiles = capability
        .to_dict()
        .get("default_query_profiles")
        .cloned()
        .unwrap_or(json!({}));
    let relationships = profiles.get(&normalized).cloned();
    match relationships {
        Some(value) => Ok(Some(
            value
                .as_array()
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .collect()
                })
                .unwrap_or_default(),
        )),
        None => {
            let mut keys: Vec<String> = profiles
                .as_object()
                .map(|object| object.keys().cloned().collect())
                .unwrap_or_default();
            keys.sort();
            Err(format!(
                "unknown query profile: {profile:?} for parser {:?}; expected one of {:?}",
                capability.name, keys
            ))
        }
    }
}

/// `_filter_strict_edges`.
fn filter_strict_edges(graph: &mut Value, function_id: &str) -> i64 {
    let mut kept_edges: Vec<Value> = Vec::new();
    let mut dropped: i64 = 0;
    let edges = graph
        .get("edges")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for edge in edges {
        let resolution_class = edge
            .get("properties")
            .and_then(|properties| properties.get("resolution_class"))
            .and_then(Value::as_str);
        if edge.get("type").and_then(Value::as_str) == Some("CALLS")
            && resolution_class == Some("direct_resolved")
        {
            kept_edges.push(edge);
        } else {
            dropped += 1;
        }
    }
    let mut keep_ids: std::collections::BTreeSet<String> =
        std::collections::BTreeSet::new();
    keep_ids.insert(function_id.to_string());
    for edge in &kept_edges {
        if let Some(start) = edge.get("start_id").map(super::canonical_marker) {
            keep_ids.insert(start);
        }
        if let Some(end) = edge.get("end_id").map(super::canonical_marker) {
            keep_ids.insert(end);
        }
    }
    if let Some(object) = graph.as_object_mut() {
        object.insert("edges".to_string(), Value::Array(kept_edges));
        let nodes = object
            .get("nodes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let kept_nodes: Vec<Value> = nodes
            .into_iter()
            .filter(|node| {
                node.get("id")
                    .map(super::canonical_marker)
                    .map(|id| keep_ids.contains(&id))
                    .unwrap_or(false)
            })
            .collect();
        object.insert("nodes".to_string(), Value::Array(kept_nodes));
    }
    dropped
}

pub fn tool_query_subgraph(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let function_id = match payload
        .get("function_id")
        .or_else(|| payload.get("id"))
    {
        Some(Value::Null) | None => return Err("function_id is required.".to_string()),
        Some(value) => match value {
            Value::String(text) => text.clone(),
            other => other.to_string(),
        },
    };
    let direction = payload_str(payload, "direction").unwrap_or("all").to_string();
    let max_depth = payload.get("max_depth").cloned().unwrap_or(json!(2));
    let include_possible = payload_bool(payload, "include_possible");
    let include_fp = payload_bool(payload, "include_fp");
    let query_profile = payload_str(payload, "query_profile").map(str::to_string);
    let parser_type = payload_str(payload, "parser_type").map(str::to_string);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode").map(str::to_string);
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let candidates = resolve_db_candidates(project_id.as_deref())?;
    let depth = normalize_depth(Some(&max_depth), 2, 10);
    let profile_rels = profile_rel_types(parser_type.as_deref(), query_profile.as_deref())?;
    let mut rel_input: Option<Value> = payload
        .get("relationship_types")
        .cloned()
        .or_else(|| payload.get("rel_types").cloned());
    if rel_input.is_none()
        && let Some(profile_relationships) = &profile_rels
    {
        rel_input = Some(json!(profile_relationships));
    }
    if rel_input.is_none() {
        let mut rels = vec!["CALLS".to_string()];
        if include_possible {
            rels.push("POSSIBLE_CALLS".to_string());
        }
        if include_fp {
            rels.push("CALLS_FUNCTION_POINTER".to_string());
        }
        if !include_possible && !include_fp {
            rels = default_flow_rel_types(parser_type.as_deref());
        }
        rel_input = Some(json!(rels));
    }
    let explicit = !payload_bool(payload, "_capability_default_relationships");
    let resolved: ResolvedRelationships = resolve_rel_types_with_diagnostics(
        runtime,
        rel_input.as_ref(),
        parser_type.as_deref(),
        &candidates,
        explicit,
    )?;
    let rel_types = resolved.applied;
    let mut capability_diagnostics = resolved.diagnostics;
    if let Some(profile) = &query_profile {
        if let Some(object) = capability_diagnostics.as_object_mut() {
            object.insert(
                "query_profile".to_string(),
                json!(profile.trim().to_lowercase()),
            );
        }
    }
    if rel_types.is_empty() {
        return Ok(unsupported_relationship_result(
            parser_type.as_deref(),
            &capability_diagnostics,
        ));
    }

    // Relationship default injection từ unified dispatch cho parser-backed
    // non-android backends đã xảy ra trước khi vào backend tool — mirror tại
    // đây vì Rust dispatch đưa payload đã merge (giữ đơn giản: nếu payload
    // mang `_capability_default_relationships`, rel_types đã là defaults).
    let direction_pattern = normalize_direction(&direction);
    let rel_pattern = format!("[:{}*1..{depth}]", rel_types.join("|"));
    let pattern = match direction_pattern.as_str() {
        "in" => format!("<-{rel_pattern}-"),
        "out" => format!("-{rel_pattern}->"),
        _ => format!("-{rel_pattern}-"),
    };
    let cypher = format!(
        "\n        MATCH (f:Function) WHERE f.id = $id\n          AND ($project_id IS NULL OR f.project_id_normalized STARTS WITH $project_id_normalized)\n        MATCH p=(f){pattern}(n)\n        RETURN p\n        "
    );
    let project_value = project_param(&project_id);
    let mut last_error: Option<String> = None;
    for candidate in &candidates {
        let result = run_cypher_first(
            runtime,
            &cypher,
            &super::map_from_static([
                ("id", json!(function_id)),
                ("project_id", project_value.clone()),
            ]),
            std::slice::from_ref(candidate),
        );
        match result {
            Ok((_, rows)) => {
                let paths: Vec<Value> = rows
                    .iter()
                    .filter_map(|row| row.get("p").cloned())
                    .collect();
                // Android body trả graph + db cho candidate đầu tiên kết nối
                // được — kể cả khi paths rỗng (empty result, không error).
                if !paths.is_empty() || backend == Backend::Android {
                    let mode = normalize_content_mode(content_mode.as_deref());
                    let prunes_note = backend == Backend::Cplus;
                    let mut graph =
                        paths_to_graph(&paths, &mode, include_raw_fields, prunes_note, backend);
                    let mut result_is_empty = false;
                    if query_profile.as_deref().map(str::trim).map(str::to_lowercase).as_deref()
                        == Some("strict")
                    {
                        let dropped = filter_strict_edges(&mut graph, &function_id);
                        if let Some(object) = capability_diagnostics.as_object_mut() {
                            object.insert("strict_edges_dropped".to_string(), json!(dropped));
                        }
                        let edges_empty = graph
                            .get("edges")
                            .and_then(Value::as_array)
                            .map(Vec::is_empty)
                            .unwrap_or(true);
                        if edges_empty {
                            result_is_empty = true;
                        }
                    }
                    if let Some(object) = graph.as_object_mut() {
                        object.insert("db".to_string(), json!(candidate));
                        // Android body (`android_mcp.tool_query_subgraph`) trả
                        // graph + db duy nhất — không gắn diagnostics.
                        if backend == Backend::Cplus {
                            object.insert(
                                "capability_diagnostics".to_string(),
                                capability_diagnostics.clone(),
                            );
                        }
                    }
                    if backend == Backend::Cplus {
                        let outcome = outcome_payload(
                            runtime,
                            &candidates,
                            project_id.as_deref(),
                            result_is_empty,
                            None,
                        );
                        if let Some(outcome_object) = outcome.as_object() {
                            for (key, value) in outcome_object {
                                if let Some(graph_object) = graph.as_object_mut() {
                                    graph_object.insert(key.clone(), value.clone());
                                }
                            }
                        }
                        if result_is_empty
                            && graph.get("outcome").and_then(Value::as_str) != Some("incomplete")
                            && let Some(graph_object) = graph.as_object_mut()
                        {
                            graph_object
                                .insert("reason".to_string(), json!("no_accepted_strict_edges"));
                        }
                    }
                    return Ok(graph);
                }
            }
            Err(error) => {
                last_error = Some(error.clone());
                if is_database_not_found_error(&error) {
                    continue;
                }
                return Err(error);
            }
        }
    }
    if let Some(error) = last_error {
        return Err(error);
    }
    // Android body: total miss là RuntimeError (`No subgraph found for node
    // {id} in any db.`) — lỗi tool thật, không phải empty result.
    if backend == Backend::Android {
        return Err(format!(
            "No subgraph found for node {function_id} in any db."
        ));
    }
    let mut result = Map::new();
    result.insert("db".to_string(), json!(candidates.first().cloned()));
    result.insert("nodes".to_string(), json!([]));
    result.insert("edges".to_string(), json!([]));
    result.insert(
        "capability_diagnostics".to_string(),
        capability_diagnostics.clone(),
    );
    let outcome =
        outcome_payload(runtime, &candidates, project_id.as_deref(), true, None);
    if let Some(outcome_object) = outcome.as_object() {
        for (key, value) in outcome_object {
            result.insert(key.clone(), value.clone());
        }
    }
    let mut result_value = Value::Object(result);
    if result_value.get("outcome").and_then(Value::as_str) != Some("incomplete")
        && let Some(object) = result_value.as_object_mut()
    {
        object.insert("reason".to_string(), json!("no_subgraph"));
    }
    Ok(result_value)
}

fn project_param(project_id: &Option<String>) -> Value {
    match project_id {
        Some(id) if !id.trim().is_empty() => json!(id),
        _ => Value::Null,
    }
}

fn normalize_direction(direction: &str) -> String {
    let text = direction.trim().to_lowercase();
    match text.as_str() {
        "in" | "incoming" | "upstream" => "in".to_string(),
        "out" | "outgoing" | "downstream" => "out".to_string(),
        _ => "both".to_string(),
    }
}

pub fn tool_find_paths(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let start = payload.get("start_function_id").cloned().unwrap_or(Value::Null);
    let end = payload.get("end_function_id").cloned().unwrap_or(Value::Null);
    if start.is_null() || end.is_null() {
        return Err("start_function_id and end_function_id are required.".to_string());
    }
    let start_id = value_to_string(&start);
    let end_id = value_to_string(&end);
    let max_depth = payload.get("max_depth").cloned().unwrap_or(json!(8));
    let include_possible = payload_bool(payload, "include_possible");
    let include_fp = payload_bool(payload, "include_fp");
    let parser_type = payload_str(payload, "parser_type").map(str::to_string);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode").map(str::to_string);
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let candidates = resolve_db_candidates(project_id.as_deref())?;
    let depth = normalize_depth(Some(&max_depth), 8, 20);
    let mut rel_input = payload
        .get("relationship_types")
        .cloned()
        .or_else(|| payload.get("rel_types").cloned());
    if rel_input.is_none() {
        let mut rels = vec!["CALLS".to_string()];
        if include_possible {
            rels.push("POSSIBLE_CALLS".to_string());
        }
        if include_fp {
            rels.push("CALLS_FUNCTION_POINTER".to_string());
        }
        if !include_possible && !include_fp {
            rels = default_flow_rel_types(parser_type.as_deref());
        }
        rel_input = Some(json!(rels));
    }
    let resolved = resolve_rel_types_with_diagnostics(
        runtime,
        rel_input.as_ref(),
        parser_type.as_deref(),
        &candidates,
        !payload_bool(payload, "_capability_default_relationships"),
    )?;
    let rel_types = resolved.applied;
    let capability_diagnostics = resolved.diagnostics;
    if rel_types.is_empty() {
        return Ok(unsupported_relationship_result(
            parser_type.as_deref(),
            &capability_diagnostics,
        ));
    }
    let rel_pattern = format!("[:{}*..{depth}]", rel_types.join("|"));
    let cypher = format!(
        "\n        MATCH (a:Function) WHERE a.id = $start\n          AND ($project_id IS NULL OR a.project_id_normalized STARTS WITH $project_id_normalized)\n        MATCH (b:Function) WHERE b.id = $end\n          AND ($project_id IS NULL OR b.project_id_normalized STARTS WITH $project_id_normalized)\n        AND a.id <> b.id\n        MATCH p=(a)-{rel_pattern}->(b)\n        RETURN p ORDER BY length(p) LIMIT $limit\n        "
    );
    let project_value = project_param(&project_id);
    for candidate in &candidates {
        let result = run_cypher_first(
            runtime,
            &cypher,
            &super::map_from_static([
                ("start", json!(start_id)),
                ("end", json!(end_id)),
                ("project_id", project_value.clone()),
                ("limit", json!(10)),
            ]),
            std::slice::from_ref(candidate),
        );
        match result {
            Ok((_, rows)) => {
                let paths: Vec<Value> = rows
                    .iter()
                    .filter_map(|row| row.get("p").cloned())
                    .collect();
                if !paths.is_empty() {
                    let mode = normalize_content_mode(content_mode.as_deref());
                    let prunes_note = backend == Backend::Cplus;
                    let mut graph =
                        paths_to_graph(&paths, &mode, include_raw_fields, prunes_note, backend);
                    if let Some(object) = graph.as_object_mut() {
                        object.insert("db".to_string(), json!(candidate));
                        if backend == Backend::Cplus {
                            object.insert(
                                "capability_diagnostics".to_string(),
                                capability_diagnostics,
                            );
                        }
                    }
                    return Ok(graph);
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
    let mut miss = json!({
        "db": candidates.first().cloned(),
        "nodes": [],
        "edges": [],
        "reason": "no_path_found",
    });
    if backend == Backend::Cplus {
        if let Some(object) = miss.as_object_mut() {
            object.insert(
                "capability_diagnostics".to_string(),
                capability_diagnostics,
            );
        }
    }
    Ok(miss)
}

fn value_to_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        other => other.to_string(),
    }
}

pub fn tool_find_path_between_module(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let source_modules = super::backend_string_list(payload.get("source_modules"));
    let target_modules = super::backend_string_list(payload.get("target_modules"));
    if source_modules.is_empty() || target_modules.is_empty() {
        return Err("source_modules and target_modules must be non-empty lists.".to_string());
    }
    let max_depth = payload.get("max_depth").cloned().unwrap_or(json!(8));
    let direction = payload_str(payload, "direction").unwrap_or("out").to_string();
    let include_possible = payload_bool(payload, "include_possible");
    let include_fp = payload_bool(payload, "include_fp");
    let parser_type = payload_str(payload, "parser_type").map(str::to_string);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode").map(str::to_string);
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let depth = normalize_depth(Some(&max_depth), 8, 20);
    let mut rel_input = payload
        .get("relationship_types")
        .cloned()
        .or_else(|| payload.get("rel_types").cloned());
    if rel_input.is_none() {
        let mut rels = vec!["CALLS".to_string()];
        if include_possible {
            rels.push("POSSIBLE_CALLS".to_string());
        }
        if include_fp {
            rels.push("CALLS_FUNCTION_POINTER".to_string());
        }
        if !include_possible && !include_fp {
            rels = default_flow_rel_types(parser_type.as_deref());
        }
        rel_input = Some(json!(rels));
    }
    let resolved = resolve_rel_types_with_diagnostics(
        runtime,
        rel_input.as_ref(),
        parser_type.as_deref(),
        &db_candidates,
        !payload_bool(payload, "_capability_default_relationships"),
    )?;
    let rel_types = resolved.applied;
    let capability_diagnostics = resolved.diagnostics;
    if rel_types.is_empty() {
        return Ok(unsupported_relationship_result(
            parser_type.as_deref(),
            &capability_diagnostics,
        ));
    }
    let project_value = project_param(&project_id);
    for direction_override in [Some(direction.as_str()), Some("both")] {
        let current_direction = match direction_override {
            Some(value) => value,
            None => continue,
        };
        let paths = find_module_paths_directed(
            runtime,
            &source_modules,
            &target_modules,
            &rel_types,
            depth,
            10,
            current_direction,
            &project_value,
            &db_candidates,
        )?;
        if !paths.is_empty() {
            let mode = normalize_content_mode(content_mode.as_deref());
            let prunes_note = backend == Backend::Cplus;
            let mut graph = paths_to_graph(&paths, &mode, include_raw_fields, prunes_note, backend);
            if let Some(object) = graph.as_object_mut() {
                object.insert("db".to_string(), json!(db_candidates.first().cloned()));
                if backend == Backend::Cplus {
                    object.insert(
                        "capability_diagnostics".to_string(),
                        capability_diagnostics,
                    );
                }
            }
            return Ok(graph);
        }
        if direction.to_lowercase().contains("both")
            || ["both", "any", "undirected"].contains(&direction.to_lowercase().as_str())
        {
            break;
        }
    }
    let mut miss = json!({
        "db": db_candidates.first().cloned(),
        "nodes": [],
        "edges": [],
    });
    if backend == Backend::Cplus {
        if let Some(object) = miss.as_object_mut() {
            object.insert(
                "capability_diagnostics".to_string(),
                capability_diagnostics,
            );
        }
    }
    Ok(miss)
}

#[allow(clippy::too_many_arguments)]
fn find_module_paths_directed(
    runtime: &mut runtime::GraphRuntime,
    source_modules: &[String],
    target_modules: &[String],
    rel_types: &[String],
    max_depth: i64,
    limit: i64,
    direction: &str,
    project_value: &Value,
    db_candidates: &[String],
) -> Result<Vec<Value>, String> {
    let rel_types_str = rel_types.join("|");
    let normalized_direction = normalize_direction(direction);
    let rel_pattern = match normalized_direction.as_str() {
        "in" => format!("<-[:{rel_types_str}*..{max_depth}]-"),
        "both" => format!("-[:{rel_types_str}*..{max_depth}]-"),
        _ => format!("-[:{rel_types_str}*..{max_depth}]->"),
    };
    let cypher = format!(
        "\n        WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets\n        MATCH (s:Function)<-[:CONTAINS]-(sf:File)\n        MATCH (t:Function)<-[:CONTAINS]-(tf:File)\n        WHERE any(token IN sources WHERE\n            toLower(coalesce(s.file_path, '')) CONTAINS token OR\n            toLower(coalesce(sf.path, '')) CONTAINS token OR\n            toLower(coalesce(sf.file_path, '')) CONTAINS token)\n          AND ($project_id IS NULL OR s.project_id_normalized STARTS WITH $project_id_normalized)\n        AND any(token IN targets WHERE\n            toLower(coalesce(t.file_path, '')) CONTAINS token OR\n            toLower(coalesce(tf.path, '')) CONTAINS token OR\n            toLower(coalesce(tf.file_path, '')) CONTAINS token)\n          AND ($project_id IS NULL OR t.project_id_normalized STARTS WITH $project_id_normalized)\n        AND s.id <> t.id\n        MATCH p=(s){rel_pattern}(t)\n        RETURN p ORDER BY length(p)\n        LIMIT $limit\n        "
    );
    let (_, rows) = run_cypher_first(
        runtime,
        &cypher,
        &super::map_from_static([
            ("sources", json!(source_modules)),
            ("targets", json!(target_modules)),
            ("limit", json!(limit)),
            ("project_id", project_value.clone()),
        ]),
        db_candidates,
    )?;
    Ok(rows
        .iter()
        .filter_map(|row| row.get("p").cloned())
        .collect())
}

pub fn tool_trace_flow(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let start = payload.get("start_id").cloned().unwrap_or(Value::Null);
    if start.is_null() {
        return Err("start_id is required.".to_string());
    }
    let end = payload.get("end_id").cloned().unwrap_or(Value::Null);
    let parser_type = payload_str(payload, "parser_type").map(str::to_string);
    let max_depth = payload.get("max_depth").cloned().unwrap_or(json!(6));
    let direction = normalize_direction(payload_str(payload, "direction").unwrap_or("out"));
    let limit = payload
        .get("limit")
        .and_then(Value::as_i64)
        .or_else(|| payload.get("top_k").and_then(Value::as_i64))
        .unwrap_or(30);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode").map(str::to_string);
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let candidates = resolve_db_candidates(project_id.as_deref())?;
    let rel_value = payload
        .get("rel_types")
        .cloned()
        .or_else(|| payload.get("relationship_types").cloned());
    let resolved = resolve_rel_types_with_diagnostics(
        runtime,
        rel_value.as_ref(),
        parser_type.as_deref(),
        &candidates,
        rel_value.is_some() && !payload_bool(payload, "_capability_default_relationships"),
    )?;
    let rel_types = resolved.applied;
    let capability_diagnostics = resolved.diagnostics;
    if rel_types.is_empty() {
        return Ok(unsupported_relationship_result(
            parser_type.as_deref(),
            &capability_diagnostics,
        ));
    }
    let depth = normalize_depth(Some(&max_depth), 6, 20);
    let rel_match = build_rel_match(&rel_types, depth, &direction);
    let start_id = value_to_string(&start);
    let end_id = if end.is_null() { None } else { Some(value_to_string(&end)) };
    let project_value = project_param(&project_id);
    let (used_db, rows) = if let Some(end_id) = &end_id {
        let query = format!(
            "MATCH (a {{id: $start}}) \
             WHERE ($project_id IS NULL OR a.project_id_normalized STARTS WITH $project_id_normalized) \
             MATCH (b {{id: $end}}) \
             WHERE ($project_id IS NULL OR b.project_id_normalized STARTS WITH $project_id_normalized) \
             MATCH p=(a){rel_match}(b) \
             RETURN p ORDER BY length(p) LIMIT $limit"
        );
        run_cypher_first(
            runtime,
            &query,
            &super::map_from_static([
                ("start", json!(start_id)),
                ("end", json!(end_id)),
                ("project_id", project_value),
                ("limit", json!(limit)),
            ]),
            &candidates,
        )?
    } else {
        let query = format!(
            "MATCH (a {{id: $start}}) \
             WHERE ($project_id IS NULL OR a.project_id_normalized STARTS WITH $project_id_normalized) \
             MATCH p=(a){rel_match}(n) \
             RETURN p LIMIT $limit"
        );
        run_cypher_first(
            runtime,
            &query,
            &super::map_from_static([
                ("start", json!(start_id)),
                ("limit", json!(limit)),
                ("project_id", project_value),
            ]),
            &candidates,
        )?
    };
    if rows.is_empty() {
        let mut miss = json!({
            "db": used_db,
            "nodes": [],
            "edges": [],
            "direction": direction,
            "rel_types": rel_types,
            "max_depth": depth,
            "reason": "no_path",
        });
        if backend == Backend::Cplus {
            if let Some(object) = miss.as_object_mut() {
                object.insert(
                    "capability_diagnostics".to_string(),
                    capability_diagnostics,
                );
            }
        }
        return Ok(miss);
    }
    let paths: Vec<Value> = rows
        .iter()
        .filter_map(|row| row.get("p").cloned())
        .collect();
    let mode = normalize_content_mode(content_mode.as_deref());
    let prunes_note = backend == Backend::Cplus;
    let mut graph = paths_to_graph(&paths, &mode, include_raw_fields, prunes_note, backend);
    if let Some(object) = graph.as_object_mut() {
        object.insert("db".to_string(), json!(used_db));
        object.insert("direction".to_string(), json!(direction));
        object.insert("rel_types".to_string(), json!(rel_types));
        object.insert("max_depth".to_string(), json!(depth));
        if backend == Backend::Cplus {
            object.insert(
                "capability_diagnostics".to_string(),
                capability_diagnostics,
            );
        }
    }
    Ok(graph)
}

pub fn tool_trace_flow_between_module(
    runtime: &mut runtime::GraphRuntime,
    backend: Backend,
    payload: &Value,
) -> Result<Value, String> {
    let source_modules = super::backend_string_list(payload.get("source_modules"));
    let target_modules = super::backend_string_list(payload.get("target_modules"));
    if source_modules.is_empty() || target_modules.is_empty() {
        return Err("source_modules and target_modules must be non-empty lists.".to_string());
    }
    let parser_type = payload_str(payload, "parser_type").map(str::to_string);
    let max_depth = payload.get("max_depth").cloned().unwrap_or(json!(8));
    let direction = payload_str(payload, "direction")
        .unwrap_or("out")
        .to_lowercase();
    let limit = payload
        .get("limit")
        .and_then(Value::as_i64)
        .or_else(|| payload.get("top_k").and_then(Value::as_i64))
        .unwrap_or(10);
    let project_id = payload_string(payload, "project_id");
    let content_mode = payload_str(payload, "content_mode").map(str::to_string);
    let include_raw_fields = payload_bool(payload, "include_raw_fields");
    let db_candidates = resolve_db_candidates(project_id.as_deref())?;
    let rel_value = payload
        .get("rel_types")
        .cloned()
        .or_else(|| payload.get("relationship_types").cloned());
    let resolved = resolve_rel_types_with_diagnostics(
        runtime,
        rel_value.as_ref(),
        parser_type.as_deref(),
        &db_candidates,
        rel_value.is_some() && !payload_bool(payload, "_capability_default_relationships"),
    )?;
    let rel_types = resolved.applied;
    let capability_diagnostics = resolved.diagnostics;
    if rel_types.is_empty() {
        return Ok(unsupported_relationship_result(
            parser_type.as_deref(),
            &capability_diagnostics,
        ));
    }
    let depth = normalize_depth(Some(&max_depth), 8, 20);
    let rel_match = build_rel_match(&rel_types, depth, &direction);
    let query = format!(
        "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets \
         MATCH (s:Function)<-[:CONTAINS]-(sf:File) \
         MATCH (t:Function)<-[:CONTAINS]-(tf:File) \
         WHERE any(token IN sources WHERE \
         toLower(coalesce(s.file_path, '')) CONTAINS token OR \
         toLower(coalesce(sf.path, '')) CONTAINS token OR \
         toLower(coalesce(sf.file_path, '')) CONTAINS token) \
         AND any(token IN targets WHERE \
         toLower(coalesce(t.file_path, '')) CONTAINS token OR \
         toLower(coalesce(tf.path, '')) CONTAINS token OR \
         toLower(coalesce(tf.file_path, '')) CONTAINS token) \
         AND ($project_id IS NULL OR s.project_id_normalized STARTS WITH $project_id_normalized) \
         AND ($project_id IS NULL OR t.project_id_normalized STARTS WITH $project_id_normalized) \
         AND s.id <> t.id \
         MATCH p=(s){rel_match}(t) \
         RETURN p ORDER BY length(p) LIMIT $limit"
    );
    let params = super::map_from_static([
        ("sources", json!(source_modules)),
        ("targets", json!(target_modules)),
        ("limit", json!(limit)),
        ("project_id", project_param(&project_id)),
    ]);
    let (mut used_db, mut rows) =
        run_cypher_first(runtime, &query, &params, &db_candidates)?;
    if rows.is_empty()
        && !["both", "any", "undirected"].contains(&direction.as_str())
    {
        let rel_match = build_rel_match(&rel_types, depth, "both");
        let fallback_query = format!(
            "WITH [t IN $sources | toLower(t)] AS sources, [t IN $targets | toLower(t)] AS targets \
             MATCH (s:Function)<-[:CONTAINS]-(sf:File) \
             MATCH (t:Function)<-[:CONTAINS]-(tf:File) \
             WHERE any(token IN sources WHERE \
             toLower(coalesce(s.file_path, '')) CONTAINS token OR \
             toLower(coalesce(sf.path, '')) CONTAINS token OR \
             toLower(coalesce(sf.file_path, '')) CONTAINS token) \
             AND any(token IN targets WHERE \
             toLower(coalesce(t.file_path, '')) CONTAINS token OR \
             toLower(coalesce(tf.path, '')) CONTAINS token OR \
             toLower(coalesce(tf.file_path, '')) CONTAINS token) \
             AND ($project_id IS NULL OR s.project_id_normalized STARTS WITH $project_id_normalized) \
             AND ($project_id IS NULL OR t.project_id_normalized STARTS WITH $project_id_normalized) \
             AND s.id <> t.id \
             MATCH p=(s){rel_match}(t) \
             RETURN p ORDER BY length(p) LIMIT $limit"
        );
        let (fallback_db, fallback_rows) =
            run_cypher_first(runtime, &fallback_query, &params, &db_candidates)?;
        used_db = fallback_db;
        rows = fallback_rows;
    }
    let paths: Vec<Value> = rows
        .iter()
        .filter_map(|row| row.get("p").cloned())
        .collect();
    let mode = normalize_content_mode(content_mode.as_deref());
    let prunes_note = backend == Backend::Cplus;
    let mut graph = paths_to_graph(&paths, &mode, include_raw_fields, prunes_note, backend);
    if let Some(object) = graph.as_object_mut() {
        object.insert("db".to_string(), json!(used_db));
        object.insert("direction".to_string(), json!(direction));
        object.insert("rel_types".to_string(), json!(rel_types));
        object.insert("max_depth".to_string(), json!(depth));
        if backend == Backend::Cplus {
            object.insert(
                "capability_diagnostics".to_string(),
                capability_diagnostics,
            );
        }
    }
    Ok(graph)
}

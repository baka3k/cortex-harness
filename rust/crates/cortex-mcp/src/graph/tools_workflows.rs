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
//! `find_screen_workflows` (services/workflow_service.py +
//! tools/ts/workflow_finder.py) và `analyze_workflow_impact`
//! (unified_mcp + tools/common/workflow_impact_scorer.py).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use super::{
    resolve_direct_capability_context, resolve_db_candidates, semantic_coverage_block,
    tools_search::resolve_graph_database,
};
use crate::framework_registry::{capability_for_parser, default_relationships};
use crate::project_registry::{list_registered_projects, resolve_project_scope_candidates};
use super::runtime;

// ---------------------------------------------------------------------------
// find_screen_workflows
// ---------------------------------------------------------------------------

fn resolve_node(
    runtime: &mut runtime::GraphRuntime,
    database: &str,
    project_id: &str,
    value: &str,
) -> (Vec<Map<String, Value>>, Vec<String>) {
    let mut warnings: Vec<String> = Vec::new();
    let query = "\n    MATCH (f:Function {project_id_normalized: $pid_normalized})\n    WHERE (f.symbol_id = $value OR toLower(f.name) = toLower($value))\n      AND f.react_role = 'screen'\n    RETURN f.symbol_id AS symbol_id,\n           f.name AS name,\n           f.file_path AS file_path,\n           f.react_role AS react_role\n    LIMIT 20\n    ";
    let mut params = Map::new();
    params.insert("pid".to_string(), json!(project_id));
    params.insert("value".to_string(), json!(value));
    let candidates = runtime
        .execute_query(query, &super::prepare_params(&params), Some(database))
        .unwrap_or_default();
    if candidates.is_empty() {
        warnings.push(format!(
            "no screen node matched '{value}' in project '{project_id}'"
        ));
    } else if candidates.len() > 1 {
        warnings.push(format!(
            "'{value}' resolved to {} screen candidates; using all of them as sources/targets",
            candidates.len()
        ));
    }
    (candidates, warnings)
}

fn score_workflow(_nodes: &[Value], rels: &[Value]) -> (f64, i64, usize) {
    let mut agg_conf = 1.0f64;
    let mut total_depth = 0i64;
    for rel in rels {
        if let Some(confidence) = rel.get("confidence").and_then(|v| v.as_f64()) {
            agg_conf *= confidence;
        }
        if let Some(depth) = rel.get("call_depth").and_then(|v| v.as_i64()) {
            total_depth += depth;
        }
    }
    let rounded = format!("{agg_conf:.6}").parse::<f64>().unwrap_or(agg_conf);
    (rounded, total_depth, rels.len())
}

fn path_query_no_apoc(max_hops: i64) -> String {
    format!(
        "\n    MATCH (a:Function)\n    WHERE a.symbol_id IN $a_ids\n      AND a.project_id_normalized STARTS WITH $pid_normalized\n      AND a.react_role = 'screen'\n    MATCH (b:Function)\n    WHERE b.symbol_id IN $b_ids\n      AND b.project_id_normalized STARTS WITH $pid_normalized\n      AND b.react_role = 'screen'\n    MATCH p = (a)-[:NAVIGATE*1..{max_hops}]->(b)\n    WHERE ALL(n IN nodes(p) WHERE n.react_role = 'screen'\n                               AND n.project_id_normalized STARTS WITH $pid_normalized)\n      AND NONE(\n            x IN nodes(p)\n            WHERE size([y IN nodes(p) WHERE y.symbol_id = x.symbol_id]) > 1\n          )\n    RETURN [n IN nodes(p) | {{\n              symbol_id: n.symbol_id,\n              name:      n.name,\n              file_path: n.file_path,\n              react_role: n.react_role\n           }}] AS nodes,\n           [r IN relationships(p) | {{\n              method:       r.method,\n              target:       r.target,\n              via:          r.via,\n              trigger_type: r.trigger_type,\n              guard:        r.guard,\n              call_depth:   coalesce(r.call_depth, 0),\n              confidence:   coalesce(r.confidence, 1.0)\n           }}] AS rels,\n           length(p) AS length\n    LIMIT $limit\n    "
    )
}

fn collect_open_ended(
    runtime: &mut runtime::GraphRuntime,
    database: &str,
    query_anchor: &str,
    anchor_ids: &[String],
    project_id: &str,
    max_hops: i64,
    max_paths: i64,
    sink: &mut Vec<Value>,
    tag: &str,
) {
    let anchor_clause = if query_anchor == "source" {
        "a.symbol_id IN $ids"
    } else {
        "b.symbol_id IN $ids"
    };
    let query = format!(
        "\n    MATCH (a:Function), (b:Function)\n    WHERE {anchor_clause}\n      AND a.project_id_normalized STARTS WITH $pid_normalized AND b.project_id_normalized STARTS WITH $pid_normalized\n      AND a.react_role = 'screen' AND b.react_role = 'screen'\n    MATCH p = (a)-[:NAVIGATE*1..{max_hops}]->(b)\n    WHERE ALL(n IN nodes(p) WHERE n.react_role = 'screen'\n                               AND n.project_id_normalized STARTS WITH $pid_normalized)\n      AND NONE(\n            x IN nodes(p)\n            WHERE size([y IN nodes(p) WHERE y.symbol_id = x.symbol_id]) > 1\n          )\n    RETURN [n IN nodes(p) | {{\n              symbol_id: n.symbol_id,\n              name:      n.name,\n              file_path: n.file_path,\n              react_role: n.react_role\n           }}] AS nodes,\n           [r IN relationships(p) | {{\n              method:       r.method,\n              target:       r.target,\n              via:          r.via,\n              trigger_type: r.trigger_type,\n              guard:        r.guard,\n              call_depth:   coalesce(r.call_depth, 0),\n              confidence:   coalesce(r.confidence, 1.0)\n           }}] AS rels,\n           length(p) AS length\n    LIMIT $limit\n    "
    );
    let mut params = Map::new();
    params.insert("ids".to_string(), json!(anchor_ids));
    params.insert("pid".to_string(), json!(project_id));
    params.insert("limit".to_string(), json!(max_paths * 3));
    let rows = runtime
        .execute_query(&query, &super::prepare_params(&params), Some(database))
        .unwrap_or_default();
    for row in rows {
        let nodes = row.get("nodes").and_then(Value::as_array).cloned().unwrap_or_default();
        let rels = row.get("rels").and_then(Value::as_array).cloned().unwrap_or_default();
        if nodes.is_empty() || rels.is_empty() {
            continue;
        }
        let (aggregate_confidence, total_call_depth, length) = score_workflow(&nodes, &rels);
        sink.push(json!({
            "path": nodes,
            "edges": rels,
            "direction": tag,
            "aggregate_confidence": aggregate_confidence,
            "total_call_depth": total_call_depth,
            "length": length,
        }));
    }
}

fn find_screen_workflows_core(
    runtime: &mut runtime::GraphRuntime,
    database: &str,
    project_id: &str,
    node_a: &str,
    node_b: Option<&str>,
    direction: &str,
    max_hops: i64,
    max_paths: i64,
    include_entry_function: bool,
    include_api_calls: bool,
) -> Result<Value, String> {
    if project_id.is_empty() {
        return Err("project_id is required".to_string());
    }
    if node_a.is_empty() {
        return Err("node_a is required (bare name or symbol_id)".to_string());
    }
    let max_hops = max_hops.clamp(1, 20);
    let max_paths = max_paths.clamp(1, 1000);
    let direction = direction.to_lowercase();
    let mode = if node_b.is_some() { "pair" } else { "single" };
    if node_b.is_none() && !["inbound", "outbound", "bidirectional"].contains(&direction.as_str())
    {
        return Err("direction must be one of: inbound, outbound, bidirectional".to_string());
    }
    let mut uncertainties: Vec<String> = Vec::new();
    let (cands_a, w_a) = resolve_node(runtime, database, project_id, node_a);
    uncertainties.extend(w_a);
    let cands_b: Vec<Map<String, Value>> = match node_b {
        Some(value) if !value.is_empty() => {
            let (cands, w_b) = resolve_node(runtime, database, project_id, value);
            uncertainties.extend(w_b);
            cands
        }
        _ => Vec::new(),
    };
    let resolved = json!({
        "node_a": {"input": node_a, "candidates": cands_a},
        "node_b": node_b.map(|value| json!({"input": value, "candidates": cands_b})).unwrap_or(Value::Null),
    });
    let a_ids: Vec<String> = cands_a
        .iter()
        .filter_map(|row| row.get("symbol_id").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    let b_ids: Vec<String> = cands_b
        .iter()
        .filter_map(|row| row.get("symbol_id").and_then(Value::as_str))
        .map(str::to_string)
        .collect();
    if a_ids.is_empty() || (node_b.is_some() && b_ids.is_empty()) {
        return Ok(json!({
            "mode": mode,
            "direction": if mode == "pair" { json!("pair") } else { json!(direction) },
            "project_id": project_id,
            "resolved": resolved,
            "workflows": [],
            "uncertainties": uncertainties,
            "truncated": false,
        }));
    }
    let mut workflows: Vec<Value> = Vec::new();
    let query = path_query_no_apoc(max_hops);
    if mode == "pair" {
        let mut params = Map::new();
        params.insert("a_ids".to_string(), json!(a_ids));
        params.insert("b_ids".to_string(), json!(b_ids));
        params.insert("pid".to_string(), json!(project_id));
        params.insert("limit".to_string(), json!(max_paths * 3));
        let rows = runtime
            .execute_query(&query, &super::prepare_params(&params), Some(database))
            .unwrap_or_default();
        for row in rows {
            let nodes = row.get("nodes").and_then(Value::as_array).cloned().unwrap_or_default();
            let rels = row.get("rels").and_then(Value::as_array).cloned().unwrap_or_default();
            if nodes.is_empty() || rels.is_empty() {
                continue;
            }
            let (aggregate_confidence, total_call_depth, length) = score_workflow(&nodes, &rels);
            workflows.push(json!({
                "path": nodes,
                "edges": rels,
                "direction": "pair",
                "aggregate_confidence": aggregate_confidence,
                "total_call_depth": total_call_depth,
                "length": length,
            }));
        }
    } else {
        if ["outbound", "bidirectional"].contains(&direction.as_str()) {
            collect_open_ended(
                runtime,
                database,
                "source",
                &a_ids,
                project_id,
                max_hops,
                max_paths,
                &mut workflows,
                "outbound",
            );
        }
        if ["inbound", "bidirectional"].contains(&direction.as_str()) {
            collect_open_ended(
                runtime,
                database,
                "target",
                &a_ids,
                project_id,
                max_hops,
                max_paths,
                &mut workflows,
                "inbound",
            );
        }
    }

    // Dedupe + rank.
    let mut best: BTreeMap<String, Value> = BTreeMap::new();
    let mut key_order: Vec<String> = Vec::new();
    for workflow in workflows {
        let key: String = workflow
            .get("path")
            .and_then(Value::as_array)
            .map(|nodes| {
                nodes
                    .iter()
                    .filter_map(|node| node.get("symbol_id").and_then(Value::as_str))
                    .collect::<Vec<_>>()
                    .join("\u{1}")
            })
            .unwrap_or_default();
        let replace = match best.get(&key) {
            None => true,
            Some(previous) => {
                let new_confidence = workflow
                    .get("aggregate_confidence")
                    .and_then(Value::as_f64)
                    .unwrap_or(0.0);
                let previous_confidence = previous
                    .get("aggregate_confidence")
                    .and_then(Value::as_f64)
                    .unwrap_or(0.0);
                new_confidence > previous_confidence
            }
        };
        if replace {
            if !key_order.contains(&key) {
                key_order.push(key.clone());
            }
            best.insert(key, workflow);
        }
    }
    let mut ranked: Vec<Value> = key_order
        .iter()
        .filter_map(|key| best.remove(key))
        .collect();
    ranked.sort_by(|left, right| {
        let left_conf = left.get("aggregate_confidence").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let right_conf = right.get("aggregate_confidence").and_then(|v| v.as_f64()).unwrap_or(0.0);
        right_conf
            .partial_cmp(&left_conf)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                let left_depth = left.get("total_call_depth").and_then(|v| v.as_i64()).unwrap_or(0);
                let right_depth = right.get("total_call_depth").and_then(|v| v.as_i64()).unwrap_or(0);
                left_depth.cmp(&right_depth)
            })
            .then_with(|| {
                let left_len = left.get("length").and_then(|v| v.as_i64()).unwrap_or(0);
                let right_len = right.get("length").and_then(|v| v.as_i64()).unwrap_or(0);
                left_len.cmp(&right_len)
            })
    });
    let truncated = ranked.len() as i64 > max_paths;
    ranked.truncate(max_paths as usize);
    if include_entry_function || include_api_calls {
        for workflow in &mut ranked {
            if let Some(object) = workflow.as_object_mut() {
                object.entry("entry_function".to_string()).or_insert(Value::Null);
                object.entry("api_calls".to_string()).or_insert(json!([]));
            }
        }
    }
    Ok(json!({
        "mode": mode,
        "direction": if mode == "pair" { json!("pair") } else { json!(direction) },
        "project_id": project_id,
        "resolved": resolved,
        "workflows": ranked,
        "uncertainties": uncertainties,
        "truncated": truncated,
    }))
}

fn merge_screen_workflow_results(
    results: &BTreeMap<String, Value>,
    node_a: &str,
    node_b: Option<&str>,
) -> Result<Value, String> {
    let ok_projects: Vec<String> = results
        .iter()
        .filter(|(_, result)| !matches!(result.get("ok"), Some(Value::Bool(false))))
        .map(|(project, _)| project.clone())
        .collect();
    let failed: BTreeMap<String, Value> = results
        .iter()
        .filter(|(_, result)| matches!(result.get("ok"), Some(Value::Bool(false))))
        .map(|(project, result)| (project.clone(), result.clone()))
        .collect();
    if ok_projects.is_empty() {
        let first_error = failed
            .values()
            .next()
            .and_then(|result| result.get("error"))
            .map(value_string)
            .unwrap_or_default();
        return Err(format!(
            "project_id is omitted and no registered project produced a result.{} \
             Register a project or pass project_id explicitly.",
            if first_error.is_empty() {
                String::new()
            } else {
                format!(" First error: {first_error}")
            }
        ));
    }
    let first = results.get(&ok_projects[0]).cloned().unwrap_or(Value::Null);
    let mut merged = Map::new();
    merged.insert("ok".to_string(), json!(true));
    merged.insert("project_id".to_string(), json!(""));
    merged.insert("projects_searched".to_string(), json!(ok_projects));
    if !failed.is_empty() {
        merged.insert(
            "projects_failed".to_string(),
            json!(failed.keys().cloned().collect::<Vec<_>>()),
        );
    }
    merged.insert(
        "mode".to_string(),
        first.get("mode").cloned().unwrap_or(json!("single")),
    );
    merged.insert(
        "direction".to_string(),
        first.get("direction").cloned().unwrap_or(json!("bidirectional")),
    );
    let mut workflows: Vec<Value> = Vec::new();
    let mut uncertainties: Vec<Value> = Vec::new();
    let mut resolved_node_a: Vec<Value> = Vec::new();
    let mut resolved_node_b: Vec<Value> = Vec::new();
    for project in &ok_projects {
        let result = results.get(project).cloned().unwrap_or(Value::Null);
        for workflow in result
            .get("workflows")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            if let Some(object) = workflow.as_object() {
                let mut tagged = object.clone();
                tagged.entry("project_id".to_string()).or_insert(json!(project));
                workflows.push(Value::Object(tagged));
            } else {
                workflows.push(workflow);
            }
        }
        for uncertainty in result
            .get("uncertainties")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            uncertainties.push(uncertainty);
        }
        let resolved = result.get("resolved").cloned().unwrap_or(Value::Null);
        let node_a_candidates = resolved
            .get("node_a")
            .and_then(|node| node.get("candidates"))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for candidate in node_a_candidates {
            if let Some(object) = candidate.as_object() {
                let mut tagged = object.clone();
                tagged.entry("project_id".to_string()).or_insert(json!(project));
                resolved_node_a.push(Value::Object(tagged));
            }
        }
        if let Some(_node_b_value) = node_b {
            let node_b_candidates = resolved
                .get("node_b")
                .and_then(|node| node.get("candidates"))
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            for candidate in node_b_candidates {
                if let Some(object) = candidate.as_object() {
                    let mut tagged = object.clone();
                    tagged.entry("project_id".to_string()).or_insert(json!(project));
                    resolved_node_b.push(Value::Object(tagged));
                }
            }
        }
    }
    merged.insert(
        "resolved".to_string(),
        json!({
            "node_a": {"input": node_a, "candidates": resolved_node_a},
            "node_b": node_b.map(|value| json!({"input": value, "candidates": resolved_node_b})).unwrap_or(Value::Null),
        }),
    );
    merged.insert("workflows".to_string(), Value::Array(workflows));
    merged.insert("uncertainties".to_string(), Value::Array(uncertainties));
    merged.insert(
        "truncated".to_string(),
        json!(ok_projects.iter().any(|project| {
            results
                .get(project)
                .and_then(|result| result.get("truncated"))
                .and_then(Value::as_bool)
                .unwrap_or(false)
        })),
    );
    Ok(Value::Object(merged))
}

/// `tool_find_screen_workflows` (cplus body + workflow_service fanout).
pub fn tool_find_screen_workflows(
    runtime: &mut runtime::GraphRuntime,
    payload: Value,
) -> Result<Value, String> {
    let parser_type = payload
        .get("parser_type")
        .and_then(Value::as_str)
        .map(str::to_string);
    let rel_value = payload
        .get("relationship_types")
        .cloned()
        .or_else(|| payload.get("rel_types").cloned());
    let db_for_candidates = payload
        .get("db")
        .and_then(Value::as_str)
        .map(str::to_string);
    let db_candidates = resolve_db_candidates(db_for_candidates.as_deref())?;
    let resolved = super::resolve_rel_types_with_diagnostics(
        runtime,
        rel_value.as_ref(),
        parser_type.as_deref(),
        &db_candidates,
        rel_value.is_some() && !payload.as_object().map(|object| object.contains_key("_capability_default_relationships")).unwrap_or(false),
    )?;
    let relationship_types = resolved.applied;
    let capability_diagnostics = resolved.diagnostics;
    if relationship_types.is_empty() {
        return Ok(super::unsupported_relationship_result(
            parser_type.as_deref(),
            &capability_diagnostics,
        ));
    }
    let project_id = payload
        .get("project_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let node_a = payload
        .get("node_a")
        .and_then(Value::as_str)
        .or_else(|| payload.get("source").and_then(Value::as_str))
        .unwrap_or("")
        .trim()
        .to_string();
    let node_b_raw = payload
        .get("node_b")
        .or_else(|| payload.get("target"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty());
    let direction = payload
        .get("direction")
        .and_then(Value::as_str)
        .unwrap_or("bidirectional")
        .trim()
        .to_lowercase();
    let explicit_database = payload
        .get("db")
        .or_else(|| payload.get("database"))
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    let max_hops = payload.get("max_hops").and_then(|v| v.as_i64()).unwrap_or(8);
    let max_paths = payload.get("max_paths").and_then(|v| v.as_i64()).unwrap_or(100);
    let include_entry_function = payload
        .get("include_entry_function")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let include_api_calls = payload
        .get("include_api_calls")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    fn resolve_database_for_project(project_id: &str, explicit: &str) -> String {
        if !explicit.is_empty() {
            return explicit.to_string();
        }
        resolve_graph_database(Some(project_id)).unwrap_or_else(|| {
            std::env::var("FALKORDB_GRAPH").unwrap_or_else(|_| "hyper_graph".to_string())
        })
    }

    if !project_id.is_empty() {
        let matched = resolve_project_scope_candidates(Some(&project_id), None)
            .map_err(|error| error.to_string())?;
        if matched.len() <= 1 {
            let project = matched
                .first()
                .map(|targets| targets.project_id.clone())
                .unwrap_or_else(|| project_id.clone());
            let database = resolve_database_for_project(&project_id, &explicit_database);
            let mut result = find_screen_workflows_core(
                runtime,
                &database,
                &project,
                &node_a,
                node_b_raw,
                &direction,
                max_hops,
                max_paths,
                include_entry_function,
                include_api_calls,
            )?;
            if let Some(object) = result.as_object_mut() {
                object.insert(
                    "capability_diagnostics".to_string(),
                    capability_diagnostics,
                );
            }
            return Ok(result);
        }
        let projects: Vec<String> = matched
            .iter()
            .map(|targets| targets.project_id.clone())
            .collect();
        let mut results: BTreeMap<String, Value> = BTreeMap::new();
        for project in projects {
            let database = resolve_database_for_project(&project, &explicit_database);
            let result = find_screen_workflows_core(
                runtime,
                &database,
                &project,
                &node_a,
                node_b_raw,
                &direction,
                max_hops,
                max_paths,
                include_entry_function,
                include_api_calls,
            )
            .unwrap_or_else(|error| json!({"ok": false, "error": error}));
            results.insert(project, result);
        }
        let mut merged = merge_screen_workflow_results(&results, &node_a, node_b_raw)?;
        if let Some(object) = merged.as_object_mut() {
            object.insert(
                "capability_diagnostics".to_string(),
                capability_diagnostics,
            );
        }
        return Ok(merged);
    }
    let projects = list_registered_projects(None).map_err(|error| error.to_string())?;
    if projects.is_empty() {
        return Err(
            "No registered project matches the query, so there is nothing to search. \
             Register a project or pass project_id explicitly."
                .to_string(),
        );
    }
    let mut results: BTreeMap<String, Value> = BTreeMap::new();
    for project in projects {
        let database = resolve_database_for_project(&project, &explicit_database);
        let result = find_screen_workflows_core(
            runtime,
            &database,
            &project,
            &node_a,
            node_b_raw,
            &direction,
            max_hops,
            max_paths,
            include_entry_function,
            include_api_calls,
        )
        .unwrap_or_else(|error| json!({"ok": false, "error": error}));
        results.insert(project, result);
    }
    let mut merged = merge_screen_workflow_results(&results, &node_a, node_b_raw)?;
    if let Some(object) = merged.as_object_mut() {
        object.insert(
            "capability_diagnostics".to_string(),
            capability_diagnostics,
        );
    }
    Ok(merged)
}

fn value_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

// ---------------------------------------------------------------------------
// analyze_workflow_impact
// ---------------------------------------------------------------------------

const EXTERNAL_MARKERS: [&str; 5] = [
    "third_party", "external", "vendor", "/usr", "node_modules",
];
const PAYMENT_AUTH: [&str; 4] = ["payment", "auth", "authentication", "authorization"];
const ORDER_LOYALTY: [&str; 3] = ["order", "loyalty", "checkout"];

fn severity_from_domain(domain: &str, step_index: i64, call_depth: i64) -> &'static str {
    let domain = domain.to_lowercase();
    if PAYMENT_AUTH.contains(&domain.as_str()) {
        if (0..=2).contains(&step_index) {
            return "critical";
        }
        if call_depth == 0 {
            return "high";
        }
        return "medium";
    }
    if ORDER_LOYALTY.contains(&domain.as_str()) {
        return "medium";
    }
    "low"
}

fn compute_workflow_risk(
    direct: &[Value],
    indirect: &[Value],
    shared_screen_conflict: bool,
) -> f64 {
    let mut score = 0.1 * direct.len() as f64;
    for workflow in direct {
        let domain = value_string(workflow.get("domain").unwrap_or(&Value::Null)).to_lowercase();
        if PAYMENT_AUTH.contains(&domain.as_str()) {
            score += 0.3;
        } else if ORDER_LOYALTY.contains(&domain.as_str()) {
            score += 0.2;
        } else {
            score += 0.1;
        }
        if workflow.get("step_index").and_then(|v| v.as_i64()) == Some(0) {
            score += 0.15;
        }
    }
    for workflow in indirect {
        let domain = value_string(workflow.get("domain").unwrap_or(&Value::Null)).to_lowercase();
        if PAYMENT_AUTH.contains(&domain.as_str()) {
            score += 0.1;
        } else if ORDER_LOYALTY.contains(&domain.as_str()) {
            score += 0.05;
        } else {
            score += 0.02;
        }
    }
    if shared_screen_conflict {
        score += 0.2;
    }
    format!("{score:.3}").parse::<f64>().unwrap_or(score).min(1.0)
}

fn generate_recommendation(
    direct: &[Value],
    indirect: &[Value],
    cascade: &[Value],
    navigators: &[Value],
    shared_screen_conflict: bool,
    outcome: &str,
    evidence_class_note: &str,
) -> String {
    let mut parts: Vec<String> = Vec::new();
    let critical: Vec<&Value> = direct
        .iter()
        .filter(|item| item.get("severity").and_then(Value::as_str) == Some("critical"))
        .collect();
    let high: Vec<&Value> = direct
        .iter()
        .filter(|item| item.get("severity").and_then(Value::as_str) == Some("high"))
        .collect();
    if !critical.is_empty() {
        let names = critical
            .iter()
            .filter_map(|item| item.get("workflow_name").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join(", ");
        let first = critical[0];
        let step_index = first.get("step_index").and_then(|v| v.as_i64()).unwrap_or(-1);
        let step_desc = if step_index == 0 {
            format!(" at step {} (entrypoint)", step_index + 1)
        } else {
            format!(" at step {}", step_index + 1)
        };
        parts.push(format!("CRITICAL: Changes affect {names}{step_desc}."));
    } else if !high.is_empty() {
        let names = high
            .iter()
            .filter_map(|item| item.get("workflow_name").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(format!("HIGH: Changes directly affect {names}."));
    } else if !direct.is_empty() {
        let names = direct
            .iter()
            .filter_map(|item| item.get("workflow_name").and_then(Value::as_str))
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(format!("Changes directly affect: {names}."));
    }
    for workflow in indirect.iter().take(3) {
        let name = workflow.get("workflow_name").map(|v| value_string(&v)).unwrap_or_default();
        let depth = workflow.get("call_depth").and_then(|v| v.as_i64()).unwrap_or(0);
        parts.push(format!("Also impacts {name} indirectly (depth={depth})."));
    }
    if shared_screen_conflict {
        let cascade_count = cascade.len();
        let mut names_set: BTreeSet<String> = BTreeSet::new();
        for workflow in cascade {
            names_set.insert(workflow.get("workflow_name").map(|v| value_string(&v)).unwrap_or_default());
        }
        let cascade_names = {
            let names: Vec<String> = names_set.into_iter().collect();
            names.join(", ")
        };
        parts.push(format!(
            "Shared screen/component used across {cascade_count} other workflow(s) — cascade risk HIGH.{}",
            if cascade_names.is_empty() {
                String::new()
            } else {
                format!(" Also affects: {cascade_names}.")
            }
        ));
    }
    if !navigators.is_empty() {
        let routes = navigators
            .iter()
            .take(3)
            .filter_map(|item| item.get("route").map(|v| value_string(&v)))
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(format!(
            "Navigator routes affected: {routes}. Verify routing config."
        ));
    }
    let mut all_domains: BTreeSet<String> = BTreeSet::new();
    for workflow in direct.iter().chain(indirect).chain(cascade.iter()) {
        all_domains.insert(value_string(workflow.get("domain").unwrap_or(&Value::Null)).to_lowercase());
    }
    let needs_e2e = all_domains
        .intersection(&BTreeSet::from(PAYMENT_AUTH.map(str::to_string)))
        .count()
        > 0;
    if needs_e2e {
        let flow_names: Vec<String> = all_domains
            .intersection(&BTreeSet::from(PAYMENT_AUTH.map(str::to_string)))
            .cloned()
            .collect();
        parts.push(format!(
            "Recommended: run e2e tests for {} flows before merge.",
            flow_names.join(" + ")
        ));
    } else if !direct.is_empty() || !indirect.is_empty() || !cascade.is_empty() {
        parts.push(
            "Recommended: run integration tests for affected workflows before merge.".to_string(),
        );
    }
    if parts.is_empty() {
        if outcome == "incomplete" {
            return "No workflow impact detected, but the semantic frontier of this \
                    traversal is incomplete — this is not an authoritative negative \
                    result. Extend semantic coverage before relying on it."
                .to_string();
        }
        return "No workflow impact detected. Proceed with standard unit tests.".to_string();
    }
    if outcome == "incomplete" {
        parts.push(
            "Note: semantic coverage of the visited frontier is incomplete; \
             negative conclusions above are not authoritative."
                .to_string(),
        );
    }
    if !evidence_class_note.is_empty() {
        parts.push(evidence_class_note.to_string());
    }
    parts.join(" ")
}

/// WorkflowImpactScorer.score — Cypher lanes + risk + recommendation.
fn workflow_impact_score(
    runtime: &mut runtime::GraphRuntime,
    function_id: &str,
    database: Option<&str>,
    flow_relationships: &[String],
    max_depth: i64,
) -> Result<Value, String> {
    let workflow_relationship = "HAS_STEP";
    let capped_depth = max_depth.min(4);
    let dbs: Vec<String> = database.map(str::to_string).into_iter().collect();
    let mut directly: Vec<Value> = Vec::new();
    let mut indirectly: Vec<Value> = Vec::new();
    let mut cascade: Vec<Value> = Vec::new();
    let mut navigators: Vec<Value> = Vec::new();
    let mut shared_screen_conflict = false;

    let direct_query = format!(
        "\n            MATCH (w:Workflow)-[s:{workflow_relationship}]->(f:Function)\n            WHERE f.symbol_id = $id OR f.file_path = $id\n            RETURN w.workflow_id                 AS workflow_id,\n                   w.name                        AS workflow_name,\n                   w.domain                      AS domain,\n                   coalesce(w.confidence, 0.5)   AS confidence,\n                   coalesce(s.order, -1)          AS step_index\n            "
    );
    let direct_rows = query_runtime(runtime, &direct_query, function_id, &dbs)?;
    for row in &direct_rows {
        let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
        let domain = value_string(&get("domain")).to_lowercase();
        let step_index = get("step_index").as_i64().unwrap_or(-1);
        let severity = severity_from_domain(&domain, step_index, 0);
        let workflow_name_value = value_string(&get("workflow_name"));
        directly.push(json!({
            "workflow_id": value_string(&get("workflow_id")),
            "workflow_name": workflow_name_value.clone(),
            "domain": domain,
            "confidence": get("confidence").as_f64().unwrap_or(0.5),
            "impact_type": "direct",
            "step_index": step_index,
            "call_depth": 0,
            "severity": severity,
            "reason": format!(
                "Function is a direct step (index {step_index}) in workflow '{}'.",
                workflow_name_value
            ),
        }));
    }
    let direct_ids: Vec<String> = directly
        .iter()
        .filter_map(|item| item.get("workflow_id").map(|v| value_string(&v)))
        .collect();

    let flow_rel_token = flow_relationships.join("|");
    let indirect_query = format!(
        "\n            MATCH (w:Workflow)-[:{workflow_relationship}]->(entry:Function)\n            MATCH path = (entry)-[:{flow_rel_token}*1..{capped_depth}]->(f:Function)\n            WHERE (f.symbol_id = $id OR f.file_path = $id)\n              AND NOT w.workflow_id IN $direct_ids\n            RETURN DISTINCT\n                   w.workflow_id                 AS workflow_id,\n                   w.name                        AS workflow_name,\n                   w.domain                      AS domain,\n                   coalesce(w.confidence, 0.5)   AS confidence,\n                   length(path)                  AS call_depth\n            ORDER BY call_depth ASC, confidence DESC\n            LIMIT 20\n            "
    );
    let mut indirect_params = Map::new();
    indirect_params.insert("id".to_string(), json!(function_id));
    indirect_params.insert("direct_ids".to_string(), json!(direct_ids));
    let indirect_rows = query_runtime_params(runtime, &indirect_query, &indirect_params, &dbs)?;
    for row in &indirect_rows {
        let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
        let domain = value_string(&get("domain")).to_lowercase();
        let call_depth = get("call_depth").as_i64().unwrap_or(1);
        let severity = severity_from_domain(&domain, -1, call_depth);
        let workflow_name_value = value_string(&get("workflow_name"));
        indirectly.push(json!({
            "workflow_id": value_string(&get("workflow_id")),
            "workflow_name": workflow_name_value.clone(),
            "domain": domain,
            "confidence": get("confidence").as_f64().unwrap_or(0.5),
            "impact_type": "indirect",
            "step_index": -1,
            "call_depth": call_depth,
            "severity": severity,
            "reason": format!(
                "Function reachable via CALLS chain (depth={call_depth}) from workflow '{}'.",
                workflow_name_value
            ),
        }));
    }

    let nav_query = format!(
        "\n            MATCH (nav:Navigator)-[r:HAS_ROUTE]->(f:Function)\n            WHERE f.symbol_id = $id OR f.file_path = $id\n            RETURN coalesce(nav.id, nav.var_name)    AS navigator_id,\n                   nav.var_name                      AS var_name,\n                   coalesce(nav.nav_type, 'stack')   AS nav_type,\n                   coalesce(r.name, f.name)           AS route_name\n            "
    );
    let nav_rows = query_runtime(runtime, &nav_query, function_id, &dbs)?;
    for row in &nav_rows {
        let get = |key: &str| row.get(key).cloned().unwrap_or(Value::Null);
        let nav_type = value_string(&get("nav_type"));
        let nav_type = if nav_type.is_empty() { "stack".to_string() } else { nav_type };
        navigators.push(json!({
            "navigator_id": value_string(&get("navigator_id")),
            "var_name": value_string(&get("var_name")),
            "nav_type": nav_type,
            "affected_route": value_string(&get("route_name")),
            "impact_type": "component_changed",
        }));
    }

    let cascade_query = format!(
        "\n            MATCH (w1:Workflow)-[:{workflow_relationship}]->(s:Function)<-[:{workflow_relationship}]-(w2:Workflow)\n            WHERE (s.symbol_id = $id OR s.file_path = $id)\n              AND w1.workflow_id < w2.workflow_id\n            RETURN DISTINCT\n                   w1.workflow_id AS wf1_id, w1.name AS wf1_name, w1.domain AS wf1_domain,\n                   w2.workflow_id AS wf2_id, w2.name AS wf2_name, w2.domain AS wf2_domain,\n                   s.name         AS shared_screen\n            LIMIT 10\n            "
    );
    let cascade_rows = query_runtime(runtime, &cascade_query, function_id, &dbs)?;
    if !cascade_rows.is_empty() {
        shared_screen_conflict = true;
        let mut seen_wf_ids: BTreeSet<String> = directly
            .iter()
            .filter_map(|item| item.get("workflow_id").map(|v| value_string(&v)))
            .collect();
        seen_wf_ids.extend(
            indirectly
                .iter()
                .filter_map(|item| item.get("workflow_id").map(|v| value_string(&v))),
        );
        for row in &cascade_rows {
            for (wf_id, wf_name, wf_domain) in [
                (
                    row.get("wf1_id").map(|v| value_string(&v)),
                    row.get("wf1_name").map(|v| value_string(&v)),
                    row.get("wf1_domain").map(|v| value_string(&v)),
                ),
                (
                    row.get("wf2_id").map(|v| value_string(&v)),
                    row.get("wf2_name").map(|v| value_string(&v)),
                    row.get("wf2_domain").map(|v| value_string(&v)),
                ),
            ] {
                if let Some(wf_id) = wf_id
                    && !wf_id.is_empty()
                    && !seen_wf_ids.contains(&wf_id)
                {
                    let domain = wf_domain.clone().unwrap_or_default().to_lowercase();
                    cascade.push(json!({
                        "workflow_id": wf_id,
                        "workflow_name": wf_name.clone().unwrap_or_default(),
                        "domain": domain,
                        "confidence": 0.5,
                        "impact_type": "cascade",
                        "step_index": -1,
                        "call_depth": 0,
                        "severity": severity_from_domain(&domain, -1, 0),
                        "reason": format!(
                            "Shared screen '{}' is also a step in this workflow.",
                            row.get("shared_screen").map(|v| value_string(&v)).unwrap_or_default()
                        ),
                    }));
                    seen_wf_ids.insert(wf_id);
                }
            }
        }
    }

    // Semantic coverage rows.
    let coverage_rows = runtime
        .execute_query(
            "MATCH (coverage:SemanticCoverage) RETURN coverage.status AS status, coverage.tu_key AS tu_key, coverage.detail AS detail",
            &BTreeMap::new(),
            database,
        )
        .unwrap_or_default();
    let semantic_coverage = semantic_coverage_block(runtime, &dbs, None);
    let _ = coverage_rows;
    let weak_flow_rels: Vec<String> = flow_relationships
        .iter()
        .filter(|rel| rel.as_str() != "CALLS")
        .cloned()
        .collect();
    let mut evidence_class_note = String::new();
    if !weak_flow_rels.is_empty() {
        evidence_class_note = format!(
            "Indirect reachability includes weak evidence relationships ({}); those paths are conservative evidence, not confirmed direct calls.",
            weak_flow_rels.join(", ")
        );
        for impact in &mut indirectly {
            if let Some(object) = impact.as_object_mut() {
                let confidence = object
                    .get("confidence")
                    .and_then(Value::as_f64)
                    .unwrap_or(0.5);
                object.insert(
                    "confidence".to_string(),
                    json!(format!("{:.3}", confidence * 0.7).parse::<f64>().unwrap_or(confidence * 0.7)),
                );
                let reason = object.get("reason").and_then(Value::as_str).unwrap_or_default();
                object.insert(
                    "reason".to_string(),
                    json!(format!("{reason} (conservative evidence class)")),
                );
            }
        }
    }

    let workflow_risk_score =
        compute_workflow_risk(&directly, &indirectly, shared_screen_conflict);
    let outcome = if directly.is_empty()
        && indirectly.is_empty()
        && cascade.is_empty()
        && navigators.is_empty()
    {
        let status = semantic_coverage
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        if status == "complete" {
            "complete"
        } else {
            "incomplete"
        }
    } else {
        "complete"
    };
    let _ = &outcome; // Python scorer không expose outcome/semantic_coverage
    let _ = &semantic_coverage;
    let recommendation = generate_recommendation(
        &directly,
        &indirectly,
        &cascade,
        &navigators,
        shared_screen_conflict,
        &outcome,
        &evidence_class_note,
    );
    Ok(json!({
        "directly_affected_workflows": directly,
        "indirectly_affected_workflows": indirectly,
        "cascade_workflows": cascade,
        "navigator_impacts": navigators,
        "shared_screen_conflict": shared_screen_conflict,
        "workflow_risk_score": workflow_risk_score,
        "recommendation": recommendation,
    }))
}

fn query_runtime(
    runtime: &mut runtime::GraphRuntime,
    cypher: &str,
    function_id: &str,
    dbs: &[String],
) -> Result<Vec<Map<String, Value>>, String> {
    let mut params = Map::new();
    params.insert("id".to_string(), json!(function_id));
    query_runtime_params(runtime, cypher, &params, dbs)
}

fn query_runtime_params(
    runtime: &mut runtime::GraphRuntime,
    cypher: &str,
    params: &Map<String, Value>,
    dbs: &[String],
) -> Result<Vec<Map<String, Value>>, String> {
    let mut last_error: Option<String> = None;
    for db in dbs {
        match runtime.execute_query(cypher, &super::prepare_params(params), Some(db)) {
            Ok(rows) => return Ok(rows),
            Err(error) => last_error = Some(error),
        }
    }
    if dbs.is_empty() {
        runtime
            .execute_query(cypher, &super::prepare_params(params), None)
            .map_err(|error| {
                last_error.unwrap_or(error)
            })
    } else {
        Err(last_error.unwrap_or_else(|| "no database".to_string()))
    }
}

/// `tool_analyze_workflow_impact`.
pub fn tool_analyze_workflow_impact(
    runtime: &mut runtime::GraphRuntime,
    arguments: Value,
) -> Result<Value, String> {
    let function_id = arguments
        .get("function_id")
        .map(value_string)
        .unwrap_or_default();
    let project_id = arguments
        .get("project_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let direction = arguments
        .get("direction")
        .and_then(Value::as_str)
        .unwrap_or("downstream")
        .to_string();
    let max_depth = arguments.get("max_depth").and_then(|v| v.as_i64()).unwrap_or(4);
    let parser_type = arguments
        .get("parser_type")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let database = resolve_graph_database(if project_id.is_empty() {
        None
    } else {
        Some(project_id.as_str())
    });
    let capped = max_depth.min(4);

    // 1. Call-graph expansion qua query_subgraph — gọi TRỰC TIẾP với runtime
    // đang giữ (dispatch_graph_tool sẽ with_graph_runtime lần 2 → std Mutex
    // non-reentrant ⇒ deadlock; sample đã xác nhận).
    let subgraph_payload = json!({
        "function_id": function_id,
        "db": database.clone().unwrap_or_default(),
        "direction": direction,
        "max_depth": capped,
        "parser_type": if parser_type.is_empty() { Value::Null } else { json!(parser_type) },
    });
    let backend = super::resolve_backend_for_parser(if parser_type.is_empty() {
        None
    } else {
        Some(parser_type.as_str())
    });
    let subgraph = match super::tools_traversal::tool_query_subgraph(runtime, backend, &subgraph_payload)
    {
        Ok(value) => value,
        Err(error) => json!({
            "error": {"type": "tool_execution_error", "message": error}
        }),
    };

    if let Some(error) = subgraph.get("error")
        && let Some(error_object) = error.as_object()
        && error_object.get("type").and_then(Value::as_str) == Some("unsupported_capability")
    {
        return Ok(subgraph);
    }
    let nodes: Vec<Value> = subgraph
        .get("nodes")
        .or_else(|| subgraph.get("subgraph"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let edges: Vec<Value> = subgraph
        .get("edges")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let externals: Vec<&Value> = nodes
        .iter()
        .filter(|node| {
            let file = value_string(node.get("file").unwrap_or(&Value::Null)).to_lowercase();
            EXTERNAL_MARKERS.iter().any(|marker| file.contains(marker))
        })
        .collect();
    let base_risk = (0.2
        + nodes.len() as f64 / 50.0
        + edges.len() as f64 / 150.0
        + externals.len() as f64 * 0.05)
        .min(1.0);
    let mut base_result = Map::new();
    base_result.insert(
        "risk_score".to_string(),
        json!(format!("{base_risk:.3}").parse::<f64>().unwrap_or(base_risk)),
    );
    base_result.insert("node_count".to_string(), json!(nodes.len()));
    base_result.insert("edge_count".to_string(), json!(edges.len()));
    base_result.insert(
        "external_dependency_count".to_string(),
        json!(externals.len()),
    );
    base_result.insert(
        "impacted_nodes".to_string(),
        Value::Array(
            nodes
                .iter()
                .map(|node| {
                    json!({
                        "id": node.get("id").cloned().unwrap_or(Value::Null),
                        "qual_name": node.get("qual_name").cloned().unwrap_or(Value::Null),
                        "file": node.get("file").cloned().unwrap_or(Value::Null),
                        "depth": node.get("depth").cloned().unwrap_or(Value::Null),
                    })
                })
                .collect(),
        ),
    );
    let selected_parser =
        crate::dispatch::normalize_parser_type(if parser_type.is_empty() { None } else { Some(&parser_type) });
    base_result.insert(
        "capability".to_string(),
        subgraph.get("capability").cloned().unwrap_or_else(|| {
            crate::dispatch::capability_summary(selected_parser.as_deref())
        }),
    );
    if let Some(diagnostics) = subgraph.get("capability_diagnostics") {
        base_result.insert("capability_diagnostics".to_string(), diagnostics.clone());
    }
    if let Some(error) = subgraph.get("error") {
        base_result.insert("subgraph_error".to_string(), error.clone());
    }

    // 2. Workflow impact layer.
    let context = resolve_direct_capability_context(
        runtime,
        "analyze_workflow_impact",
        Some(&parser_type),
        database.as_deref(),
        &["HAS_STEP", "CALLS"],
        &[],
    )?;
    if let Some(error) = context.error {
        let error_message = error
            .get("error")
            .and_then(|inner| inner.get("message"))
            .and_then(Value::as_str)
            .map(str::to_string);
        base_result.insert(
            "workflow_impact".to_string(),
            json!({
                "available": false,
                "reason": error_message.unwrap_or_else(|| {
                    "Workflow relationships (HAS_STEP) are not available in the \
                     active provider; function-level impact only."
                        .to_string()
                }),
            }),
        );
        return Ok(Value::Object(base_result));
    }
    let selected_capability = capability_for_parser(context.selected_parser.as_deref());
    let flow_defaults: Vec<String> = match &selected_capability {
        Some(capability) => default_relationships(Some(capability.name.as_str()), None),
        None => vec!["CALLS".to_string()],
    };
    let workflow_flow_relationships: Vec<String> = context
        .relationships
        .iter()
        .filter(|relationship| flow_defaults.contains(relationship))
        .cloned()
        .collect();
    base_result.insert("capability".to_string(), context.routing.clone());
    if let Some(diagnostics) = &context.diagnostics {
        base_result.insert("capability_diagnostics".to_string(), diagnostics.clone());
    }
    let wf_impact = workflow_impact_score(
        runtime,
        &function_id,
        database.as_deref(),
        &workflow_flow_relationships,
        capped,
    )?;
    let workflow_risk_score = wf_impact
        .get("workflow_risk_score")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let overall = (0.4 * base_risk + 0.6 * workflow_risk_score).min(1.0);
    let overall = format!("{overall:.3}").parse::<f64>().unwrap_or(overall);
    let mut workflow_impact = wf_impact.clone();
    if let Some(object) = workflow_impact.as_object_mut() {
        object.insert("overall_risk_score".to_string(), json!(overall));
    }
    base_result.insert("workflow_impact".to_string(), workflow_impact);
    base_result.insert("risk_score".to_string(), json!(overall));
    Ok(Value::Object(base_result))
}

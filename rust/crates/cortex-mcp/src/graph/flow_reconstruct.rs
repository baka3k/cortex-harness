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
//! `reconstruct_flow` — port thuần của `services/flow_reconstructor.py`
//! (Unified Flow Reconstructor V1.1).

use std::collections::{BTreeMap, BTreeSet, HashMap};

use serde_json::{json, Map, Value};

const CONFIDENCE_HIGH: &str = "high";
const CONFIDENCE_MEDIUM: &str = "medium";
const CONFIDENCE_LOW: &str = "low";

const RELATION_DIRECT_EDGE: &str = "direct_edge";
const RELATION_SAME_PATH_SEQ: &str = "same_path_sequence";
const RELATION_UNKNOWN: &str = "unknown";

const UNCERTAINTY_LOW: &str = "low";
const UNCERTAINTY_MEDIUM: &str = "medium";
const UNCERTAINTY_HIGH: &str = "high";

const NON_FLOW_TYPES: [&str; 8] = [
    "file", "File", "package", "Package", "chunk", "Chunk", "module", "Module",
];

fn failure() -> Value {
    json!({
        "flows": [],
        "uncertainties": ["Insufficient data to reconstruct flow"],
    })
}

fn is_non_flow_type(value: &Value) -> bool {
    let text = match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    NON_FLOW_TYPES.contains(&text.as_str())
}

fn node_line(node: &Map<String, Value>) -> i64 {
    node.get("location")
        .and_then(Value::as_object)
        .and_then(|location| location.get("line"))
        .and_then(Value::as_i64)
        .unwrap_or(0)
}

fn node_file(node: &Map<String, Value>) -> String {
    node.get("location")
        .and_then(Value::as_object)
        .and_then(|location| location.get("file"))
        .map(|value| match value {
            Value::String(text) => text.clone(),
            Value::Null => String::new(),
            other => other.to_string(),
        })
        .unwrap_or_default()
}

fn topo_order(
    entry_node_id: &str,
    nodes_in_path: &BTreeMap<String, Map<String, Value>>,
    fwd: &HashMap<String, Vec<Map<String, Value>>>,
) -> Vec<String> {
    let mut ordered: Vec<String> = Vec::new();
    let mut visited: BTreeSet<String> = BTreeSet::new();

    fn dfs(
        nid: &str,
        nodes_in_path: &BTreeMap<String, Map<String, Value>>,
        fwd: &HashMap<String, Vec<Map<String, Value>>>,
        visited: &mut BTreeSet<String>,
        ordered: &mut Vec<String>,
    ) {
        if visited.contains(nid) || !nodes_in_path.contains_key(nid) {
            return;
        }
        visited.insert(nid.to_string());
        ordered.push(nid.to_string());
        let entry_node = &nodes_in_path[nid];
        let entry_file = node_file(entry_node);
        let mut successors: Vec<String> = fwd
            .get(nid)
            .map(|edges| {
                edges
                    .iter()
                    .filter_map(|edge| edge.get("to").and_then(Value::as_str))
                    .filter(|to| nodes_in_path.contains_key(*to))
                    .map(|to| to.to_string())
                    .collect()
            })
            .unwrap_or_default();
        successors.retain(|successor| !visited.contains(successor));
        let mut same_file: Vec<String> = successors
            .iter()
            .filter(|successor| node_file(&nodes_in_path[*successor]) == entry_file)
            .cloned()
            .collect();
        let diff_file: Vec<String> = successors
            .iter()
            .filter(|successor| node_file(&nodes_in_path[*successor]) != entry_file)
            .cloned()
            .collect();
        same_file.sort_by_key(|successor| node_line(&nodes_in_path[successor]));
        for successor in same_file.into_iter().chain(diff_file) {
            dfs(&successor, nodes_in_path, fwd, visited, ordered);
        }
    }

    dfs(entry_node_id, nodes_in_path, fwd, &mut visited, &mut ordered);
    let mut unreachable: Vec<String> = nodes_in_path
        .keys()
        .filter(|nid| !visited.contains(*nid))
        .cloned()
        .collect();
    unreachable.sort_by_key(|nid| {
        let node = &nodes_in_path[nid];
        (node_file(node), node_line(node))
    });
    ordered.extend(unreachable);
    ordered
}

fn has_direct_edge(
    from_id: &str,
    to_id: &str,
    fwd: &HashMap<String, Vec<Map<String, Value>>>,
) -> bool {
    fwd.get(from_id)
        .map(|edges| {
            edges
                .iter()
                .any(|edge| edge.get("to").and_then(Value::as_str) == Some(to_id))
        })
        .unwrap_or(false)
}

fn determine_relation(
    prev_id: Option<&str>,
    curr_id: &str,
    fwd: &HashMap<String, Vec<Map<String, Value>>>,
    is_first: bool,
    path_node_ids: &[String],
) -> (&'static str, &'static str, String) {
    if is_first {
        return (RELATION_SAME_PATH_SEQ, UNCERTAINTY_LOW, "entry node for this flow".to_string());
    }
    if let Some(prev_id) = prev_id
        && has_direct_edge(prev_id, curr_id, fwd)
    {
        let edge_type = fwd
            .get(prev_id)
            .and_then(|edges| {
                edges
                    .iter()
                    .find(|edge| edge.get("to").and_then(Value::as_str) == Some(curr_id))
            })
            .and_then(|edge| edge.get("type").cloned())
            .map(|value| match value {
                Value::String(text) => text,
                Value::Null => "call".to_string(),
                other => other.to_string(),
            })
            .unwrap_or_else(|| "call".to_string());
        return (RELATION_DIRECT_EDGE, UNCERTAINTY_LOW, format!("{edge_type} edge from previous node"));
    }
    if let Some(prev_id) = prev_id {
        let prev_index = path_node_ids.iter().position(|id| id == prev_id);
        let curr_index = path_node_ids.iter().position(|id| id == curr_id);
        if let (Some(prev_index), Some(curr_index)) = (prev_index, curr_index)
            && curr_index == prev_index + 1
        {
            return (
                RELATION_SAME_PATH_SEQ,
                UNCERTAINTY_MEDIUM,
                "sequential in path without direct edge".to_string(),
            );
        }
    }
    (RELATION_UNKNOWN, UNCERTAINTY_HIGH, "no direct edge or sequence found".to_string())
}

fn compute_confidence(steps: &[Value], has_ordering_conflicts: bool) -> &'static str {
    if steps.is_empty() {
        return CONFIDENCE_LOW;
    }
    let unknown_count = steps
        .iter()
        .filter(|step| step.get("uncertainty").and_then(Value::as_str) == Some(UNCERTAINTY_HIGH))
        .count();
    let ratio = unknown_count as f64 / steps.len() as f64;
    if ratio == 0.0 && !has_ordering_conflicts {
        return CONFIDENCE_HIGH;
    }
    if ratio < 0.5 {
        return CONFIDENCE_MEDIUM;
    }
    CONFIDENCE_LOW
}

fn generate_title(entry_context: &Map<String, Value>, steps: &[Value]) -> String {
    let trigger = entry_context
        .get("trigger")
        .and_then(Value::as_str)
        .unwrap_or("");
    let entry = entry_context
        .get("entry_point")
        .and_then(Value::as_str)
        .unwrap_or("");
    if steps.len() <= 1 {
        return format!("{entry} invocation");
    }
    let last = steps
        .last()
        .and_then(|step| {
            step.get("name")
                .map(|value| match value {
                    Value::String(text) => text.clone(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                })
                .or_else(|| step.get("node_id").map(|value| value.to_string()))
        })
        .unwrap_or_default();
    if !trigger.is_empty() {
        format!("{trigger} → {last}")
    } else {
        format!("{entry} → {last}")
    }
}

fn merge_duplicate_flows(mut flows: Vec<Value>) -> Vec<Value> {
    if flows.len() <= 1 {
        return flows;
    }
    let conf_rank = |confidence: &str| match confidence {
        CONFIDENCE_HIGH => 2i32,
        CONFIDENCE_MEDIUM => 1,
        _ => 0,
    };
    let mut buckets: Vec<(String, Value)> = Vec::new();
    for flow in flows.drain(..) {
        let signature = flow
            .get("steps")
            .and_then(Value::as_array)
            .map(|steps| {
                steps
                    .iter()
                    .filter_map(|step| step.get("node_id").and_then(Value::as_str))
                    .collect::<Vec<_>>()
                    .join("\u{1}")
            })
            .unwrap_or_default();
        let matched_index = buckets
            .iter()
            .position(|(sig, _)| *sig == signature);
        match matched_index {
            None => buckets.push((signature, flow)),
            Some(index) => {
                let (_, matched) = &mut buckets[index];
                if let (Some(matched_object), Some(flow_object)) =
                    (matched.as_object_mut(), flow.as_object())
                {
                    // Merge path_ids (set semantics — Python list(set(...))).
                    merge_id_list(matched_object, flow_object, "paths_used");
                    merge_id_list(matched_object, flow_object, "discarded_paths");
                    let new_confidence = flow_object
                        .get("confidence")
                        .and_then(Value::as_str)
                        .unwrap_or(CONFIDENCE_LOW);
                    let old_confidence = matched_object
                        .get("confidence")
                        .and_then(Value::as_str)
                        .unwrap_or(CONFIDENCE_LOW);
                    if conf_rank(new_confidence) > conf_rank(old_confidence) {
                        matched_object.insert("confidence".to_string(), json!(new_confidence));
                    }
                    // Merge path_ids in steps.
                    if let (Some(old_steps), Some(new_steps)) = (
                        matched_object.get_mut("steps").and_then(Value::as_array_mut),
                        flow_object.get("steps").and_then(Value::as_array),
                    ) {
                        for (step_a, step_b) in old_steps.iter_mut().zip(new_steps) {
                            if let (Some(step_a_object), Some(step_b_object)) =
                                (step_a.as_object_mut(), step_b.as_object())
                            {
                                merge_id_list(step_a_object, step_b_object, "path_ids");
                            }
                        }
                    }
                }
            }
        }
    }
    buckets
        .into_iter()
        .map(|(_, flow)| {
            // Python strips internal `_sig` key — Rust không bao giờ thêm nó.
            flow
        })
        .collect()
}

fn merge_id_list(target: &mut Map<String, Value>, source: &Map<String, Value>, key: &str) {
    let mut combined: Vec<String> = target
        .get(key)
        .and_then(Value::as_array)
        .map(|items| items.iter().map(value_string).collect())
        .unwrap_or_default();
    combined.extend(
        source
            .get(key)
            .and_then(Value::as_array)
            .map(|items| items.iter().map(value_string).collect::<Vec<_>>())
            .unwrap_or_default(),
    );
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let merged: Vec<String> = combined.into_iter().filter(|id| seen.insert(id.clone())).collect();
    target.insert(key.to_string(), json!(merged));
}

fn value_string(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// `reconstruct_flows` entry (unified tool `reconstruct_flow`).
pub fn tool_reconstruct_flow(arguments: &Value) -> Value {
    // Python tool body đã parse entry_context_json/paths_json; guard rỗng được
    // xử lý ở dispatch direct_tool_guard (phase-11). Ở đây arguments là merged
    // payload — entry_context_json/paths_json vẫn là chuỗi JSON.
    let entry_context_json = arguments
        .get("entry_context_json")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let paths_json = arguments
        .get("paths_json")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if entry_context_json.is_empty() || paths_json.is_empty() {
        return json!({
            "flows": [],
            "uncertainties": ["entry_context_json and paths_json are required"],
        });
    }
    let entry_context: Value = match serde_json::from_str(entry_context_json) {
        Ok(value) => value,
        Err(error) => {
            return json!({
                "flows": [],
                "uncertainties": [format!("Invalid entry_context_json: {error}")],
            });
        }
    };
    let paths: Value = match serde_json::from_str(paths_json) {
        Ok(value) => value,
        Err(error) => {
            return json!({
                "flows": [],
                "uncertainties": [format!("Invalid paths_json: {error}")],
            });
        }
    };
    if !entry_context.is_object() {
        return json!({
            "flows": [],
            "uncertainties": ["entry_context_json must be a JSON object"],
        });
    }
    if !paths.is_array() {
        return json!({
            "flows": [],
            "uncertainties": ["paths_json must be a JSON array"],
        });
    }
    reconstruct(
        entry_context.as_object().expect("entry object"),
        paths.as_array().expect("paths array"),
    )
}

fn reconstruct(entry_context: &Map<String, Value>, paths: &[Value]) -> Value {
    if paths.is_empty() || entry_context.is_empty() {
        return failure();
    }
    let entry_node_id = entry_context
        .get("entry_node_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let flow_type = entry_context
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("backend");

    // 1. Node index toàn cục.
    let mut node_index: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
    for path in paths {
        for node in path
            .get("nodes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            let Some(object) = node.as_object() else { continue };
            let nid = object
                .get("node_id")
                .map(value_string)
                .unwrap_or_default();
            let mapped_type = object
                .get("mapped_type")
                .filter(|value| !value.is_null())
                .cloned()
                .or_else(|| object.get("type").cloned())
                .unwrap_or(Value::Null);
            if !nid.is_empty()
                && !node_index.contains_key(&nid)
                && !is_non_flow_type(&mapped_type)
            {
                node_index.insert(nid, object.clone());
            }
        }
    }

    // 2. Entry anchor rule.
    if entry_node_id.is_empty() || !node_index.contains_key(entry_node_id) {
        return failure();
    }

    // 3. Partition paths.
    let qualifying: Vec<&Value> = paths
        .iter()
        .filter(|path| {
            path.get("nodes")
                .and_then(Value::as_array)
                .map(|nodes| {
                    nodes.iter().any(|node| {
                        node.get("node_id").and_then(Value::as_str) == Some(entry_node_id)
                    })
                })
                .unwrap_or(false)
        })
        .collect();
    let discarded: Vec<&Value> = paths
        .iter()
        .filter(|path| !qualifying.iter().any(|qualifier| std::ptr::eq(*qualifier, *path)))
        .collect();
    if qualifying.is_empty() {
        return failure();
    }

    let mut flows: Vec<Value> = Vec::new();
    let mut global_uncertainties: Vec<String> = Vec::new();
    for (index, path) in qualifying.iter().enumerate() {
        let flow_id = format!("flow_{}", index + 1);
        let path_object = path.as_object().cloned().unwrap_or_default();
        let path_id = path_object.get("path_id").map(value_string).unwrap_or_default();

        let mut nodes_in_path: BTreeMap<String, Map<String, Value>> = BTreeMap::new();
        let mut path_node_sequence: Vec<String> = Vec::new();
        for node in path
            .get("nodes")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
        {
            let Some(object) = node.as_object() else { continue };
            let nid = object.get("node_id").map(value_string).unwrap_or_default();
            let mapped_type = object
                .get("mapped_type")
                .filter(|value| !value.is_null())
                .cloned()
                .or_else(|| object.get("type").cloned())
                .unwrap_or(Value::Null);
            if !nid.is_empty() && !is_non_flow_type(&mapped_type) {
                nodes_in_path.insert(nid.clone(), object.clone());
                path_node_sequence.push(nid);
            }
        }
        let raw_edges = path
            .get("edges")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut fwd: HashMap<String, Vec<Map<String, Value>>> = HashMap::new();
        for edge in raw_edges {
            let Some(object) = edge.as_object() else { continue };
            let src = object.get("from").map(value_string).unwrap_or_default();
            let dst = object.get("to").map(value_string).unwrap_or_default();
            if !src.is_empty() && !dst.is_empty() {
                fwd.entry(src).or_default().push(object.clone());
            }
        }

        let ordered_ids = topo_order(entry_node_id, &nodes_in_path, &fwd);
        let mut steps: Vec<Value> = Vec::new();
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut prev_id: Option<String> = None;
        for nid in &ordered_ids {
            if seen.contains(nid) {
                continue;
            }
            seen.insert(nid.clone());
            let node = &node_index[nid];
            let (relation, uncertainty, reason_text) = determine_relation(
                prev_id.as_deref(),
                nid,
                &fwd,
                steps.is_empty(),
                &path_node_sequence,
            );
            let mut step = Map::new();
            step.insert(
                "step_id".to_string(),
                json!(format!("{}_step_{}", flow_id, steps.len() + 1)),
            );
            step.insert("node_id".to_string(), json!(nid));
            step.insert(
                "name".to_string(),
                node.get("name").cloned().unwrap_or(json!(nid)),
            );
            step.insert(
                "mapped_type".to_string(),
                node.get("mapped_type")
                    .cloned()
                    .filter(|value| !value.is_null())
                    .unwrap_or(json!("function")),
            );
            step.insert("path_ids".to_string(), json!([path_id]));
            step.insert("relation".to_string(), json!(relation));
            step.insert("reason_text".to_string(), json!(reason_text));
            step.insert("uncertainty".to_string(), json!(uncertainty));
            if let Some(location) = node.get("location").filter(|value| !value.is_null()) {
                step.insert("location".to_string(), location.clone());
            }
            if let Some(summary) = node.get("summary").filter(|value| !value.is_null()) {
                step.insert("summary".to_string(), summary.clone());
            }
            steps.push(Value::Object(step));
            prev_id = Some(nid.clone());
        }

        let unreachable_in_steps: Vec<&Value> = steps
            .iter()
            .filter(|step| step.get("uncertainty").and_then(Value::as_str) == Some(UNCERTAINTY_HIGH))
            .collect();
        let has_conflicts = !unreachable_in_steps.is_empty();
        if has_conflicts {
            global_uncertainties.push(format!(
                "Path {path_id}: {} node(s) not reachable from entry via declared edges",
                unreachable_in_steps.len()
            ));
        }
        let confidence = compute_confidence(&steps, has_conflicts);
        let discarded_ids: Vec<String> = discarded
            .iter()
            .filter_map(|path| path.get("path_id").map(value_string))
            .collect();
        let flow = json!({
            "flow_id": flow_id,
            "title": generate_title(entry_context, &steps),
            "type": flow_type,
            "confidence": confidence,
            "entry_node_id": entry_node_id,
            "paths_used": [path_id],
            "discarded_paths": discarded_ids,
            "discard_reason": if discarded.is_empty() { "" } else { "does not contain entry_node_id" },
            "steps": steps,
        });
        flows.push(flow);
    }

    let flows = merge_duplicate_flows(flows);
    if flows.len() > 1 {
        global_uncertainties.push(
            "Multiple flows reconstructed; execution path is non-deterministic".to_string(),
        );
    }
    json!({
        "flows": flows,
        "uncertainties": global_uncertainties,
    })
}

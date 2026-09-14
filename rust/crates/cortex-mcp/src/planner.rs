//! Port của `compute_scc` / `topological_sort` từ
//! `code-tiny/mcp/fastmcp_server.py` — pure dependency-graph planners
//! (`_PLANNER_TOOL_NAMES` dispatch trực tiếp tới fast backend, không cần
//! graph database).
//!
//! Determinism note: Python iterates adjacency `set`s (hash order), so for
//! graphs where a node has multiple successors the SCC component indices can
//! vary between processes. The Rust port iterates neighbors in sorted order;
//! both agree whenever the graph is deterministic under any iteration order
//! (the contract fixtures use such graphs).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use crate::contract::{ErrorValue, normalize_error};

/// `_normalize_edge_semantics`.
pub fn normalize_edge_semantics(value: Option<&str>) -> &'static str {
    let semantics = value.unwrap_or("depends_on").trim().to_lowercase();
    match semantics.as_str() {
        "depends_on" | "dependent_to_dependency" | "call_graph" | "calls"
        | "caller_to_callee" => "depends_on",
        "dependency_to_dependent" | "prerequisite_to_dependent" => "dependency_to_dependent",
        _ => "depends_on",
    }
}

/// `_normalize_string_list` (fast backend variant: returns a list, `None`
/// becomes empty).
pub fn normalize_string_list(value: Option<&Value>) -> Vec<String> {
    match value {
        None => Vec::new(),
        Some(Value::String(text)) => {
            let text = text.trim();
            if text.is_empty() {
                return Vec::new();
            }
            if text.contains(',') || text.contains(';') {
                return text
                    .replace(';', ",")
                    .split(',')
                    .map(str::trim)
                    .filter(|part| !part.is_empty())
                    .map(str::to_string)
                    .collect();
            }
            vec![text.to_string()]
        }
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| item.as_str())
            .map(|item| item.trim().to_string())
            .filter(|item| !item.is_empty())
            .collect(),
        Some(value) => {
            let text = value.to_string();
            let trimmed = text.trim().trim_matches('"');
            if trimmed.is_empty() {
                Vec::new()
            } else {
                vec![trimmed.to_string()]
            }
        }
    }
}

/// `_extract_edges_from_graph_payload` — accept the known edge field aliases.
pub fn extract_edges_from_graph_payload(edges: Option<&Value>) -> Vec<(String, String)> {
    let mut pairs = Vec::new();
    let Some(Value::Array(items)) = edges else {
        return pairs;
    };
    const START_KEYS: [&str; 8] = [
        "start_id", "from", "source", "src", "start", "u", "caller", "dependent",
    ];
    const END_KEYS: [&str; 8] = [
        "end_id", "to", "target", "dst", "end", "v", "callee", "dependency",
    ];
    for edge in items {
        let Some(object) = edge.as_object() else {
            continue;
        };
        let start = START_KEYS.iter().find_map(|key| object.get(*key));
        let end = END_KEYS.iter().find_map(|key| object.get(*key));
        let (Some(start), Some(end)) = (start, end) else {
            continue;
        };
        let start = scalar_to_string(start);
        let end = scalar_to_string(end);
        if start == "None" || end == "None" {
            continue;
        }
        pairs.push((start, end));
    }
    pairs
}

fn scalar_to_string(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::String(text) => text.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        other => other.to_string(),
    }
}

/// `_build_prerequisite_graph` — adjacency where edge u->v means: u must be
/// done before v.
fn build_prerequisite_graph(
    nodes: &[String],
    edges: &[(String, String)],
    edge_semantics: &str,
) -> (BTreeSet<String>, BTreeMap<String, BTreeSet<String>>) {
    let semantics = normalize_edge_semantics(Some(edge_semantics));
    let mut node_set: BTreeSet<String> = nodes
        .iter()
        .filter(|node| !node.is_empty())
        .cloned()
        .collect();
    let mut adjacency: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for node in &node_set {
        adjacency.entry(node.clone()).or_default();
    }
    for (start, end) in edges {
        let start = start.trim();
        let end = end.trim();
        if start.is_empty() || end.is_empty() {
            continue;
        }
        node_set.insert(start.to_string());
        node_set.insert(end.to_string());
        adjacency.entry(start.to_string()).or_default();
        adjacency.entry(end.to_string()).or_default();
        let (prerequisite, dependent) = if semantics == "depends_on" {
            (end, start)
        } else {
            (start, end)
        };
        adjacency
            .entry(prerequisite.to_string())
            .or_default()
            .insert(dependent.to_string());
    }
    for node in &node_set {
        adjacency.entry(node.clone()).or_default();
    }
    (node_set, adjacency)
}

/// `_compute_scc` — Tarjan SCC. `adjacency` is iterated in sorted order
/// (Python iterates a set; see the module determinism note).
fn compute_scc(
    nodes: &BTreeSet<String>,
    adjacency: &BTreeMap<String, BTreeSet<String>>,
) -> Vec<Vec<String>> {
    let mut index_counter: usize = 0;
    let mut index: BTreeMap<String, usize> = BTreeMap::new();
    let mut lowlink: BTreeMap<String, usize> = BTreeMap::new();
    let mut stack: Vec<String> = Vec::new();
    let mut on_stack: BTreeSet<String> = BTreeSet::new();
    let mut components: Vec<Vec<String>> = Vec::new();

    for node in nodes {
        if index.contains_key(node) {
            continue;
        }
        // Iterative Tarjan (explicit stack of (node, successor iterator)).
        let mut work: Vec<(String, usize)> = vec![(node.clone(), 0)];
        while let Some((current, pointer)) = work.pop() {
            if pointer == 0 {
                index.insert(current.clone(), index_counter);
                lowlink.insert(current.clone(), index_counter);
                index_counter += 1;
                stack.push(current.clone());
                on_stack.insert(current.clone());
            }
            let successors = adjacency.get(&current).cloned().unwrap_or_default();
            let successor_list: Vec<String> = successors.into_iter().collect();
            let mut advanced = false;
            for (position, successor) in successor_list.iter().enumerate().skip(pointer) {
                if !index.contains_key(successor) {
                    // Re-enter `current` after the child completes.
                    work.push((current.clone(), position + 1));
                    work.push((successor.clone(), 0));
                    advanced = true;
                    break;
                }
                if on_stack.contains(successor) {
                    let successor_index = index[successor];
                    let entry = lowlink.entry(current.clone()).or_insert(successor_index);
                    *entry = (*entry).min(successor_index);
                }
            }
            if advanced {
                continue;
            }
            // All successors processed: propagate lowlink to the parent, then
            // maybe close a component.
            if let Some(parent) = work.last() {
                let parent = parent.0.clone();
                if parent != current {
                    let child_lowlink = lowlink[&current];
                    let entry = lowlink.entry(parent).or_insert(child_lowlink);
                    *entry = (*entry).min(child_lowlink);
                }
            }
            if lowlink[&current] == index[&current] {
                let mut component: Vec<String> = Vec::new();
                while let Some(top) = stack.pop() {
                    on_stack.remove(&top);
                    component.push(top.clone());
                    if top == current {
                        break;
                    }
                }
                component.sort();
                components.push(component);
            }
        }
    }
    components
}

/// `_build_condensed_dag` — SCC ids, condensed adjacency, node → SCC index.
#[allow(clippy::type_complexity)]
fn build_condensed_dag(
    adjacency: &BTreeMap<String, BTreeSet<String>>,
    components: &[Vec<String>],
) -> (
    Vec<String>,
    BTreeMap<String, BTreeSet<String>>,
    BTreeMap<String, usize>,
) {
    let mut node_to_scc_idx: BTreeMap<String, usize> = BTreeMap::new();
    for (component_index, component) in components.iter().enumerate() {
        for node in component {
            node_to_scc_idx.insert(node.clone(), component_index);
        }
    }
    let scc_ids: Vec<String> = (0..components.len()).map(|index| format!("scc_{index}")).collect();
    let mut dag_adjacency: BTreeMap<String, BTreeSet<String>> =
        scc_ids.iter().map(|id| (id.clone(), BTreeSet::new())).collect();
    for (source, neighbors) in adjacency {
        let source_index = node_to_scc_idx[source];
        let source_id = &scc_ids[source_index];
        for destination in neighbors {
            let destination_index = node_to_scc_idx[destination];
            let destination_id = &scc_ids[destination_index];
            if source_index != destination_index {
                dag_adjacency
                    .entry(destination_id.clone())
                    .or_default()
                    .insert(source_id.clone());
            }
        }
    }
    (scc_ids, dag_adjacency, node_to_scc_idx)
}

/// `_topological_waves` — Kahn-style topological wave decomposition.
#[allow(clippy::type_complexity)]
fn topological_waves(
    nodes: impl IntoIterator<Item = String>,
    adjacency: &BTreeMap<String, BTreeSet<String>>,
) -> (Vec<Vec<String>>, Vec<String>, BTreeMap<String, i64>) {
    let node_list: BTreeSet<String> = nodes.into_iter().filter(|node| !node.is_empty()).collect();
    let mut indegree: BTreeMap<String, i64> =
        node_list.iter().map(|node| (node.clone(), 0)).collect();
    for source in &node_list {
        if let Some(neighbors) = adjacency.get(source) {
            for destination in neighbors {
                *indegree.entry(destination.clone()).or_insert(0) += 1;
                indegree.entry(source.clone()).or_insert(0);
            }
        }
    }

    let mut current_wave: Vec<String> = indegree
        .iter()
        .filter(|(_, degree)| **degree == 0)
        .map(|(node, _)| node.clone())
        .collect();
    current_wave.sort();
    let mut waves: Vec<Vec<String>> = Vec::new();
    let mut processed: BTreeSet<String> = BTreeSet::new();

    while !current_wave.is_empty() {
        waves.push(current_wave.clone());
        let mut next_candidates: BTreeSet<String> = BTreeSet::new();
        for node in &current_wave {
            if processed.contains(node) {
                continue;
            }
            processed.insert(node.clone());
            if let Some(neighbors) = adjacency.get(node) {
                for neighbor in neighbors {
                    if let Some(degree) = indegree.get_mut(neighbor) {
                        *degree -= 1;
                        if *degree == 0 {
                            next_candidates.insert(neighbor.clone());
                        }
                    }
                }
            }
        }
        current_wave = next_candidates.into_iter().collect();
    }

    let unresolved: Vec<String> = indegree
        .keys()
        .filter(|node| !processed.contains(*node))
        .cloned()
        .collect();
    (waves, unresolved, indegree)
}

/// Python `repr([...])` for a list of strings (used inside error messages).
fn python_repr_list(items: &[String]) -> String {
    let rendered: Vec<String> = items
        .iter()
        .map(|item| format!("'{}'", item.replace('\\', "\\\\").replace('\'', "\\'")))
        .collect();
    format!("[{}]", rendered.join(", "))
}

/// `tool_compute_scc` — returns the legacy payload dict (middleware wraps it).
pub fn tool_compute_scc(arguments: &Value) -> Result<Value, PlannerError> {
    let object = arguments.as_object().cloned().unwrap_or_default();
    let nodes = normalize_string_list(object.get("nodes"));
    let edge_pairs = extract_edges_from_graph_payload(object.get("edges"));
    if nodes.is_empty() && edge_pairs.is_empty() {
        return Err(PlannerError::value_error("Provide at least one node or edge."));
    }
    let edge_semantics = object
        .get("edge_semantics")
        .and_then(Value::as_str)
        .unwrap_or("depends_on");
    let include_singletons = object
        .get("include_singletons")
        .and_then(Value::as_bool)
        .unwrap_or(true);

    let (graph_nodes, adjacency) =
        build_prerequisite_graph(&nodes, &edge_pairs, edge_semantics);
    let components = compute_scc(&graph_nodes, &adjacency);
    let mut component_payload: Vec<Value> = Vec::new();
    let mut cyclic_count = 0usize;
    let mut self_loop_count = 0usize;
    for (index, component) in components.iter().enumerate() {
        let is_cycle = if component.len() == 1 {
            let node = &component[0];
            let has_self_loop = adjacency
                .get(node)
                .map(|targets| targets.contains(node))
                .unwrap_or(false);
            if has_self_loop {
                self_loop_count += 1;
            }
            has_self_loop
        } else {
            true
        };
        if is_cycle {
            cyclic_count += 1;
        }
        if include_singletons || component.len() > 1 || is_cycle {
            component_payload.push(json!({
                "scc_id": format!("scc_{index}"),
                "nodes": component,
                "size": component.len(),
                "is_cycle": is_cycle,
            }));
        }
    }

    let mut node_to_scc: Map<String, Value> = Map::new();
    for item in &component_payload {
        let scc_id = item["scc_id"].clone();
        for node in item["nodes"].as_array().expect("nodes array") {
            node_to_scc.insert(node.as_str().expect("node str").to_string(), scc_id.clone());
        }
    }

    Ok(json!({
        "edge_semantics": normalize_edge_semantics(Some(edge_semantics)),
        "components": component_payload,
        "node_to_scc": Value::Object(node_to_scc),
        "cycle_summary": {
            "total_scc": components.len(),
            "reported_scc": component_payload.len(),
            "cyclic_scc": cyclic_count,
            "self_loops": self_loop_count,
        },
    }))
}

/// `tool_topological_sort` — returns the legacy payload dict.
pub fn tool_topological_sort(arguments: &Value) -> Result<Value, PlannerError> {
    let object = arguments.as_object().cloned().unwrap_or_default();
    let nodes = normalize_string_list(object.get("nodes"));
    let edge_pairs = extract_edges_from_graph_payload(object.get("edges"));
    if nodes.is_empty() && edge_pairs.is_empty() {
        return Err(PlannerError::value_error("Provide at least one node or edge."));
    }
    let edge_semantics = object
        .get("edge_semantics")
        .and_then(Value::as_str)
        .unwrap_or("depends_on");
    let output_mode = object
        .get("output_mode")
        .and_then(Value::as_str)
        .unwrap_or("both");
    let on_cycle_raw = object
        .get("on_cycle")
        .and_then(Value::as_str)
        .unwrap_or("auto_condense_scc");

    let (graph_nodes, adjacency) =
        build_prerequisite_graph(&nodes, &edge_pairs, edge_semantics);
    let (waves, unresolved_nodes, indegree) = topological_waves(graph_nodes.clone(), &adjacency);
    let is_dag = unresolved_nodes.is_empty();

    let cycle_mode = if on_cycle_raw.trim().is_empty() {
        "auto_condense_scc".to_string()
    } else {
        on_cycle_raw.trim().to_lowercase()
    };
    if !unresolved_nodes.is_empty() && cycle_mode == "error" {
        return Err(PlannerError::runtime_error(format!(
            "Graph contains cycles. Unresolved nodes: {}",
            python_repr_list(&unresolved_nodes)
        )));
    }

    let mut condensed: Option<Value> = None;
    let mut resolved_waves = waves.clone();
    let mut unresolved_cycles: Vec<Value> = Vec::new();
    if !unresolved_nodes.is_empty() && cycle_mode == "auto_condense_scc" {
        let components = compute_scc(&graph_nodes, &adjacency);
        let (scc_ids, dag_adjacency, node_to_scc_idx) =
            build_condensed_dag(&adjacency, &components);
        let (scc_waves, _, _) = topological_waves(scc_ids.clone(), &dag_adjacency);
        let scc_id_to_nodes: BTreeMap<String, &Vec<String>> = components
            .iter()
            .enumerate()
            .map(|(index, component)| (format!("scc_{index}"), component))
            .collect();
        resolved_waves = Vec::new();
        for scc_wave in &scc_waves {
            let mut expanded: Vec<String> = Vec::new();
            for scc_id in scc_wave {
                if let Some(component) = scc_id_to_nodes.get(scc_id) {
                    expanded.extend(component.iter().cloned());
                }
            }
            expanded.sort();
            resolved_waves.push(expanded);
        }
        for (index, component) in components.iter().enumerate() {
            if component.len() > 1 {
                unresolved_cycles.push(json!({
                    "scc_id": format!("scc_{index}"),
                    "nodes": component,
                    "size": component.len(),
                }));
            } else if let Some(single) = component.first()
                && adjacency
                    .get(single)
                    .map(|targets| targets.contains(single))
                    .unwrap_or(false)
            {
                unresolved_cycles.push(json!({
                    "scc_id": format!("scc_{index}"),
                    "nodes": component,
                    "size": 1,
                }));
            }
        }
        condensed = Some(json!({
            "scc_count": components.len(),
            "dag_nodes": scc_ids,
            "node_to_scc": node_to_scc_idx
                .iter()
                .map(|(node, index)| (node.clone(), json!(format!("scc_{index}"))))
                .collect::<Map<String, Value>>(),
        }));
    }

    let linear_order: Vec<String> = resolved_waves.iter().flatten().cloned().collect();
    let mode = if output_mode.trim().is_empty() {
        "both".to_string()
    } else {
        output_mode.trim().to_lowercase()
    };
    let mut payload = json!({
        "edge_semantics": normalize_edge_semantics(Some(edge_semantics)),
        "is_dag": is_dag,
        "on_cycle": cycle_mode,
        "unresolved_nodes": unresolved_nodes,
        "unresolved_cycles": unresolved_cycles,
        "diagnostics": {
            "node_count": graph_nodes.len(),
            "edge_count": adjacency.values().map(BTreeSet::len).sum::<usize>(),
            "indegree": indegree,
        },
    });
    let payload_object = payload.as_object_mut().expect("payload object");
    if let Some(condensed) = condensed {
        payload_object.insert("condensed".to_string(), condensed);
    }
    if mode == "linear" || mode == "both" {
        payload_object.insert("linear_order".to_string(), json!(linear_order));
    }
    if mode == "waves" || mode == "both" {
        payload_object.insert(
            "waves".to_string(),
            json!(resolved_waves
                .iter()
                .enumerate()
                .map(|(index, wave)| json!({"wave": index, "nodes": wave}))
                .collect::<Vec<_>>()),
        );
    }
    Ok(payload)
}

/// Planner failure: the Python exception class name plus its message (the
/// class name decides the contract code: `ValueError` → invalid_parameters,
/// `RuntimeError` → tool_execution_error).
#[derive(Debug, Clone)]
pub struct PlannerError {
    pub exception: &'static str,
    pub message: String,
}

impl PlannerError {
    pub fn value_error(message: impl Into<String>) -> Self {
        PlannerError { exception: "ValueError", message: message.into() }
    }

    pub fn runtime_error(message: impl Into<String>) -> Self {
        PlannerError { exception: "RuntimeError", message: message.into() }
    }
}

/// Wrap a planner result the way `_dispatch_planner_tool` does: caught
/// exceptions become `_build_tool_error` legacy payloads.
pub fn dispatch_planner_result(
    tool_name: &str,
    arguments: &Value,
    result: Result<Value, PlannerError>,
) -> Value {
    match result {
        Ok(payload) => payload,
        Err(error) => build_tool_error(tool_name, arguments, error),
    }
}

/// `_build_tool_error` — the legacy unified error payload that the
/// middleware converts to the canonical envelope.
pub fn build_tool_error(tool_name: &str, payload: &Value, error: PlannerError) -> Value {
    let arguments = payload.as_object().cloned().unwrap_or_default();
    let missing_required = crate::dispatch::missing_required_params(tool_name, payload);
    let received: Vec<String> = arguments
        .iter()
        .filter(|(_, value)| !crate::dispatch::is_missing_value(value))
        .map(|(key, _)| key.clone())
        .collect();
    let example = crate::dispatch::parser_aware_example(tool_name, payload);
    let error_type = crate::dispatch::error_type_from_exception(
        error.exception,
        &missing_required,
    );
    serde_json::json!({
        "ok": false,
        "query_engine": crate::framework_registry::query_engine_for_backend(None),
        "error": {
            "type": error_type,
            "tool": tool_name,
            "query_engine": crate::framework_registry::query_engine_for_backend(None),
            "message": error.message,
            "missing_required_params": missing_required,
            "required_params": crate::catalog::required_params(tool_name),
            "accepted_params": crate::catalog::accepted_params(tool_name),
            "received_params": received,
            "example": example,
            "next_step": "Call list_mcp_functions and retry with exact parameter names.",
        },
    })
}

/// `normalize_error` over a planner exception, pre-wrapped.
pub fn canonical_planner_error(
    tool_name: &str,
    arguments: &Value,
    error: PlannerError,
) -> Value {
    let legacy = build_tool_error(tool_name, arguments, error);
    normalize_error(
        ErrorValue::Legacy(&legacy),
        None,
        None,
        None,
        None,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::from_value;

    fn arguments(value: Value) -> Value {
        value
    }

    #[test]
    fn compute_scc_chain_and_cycle() {
        let args = arguments(json!({
            "nodes": ["C", "A", "B"],
            "edges": [
                {"from": "A", "to": "B"},
                {"from": "B", "to": "C"},
                {"from": "C", "to": "A"}
            ]
        }));
        let payload = tool_compute_scc(&args).unwrap();
        assert_eq!(payload["cycle_summary"], from_value::<Value>(json!({
            "total_scc": 1, "reported_scc": 1, "cyclic_scc": 1, "self_loops": 0
        })).unwrap());
        assert_eq!(payload["components"][0]["nodes"], json!(["A", "B", "C"]));
        assert_eq!(payload["components"][0]["is_cycle"], json!(true));
        assert_eq!(payload["node_to_scc"]["A"], json!("scc_0"));
    }

    #[test]
    fn compute_scc_requires_input() {
        let payload = tool_compute_scc(&arguments(json!({})));
        let error = payload.unwrap_err();
        assert_eq!(error.exception, "ValueError");
        assert_eq!(error.message, "Provide at least one node or edge.");
    }

    #[test]
    fn topological_sort_waves_and_condensation() {
        let chain = arguments(json!({
            "nodes": ["auth", "db", "api"],
            "edges": [{"from": "db", "to": "api"}, {"from": "api", "to": "auth"}]
        }));
        let payload = tool_topological_sort(&chain).unwrap();
        assert_eq!(payload["is_dag"], json!(true));
        // Edge semantics: {"from": X, "to": Y} with depends_on means X
        // depends on Y, so Y runs first (Python reverses the edge).
        assert_eq!(payload["linear_order"], json!(["auth", "api", "db"]));
        assert_eq!(payload["waves"], json!([
            {"wave": 0, "nodes": ["auth"]},
            {"wave": 1, "nodes": ["api"]},
            {"wave": 2, "nodes": ["db"]}
        ]));
        assert_eq!(payload["diagnostics"]["node_count"], json!(3));

        let cycle = arguments(json!({
            "nodes": ["A", "B"],
            "edges": [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}]
        }));
        let payload = tool_topological_sort(&cycle).unwrap();
        assert_eq!(payload["is_dag"], json!(false));
        // Python keeps the Kahn `unresolved_nodes` snapshot in the payload
        // even after SCC condensation resolves the waves.
        assert_eq!(payload["unresolved_nodes"], json!(["A", "B"]));
        assert_eq!(payload["condensed"]["scc_count"], json!(1));
        assert_eq!(payload["linear_order"], json!(["A", "B"]));

        let strict = arguments(json!({
            "nodes": ["A", "B"],
            "edges": [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}],
            "on_cycle": "error"
        }));
        let error = tool_topological_sort(&strict).unwrap_err();
        assert_eq!(error.exception, "RuntimeError");
        assert_eq!(
            error.message,
            "Graph contains cycles. Unresolved nodes: ['A', 'B']"
        );
    }

    #[test]
    fn edge_semantics_normalization() {
        assert_eq!(normalize_edge_semantics(Some("calls")), "depends_on");
        assert_eq!(
            normalize_edge_semantics(Some("dependency_to_dependent")),
            "dependency_to_dependent"
        );
        assert_eq!(normalize_edge_semantics(None), "depends_on");
        assert_eq!(normalize_edge_semantics(Some("bogus")), "depends_on");
    }

    #[test]
    fn string_list_coercion() {
        assert_eq!(
            normalize_string_list(Some(&json!("a, b;c"))),
            vec!["a".to_string(), "b".to_string(), "c".to_string()]
        );
        assert_eq!(
            normalize_string_list(Some(&json!(["x", " y "]))),
            vec!["x".to_string(), "y".to_string()]
        );
        assert!(normalize_string_list(Some(&json!("  "))).is_empty());
    }
}
